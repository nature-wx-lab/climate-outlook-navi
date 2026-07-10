const decoder = new TextDecoder();

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function meshCodeFromLatLon(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < 0 || lon < 100) return null;
  const p = Math.floor(lat * 1.5);
  const q = Math.floor(lon) - 100;
  const latSecond = (lat * 1.5 - p) * 8;
  const lonSecond = (lon - Math.floor(lon)) * 8;
  const r = clamp(Math.floor(latSecond), 0, 7);
  const s = clamp(Math.floor(lonSecond), 0, 7);
  const t = clamp(Math.floor((latSecond - r) * 10 + 1e-8), 0, 9);
  const u = clamp(Math.floor((lonSecond - s) * 10 + 1e-8), 0, 9);
  return `${String(p).padStart(2, "0")}${String(q).padStart(2, "0")}${r}${s}${t}${u}`;
}

export function meshBounds(meshCode) {
  const code = String(meshCode).padStart(8, "0");
  if (!/^\d{8}$/.test(code)) return null;
  const p = Number(code.slice(0, 2));
  const q = Number(code.slice(2, 4));
  const r = Number(code[4]);
  const s = Number(code[5]);
  const t = Number(code[6]);
  const u = Number(code[7]);
  const south = p / 1.5 + r / 12 + t / 120;
  const west = q + 100 + s / 8 + u / 80;
  return {
    south,
    west,
    north: south + 1 / 120,
    east: west + 1 / 80,
    centerLat: south + 1 / 240,
    centerLon: west + 1 / 160,
  };
}

function binarySearch(codes, target) {
  let low = 0;
  let high = codes.length - 1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    const value = codes[middle];
    if (value === target) return middle;
    if (value < target) low = middle + 1;
    else high = middle - 1;
  }
  return -1;
}

function geometryBounds(geometry) {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  const visit = (value) => {
    if (Array.isArray(value) && value.length >= 2 && Number.isFinite(value[0]) && Number.isFinite(value[1])) {
      west = Math.min(west, value[0]);
      east = Math.max(east, value[0]);
      south = Math.min(south, value[1]);
      north = Math.max(north, value[1]);
      return;
    }
    if (Array.isArray(value)) value.forEach(visit);
  };
  visit(geometry.coordinates);
  return { west, south, east, north };
}

function pointInRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const crosses = ((yi > lat) !== (yj > lat))
      && lon < ((xj - xi) * (lat - yi)) / ((yj - yi) || Number.EPSILON) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

function pointInPolygon(lon, lat, polygon) {
  if (!polygon.length || !pointInRing(lon, lat, polygon[0])) return false;
  return !polygon.slice(1).some((hole) => pointInRing(lon, lat, hole));
}

function pointInGeometry(lon, lat, geometry) {
  if (geometry.type === "Polygon") return pointInPolygon(lon, lat, geometry.coordinates);
  if (geometry.type === "MultiPolygon") return geometry.coordinates.some((polygon) => pointInPolygon(lon, lat, polygon));
  return false;
}

function parseChunk(buffer) {
  const view = new DataView(buffer);
  const magic = decoder.decode(new Uint8Array(buffer, 0, 8)).replace(/\0+$/, "");
  if (magic !== "NWCBCH1") throw new Error(`Unknown chunk magic: ${magic}`);
  const version = view.getUint32(8, true);
  const count = view.getUint32(12, true);
  const windowCount = view.getUint16(16, true);
  const monthCount = view.getUint16(18, true);
  const scale = view.getUint16(20, true);
  const nodata = view.getInt16(22, true);
  if (version !== 1 || windowCount !== 2 || monthCount !== 13 || scale !== 100) {
    throw new Error(`Unsupported chunk contract: v${version}/${windowCount}/${monthCount}/${scale}`);
  }
  const codesOffset = 24;
  const latsOffset = codesOffset + count * 4;
  const lonsOffset = latsOffset + count * 4;
  const valuesOffset = lonsOffset + count * 4;
  const expectedBytes = valuesOffset + windowCount * monthCount * count * 2;
  if (buffer.byteLength !== expectedBytes) {
    throw new Error(`Chunk length mismatch: ${buffer.byteLength} != ${expectedBytes}`);
  }
  return {
    count,
    windowCount,
    monthCount,
    scale,
    nodata,
    codes: new Uint32Array(buffer, codesOffset, count),
    lats: new Float32Array(buffer, latsOffset, count),
    lons: new Float32Array(buffer, lonsOffset, count),
    values: new Int16Array(buffer, valuesOffset, windowCount * monthCount * count),
  };
}

