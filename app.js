import { ClimateDataStore, meshBounds, meshCodeFromLatLon } from "./data.js?v=20260726-recent-temperature2";
import { ClimateMap, TEMPERATURE_ANOMALY_LEGEND } from "./map.js?v=20260821-default-zoom1";

const ELEMENT_ORDER = ["201", "202", "203", "101", "401", "501", "503", "610"];
const ELEMENT_FALLBACKS = {
  "201": {
    name: "平均気温", unit: "℃", decimals: 2, forecastElement: "temperature",
    definition: "月・年の平均気温です。",
  },
  "202": {
    name: "日最高気温の月平均", unit: "℃", decimals: 2, forecastElement: null,
    definition: "日最高気温を月ごとに平均した値です。月間の最高気温ではありません。",
  },
  "203": {
    name: "日最低気温の月平均", unit: "℃", decimals: 2, forecastElement: null,
    definition: "日最低気温を月ごとに平均した値です。他の気温要素より推定誤差がやや大きい面です。",
  },
  "101": {
    name: "降水量合計", unit: "mm", decimals: 1, forecastElement: "precipitation",
    definition: "各月・年の降水量合計を30年で平均した値です。",
  },
  "401": {
    name: "日照時間", unit: "h", decimals: 1, forecastElement: "sunshine",
    definition: "各月・年の日照時間を30年で平均した値です。",
  },
  "501": {
    name: "最深積雪", unit: "cm", decimals: 1, forecastElement: null,
    definition: "年値は観測地点の12個の月別平均最深積雪の最大を独立に1km内挿した面です。同じ1km地点の月別面12個の最大とは限りません。",
  },
  "503": {
    name: "降雪量合計", unit: "cm", decimals: 1, forecastElement: "snowfall",
    definition: "各月・年の降雪量合計を30年で平均した値です。",
  },
  "610": {
    name: "全天日射量", unit: "MJ/㎡/日", decimals: 2, forecastElement: null,
    definition: "各月の全天日射量の日平均です。月合計ではなく、年値はありません。",
  },
};
const DEFAULT_FORECAST_CLASSES = {
  temperature: ["低い", "平年並", "高い"],
  precipitation: ["少ない", "平年並", "多い"],
  sunshine: ["少ない", "平年並", "多い"],
  snowfall: ["少ない", "平年並", "多い"],
};
const PRODUCT_LABELS = { P1M: "1か月予報", P3M: "3か月予報" };

const store = new ClimateDataStore();
const currentMonth = new Date().getMonth() + 1;
const state = {
  mapMode: "recent",
  element: "201",
  window: "1991_2020",
  month: currentMonth,
  mode: "absolute",
  climateOpacity: 0.86,
  forecastVisible: true,
  forecastProduct: "P1M",
  forecastTerm: "0",
  forecastOpacity: 0.28,
  base: "pale",
  showPlaceLabels: true,
  placeLabelOpacity: 0.8,
  showDetailMap: false,
  detailMapOpacity: 0.7,
  showTerrain: false,
  terrainStyle: "color",
  terrainOpacity: 0.35,
  meshCode: null,
  selectedMesh: null,
  preview: null,
  regionCode: null,
  regionName: null,
  selectedForecast: null,
  recentPreset: "month",
  recentStart: null,
  recentEnd: null,
  recentResult: null,
  recentStation: null,
  recentStationId: null,
  initialized: false,
};

const elements = Object.fromEntries([
  "loading", "loadingText", "mapInfo", "mapInfoPrimary", "mapInfoSecondary", "elementSelect", "monthSelect", "climateOpacity",
  "climateControlNote", "forecastToggle", "forecastProduct", "forecastTerm", "forecastOpacity",
  "forecastProductField", "forecastTermField", "forecastOpacityField", "forecastControlNote",
  "climateLegendPanel", "climateLegend", "climateLegendTitle", "climateLegendUnit", "climateLegendTicks",
  "legendLow", "legendMiddle", "legendHigh",
  "seasonLegend", "seasonKeys", "seasonClassBelow", "seasonClassNormal", "seasonClassAbove",
  "seasonStatus", "sourceStatus", "sourceDetailStatus", "pointState", "pointUnpin", "meshCode", "meshValue", "meshPeriod", "meshCoords",
  "windowOldValue", "windowNewValue", "differenceValue", "forecastRegion", "forecastPeriod",
  "probabilityBelowLabel", "probabilityNormalLabel", "probabilityAboveLabel",
  "probabilityBelow", "probabilityNormal", "probabilityAbove", "forecastNote", "copyLink",
  "saveImage", "locate", "resetView", "notice", "settingsToggle", "settingsClose", "detailClose",
  "pointChartSection", "pointChartMeasure", "pointMonthlyChart", "pointChartCaption", "pointChartTableBody", "pointChartNote",
  "climateControlsSection", "climateControlsHeading", "climateControlsIntro", "recentControlsSection", "forecastControlsSection",
  "recentPresetControls", "recentStart", "recentEnd", "recentApply", "recentCenterField", "recentCenterSlider",
  "recentCenterLabel", "recentControlNote", "recentDetailSection", "selectedClimateSection",
  "selectedForecastSection", "climateReadingGuide", "forecastReadingGuide", "recentReadingGuide", "recentStationId",
  "recentStationName", "recentAnomalyValue", "recentPeriodLabel", "recentObservedMean",
  "recentNormalMean", "recentValidDays", "recentStationNote",
  "placeLabelsToggle", "placeLabelOpacity", "placeLabelOpacityValue",
  "detailMapToggle", "detailMapOpacity", "detailMapOpacityValue",
  "terrainToggle", "terrainStyle", "terrainOpacity", "terrainOpacityValue",
].map((id) => [id, document.getElementById(id)]));

function firstDefined(...values) {
  return values.find((value) => value !== undefined);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}

function activeCatalogEntry() {
  return store.elements().find((entry) => String(entry.code) === state.element) || {};
}

function elementConfig() {
  const fallback = ELEMENT_FALLBACKS[state.element] || {
    name: `要素${state.element}`, unit: "", decimals: 2, forecastElement: null, definition: "",
  };
  const catalog = activeCatalogEntry();
  const manifest = store.climateManifest?.element || {};
  const display = { ...(catalog.display || {}), ...(manifest.display || {}) };
  const annual = { ...(catalog.annual || {}), ...(manifest.annual || {}) };
  const baseDefinition = firstDefined(
    display.definition, display.note, manifest.definition, manifest.note,
    catalog.definition, catalog.note, fallback.definition,
  );
  return {
    code: state.element,
    name: firstDefined(display.name, manifest.name, catalog.name, catalog.element?.name, fallback.name),
    unit: firstDefined(display.unit, manifest.unit, catalog.unit, fallback.unit),
    decimals: Number(firstDefined(
      display.decimals, display.value_decimals, display.decimal_places,
      manifest.decimals, manifest.value_decimals, manifest.decimal_places,
      catalog.decimals, catalog.value_decimals, catalog.decimal_places, fallback.decimals,
    )),
    forecastElement: firstDefined(
      display.forecast_element, manifest.forecast_element, catalog.forecast_element,
      catalog.forecastElement, fallback.forecastElement,
    ),
    definition: Number(state.month) === 13 && annual.definition ? annual.definition : baseDefinition,
    qualityNote: firstDefined(display.quality_note, manifest.quality_note, catalog.quality_note, ""),
  };
}

function forecastClassLabels(forecastElement = elementConfig().forecastElement) {
  const source = forecastElement ? store.seasonElementMetadata(forecastElement)?.classes : null;
  return Array.isArray(source) && source.length === 3
    ? source
    : DEFAULT_FORECAST_CLASSES[forecastElement] || ["低い", "平年並", "高い"];
}

function forecastLeadLabel(probabilities, labels) {
  const max = Math.max(...probabilities);
  const winners = probabilities
    .map((value, index) => (value === max ? labels[index] : null))
    .filter(Boolean);
  return winners.length === 1 ? `最多は「${winners[0]}」` : `同率首位は「${winners.join("・")}」`;
}

function formatJst(value, withTime = true) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(date);
}

function periodLabel(term) {
  if (!term) return "対象期間なし";
  return `${term.label} ${formatJst(term.start, false)}〜${formatJst(term.end, false)}`;
}

function parseIsoDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  if (!match) return null;
  const parsed = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function isoDate(value) {
  return value.toISOString().slice(0, 10);
}

function addDays(value, days) {
  const parsed = typeof value === "string" ? parseIsoDate(value) : value;
  return isoDate(new Date(parsed.getTime() + days * 86400000));
}

function inclusiveDayCount(start, end) {
  return Math.round((parseIsoDate(end) - parseIsoDate(start)) / 86400000) + 1;
}

function formatIsoDate(value) {
  const parsed = parseIsoDate(value);
  if (!parsed) return value || "--";
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "UTC", year: "numeric", month: "numeric", day: "numeric",
  }).format(parsed);
}

function formatSignedTemperature(value) {
  if (!Number.isFinite(value)) return "--";
  const normalized = Math.abs(value) < 0.05 ? 0 : value;
  return `${normalized > 0 ? "+" : ""}${normalized.toFixed(1)}℃`;
}

function recentPeriodText(result = state.recentResult) {
  if (!result) return "期間未選択";
  const range = `${formatIsoDate(result.start)}〜${formatIsoDate(result.end)}`;
  return result.center
    ? `${range}（中心 ${formatIsoDate(result.center)}）`
    : `${range}（${result.expectedDays}日間）`;
}

