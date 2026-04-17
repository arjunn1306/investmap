/* ── State ────────────────────────────────────────────────────────────────── */
let map;
let markers         = [];
let currentListings = [];
let activeIdx       = -1;
let selectedFile    = null;   // file from ZIP tab drop-zone
let selectedFileRaw = null;   // file from raw Upload tab

/* ── Map init ─────────────────────────────────────────────────────────────── */
function initMap() {
  map = L.map("map", { center: [39.5, -98.35], zoom: 4, zoomControl: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  }).addTo(map);
}

/* ── Tab switching ────────────────────────────────────────────────────────── */
function switchTab(tab) {
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.dataset.tab === tab)
  );
  document.getElementById("tab-zip").classList.toggle("hidden", tab !== "zip");
  document.getElementById("tab-upload").classList.toggle("hidden", tab !== "upload");
  clearError();
}

/* ── ZIP input → update Redfin link ──────────────────────────────────────── */
function onZipInput(val) {
  const link = document.getElementById("redfin-link");
  const zip  = val.trim();
  link.href = zip.length === 5 && /^\d{5}$/.test(zip)
    ? `https://www.redfin.com/zipcode/${zip}`
    : "https://www.redfin.com";
}

/* ── File selection (ZIP tab) ─────────────────────────────────────────────── */
function onFileSelected(input) {
  selectedFile = input.files[0] || null;
  const nameEl    = document.getElementById("file-name");
  const uploadBtn = document.getElementById("upload-btn");
  if (selectedFile) {
    nameEl.textContent = selectedFile.name;
    uploadBtn.disabled = false;
  } else {
    nameEl.textContent = "Click to select CSV";
    uploadBtn.disabled = true;
  }
}

/* ── File selection (raw Upload tab) ─────────────────────────────────────── */
function onFileSelectedUpload(input) {
  selectedFileRaw = input.files[0] || null;
  const nameEl    = document.getElementById("file-name-upload");
  const uploadBtn = document.getElementById("upload-btn-raw");
  if (selectedFileRaw) {
    nameEl.textContent = selectedFileRaw.name;
    uploadBtn.disabled = false;
  } else {
    nameEl.textContent = "";
    uploadBtn.disabled = true;
  }
}

/* ── Drag-and-drop (ZIP tab) ──────────────────────────────────────────────── */
function initDropZone() {
  const zone = document.getElementById("drop-zone");
  if (!zone) return;
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
  zone.addEventListener("drop", e => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith(".csv")) {
      selectedFile = file;
      document.getElementById("file-name").textContent = file.name;
      document.getElementById("upload-btn").disabled = false;
    } else {
      showError("Please drop a .csv file.");
    }
  });

  const zoneRaw = document.getElementById("drop-zone-upload");
  if (!zoneRaw) return;
  zoneRaw.addEventListener("dragover", e => { e.preventDefault(); zoneRaw.classList.add("drag-over"); });
  zoneRaw.addEventListener("dragleave", () => zoneRaw.classList.remove("drag-over"));
  zoneRaw.addEventListener("drop", e => {
    e.preventDefault();
    zoneRaw.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith(".csv")) {
      selectedFileRaw = file;
      document.getElementById("file-name-upload").textContent = file.name;
      document.getElementById("upload-btn-raw").disabled = false;
    } else {
      showError("Please drop a .csv file.");
    }
  });
}

/* ── Cap-rate helpers ─────────────────────────────────────────────────────── */
function capClass(cr) {
  if (cr >= 0.08) return "excellent";
  if (cr >= 0.06) return "good";
  if (cr >= 0.04) return "fair";
  return "poor";
}
function capColor(cr) {
  if (cr >= 0.08) return "#10b981";
  if (cr >= 0.06) return "#84cc16";
  if (cr >= 0.04) return "#f59e0b";
  return "#ef4444";
}