export class ClimateDataStore {
  constructor() {
    this.climateManifest = null;
    this.seasonManifest = null;
    this.seasonLatest = null;
    this.regions = null;
    this.regionIndex = [];
    this.chunkCache = new Map();
  }

  async initialize() {
    const [climateManifest, seasonManifest] = await Promise.all([
      fetchJson("./data/climate/manifest.json"),
      fetchJson("./data/season/manifest.json"),
    ]);
    if (climateManifest.mesh_count !== 387717 || climateManifest.element?.code !== "201") {
      throw new Error("気候データ契約が一致しません");
    }
    this.climateManifest = climateManifest;
    this.seasonManifest = seasonManifest;
    const version = encodeURIComponent(seasonManifest.dataset_id);
    const [seasonLatest, regions] = await Promise.all([
      fetchJson(`./data/season/${seasonManifest.files.latest.path}?v=${version}`),
      fetchJson(`./data/season/${seasonManifest.files.regions.path}?v=${version}`),
    ]);
    if (seasonLatest.dataset_id !== seasonManifest.dataset_id || regions.features?.length !== 385) {
      throw new Error("季節予報データ契約が一致しません");
    }
    this.seasonLatest = seasonLatest;
    this.regions = regions;
    this.regionIndex = regions.features.map((feature) => ({
      feature,
      bounds: geometryBounds(feature.geometry),
    }));
    return this;
  }

  climateRasterPath(state) {
    const files = this.climateManifest.rasters.files;
    const group = state.mode === "difference" ? "difference" : state.window;
    const entry = files[group]?.[String(state.month)];
    if (!entry) throw new Error(`気候画像がありません: ${group}/${state.month}`);
    return `./data/climate/${entry.path}?v=${encodeURIComponent(this.climateManifest.dataset_id)}`;
  }

  async loadChunk(meshCode) {
    const code = String(meshCode).padStart(8, "0");
    const prefix = code.slice(0, 4);
    if (this.chunkCache.has(prefix)) return this.chunkCache.get(prefix);
    const entry = this.climateManifest.chunks[prefix];
    if (!entry) return null;
    const promise = fetch(`./data/climate/${entry.path}?v=${encodeURIComponent(this.climateManifest.dataset_id)}`)
      .then((response) => {
        if (!response.ok) throw new Error(`${entry.path}: HTTP ${response.status}`);
        return response.arrayBuffer();
      })
      .then(parseChunk)
      .catch((error) => {
        this.chunkCache.delete(prefix);
        throw error;
      });
    this.chunkCache.set(prefix, promise);
    return promise;
  }

  async meshRecord(meshCode) {
    const codeText = String(meshCode).padStart(8, "0");
    const chunk = await this.loadChunk(codeText);
    if (!chunk) return null;
    const index = binarySearch(chunk.codes, Number(codeText));
    if (index < 0) return null;
    const values = {};
    this.climateManifest.windows.forEach((window, windowIndex) => {
      values[window.id] = {};
      this.climateManifest.months.forEach((month, monthIndex) => {
        const raw = chunk.values[(windowIndex * chunk.monthCount + monthIndex) * chunk.count + index];
        values[window.id][month.id] = raw === chunk.nodata ? null : raw / chunk.scale;
      });
    });
    return {
      meshCode: codeText,
      centerLat: chunk.lats[index],
      centerLon: chunk.lons[index],
      values,
    };
  }

  forecastTerm(product, term) {
    return this.seasonLatest.products?.[product]?.terms?.[Number(term)] || null;
  }

  forecastProduct(product) {
    return this.seasonLatest.products?.[product] || null;
  }

  regionAtLatLon(lat, lon) {
    for (const entry of this.regionIndex) {
      const bounds = entry.bounds;
      if (lon < bounds.west || lon > bounds.east || lat < bounds.south || lat > bounds.north) continue;
      if (pointInGeometry(lon, lat, entry.feature.geometry)) return entry.feature;
    }
    return null;
  }
}

export { fetchJson };