function effectiveForecastStatus(product, productId) {
  if (!product) return "unavailable";
  const reportAgeDays = (Date.now() - new Date(product.report_datetime).getTime()) / 86400000;
  const maximumAge = product.freshness?.maximum_report_age_days || (productId === "P1M" ? 9 : 45);
  const forecastElement = elementConfig().forecastElement;
  const firstTerm = forecastElement ? store.seasonElement(productId, forecastElement)?.terms?.[0] : null;
  const targetEnd = new Date(firstTerm?.end || 0).getTime();
  return product.status === "available" && Date.now() <= targetEnd && reportAgeDays <= maximumAge
    ? "available"
    : "stale";
}

function monthLabel(month = state.month) {
  return store.climateManifest.months.find((entry) => Number(entry.id) === Number(month))?.label
    || (Number(month) === 13 ? "年値" : `${month}月`);
}

function normalizedNumber(value, decimals) {
  if (!Number.isFinite(value)) return null;
  const threshold = 0.5 * (10 ** -decimals);
  return Math.abs(value) < threshold ? 0 : value;
}

function formatClimateValue(value, signedMode = false) {
  const config = elementConfig();
  const number = normalizedNumber(value, config.decimals);
  if (number === null) return "--";
  const prefix = signedMode && number > 0 ? "+" : "";
  return `${prefix}${number.toFixed(config.decimals)}${config.unit}`;
}

function selectedValue(record = state.selectedMesh) {
  if (!record || record.elementCode !== state.element) return null;
  const oldValue = record.values["1991_2020"]?.[state.month];
  const newValue = record.values["1996_2025"]?.[state.month];
  if (!Number.isFinite(oldValue) || !Number.isFinite(newValue)) return null;
  return state.mode === "difference" ? newValue - oldValue : record.values[state.window]?.[state.month];
}

const SVG_NS = "http://www.w3.org/2000/svg";
const OLD_WINDOW = "1991_2020";
const NEW_WINDOW = "1996_2025";

function svgNode(name, attributes = {}, text = null) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  if (text !== null) node.textContent = text;
  return node;
}

function niceStep(rawStep) {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(rawStep));
  const fraction = rawStep / power;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return niceFraction * power;
}

function chartScale(values) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return null;
  const actualMin = Math.min(...finite);
  const actualMax = Math.max(...finite);
  let spread = actualMax - actualMin;
  if (spread === 0) spread = Math.max(Math.abs(actualMax) * 0.2, 1);
  const resolution = 10 ** -elementConfig().decimals;
  const step = niceStep(Math.max(spread / 4, resolution));
  let minimum = Math.floor((actualMin - spread * 0.08) / step) * step;
  let maximum = Math.ceil((actualMax + spread * 0.08) / step) * step;
  if (actualMin >= 0 && minimum < 0) minimum = 0;
  if ((maximum - minimum) / step < 2) maximum = minimum + step * 3;
  if (maximum <= minimum) maximum = minimum + step;
  const ticks = [];
  for (let value = minimum; value <= maximum + step * 0.25; value += step) ticks.push(value);
  return { minimum, maximum, step, ticks };
}

function axisLabel(value, step) {
  const decimals = Math.abs(step) < 1 ? Math.min(2, Math.max(1, elementConfig().decimals)) : 0;
  return normalizedNumber(value, decimals).toFixed(decimals);
}

function monthlySeries(record) {
  const months = store.climateManifest.months
    .filter((month) => Number(month.id) >= 1 && Number(month.id) <= 12)
    .map((month) => ({ id: Number(month.id), label: month.label }));
  return {
    months,
    oldValues: months.map((month) => record.values[OLD_WINDOW]?.[month.id] ?? null),
    newValues: months.map((month) => record.values[NEW_WINDOW]?.[month.id] ?? null),
  };
}

function renderMonthlyChart(record) {
  const svg = elements.pointMonthlyChart;
  svg.replaceChildren();
  const series = monthlySeries(record);
  const scale = chartScale([...series.oldValues, ...series.newValues]);
  const config = elementConfig();
  elements.pointChartMeasure.textContent = `${config.name}（${config.unit}）`;
  const annualNote = Number(state.month) === 13
    ? state.element === "501"
      ? "表示中の年値は、月別12点の最大値ではありません。"
      : "表示中の年値は、この月別グラフから再計算した値ではありません。"
    : "";
  elements.pointChartNote.textContent = `2つの30年平均を月別に比較。季節予報の推移ではありません。${annualNote ? ` ${annualNote}` : ""}`;
  elements.pointChartCaption.textContent = `1kmメッシュ ${record.meshCode} の${config.name}。1991–2020年平均と1996–2025年平均の1月から12月までの比較。`;
  elements.pointChartTableBody.innerHTML = series.months.map((month, index) => (
    `<tr><th>${escapeHtml(month.label)}</th><td>${escapeHtml(formatClimateValue(series.oldValues[index]))}</td><td>${escapeHtml(formatClimateValue(series.newValues[index]))}</td></tr>`
  )).join("");

  if (!scale) {
    svg.append(svgNode("text", { x: 134, y: 76, "text-anchor": "middle" }, "月別値がありません"));
    return;
  }

  const plot = { left: 36, right: 248, top: 10, bottom: 124 };
  const xAt = (index) => plot.left + ((plot.right - plot.left) * index) / 11;
  const yAt = (value) => plot.bottom - ((value - scale.minimum) / (scale.maximum - scale.minimum)) * (plot.bottom - plot.top);
  const activeIndex = Number(state.month) >= 1 && Number(state.month) <= 12 ? Number(state.month) - 1 : -1;
  if (activeIndex >= 0) {
    svg.append(svgNode("rect", {
      x: xAt(activeIndex) - 8, y: plot.top, width: 16, height: plot.bottom - plot.top,
      fill: "#176f7d", "fill-opacity": 0.08,
    }));
  }

  scale.ticks.forEach((value) => {
    const y = yAt(value);
    svg.append(svgNode("line", { x1: plot.left, y1: y, x2: plot.right, y2: y, stroke: "#d7e1e5", "stroke-width": 1 }));
    svg.append(svgNode("text", { x: plot.left - 4, y: y + 3, "text-anchor": "end" }, axisLabel(value, scale.step)));
  });

  series.months.forEach((month, index) => {
    const x = xAt(index);
    svg.append(svgNode("line", { x1: x, y1: plot.bottom, x2: x, y2: plot.bottom + 3, stroke: "#8da0a8", "stroke-width": 1 }));
    svg.append(svgNode("text", { x, y: 141, "text-anchor": "middle" }, String(month.id)));
  });
  svg.append(svgNode("text", { x: 264, y: 141, "text-anchor": "end" }, "月"));

  const drawSeries = (values, color, dashed) => {
    let path = "";
    values.forEach((value, index) => {
      if (!Number.isFinite(value)) return;
      const command = index === 0 || !Number.isFinite(values[index - 1]) ? "M" : "L";
      path += `${command}${xAt(index).toFixed(2)},${yAt(value).toFixed(2)} `;
    });
    if (path) {
      svg.append(svgNode("path", {
        d: path.trim(), fill: "none", stroke: color, "stroke-width": 2,
        ...(dashed ? { "stroke-dasharray": "5 3" } : {}),
      }));
    }
    values.forEach((value, index) => {
      if (!Number.isFinite(value)) return;
      const circle = svgNode("circle", { cx: xAt(index), cy: yAt(value), r: 2.25, fill: "#fff", stroke: color, "stroke-width": 1.5 });
      circle.append(svgNode("title", {}, `${index + 1}月 ${formatClimateValue(value)}`));
      svg.append(circle);
    });
  };
  drawSeries(series.oldValues, "#687c85", true);
  drawSeries(series.newValues, "#0f7883", false);
}

const map = new ClimateMap("map", {
  onMapClick: (latlng) => {
    if (state.mapMode === "recent") clearRecentSelection();
    else selectAtLatLon(latlng.lat, latlng.lng);
  },
  onRegionClick: (selection) => {
    if (state.mapMode === "forecast") selectAtLatLon(selection.latlng.lat, selection.latlng.lng, selection);
  },
  onRecentStationClick: (point) => selectRecentStation(point),
  onViewChange: () => {
    if (state.initialized) syncUrl();
  },
  onPointerMove: (latlng) => {
    if (["climate", "forecast"].includes(state.mapMode)) scheduleHover(latlng);
  },
  onPointerLeave: () => clearPreview(),
  onClimateLoad: () => setNotice("気候面を表示しました", "ok"),
  onClimateError: () => setNotice("気候面を読み込めませんでした", "error"),
});

function setLoading(active, text = "読み込み中") {
  elements.loading.hidden = !active;
  elements.loadingText.textContent = text;
}

let noticeTimer;
function setNotice(text, kind = "info") {
  clearTimeout(noticeTimer);
  elements.notice.textContent = text;
  elements.notice.dataset.kind = kind;
  elements.notice.hidden = false;
  noticeTimer = setTimeout(() => { elements.notice.hidden = true; }, 2800);
}

