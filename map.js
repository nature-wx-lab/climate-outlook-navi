const START_VIEW = Object.freeze({
  center: [37.2, 137.2],
  zoom: 6,
});
const BASES = {
  blank: {
    url: "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
    className: "white-context-map",
    minNativeZoom: 2,
    maxNativeZoom: 18,
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
  },
  pale: {
    url: "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
    minNativeZoom: 2,
    maxNativeZoom: 18,
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
  },
  standard: {
    url: "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
    minNativeZoom: 2,
    maxNativeZoom: 18,
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
  },
};
const DETAIL_MAP = Object.freeze({
  url: "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
  minNativeZoom: 2,
  maxNativeZoom: 18,
});
const TERRAIN_MAPS = Object.freeze({
  color: {
    url: "https://cyberjapandata.gsi.go.jp/xyz/relief/{z}/{x}/{y}.png",
    minNativeZoom: 5,
    maxNativeZoom: 15,
  },
  mono: {
    url: "https://cyberjapandata.gsi.go.jp/xyz/hillshademap/{z}/{x}/{y}.png",
    minNativeZoom: 2,
    maxNativeZoom: 16,
  },
});
const GSI_ATTRIBUTION = '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>';
const FORECAST_COLORS = ["#315eb3", "#b59a31", "#d04a3e"];
const FORECAST_NEUTRAL = "#646f78";
const DEFAULT_FORECAST_CLASS_LABELS = ["低い", "平年並", "高い"];
export const TEMPERATURE_ANOMALY_LEGEND = Object.freeze({
  title: "期間平均気温の平年差",
  unit: "℃",
  breaks: [-5, -3, -2, -1, -0.5, 0.5, 1, 2, 3, 5],
  colors: [
    "#313695", "#4575b4", "#74add1", "#abd9e9", "#e0f3f8", "#f7f7f7",
    "#fee090", "#fdae61", "#f46d43", "#d73027", "#a50026",
  ],
});

const MAJOR_PLACE_NAMES = new Set([
  "稚内", "旭川", "札幌", "釧路", "青森", "盛岡", "秋田", "仙台", "山形", "福島",
  "新潟", "富山", "金沢", "福井", "宇都宮", "前橋", "水戸", "熊谷", "東京", "千葉",
  "横浜", "甲府", "長野", "岐阜", "静岡", "名古屋", "津", "彦根", "京都", "大阪",
  "神戸", "奈良", "和歌山", "鳥取", "松江", "岡山", "広島", "山口", "徳島", "高松",
  "松山", "高知", "福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "那覇",
  "名瀬", "石垣島", "父島",
]);
const REGIONAL_PLACE_LABELS = Object.freeze([
  { name: "中国", lon: 116.4, lat: 39.9, rank: 0, minZoom: 4, regional: true },
  { name: "韓国", lon: 127.8, lat: 36.5, rank: 0, minZoom: 4, regional: true },
  { name: "北朝鮮", lon: 127.1, lat: 40.2, rank: 0, minZoom: 4, regional: true },
  { name: "ロシア", lon: 142.1, lat: 46.2, rank: 0, minZoom: 4, regional: true },
  { name: "台湾", lon: 121.0, lat: 23.8, rank: 0, minZoom: 4, regional: true },
]);

