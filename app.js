import { ClimateDataStore, meshBounds, meshCodeFromLatLon } from "./data.js?v=20260710-mvp4b";
import { ClimateMap } from "./map.js?v=20260710-mvp4b";

const store = new ClimateDataStore();
const currentMonth = new Date().getMonth() + 1;
const state = {
  window: "1996_2025",
  month: currentMonth,
  mode: "absolute",
  climateOpacity: 0.86,
  forecastVisible: true,
  forecastProduct: "P1M",
  forecastTerm: 0,
  forecastOpacity: 0.28,
  base: "pale",
  meshCode: null,
  selectedMesh: null,
  regionCode: null,
  regionName: null,
  selectedForecast: null,
  initialized: false,
};

const elements = Object.fromEntries([
  "loading", "loadingText", "mapInfo", "monthSelect", "climateOpacity",
  "forecastToggle", "forecastProduct", "forecastTerm", "forecastOpacity",
  "climateLegend", "climateLegendTitle", "legendLow", "legendMiddle", "legendHigh",
  "seasonLegend", "seasonStatus", "sourceStatus", "meshCode", "meshValue",
  "meshPeriod", "meshCoords", "windowOldValue", "windowNewValue", "differenceValue",
  "forecastRegion", "forecastPeriod", "probabilityBelow", "probabilityNormal", "probabilityAbove",
  "forecastNote", "copyLink", "saveImage", "locate", "resetView", "notice",
  "settingsToggle", "settingsClose", "detailClose",
].map((id) => [id, document.getElementById(id)]));

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[character]));
}

function forecastLeadLabel(probabilities) {
  const labels = ["低い", "平年並", "高い"];
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
  const targetEnd = new Date(product.terms?.[0]?.end || 0).getTime();
  return product.status === "available" && Date.now() <= targetEnd && reportAgeDays <= maximumAge ? "available" : "stale";
}

function monthLabel(month) {
  return Number(month) === 13 ? "年平均" : `${month}月`;
}