/* ── Formatters ───────────────────────────────────────────────────────────── */
const fmtPrice = v => v == null ? "N/A" : "$" + Math.round(v).toLocaleString("en-US");
const fmtPct   = v => v == null ? "N/A" : (v * 100).toFixed(1) + "%";
const fmtCF    = v => v == null ? "N/A" : (v >= 0 ? "+" : "") + "$" + Math.round(v).toLocaleString("en-US");
const fmtNum   = (v, d = 2) => v == null ? "N/A" : v.toFixed(d);

/* ── Marker icon ──────────────────────────────────────────────────────────── */
function markerIcon(cr, idx) {
  const c = capColor(cr);
  return L.divIcon({
    className: "",
    html: `<div style="background:${c};color:#fff;border-radius:50%;width:28px;height:28px;
        display:flex;align-items:center;justify-content:center;
        font-weight:700;font-size:11px;border:2px solid #fff;
        box-shadow:0 2px 8px rgba(0,0,0,.45)">${idx + 1}</div>`,
    iconSize: [28, 28], iconAnchor: [14, 14], popupAnchor: [0, -16],
  });
}

/* ── Popup HTML ───────────────────────────────────────────────────────────── */
function buildPopup(l) {
  const color = capColor(l.cap_rate || 0);
  const btn   = l.listing_url
    ? `<a class="redfin-popup-btn" href="${l.listing_url}" target="_blank" rel="noopener">View on Redfin &rarr;</a>`
    : "";
  return `<div class="popup-inner">
    <h3>${l.address}<br><small style="font-weight:400;color:#64748b">${l.city}, ${l.state}</small></h3>
    <div class="popup-grid">
      <div class="popup-cell"><div class="plabel">Price</div><div class="pval">${fmtPrice(l.price)}</div></div>
      <div class="popup-cell"><div class="plabel">Est. Rent/mo</div><div class="pval">${fmtPrice(l.est_rent)}</div></div>
      <div class="popup-cell"><div class="plabel">Cap Rate</div><div class="pval" style="color:${color}">${fmtPct(l.cap_rate)}</div></div>
      <div class="popup-cell"><div class="plabel">Cash-on-Cash</div><div class="pval">${fmtPct(l.cash_on_cash)}</div></div>
      <div class="popup-cell"><div class="plabel">Yr1 Cash Flow</div><div class="pval">${fmtCF(l.cashflow_year1)}</div></div>
      <div class="popup-cell"><div class="plabel">IRR</div><div class="pval">${fmtPct(l.irr)}</div></div>
      <div class="popup-cell"><div class="plabel">DSCR</div><div class="pval">${fmtNum(l.dscr)}</div></div>
      <div class="popup-cell"><div class="plabel">Cash Needed</div><div class="pval">${fmtPrice(l.total_cash_needed)}</div></div>
    </div>
    ${btn}
  </div>`;
}

