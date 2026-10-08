/* PeCal Smart Forecast — frontend logic */
const $ = (s) => document.querySelector(s);
const API = "";

/* ---------------- clock ---------------- */
function tick() {
  const d = new Date();
  $("#clock").textContent = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
setInterval(tick, 10000); tick();

/* ---------------- tabs ---------------- */
window.__c1loaded = false;
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".pane").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("#" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab === "challenge2" && !window.__c2loaded) loadChallenge2();
    if (t.dataset.tab === "challenge1" && !window.__c1loaded) loadChallenge1();
  });
});

/* ---------------- helpers ---------------- */
async function get(url) {
  const r = await fetch(API + url);
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}
const fmt = (v) => v == null ? "—" : (typeof v === "number" ? v.toLocaleString("en-GB") : v);
const utilColor = (u) => {
  if (u == null) return "rgba(120,140,160,0.35)";
  if (u >= 100) return "#ef4444";
  if (u >= 85) return "#f59e0b";
  if (u < 60) return "#3b82f6";
  return "#10b981";
};
const LAB_COLORS = ["#3b82f6","#06b6d4","#8b5cf6","#10b981","#f59e0b",
  "#ef4444","#ec4899","#14b8a6","#6366f1","#84cc16","#f97316","#0ea5e9"];

Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.color = "#64788c";
Chart.defaults.borderColor = "rgba(15,40,80,0.07)";
Chart.defaults.animation.duration = 900;
Chart.defaults.animation.easing = "easeOutQuart";

/* threshold lines 85% / 100% */
const thresholdPlugin = {
  id: "thresholds",
  afterDraw(chart) {
    if (!chart.options.plugins.thresholds?.lines) return;
    const { ctx, chartArea, scales } = chart;
    [{ v: 85, c: "#f59e0b", t: "85% bottleneck" }, { v: 100, c: "#ef4444", t: "100%" }]
      .forEach(({ v, c, t }) => {
        const y = scales.y.getPixelForValue(v);
        if (y < chartArea.top || y > chartArea.bottom) return;
        ctx.save();
        ctx.strokeStyle = c; ctx.lineWidth = 1.4; ctx.setLineDash([6, 5]);
        ctx.beginPath(); ctx.moveTo(chartArea.left, y); ctx.lineTo(chartArea.right, y); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = c; ctx.font = "600 10px sans-serif";
        ctx.fillText(t, chartArea.left + 6, y - 4);
        ctx.restore();
      });
  },
};
Chart.register(thresholdPlugin);

/* ================= TASK 1 ================= */
let horizon = 6;
let fcChart = null, capChart = null;

async function loadChallenge1() {
  window.__c1loaded = true;
  try {
    const [kpi, fc, bn, heat] = await Promise.all([
      get(`/api/kpi/forecast?months=${horizon}`),
      get(`/api/forecast?months=${horizon}`),
      get(`/api/bottlenecks?months=${horizon}&limit=8`),
      get(`/api/heatmap?months=${horizon}`),
    ]);
    renderKpi(kpi);
    renderForecast(fc);
    renderBottlenecks(bn.items || []);
    renderHeat(heat);
  } catch (e) {
    console.error(e);
  }
}

function renderKpi(k) {
  $("#k-total").textContent = fmt(k.total_geraete);
  $("#k-dated").textContent = fmt(k.mit_datum);
  $("#k-est").textContent = fmt(k.geschaetzt);
  $("#k-over").textContent = fmt(k.overdue);
  $("#k-awral").textContent = fmt(k.awral_3m);
  $("#k-util").textContent = k.avg_util != null ? k.avg_util + "%" : "—";
}