function populateElements() {
  const available = store.elements().sort((a, b) => {
    const ai = ELEMENT_ORDER.indexOf(String(a.code));
    const bi = ELEMENT_ORDER.indexOf(String(b.code));
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
  elements.elementSelect.innerHTML = available.map((entry) => {
    const code = String(entry.code);
    const name = entry.display?.short_name || entry.short_name || entry.name
      || entry.element?.short_name || entry.element?.name || ELEMENT_FALLBACKS[code]?.name || code;
    return `<option value="${escapeHtml(code)}">${escapeHtml(name)}</option>`;
  }).join("");
  elements.elementSelect.value = state.element;
}

function normalizeMonth() {
  const available = store.climateManifest.months.map((month) => Number(month.id));
  if (available.includes(Number(state.month))) return;
  state.month = available.includes(currentMonth) ? currentMonth : available[0];
}

function populateMonths() {
  normalizeMonth();
  elements.monthSelect.innerHTML = store.climateManifest.months
    .map((month) => `<option value="${month.id}">${escapeHtml(month.label)}</option>`).join("");
  elements.monthSelect.value = String(state.month);
}

function optionalNumberParam(params, key) {
  const raw = params.get(key);
  if (raw === null || raw.trim() === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function initializeRecentState() {
  const range = store.recentTemperatureRange();
  if (!range) throw new Error("最近の気温平年差の収録期間がありません");
  state.recentStart = addDays(range.end, -29);
  state.recentEnd = range.end;
}

function inferRecentPreset(start, end) {
  const days = inclusiveDayCount(start, end);
  if (days === 5) return "5day";
  if (days === 7) return "7day";
  if (days === 30) return "month";
  if (days === 90) return "90day";
  return "custom";
}

function parseInitialState() {
  const params = new URLSearchParams(location.search);
  if (["climate", "recent", "forecast"].includes(params.get("view"))) state.mapMode = params.get("view");
  if (["1991_2020", "1996_2025"].includes(params.get("window"))) state.window = params.get("window");
  const month = Number(params.get("month"));
  if (Number.isInteger(month)) state.month = month;
  normalizeMonth();
  if (["absolute", "difference"].includes(params.get("mode"))) state.mode = params.get("mode");
  if (["P1M", "P3M"].includes(params.get("forecast"))) state.forecastProduct = params.get("forecast");
  if (/^[0-3]$/.test(params.get("term") || "")) state.forecastTerm = params.get("term");
  if (params.get("overlay") === "off") state.forecastVisible = false;
  const climateOpacity = Number(params.get("cop"));
  if (Number.isFinite(climateOpacity) && climateOpacity >= 10 && climateOpacity <= 100) state.climateOpacity = climateOpacity / 100;
  const forecastOpacity = Number(params.get("fop"));
  if (Number.isFinite(forecastOpacity) && forecastOpacity >= 5 && forecastOpacity <= 70) state.forecastOpacity = forecastOpacity / 100;
  if (["blank", "pale", "standard"].includes(params.get("base"))) state.base = params.get("base");
  if (params.get("labels") === "0") state.showPlaceLabels = false;
  const placeLabelOpacity = optionalNumberParam(params, "labelOpacity");
  if (placeLabelOpacity !== null && placeLabelOpacity >= 0 && placeLabelOpacity <= 100) {
    state.placeLabelOpacity = placeLabelOpacity / 100;
  }
  if (params.get("detail") === "1") state.showDetailMap = true;
  const detailMapOpacity = optionalNumberParam(params, "detailOpacity");
  if (detailMapOpacity !== null && detailMapOpacity >= 0 && detailMapOpacity <= 100) {
    state.detailMapOpacity = detailMapOpacity / 100;
  }
  if (params.get("terrain") === "1") state.showTerrain = true;
  if (["color", "mono"].includes(params.get("terrainStyle"))) state.terrainStyle = params.get("terrainStyle");
  const terrainOpacity = optionalNumberParam(params, "terrainOpacity");
  if (terrainOpacity !== null && terrainOpacity >= 0 && terrainOpacity <= 100) {
    state.terrainOpacity = terrainOpacity / 100;
  }
  if (/^\d{8}$/.test(params.get("mesh") || "")) state.meshCode = params.get("mesh");
  if (/^(hoppo|\d{6})$/.test(params.get("region") || "")) state.regionCode = params.get("region");
  if (/^\d{5}$/.test(params.get("station") || "")) state.recentStationId = params.get("station");
  const recentRange = store.recentTemperatureRange();
  const recentStart = params.get("start");
  const recentEnd = params.get("end");
  if (
    parseIsoDate(recentStart)
    && parseIsoDate(recentEnd)
    && recentStart >= recentRange.start
    && recentEnd <= recentRange.end
    && recentStart <= recentEnd
    && inclusiveDayCount(recentStart, recentEnd) <= 93
  ) {
    state.recentStart = recentStart;
    state.recentEnd = recentEnd;
    state.recentPreset = inferRecentPreset(recentStart, recentEnd);
  }
  const lat = optionalNumberParam(params, "lat");
  const lon = optionalNumberParam(params, "lon");
  const zoom = optionalNumberParam(params, "z");
  return {
    lat,
    lon,
    zoom: zoom === null ? null : Math.min(12, Math.max(4, zoom)),
  };
}

function setFieldDisabled(field, input, disabled) {
  input.disabled = disabled;
  field?.classList.toggle("control-disabled", disabled);
}

function syncMapLayerControls() {
  elements.placeLabelsToggle.checked = state.showPlaceLabels;
  elements.placeLabelOpacity.value = String(Math.round(state.placeLabelOpacity * 100));
  elements.placeLabelOpacityValue.value = `${Math.round(state.placeLabelOpacity * 100)}%`;
  elements.placeLabelOpacity.disabled = !state.showPlaceLabels;
  elements.detailMapToggle.checked = state.showDetailMap;
  elements.detailMapOpacity.value = String(Math.round(state.detailMapOpacity * 100));
  elements.detailMapOpacityValue.value = `${Math.round(state.detailMapOpacity * 100)}%`;
  elements.detailMapOpacity.disabled = !state.showDetailMap;
  elements.terrainToggle.checked = state.showTerrain;
  elements.terrainStyle.value = state.terrainStyle;
  elements.terrainStyle.disabled = !state.showTerrain;
  elements.terrainOpacity.value = String(Math.round(state.terrainOpacity * 100));
  elements.terrainOpacityValue.value = `${Math.round(state.terrainOpacity * 100)}%`;
  elements.terrainOpacity.disabled = !state.showTerrain;
}

function applyRecentControls() {
  const range = store.recentTemperatureRange();
  const dates = store.recentTemperature.dates;
  elements.recentStart.min = range.start;
  elements.recentStart.max = range.end;
  elements.recentEnd.min = range.start;
  elements.recentEnd.max = range.end;
  elements.recentStart.value = state.recentStart;
  elements.recentEnd.value = state.recentEnd;
  document.querySelectorAll("[data-recent-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.recentPreset === state.recentPreset);
  });
  const isFiveDay = state.recentResult?.expectedDays === 5 || state.recentPreset === "5day";
  elements.recentCenterField.hidden = !isFiveDay;
  elements.recentCenterSlider.min = "2";
  elements.recentCenterSlider.max = String(dates.length - 3);
  const center = state.recentResult?.center || addDays(state.recentStart, 2);
  const centerIndex = dates.indexOf(center);
  if (centerIndex >= 2 && centerIndex <= dates.length - 3) {
    elements.recentCenterSlider.value = String(centerIndex);
  }
  elements.recentCenterLabel.textContent = formatIsoDate(center);
  elements.recentControlNote.textContent = [
    "5日は前後2日を含む計5日で、気象庁の「前3か月間の気温経過」と同じ中心日に合わせます。",
    "過去1か月は最新日を含む直近30日です。",
    `収録 ${formatIsoDate(range.start)}〜${formatIsoDate(range.end)}。任意期間は93日以内です。`,
  ].join(" ");
}

function syncElementAvailability() {
  const forecastMode = state.mapMode === "forecast";
  [...elements.elementSelect.options].forEach((option) => {
    option.disabled = forecastMode && !ELEMENT_FALLBACKS[option.value]?.forecastElement;
  });
}

function applyControls() {
  document.querySelectorAll("[data-map-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mapMode === state.mapMode);
  });
  document.querySelectorAll("[data-window]").forEach((button) => {
    button.classList.toggle("active", button.dataset.window === state.window);
  });
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === state.mode);
  });
  document.querySelectorAll("[data-base]").forEach((button) => {
    button.classList.toggle("active", button.dataset.base === state.base);
  });
  elements.elementSelect.value = state.element;
  elements.monthSelect.value = String(state.month);
  elements.climateOpacity.value = String(Math.round(state.climateOpacity * 100));
  elements.forecastToggle.checked = state.forecastVisible && !elements.forecastToggle.disabled;
  elements.forecastProduct.value = state.forecastProduct;
  elements.forecastOpacity.value = String(Math.round(state.forecastOpacity * 100));
  syncMapLayerControls();
  syncElementAvailability();
  document.getElementById("windowControls").classList.toggle("muted-control", state.mode === "difference");
  const recent = state.mapMode === "recent";
  const climate = state.mapMode === "climate";
  const forecast = state.mapMode === "forecast";
  elements.climateControlsSection.hidden = recent;
  elements.climateControlsHeading.textContent = forecast ? "比較する平年値（1km）" : "気候統計（1km）";
  elements.climateControlsIntro.textContent = forecast
    ? "季節予報と同じ要素の平年分布を比較表示します。予報地域は1kmへ補間しません。"
    : "30年平均の分布と、2つの平均期間の更新差を表示します。";
  elements.forecastControlsSection.hidden = !forecast;
  elements.recentControlsSection.hidden = !recent;
  elements.recentDetailSection.hidden = !recent;
  elements.selectedClimateSection.hidden = recent;
  if (recent) elements.pointChartSection.hidden = true;
  elements.selectedForecastSection.hidden = !forecast;
  elements.climateReadingGuide.hidden = !climate;
  elements.forecastReadingGuide.hidden = !forecast;
  elements.recentReadingGuide.hidden = !recent;
  if (recent) applyRecentControls();
}

function forecastUnavailableReason(config, productId, elementData, term, regionCount) {
  if (!config.forecastElement) {
    return `${config.name}に直接対応する季節予報要素はありません。`;
  }
  if (!elementData?.supported) {
    if (elementData?.unavailable_reason === "not_supported_by_dataset") {
      return `${config.name}に対応する季節予報データは更新準備中です。`;
    }
    if (config.forecastElement === "sunshine" && productId === "P3M") {
      return "3か月予報には日照時間の地域確率がありません。";
    }
    return `${PRODUCT_LABELS[productId]}では${config.name}に対応する地域確率を利用できません。`;
  }
  if (!term || regionCount === 0 || elementData.status === "unavailable") {
    if (config.forecastElement === "snowfall") {
      return "現在の発表には降雪量の地域確率がありません（季節限定）。";
    }
    return "現在の発表には、この対象期間の地域確率がありません。";
  }
  return null;
}

