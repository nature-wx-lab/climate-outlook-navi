import { ClimateDataStore, meshBounds, meshCodeFromLatLon } from "./data.js?v=20260710-elements1";
import { ClimateMap } from "./map.js?v=20260711-pointdetail1";

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
  element: "201",
  window: "1996_2025",
  month: currentMonth,
  mode: "absolute",
  climateOpacity: 0.86,
  forecastVisible: true,
  forecastProduct: "P1M",
  forecastTerm: "0",
  forecastOpacity: 0.28,
  base: "pale",
  meshCode: null,
  selectedMesh: null,
  preview: null,
  regionCode: null,
  regionName: null,
  selectedForecast: null,
  initialized: false,
};

const elements = Object.fromEntries([
  "loading", "loadingText", "mapInfo", "elementSelect", "monthSelect", "climateOpacity",
  "climateControlNote", "forecastToggle", "forecastProduct", "forecastTerm", "forecastOpacity",
  "forecastProductField", "forecastTermField", "forecastOpacityField", "forecastControlNote",
  "climateLegend", "climateLegendTitle", "legendLow", "legendMiddle", "legendHigh",
  "seasonLegend", "seasonKeys", "seasonClassBelow", "seasonClassNormal", "seasonClassAbove",
  "seasonStatus", "sourceStatus", "pointState", "pointUnpin", "meshCode", "meshValue", "meshPeriod", "meshCoords",
  "windowOldValue", "windowNewValue", "differenceValue", "forecastRegion", "forecastPeriod",
  "probabilityBelowLabel", "probabilityNormalLabel", "probabilityAboveLabel",
  "probabilityBelow", "probabilityNormal", "probabilityAbove", "forecastNote", "copyLink",
  "saveImage", "locate", "resetView", "notice", "settingsToggle", "settingsClose", "detailClose",
  "pointChartSection", "pointChartMeasure", "pointMonthlyChart", "pointChartCaption", "pointChartTableBody", "pointChartNote",
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
  onMapClick: (latlng) => selectAtLatLon(latlng.lat, latlng.lng),
  onRegionClick: (selection) => selectAtLatLon(selection.latlng.lat, selection.latlng.lng, selection),
  onViewChange: () => {
    if (state.initialized) syncUrl();
  },
  onPointerMove: (latlng) => scheduleHover(latlng),
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

function parseInitialState() {
  const params = new URLSearchParams(location.search);
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
  if (/^\d{8}$/.test(params.get("mesh") || "")) state.meshCode = params.get("mesh");
  if (/^(hoppo|\d{6})$/.test(params.get("region") || "")) state.regionCode = params.get("region");
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

function applyControls() {
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
  document.getElementById("windowControls").classList.toggle("muted-control", state.mode === "difference");
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
    canOverlay: Boolean(state.forecastVisible && forecastElement && elementData?.supported && regionCount > 0),
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

function updateClimateLegend() {
  const config = activeRasterLegend();
  if (!config?.colors?.length) throw new Error(`凡例がありません: ${state.element}/${state.month}/${state.mode}`);
  const stops = config.colors.map((color, index) => `${color} ${(index / (config.colors.length - 1)) * 100}%`).join(",");
  const labels = legendLabels(config, state.mode);
  elements.climateLegend.style.background = `linear-gradient(90deg,${stops})`;
  elements.climateLegendTitle.textContent = config.title
    || (state.mode === "difference" ? "30年平均値の更新差" : elementConfig().name);
  [elements.legendLow.textContent, elements.legendMiddle.textContent, elements.legendHigh.textContent] = labels;
}

function climateSubtitle() {
  const config = elementConfig();
  const windowLabel = store.climateManifest.windows.find((entry) => entry.id === state.window)?.label || state.window;
  return state.mode === "difference"
    ? `${config.name}｜${monthLabel()}｜30年平均値の更新差（1996–2025 − 1991–2020）`
    : `${config.name}｜${monthLabel()}｜${windowLabel}`;
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
  const path = store.climateRasterPath(state);
  map.setClimateRaster(path, store.climateManifest.rasters.render, state.climateOpacity);
  updateClimateLegend();
  updateClimateControlNote();
  renderSelected();
  updateStatus();
  syncUrl();
}

function updateForecastClassLabels(labels) {
  [elements.seasonClassBelow.textContent, elements.seasonClassNormal.textContent, elements.seasonClassAbove.textContent] = labels;
  [elements.probabilityBelowLabel.textContent, elements.probabilityNormalLabel.textContent, elements.probabilityAboveLabel.textContent] = labels;
}

function updateForecast() {
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
  const context = forecastContext(false);
  const climate = `気候データ ${store.climateManifest.dataset_id}｜${store.climateManifest.mesh_count.toLocaleString("ja-JP")}メッシュ`;
  const season = context.product && context.forecastElement
    ? `｜季節予報 ${formatJst(context.product.report_datetime)}発表`
    : "";
  elements.sourceStatus.textContent = `${climate}${season}`;
  const overlay = context.canOverlay
    ? `｜${periodLabel(context.term)}`
    : context.reason
      ? "｜季節予報なし・未発表"
      : "｜季節予報OFF";
  elements.mapInfo.textContent = `${climateSubtitle()}${overlay}`;
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
  const view = map.viewState();
  url.searchParams.set("z", view.zoom);
  if (state.meshCode) {
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

function bindControls() {
  elements.settingsToggle.addEventListener("click", () => document.body.classList.toggle("settings-open"));
  elements.settingsClose.addEventListener("click", () => document.body.classList.remove("settings-open"));
  elements.detailClose.addEventListener("click", () => document.body.classList.add("detail-mobile-closed"));
  elements.pointUnpin.addEventListener("click", clearPinnedSelection);
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
      const context = forecastContext(false);
      const config = activeRasterLegend();
      const detail = state.selectedMesh
        ? `${state.selectedMesh.meshCode}｜${formatClimateValue(selectedValue(), state.mode === "difference")}`
        : "地点未選択";
      const forecastDetail = state.selectedForecast && context.canOverlay
        ? `${state.regionName || state.regionCode}｜${state.selectedForecast.forecast_region_name}｜${forecastLeadLabel(state.selectedForecast.probabilities, context.labels)}｜${context.labels[0]}${state.selectedForecast.probabilities[0]}%・${context.labels[1]}${state.selectedForecast.probabilities[1]}%・${context.labels[2]}${state.selectedForecast.probabilities[2]}%`
        : context.reason || "季節予報地域: 地点未選択";
      const forecastPeriod = context.term ? `｜${periodLabel(context.term)}` : "";
      const blob = await map.capture({
        subtitle: `${climateSubtitle()}${forecastPeriod}`,
        detail,
        forecastDetail,
        legend: {
          title: elements.climateLegendTitle.textContent,
          colors: config.colors,
          low: elements.legendLow.textContent,
          middle: elements.legendMiddle.textContent,
          high: elements.legendHigh.textContent,
        },
      });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `climate-outlook-navi-${state.element}-${state.mode}-m${String(state.month).padStart(2, "0")}.png`;
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
    const initialView = parseInitialState();
    populateMonths();
    if (!elementConfig().forecastElement) state.forecastVisible = false;
    forecastContext(true);
    applyControls();
    map.setBase(state.base);
    if (initialView.lat !== null && initialView.lon !== null) {
      map.setView(initialView.lat, initialView.lon, initialView.zoom);
    }
    const prefectures = store.prefecturePath();
    if (prefectures) await map.setBoundaries(prefectures);
    state.initialized = true;
    updateClimate();
    updateForecast();
    if (state.meshCode) {
      const bounds = meshBounds(state.meshCode);
      await selectAtLatLon(bounds.centerLat, bounds.centerLon, null, { quiet: true });
      if (initialView.zoom) map.setView(bounds.centerLat, bounds.centerLon, initialView.zoom);
      else map.setView(bounds.centerLat, bounds.centerLon, 9);
    }
    elements.sourceStatus.title = "全国表示用ラスターは描画縮約。地点値は全387,717メッシュを保持した要素別バイナリから参照します。";
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