const CanvasSquareMarker = L.CircleMarker.extend({
  _updatePath() {
    if (!this._renderer?._drawing || this._empty()) return;
    const radius = Math.max(this._radius, 1);
    const context = this._renderer._ctx;
    const left = this._point.x - radius;
    const top = this._point.y - radius;
    const size = radius * 2;
    const selected = this.options.selected === true;
    context.save();
    context.globalAlpha = this.options.opacity ?? 1;
    context.fillStyle = selected ? "rgba(7, 42, 54, 0.42)" : "rgba(13, 43, 54, 0.34)";
    context.fillRect(left + 1.35, top + 1.55, size + 0.45, size + 0.45);
    if (selected) {
      context.strokeStyle = "rgba(255, 255, 255, 0.94)";
      context.lineWidth = 3.2;
      context.strokeRect(left - 1.6, top - 1.6, size + 3.2, size + 3.2);
    }
    context.fillStyle = selected ? "#0b3441" : "rgba(20, 52, 62, 0.84)";
    context.fillRect(left - 0.8, top - 0.8, size + 1.6, size + 1.6);
    context.globalAlpha = this.options.fillOpacity ?? 1;
    context.fillStyle = this.options.fillColor || "#ffffff";
    context.fillRect(left, top, size, size);
    context.globalAlpha = this.options.opacity ?? 1;
    context.strokeStyle = "rgba(255, 255, 255, 0.78)";
    context.lineWidth = 0.9;
    context.beginPath();
    context.moveTo(left + 0.45, top + size - 0.45);
    context.lineTo(left + 0.45, top + 0.45);
    context.lineTo(left + size - 0.45, top + 0.45);
    context.stroke();
    context.strokeStyle = "rgba(11, 38, 48, 0.34)";
    context.beginPath();
    context.moveTo(left + size - 0.45, top + 0.45);
    context.lineTo(left + size - 0.45, top + size - 0.45);
    context.lineTo(left + 0.45, top + size - 0.45);
    context.stroke();
    context.restore();
  },

  setSelected(selected) {
    this.options.selected = selected === true;
    this.setRadius(selected ? 6 : 4);
    this.redraw();
    return this;
  },
});

function labelCandidates(stations) {
  const stationLabels = (stations || []).flatMap((station) => {
    if (!Number.isFinite(station.lat) || !Number.isFinite(station.lon) || !station.name) return [];
    const rank = MAJOR_PLACE_NAMES.has(station.name) ? 0 : station.station_type === "surface" ? 1 : 2;
    return [{
      name: station.name,
      lat: station.lat,
      lon: station.lon,
      rank,
      minZoom: rank === 0 ? 5 : rank === 1 ? 7 : 9,
      regional: false,
    }];
  });
  return [...REGIONAL_PLACE_LABELS, ...stationLabels]
    .sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name, "ja"));
}

function rectanglesOverlap(a, b) {
  return a.x < b.x + b.width
    && a.x + a.width > b.x
    && a.y < b.y + b.height
    && a.y + a.height > b.y;
}