function forecastContext(normalize = true) {
  const config = elementConfig();
  const forecastElement = config.forecastElement;
  if (normalize && forecastElement) {
    const selected = store.seasonElement(state.forecastProduct, forecastElement);
    if (!selected?.supported) {
      const fallbackProduct = ["P1M", "P3M"].find((productId) => (
        store.seasonElement(productId, forecastElement)?.supported
      ));
      if (fallbackProduct) state.forecastProduct = fallbackProduct;
    }
  }
  const product = store.forecastProduct(state.forecastProduct);
  const elementData = forecastElement ? store.seasonElement(state.forecastProduct, forecastElement) : null;
  const terms = elementData?.terms || [];
  if (normalize && terms.length && !terms.some((term) => String(term.id) === String(state.forecastTerm))) {
    state.forecastTerm = String(terms[0].id);
  }
  const term = terms.find((candidate) => String(candidate.id) === String(state.forecastTerm)) || null;
  const regionCount = Object.keys(term?.regions || {}).length;
  const labels = forecastClassLabels(forecastElement);
  const forecastName = forecastElement
    ? store.seasonElementMetadata(forecastElement)?.name || config.name
    : config.name;
  const reason = forecastUnavailableReason(config, state.forecastProduct, elementData, term, regionCount);
  return {
    config, forecastElement, forecastName, product, elementData, terms, term, regionCount, labels, reason,
    supported: Boolean(forecastElement && elementData?.supported),
    canOverlay: Boolean(state.mapMode === "forecast" && state.forecastVisible && forecastElement && elementData?.supported && regionCount > 0),
  };
}

function updateTermOptions(context) {
  if (!context.terms.length) {
    elements.forecastTerm.innerHTML = "<option value=\"\">対象期間なし</option>";
    setFieldDisabled(elements.forecastTermField, elements.forecastTerm, true);
    return;
  }
  elements.forecastTerm.innerHTML = context.terms.map((term) => {
    const suffix = Object.keys(term.regions || {}).length ? "" : "（未発表）";
    return `<option value="${escapeHtml(term.id)}">${escapeHtml(term.label)}｜${formatJst(term.start, false)}〜${formatJst(term.end, false)}${suffix}</option>`;
  }).join("");
  elements.forecastTerm.value = String(state.forecastTerm);
  setFieldDisabled(elements.forecastTermField, elements.forecastTerm, false);
}

function legendLabels(config, mode) {
  const explicit = config.labels || config.display_labels;
  if (Array.isArray(explicit) && explicit.length === 3) return explicit;
  if (explicit?.low && explicit?.middle && explicit?.high) {
    return [explicit.low, explicit.middle, explicit.high];
  }
  if (config.low_label && config.middle_label && config.high_label) {
    return [config.low_label, config.middle_label, config.high_label];
  }
  const breaks = config.breaks || [];
  const low = breaks[0] ?? 0;
  const high = breaks.at(-1) ?? 0;
  const middle = mode === "difference"
    ? 0
    : breaks.includes(0) && low < 0 && high > 0
      ? 0
      : breaks[Math.floor(breaks.length / 2)] ?? 0;
  const digits = elementConfig().decimals > 0 && [low, middle, high].some((value) => !Number.isInteger(value)) ? 1 : 0;
  const valueText = (value, signedMode = false) => {
    const prefix = signedMode && value > 0 ? "+" : "";
    return `${prefix}${Number(value).toFixed(digits)}${elementConfig().unit}`;
  };
  return [
    `${valueText(low, mode === "difference")}以下`,
    valueText(middle, mode === "difference"),
    `${valueText(high, mode === "difference")}超`,
  ];
}

function activeRasterLegend() {
  const rasters = store.climateManifest.rasters;
  const period = Number(state.month) === 13 ? "annual" : "monthly";
  const mode = state.mode === "difference" ? "difference" : "absolute";
  return rasters.legends?.[period]?.[mode]
    || (state.mode === "difference" ? rasters.difference_legend : rasters.raw_legend);
}

function niceLegendStep(range, unit, mode) {
  if (unit === "℃" && mode === "absolute" && range >= 20) return 5;
  const target = range / 8;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  const fraction = target / magnitude;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return niceFraction * magnitude;
}

function legendValuePosition(value, breaks) {
  const low = breaks[0];
  const high = breaks.at(-1);
  if (value <= low) return 100;
  if (value >= high) return 0;
  for (let index = 0; index < breaks.length - 1; index += 1) {
    const start = breaks[index];
    const end = breaks[index + 1];
    if (value > end) continue;
    const interval = end === start ? 0 : (value - start) / (end - start);
    const palettePosition = (index + interval) / (breaks.length - 1);
    return (1 - palettePosition) * 100;
  }
  return 0;
}

function formatLegendNumber(value, step, signed) {
  const maximumFractionDigits = step >= 1 && Number.isInteger(step) ? 0 : step >= 0.1 ? 1 : 2;
  const formatted = new Intl.NumberFormat("ja-JP", { maximumFractionDigits }).format(value).replace("-", "−");
  return signed && value > 0 ? `+${formatted}` : formatted;
}

function conciseEndpointLabel(rawLabel, value, step, signed) {
  const label = String(rawLabel || "");
  const prefix = label.includes("以下") ? "≤" : label.includes("超") ? ">" : "";
  return `${prefix}${formatLegendNumber(value, step, signed)}`;
}

function climateLegendTicks(config, mode) {
  const labels = legendLabels(config, mode);
  const breaks = (config.breaks || []).map(Number).filter(Number.isFinite);
  if (breaks.length < 2) {
    return [
      { label: labels[2], position: 0 },
      { label: labels[1], position: 50 },
      { label: labels[0], position: 100 },
    ];
  }
  const low = breaks[0];
  const high = breaks.at(-1);
  const step = niceLegendStep(high - low, elementConfig().unit, mode);
  const candidates = [low, high];
  const first = Math.ceil((low - step * 1e-9) / step) * step;
  for (let value = first; value <= high + step * 1e-9; value += step) {
    candidates.push(Number(value.toFixed(8)));
  }
  const unique = [...new Set(candidates)].map((value) => ({
    value,
    position: legendValuePosition(value, breaks),
  })).sort((a, b) => a.position - b.position);
  const minimumGap = 5.5;
  const ticks = [unique[0]];
  unique.slice(1, -1).forEach((entry) => {
    if (entry.position - ticks.at(-1).position >= minimumGap && 100 - entry.position >= minimumGap) ticks.push(entry);
  });
  ticks.push(unique.at(-1));
  return ticks.map((entry) => ({
    value: entry.value,
    position: entry.position,
    label: entry.value === high
      ? conciseEndpointLabel(labels[2], entry.value, step, mode === "difference")
      : entry.value === low
        ? conciseEndpointLabel(labels[0], entry.value, step, mode === "difference")
        : formatLegendNumber(entry.value, step, mode === "difference"),
  }));
}

function renderClimateLegendTicks(ticks) {
  const nodes = ticks.map((entry) => {
    const tick = document.createElement("span");
    const label = document.createElement("b");
    tick.className = "legend-tick";
    tick.style.setProperty("--position", `${entry.position.toFixed(3)}%`);
    label.textContent = entry.label;
    tick.append(label);
    return tick;
  });
  elements.climateLegendTicks.replaceChildren(...nodes);
}

function updateClimateLegend() {
  const config = activeRasterLegend();
  if (!config?.colors?.length) throw new Error(`凡例がありません: ${state.element}/${state.month}/${state.mode}`);
  const stops = config.colors.map((color, index) => `${color} ${(index / (config.colors.length - 1)) * 100}%`).join(",");
  const labels = legendLabels(config, state.mode);
  const title = config.title || (state.mode === "difference" ? "30年平均値の更新差" : elementConfig().name);
  const ticks = climateLegendTicks(config, state.mode);
  elements.climateLegend.style.background = `linear-gradient(to top,${stops})`;
  elements.climateLegendTitle.textContent = title;
  elements.climateLegendUnit.textContent = elementConfig().unit;
  elements.climateLegendPanel.setAttribute("aria-label", `${title}（${elementConfig().unit}）の凡例。${ticks.map((tick) => tick.label).join("、")}`);
  renderClimateLegendTicks(ticks);
  [elements.legendLow.textContent, elements.legendMiddle.textContent, elements.legendHigh.textContent] = labels;
}

function recentLegendTicks() {
  return [-5, -3, -2, -1, 0, 1, 2, 3, 5].map((value) => ({
    value,
    position: legendValuePosition(value, TEMPERATURE_ANOMALY_LEGEND.breaks),
    label: value > 0 ? `+${value}` : String(value).replace("-", "−"),
  }));
}

function updateRecentLegend() {
  const config = TEMPERATURE_ANOMALY_LEGEND;
  const stops = config.colors.map(
    (color, index) => `${color} ${(index / (config.colors.length - 1)) * 100}%`,
  ).join(",");
  const ticks = recentLegendTicks();
  elements.climateLegend.style.background = `linear-gradient(to top,${stops})`;
  elements.climateLegendTitle.textContent = config.title;
  elements.climateLegendUnit.textContent = config.unit;
  elements.climateLegendPanel.setAttribute(
    "aria-label",
    `${config.title}（${config.unit}）の凡例。${ticks.map((tick) => tick.label).join("、")}`,
  );
  renderClimateLegendTicks(ticks);
  elements.legendLow.textContent = "−5℃以下";
  elements.legendMiddle.textContent = "0℃";
  elements.legendHigh.textContent = "+5℃超";
}