function renderForecast(rows) {
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const labs = [...new Set(rows.map((r) => r.MESSRAUM))].sort();
  const map = {};
  rows.forEach((r) => (map[r.MESSRAUM + r.month] = r.util_pct));

  const datasets = labs.map((lab, i) => ({
    label: lab,
    data: months.map((m) => map[lab + m]),
    borderColor: LAB_COLORS[i % LAB_COLORS.length],
    backgroundColor: LAB_COLORS[i % LAB_COLORS.length] + "22",
    borderWidth: 2, tension: 0.35, spanGaps: true,
    pointRadius: 3, pointHoverRadius: 6,
  }));

  if (fcChart) fcChart.destroy();
  fcChart = new Chart($("#fc-chart"), {
    type: "line",
    data: { labels: months, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        thresholds: { lines: true },
        legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, padding: 12 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y}%` } },
      },
      scales: {
        y: { title: { display: true, text: "Load %" }, suggestedMin: 0,
             suggestedMax: 120, grid: { color: "rgba(15,40,80,0.05)" } },
        x: { grid: { display: false } },
      },
    },
  });

  /* capacity vs load — bars per month (sum over labs) */
  const capByMonth = {}, loadByMonth = {};
  rows.forEach((r) => {
    capByMonth[r.month] = (capByMonth[r.month] || 0) + (r.soll_h || 0);
    loadByMonth[r.month] = (loadByMonth[r.month] || 0) + (r.hours || 0);
  });
  if (capChart) capChart.destroy();
  capChart = new Chart($("#cap-chart"), {
    type: "bar",
    data: {
      labels: months,
      datasets: [
        { label: "Capacity (Soll, h)", data: months.map((m) => Math.round(capByMonth[m] || 0)),
          backgroundColor: "rgba(59,130,246,0.55)", borderRadius: 8 },
        { label: "Load (estimate, h)", data: months.map((m) => Math.round(loadByMonth[m] || 0)),
          backgroundColor: "rgba(6,182,212,0.75)", borderRadius: 8 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true } } },
      scales: { y: { beginAtZero: true, grid: { color: "rgba(15,40,80,0.05)" } },
                x: { grid: { display: false } } },
    },
  });
}

function renderBottlenecks(items) {
  const tb = $("#bn-table tbody");
  if (!items.length) {
    tb.innerHTML = `<tr><td colspan="6" class="empty">No bottlenecks within ${horizon} months 🎉</td></tr>`;
    return;
  }
  tb.innerHTML = items.map((r, i) => {
    const u = r.util_pct;
    const prio = r.priority === "high" ? "red" : "amber";
    return `<tr style="animation-delay:${i * 60}ms">
      <td><b>${r.MESSRAUM}</b></td>
      <td>${r.month}</td>
      <td><span class="util-bar"><span class="util-num" style="text-align:left;color:${utilColor(u)}">${u}%</span>
        <span class="util-track"><span class="util-fill" style="width:${Math.min(u, 100)}%;background:${utilColor(u)}"></span></span></span></td>
      <td>${r.shift_to ? `<span class="pill blue">→ ${r.shift_to}</span>` : `<span class="pill grey">—</span>`}</td>
      <td>${r.recommendation || "—"}</td>
      <td><span class="pill ${prio}">${r.priority || "—"}</span></td>
    </tr>`;
  }).join("");
}

function renderHeat(heat) {
  const el = $("#heat");
  const labs = [...new Set(heat.items.map((i) => i.MESSRAUM))].sort();
  const months = heat.months;
  const map = {};
  heat.items.forEach((i) => (map[i.MESSRAUM + i.month] = i.util_pct));
  let html = `<div class="heat-row" style="--cols:${months.length}">
    <span></span>${months.map((m) => `<span class="heat-head">${m.slice(2)}</span>`).join("")}</div>`;
  labs.forEach((lab) => {
    html += `<div class="heat-row" style="--cols:${months.length}"><span class="heat-lbl">${lab}</span>`;
    months.forEach((m, j) => {
      const u = map[lab + m];
      const c = utilColor(u);
      const dark = u == null || (u < 85 && u >= 60);
      html += `<span class="heat-cell" title="${lab} ${m}: ${u == null ? "no data" : u + "%"}"
        style="background:${c};color:${dark ? "#1c2b3a" : "#fff"};animation-delay:${j * 40}ms">
        ${u == null ? "·" : u}</span>`;
    });
    html += `</div>`;
  });
  el.innerHTML = html;
}

/* horizon segment control (3 / 6 / 16 months) */
$("#horizon-seg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  $("#horizon-seg").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
  b.classList.add("on");
  horizon = +b.dataset.m;
  loadChallenge1();
});

/* ================= TASK 2 ================= */
let rfmChart = null, churnChart = null, volChart = null;
let callPeriod = "day";
let todayRows = [];
const SEG_COLORS = { Champion: "#10b981", Loyal: "#3b82f6", "At Risk": "#f59e0b", Lost: "#ef4444" };

async function loadChallenge2() {
  window.__c2loaded = true;
  try {
    const [kpi, today, rfm, churn, vol, gaps] = await Promise.all([
      get("/api/kpi/customers"),
      get(`/api/sales/today?period=${callPeriod}&limit=8`),
      get("/api/sales/rfm?limit=400"),
      get("/api/sales/churn-trend?months=12"),
      get("/api/sales/forecast-volumes?months=6"),
      get("/api/sales/industry-gaps?limit=12"),
    ]);
    $("#c-active").textContent = fmt(kpi.active);
    $("#c-sleep").textContent = fmt(kpi.sleeping);
    $("#c-lost").textContent = fmt(kpi.lost);
    $("#c-total").textContent = fmt(kpi.total);
    $("#c-top").textContent = kpi.top_branche;
    renderToday(today.items || []);
    renderRfm(rfm.items || []);
    renderChurn(churn.items || []);
    renderVolumes(vol);
    renderGaps(gaps.items || []);
  } catch (e) {
    console.error(e);
  }
}

async function loadTodayList() {
  try {
    const r = await get(`/api/sales/today?period=${callPeriod}&limit=8`);
    renderToday(r.items || []);
  } catch (e) { console.error(e); }
}

function renderToday(items) {
  todayRows = items;
  const el = $("#today-list");
  if (!items.length) { el.innerHTML = `<div class="empty">No calls needed 🎉</div>`; return; }
  el.innerHTML = items.map((r, i) => `
    <div class="call-card" style="animation-delay:${i * 70}ms">
      <span class="rank">${i + 1}</span>
      <div class="call-info">
        <div class="call-name">K-${r.kunde} <span class="pill grey">${r.branche}</span></div>
        <div class="call-meta">${r.segment} · recency ${r.recency_tage} d · ${r.kalibrierungen} calibrations · risk ${r.churn_risk}%</div>
        <div class="call-why"><b>${r.reason}</b> — ${r.action}</div>
      </div>
      <div class="call-eur">${fmt(r.potential_eur)} €</div>
    </div>`).join("");
}

function renderRfm(items) {
  const groups = {};
  items.forEach((r) => {
    (groups[r.segment] = groups[r.segment] || []).push({ x: r.recency_tage, y: r.kalibrierungen });
  });
  const ds = Object.entries(groups).map(([seg, pts]) => ({
    label: seg, data: pts,
    backgroundColor: SEG_COLORS[seg] || "#94a3b8",
    borderColor: "rgba(255,255,255,0.9)", borderWidth: 1,
    pointRadius: 5, pointHoverRadius: 8,
  }));
  if (rfmChart) rfmChart.destroy();
  rfmChart = new Chart($("#rfm-chart"), {
    type: "scatter", data: { datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true, padding: 12 } },
        tooltip: { callbacks: { label: (c) => `Recency ${c.parsed.x} d · Freq ${c.parsed.y}` } },
      },
      scales: {
        x: { title: { display: true, text: "Recency (days since last calibration)" },
             grid: { color: "rgba(15,40,80,0.05)" } },
        y: { title: { display: true, text: "Frequency (calibrations)" }, beginAtZero: true,
             grid: { color: "rgba(15,40,80,0.05)" } },
      },
    },
  });
}

function renderChurn(items) {
  if (churnChart) churnChart.destroy();
  churnChart = new Chart($("#churn-chart"), {
    type: "line",
    data: {
      labels: items.map((i) => i.month),
      datasets: [{
        label: "Churned customers", data: items.map((i) => i.churned),
        borderColor: "#ef4444", backgroundColor: "rgba(239,68,68,0.12)",
        fill: true, tension: 0.35, borderWidth: 2.5,
        pointRadius: 4, pointHoverRadius: 7,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, grid: { color: "rgba(15,40,80,0.05)" } },
        x: { grid: { display: false } },
      },
    },
  });
}

function renderVolumes(vol) {
  const hist = vol.history || [], fut = vol.forecast || [];
  const labels = [...hist.map((h) => h.month), ...fut.map((f) => f.month)];
  const actual = [...hist.map((h) => h.actual), ...fut.map(() => null)];
  const forecast = [...hist.map(() => null), ...fut.map((f) => f.forecast)];
  if (volChart) volChart.destroy();
  volChart = new Chart($("#vol-chart"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Actual", data: actual, backgroundColor: "rgba(59,130,246,0.65)", borderRadius: 7 },
        { label: "Forecast", data: forecast, backgroundColor: "rgba(6,182,212,0.55)", borderRadius: 7 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true } } },
      scales: { y: { beginAtZero: true, grid: { color: "rgba(15,40,80,0.05)" } },
                x: { grid: { display: false } } },
    },
  });
}

function renderGaps(items) {
  const tb = $("#gap-table tbody");
  if (!items.length) { tb.innerHTML = `<tr><td colspan="5" class="empty">No data</td></tr>`; return; }
  tb.innerHTML = items.map((r, i) => `
    <tr style="animation-delay:${i * 50}ms">
      <td><b>${r.branche}</b></td>
      <td>${r.messmitteltyp}</td>
      <td><span class="util-bar"><span class="util-track">
        <span class="util-fill" style="width:${r.verbreitung_pct}%;background:linear-gradient(90deg,#3b82f6,#06b6d4)"></span>
      </span><span class="util-num">${r.verbreitung_pct}%</span></span></td>
      <td><span class="pill amber">${r.kunden_ohne}</span></td>
      <td>${(r.beispiel_kunden || []).map((k) => `K-${k}`).join(", ")}</td>
    </tr>`).join("");
}

/* period selector: day / week / month */
$("#period-seg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  $("#period-seg").querySelectorAll("button").forEach((x) => x.classList.remove("on"));
  b.classList.add("on");
  callPeriod = b.dataset.p;
  loadTodayList();
});

/* export full call list to Excel (SheetJS, offline) */
$("#export-btn").addEventListener("click", async () => {
  const btn = $("#export-btn");
  const old = btn.textContent;
  btn.textContent = "… Exporting";
  btn.disabled = true;
  try {
    const r = await get(`/api/sales/today?period=${callPeriod}&limit=5000`);
    const rows = (r.items || []).map((x, i) => ({
      "#": i + 1,
      "Customer": "K-" + x.kunde,
      "Industry": x.branche,
      "Segment": x.segment,
      "Recency (days)": x.recency_tage,
      "Calibrations (2y)": x.kalibrierungen,
      "Churn risk %": x.churn_risk,
      "Overdue devices": x.overdue_devices,
      "Due in period": x.due_devices,
      "Planned next 6 mo": x.geplante_kalibrierungen,
      "Potential EUR": x.potential_eur,
      "Score": x.score,
      "Reason": x.reason,
      "Action": x.action,
    }));
    const ws = XLSX.utils.json_to_sheet(rows);
    ws["!cols"] = [{ wch: 4 }, { wch: 12 }, { wch: 30 }, { wch: 10 }, { wch: 13 },
                   { wch: 16 }, { wch: 12 }, { wch: 15 }, { wch: 13 }, { wch: 17 },
                   { wch: 14 }, { wch: 8 }, { wch: 45 }, { wch: 40 }];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Call list");
    const today = new Date().toISOString().slice(0, 10);
    XLSX.writeFile(wb, `pecal-call-list-${callPeriod}-${today}.xlsx`);
  } catch (e) {
    console.error(e);
    alert("Export failed: " + e.message);
  } finally {
    btn.textContent = old;
    btn.disabled = false;
  }
});

/* ================= init ================= */
if (location.hash === "#challenge2") {
  document.querySelector('.tab[data-tab="challenge2"]').click();
} else {
  loadChallenge1();
}
