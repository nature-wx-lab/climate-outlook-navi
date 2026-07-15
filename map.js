const START_VIEW = Object.freeze({
  center: [37.2, 137.2],
  zoom: 5,
});
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
        context.drawImage(element, layerRect.left - rect.left, layerRect.top - rect.top, layerRect.width, layerRect.height);
      } catch {
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

    const detailLines = [
      payload.detail,
      payload.forecastDetail || "季節予報地域: 地点未選択",
      "気候平均：気象庁観測から独自算出・独自内挿",
      "標高：国土数値情報 G04-a（国土交通省）",
      "季節予報：気象庁・地域確率｜灰色＝同率首位",
    ];
    if (this.baseId !== "blank") detailLines.push("地図：地理院タイル（国土地理院）");
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