function climateSubtitle() {
  const config = elementConfig();
  const windowLabel = store.climateManifest.windows.find((entry) => entry.id === state.window)?.label || state.window;
  return state.mode === "difference"
    ? `${config.name}｜${monthLabel()}｜30年平均値の更新差（1996–2025 − 1991–2020）`
    : `${config.name}｜${monthLabel()}｜${windowLabel}`;
}

function mapPrimaryTitle() {
  const config = elementConfig();
  const windowLabel = store.climateManifest.windows.find((entry) => entry.id === state.window)?.label || state.window;
  return state.mode === "difference"
    ? `${config.name}の更新差｜${monthLabel()}｜1996–2025 − 1991–2020`
    : `${config.name}の分布｜${monthLabel()}｜${windowLabel}`;
}

function updateClimateControlNote() {
  const config = elementConfig();
  const definition = [
    config.definition,
    ["203", "610"].includes(state.element) ? config.qualityNote : "",
  ].filter(Boolean).join(" ");
  const difference = "更新差は25年間が重なる30年平均同士の差で、30年間の変化量そのものではありません。";
  elements.climateControlNote.textContent = `${definition}${definition ? " " : ""}${difference}`;
}

function updateClimate() {
  if (!["climate", "forecast"].includes(state.mapMode)) return;
  map.setRecentTemperature([], false);
  const path = store.climateRasterPath(state);
  map.setClimateRaster(path, store.climateManifest.rasters.render, state.climateOpacity);
  updateClimateLegend();
  updateClimateControlNote();
  renderSelected();
  updateStatus();
  syncUrl();
}

function renderRecentSelection() {
  const point = state.recentStation;
  document.body.classList.toggle("has-selection", Boolean(point));
  document.body.classList.remove("has-preview");
  if (!point) {
    elements.recentStationId.textContent = "地点を選択";
    elements.recentStationName.textContent = "地図の四角に触れると値を確認できます";
    elements.recentAnomalyValue.textContent = "--";
    elements.recentPeriodLabel.textContent = recentPeriodText();
    elements.recentObservedMean.textContent = "--";
    elements.recentNormalMean.textContent = "--";
    elements.recentValidDays.textContent = "--";
    elements.recentStationNote.textContent = "地点値を色分けしたMAPです。観測地点間を面的に補間していません。";
    return;
  }
  elements.recentStationId.textContent = [
    `観測所 ${point.stationId}`,
    point.stationType === "amedas" ? "アメダス" : "気象台等",
    `標高 ${point.elevationM.toFixed(1)}m`,
  ].join("｜");
  elements.recentStationName.textContent = point.name;
  elements.recentAnomalyValue.textContent = formatSignedTemperature(point.anomaly);
  elements.recentPeriodLabel.textContent = recentPeriodText();
  elements.recentObservedMean.textContent = `${point.observedMean.toFixed(1)}℃`;
  elements.recentNormalMean.textContent = `${point.normalMean.toFixed(1)}℃`;
  elements.recentValidDays.textContent = `${point.validDays}/${point.expectedDays}日`;
  elements.recentStationNote.textContent = point.normalMethod === "official_5day"
    ? "5日平年値は気象庁の公式5日間平年値。地点間は補間していません。"
    : "日別平年値を指定期間で平均した独自集計値。地点間は補間していません。";
}

function selectRecentStation(point, pan = false) {
  if (state.mapMode !== "recent") return;
  state.recentStation = point;
  state.recentStationId = point.stationId;
  map.selectRecentStation(point.stationId, pan);
  document.body.classList.remove("detail-mobile-closed", "settings-open");
  renderRecentSelection();
  syncUrl();
}

function clearRecentSelection() {
  if (!state.recentStation) return;
  state.recentStation = null;
  state.recentStationId = null;
  map.selectRecentStation(null);
  renderRecentSelection();
  syncUrl();
}

function setRecentPreset(preset) {
  const range = store.recentTemperatureRange();
  const end = range.end;
  state.recentPreset = preset;
  if (preset === "5day") {
    state.recentStart = addDays(range.latestCenter, -2);
    state.recentEnd = addDays(range.latestCenter, 2);
  } else if (preset === "7day") {
    state.recentStart = addDays(end, -6);
    state.recentEnd = end;
  } else if (preset === "dekad") {
    const parsed = parseIsoDate(end);
    const day = parsed.getUTCDate();
    const startDay = day <= 10 ? 1 : day <= 20 ? 11 : 21;
    state.recentStart = `${end.slice(0, 8)}${String(startDay).padStart(2, "0")}`;
    state.recentEnd = end;
  } else if (preset === "month") {
    state.recentStart = addDays(end, -29);
    state.recentEnd = end;
  } else if (preset === "90day") {
    state.recentStart = addDays(end, -89);
    state.recentEnd = end;
  }
  updateRecentTemperature();
}

function updateRecentTemperature() {
  if (state.mapMode !== "recent") return;
  let result;
  try {
    result = store.recentTemperaturePeriod(state.recentStart, state.recentEnd);
  } catch (error) {
    setNotice(error.message, "error");
    return;
  }
  state.recentResult = result;
  if (state.recentStationId) {
    state.recentStation = result.points.find(
      (point) => point.stationId === state.recentStationId,
    ) || null;
    if (!state.recentStation) state.recentStationId = null;
  }
  clearPreview({ render: false });
  map.selectMesh(null, null);
  map.setClimateOpacity(0);
  map.setSeasonOverlay(store.regions, null, false);
  map.setRecentTemperature(result.points, true);
  map.selectRecentStation(state.recentStation?.stationId || null);
  updateRecentLegend();
  elements.seasonLegend.hidden = true;
  applyControls();
  renderRecentSelection();
  updateStatus();
  syncUrl();
}

async function switchMapMode(mode) {
  if (!["climate", "recent", "forecast"].includes(mode) || mode === state.mapMode) return;
  state.mapMode = mode;
  document.body.classList.toggle("recent-view", mode === "recent");
  document.body.classList.toggle("forecast-view", mode === "forecast");
  elements.sourceStatus.title = mode === "recent"
    ? "気象台等とアメダスの全国観測地点値。地点間は補間していません。"
    : mode === "forecast"
      ? "季節予報は公式地域確率、比較面は独自算出した1km平年値です。"
      : "全国表示用ラスターは描画縮約。地点値は全387,717メッシュを保持した要素別バイナリから参照します。";
  clearPreview({ render: false });
  if (mode === "recent") {
    updateRecentTemperature();
  } else {
    map.setRecentTemperature([], false);
    if (mode === "forecast") {
      state.forecastVisible = true;
      if (!elementConfig().forecastElement) {
        await switchElement("201");
        return;
      }
    }
    renderSelected();
    applyControls();
    updateClimate();
    updateForecast();
  }
}

function updateForecastClassLabels(labels) {
  [elements.seasonClassBelow.textContent, elements.seasonClassNormal.textContent, elements.seasonClassAbove.textContent] = labels;
  [elements.probabilityBelowLabel.textContent, elements.probabilityNormalLabel.textContent, elements.probabilityAboveLabel.textContent] = labels;
}

function updateForecast() {
  if (state.mapMode !== "forecast") {
    map.setSeasonOverlay(store.regions, null, false);
    elements.seasonLegend.hidden = true;
    return;
  }
  const context = forecastContext(true);
  updateTermOptions(context);
  updateForecastClassLabels(context.labels);
  state.selectedForecast = null;
  if (context.canOverlay && state.selectedMesh && !state.regionCode) {
    const feature = store.regionAtLatLon(state.selectedMesh.centerLat, state.selectedMesh.centerLon);
    state.regionCode = feature?.properties.code || null;
    state.regionName = feature?.properties.name || null;
  }
  if (state.regionCode && context.term) {
    const feature = store.regions.features.find((candidate) => candidate.properties.code === state.regionCode);
    state.regionName = feature?.properties.name || state.regionName;
    state.selectedForecast = context.term.regions?.[state.regionCode] || null;
  }
  map.setSeasonOverlay(
    store.regions,
    context.term,
    context.canOverlay,
    state.forecastOpacity,
    context.labels,
  );

  ["P1M", "P3M"].forEach((productId) => {
    const option = elements.forecastProduct.querySelector(`option[value="${productId}"]`);
    if (option) option.disabled = !context.forecastElement || !store.seasonElement(productId, context.forecastElement)?.supported;
  });
  elements.forecastProduct.value = state.forecastProduct;
  const hasSupportedProduct = Boolean(context.forecastElement && ["P1M", "P3M"].some((productId) => (
    store.seasonElement(productId, context.forecastElement)?.supported
  )));
  setFieldDisabled(elements.forecastProductField, elements.forecastProduct, !hasSupportedProduct);
  elements.forecastToggle.disabled = Boolean(context.reason);
  elements.forecastOpacity.disabled = !context.canOverlay;
  elements.forecastOpacityField.classList.toggle("control-disabled", !context.canOverlay);
  elements.forecastControlNote.textContent = context.reason
    || "色は地域内で最も確率が高い階級。確率値を気候平均へ足したり、1kmへ補間したりしません。";

  const showReason = Boolean(context.reason);
  elements.seasonLegend.hidden = !showReason && !state.forecastVisible;
  elements.seasonKeys.hidden = showReason;
  const effectiveStatus = effectiveForecastStatus(context.product, state.forecastProduct);
  elements.seasonStatus.innerHTML = context.reason
    ? `<b>${escapeHtml(context.forecastElement ? context.forecastName : context.config.name)}の季節予報</b><span class="unavailable">${escapeHtml(context.reason)}</span>`
    : [
      `<b>${PRODUCT_LABELS[state.forecastProduct]}・${escapeHtml(context.forecastName)}</b>`,
      `<span>${escapeHtml(periodLabel(context.term))}</span>`,
      `<span>発表 ${formatJst(context.product?.report_datetime)}｜${effectiveStatus === "available" ? "利用可能" : "更新注意"}</span>`,
      `<small>色は最多階級。同率首位は灰色。確率3値は地点詳細で確認。</small>`,
    ].join("");
  renderSelected();
  updateStatus();
  applyControls();
  syncUrl();
}

