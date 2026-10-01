/* Hof Hans – frontend (plain JS + Leaflet) */
"use strict";

const state = {
  tab: "fields",
  fieldId: null,
  terminal: "John Deere G5", // default terminal until the selector lands
  section: 25,
};

let DATA = null;
let fieldById = {};
let map = null;
let layers = { all: null, current: null, raw: null };
let mapSeq = 0; // guards against out-of-order map responses
let rawMapSeq = 0;
let rawMapTimer = null;

const $ = (id) => document.getElementById(id);
const fmtHa = (n) => (n == null ? "–" : (Math.round(n * 10) / 10).toFixed(1));
const fmtNum = (n) => (n == null ? "–" : n.toLocaleString("en-US"));
const fmtDate = (iso) => {
  if (!iso) return "–";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
};
function mapNameForTerminal() {
  return DATA.terminals.find((t) => t.model === state.terminal).map;
}
function categoryOf(field, mapName) {
  const cat = DATA.maps[mapName].category;
  return (field.categories && field.categories[cat]) || {};
}

async function init() {
  DATA = await (await fetch("/api/fields")).json();
  DATA.fields.forEach((f) => (fieldById[f.id] = f));

  initMap();
  $("field-count").textContent = DATA.fields.length;
  buildFieldList();
  buildTerminalSelect();
  buildSectionSelect();
  buildRawList();
  drawAllBorders();
  bindUI();
  selectField(DATA.fields[0].id, { fit: true });
}

// small "N" + arrow; the map is never rotated, so a static arrow is enough
const NorthArrow = L.Control.extend({
  options: { position: "bottomleft" },
  onAdd() {
    const div = L.DomUtil.create("div", "north-arrow");
    div.innerHTML =
      '<span class="na-letter">N</span><span class="na-arrow">&#9650;</span>';
    L.DomEvent.disableClickPropagation(div);
    return div;
  },
});

// what the colors on the map mean
const MapLegend = L.Control.extend({
  options: { position: "bottomright" },
  onAdd() {
    const div = L.DomUtil.create("div", "map-legend");
    div.id = "map-legend";
    div.innerHTML =
      '<div class="lg-row"><span class="lg-line"></span>Field border</div>' +
      '<div class="lg-row"><span class="lg-fill"></span>Spray map</div>';
    L.DomEvent.disableClickPropagation(div);
    return div;
  },
});

function updateLegend(tab, minCount = 0, maxCount = 0) {
  const legend = $("map-legend");
  if (!legend) return;
  if (tab === "raw") {
    legend.innerHTML =
      '<div class="lg-row"><span class="lg-line"></span>Field border</div>' +
      '<div class="lg-density-title">Detections per cell</div>' +
      '<div class="lg-gradient"></div>' +
      '<div class="lg-scale-labels"><span id="lg-min">0</span>' +
      '<span id="lg-max">0</span></div>';
    $("lg-min").textContent = minCount.toLocaleString("en-US");
    $("lg-max").textContent = maxCount.toLocaleString("en-US");
    return;
  }
  if (tab === "raw-points") {
    legend.innerHTML =
      '<div class="lg-row"><span class="lg-line"></span>Field border</div>' +
      '<div class="lg-row"><span class="lg-point"></span>Individual detections</div>';
    return;
  }
  legend.innerHTML =
    '<div class="lg-row"><span class="lg-line"></span>Field border</div>' +
    '<div class="lg-row"><span class="lg-fill"></span>Spray map</div>';
}

function initMap() {
  map = L.map("map", { preferCanvas: true });
  map.createPane("rawPane").style.zIndex = 410;
  map.createPane("borderPane").style.zIndex = 650;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  map.addControl(new NorthArrow());
  L.control.scale({ position: "bottomleft" }).addTo(map);
  map.addControl(new MapLegend());
  layers.all = L.layerGroup().addTo(map);
  layers.current = L.layerGroup().addTo(map);
  layers.raw = L.layerGroup().addTo(map);
  map.on("click", onMapClick);
  map.on("moveend zoomend", scheduleRawMap);
}