function signed(value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "--";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}℃`;
}

function temperature(value) {
  return value === null || value === undefined || !Number.isFinite(value) ? "--" : `${value.toFixed(2)}℃`;
}

function selectedValue(record = state.selectedMesh) {
  if (!record) return null;
  const oldValue = record.values["1991_2020"][state.month];
  const newValue = record.values["1996_2025"][state.month];
  return state.mode === "difference"
    ? newValue - oldValue
    : record.values[state.window][state.month];
}

const map = new ClimateMap("map", {
  onMapClick: (latlng) => selectAtLatLon(latlng.lat, latlng.lng),
  onRegionClick: (selection) => selectAtLatLon(selection.latlng.lat, selection.latlng.lng, selection),
  onViewChange: () => {
    if (state.initialized) syncUrl();
  },
  onPointerMove: (latlng) => scheduleHover(latlng),
  onPointerLeave: () => clearHover(),
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

function populateMonths() {
  elements.monthSelect.innerHTML = store.climateManifest.months
    .map((month) => `<option value="${month.id}">${month.label}</option>`).join("");
  elements.monthSelect.value = String(state.month);
}

function parseInitialState() {
  const params = new URLSearchParams(location.search);
  if (["1991_2020", "1996_2025"].includes(params.get("window"))) state.window = params.get("window");
  const month = Number(params.get("month"));
  if (Number.isInteger(month) && month >= 1 && month <= 13) state.month = month;
  if (["absolute", "difference"].includes(params.get("mode"))) state.mode = params.get("mode");
  if (["P1M", "P3M"].includes(params.get("forecast"))) state.forecastProduct = params.get("forecast");
  const term = Number(params.get("term"));
  if (Number.isInteger(term) && term >= 0 && term <= 3) state.forecastTerm = term;
  if (params.get("overlay") === "off") state.forecastVisible = false;
  const climateOpacity = Number(params.get("cop"));
  if (Number.isFinite(climateOpacity) && climateOpacity >= 10 && climateOpacity <= 100) state.climateOpacity = climateOpacity / 100;
  const forecastOpacity = Number(params.get("fop"));
  if (Number.isFinite(forecastOpacity) && forecastOpacity >= 5 && forecastOpacity <= 70) state.forecastOpacity = forecastOpacity / 100;
  if (["blank", "pale", "standard"].includes(params.get("base"))) state.base = params.get("base");
  if (/^\d{8}$/.test(params.get("mesh") || "")) state.meshCode = params.get("mesh");
  if (/^(hoppo|\d{6})$/.test(params.get("region") || "")) state.regionCode = params.get("region");
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  const zoom = Number(params.get("z"));
  return {
    lat: Number.isFinite(lat) ? lat : null,
    lon: Number.isFinite(lon) ? lon : null,
    zoom: Number.isFinite(zoom) ? Math.min(12, Math.max(4, zoom)) : null,
  };
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
  elements.monthSelect.value = String(state.month);
  elements.climateOpacity.value = String(Math.round(state.climateOpacity * 100));
  elements.forecastToggle.checked = state.forecastVisible;
  elements.forecastProduct.value = state.forecastProduct;
  elements.forecastOpacity.value = String(Math.round(state.forecastOpacity * 100));
  document.getElementById("windowControls").classList.toggle("muted-control", state.mode === "difference");
}

function updateTermOptions() {
  const product = store.forecastProduct(state.forecastProduct);
  elements.forecastTerm.innerHTML = product.terms.map((term) => (
    `<option value="${term.id}">${term.label}｜${formatJst(term.start, false)}〜${formatJst(term.end, false)}</option>`
  )).join("");
  elements.forecastTerm.value = String(state.forecastTerm);
}

function updateClimateLegend() {
  const raster = store.climateManifest.rasters;
  const config = state.mode === "difference" ? raster.difference_legend : raster.raw_legend;
  const stops = config.colors.map((color, index) => `${color} ${(index / (config.colors.length - 1)) * 100}%`).join(",");
  elements.climateLegend.style.background = `linear-gradient(90deg,${stops})`;
  if (state.mode === "difference") {
    elements.climateLegendTitle.textContent = "30年平均値の更新差";
    elements.legendLow.textContent = "−2.0℃以下";
    elements.legendMiddle.textContent = "0℃";
    elements.legendHigh.textContent = "+2.0℃超";
  } else {
    elements.climateLegendTitle.textContent = "平均気温";
    elements.legendLow.textContent = "−27℃以下";
    elements.legendMiddle.textContent = "0℃";
    elements.legendHigh.textContent = "30℃超";
  }
}

function climateSubtitle() {
  const windowLabel = store.climateManifest.windows.find((entry) => entry.id === state.window)?.label || state.window;
  return state.mode === "difference"
    ? `${monthLabel(state.month)}｜30年平均値の更新差（1996–2025 − 1991–2020）`
    : `${monthLabel(state.month)}｜${windowLabel}`;
}

function updateClimate() {
  const path = store.climateRasterPath(state);
  map.setClimateRaster(path, store.climateManifest.rasters.render, state.climateOpacity);
  updateClimateLegend();
  elements.mapInfo.textContent = climateSubtitle();
  renderSelected();
  updateStatus();
  syncUrl();
}

function updateForecast() {
  updateTermOptions();
  const product = store.forecastProduct(state.forecastProduct);
  const term = store.forecastTerm(state.forecastProduct, state.forecastTerm);
  const effectiveStatus = effectiveForecastStatus(product, state.forecastProduct);
  if (state.regionCode) {
    const feature = store.regions.features.find((candidate) => candidate.properties.code === state.regionCode);
    state.regionName = feature?.properties.name || state.regionName;
    state.selectedForecast = term?.regions[state.regionCode] || null;
  }
  map.setSeasonOverlay(store.regions, term, state.forecastVisible, state.forecastOpacity);
  elements.seasonLegend.hidden = !state.forecastVisible;
  elements.seasonStatus.innerHTML = [
    `<b>${state.forecastProduct === "P1M" ? "1か月予報" : "3か月予報"}・気温</b>`,
    `<span>${periodLabel(term)}</span>`,
    `<span>発表 ${formatJst(product.report_datetime)}｜${effectiveStatus === "available" ? "利用可能" : "更新注意"}</span>`,
    "<small>色は最多階級。同率首位は灰色。確率3値は地点詳細で確認。</small>",
  ].join("");
  renderSelected();
  updateStatus();
  syncUrl();
}

function updateStatus() {
  const product = store.forecastProduct(state.forecastProduct);
  const term = store.forecastTerm(state.forecastProduct, state.forecastTerm);
  elements.sourceStatus.textContent = `気候データ ${store.climateManifest.dataset_id}｜${store.climateManifest.mesh_count.toLocaleString("ja-JP")}メッシュ｜季節予報 ${formatJst(product.report_datetime)}発表`;
  const overlay = state.forecastVisible ? `｜${periodLabel(term)}` : "｜季節予報OFF";
  elements.mapInfo.textContent = `${climateSubtitle()}${overlay}`;
}

async function selectAtLatLon(lat, lon, regionSelection = null, options = {}) {
  const code = meshCodeFromLatLon(lat, lon);
  if (!code) return;
  const previousCode = state.meshCode;
  setNotice(`1kmメッシュ ${code} を確認中…`);
  const record = await store.meshRecord(code);
  if (!record) {
    if (previousCode !== code) {
      state.meshCode = null;
      state.selectedMesh = null;
      if (!regionSelection) {
        state.regionCode = null;
        state.regionName = null;
        state.selectedForecast = null;
      }
    }
    renderSelected();
    setNotice("この1kmメッシュには気候値がありません（海域など）", "warn");
    return;
  }
  state.meshCode = code;
  state.selectedMesh = record;
  document.body.classList.add("has-selection");
  document.body.classList.remove("detail-mobile-closed", "settings-open");
  if (regionSelection) {
    state.regionCode = regionSelection.class15Code;
    state.regionName = regionSelection.class15Name;
    state.selectedForecast = regionSelection.forecast;
  } else if (state.forecastVisible) {
    const feature = store.regionAtLatLon(record.centerLat, record.centerLon);
    const term = store.forecastTerm(state.forecastProduct, state.forecastTerm);
    state.regionCode = feature?.properties.code || null;
    state.regionName = feature?.properties.name || null;
    state.selectedForecast = state.regionCode ? term?.regions[state.regionCode] || null : null;
  } else if (previousCode !== code) {
    state.regionCode = null;
    state.regionName = null;
    state.selectedForecast = null;
  }
  map.selectMesh(code, meshBounds(code), options.pan === true);
  renderSelected();
  syncUrl();
  setNotice(`1kmメッシュ ${code} を選択しました`, "ok");
}

function renderSelected() {
  const record = state.selectedMesh;
  if (!record) {
    document.body.classList.remove("has-selection");
    elements.meshCode.textContent = "地図をクリック";
    elements.meshValue.textContent = "--";
    elements.meshPeriod.textContent = "1km気候平均を確認できます";
    elements.meshCoords.textContent = "海域には値を表示しません";
    elements.windowOldValue.textContent = "--";
    elements.windowNewValue.textContent = "--";
    elements.differenceValue.textContent = "--";
  } else {
    const oldValue = record.values["1991_2020"][state.month];
    const newValue = record.values["1996_2025"][state.month];
    const value = selectedValue(record);
    elements.meshCode.textContent = `1kmメッシュ ${record.meshCode}`;
    elements.meshValue.textContent = state.mode === "difference" ? signed(value) : temperature(value);
    elements.meshPeriod.textContent = climateSubtitle();
    elements.meshCoords.textContent = `中心 ${record.centerLat.toFixed(4)}°N, ${record.centerLon.toFixed(4)}°E｜独自算出・独自内挿`;
    elements.windowOldValue.textContent = temperature(oldValue);
    elements.windowNewValue.textContent = temperature(newValue);
    elements.differenceValue.textContent = signed(newValue - oldValue);
  }

  const term = store.seasonLatest ? store.forecastTerm(state.forecastProduct, state.forecastTerm) : null;
  if (state.selectedForecast) {
    const probabilities = state.selectedForecast.probabilities;
    elements.forecastRegion.textContent = `${state.regionName || state.regionCode}｜${state.selectedForecast.forecast_region_name}`;
    elements.forecastPeriod.textContent = periodLabel(term);
    elements.probabilityBelow.textContent = `${probabilities[0]}%`;
    elements.probabilityNormal.textContent = `${probabilities[1]}%`;
    elements.probabilityAbove.textContent = `${probabilities[2]}%`;
    elements.forecastNote.textContent = `${forecastLeadLabel(probabilities)}。この地点が属する予報地域の確率で、1km地点予報ではありません。`;
  } else {
    elements.forecastRegion.textContent = state.forecastVisible ? "地域レイヤ上で地点をクリック" : "季節予報レイヤはOFF";
    elements.forecastPeriod.textContent = periodLabel(term);
    elements.probabilityBelow.textContent = "--";
    elements.probabilityNormal.textContent = "--";
    elements.probabilityAbove.textContent = "--";
    elements.forecastNote.textContent = "気候平均と季節予報は異なる空間単位です。";
  }
}

let hoverTimer;
let hoverSequence = 0;
function scheduleHover(latlng) {
  clearTimeout(hoverTimer);
  if (!state.initialized || map.viewState().zoom < 7) {
    map.hideHover();
    return;
  }
  const sequence = ++hoverSequence;
  hoverTimer = setTimeout(async () => {
    const code = meshCodeFromLatLon(latlng.lat, latlng.lng);
    const record = code ? await store.meshRecord(code).catch(() => null) : null;
    if (sequence !== hoverSequence || !record) {
      map.hideHover();
      return;
    }
    const value = selectedValue(record);
    map.showHover(latlng, `<b>${state.mode === "difference" ? signed(value) : temperature(value)}</b><br>1kmメッシュ ${escapeHtml(code)}<br>${escapeHtml(monthLabel(state.month))}`);
  }, 130);
}

function clearHover() {
  clearTimeout(hoverTimer);
  hoverSequence += 1;
  map.hideHover();
}

function buildUrl() {
  const url = new URL(location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("element", "201");
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

function bindControls() {
  elements.settingsToggle.addEventListener("click", () => document.body.classList.toggle("settings-open"));
  elements.settingsClose.addEventListener("click", () => document.body.classList.remove("settings-open"));
  elements.detailClose.addEventListener("click", () => document.body.classList.add("detail-mobile-closed"));
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
    state.forecastTerm = 0;
    updateForecast();
  });
  elements.forecastTerm.addEventListener("change", () => {
    state.forecastTerm = Number(elements.forecastTerm.value);
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
      const config = state.mode === "difference"
        ? store.climateManifest.rasters.difference_legend
        : store.climateManifest.rasters.raw_legend;
      const detail = state.selectedMesh
        ? `${state.selectedMesh.meshCode}｜${state.mode === "difference" ? signed(selectedValue()) : temperature(selectedValue())}`
        : "地点未選択";
      const forecastDetail = state.selectedForecast
        ? `${state.regionName || state.regionCode}｜${state.selectedForecast.forecast_region_name}｜${forecastLeadLabel(state.selectedForecast.probabilities)}｜低い${state.selectedForecast.probabilities[0]}%・平年並${state.selectedForecast.probabilities[1]}%・高い${state.selectedForecast.probabilities[2]}%`
        : "季節予報地域: 地点未選択";
      const blob = await map.capture({
        subtitle: `${climateSubtitle()}｜${periodLabel(store.forecastTerm(state.forecastProduct, state.forecastTerm))}`,
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
      link.download = `climate-outlook-navi-201-${state.mode}-m${String(state.month).padStart(2, "0")}.png`;
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
    await store.initialize();
    populateMonths();
    const initialView = parseInitialState();
    applyControls();
    map.setBase(state.base);
    if (initialView.lat !== null && initialView.lon !== null) {
      map.setView(initialView.lat, initialView.lon, initialView.zoom || 5);
    }
    const pref = store.climateManifest.static?.prefectures;
    if (pref) await map.setBoundaries(`./data/climate/${pref.path}?v=${encodeURIComponent(store.climateManifest.dataset_id)}`);
    state.initialized = true;
    updateClimate();
    updateForecast();
    if (state.meshCode) {
      const bounds = meshBounds(state.meshCode);
      await selectAtLatLon(bounds.centerLat, bounds.centerLon);
      if (initialView.zoom) map.setView(bounds.centerLat, bounds.centerLon, initialView.zoom);
      else map.setView(bounds.centerLat, bounds.centerLon, 9);
    }
    elements.sourceStatus.title = "全国表示用ラスターは描画縮約。地点値は全387,717メッシュを保持した分割バイナリから参照します。";
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