function updateStatus() {
  if (state.mapMode === "recent") {
    const manifest = store.recentTemperatureManifest;
    elements.sourceStatus.textContent = [
      `最近の気温 ${manifest.dataset_id}`,
      `観測 ${formatIsoDate(manifest.observation_start)}〜${formatIsoDate(manifest.observation_end)}`,
      `${manifest.station_count}地点`,
    ].join("｜");
    elements.sourceDetailStatus.textContent = "気象台等とアメダスの全国観測地点値です。観測地点間を面的に補間していません。";
    elements.mapInfoPrimary.textContent = `平均気温の平年差｜${recentPeriodText()}`;
    elements.mapInfoSecondary.textContent = `${state.recentResult?.points.length || 0}/${manifest.station_count}地点｜1991–2020平年値`;
    return;
  }
  if (state.mapMode === "climate") {
    elements.sourceStatus.textContent = `気候データ ${store.climateManifest.dataset_id}｜${store.climateManifest.mesh_count.toLocaleString("ja-JP")}メッシュ`;
    elements.sourceDetailStatus.textContent = "30年平均を独自算出・独自内挿した1km面です。季節予報は予測値モードで表示します。";
    elements.mapInfoPrimary.textContent = mapPrimaryTitle();
    elements.mapInfoSecondary.textContent = "平年値モード｜1km気候統計";
    return;
  }
  const context = forecastContext(false);
  const climate = `気候データ ${store.climateManifest.dataset_id}｜${store.climateManifest.mesh_count.toLocaleString("ja-JP")}メッシュ`;
  const season = context.product && context.forecastElement
    ? `｜季節予報 ${formatJst(context.product.report_datetime)}発表`
    : "";
  elements.sourceStatus.textContent = `${climate}${season}`;
  elements.sourceDetailStatus.textContent = "季節予報は公式地域確率、比較面は独自算出した1km平年値です。予報地域を1kmへ補間していません。";
  const forecastTitle = context.canOverlay
    ? `${PRODUCT_LABELS[state.forecastProduct]}・${context.forecastName}｜${periodLabel(context.term)}`
    : context.reason
      ? `${PRODUCT_LABELS[state.forecastProduct]}・${context.forecastName}｜表示なし`
      : `${PRODUCT_LABELS[state.forecastProduct]}・${context.forecastName}｜OFF`;
  elements.mapInfoPrimary.textContent = forecastTitle;
  elements.mapInfoSecondary.textContent = `比較面：${mapPrimaryTitle()}`;
}

let selectionSequence = 0;
let selectionPending = false;

async function selectAtLatLon(lat, lon, regionSelection = null, options = {}) {
  const code = meshCodeFromLatLon(lat, lon);
  if (!code) return;
  const sequence = ++selectionSequence;
  selectionPending = true;
  clearPreview({ render: false });
  setNotice(`1kmメッシュ ${code} を確認中…`);
  const requestedElement = state.element;
  let record;
  try {
    record = await store.meshRecord(code, requestedElement);
  } catch (error) {
    if (sequence === selectionSequence) {
      selectionPending = false;
      setNotice(`地点値を取得できませんでした: ${error.message}`, "error");
    }
    return;
  }
  if (sequence !== selectionSequence || requestedElement !== state.element) return;
  selectionPending = false;
  if (!record) {
    if (!state.selectedMesh) {
      state.meshCode = null;
      state.regionCode = null;
      state.regionName = null;
      state.selectedForecast = null;
      map.selectMesh(null, null);
      renderSelected();
      syncUrl();
    }
    setNotice("この1kmメッシュには気候値がありません（海域など）", "warn");
    return;
  }
  state.meshCode = code;
  state.selectedMesh = record;
  document.body.classList.remove("detail-mobile-closed", "settings-open");
  const context = forecastContext(false);
  if (regionSelection && context.canOverlay) {
    state.regionCode = regionSelection.class15Code;
    state.regionName = regionSelection.class15Name;
    state.selectedForecast = regionSelection.forecast;
  } else if (context.canOverlay) {
    const feature = store.regionAtLatLon(record.centerLat, record.centerLon);
    state.regionCode = feature?.properties.code || null;
    state.regionName = feature?.properties.name || null;
    state.selectedForecast = state.regionCode ? context.term?.regions?.[state.regionCode] || null : null;
  } else {
    state.regionCode = null;
    state.regionName = null;
    state.selectedForecast = null;
  }
  map.selectMesh(code, meshBounds(code), options.pan === true);
  renderSelected();
  syncUrl();
  if (!options.quiet) setNotice(`1kmメッシュ ${code} を固定しました`, "ok");
}

function clearPinnedSelection() {
  selectionSequence += 1;
  selectionPending = false;
  clearPreview({ render: false });
  state.meshCode = null;
  state.selectedMesh = null;
  state.regionCode = null;
  state.regionName = null;
  state.selectedForecast = null;
  map.selectMesh(null, null);
  document.body.classList.remove("detail-mobile-closed");
  renderSelected();
  syncUrl();
  setNotice("地点の固定を解除しました", "ok");
}

function pointForecast(selection, context) {
  if (!selection || !context.canOverlay) return { regionCode: null, regionName: null, forecast: null };
  if (selection.kind === "pinned") {
    return { regionCode: state.regionCode, regionName: state.regionName, forecast: state.selectedForecast };
  }
  if (!selection.record) return { regionCode: null, regionName: null, forecast: null };
  const feature = store.regionAtLatLon(selection.record.centerLat, selection.record.centerLon);
  const regionCode = feature?.properties.code || null;
  return {
    regionCode,
    regionName: feature?.properties.name || null,
    forecast: regionCode ? context.term?.regions?.[regionCode] || null : null,
  };
}

function renderSelected() {
  if (state.mapMode === "recent") {
    renderRecentSelection();
    return;
  }
  const pinnedRecord = state.selectedMesh?.elementCode === state.element ? state.selectedMesh : null;
  const preview = !pinnedRecord && state.preview?.elementCode === state.element ? state.preview : null;
  const selection = pinnedRecord
    ? { kind: "pinned", status: "ready", record: pinnedRecord, meshCode: pinnedRecord.meshCode }
    : preview ? { kind: "preview", ...preview } : null;
  const record = selection?.record || null;
  document.body.classList.toggle("has-selection", Boolean(pinnedRecord));
  document.body.classList.toggle("has-preview", Boolean(preview));
  elements.pointUnpin.hidden = selection?.kind !== "pinned";
  elements.pointChartSection.hidden = selection?.kind !== "pinned" || !record;

  if (!selection) {
    elements.pointState.dataset.state = "idle";
    elements.pointState.textContent = "カーソルで確認・クリックで固定";
    elements.meshCode.textContent = "地点未選択";
    elements.meshValue.textContent = "--";
    elements.meshPeriod.textContent = "地図上のカーソル位置を表示します";
    elements.meshCoords.textContent = "海域には値を表示しません";
    elements.windowOldValue.textContent = "--";
    elements.windowNewValue.textContent = "--";
    elements.differenceValue.textContent = "--";
  } else if (!record) {
    elements.pointState.dataset.state = "preview";
    elements.pointState.textContent = "カーソル位置（プレビュー）";
    elements.meshCode.textContent = `1kmメッシュ ${selection.meshCode}`;
    elements.meshValue.textContent = "--";
    elements.meshPeriod.textContent = selection.status === "error" ? "地点値を取得できませんでした" : "このメッシュには気候値がありません";
    elements.meshCoords.textContent = "海域など、地点参照データのない場所です";
    elements.windowOldValue.textContent = "--";
    elements.windowNewValue.textContent = "--";
    elements.differenceValue.textContent = "--";
  } else {
    const oldValue = record.values[OLD_WINDOW]?.[state.month];
    const newValue = record.values[NEW_WINDOW]?.[state.month];
    elements.pointState.dataset.state = selection.kind;
    elements.pointState.textContent = selection.kind === "pinned" ? "選択地点（固定）" : "カーソル位置（プレビュー）";
    elements.meshCode.textContent = `1kmメッシュ ${record.meshCode}`;
    elements.meshValue.textContent = formatClimateValue(selectedValue(record), state.mode === "difference");
    elements.meshPeriod.textContent = climateSubtitle();
    elements.meshCoords.textContent = `中心 ${record.centerLat.toFixed(4)}°N, ${record.centerLon.toFixed(4)}°E｜独自算出・独自内挿`;
    elements.windowOldValue.textContent = formatClimateValue(oldValue);
    elements.windowNewValue.textContent = formatClimateValue(newValue);
    elements.differenceValue.textContent = Number.isFinite(oldValue) && Number.isFinite(newValue)
      ? formatClimateValue(newValue - oldValue, true)
      : "--";
    if (selection.kind === "pinned") renderMonthlyChart(record);
  }

  const context = forecastContext(false);
  const displayed = pointForecast(selection, context);
  updateForecastClassLabels(context.labels);
  if (displayed.forecast && context.canOverlay) {
    const probabilities = displayed.forecast.probabilities;
    elements.forecastRegion.textContent = `${displayed.regionName || displayed.regionCode}｜${displayed.forecast.forecast_region_name}`;
    elements.forecastPeriod.textContent = periodLabel(context.term);
    elements.probabilityBelow.textContent = `${probabilities[0]}%`;
    elements.probabilityNormal.textContent = `${probabilities[1]}%`;
    elements.probabilityAbove.textContent = `${probabilities[2]}%`;
    elements.forecastNote.textContent = `${forecastLeadLabel(probabilities, context.labels)}。この地点が属する予報地域の確率で、1km地点予報ではありません。`;
  } else {
    elements.forecastRegion.textContent = context.reason
      || (displayed.regionCode && context.canOverlay
        ? `${displayed.regionName || displayed.regionCode}｜この地域は発表なし`
        : state.forecastVisible ? "カーソル位置または固定地点で確認" : "季節予報レイヤはOFF");
    elements.forecastPeriod.textContent = periodLabel(context.term);
    elements.probabilityBelow.textContent = "--";
    elements.probabilityNormal.textContent = "--";
    elements.probabilityAbove.textContent = "--";
    elements.forecastNote.textContent = context.reason
      || (displayed.regionCode && context.canOverlay
        ? "発表のある地域だけを着色しています。欠色地域を0%として扱わないでください。"
        : "気候平均と季節予報は異なる空間単位です。");
  }
}

