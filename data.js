const decoder = new TextDecoder();
const CLIMATE_ROOT = "./data/climate";
const EXPECTED_MESH_COUNT = 387717;

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

async function fetchOptionalJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function cleanRelativePath(value) {
  const path = String(value || "").replace(/^\.\//, "").replace(/^\/+/, "");
  if (!path || path.includes("..") || path.includes("\\")) throw new Error(`Unsafe data path: ${value}`);
  return path;
}

function directoryOf(path) {
  const clean = cleanRelativePath(path);
  const slash = clean.lastIndexOf("/");
  return slash < 0 ? "" : clean.slice(0, slash);
}

function climateUrl(base, path) {
  const relative = cleanRelativePath(path);
  return `${CLIMATE_ROOT}/${base ? `${base}/` : ""}${relative}`;
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

function typedValues(buffer, offset, length, dtypeCode) {
  if (dtypeCode === 1) return new Int16Array(buffer, offset, length);
  if (dtypeCode === 2) return new Uint16Array(buffer, offset, length);
  if (dtypeCode === 3) return new Uint32Array(buffer, offset, length);
  throw new Error(`Unsupported climate dtype: ${dtypeCode}`);
}

function parseChunkV1(buffer, view, expected) {
  const count = view.getUint32(12, true);
  const windowCount = view.getUint16(16, true);
  const monthCount = view.getUint16(18, true);
  const scale = view.getUint16(20, true);
  const nodata = view.getInt16(22, true);
  if (view.getUint32(8, true) !== 1 || windowCount !== 2 || monthCount !== 13 || scale !== 100) {
    throw new Error(`Unsupported v1 chunk contract: ${windowCount}/${monthCount}/${scale}`);
  }
  if (expected?.version !== undefined && Number(expected.version) !== 1) throw new Error("Climate v1 manifest mismatch");
  return { count, windowCount, monthCount, scale, nodata, dtypeCode: 1, headerBytes: 24 };
}

function parseChunkV2(buffer, view, expected) {
  const count = view.getUint32(12, true);
  const windowCount = view.getUint16(16, true);
  const monthCount = view.getUint16(18, true);
  const scale = view.getUint16(20, true);
  const dtypeCode = view.getUint8(22);
  const reserved = view.getUint8(23);
  const nodataBits = view.getUint32(24, true);
  const nodata = dtypeCode === 1
    ? ((nodataBits & 0xffff) >= 0x8000 ? (nodataBits & 0xffff) - 0x10000 : nodataBits & 0xffff)
    : dtypeCode === 2
      ? nodataBits & 0xffff
      : nodataBits;
  if (view.getUint32(8, true) !== 2 || windowCount !== 2 || ![12, 13].includes(monthCount)
      || scale !== 100 || ![1, 2, 3].includes(dtypeCode) || reserved !== 0) {
    throw new Error(`Unsupported v2 chunk contract: ${windowCount}/${monthCount}/${scale}/${dtypeCode}`);
  }
  if (expected?.version !== undefined && Number(expected.version) !== 2) throw new Error("Climate v2 manifest mismatch");
  return { count, windowCount, monthCount, scale, nodata, dtypeCode, headerBytes: 28 };
}

function parseChunk(buffer, manifest) {
  if (buffer.byteLength < 24) throw new Error("Climate chunk is shorter than its header");
  const view = new DataView(buffer);
  const magic = decoder.decode(new Uint8Array(buffer, 0, 8)).replace(/\0+$/, "");
  const expected = manifest.chunk_format || {};
  const header = magic === "NWCBCH1"
    ? parseChunkV1(buffer, view, expected)
    : magic === "NWCBCH2"
      ? parseChunkV2(buffer, view, expected)
      : null;
  if (!header) throw new Error(`Unknown chunk magic: ${magic}`);
  if (expected.magic && expected.magic !== magic) throw new Error(`Chunk magic mismatch: ${magic}`);
  if (expected.header_bytes !== undefined && Number(expected.header_bytes) !== header.headerBytes) {
    throw new Error("Chunk header length does not match the element manifest");
  }
  if (expected.dtype_code !== undefined && Number(expected.dtype_code) !== header.dtypeCode) {
    throw new Error("Chunk dtype does not match the element manifest");
  }
  if (expected.nodata !== undefined && Number(expected.nodata) !== header.nodata) {
    throw new Error("Chunk nodata value does not match the element manifest");
  }
  if (header.monthCount !== manifest.months.length || header.windowCount !== manifest.windows.length) {
    throw new Error("Chunk dimensions do not match the element manifest");
  }

  const codesOffset = header.headerBytes;
  const latsOffset = codesOffset + header.count * 4;
  const lonsOffset = latsOffset + header.count * 4;
  const valuesOffset = lonsOffset + header.count * 4;
  const valueCount = header.windowCount * header.monthCount * header.count;
  const bytesPerValue = header.dtypeCode === 3 ? 4 : 2;
  const expectedBytes = valuesOffset + valueCount * bytesPerValue;
  if (buffer.byteLength !== expectedBytes) {
    throw new Error(`Chunk length mismatch: ${buffer.byteLength} != ${expectedBytes}`);
  }
  return {
    ...header,
    codes: new Uint32Array(buffer, codesOffset, header.count),
    lats: new Float32Array(buffer, latsOffset, header.count),
    lons: new Float32Array(buffer, lonsOffset, header.count),
    values: typedValues(buffer, valuesOffset, valueCount, header.dtypeCode),
  };
}

function catalogEntries(catalog) {
  const source = catalog?.elements || {};
  if (Array.isArray(source)) {
    return source.map((entry) => [String(entry.code || entry.element?.code), entry]);
  }
  return Object.entries(source).map(([code, entry]) => [String(code), entry]);
}

function manifestPathFor(code, entry) {
  return entry?.manifest_path || entry?.manifest?.path || entry?.path
    || (code === "201" ? "manifest.json" : `elements/${code}/manifest.json`);
}

export class ClimateDataStore {
  constructor() {
    this.climateCatalog = null;
    this.elementEntries = new Map();
    this.elementManifests = new Map();
    this.activeElementCode = "201";
    this.climateManifest = null;
    this.climateBase = "";
    this.seasonManifest = null;
    this.seasonLatest = null;
    this.regions = null;
    this.regionIndex = [];
    this.chunkCache = new Map();
  }

  async initialize(requestedElement = "201") {
    const [catalog, seasonManifest] = await Promise.all([
      fetchOptionalJson(`${CLIMATE_ROOT}/catalog.json`),
      fetchJson("./data/season/manifest.json"),
    ]);
    this.seasonManifest = seasonManifest;

    if (catalog) {
      if (catalog.mesh_count !== undefined && Number(catalog.mesh_count) !== EXPECTED_MESH_COUNT) {
        throw new Error("気候カタログのメッシュ数が一致しません");
      }
      this.climateCatalog = catalog;
      catalogEntries(catalog).forEach(([code, entry]) => this.elementEntries.set(code, entry));
    } else {
      const legacy = await fetchJson(`${CLIMATE_ROOT}/manifest.json`);
      this.climateCatalog = {
        schema_version: 1,
        default_element: "201",
        elements: { "201": { code: "201", name: legacy.element?.name, manifest_path: "manifest.json" } },
      };
      this.elementEntries.set("201", this.climateCatalog.elements["201"]);
      this.elementManifests.set("201", { manifest: legacy, base: "" });
    }
    if (!this.elementEntries.has("201")) {
      this.elementEntries.set("201", { code: "201", name: "平均気温", manifest_path: "manifest.json" });
    }

    const initialCode = this.elementEntries.has(String(requestedElement))
      ? String(requestedElement)
      : String(this.climateCatalog.default_element || "201");
    await this.setElement(initialCode);

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

  elements() {
    return [...this.elementEntries.entries()].map(([code, entry]) => ({ code, ...entry }));
  }

  hasElement(code) {
    return this.elementEntries.has(String(code));
  }

  async setElement(code) {
    const elementCode = String(code);
    const entry = this.elementEntries.get(elementCode);
    if (!entry) throw new Error(`Unknown climate element: ${elementCode}`);
    let loaded = this.elementManifests.get(elementCode);
    if (!loaded) {
      const manifestPath = cleanRelativePath(manifestPathFor(elementCode, entry));
      loaded = {
        manifest: await fetchJson(`${CLIMATE_ROOT}/${manifestPath}`),
        base: directoryOf(manifestPath),
      };
      this.elementManifests.set(elementCode, loaded);
    }
    const manifestCode = String(loaded.manifest.element?.code || elementCode);
    if (loaded.manifest.mesh_count !== EXPECTED_MESH_COUNT || manifestCode !== elementCode) {
      throw new Error(`気候データ契約が一致しません: ${elementCode}`);
    }
    this.activeElementCode = elementCode;
    this.climateManifest = loaded.manifest;
    this.climateBase = loaded.base;
    return loaded.manifest;
  }

  climateAssetPath(path, elementCode = this.activeElementCode) {
    const loaded = this.elementManifests.get(String(elementCode));
    if (!loaded) throw new Error(`Element manifest is not loaded: ${elementCode}`);
    return climateUrl(loaded.base, path);
  }

  prefecturePath() {
    const local = this.climateManifest.static?.prefectures;
    if (local?.path) return this.climateAssetPath(local.path);
    const shared = this.climateCatalog.static?.prefectures || this.climateCatalog.prefectures;
    if (shared?.path) return climateUrl("", shared.path);
    const assetPath = Object.keys(this.climateCatalog.assets || {})
      .find((path) => /(^|\/)prefectures\.geojson$/.test(path));
    if (assetPath) return climateUrl("", assetPath);
    const legacy = this.elementManifests.get("201");
    const legacyPrefectures = legacy?.manifest.static?.prefectures;
    if (legacyPrefectures?.path) return climateUrl(legacy.base, legacyPrefectures.path);
    return null;
  }

  climateRasterPath(state) {
    const files = this.climateManifest.rasters.files;
    const group = state.mode === "difference" ? "difference" : state.window;
    const entry = files[group]?.[String(state.month)];
    if (!entry) throw new Error(`気候画像がありません: ${this.activeElementCode}/${group}/${state.month}`);
    return `${this.climateAssetPath(entry.path)}?v=${encodeURIComponent(this.climateManifest.dataset_id)}`;
  }

  async loadChunk(meshCode, elementCode = this.activeElementCode) {
    const code = String(meshCode).padStart(8, "0");
    const prefix = code.slice(0, 4);
    const cacheKey = `${elementCode}:${prefix}`;
    if (this.chunkCache.has(cacheKey)) return this.chunkCache.get(cacheKey);
    const loaded = this.elementManifests.get(String(elementCode));
    if (!loaded) throw new Error(`Element manifest is not loaded: ${elementCode}`);
    const entry = loaded.manifest.chunks[prefix];
    if (!entry) return null;
    const path = `${climateUrl(loaded.base, entry.path)}?v=${encodeURIComponent(loaded.manifest.dataset_id)}`;
    const promise = fetch(path)
      .then((response) => {
        if (!response.ok) throw new Error(`${entry.path}: HTTP ${response.status}`);
        return response.arrayBuffer();
      })
      .then((buffer) => parseChunk(buffer, loaded.manifest))
      .catch((error) => {
        this.chunkCache.delete(cacheKey);
        throw error;
      });
    this.chunkCache.set(cacheKey, promise);
    return promise;
  }

  async meshRecord(meshCode, elementCode = this.activeElementCode) {
    const codeText = String(meshCode).padStart(8, "0");
    const loaded = this.elementManifests.get(String(elementCode));
    if (!loaded) throw new Error(`Element manifest is not loaded: ${elementCode}`);
    const chunk = await this.loadChunk(codeText, elementCode);
    if (!chunk) return null;
    const index = binarySearch(chunk.codes, Number(codeText));
    if (index < 0) return null;
    const values = {};
    loaded.manifest.windows.forEach((window, windowIndex) => {
      values[window.id] = {};
      loaded.manifest.months.forEach((month, monthIndex) => {
        const raw = chunk.values[(windowIndex * chunk.monthCount + monthIndex) * chunk.count + index];
        values[window.id][month.id] = raw === chunk.nodata ? null : raw / chunk.scale;
      });
    });
    return {
      elementCode: String(elementCode),
      meshCode: codeText,
      centerLat: chunk.lats[index],
      centerLon: chunk.lons[index],
      values,
    };
  }

  forecastProduct(product) {
    return this.seasonLatest.products?.[product] || null;
  }

  seasonElement(product, element) {
    const source = this.forecastProduct(product);
    if (!source) return null;
    if (source.elements) return source.elements[element] || null;
    if (element === "temperature" && Array.isArray(source.terms)) {
      return { status: "available", supported: true, unavailable_reason: null, terms: source.terms };
    }
    return { status: "unavailable", supported: false, unavailable_reason: "not_supported_by_dataset", terms: [] };
  }

  seasonElementMetadata(element) {
    if (this.seasonLatest.elements?.[element]) return this.seasonLatest.elements[element];
    if (element === "temperature" && this.seasonLatest.element) return this.seasonLatest.element;
    return null;
  }

  forecastTerm(product, element, termId) {
    const terms = this.seasonElement(product, element)?.terms || [];
    return terms.find((term) => String(term.id) === String(termId)) || null;
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

export { fetchJson, parseChunk };