function buildFieldList() {
  const ul = $("field-list");
  ul.innerHTML = "";
  for (const f of DATA.fields) {
    const li = document.createElement("li");
    li.dataset.id = f.id;
    const name = document.createElement("div");
    name.className = "fname";
    name.textContent = f.name;
    const meta = document.createElement("div");
    meta.className = "fmeta";
    meta.textContent =
      `${fmtHa(f.area_ha)} ha · ${f.plant_type} · ${fmtDate(f.flight_date)}`;
    li.append(name, meta);
    li.addEventListener("click", () => selectField(f.id, { fit: true }));
    ul.appendChild(li);
  }
  refreshList();
}

function refreshList() {
  for (const li of document.querySelectorAll("#field-list li")) {
    li.classList.toggle("active", li.dataset.id === state.fieldId);
  }
  const active = document.querySelector("#field-list li.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}

function buildTerminalSelect() {
  const sel = $("terminal");
  sel.innerHTML = "";
  for (const t of DATA.terminals) {
    const opt = document.createElement("option");
    opt.value = t.model;
    opt.textContent = t.model;
    sel.appendChild(opt);
  }
  sel.value = state.terminal;
}

function buildSectionSelect() {
  const sel = $("section");
  sel.innerHTML = "";
  for (const s of DATA.sections) {
    const opt = document.createElement("option");
    opt.value = s;
    opt.textContent = `${s} cm`;
    sel.appendChild(opt);
  }
  sel.value = state.section;
}

function updateResults() {
  const f = fieldById[state.fieldId];
  if (!f) return;
  const cat = categoryOf(f, mapNameForTerminal());
  const sav = cat[`Savings_${state.section}_cm`];
  const area = cat[`Sprayarea_${state.section}_cm`];

  $("res-savings").textContent = sav == null ? "–" : `${sav.toFixed(1)} %`;
  $("res-spray").textContent =
    area == null
      ? "sprays – of – ha"
      : `sprays ${area.toFixed(2)} of ${f.area_ha.toFixed(1)} ha`;
}

function onMapClick(e) {
  const id = e.target && e.target.options && e.target.options.fieldId;
  if (id && id !== state.fieldId) selectField(id, { fit: false });
}

// ---------------------------------------------------------------------------
// map drawing
// ---------------------------------------------------------------------------

const ALL_BORDER_STYLE = { color: "#6b7280", weight: 1.5, dashArray: "4 3", fill: false };
const SEL_BORDER_STYLE = { color: "#111827", weight: 3, fill: false };
const MAP_POLY_STYLE = {
  color: "#c2410c", weight: 1, fillColor: "#f97316", fillOpacity: 0.45,
};
const RAW_DENSITY_COLORS = [
  "#fee9c0", "#fed37e", "#da7945", "#A84D1D", "#8c2d04",
];

function rawDensityColor(intensity) {
  const value = Math.max(0, Math.min(1, intensity));
  const index = Math.round(value * (RAW_DENSITY_COLORS.length - 1));
  return RAW_DENSITY_COLORS[index];
}

function drawAllBorders() {
  layers.all.clearLayers();
  for (const f of DATA.fields) {
    if (!f.border) continue;
    L.geoJSON(f.border, {
      style: ALL_BORDER_STYLE,
      pane: "borderPane",
      onEachFeature: (_ft, lyr) => { lyr.options.fieldId = f.id; },
    }).addTo(layers.all);
  }
}

function selectField(id, { fit = false } = {}) {
  state.fieldId = id;
  refreshList();
  updateResults();
  drawCurrent({ fit });
}

async function drawCurrent({ fit = false } = {}) {
  const seq = ++mapSeq;
  const f = fieldById[state.fieldId];
  let res;
  try {
    res = await (await fetch(`/api/fields/${f.id}/map`)).json();
  } catch (err) {
    console.error("map fetch failed", err);
    return;
  }
  if (seq !== mapSeq || state.tab !== "fields") return; // stale or tab changed

  layers.current.clearLayers();
  const onEach = (_ft, lyr) => { lyr.options.fieldId = f.id; };
    for (const feat of res.fc.features) {
    if (feat.properties.kind === "border") {
      L.geoJSON(feat, {
        style: SEL_BORDER_STYLE,
        pane: "borderPane",
        onEachFeature: onEach,
      })
        .addTo(layers.current);
    } else {
      L.geoJSON(feat, { style: MAP_POLY_STYLE, onEachFeature: onEach })
        .addTo(layers.current);
    }
  }
  setMapNote(res);
  if (fit && f.border) {
    map.fitBounds(L.geoJSON(f.border).getBounds(), { padding: [20, 20] });
  }
}

function setMapNote(res) {
  const note = $("map-note");
  const text = res.kind === null
    ? `No map data for this field – showing the field border.`
    : "";
  note.textContent = text;
  note.hidden = !text;
}

// ---------------------------------------------------------------------------
// raw data tab (lightweight: field 19 border + info, no detection access)
// ---------------------------------------------------------------------------

function buildRawList() {
  const ul = $("raw-field-list");
  ul.innerHTML = "";
  let nRaw = 0;
  for (const f of DATA.fields) {
    const li = document.createElement("li");
    li.dataset.id = f.id;
    const name = document.createElement("div");
    name.className = "fname";
    name.textContent = f.name;
    const meta = document.createElement("div");
    meta.className = "fmeta";
    if (f.has_raw_data) {
      nRaw++;
      meta.textContent =
        `${fmtHa(f.area_ha)} ha · ${f.plant_type} · ${fmtDate(f.flight_date)}`;
      li.addEventListener("click", () => selectRawField(f.id));
    } else {
      li.classList.add("disabled");
      meta.textContent = "no raw data";
    }
    li.append(name, meta);
    ul.appendChild(li);
  }
  $("raw-count").textContent = nRaw;
}

function selectRawField(id) {
  const f = fieldById[id];
  state.fieldId = id;
  rawMapSeq++;
  document.querySelectorAll("#raw-field-list li").forEach((el) =>
    el.classList.toggle("active", el.dataset.id === id)
  );
  layers.current.clearLayers();
  layers.raw.clearLayers();
  if (f.border) {
    L.geoJSON(f.border, {
      style: SEL_BORDER_STYLE,
      pane: "borderPane",
    }).addTo(layers.current);
    map.fitBounds(L.geoJSON(f.border).getBounds(), { padding: [20, 20] });
  }
  const info = $("raw-info");
  info.innerHTML = "";
  const title = document.createElement("div");
  title.className = "raw-title";
  title.textContent = f.name;
  const meta = document.createElement("div");
  meta.className = "raw-meta";
  meta.textContent =
    `${fmtHa(f.area_ha)} ha · ${f.plant_type} · flight ${fmtDate(f.flight_date)}` +
    ` · ${fmtNum(f.plants_in_field)} detections`;
  info.append(title, meta);
  const note = $("map-note");
  note.textContent = "Loading detections...";
  note.hidden = false;
  scheduleRawMap();
}

function scheduleRawMap() {
  if (state.tab !== "raw" || !state.fieldId) return;
  clearTimeout(rawMapTimer);
  rawMapTimer = setTimeout(loadRawMap, 200);
}

async function loadRawMap() {
  if (state.tab !== "raw" || !state.fieldId) return;
  const bounds = map.getBounds();
  const bbox = [bounds.getWest(), bounds.getSouth(),
    bounds.getEast(), bounds.getNorth()].join(",");
  const seq = ++rawMapSeq;
  let res;
  try {
    const response = await fetch(
      `/api/raw/${state.fieldId}/detections?bbox=${bbox}&zoom=${map.getZoom()}`
    );
    if (!response.ok) throw new Error(`raw map request failed: ${response.status}`);
    res = await response.json();
  } catch (err) {
    console.error("raw map fetch failed", err);
    return;
  }
  if (seq !== rawMapSeq || state.tab !== "raw") return;

  layers.raw.clearLayers();
  if (res.mode === "grid") {
    const minCount = res.scale_min || 0;
    const maxCount = res.scale_max || 0;
    const countRange = maxCount - minCount;
    updateLegend("raw", minCount, maxCount);
    L.geoJSON(res.fc, {
      pane: "rawPane",
      style: (feature) => {
        const count = feature.properties.count || 1;
        const intensity = countRange === 0
          ? 1
          : (count - minCount) / countRange;
        return {
          color: rawDensityColor(intensity),
          weight: 0.3,
          opacity: 1,
          fillColor: rawDensityColor(intensity),
          fillOpacity: 1,
        };
      },
    }).addTo(layers.raw);
  } else {
    updateLegend("raw-points");
    L.geoJSON(res.fc, {
      pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
        radius: 3,
        color: "#7c2d12",
        weight: 1,
        fillColor: "#f97316",
        fillOpacity: 0.55,
        pane: "rawPane",
      }),
    }).addTo(layers.raw);
  }

  const note = $("map-note");
  note.textContent = res.mode === "grid"
    ? `Density grid representing ${res.total.toLocaleString("en-US")} detections in visible cells. Single detections appear as points at maximum zoom.`
    : `Showing ${res.shown.toLocaleString("en-US")} detections.`;
  note.hidden = false;
}