const HOVER_INTERVAL_MS = 100;
let hoverTimer = null;
let hoverSequence = 0;
let hoverLastStarted = 0;
let pendingHover = null;
let latestHoverKey = null;

function supportsLivePreview() {
  return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

function queueHover() {
  if (hoverTimer !== null || !pendingHover) return;
  const delay = Math.max(0, HOVER_INTERVAL_MS - (performance.now() - hoverLastStarted));
  hoverTimer = setTimeout(processHover, delay);
}

async function processHover() {
  hoverTimer = null;
  const request = pendingHover;
  pendingHover = null;
  if (!request) return;
  hoverLastStarted = performance.now();
  let record = null;
  let status = "empty";
  try {
    record = await store.meshRecord(request.meshCode, request.elementCode);
    status = record ? "ready" : "empty";
  } catch {
    status = "error";
  }
  if (request.sequence === hoverSequence && request.elementCode === state.element && !state.selectedMesh) {
    state.preview = { ...request, status, record };
    renderSelected();
  }
  queueHover();
}

function scheduleHover(latlng) {
  if (!state.initialized || state.selectedMesh || selectionPending || !supportsLivePreview()) {
    if (state.preview) clearPreview();
    return;
  }
  const code = meshCodeFromLatLon(latlng.lat, latlng.lng);
  if (!code) {
    clearPreview();
    return;
  }
  const key = `${state.element}:${code}`;
  if (key === latestHoverKey) return;
  latestHoverKey = key;
  const sequence = ++hoverSequence;
  pendingHover = {
    sequence,
    elementCode: state.element,
    meshCode: code,
    lat: latlng.lat,
    lon: latlng.lng,
  };
  queueHover();
}

function clearPreview({ render = true } = {}) {
  if (hoverTimer !== null) clearTimeout(hoverTimer);
  hoverTimer = null;
  pendingHover = null;
  latestHoverKey = null;
  hoverSequence += 1;
  const hadPreview = Boolean(state.preview);
  state.preview = null;
  if (render && hadPreview) renderSelected();
}

function buildUrl() {
  const url = new URL(location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("view", state.mapMode);
  url.searchParams.set("element", state.element);
  url.searchParams.set("window", state.window);
  url.searchParams.set("month", state.month);
  url.searchParams.set("mode", state.mode);
  url.searchParams.set("forecast", state.forecastProduct);
  url.searchParams.set("term", state.forecastTerm);
  url.searchParams.set("overlay", state.forecastVisible ? "on" : "off");
  url.searchParams.set("cop", Math.round(state.climateOpacity * 100));
  url.searchParams.set("fop", Math.round(state.forecastOpacity * 100));
  url.searchParams.set("base", state.base);
  url.searchParams.set("labels", state.showPlaceLabels ? "1" : "0");
  url.searchParams.set("labelOpacity", Math.round(state.placeLabelOpacity * 100));
  url.searchParams.set("detail", state.showDetailMap ? "1" : "0");
  url.searchParams.set("detailOpacity", Math.round(state.detailMapOpacity * 100));
  url.searchParams.set("terrain", state.showTerrain ? "1" : "0");
  url.searchParams.set("terrainStyle", state.terrainStyle);
  url.searchParams.set("terrainOpacity", Math.round(state.terrainOpacity * 100));
  if (state.mapMode === "recent") {
    url.searchParams.set("start", state.recentStart);
    url.searchParams.set("end", state.recentEnd);
    if (state.recentStationId) url.searchParams.set("station", state.recentStationId);
  }
  const view = map.viewState();
  url.searchParams.set("z", view.zoom);
  if (["climate", "forecast"].includes(state.mapMode) && state.meshCode) {
    url.searchParams.set("mesh", state.meshCode);
    if (state.regionCode) url.searchParams.set("region", state.regionCode);
  } else {
    url.searchParams.set("lat", view.lat.toFixed(3));
    url.searchParams.set("lon", view.lon.toFixed(3));
  }
  return url;
}

function syncUrl() {
  if (!state.initialized) return;
  history.replaceState(null, "", buildUrl());
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = value;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const result = document.execCommand("copy");
    area.remove();
    return result;
  }
}

async function switchElement(code) {
  if (String(code) === state.element) return;
  const previousMesh = state.meshCode;
  elements.elementSelect.disabled = true;
  setLoading(true, "気候要素を切り替えています");
  selectionSequence += 1;
  selectionPending = false;
  clearPreview();
  state.selectedForecast = null;
  state.regionCode = null;
  state.regionName = null;
  map.setSeasonOverlay(store.regions, null, false);
  try {
    await store.setElement(code);
    state.element = String(code);
    state.selectedMesh = null;
    if (!elementConfig().forecastElement) state.forecastVisible = false;
    populateMonths();
    applyControls();
    updateClimate();
    updateForecast();
    if (previousMesh) {
      const bounds = meshBounds(previousMesh);
      await selectAtLatLon(bounds.centerLat, bounds.centerLon, null, { quiet: true });
    }
    setNotice(`${elementConfig().name}へ切り替えました`, "ok");
  } catch (error) {
    console.error(error);
    setNotice(`要素を切り替えられませんでした: ${error.message}`, "error");
    elements.elementSelect.value = state.element;
  } finally {
    elements.elementSelect.disabled = false;
    setLoading(false);
  }
}

let recentSliderFrame = null;

function bindControls() {
  elements.settingsToggle.addEventListener("click", () => document.body.classList.toggle("settings-open"));
  elements.settingsClose.addEventListener("click", () => document.body.classList.remove("settings-open"));
  elements.detailClose.addEventListener("click", () => document.body.classList.add("detail-mobile-closed"));
  elements.pointUnpin.addEventListener("click", clearPinnedSelection);
  document.querySelectorAll("[data-map-mode]").forEach((button) => button.addEventListener("click", () => {
    switchMapMode(button.dataset.mapMode);
  }));
  document.querySelectorAll("[data-recent-preset]").forEach((button) => button.addEventListener("click", () => {
    setRecentPreset(button.dataset.recentPreset);
  }));
  elements.recentApply.addEventListener("click", () => {
    const range = store.recentTemperatureRange();
    const start = elements.recentStart.value;
    const end = elements.recentEnd.value;
    const days = parseIsoDate(start) && parseIsoDate(end) ? inclusiveDayCount(start, end) : 0;
    if (!days || start < range.start || end > range.end || start > end) {
      setNotice("収録範囲内で開始日と終了日を指定してください", "error");
      return;
    }
    if (days > 93) {
      setNotice("任意期間は93日以内にしてください", "error");
      return;
    }
    state.recentStart = start;
    state.recentEnd = end;
    state.recentPreset = inferRecentPreset(start, end);
    updateRecentTemperature();
  });
  elements.recentCenterSlider.addEventListener("input", () => {
    const dates = store.recentTemperature.dates;
    const centerIndex = Number(elements.recentCenterSlider.value);
    state.recentPreset = "5day";
    state.recentStart = dates[centerIndex - 2];
    state.recentEnd = dates[centerIndex + 2];
    elements.recentCenterLabel.textContent = formatIsoDate(dates[centerIndex]);
    if (recentSliderFrame !== null) cancelAnimationFrame(recentSliderFrame);
    recentSliderFrame = requestAnimationFrame(() => {
      recentSliderFrame = null;
      updateRecentTemperature();
    });
  });
  elements.elementSelect.addEventListener("change", () => switchElement(elements.elementSelect.value));
  document.querySelectorAll("[data-window]").forEach((button) => button.addEventListener("click", () => {
    state.window = button.dataset.window;
    if (state.mode === "difference") state.mode = "absolute";
    applyControls();
    updateClimate();
  }));
  document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
    state.mode = button.dataset.mode;
    applyControls();
    updateClimate();
  }));
  document.querySelectorAll("[data-base]").forEach((button) => button.addEventListener("click", () => {
    state.base = map.setBase(button.dataset.base);
    applyControls();
    syncUrl();
  }));
  elements.placeLabelsToggle.addEventListener("change", () => {
    state.showPlaceLabels = elements.placeLabelsToggle.checked;
    map.setPlaceLabelsVisible(state.showPlaceLabels);
    syncMapLayerControls();
    syncUrl();
  });
  elements.placeLabelOpacity.addEventListener("input", () => {
    state.placeLabelOpacity = Number(elements.placeLabelOpacity.value) / 100;
    map.setPlaceLabelOpacity(state.placeLabelOpacity);
    syncMapLayerControls();
    syncUrl();
  });
  elements.detailMapToggle.addEventListener("change", () => {
    state.showDetailMap = elements.detailMapToggle.checked;
    map.setDetailMap(state.showDetailMap, state.detailMapOpacity);
    syncMapLayerControls();
    syncUrl();
  });
  elements.detailMapOpacity.addEventListener("input", () => {
    state.detailMapOpacity = Number(elements.detailMapOpacity.value) / 100;
    map.setDetailMapOpacity(state.detailMapOpacity);
    syncMapLayerControls();
    syncUrl();
  });
  elements.terrainToggle.addEventListener("change", () => {
    state.showTerrain = elements.terrainToggle.checked;
    map.setTerrain(state.showTerrain, state.terrainStyle, state.terrainOpacity);
    syncMapLayerControls();
    syncUrl();
  });
  elements.terrainStyle.addEventListener("change", () => {
    state.terrainStyle = elements.terrainStyle.value;
    map.setTerrain(state.showTerrain, state.terrainStyle, state.terrainOpacity);
    syncMapLayerControls();
    syncUrl();
  });
  elements.terrainOpacity.addEventListener("input", () => {
    state.terrainOpacity = Number(elements.terrainOpacity.value) / 100;
    map.setTerrainOpacity(state.terrainOpacity);
    syncMapLayerControls();
    syncUrl();
  });
  elements.monthSelect.addEventListener("change", () => {
    state.month = Number(elements.monthSelect.value);
    updateClimate();
  });
  elements.climateOpacity.addEventListener("input", () => {
    state.climateOpacity = Number(elements.climateOpacity.value) / 100;
    map.setClimateOpacity(state.climateOpacity);
    syncUrl();
  });
  elements.forecastToggle.addEventListener("change", () => {
    state.forecastVisible = elements.forecastToggle.checked;
    updateForecast();
  });
  elements.forecastProduct.addEventListener("change", () => {
    state.forecastProduct = elements.forecastProduct.value;
    state.forecastTerm = "0";
    updateForecast();
  });
  elements.forecastTerm.addEventListener("change", () => {
    state.forecastTerm = elements.forecastTerm.value;
    updateForecast();
  });
  elements.forecastOpacity.addEventListener("input", () => {
    state.forecastOpacity = Number(elements.forecastOpacity.value) / 100;
    updateForecast();
  });
  elements.resetView.addEventListener("click", () => map.resetView());
  elements.locate.addEventListener("click", () => {
    if (!navigator.geolocation) {
      setNotice("このブラウザでは現在地を取得できません", "error");
      return;
    }
    elements.locate.disabled = true;
    setNotice("現在地を確認中…");
    navigator.geolocation.getCurrentPosition(async (position) => {
      if (state.mapMode === "recent") {
        map.setView(position.coords.latitude, position.coords.longitude, 8);
        setNotice("現在地周辺へ移動しました", "ok");
        elements.locate.disabled = false;
        return;
      }
      const code = meshCodeFromLatLon(position.coords.latitude, position.coords.longitude);
      const bounds = code ? meshBounds(code) : null;
      if (bounds) await selectAtLatLon(bounds.centerLat, bounds.centerLon, null, { pan: true });
      else setNotice("現在地を1kmメッシュへ変換できませんでした", "error");
      elements.locate.disabled = false;
    }, (error) => {
      setNotice(`現在地を取得できませんでした: ${error.message}`, "error");
      elements.locate.disabled = false;
    }, { enableHighAccuracy: false, timeout: 12000, maximumAge: 300000 });
  });
  elements.copyLink.addEventListener("click", async () => {
    const ok = await copyText(buildUrl().toString());
    setNotice(ok ? "表示状態のリンクをコピーしました" : "リンクをコピーできませんでした", ok ? "ok" : "error");
  });
  elements.saveImage.addEventListener("click", async () => {
    elements.saveImage.disabled = true;
    setNotice("地図画像を作成中…");
    try {
      let blob;
      let filename;
      if (state.mapMode === "recent") {
        const point = state.recentStation;
        blob = await map.capture({
          subtitle: `平均気温の平年差｜${recentPeriodText()}`,
          detailLines: [
            point
              ? `${point.name}｜平年差 ${formatSignedTemperature(point.anomaly)}｜期間平均 ${point.observedMean.toFixed(1)}℃`
              : `全国 ${state.recentResult.points.length}/${store.recentTemperatureManifest.station_count}地点`,
            "平年：気象庁2020年平年値（1991–2020）",
            state.recentResult.expectedDays === 5
              ? "5日：気象庁の公式5日間平年値を使用"
              : "任意期間：公式日別平年値から独自集計",
            "表示：気象台等＋アメダス（地点間の面的補間なし）",
          ],
          legend: {
            ...TEMPERATURE_ANOMALY_LEGEND,
            ticks: recentLegendTicks(),
          },
        });
        filename = `climate-outlook-navi-temperature-anomaly-${state.recentStart}-${state.recentEnd}.png`;
      } else {
        const context = forecastContext(false);
        const config = activeRasterLegend();
        const ticks = climateLegendTicks(config, state.mode);
        const detail = state.selectedMesh
          ? `${state.selectedMesh.meshCode}｜${formatClimateValue(selectedValue(), state.mode === "difference")}`
          : "地点未選択";
        const forecastDetail = state.selectedForecast && context.canOverlay
          ? `${state.regionName || state.regionCode}｜${state.selectedForecast.forecast_region_name}｜${forecastLeadLabel(state.selectedForecast.probabilities, context.labels)}｜${context.labels[0]}${state.selectedForecast.probabilities[0]}%・${context.labels[1]}${state.selectedForecast.probabilities[1]}%・${context.labels[2]}${state.selectedForecast.probabilities[2]}%`
          : context.reason || "季節予報地域: 地点未選択";
        const forecastMode = state.mapMode === "forecast";
        const forecastPeriod = forecastMode && context.term ? `｜季節予報：${periodLabel(context.term)}` : "";
        const captureSubtitle = forecastMode
          ? `${PRODUCT_LABELS[state.forecastProduct]}・${context.forecastName}${forecastPeriod}｜比較面：${mapPrimaryTitle()}`
          : mapPrimaryTitle();
        const detailLines = [
          detail,
          ...(forecastMode ? [forecastDetail] : []),
          "平年値：気象庁観測から独自算出・独自内挿",
          "標高：国土数値情報 G04-a（国土交通省）",
          ...(forecastMode ? ["予測値：気象庁・地域確率｜灰色＝同率首位"] : []),
        ];
        blob = await map.capture({
          subtitle: captureSubtitle,
          detailLines,
          legend: {
            title: elements.climateLegendTitle.textContent,
            unit: elementConfig().unit,
            colors: config.colors,
            ticks,
            low: elements.legendLow.textContent,
            middle: elements.legendMiddle.textContent,
            high: elements.legendHigh.textContent,
          },
        });
        filename = `climate-outlook-navi-${state.element}-${state.mode}-m${String(state.month).padStart(2, "0")}.png`;
      }
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      setNotice("地図画像を保存しました", "ok");
    } catch (error) {
      console.error(error);
      setNotice(`画像保存に失敗しました: ${error.message}`, "error");
    } finally {
      elements.saveImage.disabled = false;
    }
  });
}

