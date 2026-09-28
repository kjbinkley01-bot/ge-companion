/* GE Companion dashboard. Talks only to the local server at the same origin. */
"use strict";

// Utilities ----------------------------------------------------------------------
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("geco." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("geco." + k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } },
};

function gp(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return Math.round(n).toLocaleString("en-US");
}
function short(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  const a = Math.abs(n), s = n < 0 ? "-" : "";
  if (a >= 1e9) return s + trim(a / 1e9) + "b";
  if (a >= 1e6) return s + trim(a / 1e6) + "m";
  if (a >= 1e4) return s + trim(a / 1e3) + "k";
  return s + Math.round(a).toLocaleString("en-US");
}
function trim(x) { return x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(2); }
function pct(x, digits = 1) { return x === null || x === undefined ? "-" : (x * 100).toFixed(digits) + "%"; }
function signCls(x) { return x === null || x === undefined ? "" : x > 0 ? "pos" : x < 0 ? "neg" : ""; }
function signed(x, f = gp) { if (x === null || x === undefined) return "-"; return (x > 0 ? "+" : "") + f(x); }
function ago(sec) {
  if (sec === null || sec === undefined) return "-";
  if (sec < 60) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  if (sec < 86400) return (sec / 3600).toFixed(1) + "h";
  return (sec / 86400).toFixed(1) + "d";
}
function iconUrl(icon) { return icon ? "https://oldschool.runescape.wiki/images/" + encodeURIComponent(icon.replace(/ /g, "_")) : ""; }
function wikiUrl(name) { return "https://oldschool.runescape.wiki/w/" + encodeURIComponent(name.replace(/ /g, "_")); }
function itemCell(r, extra = "") {
  return `<div class="item-cell"><img loading="lazy" alt="" src="${esc(iconUrl(r.icon))}" onerror="this.style.visibility='hidden'"><span class="nm">${esc(r.name)}</span>${extra}</div>`;
}
function fmtTime(ts, withDate) {
  const d = new Date(ts * 1000);
  return withDate ? d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, o);
  let data = null;
  try { data = await r.json(); } catch (e) { /* non JSON */ }
  if (!r.ok) throw new Error((data && data.error) || `Request failed (${r.status})`);
  return data;
}

function toast(title, msg, onClick) {
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<div class="t">${esc(title)}</div><div>${esc(msg)}</div>`;
  el.onclick = () => { el.remove(); onClick && onClick(); };
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 9000);
}

// State --------------------------------------------------------------------------
const S = {
  rows: [], byId: new Map(), nature: null, watch: new Set(), now: 0, status: {},
  tab: store.get("tab", "flips"), lastNid: 0, loaded: false,
};

// Theme --------------------------------------------------------------------------
(function initTheme() {
  const saved = store.get("theme", null);
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  document.documentElement.dataset.theme = saved || (prefersLight ? "light" : "dark");
  $("#themeBtn").onclick = () => {
    const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t;
    store.set("theme", t);
  };
})();

// Item picker (search box with suggestions) ---------------------------------------
function makePicker(input, list, onPick) {
  let hl = 0, matches = [];
  function render() {
    const q = input.value.trim().toLowerCase();
    if (!q) { list.hidden = true; return; }
    const starts = [], contains = [];
    for (const r of S.rows) {
      const n = r.name.toLowerCase();
      if (n.startsWith(q)) starts.push(r); else if (n.includes(q)) contains.push(r);
      if (starts.length > 40) break;
    }
    matches = starts.concat(contains).slice(0, 12);
    hl = 0;
    list.innerHTML = matches.length ? matches.map((r, i) =>
      `<div data-i="${i}" class="${i === 0 ? "hl" : ""}"><img alt="" src="${esc(iconUrl(r.icon))}" onerror="this.style.visibility='hidden'"><span>${esc(r.name)}</span><span class="pr">${short(r.high)}</span></div>`).join("")
      : `<div class="muted">No items match</div>`;
    list.hidden = false;
  }
  function pick(i) { const r = matches[i]; if (!r) return; list.hidden = true; onPick(r); }
  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    const items = $$("div[data-i]", list);
    if (e.key === "ArrowDown") { hl = Math.min(items.length - 1, hl + 1); e.preventDefault(); }
    else if (e.key === "ArrowUp") { hl = Math.max(0, hl - 1); e.preventDefault(); }
    else if (e.key === "Enter") { pick(hl); e.preventDefault(); return; }
    else if (e.key === "Escape") { list.hidden = true; return; }
    items.forEach((el, i) => el.classList.toggle("hl", i === hl));
  });
  list.addEventListener("mousedown", (e) => { const d = e.target.closest("div[data-i]"); if (d) { e.preventDefault(); pick(+d.dataset.i); } });
  input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 150));
}
function pickerField(label, id, cls = "field wide") {
  return `<label class="${cls}"><span>${label}</span><div class="picker"><input class="input" id="${id}" placeholder="Type an item name" autocomplete="off"><div class="picker-list" id="${id}List" hidden></div></div></label>`;
}

// Charts -------------------------------------------------------------------------
// Line chart for instant-buy / instant-sell prices with a volume panel below.
// One y-axis per panel; hover shows a crosshair and a tooltip.
function niceTicks(min, max, n = 4) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || mag * 10;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step, out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(v);
  return out;
}

function priceChart(host, data, opts = {}) {
  const W = Math.max(320, host.clientWidth || 600), H1 = 220, H2 = 70, gap = 18, padL = 58, padR = 12, padT = 8, padB = 22;
  const H = padT + H1 + gap + H2 + padB;
  const pts = data.filter((d) => d.avgHighPrice != null || d.avgLowPrice != null);
  if (!pts.length) { host.innerHTML = `<div class="empty">No trades recorded in this window.</div>`; return; }
  const t0 = data[0].timestamp, t1 = data[data.length - 1].timestamp || t0 + 1;
  const vals = pts.flatMap((d) => [d.avgHighPrice, d.avgLowPrice]).filter((v) => v != null);
  let ymin = Math.min(...vals), ymax = Math.max(...vals);
  const pad = (ymax - ymin) * 0.08 || ymax * 0.02 || 1; ymin -= pad; ymax += pad;
  const ticks = niceTicks(ymin, ymax, 4); ymin = ticks[0]; ymax = ticks[ticks.length - 1];
  const vmax = Math.max(1, ...data.map((d) => (d.highPriceVolume || 0) + (d.lowPriceVolume || 0)));
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (W - padL - padR);
  const y = (v) => padT + H1 - ((v - ymin) / (ymax - ymin)) * H1;
  const vy0 = padT + H1 + gap + H2;
  const vy = (v) => vy0 - (v / vmax) * H2;
  const path = (key) => {
    let d = "", pen = false;
    for (const p of data) {
      const v = p[key];
      if (v == null) { pen = false; continue; }
      d += (pen ? "L" : "M") + x(p.timestamp).toFixed(1) + "," + y(v).toFixed(1);
      pen = true;
    }
    return d;
  };
  const bw = Math.max(1, (W - padL - padR) / data.length - 2);
  const bars = data.map((p) => {
    const v = (p.highPriceVolume || 0) + (p.lowPriceVolume || 0);
    if (!v) return "";
    const top = vy(v), h = Math.max(1, vy0 - top);
    return `<rect class="bar" x="${(x(p.timestamp) - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(2, bw / 2)}"/>`;
  }).join("");
  const span = t1 - t0, withDate = span > 36 * 3600;
  const xt = [];
  for (let i = 0; i <= 5; i++) xt.push(t0 + (span * i) / 5);
  host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Price and volume chart">
    ${ticks.map((t) => `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${short(t)}</text>`).join("")}
    <path class="l2" d="${path("avgLowPrice")}"/>
    <path class="l1" d="${path("avgHighPrice")}"/>
    <line class="axis" x1="${padL}" x2="${W - padR}" y1="${vy0}" y2="${vy0}"/>
    ${bars}
    <text x="${padL - 8}" y="${vy(vmax) + 4}" text-anchor="end">${short(vmax)}</text>
    ${xt.map((t, i) => `<text x="${x(t)}" y="${H - 4}" text-anchor="${i === 0 ? "start" : i === 5 ? "end" : "middle"}">${fmtTime(t, withDate)}</text>`).join("")}
    <g class="hover" visibility="hidden"><line class="xhair" y1="${padT}" y2="${vy0}"/><circle class="dot1" r="4"/><circle class="dot2" r="4"/></g>
    <rect x="${padL}" y="${padT}" width="${W - padL - padR}" height="${vy0 - padT}" fill="transparent" class="hit"/>
  </svg>`;
  const svg = $("svg", host), g = $(".hover", svg), tip = $("#tooltip");
  const hit = $(".hit", svg);
  hit.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * W;
    const t = t0 + ((mx - padL) / (W - padL - padR)) * (t1 - t0);
    let best = data[0];
    for (const p of data) if (Math.abs(p.timestamp - t) < Math.abs(best.timestamp - t)) best = p;
    const px = x(best.timestamp);
    g.setAttribute("visibility", "visible");
    $(".xhair", g).setAttribute("x1", px); $(".xhair", g).setAttribute("x2", px);
    const d1 = $(".dot1", g), d2 = $(".dot2", g);
    if (best.avgHighPrice != null) { d1.setAttribute("cx", px); d1.setAttribute("cy", y(best.avgHighPrice)); d1.style.display = ""; } else d1.style.display = "none";
    if (best.avgLowPrice != null) { d2.setAttribute("cx", px); d2.setAttribute("cy", y(best.avgLowPrice)); d2.style.display = ""; } else d2.style.display = "none";
    tip.innerHTML = `<div class="muted">${esc(fmtTime(best.timestamp, true))}</div>
      <div class="r"><span><i style="background:var(--series-1)"></i>Instant buy</span><b>${gp(best.avgHighPrice)}</b></div>
      <div class="r"><span><i style="background:var(--series-2)"></i>Instant sell</span><b>${gp(best.avgLowPrice)}</b></div>
      <div class="r"><span class="muted">Volume</span><span>${gp((best.highPriceVolume || 0) + (best.lowPriceVolume || 0))}</span></div>`;
    tip.hidden = false;
    const tx = Math.min(window.innerWidth - 200, e.clientX + 14);
    tip.style.left = tx + "px"; tip.style.top = (e.clientY + 14) + "px";
  });
  hit.addEventListener("mouseleave", () => { g.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

// Single series bar chart (used for daily flip profit). Negative bars go below zero.
function barChart(host, items, labelKey, valueKey) {
  const W = Math.max(320, host.clientWidth || 600), H = 190, padL = 58, padR = 10, padT = 10, padB = 24;
  if (!items.length) { host.innerHTML = `<div class="empty">Closed flips will show up here by day.</div>`; return; }
  const vals = items.map((d) => d[valueKey]);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
  const ymin = ticks[0], ymax = ticks[ticks.length - 1];
  const y = (v) => padT + (H - padT - padB) * (1 - (v - ymin) / (ymax - ymin || 1));
  const slot = (W - padL - padR) / items.length, bw = Math.max(2, Math.min(28, slot - 2));
  const bars = items.map((d, i) => {
    const v = d[valueKey], cx = padL + slot * (i + 0.5), top = y(Math.max(0, v)), bot = y(Math.min(0, v));
    return `<rect class="bar ${v < 0 ? "neg" : "posb"}" data-i="${i}" x="${cx - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, bot - top)}" rx="3"/>`;
  }).join("");
  const every = Math.ceil(items.length / 8);
  host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Profit by day">
    ${ticks.map((t) => `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${short(t)}</text>`).join("")}
    <line class="axis" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>
    ${bars}
    ${items.map((d, i) => i % every ? "" : `<text x="${padL + slot * (i + 0.5)}" y="${H - 6}" text-anchor="middle">${esc(String(d[labelKey]).slice(5))}</text>`).join("")}
  </svg>`;
  const tip = $("#tooltip");
  $$("rect.bar", host).forEach((r) => {
    r.addEventListener("mousemove", (e) => {
      const d = items[+r.dataset.i];
      tip.innerHTML = `<div class="muted">${esc(d[labelKey])}</div><b class="${signCls(d[valueKey])}">${signed(d[valueKey])} gp</b>`;
      tip.hidden = false; tip.style.left = (e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px";
    });
    r.addEventListener("mouseleave", () => (tip.hidden = true));
  });
}

function sparkline(data) {
  const vals = data.map((d) => {
    const hv = d.highPriceVolume || 0, lv = d.lowPriceVolume || 0;
    const a = d.avgHighPrice, b = d.avgLowPrice;
    if (a != null && b != null && hv + lv) return (a * hv + b * lv) / (hv + lv);
    return a ?? b;
  }).filter((v) => v != null);
  if (vals.length < 2) return `<span class="muted small">Collecting</span>`;
  const mn = Math.min(...vals), mx = Math.max(...vals), W = 110, H = 28;
  const d = vals.map((v, i) => (i ? "L" : "M") + ((i / (vals.length - 1)) * (W - 2) + 1).toFixed(1) + "," + (H - 3 - ((v - mn) / (mx - mn || 1)) * (H - 6)).toFixed(1)).join("");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${d}"/></svg>`;
}

// Sortable table helper ----------------------------------------------------------
function sortRows(rows, key, dir) {
  const m = dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "string") return m * x.localeCompare(y);
    return m * (x - y);
  });
}
function thead(cols, sort) {
  return "<thead><tr>" + cols.map((c) => {
    const s = c.sort ? "sortable" : "", on = sort && c.sort && sort.key === c.sort ? " sorted" : "";
    const arrow = on ? (sort.dir === "asc" ? " ▲" : " ▼") : "";
    return `<th class="${s}${on}${c.num ? " num" : ""}" ${c.sort ? `data-sort="${c.sort}"` : ""} ${c.title ? `title="${esc(c.title)}"` : ""}>${esc(c.label)}${arrow}</th>`;
  }).join("") + "</tr></thead>";
}
function bindSort(host, sort, rerender) {
  $$("th[data-sort]", host).forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.sort;
    if (sort.key === k) sort.dir = sort.dir === "asc" ? "desc" : "asc";
    else { sort.key = k; sort.dir = k === "name" ? "asc" : "desc"; }
    rerender();
  }));
}
function bindRowClicks(host) {
  $$("tbody tr[data-id]", host).forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest("button, a, input")) return;
    openItem(+tr.dataset.id);
  }));
  $$(".star", host).forEach((b) => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    await toggleWatch(+b.dataset.id);
  }));
}
function star(id) { const on = S.watch.has(id); return `<button class="star ${on ? "on" : ""}" data-id="${id}" title="${on ? "Remove from" : "Add to"} watchlist" aria-label="Watchlist">${on ? "★" : "☆"}</button>`; }
function pressureBar(r) {
  if (r.buyPressure == null) return `<span class="muted">-</span>`;
  const p = Math.round(r.buyPressure * 100);
  return `<span class="pressure" title="Last hour: ${p}% of trades were instant buys (buyers paying up), ${100 - p}% instant sells"><i style="width:${p}%"></i></span>`;
}
function signalTag(r) {
  if (!r.signal) return "";
  const t = { pump: "Pump?", dump: "Dump?", spike: "Vol spike" }[r.signal];
  return ` <span class="tag ${r.signal}" title="Last hour volume is 4x+ its recent average${r.signal !== "spike" ? " with a sharp price move" : ""}">${t}</span>`;
}

