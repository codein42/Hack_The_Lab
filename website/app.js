/* Lab Planner – reads window.DATA from data/data.js (built by build_data.py). */
const D = window.DATA;
const TODAY = new Date(D.meta.today);
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n, d = 0) => (n == null || isNaN(n) ? "–" : Number(n).toLocaleString("de-DE", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (n) => (n == null ? "–" : fmt(n) + " %");
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const cls = (load) => (load == null ? "none" : load > 100 ? "bad" : load >= 85 ? "warn" : "ok");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthLabel = (iso) => MONTHS[+iso.slice(5, 7) - 1] + " " + iso.slice(0, 4);
const addDays = (iso, n) => { const d = new Date(iso); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const daysFromToday = (iso) => Math.round((new Date(iso) - TODAY) / 864e5);
const store = {
  get(k, def) { try { const v = localStorage.getItem("lp:" + k); return v == null ? def : JSON.parse(v); } catch { return def; } },
  set(k, v) { try { localStorage.setItem("lp:" + k, JSON.stringify(v)); } catch {} },
};
const TECH_WEEK_H = 38.5 * 0.8; // one technician, effective productive hours per week

/* ------------------------------------------------------------------ indexes */
const LABS = D.labs.slice().sort((a, b) => +a.id.slice(2) - +b.id.slice(2));
const LAB = Object.fromEntries(LABS.map((l) => [l.id, l]));
const WEEKS = [...new Set(D.roomWeek.map((r) => r.week))].sort();
const RW = {};
for (const r of D.roomWeek) (RW[r.room] ??= [])[WEEKS.indexOf(r.week)] = r;
const hasData = (room) => RW[room]?.some((r) => r.load != null);
const GROUP_ROOM = {};
for (const l of LABS) for (const g of l.groups) GROUP_ROOM[g] ??= l.id;
const CUST = Object.fromEntries(D.customers.map((c) => [c.customer, c]));
const REC_BY_CUST = {};
for (const r of D.recurring) (REC_BY_CUST[r.customer] ??= []).push(r);

// employees: vacation can be edited in the planner (kept in this browser)
const EMP = D.employees.map((e, i) => ({ ...e, id: i, vacation: store.get("vac:" + i, e.vacation) }));

/* ------------------------------------------------------------------ admin fixes (accepted suggestions) */
let FIXES = store.get("fixes", []);
function effective(room, i) {
  const r = RW[room]?.[i];
  if (!r || r.load == null) return null;
  let need = r.need_h, avail = r.avail_h;
  for (const f of FIXES) {
    if (f.room !== room) continue;
    if (f.type === "move") { if (f.from === i) need -= f.hours; if (f.to === i) need += f.hours; }
    else if (f.week === i) avail += f.hours;
  }
  return { ...r, need, avail, load: (need / avail) * 100, fixed: FIXES.some((f) => f.room === room && (f.from === i || f.to === i || f.week === i)) };
}

/* ------------------------------------------------------------------ tabs */
document.querySelectorAll("#nav button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll("#nav button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll("section.tab").forEach((s) => s.classList.toggle("on", s.id === b.dataset.tab));
    store.set("tab", b.dataset.tab);
  })
);
$("#asof").textContent = `Data as of ${D.meta.today} · built ${D.meta.built}`;

/* ------------------------------------------------------------------ heatmap */
function heatmap(el, loadFn, { onClick, selected } = {}) {
  let h = "<table><tr><th></th>";
  WEEKS.forEach((w, i) => (h += `<th class="wk">${i % 4 === 0 ? "KW" + (i + 1) : ""}</th>`));
  h += "</tr>";
  for (const l of LABS) {
    h += `<tr><th class="lab">${l.id} <span class="muted">${esc(l.name)}</span></th>`;
    WEEKS.forEach((w, i) => {
      const e = loadFn(l.id, i);
      const sel = selected && selected.room === l.id && selected.i === i ? " sel" : "";
      const tip = e ? `${l.id} KW${i + 1} (${w}): ${fmt(e.load)} % – need ${fmt(e.need)} h / available ${fmt(e.avail)} h` : `${l.id}: no capacity data`;
      h += `<td><div class="cell ${e ? cls(e.load) : "none"}${sel}${e?.fixed ? " fixed" : ""}" data-r="${l.id}" data-i="${i}" title="${tip}"></div></td>`;
    });
    h += "</tr>";
  }
  el.innerHTML = h + "</table>";
  if (onClick) el.querySelectorAll(".cell:not(.none)").forEach((c) => c.addEventListener("click", () => onClick(c.dataset.r, +c.dataset.i)));
}

/* ------------------------------------------------------------------ vacation helpers */
const onVacation = (emp, i) => emp.vacation.includes(WEEKS[i]);
const labEmps = (room) => EMP.filter((e) => e.room === room);
function qualifiedPresent(group, i) {
  return EMP.filter((e) => !onVacation(e, i) && e.quals.some((q) => q.group === group && q.expires >= WEEKS[i]));
}

/* ================================================================== DASHBOARD */
function renderDashboard() {
  const labWeeks = LABS.filter((l) => hasData(l.id)).flatMap((l) => WEEKS.map((_, i) => effective(l.id, i)).filter(Boolean));
  const red = labWeeks.filter((e) => e.load > 100).length;
  const atRisk = D.customers.filter((c) => c.churn_prob >= 0.5 && c.cal_12m >= 10);
  const soon = D.recurring.filter((r) => daysFromToday(r.next_date) <= 60);
  const late = D.recurring.filter((r) => daysFromToday(r.contact_by) < 0 && recStatus(r) === "not contacted");
  const kpi = (v, l, d = "") => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="d">${d}</div></div>`;
  $("#kpis").innerHTML =
    kpi(`${red}<span class="muted small"> / ${labWeeks.length}</span>`, "overloaded lab-weeks in 2027", `<span class="pill bad">over 100 %</span>`) +
    kpi(fmt(soon.length), "recurring orders in next 60 days", `${fmt(soon.reduce((s, r) => s + r.avg_n, 0))} calibrations expected`) +
    kpi(fmt(late.length), "recurring customers not yet contacted", `<span class="pill ${late.length ? "bad" : "ok"}">contact date passed</span>`) +
    kpi(fmt(atRisk.length), "customers likely to stop", `${fmt(atRisk.reduce((s, c) => s + c.volume_at_risk, 0))} calibrations at risk`) +
    kpi(fmt(D.meta.churn_auc, 2), "churn model AUC", `simple guess: ${fmt(D.meta.churn_auc_naive, 2)}`);

  // admin brief
  const q1 = LABS.filter((l) => hasData(l.id)).map((l) => ({ l, n: WEEKS.slice(0, 13).filter((_, i) => effective(l.id, i)?.load > 100).length })).sort((a, b) => b.n - a.n);
  const warns = vacationWarnings();
  const exp = EMP.flatMap((e) => e.quals.filter((q) => daysFromToday(q.expires) <= 60).map((q) => ({ e, q })));
  const li = (pill, txt) => `<li>${pill}<span>${txt}</span></li>`;
  $("#briefAdmin").innerHTML =
    li(`<span class="pill bad">capacity</span>`, `Q1 2027 overloaded weeks: ${q1.slice(0, 3).map((x) => `<b>${x.l.id}</b> ${x.n}/13`).join(", ")}`) +
    li(`<span class="pill warn">vacation</span>`, `${warns.length} vacation/skill warnings in 2027${warns[0] ? ` – first: ${esc(warns[0].text)}` : ""}`) +
    li(`<span class="pill warn">skills</span>`, `${exp.length} qualifications expire within 60 days`) +
    li(`<span class="pill info">equipment</span>`, `Plan maintenance in quiet weeks: ${maintWindows().slice(0, 3).map((m) => `${m.lab} KW${m.weeks[0].i + 1}`).join(", ")}`);

  // sales brief
  const wb = D.customers.filter((c) => c.action === "win-back").sort((a, b) => b.volume_at_risk - a.volume_at_risk).slice(0, 3);
  const od = D.customers.filter((c) => c.action === "remind: overdue");
  $("#briefSales").innerHTML =
    li(`<span class="pill bad">call now</span>`, `${late.length} recurring customers should already have been contacted (${fmt(late.reduce((s, r) => s + r.avg_n, 0))} calibrations)`) +
    li(`<span class="pill bad">win-back</span>`, `Top risk: ${wb.map((c) => `<b>${c.customer}</b> (${fmt(c.churn_prob * 100)} %)`).join(", ")}`) +
    li(`<span class="pill warn">overdue</span>`, `${fmt(od.length)} customers have ${fmt(od.reduce((s, c) => s + c.overdue_open, 0))} overdue instruments – send reminder + pickup offer`) +
    li(`<span class="pill info">cross-sell</span>`, `${D.customers.filter((c) => c.action === "cross-sell").length} customers calibrate fewer instrument types than their industry peers`);
  drawCharts();
}

const charts = {};
function drawCharts() {
  if (!window.Chart) { document.querySelectorAll("canvas").forEach((c) => (c.parentElement.innerHTML = '<p class="muted">Charts need internet (Chart.js from CDN).</p>')); return; }
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--line");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  const accent = css("--accent"), bad = css("--bad"), ok = css("--ok"), warn = css("--warn"), muted = css("--muted");

  // monthly forecast per segment
  const segs = [...new Set(D.monthly.map((r) => r.segment))].sort((a, b) => (a === "TOTAL" ? -1 : b === "TOTAL" ? 1 : a.localeCompare(b)));
  const sel = $("#segSel");
  if (!sel.options.length) { sel.innerHTML = segs.map((s) => `<option>${esc(s)}</option>`).join(""); sel.onchange = drawCharts; }
  const rows = D.monthly.filter((r) => r.segment === sel.value);
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const series = (kind, f = "calibrations") => months.map((m) => rows.find((r) => r.month === m && r.kind === kind)?.[f] ?? null);
  charts.m?.destroy();
  charts.m = new Chart($("#chMonthly"), {
    type: "line",
    data: { labels: months.map(monthLabel), datasets: [
      { label: "actual", data: series("actual"), borderColor: muted, pointRadius: 0, tension: 0.2 },
      { label: "backtest", data: series("backtest"), borderColor: warn, borderDash: [4, 4], pointRadius: 0 },
      { label: "range", data: series("forecast", "high"), borderColor: "transparent", backgroundColor: accent + "22", fill: "+1", pointRadius: 0 },
      { label: "low", data: series("forecast", "low"), borderColor: "transparent", pointRadius: 0 },
      { label: "forecast", data: series("forecast"), borderColor: accent, borderWidth: 2.5, pointRadius: 0 },
    ] },
    options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { filter: (i) => !["low", "range"].includes(i.text), boxWidth: 12 } } },
      scales: { y: { beginAtZero: true } } },
  });

  const ind = D.industry.filter((r) => r.branche !== "TOTAL").slice().sort((a, b) => b.volume_at_risk - a.volume_at_risk);
  charts.r?.destroy();
  charts.r = new Chart($("#chRisk"), {
    type: "bar",
    data: { labels: ind.map((r) => r.branche.length > 28 ? r.branche.slice(0, 26) + "…" : r.branche),
      datasets: [{ label: "calibrations at risk", data: ind.map((r) => r.volume_at_risk), backgroundColor: bad }] },
    options: { indexAxis: "y", maintainAspectRatio: false, plugins: { legend: { display: false },
      tooltip: { callbacks: { afterLabel: (c) => `${fmt(ind[c.dataIndex].customers_high_risk)} customers at high risk · ${fmt(ind[c.dataIndex].risk_share_pct * 100)} % of volume` } } } },
  });

  charts.d?.destroy();
  charts.d = new Chart($("#chDue"), {
    type: "bar",
    data: { labels: D.duePipeline.map((r) => monthLabel(r.month)), datasets: [
      { label: "expected back", data: D.duePipeline.map((r) => r.expected_back), backgroundColor: ok },
      { label: "due but at risk of not coming", data: D.duePipeline.map((r) => r.at_risk), backgroundColor: warn },
    ] },
    options: { maintainAspectRatio: false, scales: { x: { stacked: true }, y: { stacked: true } }, plugins: { legend: { labels: { boxWidth: 12 } } } },
  });

  charts.y?.destroy();
  charts.y = new Chart($("#chDaily"), {
    type: "line",
    data: { labels: D.daily.map((r) => r.datum), datasets: [
      { label: "load %", data: D.daily.map((r) => r.load_pct), borderColor: accent, pointRadius: 0, borderWidth: 1.5,
        segment: { borderColor: (c) => (c.p1.parsed.y > 100 ? bad : accent) } },
      { label: "100 %", data: D.daily.map(() => 100), borderColor: muted, borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
    ] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } },
      scales: { x: { ticks: { maxTicksLimit: 12, callback(v) { return monthLabel(this.getLabelForValue(v)); } } } } },
  });
}

/* ================================================================== ADMIN */
let selCell = null;
function renderAdmin() {
  heatmap($("#heatAdmin"), effective, { onClick: (room, i) => { selCell = { room, i }; renderAdmin(); }, selected: selCell });
  renderCellDetail();
  renderVacation();
  renderSkills();
  renderMaint();
  renderBatch();
}

function suggestFixes(room, i) {
  const e = effective(room, i);
  const excess = e.need - 0.85 * e.avail;
  const out = [];
  // 1) move flexible work to the quietest week within ±4 weeks
  let best = null;
  for (let j = Math.max(0, i - 4); j <= Math.min(WEEKS.length - 1, i + 4); j++) {
    if (j === i) continue;
    const o = effective(room, j);
    const spare = o ? 0.85 * o.avail - o.need : 0;
    if (spare > 1 && (!best || spare > best.spare)) best = { j, spare, o };
  }
  if (best) {
    const h = Math.min(excess, best.spare);
    const n = Math.round(h / (e.h_per_service || 1));
    out.push({ type: "move", from: i, to: best.j, hours: h,
      text: `Move flexible orders (~${fmt(h)} h, ~${fmt(n)} calibrations) to KW${best.j + 1}, which is at ${fmt(best.o.load)} %` });
  }
  // 2) borrow a cross-trained technician who is not on vacation and whose own lab has room
  const groups = LAB[room].groups;
  const helper = EMP.find((x) => x.room !== room && !onVacation(x, i) && x.quals.some((q) => groups.includes(q.group) && q.expires >= WEEKS[i]) && (effective(x.room, i)?.load ?? 0) < 85);
  if (helper) out.push({ type: "staff", week: i, hours: TECH_WEEK_H, text: `Borrow ${helper.name} from ${helper.room} (qualified for ${helper.quals.find((q) => groups.includes(q.group)).group}, +${fmt(TECH_WEEK_H)} h)` });
  // 3) overtime via working-time account (+10 %)
  out.push({ type: "overtime", week: i, hours: e.avail * 0.1, text: `Overtime via working-time account: +10 % (+${fmt(e.avail * 0.1)} h), paid back in a quiet week` });
  // 4) ask sales to pull recurring orders earlier
  const mo = +WEEKS[i].slice(5, 7);
  const rec = D.recurring.filter((r) => r.room === room && r.mo === mo);
  if (rec.length) out.push({ type: "sales", text: `Ask sales to call ${rec.length} recurring customers who send ${room} work in ${MONTHS[mo - 1]} to send 2–3 weeks earlier (${fmt(rec.reduce((s, r) => s + r.avg_n, 0))} calibrations)` });
  return { e, excess, out };
}

function renderCellDetail() {
  const el = $("#cellDetail");
  if (!selCell) return;
  const { room, i } = selCell;
  const { e, excess, out } = suggestFixes(room, i);
  const away = labEmps(room).filter((x) => onVacation(x, i));
  const my = FIXES.filter((f) => f.room === room && (f.from === i || f.week === i || f.to === i));
  let h = `<div class="row" style="justify-content:space-between"><h3 style="margin:0">${room} ${esc(LAB[room].name)} · KW${i + 1} (${WEEKS[i]})</h3>
    <span class="pill ${cls(e.load)}">${fmt(e.load)} % load</span></div>
    <p class="muted">Need ${fmt(e.need)} h · available ${fmt(e.avail)} h · ~${fmt(e.services)} calibrations ·
    on vacation (sample): ${away.length ? away.map((x) => esc(x.name)).join(", ") : "nobody"}</p>`;
  if (e.load < 85) h += `<p>No action needed. This week has ~${fmt(0.85 * e.avail - e.need)} h spare: a good week to pull work into or schedule maintenance.</p>`;
  else {
    h += `<p><b>${fmt(excess)} h</b> over the 85 % target. Suggested fixes:</p>`;
    out.forEach((f, k) => {
      h += `<div class="fix"><span>${esc(f.text)}</span>${f.type === "sales" ? `<span class="pill info">for sales</span>` : `<button class="btn s p" data-k="${k}">Accept</button>`}</div>`;
    });
  }
  if (my.length) h += `<p class="small muted" style="margin-top:10px">Accepted here: ${my.length} fix(es). <button class="btn s" id="undoFix">Undo</button></p>`;
  el.innerHTML = h;
  el.querySelectorAll("button[data-k]").forEach((b) => b.addEventListener("click", () => {
    FIXES.push({ room, ...out[+b.dataset.k] }); store.set("fixes", FIXES); renderAdmin(); renderDashboard();
  }));
  $("#undoFix")?.addEventListener("click", () => {
    FIXES = FIXES.filter((f) => !(f.room === room && (f.from === i || f.week === i || f.to === i))); store.set("fixes", FIXES); renderAdmin(); renderDashboard();
  });
}

function vacationWarnings() {
  const out = [];
  for (const l of LABS) {
    const emps = labEmps(l.id);
    WEEKS.forEach((w, i) => {
      const e = effective(l.id, i);
      const busy = e && e.load >= 85;
      const away = emps.filter((x) => onVacation(x, i));
      if (busy && away.length && (away.length >= 2 || away.length / emps.length >= 0.4))
        out.push({ i, room: l.id, kind: "vacation", emp: away[0], text: `KW${i + 1} ${l.id}: ${away.length} of ${emps.length} on vacation while load is ${fmt(e.load)} %` });
      if (busy) for (const g of l.groups.slice(0, 3)) if (!qualifiedPresent(g, i).length)
        out.push({ i, room: l.id, kind: "skill", text: `KW${i + 1} ${l.id}: nobody qualified for ${g} is in` });
    });
  }
  return out.sort((a, b) => a.i - b.i);
}

function quietWeekFor(emp, i) {
  let best = null;
  for (let j = Math.max(0, i - 6); j <= Math.min(WEEKS.length - 1, i + 6); j++) {
    if (onVacation(emp, j)) continue;
    const e = effective(emp.room, j);
    if (e && e.load < 85 && (!best || e.load < best.load)) best = { j, load: e.load };
  }
  return best;
}

function renderVacation() {
  const sel = $("#vacLab");
  if (!sel.options.length) {
    sel.innerHTML = LABS.map((l) => `<option value="${l.id}">${l.id} ${esc(l.name)} (${labEmps(l.id).length} people)</option>`).join("");
    sel.value = store.get("vacLab", "MR7");
    sel.onchange = () => { store.set("vacLab", sel.value); renderVacation(); renderSkills(); };
  }
  const room = sel.value, emps = labEmps(room);
  let h = `<table><tr><th></th>${WEEKS.map((_, i) => `<th class="small muted" style="font-weight:400">${i % 4 === 0 ? i + 1 : ""}</th>`).join("")}</tr>`;
  for (const e of emps) h += `<tr><th class="small" style="white-space:nowrap">${esc(e.name)}</th>${WEEKS.map((w, i) => `<td class="v ${onVacation(e, i) ? "off" : "on"}" title="${esc(e.name)} KW${i + 1}${onVacation(e, i) ? " vacation" : ""}"></td>`).join("")}</tr>`;
  h += `<tr><th class="small muted">in office</th>${WEEKS.map((_, i) => `<td class="small muted" style="text-align:center;font-size:10px">${emps.filter((e) => !onVacation(e, i)).length}</td>`).join("")}</tr>`;
  h += `<tr><th class="small muted">lab load</th>${WEEKS.map((_, i) => { const e = effective(room, i); return `<td><div class="cell ${e ? cls(e.load) : "none"}" style="width:14px;height:12px;cursor:default" title="${e ? fmt(e.load) + " %" : "no data"}"></div></td>`; }).join("")}</tr>`;
  $("#vacGrid").innerHTML = h + "</table>";

  const warns = vacationWarnings();
  $("#vacWarn").innerHTML = warns.length ? warns.slice(0, 80).map((w, k) => {
    let act = "";
    if (w.kind === "vacation") {
      const q = quietWeekFor(w.emp, w.i);
      act = q ? `<button class="btn s" data-k="${k}" data-to="${q.j}">Move ${esc(w.emp.name)} to KW${q.j + 1}</button>` : `<span class="muted small">no quiet week nearby – plan overtime</span>`;
    } else act = `<span class="muted small">cross-train or pre-book work earlier</span>`;
    return `<li><span class="pill ${w.kind === "vacation" ? "warn" : "bad"}">${w.kind}</span><span style="flex:1">${esc(w.text)}</span>${act}</li>`;
  }).join("") : `<li class="muted">No conflicts – all busy weeks have enough people.</li>`;
  $("#vacWarn").querySelectorAll("button[data-k]").forEach((b) => b.addEventListener("click", () => {
    const w = warns[+b.dataset.k], e = w.emp;
    e.vacation = e.vacation.filter((x) => x !== WEEKS[w.i]).concat(WEEKS[+b.dataset.to]).sort();
    store.set("vac:" + e.id, e.vacation);
    renderAdmin(); renderDashboard();
  }));
}

function renderSkills() {
  const room = $("#vacLab").value, groups = LAB[room].groups;
  const people = EMP.filter((e) => e.room === room || e.quals.some((q) => groups.includes(q.group)));
  let h = `<table><tr><th>Person</th><th>Home lab</th>${groups.map((g) => `<th>${esc(g)}</th>`).join("")}</tr>`;
  for (const e of people) {
    h += `<tr><td>${esc(e.name)}</td><td>${e.room}</td>${groups.map((g) => {
      const q = e.quals.find((x) => x.group === g);
      if (!q) return `<td class="muted">–</td>`;
      const d = daysFromToday(q.expires);
      return `<td><span class="pill ${d < 0 ? "bad" : d < 120 ? "warn" : q.cross ? "info" : "ok"}">${q.cross ? "cross " : ""}✓</span> <span class="small muted">${q.expires.slice(0, 7)}</span></td>`;
    }).join("")}</tr>`;
  }
  const single = groups.filter((g) => people.filter((e) => e.quals.some((q) => q.group === g)).length <= 1);
  $("#skills").innerHTML = h + "</table>" + (single.length ? `<div class="alert warn small">Only one qualified person for: <b>${single.map(esc).join(", ")}</b> – cross-train a second person.</div>` : "");
  const exp = EMP.flatMap((e) => e.quals.map((q) => ({ e, q, d: daysFromToday(q.expires) }))).filter((x) => x.d <= 120).sort((a, b) => a.d - b.d);
  $("#expiring").innerHTML = exp.length ? `<table><tr><th>Person</th><th>Lab</th><th>Qualification</th><th>Expires</th><th>Plan</th></tr>${exp.map(({ e, q, d }) => {
    const qi = maintWindows().find((m) => m.lab === e.room)?.weeks[0];
    return `<tr><td>${esc(e.name)}</td><td>${e.room}</td><td>${esc(q.group)}${q.cross ? ' <span class="pill info">cross</span>' : ""}</td><td><span class="pill ${d < 30 ? "bad" : "warn"}">${q.expires}</span></td><td class="small">${qi ? `retrain in KW${qi.i + 1} (quiet)` : "–"}</td></tr>`;
  }).join("")}</table>` : `<p class="muted">Nothing expires soon.</p>`;
}

function maintWindows() {
  return LABS.filter((l) => hasData(l.id)).map((l) => ({
    lab: l.id, name: l.name,
    weeks: WEEKS.map((_, i) => ({ i, e: effective(l.id, i) })).sort((a, b) => a.e.load - b.e.load).slice(0, 3),
  }));
}
function renderMaint() {
  $("#maint").innerHTML = `<table><tr><th>Lab</th><th>Quietest weeks</th><th>Avg load</th></tr>${maintWindows().map((m) => {
    const avg = WEEKS.reduce((s, _, i) => s + effective(m.lab, i).load, 0) / WEEKS.length;
    return `<tr><td><b>${m.lab}</b> ${esc(m.name)}</td><td>${m.weeks.map((w) => `<span class="pill ${cls(w.e.load)}">KW${w.i + 1} · ${fmt(w.e.load)} %</span>`).join(" ")}</td><td class="n">${pct(avg)}</td></tr>`;
  }).join("")}</table><p class="small muted">MR12 and MR14 have no capacity data in the forecast yet.</p>`;
}
function renderBatch() {
  const top = D.customers.filter((c) => c.due_next_3m + c.overdue_open >= 50).sort((a, b) => b.due_next_3m + b.overdue_open - (a.due_next_3m + a.overdue_open)).slice(0, 25);
  $("#batch").innerHTML = `<table><tr><th>Customer</th><th>Industry</th><th class="n">Due 3 m</th><th class="n">Overdue</th><th>Main lab</th></tr>${top.map((c) =>
    `<tr><td><b>${c.customer}</b></td><td class="small">${esc(c.branche)}</td><td class="n">${fmt(c.due_next_3m)}</td><td class="n">${fmt(c.overdue_open)}</td><td>${c.room ?? "–"}</td></tr>`).join("")}</table>`;
}

/* ================================================================== SALES */
const recKey = (r) => `rec:${r.customer}:${r.mo}`;
const recStatus = (r) => store.get(recKey(r), "not contacted");
function labLoadInMonth(room, mo) {
  const xs = WEEKS.map((w, i) => (+w.slice(5, 7) === mo ? effective(room, i) : null)).filter(Boolean);
  return xs.length ? xs.reduce((s, e) => s + e.load, 0) / xs.length : null;
}

function initSales() {
  const months = [...new Set(D.recurring.map((r) => r.next_date.slice(0, 7)))].sort();
  $("#recMonth").innerHTML = `<option value="">All months</option>` + months.map((m) => `<option value="${m}">${monthLabel(m + "-01")}</option>`).join("");
  ["#recQ", "#recMonth", "#recKind", "#recStatus"].forEach((s) => $(s).addEventListener("input", renderRecurring));
  const acts = [...new Set(D.customers.map((c) => c.action))];
  $("#riskAction").innerHTML = `<option value="">All actions</option>` + acts.map((a) => `<option ${a === "win-back" ? "selected" : ""}>${a}</option>`).join("");
  $("#riskInd").innerHTML = `<option value="">All industries</option>` + [...new Set(D.customers.map((c) => c.branche))].sort().map((b) => `<option>${esc(b)}</option>`).join("");
  ["#riskQ", "#riskAction", "#riskInd", "#riskContract"].forEach((s) => $(s).addEventListener("input", renderRisk));
  $("#aucTxt").textContent = fmt(D.meta.churn_auc, 2) + " vs " + fmt(D.meta.churn_auc_naive, 2) + " simple guess";

  // quote form
  const groups = Object.keys(GROUP_ROOM);
  $("#qGroup").innerHTML = groups.map((g) => `<option value="${esc(g)}">${esc(g)} (${GROUP_ROOM[g]})</option>`).join("");
  $("#qGroup").value = "Gewindelehrdorn";
  $("#qWeek").innerHTML = WEEKS.map((w, i) => `<option value="${i}">KW${i + 1} · ${w}</option>`).join("");
  $("#qWeek").value = "9";
  $("#custList").innerHTML = D.customers.slice().sort((a, b) => b.cal_12m - a.cal_12m).slice(0, 1500).map((c) => `<option value="${c.customer}">${esc(c.branche)}</option>`).join("");
  $("#qCust").value = D.customers.filter((c) => c.action === "win-back").sort((a, b) => b.volume_at_risk - a.volume_at_risk)[0]?.customer ?? "";
  ["#qCust", "#qGroup", "#qQty", "#qWeek", "#qLead"].forEach((s) => $(s).addEventListener("input", renderQuote));
}

function renderRecurring() {
  const q = $("#recQ").value.toLowerCase(), m = $("#recMonth").value, k = $("#recKind").value, st = $("#recStatus").value;
  const rows = D.recurring.filter((r) => (!m || r.next_date.startsWith(m)) && (!k || r.kind === k) && (!st || recStatus(r) === st) &&
    (!q || `${r.customer} ${r.branche} ${r.grp}`.toLowerCase().includes(q)));
  $("#recCount").textContent = `${rows.length} orders · ${fmt(rows.reduce((s, r) => s + r.avg_n, 0))} calibrations`;
  const show = rows.slice(0, 300);
  $("#recTable").innerHTML = `<tr><th>Expected</th><th>Customer</th><th>Pattern</th><th>History</th><th class="n">Exp. volume</th><th>Lab · load</th><th>Contact by</th><th>Risk</th><th>Status</th><th></th></tr>` +
    show.map((r, n) => {
      const d = daysFromToday(r.contact_by);
      const load = r.room ? labLoadInMonth(r.room, r.mo) : null;
      const st = recStatus(r);
      return `<tr>
        <td><b>${monthLabel(r.next_date)}</b></td>
        <td><b>${r.customer}</b><div class="small muted">${esc(r.branche ?? "")}</div></td>
        <td><span class="pill ${r.kind === "seasonal peak" ? "warn" : "info"}">${r.kind}</span><div class="small muted">${esc(r.grp ?? "")}</div></td>
        <td class="small">${r.years.map((y, i) => `${y}: <b>${r.counts[i]}</b>`).join(" · ")}</td>
        <td class="n">${fmt(r.avg_n)}</td>
        <td>${r.room ?? "–"} ${load != null ? `<span class="pill ${cls(load)}">${fmt(load)} %</span>` : ""}</td>
        <td>${st !== "not contacted" ? `<span class="small muted">${r.contact_by}</span>` : d < 0 ? `<span class="pill bad">late – call now</span>` : d <= 14 ? `<span class="pill warn">${r.contact_by}</span>` : `<span class="small">${r.contact_by}</span>`}</td>
        <td>${r.churn_prob >= 0.3 ? `<span class="pill bad">${fmt(r.churn_prob * 100)} %</span>` : `<span class="muted small">low</span>`}</td>
        <td><select data-n="${n}" class="recSt">${["not contacted", "contacted", "confirmed", "booked"].map((s) => `<option ${s === st ? "selected" : ""}>${s}</option>`).join("")}</select></td>
        <td><button class="btn s" data-mail="${n}">Email</button></td></tr>`;
    }).join("") + (rows.length > show.length ? `<tr><td colspan="10" class="muted small">Showing first ${show.length} – use the filters.</td></tr>` : "");
  $("#recTable").querySelectorAll(".recSt").forEach((s) => s.addEventListener("change", () => { store.set(recKey(show[+s.dataset.n]), s.value); renderDashboard(); }));
  $("#recTable").querySelectorAll("[data-mail]").forEach((b) => b.addEventListener("click", () => mailRecurring(show[+b.dataset.mail])));
}

const isContract = (c) => c.cal_12m >= 100 && ((c.churn_prob >= 0.2 && c.churn_prob <= 0.8) || REC_BY_CUST[c.customer]);
function nextStep(c) {
  const steps = {
    "win-back": "Call this week: ask what changed, offer a priority slot",
    "remind: overdue": `Send overdue list (${fmt(c.overdue_open)}) + free pickup offer`,
    "remind: due soon": `Remind about ${fmt(c.due_next_3m)} instruments due, pre-book a slot`,
    "cross-sell": `Offer calibration of ${c.top_missing_group ?? "missing groups"} (peers send these)`,
    monitor: "No action – keep watching",
  };
  return steps[c.action] ?? "–";
}
function renderRisk() {
  const q = $("#riskQ").value.toLowerCase(), a = $("#riskAction").value, b = $("#riskInd").value, onlyC = $("#riskContract").checked;
  const rows = D.customers.filter((c) => (!a || c.action === a) && (!b || c.branche === b) && (!onlyC || isContract(c)) &&
    (!q || `${c.customer} ${c.branche}`.toLowerCase().includes(q))).sort((x, y) => y.volume_at_risk - x.volume_at_risk).slice(0, 200);
  $("#riskTable").innerHTML = `<tr><th>Customer</th><th>Action</th><th class="n">Churn</th><th class="n">At risk</th><th class="n">Cal. 12 m</th><th>Why</th><th>Next step</th><th></th></tr>` +
    rows.map((c, n) => `<tr>
      <td><b>${c.customer}</b><div class="small muted">${esc(c.branche)}</div></td>
      <td><span class="pill ${c.action === "win-back" ? "bad" : c.action.startsWith("remind") ? "warn" : c.action === "cross-sell" ? "info" : "mute"}">${c.action}</span>
        ${isContract(c) ? `<div><span class="pill ok" title="Offer a yearly plan: reserved slots, fixed price">contract offer</span></div>` : ""}</td>
      <td class="n">${fmt(c.churn_prob * 100)} %</td><td class="n">${fmt(c.volume_at_risk)}</td><td class="n">${fmt(c.cal_12m)}</td>
      <td class="small">${esc(c.why)}</td><td class="small">${esc(nextStep(c))}${isContract(c) ? "; offer annual contract" : ""}</td>
      <td><button class="btn s" data-mail="${n}">Email</button></td></tr>`).join("");
  $("#riskTable").querySelectorAll("[data-mail]").forEach((x) => x.addEventListener("click", () => mailCustomer(rows[+x.dataset.mail])));
}

function quoteOptions(room, i, qty) {
  const rw = RW[room]?.[i];
  if (!rw || rw.load == null) return null;
  const hrs = qty * rw.h_per_service;
  const loadWith = (j) => { const e = effective(room, j); return e ? ((e.need + hrs) / e.avail) * 100 : null; };
  const L = loadWith(i);
  const L2 = Math.max(L, loadWith(Math.min(i + 1, WEEKS.length - 1)) ?? L);
  let q = { j: i, load: L };
  for (let j = i; j <= Math.min(WEEKS.length - 1, i + 5); j++) { const l = loadWith(j); if (l != null && l < q.load) q = { j, load: l }; }
  const exp = 15 + (L > 100 ? 15 : L >= 85 ? 5 : 0);
  const std = L2 > 100 ? 5 : 0;
  const flex = q.load < 70 ? -8 : q.load < 85 ? -5 : 0;
  return { hrs, opts: [
    { key: "5", name: "Express", days: 5, adj: exp, load: L, back: addDays(WEEKS[i], 5), note: L > 100 ? "week is already full – surcharge covers overtime" : "jumps the queue" },
    { key: "16", name: "Standard", days: 16, adj: std, load: L2, back: addDays(WEEKS[i], 16), note: std ? "peak-week surcharge" : "normal queue" },
    { key: "30", name: "Flexible", days: 30, adj: flex, load: q.load, back: [addDays(WEEKS[i], 30), addDays(WEEKS[q.j], 7)].sort()[1], note: `scheduled in quieter KW${q.j + 1}` },
  ] };
}
function renderQuote() {
  const c = CUST[$("#qCust").value.trim()], g = $("#qGroup").value, room = GROUP_ROOM[g], i = +$("#qWeek").value, qty = Math.max(1, +$("#qQty").value || 1), lead = $("#qLead").value;
  let w = "";
  if (!c) w += `<div class="alert info small">Unknown customer – priced without customer history.</div>`;
  else {
    if (c.churn_prob >= 0.4) w += `<div class="alert bad"><b>At-risk customer</b> (${fmt(c.churn_prob * 100)} % churn, ${fmt(c.cal_12m)} calibrations/yr): ${esc(c.why)}. Consider waiving the surcharge or giving a priority slot.</div>`;
    for (const r of (REC_BY_CUST[c.customer] ?? []).slice(0, 2)) w += `<div class="alert info small">Usually sends ~${fmt(r.avg_n)} instruments (${esc(r.grp)}) in <b>${MONTHS[r.mo - 1]}</b>. Offer to bundle or pre-book.</div>`;
    if (isContract(c)) w += `<div class="alert warn small">Good candidate for an <b>annual contract</b>: reserved slots, fixed price.</div>`;
  }
  $("#qWarn").innerHTML = w;
  const res = quoteOptions(room, i, qty);
  if (!res) { $("#qOut").innerHTML = `<h3>${room}</h3><p class="muted">No capacity data for ${room} in the forecast – standard list price.</p>`; return; }
  const atRisk = c && c.churn_prob >= 0.4;
  const shown = (o) => (atRisk && o.adj > 0 ? 0 : o.adj);
  const prio = lead === "5" || atRisk ? ["High", "bad"] : lead === "30" ? ["Low", "ok"] : ["Normal", "info"];
  $("#qOut").innerHTML = `<div class="row" style="justify-content:space-between"><h3 style="margin:0">${room} ${esc(LAB[room].name)} · ${fmt(qty)} × ${esc(g)}</h3>
      <span class="pill ${prio[1]}">Priority: ${prio[0]}</span></div>
    <p class="muted small">≈ ${fmt(res.hrs, 1)} lab hours. Arrival KW${i + 1}.</p>
    <div class="opts">${res.opts.map((o) => {
      const adj = atRisk && o.adj > 0 ? 0 : o.adj;
      return `<div class="opt ${o.key === lead ? "best" : ""}">
        <div class="small muted">${o.name} · back by ${o.back}</div>
        <div class="price">${100 + adj} %</div>
        <div class="small">${adj > 0 ? `+${adj} % surcharge` : adj < 0 ? `${adj} % discount` : "list price"}${atRisk && o.adj > 0 ? " (waived: at-risk)" : ""}</div>
        <div class="small muted">lab load ${fmt(o.load)} % – ${o.note}</div></div>`;
    }).join("")}</div>
    ${lead === "5" && res.opts[0].load > 100 && shown(res.opts[2]) < shown(res.opts[0]) ? `<div class="alert warn small">Tip: offer Flexible instead – ${shown(res.opts[0]) - shown(res.opts[2])} % cheaper for the customer and the work moves out of a full week in ${room}.</div>` : ""}
    <div class="row" style="margin-top:12px"><button class="btn p" id="qMail">Draft quote email</button></div>`;
  $("#qMail").onclick = () => {
    const o = res.opts.find((x) => x.key === lead);
    const adj = atRisk && o.adj > 0 ? 0 : o.adj;
    showMail(`Quote for customer ${c?.customer ?? ""}`,
`Subject: Your calibration order – ${fmt(qty)} × ${g}

Dear customer${c ? " " + c.customer : ""},

thank you for your request. For ${fmt(qty)} × ${g} arriving in KW${i + 1}:

  Option ${o.name}: back by ${o.back}, ${100 + adj} % of list price${adj < 0 ? ` (${-adj} % discount for a flexible date)` : ""}
${res.opts.filter((x) => x.key !== lead).map((x) => `  Alternative ${x.name}: back by ${x.back}, ${100 + (atRisk && x.adj > 0 ? 0 : x.adj)} % of list price`).join("\n")}

We can reserve the slot as soon as you confirm.

Best regards
Your Perschmann Calibration team`);
  };
}

/* ------------------------------------------------------------------ email drafts */
function showMail(title, text) { $("#dlgTitle").textContent = title; $("#dlgText").value = text; $("#dlg").showModal(); }
$("#dlgClose").onclick = () => $("#dlg").close();
$("#dlgCopy").onclick = () => { navigator.clipboard?.writeText($("#dlgText").value); $("#dlgCopy").textContent = "Copied"; setTimeout(() => ($("#dlgCopy").textContent = "Copy"), 1200); };
function mailRecurring(r) {
  showMail(`Customer ${r.customer} – ${MONTHS[r.mo - 1]} order`,
`Subject: Planning your ${MONTHS[r.mo - 1]} calibration – reserve your slot

Dear customer ${r.customer},

every year around ${MONTHS[r.mo - 1]} you send us about ${fmt(r.avg_n)} instruments (mainly ${r.grp}).
${r.years.map((y, i) => `  ${y}: ${r.counts[i]} instruments`).join("\n")}

To make sure they are back on time, we would like to reserve lab capacity for you now.
If you can send them 2–3 weeks earlier, we can offer a reduced price.

Could you confirm the expected quantity and date?

Best regards
Your Perschmann Calibration team`);
}
function mailCustomer(c) {
  const body = {
    "win-back": `we noticed we have not received instruments from you for ${fmt(c.months_since_last_cal, 0)} months. We would like to understand whether anything has changed and how we can help – for example with a priority slot or a free pickup.`,
    "remind: overdue": `according to our records ${fmt(c.overdue_open)} of your instruments are past their calibration date. We can pick them up free of charge and return them within our standard lead time.`,
    "remind: due soon": `${fmt(c.due_next_3m)} of your instruments are due for calibration within the next 3 months. Shall we reserve a slot for you now?`,
    "cross-sell": `companies in your industry also have their ${c.top_missing_group ?? "other instruments"} calibrated with us. We would be happy to include them in your next order.`,
    monitor: "thank you for working with us. Is there anything we can plan ahead for you?",
  }[c.action];
  showMail(`Customer ${c.customer} – ${c.action}`,
`Subject: Your calibrations with Perschmann

Dear customer ${c.customer},

${body}
${isContract(c) ? "\nWe can also offer an annual calibration plan: reserved slots and a fixed price for the whole year.\n" : ""}
Best regards
Your Perschmann Calibration team`);
}

/* ================================================================== WHAT-IF */
function initWhatif() {
  const labOpts = LABS.filter((l) => hasData(l.id)).map((l) => `<option value="${l.id}">${l.id} ${esc(l.name)}</option>`).join("");
  $("#wHireLab").innerHTML = labOpts; $("#wHireLab").value = "MR7";
  $("#wDownLab").innerHTML = labOpts; $("#wDownLab").value = "MR5";
  $("#wLose").innerHTML += D.customers.slice().sort((a, b) => b.cal_12m - a.cal_12m).slice(0, 25)
    .map((c) => `<option value="${c.customer}">${c.customer} · ${fmt(c.cal_12m)}/yr · ${c.room ?? "?"}</option>`).join("");
  document.querySelectorAll("#whatif input, #whatif select").forEach((x) => x.addEventListener("input", renderWhatif));
  $("#wReset").onclick = () => { ["#wDemand", "#wHire", "#wDown", "#wVac"].forEach((s) => ($(s).value = 0)); $("#wLose").value = ""; renderWhatif(); };
}
function whatifFn() {
  const dem = +$("#wDemand").value / 100, hire = +$("#wHire").value, hireLab = $("#wHireLab").value;
  const down = +$("#wDown").value, downLab = $("#wDownLab").value, vac = +$("#wVac").value / 100;
  const lose = CUST[$("#wLose").value];
  const peak = WEEKS.map((_, i) => ({ i, l: effective(downLab, i)?.load ?? 0 })).sort((a, b) => b.l - a.l)[0].i;
  const downStart = Math.max(0, Math.min(WEEKS.length - down, peak - Math.floor(down / 2)));
  const quiet = WEEKS.map((w, i) => [1, 2, 11].includes(+w.slice(5, 7)) ? i : -1).filter((i) => i >= 0);
  return (room, i) => {
    const e = effective(room, i);
    if (!e) return null;
    let need = e.need * (1 + dem), avail = e.avail;
    if (lose && lose.room === room) need -= (lose.cal_12m / 52) * e.h_per_service;
    if (room === hireLab) avail += hire * TECH_WEEK_H;
    if (room === downLab && i >= downStart && i < downStart + down) avail *= 0.5;
    if (vac) {
      const lab = LAB[room], yearVac = lab.fte * 30 * 7.7;
      const vacWeek = (m) => (yearVac * lab.vacation_profile[m - 1]) / 4.33;
      const mo = +WEEKS[i].slice(5, 7);
      const moved = WEEKS.reduce((s, w) => s + ([7, 8].includes(+w.slice(5, 7)) ? vac * vacWeek(+w.slice(5, 7)) : 0), 0);
      if ([7, 8].includes(mo)) avail += vac * vacWeek(mo);
      if (quiet.includes(i)) avail -= moved / quiet.length;
    }
    need = Math.max(0, need);
    return { ...e, need, avail, load: (need / avail) * 100, fixed: false };
  };
}
function renderWhatif() {
  $("#wDemandV").textContent = (+$("#wDemand").value > 0 ? "+" : "") + $("#wDemand").value + " %";
  $("#wHireV").textContent = $("#wHire").value + " in";
  $("#wDownV").textContent = $("#wDown").value + " weeks (capacity −50 %, around the lab's peak)";
  $("#wVacV").textContent = $("#wVac").value + " % of Jul/Aug → Jan/Feb/Nov";
  const fn = whatifFn();
  heatmap($("#heatWhatif"), fn);
  const labs = LABS.filter((l) => hasData(l.id));
  const stats = (f) => {
    let red = 0, over = 0, tot = 0, n = 0;
    for (const l of labs) WEEKS.forEach((_, i) => { const e = f(l.id, i); if (!e) return; n++; tot += e.load; if (e.load > 100) red++; over += Math.max(0, e.need - e.avail); });
    return { red, over, avg: tot / n };
  };
  const a = stats(effective), b = stats(fn);
  const delta = (x, y, d = 0, good = "down") => { const diff = y - x; if (Math.abs(diff) < 0.5) return `<span class="muted">no change</span>`;
    const better = good === "down" ? diff < 0 : diff > 0; return `<span class="pill ${better ? "ok" : "bad"}">${diff > 0 ? "+" : ""}${fmt(diff, d)}</span>`; };
  const lose = CUST[$("#wLose").value];
  const kpi = (v, l, d) => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div><div class="d">${d}</div></div>`;
  $("#wKpis").innerHTML =
    kpi(fmt(b.red), "overloaded lab-weeks", `was ${fmt(a.red)} ${delta(a.red, b.red)}`) +
    kpi(fmt(b.over), "hours over capacity (year)", `was ${fmt(a.over)} ${delta(a.over, b.over)}`) +
    kpi(pct(b.avg), "average lab load", `was ${pct(a.avg)} ${delta(a.avg, b.avg)}`) +
    kpi(lose ? fmt(lose.cal_12m) : "0", "calibrations lost per year", lose ? `<span class="pill bad">customer ${lose.customer}</span>` : `<span class="muted">pick a customer</span>`);
}

/* ================================================================== start */
renderDashboard();
renderAdmin();
initSales(); renderRecurring(); renderRisk(); renderQuote();
initWhatif(); renderWhatif();
const t = store.get("tab", "dash");
document.querySelector(`#nav button[data-tab="${t}"]`)?.click();