/* ── Render cards ─────────────────────────────────────────────────────────── */
function renderCards(listings) {
  const container = document.getElementById("listings-list");
  container.innerHTML = "";

  if (!listings.length) {
    container.innerHTML =
      `<div id="no-results">No listings match your criteria.<br>Try lowering the minimum cap rate or CoC.</div>`;
    return;
  }

  listings.forEach((l, idx) => {
    const cc      = capClass(l.cap_rate || 0);
    const cfClass = (l.cashflow_year1 || 0) >= 0 ? "pos" : "neg";
    const cocClass = (l.cash_on_cash || 0) >= 0.05 ? "pos" : "";
    const noPin   = (!l.lat || !l.lon)
      ? ' <span style="font-size:.63rem;color:#f59e0b" title="Could not geocode">⚠</span>' : "";

    const card = document.createElement("div");
    card.className = "listing-card";
    card.id = `card-${idx}`;
    card.innerHTML = `
      <div class="card-top">
        <div class="card-addr">
          <div class="street">${idx + 1}. ${l.address}${noPin}</div>
          <div class="city-st">${l.city}, ${l.state} ${l.zipcode}</div>
        </div>
        <span class="cap-badge cap-${cc}">${fmtPct(l.cap_rate)}</span>
      </div>
      <div class="metrics-grid">
        <div class="metric-cell"><div class="mlabel">Price</div><div class="mval">${fmtPrice(l.price)}</div></div>
        <div class="metric-cell"><div class="mlabel">CoC Return</div><div class="mval ${cocClass}">${fmtPct(l.cash_on_cash)}</div></div>
        <div class="metric-cell"><div class="mlabel">Yr1 Cash Flow</div><div class="mval ${cfClass}">${fmtCF(l.cashflow_year1)}</div></div>
        <div class="metric-cell"><div class="mlabel">Est. Rent/mo</div><div class="mval">${fmtPrice(l.est_rent)}</div></div>
        <div class="metric-cell"><div class="mlabel">IRR</div><div class="mval">${fmtPct(l.irr)}</div></div>
        <div class="metric-cell"><div class="mlabel">DSCR</div><div class="mval">${fmtNum(l.dscr)}</div></div>
      </div>`;
    card.addEventListener("click", () => activateCard(idx));
    container.appendChild(card);
  });
}

/* ── Activate card/marker ─────────────────────────────────────────────────── */
function activateCard(idx) {
  document.getElementById(`card-${activeIdx}`)?.classList.remove("active");
  markers[activeIdx]?.closePopup();

  activeIdx = idx;
  const card = document.getElementById(`card-${idx}`);
  if (card) { card.classList.add("active"); card.scrollIntoView({ behavior: "smooth", block: "nearest" }); }

  const l = currentListings[idx];
  if (l?.lat && l?.lon) {
    map.flyTo([l.lat, l.lon], 15, { duration: 0.7 });
    setTimeout(() => markers[idx]?.openPopup(), 800);
  }
}