async function toggleWatch(id) {
  if (S.watch.has(id)) { await api("/api/watchlist?id=" + id, { method: "DELETE" }); S.watch.delete(id); }
  else { await api("/api/watchlist", { method: "POST", body: { id } }); S.watch.add(id); }
  renderTab(S.tab, true);
  if (!$("#drawer").hidden && S.openId === id) openItem(id);
}

// Tabs ---------------------------------------------------------------------------
const renderers = {};
function showTab(name) {
  S.tab = name; store.set("tab", name);
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + name));
  renderTab(name);
}
function renderTab(name, soft) {
  const fn = renderers[name];
  if (fn) fn($("#tab-" + name), soft);
}
$$("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

// Flip finder --------------------------------------------------------------------
const FF = Object.assign({
  q: "", maxPrice: "", minProfit: "", minRoi: "", minVol: "500", members: "all",
  hideStale: true, preset: "all", show: 100, sort: { key: "est4h", dir: "desc" },
}, store.get("ff", {}));
FF.show = 100;
const PRESETS = {
  all: { label: "All", apply: {} },
  volume: { label: "High volume", apply: { minVol: "20000", maxPrice: "", minProfit: "1", minRoi: "", sort: { key: "est4h", dir: "desc" } } },
  bigticket: { label: "Big ticket", apply: { minVol: "20", maxPrice: "", minProfit: "100000", minRoi: "", sort: { key: "profit", dir: "desc" } } },
  lowrisk: { label: "Steady margins", apply: { minVol: "5000", maxPrice: "5000000", minProfit: "", minRoi: "1", sort: { key: "roi", dir: "desc" } } },
  cheap: { label: "Under 100k", apply: { minVol: "1000", maxPrice: "100000", minProfit: "1", minRoi: "", sort: { key: "est4h", dir: "desc" } } },
};

renderers.flips = function (host, soft) {
  if (!soft || !$("#ffTable", host)) {
    host.innerHTML = `
      <h2>Flip finder</h2>
      <p class="lede">Buy near the instant-sell price, sell near the instant-buy price. Profit is after the 2% GE tax. <b>Est. 4h</b> is profit per item times what you can realistically fill in one buy limit window (the smaller of the limit and about a sixth of daily volume).</p>
      <div class="chips" id="ffPresets">${Object.entries(PRESETS).map(([k, p]) => `<button class="chip ${FF.preset === k ? "on" : ""}" data-p="${k}">${p.label}</button>`).join("")}</div>
      <div class="filters">
        <label class="field wide"><span>Filter by name</span><input class="input" id="ffQ" value="${esc(FF.q)}" placeholder="e.g. rune, potion"></label>
        <label class="field"><span>Max buy price</span><input class="input" id="ffMax" inputmode="numeric" value="${esc(FF.maxPrice)}" placeholder="Any"></label>
        <label class="field"><span>Min profit / item</span><input class="input" id="ffMinP" inputmode="numeric" value="${esc(FF.minProfit)}" placeholder="Any"></label>
        <label class="field"><span>Min ROI %</span><input class="input" id="ffRoi" inputmode="decimal" value="${esc(FF.minRoi)}" placeholder="Any"></label>
        <label class="field"><span>Min 24h volume</span><input class="input" id="ffVol" inputmode="numeric" value="${esc(FF.minVol)}" placeholder="Any"></label>
        <label class="field"><span>Members</span><select class="input" id="ffMem"><option value="all">All items</option><option value="f2p">F2P only</option><option value="p2p">Members only</option></select></label>
        <label class="check"><input type="checkbox" id="ffStale" ${FF.hideStale ? "checked" : ""}> Hide stale prices</label>
      </div>
      <div id="ffInfo" class="muted small" style="margin-bottom:6px"></div>
      <div class="table-wrap" id="ffTable"></div>`;
    $("#ffMem", host).value = FF.members;
    const bind = (id, key, ev = "input") => $(id, host).addEventListener(ev, (e) => {
      FF[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      FF.preset = "custom"; FF.show = 100; $$("#ffPresets .chip", host).forEach((c) => c.classList.remove("on"));
      store.set("ff", FF); drawFlips(host);
    });
    bind("#ffQ", "q"); bind("#ffMax", "maxPrice"); bind("#ffMinP", "minProfit"); bind("#ffRoi", "minRoi");
    bind("#ffVol", "minVol"); bind("#ffMem", "members", "change"); bind("#ffStale", "hideStale", "change");
    $$("#ffPresets .chip", host).forEach((c) => c.addEventListener("click", () => {
      const p = PRESETS[c.dataset.p];
      Object.assign(FF, { minVol: "", maxPrice: "", minProfit: "", minRoi: "" }, JSON.parse(JSON.stringify(p.apply)));
      FF.preset = c.dataset.p; FF.show = 100; store.set("ff", FF);
      renderers.flips(host, false);
    }));
  }
  drawFlips(host);
};

function numOr(v, d) { const n = parseFloat(String(v).replace(/[, ]/g, "").replace(/k$/i, "e3").replace(/m$/i, "e6").replace(/b$/i, "e9")); return Number.isFinite(n) ? n : d; }

function drawFlips(host) {
  const q = FF.q.trim().toLowerCase();
  const maxP = numOr(FF.maxPrice, Infinity), minP = numOr(FF.minProfit, -Infinity);
  const minR = numOr(FF.minRoi, -Infinity) / 100, minV = numOr(FF.minVol, 0);
  let rows = S.rows.filter((r) => r.profit != null && r.profit > 0 && r.low != null
    && (!q || r.name.toLowerCase().includes(q)) && r.low <= maxP && r.profit >= minP
    && (r.roi ?? -1) >= minR && (r.vol24 || r.vol1h * 24) >= minV
    && (FF.members === "all" || (FF.members === "f2p" ? !r.members : r.members))
    && (!FF.hideStale || !r.stale));
  rows = sortRows(rows, FF.sort.key, FF.sort.dir);
  const cols = [
    { label: "", }, { label: "Item", sort: "name" },
    { label: "Buy at", sort: "low", num: 1, title: "Latest instant-sell price" },
    { label: "Sell at", sort: "high", num: 1, title: "Latest instant-buy price" },
    { label: "Tax", sort: "tax", num: 1 },
    { label: "Profit", sort: "profit", num: 1, title: "Per item, after tax" },
    { label: "ROI", sort: "roi", num: 1 },
    { label: "Limit", sort: "limit", num: 1, title: "GE buy limit per 4 hours" },
    { label: "1h vol", sort: "vol1h", num: 1 },
    { label: "24h vol", sort: "vol24", num: 1 },
    { label: "Est. 4h", sort: "est4h", num: 1, title: "Profit x realistic fill for one buy limit window" },
    { label: "Pressure", title: "Share of last hour trades that were instant buys" },
    { label: "Updated", sort: "age", num: 1 },
  ];
  const shown = rows.slice(0, FF.show);
  $("#ffInfo", host).textContent = `${rows.length.toLocaleString()} items match` + (S.status.backfill && S.status.backfill !== "done" ? ` · Loading 24h history (${S.status.backfill}), volumes fill in shortly` : "");
  $("#ffTable", host).innerHTML = rows.length ? `<table>${thead(cols, FF.sort)}<tbody>${shown.map((r) => `
    <tr data-id="${r.id}">
      <td>${star(r.id)}</td>
      <td>${itemCell(r, signalTag(r) + (r.stale ? ` <span class="tag stale">Stale</span>` : "") + (r.taxExempt ? ` <span class="tag free">No tax</span>` : ""))}</td>
      <td class="num">${gp(r.low)}</td><td class="num">${gp(r.high)}</td>
      <td class="num muted">${gp(r.tax)}</td>
      <td class="num pos"><b>${gp(r.profit)}</b></td>
      <td class="num">${pct(r.roi, 2)}</td>
      <td class="num">${gp(r.limit)}</td>
      <td class="num">${short(r.vol1h)}</td><td class="num">${short(r.vol24)}</td>
      <td class="num"><b>${short(r.est4h)}</b></td>
      <td>${pressureBar(r)}</td>
      <td class="num muted">${ago(r.age)}</td>
    </tr>`).join("")}</tbody></table>
    ${rows.length > FF.show ? `<div class="more-row"><button class="btn small" id="ffMore">Show 100 more</button></div>` : ""}`
    : `<div class="empty">No items match these filters. Try loosening them.</div>`;
  bindSort($("#ffTable", host), FF.sort, () => { store.set("ff", FF); drawFlips(host); });
  bindRowClicks($("#ffTable", host));
  const more = $("#ffMore", host);
  if (more) more.onclick = () => { FF.show += 100; drawFlips(host); };
}

// Movers -------------------------------------------------------------------------
const MV = Object.assign({ period: "chg24h", minVol: "1000", minPrice: "1000" }, store.get("mv", {}));
renderers.movers = function (host, soft) {
  if (!soft || !$("#mvUp", host)) {
    host.innerHTML = `
      <h2>Movers</h2>
      <p class="lede">Biggest price changes, measured on volume weighted hourly averages. Longer periods fill in as the app collects history (7d needs a week of running).</p>
      <div class="filters">
        <div class="field"><span>Period</span><div class="seg" id="mvPer" style="margin-left:0">${[["chg1h", "1h"], ["chg6h", "6h"], ["chg24h", "24h"], ["chg7d", "7d"]].map(([k, l]) => `<button data-k="${k}" class="${MV.period === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
        <label class="field"><span>Min 24h volume</span><input class="input" id="mvVol" value="${esc(MV.minVol)}"></label>
        <label class="field"><span>Min price</span><input class="input" id="mvPrice" value="${esc(MV.minPrice)}"></label>
      </div>
      <div class="grid2"><div><h3>Gainers</h3><div class="table-wrap" id="mvUp"></div></div><div><h3>Losers</h3><div class="table-wrap" id="mvDown"></div></div></div>
      <h3>Unusual volume</h3><p class="lede small">Last hour traded at least 4x its recent hourly average. A spike with a sharp rise can mean a pump; be careful buying into it.</p>
      <div class="table-wrap" id="mvSig"></div>`;
    $$("#mvPer button", host).forEach((b) => b.addEventListener("click", () => { MV.period = b.dataset.k; store.set("mv", MV); renderers.movers(host, false); }));
    $("#mvVol", host).addEventListener("input", (e) => { MV.minVol = e.target.value; store.set("mv", MV); drawMovers(host); });
    $("#mvPrice", host).addEventListener("input", (e) => { MV.minPrice = e.target.value; store.set("mv", MV); drawMovers(host); });
  }
  drawMovers(host);
};
function drawMovers(host) {
  const k = MV.period, minV = numOr(MV.minVol, 0), minP = numOr(MV.minPrice, 0);
  const base = S.rows.filter((r) => r[k] != null && (r.vol24 || 0) >= minV && (r.high || 0) >= minP);
  const up = sortRows(base.filter((r) => r[k] > 0), k, "desc").slice(0, 25);
  const down = sortRows(base.filter((r) => r[k] < 0), k, "asc").slice(0, 25);
  const tbl = (rows) => rows.length ? `<table>${thead([{ label: "" }, { label: "Item" }, { label: "Price", num: 1 }, { label: "Change", num: 1 }, { label: "24h vol", num: 1 }], null)}<tbody>${rows.map((r) => `
    <tr data-id="${r.id}"><td>${star(r.id)}</td><td>${itemCell(r, signalTag(r))}</td><td class="num">${gp(r.high)}</td><td class="num ${signCls(r[k])}"><b>${r[k] > 0 ? "+" : ""}${pct(r[k])}</b></td><td class="num">${short(r.vol24)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">${S.status.backfill && S.status.backfill !== "done" ? "Loading history..." : "Not enough history for this period yet."}</div>`;
  $("#mvUp", host).innerHTML = tbl(up);
  $("#mvDown", host).innerHTML = tbl(down);
  const sig = sortRows(S.rows.filter((r) => r.signal), "vol1h", "desc").slice(0, 30);
  $("#mvSig", host).innerHTML = sig.length ? `<table>${thead([{ label: "" }, { label: "Item" }, { label: "Price", num: 1 }, { label: "1h change", num: 1 }, { label: "Last hour vol", num: 1 }, { label: "24h vol", num: 1 }, { label: "Pressure" }], null)}<tbody>${sig.map((r) => `
    <tr data-id="${r.id}"><td>${star(r.id)}</td><td>${itemCell(r, signalTag(r))}</td><td class="num">${gp(r.high)}</td><td class="num ${signCls(r.chg1h)}">${r.chg1h > 0 ? "+" : ""}${pct(r.chg1h)}</td><td class="num">${short(r.vol1h)}</td><td class="num">${short(r.vol24)}</td><td>${pressureBar(r)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">Nothing unusual right now.</div>`;
  ["#mvUp", "#mvDown", "#mvSig"].forEach((s) => bindRowClicks($(s, host)));
}

// Watchlist ----------------------------------------------------------------------
const sparkCache = new Map();
renderers.watch = async function (host) {
  const rows = S.rows.filter((r) => S.watch.has(r.id));
  host.innerHTML = `<h2>Watchlist</h2><p class="lede">Items you've starred anywhere in the app. The sparkline is the last 24 hours from your own saved history.</p>
    <div class="table-wrap" id="wlTable"></div>`;
  if (!rows.length) { $("#wlTable", host).innerHTML = `<div class="empty">Click the ☆ next to any item to watch it.</div>`; return; }
  const cols = [{ label: "" }, { label: "Item" }, { label: "24h" }, { label: "Buy at", num: 1 }, { label: "Sell at", num: 1 }, { label: "Profit", num: 1 }, { label: "ROI", num: 1 }, { label: "1h", num: 1 }, { label: "24h", num: 1 }, { label: "24h vol", num: 1 }, { label: "Pressure" }, { label: "Updated", num: 1 }];
  const draw = () => {
    $("#wlTable", host).innerHTML = `<table>${thead(cols, null)}<tbody>${rows.map((r) => `
      <tr data-id="${r.id}"><td>${star(r.id)}</td><td>${itemCell(r, signalTag(r))}</td><td>${sparkCache.has(r.id) ? sparkline(sparkCache.get(r.id)) : ""}</td>
      <td class="num">${gp(r.low)}</td><td class="num">${gp(r.high)}</td><td class="num ${signCls(r.profit)}">${gp(r.profit)}</td><td class="num">${pct(r.roi, 2)}</td>
      <td class="num ${signCls(r.chg1h)}">${r.chg1h != null ? (r.chg1h > 0 ? "+" : "") + pct(r.chg1h) : "-"}</td>
      <td class="num ${signCls(r.chg24h)}">${r.chg24h != null ? (r.chg24h > 0 ? "+" : "") + pct(r.chg24h) : "-"}</td>
      <td class="num">${short(r.vol24)}</td><td>${pressureBar(r)}</td><td class="num muted">${ago(r.age)}</td></tr>`).join("")}</tbody></table>`;
    bindRowClicks($("#wlTable", host));
  };
  draw();
  await Promise.all(rows.map(async (r) => {
    try { const d = await api(`/api/localhistory?id=${r.id}&res=1h&days=1`); sparkCache.set(r.id, d.data); } catch (e) { /* ignore */ }
  }));
  if (S.tab === "watch") draw();
};

// Alerts -------------------------------------------------------------------------
let ALERT_KINDS = {};
renderers.alerts = async function (host) {
  const [a, n] = await Promise.all([api("/api/alerts"), api("/api/notifications")]);
  ALERT_KINDS = a.kinds;
  host.innerHTML = `<h2>Alerts</h2>
    <p class="lede">Alerts are checked every time prices refresh (about once a minute) while the app is running. When one fires you get a pop-up here and the bell lights up. The same alert won't repeat for 30 minutes.</p>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">New alert</h3>
        <div class="inline-form">${pickerField("Item", "alItem")}
          <label class="field"><span>When</span><select class="input" id="alKind">${Object.entries(ALERT_KINDS).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></label>
          <label class="field"><span>Value</span><input class="input" id="alTh" placeholder="e.g. 750k"></label>
          <label class="check"><input type="checkbox" id="alOnce"> Only once</label>
          <button class="btn primary" id="alAdd">Add alert</button></div>
        <div id="alMsg" class="small" style="margin-top:8px"></div>
        <label class="check" style="margin-top:10px"><input type="checkbox" id="alSound" ${store.get("sound", true) ? "checked" : ""}> Play a sound when an alert fires</label>
      </div>
      <div class="card"><div style="display:flex;align-items:center;gap:8px"><h3 style="margin:0">Recent alerts</h3><button class="btn small ghost" id="alClear" style="margin-left:auto">Clear</button></div>
        <div class="feed" id="alFeed">${n.items.length ? n.items.map((x) => `<div class="n ${x.seen ? "" : "new"}" data-id="${x.item_id}"><span class="muted small" style="white-space:nowrap">${esc(fmtTime(x.ts, true))}</span><span>${esc(x.message)}</span></div>`).join("") : `<div class="empty">Nothing yet.</div>`}</div>
      </div>
    </div>
    <h3>Your alerts</h3>
    <div class="table-wrap">${a.alerts.length ? `<table>${thead([{ label: "Item" }, { label: "Condition" }, { label: "Value", num: 1 }, { label: "Now", num: 1 }, { label: "Last fired" }, { label: "" }], null)}<tbody>${a.alerts.map((x) => {
      const r = S.byId.get(x.item_id) || {};
      const now = { price_below: gp(r.low), price_above: gp(r.high), profit_above: gp(r.profit), roi_above: pct(r.roi, 2), move_pct: r.chg1h != null ? pct(r.chg1h) : "-", spike: r.signal || "none" }[x.kind];
      const val = x.kind === "roi_above" || x.kind === "move_pct" ? x.threshold + "%" : x.kind === "spike" ? "" : gp(x.threshold);
      return `<tr data-id="${x.item_id}"><td>${itemCell({ name: x.name || "Item " + x.item_id, icon: x.icon })}</td><td>${esc(x.label)}${x.once ? ' <span class="tag">Once</span>' : ""}${x.enabled ? "" : ' <span class="tag">Paused</span>'}</td><td class="num">${val}</td><td class="num">${now}</td><td class="muted">${x.last_fired ? esc(fmtTime(x.last_fired, true)) : "Never"}</td>
        <td class="num"><button class="btn small" data-tog="${x.aid}">${x.enabled ? "Pause" : "Resume"}</button> <button class="btn small danger" data-del="${x.aid}">Delete</button></td></tr>`;
    }).join("")}</tbody></table>` : `<div class="empty">No alerts yet. Add one above or from any item's detail panel.</div>`}</div>`;
  let picked = null;
  makePicker($("#alItem", host), $("#alItemList", host), (r) => { picked = r; $("#alItem", host).value = r.name; });
  $("#alSound", host).onchange = (e) => store.set("sound", e.target.checked);
  $("#alAdd", host).onclick = async () => {
    const msg = $("#alMsg", host);
    if (!picked) { msg.innerHTML = `<span class="err">Pick an item from the list first.</span>`; return; }
    const kind = $("#alKind", host).value;
    const th = numOr($("#alTh", host).value, NaN);
    if (kind !== "spike" && !Number.isFinite(th)) { msg.innerHTML = `<span class="err">Enter a number for the value.</span>`; return; }
    await api("/api/alerts", { method: "POST", body: { item_id: picked.id, kind, threshold: kind === "spike" ? 0 : th, once: $("#alOnce", host).checked } });
    renderers.alerts(host); pollNotifications();
  };
  $("#alClear", host).onclick = async () => { await api("/api/notifications/clear", { method: "POST" }); renderers.alerts(host); pollNotifications(); };
  $$("[data-tog]", host).forEach((b) => (b.onclick = async (e) => { e.stopPropagation(); await api("/api/alerts/toggle", { method: "POST", body: { aid: +b.dataset.tog } }); renderers.alerts(host); }));
  $$("[data-del]", host).forEach((b) => (b.onclick = async (e) => { e.stopPropagation(); await api("/api/alerts?aid=" + b.dataset.del, { method: "DELETE" }); renderers.alerts(host); }));
  bindRowClicks(host);
  $$("#alFeed .n", host).forEach((el) => el.addEventListener("click", () => openItem(+el.dataset.id)));
  await api("/api/notifications/seen", { method: "POST" });
  setBell(0);
};

// Flip log -----------------------------------------------------------------------
renderers.log = async function (host) {
  const d = await api("/api/flips");
  const s = d.summary;
  host.innerHTML = `<h2>Flip log</h2>
    <p class="lede">Record what you actually bought and sold to track real profit after tax. Open flips (no sell price yet) are valued at the current instant-buy price.</p>
    <div class="tiles">
      <div class="tile"><div class="k">Realized profit</div><div class="v ${signCls(s.realized)}">${signed(s.realized, short)}</div><div class="s">${s.closed} closed flips</div></div>
      <div class="tile"><div class="k">Win rate</div><div class="v">${s.winRate == null ? "-" : pct(s.winRate, 0)}</div><div class="s">Avg ROI ${pct(s.avgRoi, 2)}</div></div>
      <div class="tile"><div class="k">Open positions</div><div class="v ${signCls(s.unrealized)}">${signed(s.unrealized, short)}</div><div class="s">${s.open} open, if sold now</div></div>
      <div class="tile"><div class="k">Tax paid</div><div class="v">${short(s.taxPaid)}</div><div class="s">On closed flips</div></div>
      <div class="tile"><div class="k">GP per hour held</div><div class="v">${s.gpPerHourHeld == null ? "-" : short(s.gpPerHourHeld)}</div><div class="s">Profit over buy-to-sell time</div></div>
    </div>
    <div class="card" style="margin-bottom:14px"><h3 style="margin-top:0">Log a flip</h3>
      <div class="inline-form">${pickerField("Item", "flItem")}
        <label class="field"><span>Quantity</span><input class="input" id="flQty" inputmode="numeric"></label>
        <label class="field"><span>Bought at (each)</span><input class="input" id="flBuy" inputmode="numeric"></label>
        <label class="field"><span>Sold at (each)</span><input class="input" id="flSell" inputmode="numeric" placeholder="Leave blank if open"></label>
        <label class="field wide"><span>Note</span><input class="input" id="flNote" placeholder="Optional"></label>
        <button class="btn" id="flLive" title="Fill in the current prices">Use live prices</button>
        <button class="btn primary" id="flAdd">Save</button></div>
      <div id="flMsg" class="small" style="margin-top:8px"></div></div>
    <div class="grid2">
      <div class="chart-card"><div class="chart-head"><span class="title">Profit by day</span><span class="muted small">Last 30 days with closed flips</span></div><div id="flChart"></div></div>
      <div class="card"><h3 style="margin-top:0">Best items</h3>${s.topItems.length ? `<table>${thead([{ label: "Item" }, { label: "Flips", num: 1 }, { label: "Profit", num: 1 }], null)}<tbody>${s.topItems.map((t) => { const m = S.byId.get(t.id) || t; return `<tr data-id="${t.id}"><td>${itemCell({ name: t.name, icon: m.icon })}</td><td class="num">${t.flips}</td><td class="num ${signCls(t.profit)}">${signed(t.profit, short)}</td></tr>`; }).join("")}</tbody></table>` : `<div class="empty">No closed flips yet.</div>`}</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;margin:18px 0 8px"><h3 style="margin:0">All flips</h3><a class="btn small" href="/api/flips.csv" style="margin-left:auto">Export CSV</a></div>
    <div class="table-wrap">${d.flips.length ? `<table>${thead([{ label: "Item" }, { label: "Qty", num: 1 }, { label: "Bought", num: 1 }, { label: "Sold", num: 1 }, { label: "Tax each", num: 1 }, { label: "Profit", num: 1 }, { label: "ROI", num: 1 }, { label: "Date" }, { label: "" }], null)}<tbody>${d.flips.map((f) => `
      <tr data-id="${f.item_id}"><td>${itemCell(f, f.note ? ` <span class="muted small">${esc(f.note)}</span>` : "")}</td><td class="num">${gp(f.qty)}</td><td class="num">${gp(f.buy_price)}</td>
      <td class="num">${f.open ? `<span class="muted">Open (now ${gp(f.livePrice)})</span>` : gp(f.sell_price)}</td><td class="num muted">${gp(f.taxEach)}</td>
      <td class="num ${signCls(f.open ? f.unrealized : f.profit)}">${f.open ? `<span title="Unrealized">${signed(f.unrealized)}*</span>` : signed(f.profit)}</td>
      <td class="num">${pct(f.roi, 2)}</td><td class="muted">${esc(fmtTime(f.sell_ts || f.buy_ts, true))}</td>
      <td class="num">${f.open ? `<button class="btn small" data-close="${f.fid}">Close</button> ` : ""}<button class="btn small danger" data-del="${f.fid}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No flips logged yet.</div>`}</div>
    <p class="muted small">* Unrealized: what the open position would make if sold at the current instant-buy price after tax.</p>`;
  let picked = null;
  makePicker($("#flItem", host), $("#flItemList", host), (r) => { picked = r; $("#flItem", host).value = r.name; if (!$("#flQty", host).value) $("#flQty", host).value = r.limit || ""; });
  $("#flLive", host).onclick = () => { if (!picked) return; $("#flBuy", host).value = picked.low || ""; $("#flSell", host).value = picked.high || ""; };
  $("#flAdd", host).onclick = async () => {
    const msg = $("#flMsg", host);
    if (!picked) { msg.innerHTML = `<span class="err">Pick an item from the list first.</span>`; return; }
    const qty = numOr($("#flQty", host).value, NaN), buy = numOr($("#flBuy", host).value, NaN), sellRaw = $("#flSell", host).value.trim();
    if (!(qty > 0) || !(buy >= 0)) { msg.innerHTML = `<span class="err">Enter a quantity and a buy price.</span>`; return; }
    try {
      await api("/api/flips", { method: "POST", body: { item_id: picked.id, qty: Math.round(qty), buy_price: Math.round(buy), sell_price: sellRaw ? Math.round(numOr(sellRaw, 0)) : null, note: $("#flNote", host).value } });
      renderers.log(host);
    } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $$("[data-del]", host).forEach((b) => (b.onclick = async (e) => { e.stopPropagation(); if (!confirmInline(b)) return; await api("/api/flips?fid=" + b.dataset.del, { method: "DELETE" }); renderers.log(host); }));
  $$("[data-close]", host).forEach((b) => (b.onclick = (e) => {
    e.stopPropagation();
    const f = d.flips.find((x) => x.fid === +b.dataset.close);
    const td = b.parentElement;
    td.innerHTML = `<input class="input" style="width:110px;display:inline-block" placeholder="Sold at" value="${f.livePrice || ""}"> <button class="btn small primary">Save</button>`;
    $("input", td).focus();
    $("button", td).onclick = async (ev) => {
      ev.stopPropagation();
      const sp = Math.round(numOr($("input", td).value, NaN));
      if (!Number.isFinite(sp)) return;
      await api("/api/flips", { method: "POST", body: { fid: f.fid, item_id: f.item_id, qty: f.qty, buy_price: f.buy_price, sell_price: sp, buy_ts: f.buy_ts, note: f.note } });
      renderers.log(host);
    };
  }));
  bindRowClicks(host);
  barChart($("#flChart", host), s.byDay, "day", "profit");
};
function confirmInline(btn) {
  if (btn.dataset.armed) return true;
  btn.dataset.armed = "1"; btn.textContent = "Confirm?";
  setTimeout(() => { if (btn.isConnected) { delete btn.dataset.armed; btn.textContent = "Delete"; } }, 3000);
  return false;
}

// High alch ----------------------------------------------------------------------
const AL = Object.assign({ patient: false, minVol: "100", members: "all" }, store.get("al", {}));
renderers.alch = function (host, soft) {
  if (!soft || !$("#alTable", host)) {
    host.innerHTML = `<h2>High alchemy</h2>
      <p class="lede">Profit per cast = high alch value, minus the item's price, minus one nature rune. Each cast is 65 Magic XP. Alching is free of GE tax because you never sell on the GE.</p>
      <div class="filters">
        <div class="tile" style="margin:0"><div class="k">Nature rune</div><div class="v" id="alNat">-</div></div>
        <label class="field"><span>Min 24h volume</span><input class="input" id="alVol" value="${esc(AL.minVol)}"></label>
        <label class="field"><span>Members</span><select class="input" id="alMem"><option value="all">All</option><option value="f2p">F2P only</option><option value="p2p">Members only</option></select></label>
        <label class="check"><input type="checkbox" id="alPat" ${AL.patient ? "checked" : ""}> Buy with patient offers (instant-sell price)</label>
      </div><div class="table-wrap" id="alTable"></div>`;
    $("#alMem", host).value = AL.members;
    $("#alVol", host).oninput = (e) => { AL.minVol = e.target.value; store.set("al", AL); drawAlch(host); };
    $("#alMem", host).onchange = (e) => { AL.members = e.target.value; store.set("al", AL); drawAlch(host); };
    $("#alPat", host).onchange = (e) => { AL.patient = e.target.checked; store.set("al", AL); drawAlch(host); };
  }
  drawAlch(host);
};
const ALS = { key: "perLimit", dir: "desc" };
function drawAlch(host) {
  $("#alNat", host).textContent = S.nature ? gp(S.nature) : "-";
  const minV = numOr(AL.minVol, 0);
  let rows = S.rows.filter((r) => r.highalch && S.nature && (r.vol24 || 0) >= minV
    && (AL.members === "all" || (AL.members === "f2p" ? !r.members : r.members))).map((r) => {
    const buy = AL.patient ? r.low : r.high;
    const p = buy ? r.highalch - buy - S.nature : null;
    return Object.assign({}, r, { buyA: buy, perCast: p, perLimit: p != null && r.limit ? p * r.limit : null, gpXp: p != null ? p / 65 : null });
  }).filter((r) => r.perCast != null && r.perCast > -500);
  rows = sortRows(rows, ALS.key, ALS.dir).slice(0, 150);
  $("#alTable", host).innerHTML = rows.length ? `<table>${thead([{ label: "" }, { label: "Item", sort: "name" }, { label: "Buy at", sort: "buyA", num: 1 }, { label: "Alch value", sort: "highalch", num: 1 }, { label: "Profit / cast", sort: "perCast", num: 1 }, { label: "GP / XP", sort: "gpXp", num: 1, title: "Positive means you earn gp while training" }, { label: "Limit", sort: "limit", num: 1 }, { label: "Profit / limit", sort: "perLimit", num: 1 }, { label: "24h vol", sort: "vol24", num: 1 }], ALS)}<tbody>${rows.map((r) => `
    <tr data-id="${r.id}"><td>${star(r.id)}</td><td>${itemCell(r)}</td><td class="num">${gp(r.buyA)}</td><td class="num">${gp(r.highalch)}</td><td class="num ${signCls(r.perCast)}"><b>${signed(r.perCast)}</b></td><td class="num ${signCls(r.gpXp)}">${r.gpXp.toFixed(2)}</td><td class="num">${gp(r.limit)}</td><td class="num ${signCls(r.perLimit)}">${signed(r.perLimit, short)}</td><td class="num">${short(r.vol24)}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">No items match.</div>`;
  bindSort($("#alTable", host), ALS, () => drawAlch(host));
  bindRowClicks($("#alTable", host));
}

// Decanting ----------------------------------------------------------------------
renderers.decant = async function (host) {
  host.innerHTML = `<h2>Decanting</h2><p class="lede">Buy potions in the cheapest dose (price per dose), have Bob Barter at the GE decant them into 4-dose for free, and sell. Profit is per 4-dose potion after tax. Buy limits apply to the dose you buy.</p><div class="table-wrap" id="dcTable"><div class="empty">Loading...</div></div>`;
  const rows = await api("/api/decant");
  const good = rows.filter((r) => r.best.profitPer4 > 0);
  $("#dcTable", host).innerHTML = good.length ? `<table>${thead([{ label: "Potion" }, { label: "Buy dose" }, { label: "Buy at", num: 1 }, { label: "Per dose", num: 1 }, { label: "Sell (4) at", num: 1 }, { label: "After tax", num: 1 }, { label: "Profit / (4)", num: 1 }, { label: "Buy limit", num: 1 }, { label: "Profit / limit", num: 1 }, { label: "24h vol (buy dose)", num: 1 }], null)}<tbody>${good.map((r) => {
    const m = S.byId.get(r.sellId) || {}, bd = S.byId.get(r.best.id) || {};
    return `<tr data-id="${r.best.id}"><td>${itemCell({ name: r.name, icon: m.icon })}</td><td>(${r.best.dose})</td><td class="num">${gp(r.best.buy)}</td><td class="num">${gp(r.best.perDose)}</td><td class="num">${gp(r.sell4)}</td><td class="num">${gp(r.sell4Net)}</td><td class="num pos"><b>${gp(r.best.profitPer4)}</b></td><td class="num">${gp(r.best.limit)}</td><td class="num pos">${short(r.profitPerLimit)}</td><td class="num">${short(bd.vol24)}</td></tr>`;
  }).join("")}</tbody></table>` : `<div class="empty">No profitable decants right now.</div>`;
  bindRowClicks($("#dcTable", host));
};

// Account ------------------------------------------------------------------------
const ACC = Object.assign({ player: "", mode: "normal" }, store.get("acc", {}));
renderers.account = function (host, soft) {
  if (soft && $("#acBody", host)) return;
  host.innerHTML = `<h2>Account</h2>
    <p class="lede">Your stats from the official hiscores. Every lookup is saved, so XP gains and goal pace build up the more you check. Nothing here logs in to the game.</p>
    <div class="filters">
      <label class="field wide"><span>Player name</span><input class="input" id="acName" maxlength="12" value="${esc(ACC.player)}" placeholder="Your RSN"></label>
      <label class="field"><span>Account type</span><select class="input" id="acMode"><option value="normal">Regular</option><option value="ironman">Ironman</option><option value="hardcore">Hardcore</option><option value="ultimate">Ultimate</option></select></label>
      <button class="btn primary" id="acGo">Look up</button>
    </div>
    <div id="acBody"></div>`;
  $("#acMode", host).value = ACC.mode;
  const go = () => { ACC.player = $("#acName", host).value.trim(); ACC.mode = $("#acMode", host).value; store.set("acc", ACC); loadAccount(host); };
  $("#acGo", host).onclick = go;
  $("#acName", host).addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  if (ACC.player) loadAccount(host);
};
async function loadAccount(host) {
  const body = $("#acBody", host);
  if (!ACC.player) return;
  body.innerHTML = `<div class="empty">Looking up ${esc(ACC.player)}...</div>`;
  let d, goals;
  try {
    d = await api(`/api/hiscores?player=${encodeURIComponent(ACC.player)}&mode=${ACC.mode}`);
    goals = await api(`/api/goals?player=${encodeURIComponent(ACC.player)}&mode=${ACC.mode}`);
  } catch (e) { body.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const ov = d.skills.find((s) => s.name === "Overall") || {};
  const skills = d.skills.filter((s) => s.name !== "Overall");
  const hasGain = (k) => skills.some((s) => s["gain_" + k] > 0);
  body.innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="k">Total level</div><div class="v">${gp(ov.level)}</div><div class="s">Rank ${gp(ov.rank)}</div></div>
      <div class="tile"><div class="k">Total XP</div><div class="v">${short(ov.xp)}</div><div class="s">${gp(ov.xp)}</div></div>
      <div class="tile"><div class="k">Gained today</div><div class="v">${ov.gain_day != null ? short(ov.gain_day) : "-"}</div><div class="s">Since first lookup in 24h</div></div>
      <div class="tile"><div class="k">Gained this week</div><div class="v">${ov.gain_week != null ? short(ov.gain_week) : "-"}</div><div class="s">Tracking since ${esc(fmtTime(d.trackedSince, true))}</div></div>
    </div>
    <div class="card" style="margin-bottom:14px"><h3 style="margin-top:0">Goals</h3>
      <div class="inline-form">
        <label class="field"><span>Skill</span><select class="input" id="glSkill">${skills.map((s) => `<option>${esc(s.name)}</option>`).join("")}</select></label>
        <label class="field"><span>Target level</span><input class="input" id="glLvl" value="99" inputmode="numeric"></label>
        ${pickerField("Supply item (optional)", "glItem", "field")}
        <label class="field"><span>XP per item</span><input class="input" id="glXp" placeholder="e.g. 252" inputmode="decimal"></label>
        <button class="btn primary" id="glAdd">Add goal</button>
      </div>
      <p class="muted small">Add a supply item and the XP each one gives (dragon bones on a gilded altar = 252, a Prayer training example) to see how many you need and what they cost at live prices.</p>
      <div id="glMsg" class="small"></div>
      <div class="table-wrap" style="margin-top:8px">${goals.length ? `<table>${thead([{ label: "Skill" }, { label: "Target" }, { label: "Progress" }, { label: "XP left", num: 1 }, { label: "Pace / day", num: 1 }, { label: "ETA" }, { label: "Supplies", num: 1 }, { label: "Cost", num: 1 }, { label: "" }], null)}<tbody>${goals.map((g) => `
        <tr class="static"><td><b>${esc(g.skill)}</b></td><td>Level ${g.target_level}</td>
        <td><div class="progress" title="${pct(g.progress)}"><i style="width:${(g.progress || 0) * 100}%"></i></div><span class="muted small">${pct(g.progress)}</span></td>
        <td class="num">${gp(g.remaining)}</td><td class="num">${g.xpPerDay ? short(g.xpPerDay) : "-"}</td>
        <td>${g.etaDays ? Math.ceil(g.etaDays) + " days" : `<span class="muted small">${g.remaining === 0 ? "Done" : "Needs more lookups"}</span>`}</td>
        <td class="num">${g.actions ? gp(g.actions) + " x " + esc(g.methodName || "") : "-"}</td>
        <td class="num">${g.methodCost ? short(g.methodCost) : "-"}</td>
        <td class="num"><button class="btn small danger" data-gdel="${g.gid}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No goals yet.</div>`}</div>
    </div>
    <div class="grid2">
      <div><h3>Skills</h3><div class="table-wrap"><table>${thead([{ label: "Skill" }, { label: "Level", num: 1 }, { label: "XP", num: 1 }, { label: "To next", num: 1 }, { label: "" }, ...(hasGain("day") ? [{ label: "Today", num: 1 }] : []), ...(hasGain("week") ? [{ label: "Week", num: 1 }] : []), { label: "Rank", num: 1 }], null)}<tbody>${skills.map((s) => `
        <tr class="static"><td>${esc(s.name)}</td><td class="num"><b>${s.level > 0 ? s.level : "-"}</b>${s.virtual > 99 ? ` <span class="muted small">(${s.virtual})</span>` : ""}</td><td class="num">${s.xp >= 0 ? gp(s.xp) : "-"}</td>
        <td class="num muted">${s.toNext ? short(s.toNext) : "-"}</td><td><div class="progress" style="min-width:60px"><i style="width:${(s.pctToNext || 0) * 100}%"></i></div></td>
        ${hasGain("day") ? `<td class="num ${s.gain_day > 0 ? "pos" : "muted"}">${s.gain_day > 0 ? "+" + short(s.gain_day) : "-"}</td>` : ""}
        ${hasGain("week") ? `<td class="num ${s.gain_week > 0 ? "pos" : "muted"}">${s.gain_week > 0 ? "+" + short(s.gain_week) : "-"}</td>` : ""}
        <td class="num muted">${s.rank > 0 ? gp(s.rank) : "-"}</td></tr>`).join("")}</tbody></table></div></div>
      <div><h3>Bosses, clues and activities</h3><div class="table-wrap">${d.activities.length ? `<table>${thead([{ label: "Activity" }, { label: "Score", num: 1 }, { label: "Rank", num: 1 }], null)}<tbody>${d.activities.map((a) => `<tr class="static"><td>${esc(a.name)}</td><td class="num"><b>${gp(a.score)}</b></td><td class="num muted">${gp(a.rank)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No ranked activities.</div>`}</div></div>
    </div>`;
  let picked = null;
  makePicker($("#glItem", body), $("#glItemList", body), (r) => { picked = r; $("#glItem", body).value = r.name; });
  $("#glItem", body).addEventListener("input", () => { if (!$("#glItem", body).value) picked = null; });
  $("#glAdd", body).onclick = async () => {
    try {
      await api("/api/goals", { method: "POST", body: { player: ACC.player, mode: ACC.mode, skill: $("#glSkill", body).value, target_level: +$("#glLvl", body).value, method_item_id: picked ? picked.id : null, xp_each: picked ? numOr($("#glXp", body).value, 0) || null : null } });
      loadAccount(host);
    } catch (e) { $("#glMsg", body).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $$("[data-gdel]", body).forEach((b) => (b.onclick = async () => { if (!confirmInline(b)) return; await api("/api/goals?gid=" + b.dataset.gdel, { method: "DELETE" }); loadAccount(host); }));
}

// Settings -----------------------------------------------------------------------
renderers.settings = async function (host) {
  const [cfg, st] = await Promise.all([api("/api/settings"), api("/api/status")]);
  host.innerHTML = `<h2>Settings</h2>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">Data source</h3>
        <p class="small muted">The OSRS Wiki asks tools to identify themselves. Adding your Discord name is optional but lets the Wiki team contact you about API changes.</p>
        <label class="field" style="margin-bottom:10px"><span>User-Agent</span><input class="input" id="stUA" value="${esc(cfg.user_agent)}"></label>
        <div class="inline-form" style="margin-top:0">
          <label class="field"><span>Price refresh (sec)</span><input class="input" id="stPoll" value="${cfg.latest_poll_seconds}"></label>
          <label class="field"><span>Stale after (min)</span><input class="input" id="stStale" value="${cfg.stale_minutes}"></label>
          <label class="field"><span>Alert cooldown (min)</span><input class="input" id="stCool" value="${cfg.alert_cooldown_minutes}"></label>
          <label class="field"><span>Keep 5m history (days)</span><input class="input" id="st5" value="${cfg.keep_5m_days}"></label>
          <label class="field"><span>Keep 1h history (days, 0 = forever)</span><input class="input" id="st1" value="${cfg.keep_1h_days}"></label>
        </div>
        <div style="margin-top:12px"><button class="btn primary" id="stSave">Save</button> <span id="stMsg" class="small muted"></span></div>
      </div>
      <div class="card"><h3 style="margin-top:0">Status</h3><div class="kv">
        <span class="k">Items tracked</span><span>${gp(st.items)} (${gp(st.priced)} with prices)</span>
        <span class="k">Last price refresh</span><span>${st.last_latest ? esc(fmtTime(st.last_latest, true)) : "Never"}</span>
        <span class="k">History backfill</span><span>${esc(st.backfill)}</span>
        <span class="k">Hourly snapshots saved</span><span>${gp(st.h1Snapshots)}</span>
        <span class="k">Database size</span><span>${short(st.dbBytes / 1024)} KB</span>
        <span class="k">GE tax</span><span>${(cfg.tax_rate * 100).toFixed(1)}%, capped at ${short(cfg.tax_cap)} per item</span>
      </div>
      ${st.errors.length ? `<h3>Recent problems</h3><div class="small err">${st.errors.map(esc).join("<br>")}</div>` : `<p class="small muted" style="margin-top:12px">No problems reported.</p>`}
      </div>
    </div>
    <p class="small muted" style="margin-top:16px">Prices come from the OSRS Wiki real-time prices API (data provided by RuneLite users). They're strong estimates, not guarantees: price check in game before big flips.</p>`;
  $("#stSave", host).onclick = async () => {
    const r = await api("/api/settings", { method: "POST", body: { user_agent: $("#stUA", host).value, latest_poll_seconds: $("#stPoll", host).value, stale_minutes: $("#stStale", host).value, alert_cooldown_minutes: $("#stCool", host).value, keep_5m_days: $("#st5", host).value, keep_1h_days: $("#st1", host).value } });
    $("#stMsg", host).textContent = "Saved. " + (r.note || "");
  };
};

// Item drawer --------------------------------------------------------------------
const DR = { lookback: store.get("lookback", "24h") };
async function openItem(id) {
  S.openId = id;
  const drawer = $("#drawer"), body = $("#drawerBody");
  drawer.hidden = false; $("#scrim").hidden = false;
  body.innerHTML = `<div class="empty">Loading...</div>`;
  let d;
  try { d = await api("/api/item?id=" + id); } catch (e) { body.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const m = d.meta, r = d.row || {};
  const alchP = r.alchProfit;
  body.innerHTML = `
    <div class="drawer-head"><img alt="" src="${esc(iconUrl(m.icon))}" onerror="this.style.visibility='hidden'">
      <div><h2>${esc(m.name)} ${m.members ? '<span class="tag">Members</span>' : '<span class="tag">F2P</span>'} ${r.taxExempt ? '<span class="tag free">No tax</span>' : ""}${signalTag(r)}</h2>
      <div class="muted">${esc(m.examine || "")}</div>
      <div class="small" style="margin-top:4px"><a href="${wikiUrl(m.name)}" target="_blank" rel="noopener">Wiki page</a> · <a href="https://prices.runescape.wiki/osrs/item/${m.id}" target="_blank" rel="noopener">Wiki prices</a> · ID ${m.id}</div></div>
      <button class="icon-btn close" id="drClose" aria-label="Close">✕</button></div>
    ${r.stale ? `<div class="notice">Prices for this item haven't traded in ${ago(r.age)}. Treat them as rough.</div>` : ""}
    <div class="tiles">
      <div class="tile"><div class="k">Instant buy</div><div class="v">${gp(r.high)}</div><div class="s">${r.highTime ? ago(S.now - r.highTime) + " ago" : "-"}</div></div>
      <div class="tile"><div class="k">Instant sell</div><div class="v">${gp(r.low)}</div><div class="s">${r.lowTime ? ago(S.now - r.lowTime) + " ago" : "-"}</div></div>
      <div class="tile"><div class="k">Flip profit</div><div class="v ${signCls(r.profit)}">${gp(r.profit)}</div><div class="s">Margin ${gp(r.margin)} · tax ${gp(r.tax)}</div></div>
      <div class="tile"><div class="k">ROI</div><div class="v">${pct(r.roi, 2)}</div><div class="s">Est. 4h ${short(r.est4h)}</div></div>
      <div class="tile"><div class="k">Buy limit</div><div class="v">${gp(m.limit)}</div><div class="s">Per 4 hours</div></div>
      <div class="tile"><div class="k">Volume</div><div class="v">${short(r.vol24)}</div><div class="s">24h · ${short(r.vol1h)} last hour</div></div>
      <div class="tile"><div class="k">Buy pressure</div><div class="v">${r.buyPressure != null ? pct(r.buyPressure, 0) : "-"}</div><div class="s">${pressureBar(r)} instant buys</div></div>
      <div class="tile"><div class="k">High alch</div><div class="v">${gp(m.highalch)}</div><div class="s ${signCls(alchP)}">${alchP != null ? signed(alchP) + " per cast" : "-"}</div></div>
    </div>
    <div class="chart-card">
      <div class="chart-head"><span class="title">Price history</span>
        <span class="legend"><span><i style="background:var(--series-1)"></i>Instant buy</span><span><i style="background:var(--series-2)"></i>Instant sell</span><span><i style="background:var(--series-1);opacity:.55;height:8px"></i>Volume (lower panel)</span></span>
        <div class="seg" id="drLb">${["6h", "24h", "7d", "30d", "6m", "1y", "local"].map((l) => `<button data-l="${l}" class="${DR.lookback === l ? "on" : ""}" title="${l === "local" ? "Your own saved 5 minute history" : ""}">${l === "local" ? "Saved" : l}</button>`).join("")}</div></div>
      <div id="drChart" style="min-height:330px"><div class="empty">Loading chart...</div></div>
      <div id="drChg" class="small muted"></div>
    </div>
    <div class="actions">
      <button class="btn" id="drWatch">${d.watched ? "★ Watching" : "☆ Watch"}</button>
      <button class="btn" id="drAlertBtn">Add alert</button>
      <button class="btn" id="drFlipBtn">Log a flip</button>
    </div>
    <div id="drForm"></div>
    ${d.alerts.length ? `<h3>Alerts on this item</h3><div class="small">${d.alerts.map((a) => `<div>${esc((ALERT_KINDS[a.kind] || a.kind))} ${a.kind === "spike" ? "" : esc(a.kind.includes("pct") || a.kind.includes("roi") ? a.threshold + "%" : gp(a.threshold))}${a.enabled ? "" : " (paused)"}</div>`).join("")}</div>` : ""}
    ${d.flips.length ? `<h3>Your flips</h3><div class="small">${d.flips.map((f) => `<div>${esc(fmtTime(f.buy_ts, true))}: ${gp(f.qty)} bought at ${gp(f.buy_price)}${f.sell_price != null ? `, sold at ${gp(f.sell_price)}` : " (open)"}</div>`).join("")}</div>` : ""}`;
  $("#drClose").onclick = closeItem;
  $("#drWatch").onclick = () => toggleWatch(id);
  $$("#drLb button").forEach((b) => (b.onclick = () => { DR.lookback = b.dataset.l; store.set("lookback", DR.lookback); $$("#drLb button").forEach((x) => x.classList.toggle("on", x === b)); loadChart(id); }));
  $("#drAlertBtn").onclick = () => {
    $("#drForm").innerHTML = `<div class="card"><div class="inline-form" style="margin-top:0">
      <label class="field wide"><span>When</span><select class="input" id="daKind">${Object.entries(ALERT_KINDS).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></label>
      <label class="field"><span>Value</span><input class="input" id="daTh" value="${r.low || ""}"></label>
      <label class="check"><input type="checkbox" id="daOnce"> Only once</label>
      <button class="btn primary" id="daSave">Save alert</button></div><div id="daMsg" class="small"></div></div>`;
    $("#daSave").onclick = async () => {
      const kind = $("#daKind").value, th = numOr($("#daTh").value, NaN);
      if (kind !== "spike" && !Number.isFinite(th)) { $("#daMsg").innerHTML = `<span class="err">Enter a number.</span>`; return; }
      await api("/api/alerts", { method: "POST", body: { item_id: id, kind, threshold: kind === "spike" ? 0 : th, once: $("#daOnce").checked } });
      openItem(id); pollNotifications();
    };
  };
  $("#drFlipBtn").onclick = () => {
    $("#drForm").innerHTML = `<div class="card"><div class="inline-form" style="margin-top:0">
      <label class="field"><span>Quantity</span><input class="input" id="dfQty" value="${m.limit || ""}"></label>
      <label class="field"><span>Bought at</span><input class="input" id="dfBuy" value="${r.low || ""}"></label>
      <label class="field"><span>Sold at</span><input class="input" id="dfSell" placeholder="Blank if open"></label>
      <button class="btn primary" id="dfSave">Save flip</button></div><div id="dfMsg" class="small"></div></div>`;
    $("#dfSave").onclick = async () => {
      const qty = numOr($("#dfQty").value, NaN), buy = numOr($("#dfBuy").value, NaN), sell = $("#dfSell").value.trim();
      if (!(qty > 0) || !(buy >= 0)) { $("#dfMsg").innerHTML = `<span class="err">Enter a quantity and buy price.</span>`; return; }
      await api("/api/flips", { method: "POST", body: { item_id: id, qty: Math.round(qty), buy_price: Math.round(buy), sell_price: sell ? Math.round(numOr(sell, 0)) : null } });
      openItem(id);
      if (S.tab === "log") renderTab("log");
    };
  };
  loadChart(id);
}
async function loadChart(id) {
  const host = $("#drChart");
  if (!host) return;
  host.innerHTML = `<div class="empty">Loading chart...</div>`;
  try {
    const d = DR.lookback === "local" ? await api(`/api/localhistory?id=${id}&res=5m&days=7`) : await api(`/api/timeseries?id=${id}&lookback=${DR.lookback}`);
    if (S.openId !== id) return;
    const data = d.data || [];
    if (DR.lookback === "local" && !data.length) { host.innerHTML = `<div class="empty">Your saved 5 minute history builds up while the app runs. Check back later.</div>`; $("#drChg").textContent = ""; return; }
    priceChart(host, data);
    const f = data.find((p) => p.avgHighPrice != null), l = [...data].reverse().find((p) => p.avgHighPrice != null);
    $("#drChg").innerHTML = f && l ? `Instant buy changed <b class="${signCls(l.avgHighPrice - f.avgHighPrice)}">${signed(l.avgHighPrice - f.avgHighPrice)} (${pct(l.avgHighPrice / f.avgHighPrice - 1)})</b> over this window.` : "";
  } catch (e) { host.innerHTML = `<div class="notice">Couldn't load the chart: ${esc(e.message)}</div>`; }
}
function closeItem() { $("#drawer").hidden = true; $("#scrim").hidden = true; S.openId = null; $("#tooltip").hidden = true; }
$("#scrim").onclick = closeItem;
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#drawer").hidden) closeItem(); });

// Global search ------------------------------------------------------------------
makePicker($("#globalSearch"), $("#globalResults"), (r) => { $("#globalSearch").value = ""; openItem(r.id); });

// Polling ------------------------------------------------------------------------
function setBell(n) { const b = $("#bellCount"); b.hidden = !n; b.textContent = n; document.title = n ? `(${n}) GE Companion` : "GE Companion"; }
$("#bell").onclick = () => showTab("alerts");

function beep() {
  if (!store.get("sound", true)) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 880; o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0.08, ctx.currentTime); g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
    o.start(); o.stop(ctx.currentTime + 0.35);
  } catch (e) { /* audio blocked */ }
}

async function pollNotifications() {
  try {
    const d = await api("/api/notifications?since=" + S.lastNid);
    const fresh = d.items.filter((x) => x.nid > S.lastNid);
    if (S.lastNid && fresh.length) {
      fresh.slice(0, 4).forEach((x) => toast("Alert", x.message, () => openItem(x.item_id)));
      beep();
    }
    if (d.items.length) S.lastNid = Math.max(S.lastNid, ...d.items.map((x) => x.nid));
    setBell(S.tab === "alerts" ? 0 : d.unseen);
  } catch (e) { /* server offline */ }
}

function setLive() {
  const el = $("#liveStatus"), last = S.status.last_latest;
  const age = last ? Date.now() / 1000 - last : null;
  el.className = "live " + (age == null ? "bad" : age < 180 ? "ok" : age < 600 ? "warn" : "bad");
  $(".txt", el).textContent = age == null ? "No data yet" : "Prices " + ago(age) + " old";
}

async function loadMarket() {
  try {
    const d = await api("/api/market");
    S.rows = d.rows; S.byId = new Map(d.rows.map((r) => [r.id, r])); S.nature = d.natureRune;
    S.watch = new Set(d.watchlist); S.now = d.now; S.status = d.status;
    setLive();
    if (!S.loaded) { S.loaded = true; showTab(S.tab in renderers ? S.tab : "flips"); }
    else if (["flips", "movers", "alch"].includes(S.tab)) renderTab(S.tab, true);
  } catch (e) {
    $("#liveStatus").className = "live bad"; $(".txt", $("#liveStatus")).textContent = "App not running";
  }
}

(async function boot() {
  try { ALERT_KINDS = (await api("/api/alerts")).kinds; } catch (e) { /* ignore */ }
  await loadMarket();
  await pollNotifications();
  setInterval(loadMarket, 30000);
  setInterval(pollNotifications, 15000);
  setInterval(setLive, 5000);
})();
