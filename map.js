const JAPAN_BOUNDS = L.latLngBounds([19.5, 121.5], [46.8, 154.8]);
const START_VIEW = { center: [37.2, 137.2], zoom: 5 };
const BASES = {
  blank: null,
  pale: {
    url: "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
  },
  standard: {
    url: "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>',
  },
};
const FORECAST_COLORS = ["#315eb3", "#b59a31", "#d04a3e"];
const FORECAST_NEUTRAL = "#646f78";
const DEFAULT_FORECAST_CLASS_LABELS = ["低い", "平年並", "高い"];

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
    this.map.createPane("climatePane").style.zIndex = 330;
    this.map.createPane("seasonPane").style.zIndex = 410;
    this.map.createPane("boundaryPane").style.zIndex = 430;
    this.map.createPane("selectionPane").style.zIndex = 450;
    this.boundaryRenderer = L.canvas({ pane: "boundaryPane", padding: 0.3 });
    this.seasonRenderer = L.canvas({ pane: "seasonPane", padding: 0.3 });
    this.baseLayer = null;
    this.baseId = "pale";
    this.climateLayer = null;
    this.seasonLayer = null;
    this.boundaryLayer = null;
    this.selectionLayer = null;
    this.hoverTooltip = L.tooltip({ direction: "top", offset: [0, -6], opacity: 0.94, className: "climate-tooltip" });
    this.setBase("pale");
    this.map.on("click", (event) => this.handlers.onMapClick?.(event.latlng));
    this.map.on("moveend zoomend", () => this.handlers.onViewChange?.(this.viewState()));
    this.map.on("mousemove", (event) => this.handlers.onPointerMove?.(event.latlng));
    this.map.on("mouseout", () => this.handlers.onPointerLeave?.());
  }

  setBase(id) {
    if (!(id in BASES)) id = "pale";
    if (this.baseLayer) this.map.removeLayer(this.baseLayer);
    this.baseLayer = null;
    this.baseId = id;
    const config = BASES[id];
    if (config) {
      this.baseLayer = L.tileLayer(config.url, {
        minZoom: 4,
        maxZoom: 12,
        maxNativeZoom: 18,
        crossOrigin: true,
        attribution: config.attribution,
        updateWhenIdle: true,
      }).addTo(this.map);
    }
    return id;
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
      style: { color: "#273a47", weight: 0.65, opacity: 0.82, fill: false },
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
  }

  setSeasonOverlay(regions, term, visible, opacity = 0.28, classLabels = DEFAULT_FORECAST_CLASS_LABELS) {
    if (this.seasonLayer) this.map.removeLayer(this.seasonLayer);
    this.seasonLayer = null;
    if (!visible || !term || !Object.keys(term.regions || {}).length) return;
    const resolved = term.regions;
    this.seasonLayer = L.geoJSON(regions, {
      pane: "seasonPane",
      renderer: this.seasonRenderer,
      style: (feature) => {
        const forecast = resolved[feature.properties.code];
        const dominant = forecast ? dominantClass(forecast.probabilities) : -1;
        return {
          color: "#28333c",
          weight: 0.75,
          opacity: 0.85,
          dashArray: "5 4",
          fillColor: dominant >= 0 ? FORECAST_COLORS[dominant] : FORECAST_NEUTRAL,
          fillOpacity: forecast ? opacity : 0,
        };
      },
      onEachFeature: (feature, layer) => {
        const forecast = resolved[feature.properties.code];
        if (!forecast) return;
        const p = forecast.probabilities;
        layer.bindTooltip(
          `<b>${feature.properties.name}</b><br>${forecast.forecast_region_name}<br>${leadClassLabel(p, classLabels)}<br>${classLabels[0]} ${p[0]}%｜${classLabels[1]} ${p[1]}%｜${classLabels[2]} ${p[2]}%`,
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

  showHover(latlng, html) {
    this.hoverTooltip.setLatLng(latlng).setContent(html).addTo(this.map);
  }

  hideHover() {
    this.map.removeLayer(this.hoverTooltip);
  }

  resetView() {
    this.map.fitBounds(JAPAN_BOUNDS, { padding: [10, 10] });
  }

  setView(lat, lon, zoom) {
    this.map.setView([lat, lon], zoom);
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
        context.drawImage(element, layerRect.left - rect.left, layerRect.top - rect.top, layerRect.width, layerRect.height);
      } catch {
        // The same-origin climate raster and canvas overlays remain exportable.
      }
    }

    context.fillStyle = "rgba(255,255,255,.94)";
    roundRect(context, 14, 14, Math.min(rect.width - 28, 620), 66, 6);
    context.fill();
    context.fillStyle = "#18323d";
    context.font = "700 18px -apple-system, BlinkMacSystemFont, sans-serif";
    context.fillText("Nature Wx Lab｜気候ものさしナビ", 26, 40);
    context.font = "12px -apple-system, BlinkMacSystemFont, sans-serif";
    context.fillText(payload.subtitle.slice(0, 88), 26, 62);

    const legend = payload.legend;
    const legendWidth = Math.min(430, rect.width - 28);
    const legendX = rect.width - legendWidth - 14;
    const legendY = rect.height - 84;
    context.fillStyle = "rgba(255,255,255,.94)";
    roundRect(context, legendX, legendY, legendWidth, 70, 6);
    context.fill();
    context.fillStyle = "#18323d";
    context.font = "700 12px -apple-system, BlinkMacSystemFont, sans-serif";
    context.fillText(legend.title, legendX + 12, legendY + 18);
    const barX = legendX + 12;
    const barY = legendY + 28;
    const barWidth = legendWidth - 24;
    legend.colors.forEach((color, index) => {
      context.fillStyle = color;
      context.fillRect(barX + (barWidth * index) / legend.colors.length, barY, barWidth / legend.colors.length + 1, 14);
    });
    context.fillStyle = "#30444d";
    context.font = "10px -apple-system, BlinkMacSystemFont, sans-serif";
    context.fillText(legend.low, barX, barY + 29);
    context.textAlign = "center";
    context.fillText(legend.middle, barX + barWidth / 2, barY + 29);
    context.textAlign = "right";
    context.fillText(legend.high, barX + barWidth, barY + 29);
    context.textAlign = "left";

    const detailWidth = Math.min(520, rect.width - 28);
    const detailHeight = 62;
    const detailY = legendY - detailHeight - 12;
    context.fillStyle = "rgba(255,255,255,.94)";
    roundRect(context, 14, detailY, detailWidth, detailHeight, 6);
    context.fill();
    context.fillStyle = "#30444d";
    context.font = "10px -apple-system, BlinkMacSystemFont, sans-serif";
    context.fillText(payload.detail.slice(0, 78), 24, detailY + 18);
    context.fillText((payload.forecastDetail || "季節予報地域: 地点未選択").slice(0, 78), 24, detailY + 35);
    context.fillText("灰色＝同率首位｜気候平均：独自算出1km面｜季節予報：気象庁・地域確率", 24, detailY + 52);
    return imageToBlob(canvas);
  }
}

export { FORECAST_COLORS };