const CanvasPlaceLabelLayer = L.Layer.extend({
  initialize(options = {}) {
    L.setOptions(this, options);
    this._labels = [];
    this._visible = true;
    this._opacity = 0.8;
    this._frame = null;
  },

  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "leaflet-place-label-layer leaflet-zoom-animated");
    this._canvas.setAttribute("aria-hidden", "true");
    map.getPane(this.options.pane).appendChild(this._canvas);
    map.on("move zoom resize", this._scheduleRedraw, this);
    this._redraw();
  },

  onRemove(map) {
    map.off("move zoom resize", this._scheduleRedraw, this);
    if (this._frame !== null) cancelAnimationFrame(this._frame);
    this._canvas?.remove();
    this._canvas = null;
    this._map = null;
  },

  setLabels(stations) {
    this._labels = labelCandidates(stations);
    this._scheduleRedraw();
    return this;
  },

  setVisible(visible) {
    this._visible = visible === true;
    this._scheduleRedraw();
    return this;
  },

  setOpacity(opacity) {
    this._opacity = Math.min(1, Math.max(0, Number(opacity) || 0));
    this._scheduleRedraw();
    return this;
  },

  _scheduleRedraw() {
    if (!this._map || this._frame !== null) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = null;
      this._redraw();
    });
  },

  _redraw() {
    if (!this._map || !this._canvas) return;
    const size = this._map.getSize();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const width = Math.max(1, Math.round(size.x * dpr));
    const height = Math.max(1, Math.round(size.y * dpr));
    if (this._canvas.width !== width || this._canvas.height !== height) {
      this._canvas.width = width;
      this._canvas.height = height;
      this._canvas.style.width = `${size.x}px`;
      this._canvas.style.height = `${size.y}px`;
    }
    L.DomUtil.setPosition(this._canvas, this._map.containerPointToLayerPoint([0, 0]));
    const context = this._canvas.getContext("2d");
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, size.x, size.y);
    if (!this._visible || this._opacity <= 0) return;

    const zoom = this._map.getZoom();
    const fontSize = zoom <= 5 ? 11 : zoom <= 7 ? 12 : 13;
    const densityLimit = Math.max(18, Math.floor((size.x * size.y) / 28000));
    const maxLabels = Math.min(
      densityLimit,
      zoom <= 5 ? 48 : zoom <= 6 ? 82 : zoom <= 8 ? 160 : 260,
    );
    const occupied = [];
    let drawn = 0;
    context.font = `800 ${fontSize}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
    context.textBaseline = "middle";
    context.textAlign = "left";
    context.lineJoin = "round";
    context.lineWidth = Math.max(2.8, fontSize * 0.27);

    for (const label of this._labels) {
      if (zoom < label.minZoom) continue;
      const point = this._map.latLngToContainerPoint([label.lat, label.lon]);
      if (point.x < -90 || point.x > size.x + 90 || point.y < -30 || point.y > size.y + 30) continue;
      const textWidth = context.measureText(label.name).width;
      const textX = label.regional ? point.x - textWidth / 2 : point.x + 5;
      const rect = {
        x: textX - 3,
        y: point.y - fontSize * 0.7,
        width: textWidth + 6,
        height: fontSize * 1.4,
      };
      if (occupied.some((candidate) => rectanglesOverlap(rect, candidate))) continue;
      occupied.push(rect);
      context.globalAlpha = this._opacity * (label.rank === 0 ? 1 : label.rank === 1 ? 0.86 : 0.7);
      context.strokeStyle = "rgba(255, 255, 255, 0.88)";
      context.fillStyle = "rgba(31, 48, 57, 0.88)";
      if (!label.regional) {
        context.beginPath();
        context.arc(point.x, point.y, label.rank === 0 ? 2.7 : 2.1, 0, Math.PI * 2);
        context.fill();
      }
      context.strokeText(label.name, textX, point.y);
      context.fillText(label.name, textX, point.y);
      drawn += 1;
      if (drawn >= maxLabels) break;
    }
    context.globalAlpha = 1;
  },
});

function temperatureAnomalyColor(value) {
  const index = TEMPERATURE_ANOMALY_LEGEND.breaks.findIndex((limit) => value <= limit);
  return TEMPERATURE_ANOMALY_LEGEND.colors[
    index < 0 ? TEMPERATURE_ANOMALY_LEGEND.colors.length - 1 : index
  ];
}

function signedTemperature(value) {
  const normalized = Math.abs(value) < 0.05 ? 0 : value;
  return `${normalized > 0 ? "+" : ""}${normalized.toFixed(1)}℃`;
}

function dominantClass(probabilities) {
  const max = Math.max(...probabilities);
  const winners = probabilities.map((value, index) => (value === max ? index : -1)).filter((index) => index >= 0);
  return winners.length === 1 ? winners[0] : -1;
}

function leadClassLabel(probabilities, classLabels = DEFAULT_FORECAST_CLASS_LABELS) {
  const max = Math.max(...probabilities);
  const winners = probabilities
    .map((value, index) => (value === max ? classLabels[index] : null))
    .filter(Boolean);
  return winners.length === 1 ? `最多：${winners[0]}` : `同率首位：${winners.join("・")}`;
}

function seasonTooltipContent(featureName, forecast, classLabels) {
  const probabilities = forecast.probabilities;
  const tooltip = document.createElement("span");
  const heading = document.createElement("b");
  heading.textContent = featureName;
  tooltip.append(
    heading,
    document.createElement("br"),
    document.createTextNode(forecast.forecast_region_name),
    document.createElement("br"),
    document.createTextNode(leadClassLabel(probabilities, classLabels)),
    document.createElement("br"),
    document.createTextNode(
      `${classLabels[0]} ${probabilities[0]}%｜${classLabels[1]} ${probabilities[1]}%｜${classLabels[2]} ${probabilities[2]}%`,
    ),
  );
  return tooltip;
}

function recentTemperatureTooltip(point) {
  const tooltip = document.createElement("span");
  const heading = document.createElement("b");
  heading.textContent = point.name;
  tooltip.append(
    heading,
    document.createElement("br"),
    document.createTextNode(
      `${point.prefecture}｜${point.stationType === "amedas" ? "アメダス" : "気象台等"}`,
    ),
    document.createElement("br"),
    document.createTextNode(`平年差 ${signedTemperature(point.anomaly)}`),
    document.createElement("br"),
    document.createTextNode(
      `期間平均 ${point.observedMean.toFixed(1)}℃｜平年 ${point.normalMean.toFixed(1)}℃`,
    ),
    document.createElement("br"),
    document.createTextNode(`有効 ${point.validDays}/${point.expectedDays}日`),
  );
  return tooltip;
}

function imageToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("PNGを生成できませんでした"))), "image/png");
  });
}

function roundRect(context, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.arcTo(x + width, y, x + width, y + height, r);
  context.arcTo(x + width, y + height, x, y + height, r);
  context.arcTo(x, y + height, x, y, r);
  context.arcTo(x, y, x + width, y, r);
  context.closePath();
}

function fitCanvasText(context, value, maxWidth) {
  const text = String(value ?? "");
  if (context.measureText(text).width <= maxWidth) return text;
  const characters = Array.from(text);
  while (characters.length && context.measureText(`${characters.join("")}…`).width > maxWidth) {
    characters.pop();
  }
  return `${characters.join("")}…`;
}

function wrapCanvasText(context, value, maxWidth) {
  const characters = Array.from(String(value ?? ""));
  const lines = [];
  let line = "";
  characters.forEach((character) => {
    const candidate = `${line}${character}`;
    if (line && context.measureText(candidate).width > maxWidth) {
      const lineCharacters = Array.from(line);
      const minimumBreakIndex = Math.floor(lineCharacters.length * 0.35);
      let breakIndex = -1;
      lineCharacters.forEach((lineCharacter, index) => {
        if (["｜", " ", "・", "、", "。"].includes(lineCharacter) && index >= minimumBreakIndex) {
          breakIndex = index + 1;
        }
      });
      if (breakIndex > 0) {
        lines.push(lineCharacters.slice(0, breakIndex).join("").trimEnd());
        line = `${lineCharacters.slice(breakIndex).join("").trimStart()}${character}`;
      } else {
        lines.push(line);
        line = character;
      }
    } else {
      line = candidate;
    }
  });
  if (line || !lines.length) lines.push(line);
  return lines;
}

export class ClimateMap {
  constructor(elementId, handlers = {}) {
    this.handlers = handlers;
    this.map = L.map(elementId, {
      preferCanvas: true,
      zoomControl: false,
      minZoom: 4,
      maxZoom: 12,
      maxBounds: [[17, 116], [50, 162]],
      maxBoundsViscosity: 0.7,
    }).setView(START_VIEW.center, START_VIEW.zoom);
    L.control.zoom({ position: "bottomleft" }).addTo(this.map);
    this.map.createPane("detailMapPane").style.zIndex = 205;
    this.map.createPane("terrainPane").style.zIndex = 210;
    this.map.createPane("climatePane").style.zIndex = 330;
    this.map.createPane("recentTemperaturePane").style.zIndex = 390;
    this.map.createPane("seasonPane").style.zIndex = 410;
    this.map.createPane("boundaryPane").style.zIndex = 430;
    this.map.createPane("placeLabelPane").style.zIndex = 440;
    this.map.createPane("selectionPane").style.zIndex = 450;
    this.map.getPane("recentTemperaturePane").style.pointerEvents = "none";
    this.map.getPane("boundaryPane").style.pointerEvents = "none";
    this.map.getPane("placeLabelPane").style.pointerEvents = "none";
    this.map.getPane("selectionPane").style.pointerEvents = "none";
    this.boundaryRenderer = L.canvas({ pane: "boundaryPane", padding: 0.3 });
    this.seasonRenderer = L.canvas({ pane: "seasonPane", padding: 0.3 });
    this.recentTemperatureRenderer = L.canvas({ pane: "recentTemperaturePane", padding: 0.3 });
    this.baseLayer = null;
    this.baseId = "pale";
    this.detailMapLayer = null;
    this.terrainLayer = null;
    this.terrainStyle = "color";
    this.climateLayer = null;
    this.seasonLayer = null;
    this.recentTemperatureLayer = null;
    this.recentTemperatureMarkers = new Map();
    this.boundaryLayer = null;
    this.selectionLayer = null;
    this.placeLabelLayer = new CanvasPlaceLabelLayer({ pane: "placeLabelPane" }).addTo(this.map);
    this.setBase("pale");
    this.map.on("click", (event) => this.handlers.onMapClick?.(event.latlng));
    this.map.on("moveend zoomend", () => this.handlers.onViewChange?.(this.viewState()));
    this.map.on("mousemove", (event) => this.handlers.onPointerMove?.(event.latlng));
    this.map.getContainer().addEventListener("mouseleave", () => this.handlers.onPointerLeave?.());
    this.map.on("movestart zoomstart", () => this.handlers.onPointerLeave?.());
  }

  setBase(id) {
    if (!(id in BASES)) id = "pale";
    if (this.baseLayer) this.map.removeLayer(this.baseLayer);
    this.baseLayer = null;
    this.baseId = id;
    const config = BASES[id];
    this.baseLayer = L.tileLayer(config.url, {
      minZoom: 4,
      maxZoom: 12,
      minNativeZoom: config.minNativeZoom,
      maxNativeZoom: config.maxNativeZoom,
      crossOrigin: true,
      className: config.className || "",
      attribution: config.attribution,
      updateWhenIdle: true,
    }).addTo(this.map);
    return id;
  }

  setPlaceLabels(stations) {
    this.placeLabelLayer.setLabels(stations);
  }

  setPlaceLabelsVisible(visible) {
    this.placeLabelLayer.setVisible(visible);
  }

  setPlaceLabelOpacity(opacity) {
    this.placeLabelLayer.setOpacity(opacity);
  }

  setDetailMap(visible, opacity = 0.7) {
    if (this.detailMapLayer) this.map.removeLayer(this.detailMapLayer);
    this.detailMapLayer = null;
    if (!visible) return;
    this.detailMapLayer = L.tileLayer(DETAIL_MAP.url, {
      pane: "detailMapPane",
      minZoom: 4,
      maxZoom: 12,
      minNativeZoom: DETAIL_MAP.minNativeZoom,
      maxNativeZoom: DETAIL_MAP.maxNativeZoom,
      crossOrigin: true,
      attribution: GSI_ATTRIBUTION,
      opacity,
      updateWhenIdle: true,
    }).addTo(this.map);
  }

  setDetailMapOpacity(opacity) {
    this.detailMapLayer?.setOpacity(opacity);
  }

  setTerrain(visible, style = "color", opacity = 0.35) {
    if (this.terrainLayer) this.map.removeLayer(this.terrainLayer);
    this.terrainLayer = null;
    this.terrainStyle = style in TERRAIN_MAPS ? style : "color";
    if (!visible) return;
    const config = TERRAIN_MAPS[this.terrainStyle];
    this.terrainLayer = L.tileLayer(config.url, {
      pane: "terrainPane",
      minZoom: 4,
      maxZoom: 12,
      minNativeZoom: config.minNativeZoom,
      maxNativeZoom: config.maxNativeZoom,
      crossOrigin: true,
      attribution: GSI_ATTRIBUTION,
      opacity,
      updateWhenIdle: true,
    }).addTo(this.map);
  }

  setTerrainOpacity(opacity) {
    this.terrainLayer?.setOpacity(opacity);
  }

  async setBoundaries(path) {
    if (!path) return;
    const response = await fetch(path, { cache: "force-cache" });
    if (!response.ok) throw new Error(`都道府県境界: HTTP ${response.status}`);
    const data = await response.json();
    if (this.boundaryLayer) this.map.removeLayer(this.boundaryLayer);
    this.boundaryLayer = L.geoJSON(data, {
      pane: "boundaryPane",
      renderer: this.boundaryRenderer,
      interactive: false,
      style: { color: "#1e2328", weight: 0.6, opacity: 0.72, fill: false },
    }).addTo(this.map);
  }

  setClimateRaster(path, render, opacity = 0.84) {
    if (this.climateLayer) this.map.removeLayer(this.climateLayer);
    const bounds = [[render.bounds.south, render.bounds.west], [render.bounds.north, render.bounds.east]];
    this.climateLayer = L.imageOverlay(path, bounds, {
      pane: "climatePane",
      opacity,
      interactive: false,
      className: "climate-raster",
      alt: "全国1km気候平均の表示用縮約画像",
    });
    this.climateLayer.on("load", () => this.handlers.onClimateLoad?.());
    this.climateLayer.on("error", () => this.handlers.onClimateError?.());
    this.climateLayer.addTo(this.map);
  }

  setClimateOpacity(opacity) {
    this.climateLayer?.setOpacity(opacity);
    const image = this.climateLayer?.getElement();
    if (image) image.setAttribute("aria-hidden", opacity <= 0 ? "true" : "false");
  }

  setRecentTemperature(points, visible = true) {
    if (this.recentTemperatureLayer) this.map.removeLayer(this.recentTemperatureLayer);
    this.recentTemperatureLayer = null;
    this.recentTemperatureMarkers.clear();
    this.map.getPane("recentTemperaturePane").style.pointerEvents = visible ? "auto" : "none";
    this.map.getPane("seasonPane").style.pointerEvents = visible ? "none" : "auto";
    if (!visible) return;
    const markers = points.map((point) => {
      const marker = new CanvasSquareMarker([point.lat, point.lon], {
        pane: "recentTemperaturePane",
        renderer: this.recentTemperatureRenderer,
        bubblingMouseEvents: false,
        radius: 4,
        selected: false,
        opacity: 0.96,
        fillColor: temperatureAnomalyColor(point.anomaly),
        fillOpacity: 0.96,
      });
      marker.bindTooltip(recentTemperatureTooltip(point), {
        sticky: true,
        className: "recent-temperature-tooltip",
        direction: "top",
      });
      marker.on("click", () => this.handlers.onRecentStationClick?.(point));
      this.recentTemperatureMarkers.set(point.stationId, marker);
      return marker;
    });
    this.recentTemperatureLayer = L.featureGroup(markers).addTo(this.map);
  }

  selectRecentStation(stationId, pan = false) {
    this.recentTemperatureMarkers.forEach((marker, id) => {
      marker.setSelected(id === stationId);
    });
    const marker = this.recentTemperatureMarkers.get(stationId);
    if (marker && pan) this.map.setView(marker.getLatLng(), Math.max(this.map.getZoom(), 7));
  }

  setSeasonOverlay(regions, term, visible, opacity = 0.28, classLabels = DEFAULT_FORECAST_CLASS_LABELS) {
    if (this.seasonLayer) this.map.removeLayer(this.seasonLayer);
    this.seasonLayer = null;
    if (!visible || !term || !Object.keys(term.regions || {}).length) return;
    const resolved = term.regions;
    this.seasonLayer = L.geoJSON(regions, {
      pane: "seasonPane",
      renderer: this.seasonRenderer,
      bubblingMouseEvents: false,
      filter: (feature) => Boolean(resolved[feature.properties.code]),
      style: (feature) => {
        const forecast = resolved[feature.properties.code];
        const dominant = forecast ? dominantClass(forecast.probabilities) : -1;
        return {
          stroke: false,
          fill: true,
          fillColor: dominant >= 0 ? FORECAST_COLORS[dominant] : FORECAST_NEUTRAL,
          fillOpacity: forecast ? opacity : 0,
        };
      },
      onEachFeature: (feature, layer) => {
        const forecast = resolved[feature.properties.code];
        if (!forecast) return;
        layer.bindTooltip(
          seasonTooltipContent(feature.properties.name, forecast, classLabels),
          { sticky: true, className: "season-tooltip", direction: "top" },
        );
        layer.on("click", (event) => this.handlers.onRegionClick?.({
          latlng: event.latlng,
          class15Code: feature.properties.code,
          class15Name: feature.properties.name,
          forecast,
        }));
      },
    }).addTo(this.map);
  }

  selectMesh(meshCode, bounds, pan = false) {
    if (this.selectionLayer) this.map.removeLayer(this.selectionLayer);
    if (!bounds) return;
    this.selectionLayer = L.rectangle([[bounds.south, bounds.west], [bounds.north, bounds.east]], {
      pane: "selectionPane",
      color: "#111827",
      weight: 2,
      opacity: 1,
      fillColor: "#ffffff",
      fillOpacity: 0.04,
      interactive: false,
    }).addTo(this.map);
    this.selectionLayer.bindTooltip(`1kmメッシュ ${meshCode}`, { permanent: false, direction: "top" });
    if (pan) this.map.setView([bounds.centerLat, bounds.centerLon], Math.max(this.map.getZoom(), 9));
  }

  resetView() {
    this.map.setView(START_VIEW.center, START_VIEW.zoom);
  }

  setView(lat, lon, zoom) {
    this.map.setView([lat, lon], Number.isFinite(zoom) ? zoom : START_VIEW.zoom);
  }

  viewState() {
    const center = this.map.getCenter();
    return { lat: center.lat, lon: center.lng, zoom: this.map.getZoom(), base: this.baseId };
  }

  invalidateSize() {
    this.map.invalidateSize();
  }

  async capture(payload) {
    const mapNode = this.map.getContainer();
    const rect = mapNode.getBoundingClientRect();
    const scale = Math.min(2, window.devicePixelRatio || 1.5);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(rect.width * scale);
    canvas.height = Math.round(rect.height * scale);
    const context = canvas.getContext("2d");
    context.scale(scale, scale);
    context.fillStyle = "#e9f0f4";
    context.fillRect(0, 0, rect.width, rect.height);

    const drawable = [...mapNode.querySelectorAll("img.leaflet-tile-loaded, img.leaflet-image-layer, canvas.leaflet-zoom-animated")];
    for (const element of drawable) {
      const layerRect = element.getBoundingClientRect();
      if (layerRect.width <= 0 || layerRect.height <= 0) continue;
      try {
        if (element instanceof HTMLImageElement && element.decode) await element.decode().catch(() => {});
        let paintNode = element;
        let layerOpacity = 1;
        const layerFilters = [];
        while (paintNode && paintNode !== mapNode) {
          const style = getComputedStyle(paintNode);
          const opacity = Number(style.opacity);
          if (Number.isFinite(opacity)) layerOpacity *= opacity;
          if (style.filter && style.filter !== "none") layerFilters.push(style.filter);
          paintNode = paintNode.parentElement;
        }
        context.save();
        context.globalAlpha = layerOpacity;
        context.filter = layerFilters.join(" ") || "none";
        context.drawImage(element, layerRect.left - rect.left, layerRect.top - rect.top, layerRect.width, layerRect.height);
        context.restore();
      } catch {
        context.restore();
        // The same-origin climate raster and canvas overlays remain exportable.
      }
    }

    const titleWidth = Math.min(rect.width - 28, 620);
    const titleText = "Nature Wx Lab｜気候ものさしナビ";
    let titleFontSize = 18;
    context.font = `700 ${titleFontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
    while (titleFontSize > 14 && context.measureText(titleText).width > titleWidth - 24) {
      titleFontSize -= 1;
      context.font = `700 ${titleFontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
    }
    context.font = "700 12px -apple-system, BlinkMacSystemFont, sans-serif";
    const subtitleLines = wrapCanvasText(context, payload.subtitle, titleWidth - 24);
    const titleHeight = 66 + Math.max(0, subtitleLines.length - 1) * 14;
    context.fillStyle = "rgba(255,255,255,.94)";
    roundRect(context, 14, 14, titleWidth, titleHeight, 6);
    context.fill();
    context.fillStyle = "#18323d";
    context.font = `700 ${titleFontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
    context.fillText(titleText, 26, 40);
    context.font = "700 12px -apple-system, BlinkMacSystemFont, sans-serif";
    subtitleLines.forEach((line, index) => context.fillText(line, 26, 62 + index * 14));

    const detailLines = [...(payload.detailLines || [
      payload.detail,
      payload.forecastDetail || "季節予報地域: 地点未選択",
      "気候平均：気象庁観測から独自算出・独自内挿",
      "標高：国土数値情報 G04-a（国土交通省）",
      "季節予報：気象庁・地域確率｜灰色＝同率首位",
    ])];
    detailLines.push("地図：地理院タイル（国土地理院）");
    if (this.terrainLayer && this.terrainStyle === "color") {
      detailLines.push("色別標高図の海域部：海上保安庁海洋情報部資料");
    }
    const detailWidth = Math.min(520, rect.width - 28);
    const detailHeight = 16 + detailLines.length * 14;
    const detailY = rect.height - detailHeight - 14;
    context.fillStyle = "rgba(255,255,255,.94)";
    roundRect(context, 14, detailY, detailWidth, detailHeight, 6);
    context.fill();
    context.fillStyle = "#30444d";
    context.font = "10px -apple-system, BlinkMacSystemFont, sans-serif";
    detailLines.forEach((line, index) => {
      context.fillText(fitCanvasText(context, line, detailWidth - 20), 24, detailY + 18 + index * 14);
    });

    const legend = payload.legend;
    const legendWidth = 118;
    const legendX = rect.width - legendWidth - 14;
    const legendY = rect.width >= 780 ? 14 : 14 + titleHeight + 12;
    let legendHeadingFontSize = 10;
    context.font = `700 ${legendHeadingFontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
    const fullLegendHeading = `${legend.title}（${legend.unit}）`;
    let legendHeadingLines = [fullLegendHeading];
    if (context.measureText(fullLegendHeading).width > legendWidth - 20) {
      while (legendHeadingFontSize > 8 && context.measureText(legend.title).width > legendWidth - 20) {
        legendHeadingFontSize -= 1;
        context.font = `700 ${legendHeadingFontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
      }
      legendHeadingLines = [
        ...wrapCanvasText(context, legend.title, legendWidth - 20),
        `（${legend.unit}）`,
      ];
    }
    const legendHeaderHeight = 14 + legendHeadingLines.length * 12;
    const barHeight = Math.min(260, Math.max(150, detailY - legendY - legendHeaderHeight - 14));
    const legendHeight = legendHeaderHeight + barHeight + 14;
    context.fillStyle = "rgba(255,255,255,.94)";
    roundRect(context, legendX, legendY, legendWidth, legendHeight, 6);
    context.fill();
    context.fillStyle = "#18323d";
    context.font = `700 ${legendHeadingFontSize}px -apple-system, BlinkMacSystemFont, sans-serif`;
    legendHeadingLines.forEach((line, index) => context.fillText(line, legendX + 10, legendY + 16 + index * 12));
    const barX = legendX + 11;
    const barY = legendY + legendHeaderHeight;
    const barWidth = 32;
    const gradient = context.createLinearGradient(0, barY + barHeight, 0, barY);
    legend.colors.forEach((color, index) => gradient.addColorStop(index / (legend.colors.length - 1), color));
    context.fillStyle = gradient;
    context.fillRect(barX, barY, barWidth, barHeight);
    context.strokeStyle = "#52646d";
    context.lineWidth = 1;
    context.strokeRect(barX, barY, barWidth, barHeight);
    context.fillStyle = "#30444d";
    context.font = "700 10px -apple-system, BlinkMacSystemFont, sans-serif";
    legend.ticks.forEach((tick) => {
      const y = barY + (tick.position / 100) * barHeight;
      context.strokeStyle = "rgba(43,57,65,.64)";
      context.beginPath();
      context.moveTo(barX, y);
      context.lineTo(barX + barWidth + 8, y);
      context.stroke();
      context.fillText(tick.label, barX + barWidth + 12, y + 3);
    });
    return imageToBlob(canvas);
  }
}

export { FORECAST_COLORS };