async function initialize() {
  try {
    setLoading(true, "気候データと季節予報を読み込んでいます");
    const requestedElement = new URLSearchParams(location.search).get("element") || "201";
    await store.initialize(requestedElement);
    state.element = store.activeElementCode;
    populateElements();
    initializeRecentState();
    const initialView = parseInitialState();
    if (state.mapMode === "forecast" && !elementConfig().forecastElement) {
      await store.setElement("201");
      state.element = store.activeElementCode;
      populateElements();
    }
    populateMonths();
    if (!elementConfig().forecastElement) state.forecastVisible = false;
    forecastContext(true);
    document.body.classList.toggle("recent-view", state.mapMode === "recent");
    document.body.classList.toggle("forecast-view", state.mapMode === "forecast");
    applyControls();
    map.setBase(state.base);
    map.setPlaceLabels(store.recentTemperature.stations);
    map.setPlaceLabelsVisible(state.showPlaceLabels);
    map.setPlaceLabelOpacity(state.placeLabelOpacity);
    map.setDetailMap(state.showDetailMap, state.detailMapOpacity);
    map.setTerrain(state.showTerrain, state.terrainStyle, state.terrainOpacity);
    if (initialView.lat !== null && initialView.lon !== null) {
      map.setView(initialView.lat, initialView.lon, initialView.zoom);
    }
    const prefectures = store.prefecturePath();
    if (prefectures) await map.setBoundaries(prefectures);
    state.initialized = true;
    if (state.mapMode === "recent") {
      updateRecentTemperature();
    } else {
      updateClimate();
      updateForecast();
    }
    if (["climate", "forecast"].includes(state.mapMode) && state.meshCode) {
      const bounds = meshBounds(state.meshCode);
      await selectAtLatLon(bounds.centerLat, bounds.centerLon, null, { quiet: true });
      if (initialView.zoom) map.setView(bounds.centerLat, bounds.centerLon, initialView.zoom);
      else map.setView(bounds.centerLat, bounds.centerLon, 9);
    }
    elements.sourceStatus.title = state.mapMode === "recent"
      ? "気象台等とアメダスの全国観測地点値。地点間は補間していません。"
      : state.mapMode === "forecast"
        ? "季節予報は公式地域確率、比較面は独自算出した1km平年値です。"
        : "全国表示用ラスターは描画縮約。地点値は全387,717メッシュを保持した要素別バイナリから参照します。";
    bindControls();
    renderSelected();
    syncUrl();
    setLoading(false);
  } catch (error) {
    console.error(error);
    setLoading(true, `読み込みに失敗しました: ${error.message}`);
    elements.loading.classList.add("error");
  }
}

initialize();