/* ── Render markers ───────────────────────────────────────────────────────── */
function renderMarkers(listings) {
  markers.forEach(m => m && map.removeLayer(m));
  markers = [];
  const bounds = [];

  listings.forEach((l, idx) => {
    if (!l.lat || !l.lon) { markers.push(null); return; }

    const marker = L.marker([l.lat, l.lon], { icon: markerIcon(l.cap_rate || 0, idx) });
    marker.bindPopup(buildPopup(l), { maxWidth: 300 });
    marker.on("click", () => {
      document.getElementById(`card-${activeIdx}`)?.classList.remove("active");
      activeIdx = idx;
      const card = document.getElementById(`card-${idx}`);
      if (card) { card.classList.add("active"); card.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
    });
    marker.addTo(map);
    markers.push(marker);
    bounds.push([l.lat, l.lon]);
  });

  if (bounds.length) map.fitBounds(bounds, { padding: [50, 50] });
}

/* ── Apply results to UI ──────────────────────────────────────────────────── */
function applyResults(data) {
  currentListings = data.listings;
  document.getElementById("results-count").textContent =
    `${data.total} matching / ${data.total_scraped} total`;
  document.getElementById("results-header").classList.remove("hidden");
  document.getElementById("map-placeholder").classList.add("hidden");

  // Collapse search form so listings fill the full panel
  document.getElementById("sidebar").classList.add("results-mode");

  renderCards(currentListings);
  renderMarkers(currentListings);
  if (data.zip_center && !currentListings.some(l => l.lat)) {
    map.flyTo([data.zip_center.lat, data.zip_center.lon], 12, { duration: 1 });
  }
}

/* ── Back to search ───────────────────────────────────────────────────────── */
function backToSearch() {
  document.getElementById("sidebar").classList.remove("results-mode");
}

/* ── Filter params (ZIP tab) ──────────────────────────────────────────────── */
function filterParams() {
  return {
    min_cap_rate: parseFloat(document.getElementById("min-cap-rate").value) / 100,
    min_coc:      parseFloat(document.getElementById("min-coc").value) / 100,
    max_price:    parseFloat(document.getElementById("max-price").value) || 0,
    down_pct:     parseFloat(document.getElementById("down-pct").value),
    rate:         parseFloat(document.getElementById("rate").value),
    rent_pct:     parseFloat(document.getElementById("rent-pct").value),
  };
}

/* ── Filter params (Upload tab) ───────────────────────────────────────────── */
function filterParamsUpload() {
  return {
    min_cap_rate: parseFloat(document.getElementById("min-cap-rate-u").value) / 100,
    min_coc:      parseFloat(document.getElementById("min-coc-u").value) / 100,
    max_price:    parseFloat(document.getElementById("max-price-u").value) || 0,
    down_pct:     parseFloat(document.getElementById("down-pct-u").value),
    rate:         parseFloat(document.getElementById("rate-u").value),
    rent_pct:     parseFloat(document.getElementById("rent-pct-u").value),
  };
}

/* ── Analyze (ZIP tab — uploads Redfin CSV for that ZIP) ──────────────────── */
async function analyzeUpload() {
  if (!selectedFile) { showError("Please select a CSV file first."); return; }

  resetResults();
  setLoading(true, "Analyzing listings…");

  const fd = new FormData();
  fd.append("file", selectedFile);
  const fp = filterParams();
  Object.entries(fp).forEach(([k, v]) => fd.append(k, v));

  try {
    const res  = await fetch("/api/upload", { method: "POST", body: fd });
    let data;
    try { data = await res.json(); }
    catch { setLoading(false); showError(`Server error (HTTP ${res.status}). Try again or refresh.`); return; }
    setLoading(false);
    if (!res.ok) { showError(data.error || "An unexpected error occurred."); return; }
    applyResults(data);
  } catch (e) {
    setLoading(false);
    showError("Network error: " + e.message);
  }
}

/* ── Analyze (raw Upload tab) ─────────────────────────────────────────────── */
async function analyzeUploadRaw() {
  if (!selectedFileRaw) { showError("Please select a CSV file first."); return; }

  resetResults();
  setLoading(true, "Analyzing CSV…");

  const fd = new FormData();
  fd.append("file", selectedFileRaw);
  const fp = filterParamsUpload();
  Object.entries(fp).forEach(([k, v]) => fd.append(k, v));

  try {
    const res  = await fetch("/api/upload", { method: "POST", body: fd });
    let data;
    try { data = await res.json(); }
    catch { setLoading(false); showError(`Server error (HTTP ${res.status}). Try again or refresh.`); return; }
    setLoading(false);
    if (!res.ok) { showError(data.error || "An unexpected error occurred."); return; }
    applyResults(data);
  } catch (e) {
    setLoading(false);
    showError("Network error: " + e.message);
  }
}

/* ── UI helpers ───────────────────────────────────────────────────────────── */
function setLoading(on, msg = "Loading…") {
  document.querySelectorAll(".primary-btn").forEach(b => b.disabled = on);
  document.getElementById("loading").classList.toggle("hidden", !on);
  document.getElementById("loading-msg").textContent = msg;
  if (!on) {
    const btn = document.getElementById("upload-btn");
    if (btn) btn.disabled = !selectedFile;
    const btnRaw = document.getElementById("upload-btn-raw");
    if (btnRaw) btnRaw.disabled = !selectedFileRaw;
  }
}

function showError(msg) {
  document.getElementById("error-text").textContent = msg;
  document.getElementById("error-box").classList.remove("hidden");
}
function clearError() { document.getElementById("error-box").classList.add("hidden"); }

function resetResults() {
  clearError();
  document.getElementById("results-header").classList.add("hidden");
  document.getElementById("listings-list").innerHTML = "";
  document.getElementById("sidebar").classList.remove("results-mode");
  activeIdx = -1;
}

/* ── Boot ─────────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initDropZone();
});