function setTab(tab) {
  if (state.tab === tab) return;
  state.tab = tab;
  updateLegend(tab);
  $("panel-fields").hidden = tab !== "fields";
  $("panel-raw").hidden = tab !== "raw";
  $("tab-fields").classList.toggle("active", tab === "fields");
  $("tab-raw").classList.toggle("active", tab === "raw");
  layers.all.clearLayers();
  if (tab === "fields") {
    layers.raw.clearLayers();
    drawAllBorders();
    drawCurrent({ fit: true });
  } else {
    const f = DATA.fields.find((x) => x.has_raw_data);
    if (f) selectRawField(f.id);
    else {
      layers.current.clearLayers();
      $("map-note").hidden = true;
    }
  }
  setTimeout(() => map.invalidateSize(), 0);
}

// ---------------------------------------------------------------------------
// ui
// ---------------------------------------------------------------------------

function bindUI() {
  $("list-toggle").addEventListener("click", () => {
    const collapsed = $("field-list").classList.toggle("collapsed");
    $("list-toggle").setAttribute("aria-expanded", String(!collapsed));
    $("list-icon").textContent = collapsed ? "+" : "−";
  });
  $("raw-list-toggle").addEventListener("click", () => {
    const collapsed = $("raw-field-list").classList.toggle("collapsed");
    $("raw-list-toggle").setAttribute("aria-expanded", String(!collapsed));
    $("raw-list-icon").textContent = collapsed ? "+" : "−";
  });
  $("terminal").addEventListener("change", (e) => {
    // only the results change; the displayed map is always the same
    state.terminal = e.target.value;
    refreshList();
    updateResults();
  });
  $("section").addEventListener("change", (e) => {
    state.section = Number(e.target.value);
    refreshList();
    updateResults();
  });
  $("tab-fields").addEventListener("click", () => setTab("fields"));
  $("tab-raw").addEventListener("click", () => setTab("raw"));
  $("download").addEventListener("click", () => {
    const mapName = mapNameForTerminal();
    location.href =
      `/api/fields/${state.fieldId}/download?map=${encodeURIComponent(mapName)}`;
  });
}

init();