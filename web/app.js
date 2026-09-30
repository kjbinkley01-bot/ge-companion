/* Bankstanding dashboard. Talks only to the local server at the same origin. */
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
function pct(x, digits = 1) {
  if (x === null || x === undefined) return "-";
  const s = (x * 100).toFixed(digits);
  return (/^-0(\.0*)?$/.test(s) ? s.slice(1) : s) + "%";  // no "-0.00%"
}
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

// Axis labels: time of day for short windows, date and time up to 3 days, date after that.
function axisTime(ts, span) {
  const d = new Date(ts * 1000);
  if (span > 3 * 86400) return d.toLocaleDateString([], { month: "short", day: "numeric" });
  return fmtTime(ts, span > 36 * 3600);
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
  limits: {}, tax: null, fillShare: 0.2,
  // The app opens on Net worth; a page refresh returns to the tab you were on.
  tab: (() => { try { return sessionStorage.getItem("geco.tab") || "networth"; } catch (e) { return "networth"; } })(), lastNid: 0, loaded: false,
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
  const span = t1 - t0, nx = Math.max(2, Math.min(5, Math.floor((W - padL - padR) / 120)));
  const xt = [];
  for (let i = 0; i <= nx; i++) xt.push(t0 + (span * i) / nx);
  host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Price and volume chart">
    ${ticks.map((t) => `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${short(t)}</text>`).join("")}
    <path class="l2" d="${path("avgLowPrice")}"/>
    <path class="l1" d="${path("avgHighPrice")}"/>
    <line class="axis" x1="${padL}" x2="${W - padR}" y1="${vy0}" y2="${vy0}"/>
    ${bars}
    <text x="${padL - 8}" y="${vy(vmax) + 4}" text-anchor="end">${short(vmax)}</text>
    ${xt.map((t, i) => `<text x="${x(t)}" y="${H - 4}" text-anchor="${i === 0 ? "start" : i === nx ? "end" : "middle"}">${axisTime(t, span)}</text>`).join("")}
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

// Single series bar chart (daily flip profit, profit by hour, return histograms).
// Negative bars go below zero. opts: label(v) for axis labels, tip(d) for the tooltip,
// cls(d) for the bar class, empty text, height.
function barChart(host, items, labelKey, valueKey, opts = {}) {
  const W = Math.max(320, host.clientWidth || 600), H = opts.height || 190, padL = 58, padR = 10, padT = 10, padB = 24;
  if (!items.length || (opts.hideEmpty && !items.some((d) => d[valueKey]))) { host.innerHTML = `<div class="empty">${esc(opts.empty || "Closed flips will show up here by day.")}</div>`; return; }
  const label = opts.label || ((s) => String(s).slice(5));
  const tipFn = opts.tip || ((d) => `<div class="muted">${esc(d[labelKey])}</div><b class="${signCls(d[valueKey])}">${signed(d[valueKey])} gp</b>`);
  const clsFn = opts.cls || ((d) => (d[valueKey] < 0 ? "neg" : "posb"));
  const vals = items.map((d) => d[valueKey]);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
  const ymin = ticks[0], ymax = ticks[ticks.length - 1];
  const y = (v) => padT + (H - padT - padB) * (1 - (v - ymin) / (ymax - ymin || 1));
  const slot = (W - padL - padR) / items.length, bw = Math.max(2, Math.min(28, slot - 2));
  const bars = items.map((d, i) => {
    const v = d[valueKey], cx = padL + slot * (i + 0.5), top = y(Math.max(0, v)), bot = y(Math.min(0, v));
    return `<rect class="bar ${clsFn(d)}" data-i="${i}" x="${cx - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, bot - top)}" rx="3"/>`;
  }).join("");
  const every = Math.ceil(items.length / (opts.maxLabels || 8));
  host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(opts.aria || "Bar chart")}">
    ${ticks.map((t) => `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${short(t)}</text>`).join("")}
    <line class="axis" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>
    ${bars}
    ${items.map((d, i) => i % every ? "" : `<text x="${padL + slot * (i + 0.5)}" y="${H - 6}" text-anchor="middle">${esc(label(d[labelKey]))}</text>`).join("")}
  </svg>`;
  const tip = $("#tooltip");
  $$("rect.bar", host).forEach((r) => {
    r.addEventListener("mousemove", (e) => {
      tip.innerHTML = tipFn(items[+r.dataset.i]);
      tip.hidden = false; tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px";
    });
    r.addEventListener("mouseleave", () => (tip.hidden = true));
  });
}

// Line chart over time for one to three series on one shared axis (indices, equity,
// net worth, forecasts). series: [{name, cls: "l1" | "l2" | "l3", color, pts: [{t, v}],
// dash, band: [{t, lo, hi}]}]. A band is drawn as a shaded range behind its line.
// With two or more series there is a legend and each line is labelled at its end.
function lineChart(host, series, opts = {}) {
  series = series.filter((s) => s.pts && s.pts.length >= 2);
  const W = Math.max(320, host.clientWidth || 600), H = opts.height || 230, multi = series.length > 1;
  const endLabels = multi && !opts.noEndLabels;
  const padL = 62, padR = endLabels ? 96 : 14, padT = 10, padB = 22;
  const all = series.flatMap((s) => s.pts.concat((s.band || []).flatMap((b) => [{ t: b.t, v: b.lo }, { t: b.t, v: b.hi }])));
  if (!all.length) { host.innerHTML = `<div class="empty">${esc(opts.empty || "No data yet.")}</div>`; return; }
  const fmt = opts.fmt || short;
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t));
  let ymin = Math.min(...all.map((p) => p.v)), ymax = Math.max(...all.map((p) => p.v));
  if (opts.base != null) { ymin = Math.min(ymin, opts.base); ymax = Math.max(ymax, opts.base); }
  if (opts.zero) { ymin = Math.min(0, ymin); ymax = Math.max(0, ymax); }
  const pad = (ymax - ymin) * 0.08 || Math.abs(ymax) * 0.02 || 1;
  if (!opts.zero) { ymin -= pad; ymax += pad; }
  const ticks = niceTicks(ymin, ymax, 4); ymin = ticks[0]; ymax = ticks[ticks.length - 1];
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (W - padL - padR);
  const y = (v) => padT + (H - padT - padB) * (1 - (v - ymin) / (ymax - ymin || 1));
  const path = (pts) => pts.map((p, i) => (i ? "L" : "M") + x(p.t).toFixed(1) + "," + y(p.v).toFixed(1)).join("");
  const span = t1 - t0, xt = [], nx = Math.max(2, Math.min(5, Math.floor((W - padL - padR) / 130)));
  for (let i = 0; i <= nx; i++) xt.push(t0 + (span * i) / nx);
  // End labels, nudged apart so they never overlap.
  const ends = series.map((s) => ({ s, yy: y(s.pts[s.pts.length - 1].v) })).sort((a, b) => a.yy - b.yy);
  for (let i = 1; i < ends.length; i++) if (ends[i].yy - ends[i - 1].yy < 13) ends[i].yy = ends[i - 1].yy + 13;
  host.innerHTML = `${multi ? `<div class="legend-inline" style="margin-bottom:4px">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : ""}
    <svg class="chart" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(opts.aria || "Line chart")}">
    ${ticks.map((t) => `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end">${fmt(t)}</text>`).join("")}
    ${opts.base != null ? `<line class="base" x1="${padL}" x2="${W - padR}" y1="${y(opts.base)}" y2="${y(opts.base)}"/>` : ""}
    ${series.map((s) => s.band && s.band.length > 1 ? `<path d="${s.band.map((b, i) => (i ? "L" : "M") + x(b.t).toFixed(1) + "," + y(b.hi).toFixed(1)).join("")}${s.band.slice().reverse().map((b) => "L" + x(b.t).toFixed(1) + "," + y(b.lo).toFixed(1)).join("")}Z" style="fill:${s.color};opacity:.16"/>` : "").join("")}
    ${series.map((s) => `<path class="${s.cls}" d="${path(s.pts)}"${s.dash ? ` stroke-dasharray="6 4"` : ""}/>`).join("")}
    ${endLabels ? ends.map((e) => `<text class="endlbl" x="${W - padR + 6}" y="${e.yy + 4}">${esc(e.s.name.length > 13 ? e.s.name.slice(0, 12) + "." : e.s.name)}</text>`).join("") : ""}
    ${xt.map((t, i) => `<text x="${x(t)}" y="${H - 4}" text-anchor="${i === 0 ? "start" : i === nx ? "end" : "middle"}">${axisTime(t, span)}</text>`).join("")}
    <g class="hover" visibility="hidden"><line class="xhair" y1="${padT}" y2="${H - padB}"/>${series.map((s, i) => `<circle r="4" data-s="${i}" style="fill:${s.color};stroke:var(--surface);stroke-width:2"/>`).join("")}</g>
    <rect x="${padL}" y="${padT}" width="${W - padL - padR}" height="${H - padT - padB}" fill="transparent" class="hit"/>
  </svg>`;
  const svg = $("svg", host), g = $(".hover", svg), tip = $("#tooltip");
  const nearest = (pts, t) => { let b = pts[0]; for (const p of pts) if (Math.abs(p.t - t) < Math.abs(b.t - t)) b = p; return b; };
  $(".hit", svg).addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    const t = t0 + ((((e.clientX - rect.left) / rect.width) * W - padL) / (W - padL - padR)) * (t1 - t0);
    const near = series.map((s) => nearest(s.pts, t));
    // Only series that actually have a point near the cursor (history vs forecast ranges differ).
    const gapOf = (s) => (s.pts[s.pts.length - 1].t - s.pts[0].t) / Math.max(1, s.pts.length - 1);
    const live = series.map((s, i) => Math.abs(near[i].t - t) <= gapOf(s) * 0.75);
    if (!live.some(Boolean)) { let bi = 0; near.forEach((p, i) => { if (Math.abs(p.t - t) < Math.abs(near[bi].t - t)) bi = i; }); live[bi] = true; }
    const first = near[live.indexOf(true)];
    const px = x(first.t);
    g.setAttribute("visibility", "visible");
    $(".xhair", g).setAttribute("x1", px); $(".xhair", g).setAttribute("x2", px);
    $$("circle", g).forEach((c) => { const i = +c.dataset.s, p = near[i]; c.style.display = live[i] ? "" : "none"; c.setAttribute("cx", x(p.t)); c.setAttribute("cy", y(p.v)); });
    const tf = opts.tipFmt || fmt;
    tip.innerHTML = `<div class="muted">${esc(fmtTime(first.t, true))}</div>` + series.map((s, i) => {
      if (!live[i]) return "";
      const b = s.band && s.band.find((q) => q.t === near[i].t);
      return `<div class="r"><span>${multi ? `<i style="background:${s.color}"></i>` : ""}${esc(s.name)}</span><b>${tf(near[i].v)}</b></div>` + (b ? `<div class="r"><span class="muted">Likely range</span><span>${tf(b.lo)} to ${tf(b.hi)}</span></div>` : "");
    }).join("");
    tip.hidden = false;
    tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px";
  });
  $(".hit", svg).addEventListener("mouseleave", () => { g.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

// Weekday by hour heatmap of price vs each day's own average. Diverging scale:
// blue is cheaper than usual, red is pricier, gray is the day's average.
const WDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function hourLabel(h) { return new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric" }); }
function heatmap(host, grid) {
  const W = Math.max(360, host.clientWidth || 640), padL = 40, padT = 16;
  const cw = (W - padL - 4) / 24, ch = 22, H = padT + ch * 7 + 4;
  const devs = grid.flat().map((c) => c.dev).filter((v) => v != null);
  if (!devs.length) { host.innerHTML = `<div class="empty">Not enough hourly history yet. This fills in after a few days of running.</div>`; return; }
  const maxAbs = Math.max(0.002, ...devs.map(Math.abs));
  const fill = (d) => { const t = Math.round(Math.min(1, Math.abs(d) / maxAbs) * 100); return `color-mix(in oklab, var(${d < 0 ? "--div-neg" : "--div-pos"}) ${t}%, var(--div-mid))`; };
  let cells = "";
  grid.forEach((row, wd) => row.forEach((c, h) => {
    const xx = padL + h * cw, yy = padT + wd * ch;
    cells += c.dev == null ? `<rect class="cell empty" x="${xx}" y="${yy}" width="${cw}" height="${ch}" rx="3"/>`
      : `<rect class="cell" data-w="${wd}" data-h="${h}" x="${xx}" y="${yy}" width="${cw}" height="${ch}" rx="3" style="fill:${fill(c.dev)}"/>`;
  }));
  host.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Price by weekday and hour">
    ${[0, 3, 6, 9, 12, 15, 18, 21].map((h) => `<text x="${padL + h * cw + cw / 2}" y="11" text-anchor="middle">${esc(hourLabel(h))}</text>`).join("")}
    ${WDAYS.map((d, i) => `<text x="${padL - 6}" y="${padT + i * ch + ch / 2 + 4}" text-anchor="end">${d}</text>`).join("")}
    ${cells}</svg>
    <div class="heat-legend"><span>Cheaper</span><span class="ramp"></span><span>Pricier</span><span class="muted">than that day's average (scale ±${pct(maxAbs)})</span></div>`;
  const tip = $("#tooltip");
  $$("rect.cell[data-w]", host).forEach((r) => {
    r.addEventListener("mousemove", (e) => {
      const c = grid[+r.dataset.w][+r.dataset.h];
      tip.innerHTML = `<div class="muted">${WDAYS[+r.dataset.w]} ${esc(hourLabel(+r.dataset.h))}</div>
        <div class="r"><span>vs day average</span><b>${c.dev > 0 ? "+" : ""}${pct(c.dev, 2)}</b></div>
        <div class="r"><span>Avg margin</span><b>${gp(c.margin)}</b></div>
        <div class="r"><span>Avg volume</span><b>${short(c.vol)}</b></div>
        <div class="r"><span class="muted">Days sampled</span><span>${c.n}</span></div>`;
      tip.hidden = false; tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px";
    });
    r.addEventListener("mouseleave", () => (tip.hidden = true));
  });
}

// Tiny trend line for tables, from plain numbers.
function miniLine(vals, W = 110, H = 28) {
  vals = vals.filter((v) => v != null);
  if (vals.length < 2) return `<span class="muted small">-</span>`;
  const mn = Math.min(...vals), mx = Math.max(...vals);
  const d = vals.map((v, i) => (i ? "L" : "M") + ((i / (vals.length - 1)) * (W - 2) + 1).toFixed(1) + "," + (H - 3 - ((v - mn) / (mx - mn || 1)) * (H - 6)).toFixed(1)).join("");
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${d}"/></svg>`;
}

// GE tax on the client, mirroring geco/market.py (2%, rounded down, capped, exemptions).
function taxOf(price, row) {
  if (!price || (row && row.taxExempt)) return 0;
  const t = S.tax || { bp: 200, cap: 5000000 };
  return Math.min(Math.floor((price * t.bp) / 10000), t.cap);
}
function breakeven(buy, row) {
  if (!buy) return null;
  let lo = Math.round(buy), hi = Math.round(buy) * 2 + 2;
  while (lo < hi) { const mid = Math.floor((lo + hi) / 2); if (mid - taxOf(mid, row) >= buy) hi = mid; else lo = mid + 1; }
  return lo;
}
function stabCell(r) {
  if (r.stability == null) return `<span class="muted">-</span>`;
  const p = Math.round(r.stability * 100);
  return `<span class="stab" title="Margin was profitable after tax in ${p}% of recent 5 minute windows${r.volatility != null ? `. Price volatility ${pct(r.volatility, 2)}` : ""}"><span class="bar"><i style="width:${p}%"></i></span>${p}%</span>`;
}
function limitLeft(r) {
  const l = S.limits && S.limits[r.id];
  if (!l) return gp(r.limit);
  return `<span title="Bought ${gp(l.used)} this window, resets in ${ago(l.resetAt - Date.now() / 1000)}">${gp(l.left)} <span class="muted small">/ ${gp(r.limit)}</span></span>`;
}
function fillCell(r) {
  if (r.fillHrs == null) return "-";
  return `<span title="Hours to buy and sell one full limit at your share of volume">${r.fillHrs < 1 ? "<1" : r.fillHrs < 10 ? r.fillHrs.toFixed(1) : Math.round(r.fillHrs)}h</span>`;
}
function tipAt(e, html) { const tip = $("#tooltip"); tip.innerHTML = html; tip.hidden = false; tip.style.left = Math.min(window.innerWidth - 220, e.clientX + 14) + "px"; tip.style.top = (e.clientY + 14) + "px"; }

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
  S.tab = name;
  try { sessionStorage.setItem("geco.tab", name); } catch (e) { /* storage unavailable */ }
  document.body.classList.remove("nav-open"); $("#navScrim").hidden = true;
  $$("#tabs button[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => (s.hidden = s.id !== "tab-" + name));
  renderTab(name);
}
function renderTab(name, soft) {
  const fn = renderers[name];
  if (fn) fn($("#tab-" + name), soft);
}
$$("#tabs button[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
// Side menu: collapse to icons on desktop, slide in over the page on small screens.
if (store.get("navCollapsed", false)) document.body.classList.add("nav-collapsed");
$("#navCollapse").onclick = () => { const c = document.body.classList.toggle("nav-collapsed"); store.set("navCollapsed", c); window.dispatchEvent(new Event("resize")); };
$("#navBtn").onclick = () => { const o = document.body.classList.toggle("nav-open"); $("#navScrim").hidden = !o; };
$("#navScrim").onclick = () => { document.body.classList.remove("nav-open"); $("#navScrim").hidden = true; };

// Flip finder --------------------------------------------------------------------
const FF = Object.assign({
  q: "", maxPrice: "", minProfit: "", minRoi: "", minVol: "500", minStab: "", members: "all",
  hideStale: true, hideTraps: true, preset: "all", show: 100, sort: { key: "adj4h", dir: "desc" },
  cash: "", slots: "8",
}, store.get("ff", {}));
FF.show = 100;
const PRESETS = {
  all: { label: "All", apply: {} },
  reliable: { label: "Reliable", apply: { minVol: "2000", minStab: "60", minProfit: "1", sort: { key: "adj4h", dir: "desc" } } },
  volume: { label: "High volume", apply: { minVol: "20000", maxPrice: "", minProfit: "1", minRoi: "", sort: { key: "est4h", dir: "desc" } } },
  bigticket: { label: "Big ticket", apply: { minVol: "20", maxPrice: "", minProfit: "100000", minRoi: "", sort: { key: "profit", dir: "desc" } } },
  lowrisk: { label: "Steady margins", apply: { minVol: "5000", maxPrice: "5000000", minProfit: "", minRoi: "1", minStab: "40", sort: { key: "roi", dir: "desc" } } },
  cheap: { label: "Under 100k", apply: { minVol: "1000", maxPrice: "100000", minProfit: "1", minRoi: "", sort: { key: "adj4h", dir: "desc" } } },
};

function flagTags(r) {
  return signalTag(r)
    + (r.trap ? ` <span class="tag trap" title="${r.gap != null && r.gap > 900 ? `The two prices traded ${ago(r.gap)} apart, so this margin may not exist right now` : "Recent 5 minute averages show no margin after tax; the latest prices may be a one-off"}">Trap?</span>` : "")
    + (r.stale ? ` <span class="tag stale">Stale</span>` : "")
    + (r.taxExempt ? ` <span class="tag free">No tax</span>` : "");
}

renderers.flips = function (host, soft) {
  if (!soft || !$("#ffTable", host)) {
    host.innerHTML = `
      <h2>Flip finder</h2>
      <p class="lede">Buy near the instant-sell price, sell near the instant-buy price. Profit is after the 2% GE tax. <b>Est. 4h</b> is profit per item times what you can realistically fill in one buy limit window: your share (${pct(S.fillShare || 0.2, 0)}, set in Settings) of the instant-sell volume you buy from and the instant-buy volume you sell into. <b>Adj. 4h</b> scales that by margin stability, the share of recent 5 minute windows where the flip actually worked.</p>
      <div class="chips" id="ffPresets">${Object.entries(PRESETS).map(([k, p]) => `<button class="chip ${FF.preset === k ? "on" : ""}" data-p="${k}">${p.label}</button>`).join("")}</div>
      <div class="filters">
        <label class="field wide"><span>Filter by name</span><input class="input" id="ffQ" value="${esc(FF.q)}" placeholder="e.g. rune, potion"></label>
        <label class="field"><span>Max buy price</span><input class="input" id="ffMax" inputmode="numeric" value="${esc(FF.maxPrice)}" placeholder="Any"></label>
        <label class="field"><span>Min profit / item</span><input class="input" id="ffMinP" inputmode="numeric" value="${esc(FF.minProfit)}" placeholder="Any"></label>
        <label class="field"><span>Min ROI %</span><input class="input" id="ffRoi" inputmode="decimal" value="${esc(FF.minRoi)}" placeholder="Any"></label>
        <label class="field"><span>Min 24h volume</span><input class="input" id="ffVol" inputmode="numeric" value="${esc(FF.minVol)}" placeholder="Any"></label>
        <label class="field"><span>Min stability %</span><input class="input" id="ffStab" inputmode="numeric" value="${esc(FF.minStab)}" placeholder="Any"></label>
        <label class="field"><span>Members</span><select class="input" id="ffMem"><option value="all">All items</option><option value="f2p">F2P only</option><option value="p2p">Members only</option></select></label>
        <label class="check"><input type="checkbox" id="ffStale" ${FF.hideStale ? "checked" : ""}> Hide stale prices</label>
        <label class="check" title="Margins whose two prices traded far apart, or that recent history says don't hold"><input type="checkbox" id="ffTraps" ${FF.hideTraps ? "checked" : ""}> Hide margin traps</label>
      </div>
      <details class="card" id="ffPlanCard" style="margin-bottom:12px" ${store.get("planOpen", false) ? "open" : ""}><summary>Slot planner</summary>
        <p class="small muted" style="margin:8px 0">Fills your GE slots with the flips above (same filters) that make the most in one 4 hour window with the cash you have, respecting buy limits you have already used.</p>
        <div class="inline-form" style="margin-top:0">
          <label class="field"><span>Cash to flip with</span><input class="input" id="ffCash" value="${esc(FF.cash)}" placeholder="e.g. 50m"></label>
          <label class="field"><span>GE slots</span><input class="input" id="ffSlots" value="${esc(FF.slots)}" inputmode="numeric"></label>
        </div>
        <div id="ffPlan" style="margin-top:10px"></div>
      </details>
      <div id="ffInfo" class="muted small" style="margin-bottom:6px"></div>
      <div class="table-wrap" id="ffTable"></div>`;
    $("#ffMem", host).value = FF.members;
    const bind = (id, key, ev = "input") => $(id, host).addEventListener(ev, (e) => {
      FF[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      if (!["cash", "slots"].includes(key)) { FF.preset = "custom"; FF.show = 100; $$("#ffPresets .chip", host).forEach((c) => c.classList.remove("on")); }
      store.set("ff", FF); drawFlips(host);
    });
    bind("#ffQ", "q"); bind("#ffMax", "maxPrice"); bind("#ffMinP", "minProfit"); bind("#ffRoi", "minRoi");
    bind("#ffVol", "minVol"); bind("#ffStab", "minStab"); bind("#ffMem", "members", "change");
    bind("#ffStale", "hideStale", "change"); bind("#ffTraps", "hideTraps", "change");
    bind("#ffCash", "cash"); bind("#ffSlots", "slots");
    $("#ffPlanCard", host).addEventListener("toggle", (e) => { store.set("planOpen", e.target.open); drawFlips(host); });
    $$("#ffPresets .chip", host).forEach((c) => c.addEventListener("click", () => {
      const p = PRESETS[c.dataset.p];
      Object.assign(FF, { minVol: "", maxPrice: "", minProfit: "", minRoi: "", minStab: "" }, JSON.parse(JSON.stringify(p.apply)));
      FF.preset = c.dataset.p; FF.show = 100; store.set("ff", FF);
      renderers.flips(host, false);
    }));
  }
  drawFlips(host);
};

function numOr(v, d) { const n = parseFloat(String(v).replace(/[, ]/g, "").replace(/k$/i, "e3").replace(/m$/i, "e6").replace(/b$/i, "e9")); return Number.isFinite(n) ? n : d; }

function filteredFlips() {
  const q = FF.q.trim().toLowerCase();
  const maxP = numOr(FF.maxPrice, Infinity), minP = numOr(FF.minProfit, -Infinity);
  const minR = numOr(FF.minRoi, -Infinity) / 100, minV = numOr(FF.minVol, 0), minS = numOr(FF.minStab, -1) / 100;
  return S.rows.filter((r) => r.profit != null && r.profit > 0 && r.low != null
    && (!q || r.name.toLowerCase().includes(q)) && r.low <= maxP && r.profit >= minP
    && (r.roi ?? -1) >= minR && (r.vol24 || r.vol1h * 24) >= minV
    && (minS < 0 || (r.stability ?? -1) >= minS)
    && (FF.members === "all" || (FF.members === "f2p" ? !r.members : r.members))
    && (!FF.hideStale || !r.stale) && (!FF.hideTraps || !r.trap));
}

// Greedy slot planner: repeatedly take the flip that adds the most expected profit
// with the cash that is left, one item per slot.
function planSlots(rows, cash, slots) {
  const picks = [];
  let left = cash;
  const pool = rows.filter((r) => !r.trap && r.estQty > 0 && r.low > 0);
  while (picks.length < slots && left > 0) {
    let best = null;
    for (const r of pool) {
      if (picks.some((p) => p.r.id === r.id)) continue;
      const lim = S.limits && S.limits[r.id] ? S.limits[r.id].left : Infinity;
      const qty = Math.min(r.estQty, lim ?? Infinity, Math.floor(left / r.low));
      if (qty <= 0) continue;
      const value = r.profit * qty * (r.stability != null ? r.stability : 0.5);
      if (!best || value > best.value) best = { r, qty, value };
    }
    if (!best) break;
    picks.push(best);
    left -= best.qty * best.r.low;
  }
  return { picks, left };
}

function drawPlan(host, rows) {
  const box = $("#ffPlan", host);
  if (!box || !$("#ffPlanCard", host).open) return;
  const cash = numOr(FF.cash, 0), slots = Math.max(1, Math.min(8, Math.round(numOr(FF.slots, 8))));
  if (!cash) { box.innerHTML = `<div class="muted small">Enter the cash you want to flip with.</div>`; return; }
  const { picks, left } = planSlots(rows, cash, slots);
  if (!picks.length) { box.innerHTML = `<div class="muted small">Nothing fits that budget with the current filters.</div>`; return; }
  const tot = picks.reduce((a, p) => a + p.r.profit * p.qty, 0), adj = picks.reduce((a, p) => a + p.value, 0);
  box.innerHTML = `<div class="table-wrap"><table>${thead([{ label: "Slot" }, { label: "Item" }, { label: "Buy qty", num: 1 }, { label: "Buy at", num: 1 }, { label: "Sell at", num: 1 }, { label: "Cost", num: 1 }, { label: "Profit if filled", num: 1 }, { label: "Stability", num: 1 }, { label: "Fill", num: 1 }], null)}<tbody>${picks.map((p, i) => `
    <tr data-id="${p.r.id}"><td class="muted">${i + 1}</td><td>${itemCell(p.r)}</td><td class="num">${gp(p.qty)}</td><td class="num">${gp(p.r.low)}</td><td class="num">${gp(p.r.high)}</td><td class="num">${short(p.qty * p.r.low)}</td><td class="num pos"><b>${short(p.r.profit * p.qty)}</b></td><td class="num">${stabCell(p.r)}</td><td class="num">${fillCell(p.r)}</td></tr>`).join("")}</tbody></table></div>
    <p class="small" style="margin:8px 0 0">Uses <b>${short(cash - left)}</b> of ${short(cash)}. Up to <b class="pos">${short(tot)}</b> profit if every offer fills, about <b>${short(adj)}</b> after margin stability.</p>`;
  bindRowClicks(box);
}

function drawFlips(host) {
  let rows = filteredFlips();
  rows = sortRows(rows, FF.sort.key, FF.sort.dir);
  const cols = [
    { label: "", }, { label: "Item", sort: "name" },
    { label: "Buy at", sort: "low", num: 1, title: "Latest instant-sell price" },
    { label: "Sell at", sort: "high", num: 1, title: "Latest instant-buy price" },
    { label: "Tax", sort: "tax", num: 1 },
    { label: "Profit", sort: "profit", num: 1, title: "Per item, after tax" },
    { label: "ROI", sort: "roi", num: 1 },
    { label: "Limit", sort: "limit", num: 1, title: "GE buy limit per 4 hours (what's left if you logged buys)" },
    { label: "24h vol", sort: "vol24", num: 1 },
    { label: "Fill", sort: "fillHrs", num: 1, title: "Hours to buy and sell one full limit at your share of volume" },
    { label: "Stability", sort: "stability", num: 1, title: "Share of recent 5 minute windows where this margin was profitable after tax" },
    { label: "Est. 4h", sort: "est4h", num: 1, title: "Profit x realistic fill for one buy limit window" },
    { label: "Adj. 4h", sort: "adj4h", num: 1, title: "Est. 4h x margin stability (traps count a quarter)" },
    { label: "Pressure", title: "Share of last hour trades that were instant buys" },
    { label: "Updated", sort: "age", num: 1 },
  ];
  const shown = rows.slice(0, FF.show);
  const warm = S.status.m5Windows != null && S.status.m5Windows < 6 ? " · Stability needs 30 minutes of 5 minute history" : "";
  $("#ffInfo", host).textContent = `${rows.length.toLocaleString()} items match` + (S.status.backfill && S.status.backfill !== "done" ? ` · Loading history (${S.status.backfill}), volumes fill in shortly` : "") + warm;
  $("#ffTable", host).innerHTML = rows.length ? `<table>${thead(cols, FF.sort)}<tbody>${shown.map((r) => `
    <tr data-id="${r.id}">
      <td>${star(r.id)}</td>
      <td>${itemCell(r, flagTags(r))}</td>
      <td class="num">${gp(r.low)}</td><td class="num">${gp(r.high)}</td>
      <td class="num muted">${gp(r.tax)}</td>
      <td class="num pos"><b>${gp(r.profit)}</b></td>
      <td class="num">${pct(r.roi, 2)}</td>
      <td class="num">${limitLeft(r)}</td>
      <td class="num">${short(r.vol24)}</td>
      <td class="num">${fillCell(r)}</td>
      <td class="num">${stabCell(r)}</td>
      <td class="num">${short(r.est4h)}</td>
      <td class="num"><b>${short(r.adj4h)}</b></td>
      <td>${pressureBar(r)}</td>
      <td class="num muted">${ago(r.age)}</td>
    </tr>`).join("")}</tbody></table>
    ${rows.length > FF.show ? `<div class="more-row"><button class="btn small" id="ffMore">Show 100 more</button></div>` : ""}`
    : `<div class="empty">No items match these filters. Try loosening them.</div>`;
  bindSort($("#ffTable", host), FF.sort, () => { store.set("ff", FF); drawFlips(host); });
  bindRowClicks($("#ffTable", host));
  const more = $("#ffMore", host);
  if (more) more.onclick = () => { FF.show += 100; drawFlips(host); };
  drawPlan(host, rows);
}

// Market overview ----------------------------------------------------------------
const MK = Object.assign({ days: 7, cat: "runes" }, store.get("mk", {}));
let MK_DATA = null;
renderers.market = function (host, soft) {
  if (!soft || !$("#mkTiles", host)) {
    host.innerHTML = `<h2>Market</h2>
      <p class="lede">How the whole Grand Exchange is moving. Each index starts at 100 and tracks its items' prices weighted by gp traded (no item above 20% of its basket), so you can tell a market wide move from one item's story. Built from your saved hourly history.</p>
      <div class="tiles" id="mkTiles"></div>
      <div class="chart-card"><div class="chart-head"><span class="title">Market heatmap</span><span class="muted small">The 200 biggest markets by gp traded, grouped by class. Click one to open it in the Terminal.</span></div><div id="mkHeat"></div></div>
      <div class="chart-card">
        <div class="chart-head"><span class="title" id="mkTitle">Index</span>
          <select class="input" id="mkCat" style="width:auto"></select>
          <div class="seg" id="mkDays">${[[7, "7d"], [30, "30d"], [90, "90d"]].map(([d, l]) => `<button data-d="${d}" class="${MK.days === d ? "on" : ""}">${l}</button>`).join("")}</div></div>
        <div id="mkChart" style="min-height:250px"><div class="empty">Loading...</div></div>
      </div>
      <div class="grid2">
        <div><h3>Categories</h3><div class="table-wrap" id="mkCats"></div></div>
        <div><h3>Biggest markets (24h gp traded)</h3><div class="table-wrap" id="mkBig"></div></div>
      </div>`;
    $$("#mkDays button", host).forEach((b) => (b.onclick = () => { MK.days = +b.dataset.d; store.set("mk", MK); $$("#mkDays button", host).forEach((x) => x.classList.toggle("on", x === b)); loadIndices(host); }));
    $("#mkCat", host).onchange = (e) => { MK.cat = e.target.value; store.set("mk", MK); drawIndexChart(host); };
    loadIndices(host);
  }
  if (!soft || !$("#mkHeat svg", host)) {
    api("/api/heatmap?n=200").then((d) => {
      const groups = {};
      d.items.forEach((i) => { (groups[i.category] = groups[i.category] || []).push(Object.assign({}, i, { extra: `Price ${pfmt(i.price)} · 7d ${i.chg7d == null ? "-" : sgnPct(i.chg7d, 1)}` })); });
      treemap($("#mkHeat", host), Object.entries(groups).map(([name, items]) => ({ name, items })), { height: 420, range: 0.05, valueLabel: "24h gp traded", legend: "Size: 24h gp traded. Color: 24h change.", onPick: (x) => openTerminal(x.id), aria: "Market heatmap" });
    }).catch(() => {});
  }
  drawMarketTiles(host);
};
function drawMarketTiles(host) {
  const liquid = S.rows.filter((r) => (r.vol24 || 0) >= 1000 && r.high && r.low);
  const gpTraded = liquid.reduce((a, r) => a + r.vol24 * (r.high + r.low) / 2, 0);
  const moves = liquid.map((r) => r.chg24h).filter((v) => v != null).sort((a, b) => a - b);
  const up = moves.filter((v) => v > 0.001).length, down = moves.filter((v) => v < -0.001).length;
  const med = moves.length ? moves[Math.floor(moves.length / 2)] : null;
  const spikes = S.rows.filter((r) => r.signal).length;
  $("#mkTiles", host).innerHTML = `
    <div class="tile"><div class="k">GP traded 24h</div><div class="v">${short(gpTraded)}</div><div class="s">${gp(liquid.length)} liquid items</div></div>
    <div class="tile"><div class="k">Advancers / decliners</div><div class="v"><span class="pos">${up}</span> / <span class="neg">${down}</span></div><div class="s">24h, items with 1k+ volume</div></div>
    <div class="tile"><div class="k">Median 24h move</div><div class="v ${signCls(med)}">${med == null ? "-" : (med > 0 ? "+" : "") + pct(med, 2)}</div><div class="s">Typical liquid item</div></div>
    <div class="tile"><div class="k">Volume spikes</div><div class="v">${spikes}</div><div class="s">Last hour 4x normal</div></div>`;
  const big = liquid.map((r) => Object.assign({ gpv: r.vol24 * (r.high + r.low) / 2 }, r)).sort((a, b) => b.gpv - a.gpv).slice(0, 15);
  const bigHost = $("#mkBig", host);
  if (bigHost) {
    bigHost.innerHTML = big.length ? `<table>${thead([{ label: "Item" }, { label: "GP traded", num: 1 }, { label: "Price", num: 1 }, { label: "24h", num: 1 }], null)}<tbody>${big.map((r) => `<tr data-id="${r.id}"><td>${itemCell(r)}</td><td class="num">${short(r.gpv)}</td><td class="num">${gp(r.high)}</td><td class="num ${signCls(r.chg24h)}">${r.chg24h != null ? (r.chg24h > 0 ? "+" : "") + pct(r.chg24h) : "-"}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">Volumes fill in once history loads.</div>`;
    bindRowClicks(bigHost);
  }
}
async function loadIndices(host) {
  $("#mkChart", host).innerHTML = `<div class="empty">Loading...</div>`;
  try { MK_DATA = await api("/api/indices?days=" + MK.days); } catch (e) { $("#mkChart", host).innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const cats = MK_DATA.categories;
  const sel = $("#mkCat", host);
  sel.innerHTML = cats.map((c) => `<option value="${c.key}">${esc(c.label)}</option>`).join("");
  if (!cats.some((c) => c.key === MK.cat)) MK.cat = cats[0] ? cats[0].key : "market";
  sel.value = MK.cat;
  drawIndexChart(host);
  $("#mkCats", host).innerHTML = cats.length ? `<table>${thead([{ label: "Index" }, { label: "Items", num: 1 }, { label: MK.days + "d trend" }, { label: "24h", num: 1 }, { label: MK.days + "d", num: 1 }, { label: "Biggest weight" }], null)}<tbody>${cats.map((c) => `
    <tr data-cat="${c.key}" class="${c.key === MK.cat ? "" : ""}"><td><b>${esc(c.label)}</b></td><td class="num">${c.items}</td><td>${miniLine(c.series.map((p) => p.v))}</td>
    <td class="num ${signCls(c.chg24)}">${c.chg24 != null ? (c.chg24 > 0 ? "+" : "") + pct(c.chg24, 2) : "-"}</td>
    <td class="num ${signCls(c.change)}"><b>${c.change != null ? (c.change > 0 ? "+" : "") + pct(c.change, 2) : "-"}</b></td>
    <td class="small muted">${c.top.slice(0, 3).map((t) => esc(t.name)).join(", ")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No hourly history yet. The index fills in as the app runs.</div>`;
  $$("#mkCats tr[data-cat]", host).forEach((tr) => tr.addEventListener("click", () => { MK.cat = tr.dataset.cat; store.set("mk", MK); sel.value = MK.cat; drawIndexChart(host); window.scrollTo({ top: 0, behavior: "smooth" }); }));
}
function drawIndexChart(host) {
  if (!MK_DATA) return;
  const cat = MK_DATA.categories.find((c) => c.key === MK.cat), mk = MK_DATA.categories.find((c) => c.key === "market");
  if (!cat) { $("#mkChart", host).innerHTML = `<div class="empty">No history yet for this index.</div>`; return; }
  $("#mkTitle", host).textContent = cat.label;
  const series = [{ name: cat.key === "market" ? "Market" : cat.label, cls: "l1", color: "var(--series-1)", pts: cat.series }];
  if (mk && cat.key !== "market") series.push({ name: "Market", cls: "l2", color: "var(--series-2)", pts: mk.series });
  lineChart($("#mkChart", host), series, { base: 100, fmt: (v) => v.toFixed(1), aria: cat.label + " index", height: 240 });
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
  const cols = [{ label: "" }, { label: "Item" }, { label: "24h" }, { label: "Buy at", num: 1 }, { label: "Sell at", num: 1 }, { label: "Profit", num: 1 }, { label: "ROI", num: 1 }, { label: "Stability", num: 1 }, { label: "1h", num: 1 }, { label: "24h", num: 1 }, { label: "24h vol", num: 1 }, { label: "Pressure" }, { label: "Updated", num: 1 }];
  const draw = () => {
    $("#wlTable", host).innerHTML = `<table>${thead(cols, null)}<tbody>${rows.map((r) => `
      <tr data-id="${r.id}"><td>${star(r.id)}</td><td>${itemCell(r, flagTags(r))}</td><td>${sparkCache.has(r.id) ? sparkline(sparkCache.get(r.id)) : ""}</td>
      <td class="num">${gp(r.low)}</td><td class="num">${gp(r.high)}</td><td class="num ${signCls(r.profit)}">${gp(r.profit)}</td><td class="num">${pct(r.roi, 2)}</td><td class="num">${stabCell(r)}</td>
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
let ALERT_KINDS = {}, PCT_KINDS = new Set(["roi_above", "move_pct", "drop_pct", "rise_pct", "stability_above"]), NOVAL_KINDS = new Set(["spike", "dump"]);
function kindOptions(sel) { return Object.entries(ALERT_KINDS).map(([k, l]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(l)}</option>`).join(""); }
function condValue(kind, th) { return PCT_KINDS.has(kind) ? th + "%" : NOVAL_KINDS.has(kind) ? "" : gp(th); }
function condNow(kind, r) {
  return ({ price_below: gp(r.low), price_above: gp(r.high), profit_above: gp(r.profit), roi_above: pct(r.roi, 2),
    move_pct: r.chg1h != null ? pct(r.chg1h) : "-", drop_pct: r.chg1h != null ? pct(r.chg1h) : "-", rise_pct: r.chg1h != null ? pct(r.chg1h) : "-",
    vol_above: gp(r.vol1h), stability_above: r.stability != null ? pct(r.stability, 0) : "-", spike: r.signal || "none", dump: r.signal || "none" })[kind] ?? "-";
}
function desktopState() {
  if (!("Notification" in window)) return "unsupported";
  return Notification.permission;
}
renderers.alerts = async function (host) {
  const [a, n] = await Promise.all([api("/api/alerts"), api("/api/notifications")]);
  ALERT_KINDS = a.kinds; PCT_KINDS = new Set(a.pctKinds); NOVAL_KINDS = new Set(a.noValueKinds);
  const ds = desktopState();
  host.innerHTML = `<h2>Alerts</h2>
    <p class="lede">Alerts are checked every time prices refresh (about once a minute) while the app is running. When one fires you get a pop-up here and the bell lights up. The same alert won't repeat for 30 minutes (per item for watchlist-wide alerts). Buy limit resets from your flip log are announced here too.</p>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">New alert</h3>
        <div class="inline-form">${pickerField("Item", "alItem")}
          <label class="check"><input type="checkbox" id="alAll"> Any watchlist item</label></div>
        <div class="cond-row">
          <label class="field wide"><span>When</span><select class="input" id="alKind">${kindOptions()}</select></label>
          <label class="field"><span>Value</span><input class="input" id="alTh" placeholder="e.g. 750k"></label></div>
        <div id="alExtra"></div>
        <div class="inline-form">
          <button class="btn small" id="alMore">+ And another condition</button>
          <label class="check"><input type="checkbox" id="alOnce"> Only once</label>
          <button class="btn primary" id="alAdd">Add alert</button></div>
        <div id="alMsg" class="small" style="margin-top:8px"></div>
        <label class="check" style="margin-top:10px"><input type="checkbox" id="alSound" ${store.get("sound", true) ? "checked" : ""}> Play a sound when an alert fires</label>
        <div class="small" style="margin-top:4px">${ds === "granted" ? `Desktop notifications are on. They show when this tab is in the background. <label class="check" style="display:inline-flex;padding:0"><input type="checkbox" id="alDesk" ${store.get("desktop", true) ? "checked" : ""}> Use them</label>`
          : ds === "unsupported" ? `<span class="muted">This browser does not support desktop notifications.</span>`
          : ds === "denied" ? `<span class="muted">Desktop notifications are blocked for this page in your browser settings.</span>`
          : `<button class="btn small" id="alAsk">Turn on desktop notifications</button> <span class="muted">so alerts reach you when this tab is hidden.</span>`}</div>
      </div>
      <div class="card"><div style="display:flex;align-items:center;gap:8px"><h3 style="margin:0">Recent alerts</h3><button class="btn small ghost" id="alClear" style="margin-left:auto">Clear</button></div>
        <div class="feed" id="alFeed">${n.items.length ? n.items.map((x) => `<div class="n ${x.seen ? "" : "new"}" data-id="${x.item_id}"><span class="muted small" style="white-space:nowrap">${esc(fmtTime(x.ts, true))}</span><span>${esc(x.message)}</span></div>`).join("") : `<div class="empty">Nothing yet.</div>`}</div>
      </div>
    </div>
    <h3>Your alerts</h3>
    <div class="table-wrap">${a.alerts.length ? `<table>${thead([{ label: "Item" }, { label: "Conditions (all must hold)" }, { label: "Now", num: 1 }, { label: "Last fired" }, { label: "" }], null)}<tbody>${a.alerts.map((x) => {
      const r = S.byId.get(x.item_id) || {};
      return `<tr ${x.item_id ? `data-id="${x.item_id}"` : `class="static"`}><td>${x.item_id ? itemCell({ name: x.name || "Item " + x.item_id, icon: x.icon }) : `<b>Any watchlist item</b>`}</td>
        <td>${x.conditions.map((c) => `${esc(c.label)} <b>${condValue(c.kind, c.threshold)}</b>`).join('<span class="muted"> and </span>')}${x.once ? ' <span class="tag">Once</span>' : ""}${x.enabled ? "" : ' <span class="tag">Paused</span>'}</td>
        <td class="num">${x.item_id ? x.conditions.map((c) => condNow(c.kind, r)).join(" / ") : "-"}</td><td class="muted">${x.last_fired ? esc(fmtTime(x.last_fired, true)) : "Never"}</td>
        <td class="num"><button class="btn small" data-tog="${x.aid}">${x.enabled ? "Pause" : "Resume"}</button> <button class="btn small danger" data-del="${x.aid}">Delete</button></td></tr>`;
    }).join("")}</tbody></table>` : `<div class="empty">No alerts yet. Add one above or from any item's detail panel.</div>`}</div>`;
  let picked = null;
  makePicker($("#alItem", host), $("#alItemList", host), (r) => { picked = r; $("#alItem", host).value = r.name; $("#alAll", host).checked = false; });
  $("#alAll", host).onchange = (e) => { $("#alItem", host).disabled = e.target.checked; };
  $("#alSound", host).onchange = (e) => store.set("sound", e.target.checked);
  if ($("#alDesk", host)) $("#alDesk", host).onchange = (e) => store.set("desktop", e.target.checked);
  if ($("#alAsk", host)) $("#alAsk", host).onclick = async () => { try { await Notification.requestPermission(); } catch (e) { /* ignore */ } renderers.alerts(host); };
  $("#alMore", host).onclick = () => {
    if ($$(".cond-row", $("#alExtra", host)).length >= 3) return;
    const row = document.createElement("div");
    row.className = "cond-row";
    row.innerHTML = `<label class="field wide"><span>And</span><select class="input xk">${kindOptions("profit_above")}</select></label><label class="field"><span>Value</span><input class="input xt"></label><button class="btn small ghost xr" title="Remove">✕</button>`;
    $(".xr", row).onclick = () => row.remove();
    $("#alExtra", host).appendChild(row);
  };
  $("#alAdd", host).onclick = async () => {
    const msg = $("#alMsg", host), all = $("#alAll", host).checked;
    if (!picked && !all) { msg.innerHTML = `<span class="err">Pick an item from the list, or tick Any watchlist item.</span>`; return; }
    const kind = $("#alKind", host).value;
    const th = numOr($("#alTh", host).value, NaN);
    if (!NOVAL_KINDS.has(kind) && !Number.isFinite(th)) { msg.innerHTML = `<span class="err">Enter a number for the value.</span>`; return; }
    const extra = [];
    for (const row of $$(".cond-row", $("#alExtra", host))) {
      const k = $(".xk", row).value, t = numOr($(".xt", row).value, NaN);
      if (!NOVAL_KINDS.has(k) && !Number.isFinite(t)) { msg.innerHTML = `<span class="err">Enter a number for every extra condition.</span>`; return; }
      extra.push({ kind: k, threshold: NOVAL_KINDS.has(k) ? 0 : t });
    }
    try {
      await api("/api/alerts", { method: "POST", body: { item_id: all ? 0 : picked.id, kind, threshold: NOVAL_KINDS.has(kind) ? 0 : th, once: $("#alOnce", host).checked, extra } });
    } catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; return; }
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
  S.limits = d.limits || S.limits;
  const lims = Object.entries(d.limits || {}).map(([id, l]) => Object.assign({ id: +id }, l, S.byId.get(+id) ? { name: S.byId.get(+id).name, icon: S.byId.get(+id).icon } : { name: "Item " + id })).sort((a, b) => a.resetAt - b.resetAt);
  const edge = (v) => v == null ? "-" : `<span class="${signCls(v)}">${v > 0 ? "+" : ""}${pct(v, 2)}</span>`;
  host.innerHTML = `<h2>Flip log</h2>
    <p class="lede">With the RuneLite plugin running, every GE trade is recorded here on its own (marked Auto, buys matched to sells first in, first out). You can also record trades by hand to track real profit after tax. Open flips (no sell price yet) are valued at the current instant-buy price. Buys you log here also count against the item's GE buy limit, so the flip finder and planner show what you have left.</p>
    <div class="tiles">
      <div class="tile"><div class="k">Realized profit</div><div class="v ${signCls(s.realized)}">${signed(s.realized, short)}</div><div class="s">${s.closed} closed flips</div></div>
      <div class="tile"><div class="k">Win rate</div><div class="v">${s.winRate == null ? "-" : pct(s.winRate, 0)}</div><div class="s">Avg ROI ${pct(s.avgRoi, 2)} · best run ${s.bestStreak}</div></div>
      <div class="tile"><div class="k">Avg per flip</div><div class="v ${signCls(s.avgProfit)}">${s.avgProfit == null ? "-" : signed(s.avgProfit, short)}</div><div class="s">Turnover ${short(s.turnover)}</div></div>
      <div class="tile"><div class="k">Worst drawdown</div><div class="v ${s.maxDrawdown ? "neg" : ""}">${s.maxDrawdown ? "-" + short(s.maxDrawdown) : "0"}</div><div class="s">Peak to trough, realized</div></div>
      <div class="tile"><div class="k">Open positions</div><div class="v ${signCls(s.unrealized)}">${signed(s.unrealized, short)}</div><div class="s">${s.open} open, if sold now</div></div>
      <div class="tile"><div class="k">Tax paid</div><div class="v">${short(s.taxPaid)}</div><div class="s">On closed flips</div></div>
      <div class="tile"><div class="k">Edge vs market</div><div class="v small" style="font-size:14px">Buy ${edge(s.avgBuyEdge)} · Sell ${edge(s.avgSellEdge)}</div><div class="s" title="Compared with the instant prices when you logged the flip. Positive is better than the market.">vs instant prices at the time</div></div>
      <div class="tile"><div class="k">GP per hour held</div><div class="v">${s.gpPerHourHeld == null ? "-" : short(s.gpPerHourHeld)}</div><div class="s">Profit over buy-to-sell time</div></div>
    </div>
    ${lims.length ? `<div class="card" style="margin-bottom:14px"><h3 style="margin-top:0">Buy limits in use</h3><div class="table-wrap" style="border:0"><table>${thead([{ label: "Item" }, { label: "Bought", num: 1 }, { label: "Left", num: 1 }, { label: "Resets in", num: 1 }, { label: "Resets at" }], null)}<tbody>${lims.map((l) => `<tr data-id="${l.id}"><td>${itemCell(l)}</td><td class="num">${gp(l.used)}${l.limit ? ` <span class="muted small">/ ${gp(l.limit)}</span>` : ""}</td><td class="num"><b>${l.left == null ? "-" : gp(l.left)}</b></td><td class="num">${ago(l.resetAt - Date.now() / 1000)}</td><td class="muted">${esc(fmtTime(l.resetAt))}</td></tr>`).join("")}</tbody></table></div></div>` : ""}
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
      <div class="chart-card"><div class="chart-head"><span class="title">Cumulative realized profit</span><span class="muted small">Every closed flip, in order</span></div><div id="flEq"></div></div>
      <div class="chart-card"><div class="chart-head"><span class="title">Profit by day</span><span class="muted small">Last 30 days with closed flips</span></div><div id="flChart"></div></div>
      <div class="chart-card"><div class="chart-head"><span class="title">Profit by hour sold</span><span class="muted small">Your local time</span></div><div id="flHour"></div></div>
      <div class="chart-card"><div class="chart-head"><span class="title">Profit by weekday</span></div><div id="flWday"></div></div>
    </div>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">Best items</h3>${s.topItems.length ? `<table>${thead([{ label: "Item" }, { label: "Flips", num: 1 }, { label: "Profit", num: 1 }], null)}<tbody>${s.topItems.map((t) => { const m = S.byId.get(t.id) || t; return `<tr data-id="${t.id}"><td>${itemCell({ name: t.name, icon: m.icon })}</td><td class="num">${t.flips}</td><td class="num ${signCls(t.profit)}">${signed(t.profit, short)}</td></tr>`; }).join("")}</tbody></table>` : `<div class="empty">No closed flips yet.</div>`}</div>
      <div class="card"><h3 style="margin-top:0">How long you hold</h3>${s.closed ? `<table>${thead([{ label: "Held" }, { label: "Flips", num: 1 }, { label: "Profit", num: 1 }, { label: "Avg ROI", num: 1 }], null)}<tbody>${s.holdTimes.map((h) => `<tr class="static"><td>${esc(h.label)}</td><td class="num">${h.flips}</td><td class="num ${signCls(h.profit)}">${h.flips ? signed(h.profit, short) : "-"}</td><td class="num">${h.avgRoi == null ? "-" : pct(h.avgRoi, 2)}</td></tr>`).join("")}</tbody></table>
        ${s.best ? `<p class="small" style="margin:10px 0 0">Best flip: <b>${esc(s.best.name)}</b> <span class="pos">${signed(s.best.profit, short)}</span> · Worst: <b>${esc(s.worst.name)}</b> <span class="${signCls(s.worst.profit)}">${signed(s.worst.profit, short)}</span></p>` : ""}` : `<div class="empty">No closed flips yet.</div>`}</div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;margin:18px 0 8px"><h3 style="margin:0">All flips</h3><a class="btn small" href="/api/flips.csv" style="margin-left:auto">Export CSV</a></div>
    <div class="table-wrap">${d.flips.length ? `<table>${thead([{ label: "Item" }, { label: "Qty", num: 1 }, { label: "Bought", num: 1 }, { label: "Sold", num: 1 }, { label: "Tax each", num: 1 }, { label: "Profit", num: 1 }, { label: "ROI", num: 1 }, { label: "Edge", num: 1, title: "Buy and sell price vs the instant prices when logged; positive is better" }, { label: "Date" }, { label: "" }], null)}<tbody>${d.flips.map((f) => `
      <tr data-id="${f.item_id}"><td>${itemCell(f, (f.auto ? ` <span class="tag free" title="Recorded automatically from your GE trades${f.acctName ? " on " + esc(f.acctName) : ""}">Auto</span>` : "") + (f.note ? ` <span class="muted small">${esc(f.note)}</span>` : ""))}</td><td class="num">${gp(f.qty)}</td><td class="num">${gp(f.buy_price)}</td>
      <td class="num">${f.open ? `<span class="muted">Open (now ${gp(f.livePrice)})</span>` : gp(f.sell_price)}</td><td class="num muted">${gp(f.taxEach)}</td>
      <td class="num ${signCls(f.open ? f.unrealized : f.profit)}">${f.open ? `<span title="Unrealized">${signed(f.unrealized)}*</span>` : signed(f.profit)}</td>
      <td class="num">${pct(f.roi, 2)}</td><td class="num small">${f.buyEdge == null && f.sellEdge == null ? "-" : `${edge(f.buyEdge)}${f.sellEdge != null ? " / " + edge(f.sellEdge) : ""}`}</td><td class="muted">${esc(fmtTime(f.sell_ts || f.buy_ts, true))}</td>
      <td class="num">${f.auto ? `<button class="btn small" data-ign="${f.item_id}" data-acct="${esc(f.acct || "")}" title="Stop treating this item's trades as flips (for example gear you bought to use)">Not a flip</button>` : `${f.open ? `<button class="btn small" data-close="${f.fid}">Close</button> ` : ""}<button class="btn small danger" data-del="${f.fid}">Delete</button>`}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No flips logged yet.</div>`}</div>
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
  $$("[data-ign]", host).forEach((b) => (b.onclick = async (e) => { e.stopPropagation(); await api("/api/flips/ignore", { method: "POST", body: { acct: b.dataset.acct, item: +b.dataset.ign } }); renderers.log(host); }));
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
  barChart($("#flChart", host), s.byDay, "day", "profit", { aria: "Profit by day" });
  lineChart($("#flEq", host), [{ name: "Realized profit", cls: "l1", color: "var(--series-1)", pts: s.equity }], { zero: true, empty: "Close some flips to see your running total.", aria: "Cumulative realized profit", height: 190, tipFmt: (v) => signed(v) + " gp" });
  barChart($("#flHour", host), s.byHour, "hour", "profit", { label: (h) => (h % 3 ? "" : hourLabel(h)), maxLabels: 24, hideEmpty: true, empty: "Close some flips to see which hours pay best.", aria: "Profit by hour", tip: (d) => `<div class="muted">${esc(hourLabel(d.hour))}</div><b class="${signCls(d.profit)}">${signed(d.profit)} gp</b><div class="muted">${d.flips} flips</div>` });
  barChart($("#flWday", host), s.byWeekday, "day", "profit", { label: (x) => x, maxLabels: 7, hideEmpty: true, empty: "Close some flips to see which days pay best.", aria: "Profit by weekday", tip: (d) => `<div class="muted">${esc(d.day)}</div><b class="${signCls(d.profit)}">${signed(d.profit)} gp</b><div class="muted">${d.flips} flips</div>` });
};
function confirmInline(btn) {
  if (btn.dataset.armed) return true;
  btn.dataset.armed = "1"; btn.textContent = "Confirm?";
  setTimeout(() => { if (btn.isConnected) { delete btn.dataset.armed; btn.textContent = "Delete"; } }, 3000);
  return false;
}

// Portfolio ----------------------------------------------------------------------
renderers.portfolio = async function (host) {
  let p;
  try { p = await api("/api/portfolio"); } catch (e) { host.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const t = p.totals;
  host.innerHTML = `<h2>Manual holdings</h2>
    <p class="lede">Track what you hold (bank, stock you are sitting on) and your cash stack. Items are valued at the instant-buy price after tax, what they would fetch listed at the going rate. Open flips from the flip log count too. Net worth is saved every hour while the app runs.</p>
    <div class="tiles">
      <div class="tile"><div class="k">Net worth</div><div class="v">${short(t.total)}</div><div class="s">${gp(t.total)} gp</div></div>
      <div class="tile"><div class="k">Items</div><div class="v">${short(t.holdings)}</div><div class="s">${p.holdings.length} holdings</div></div>
      <div class="tile"><div class="k">Cash</div><div class="v">${short(p.coins)}</div><div class="s">Coins and platinum</div></div>
      <div class="tile"><div class="k">Open flips</div><div class="v">${short(t.flips)}</div><div class="s">${p.openFlips.length} positions</div></div>
      <div class="tile"><div class="k">Unrealized P/L</div><div class="v ${signCls(t.pnl)}">${t.pnl == null ? "-" : signed(t.pnl, short)}</div><div class="s">Holdings with a cost</div></div>
      <div class="tile"><div class="k">24h change</div><div class="v ${signCls(t.chg24)}">${signed(t.chg24, short)}</div><div class="s">Item value only</div></div>
      <div class="tile"><div class="k">Quick sale value</div><div class="v">${short(t.quick)}</div><div class="s">Everything sold instantly</div></div>
    </div>
    <div class="chart-card"><div class="chart-head"><span class="title">Net worth</span><span class="muted small">Hourly, last 90 days</span></div><div id="pfChart"></div></div>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">Add a holding</h3>
        <div class="inline-form">${pickerField("Item", "pfItem")}
          <label class="field"><span>Quantity</span><input class="input" id="pfQty" inputmode="numeric"></label>
          <label class="field"><span>Cost each</span><input class="input" id="pfCost" placeholder="Optional"></label>
          <button class="btn primary" id="pfAdd">Add</button></div>
        <div class="inline-form" style="margin-top:14px">
          <label class="field"><span>Cash stack</span><input class="input" id="pfCoins" value="${p.coins ? short(p.coins) : ""}" placeholder="e.g. 25m"></label>
          <button class="btn" id="pfCoinsSave">Save cash</button></div>
        <div id="pfMsg" class="small" style="margin-top:8px"></div></div>
      <div class="card"><h3 style="margin-top:0">Paste a list</h3>
        <p class="small muted" style="margin:0 0 6px">One item per line: <code>2 x Abyssal whip</code>, <code>Shark, 500</code>, <code>Nature rune 10k</code>, or <code>Shark, 500, 950</code> with a cost each.</p>
        <textarea class="input" id="pfText" placeholder="Abyssal whip x 1&#10;Shark, 500, 950&#10;Nature rune 10k"></textarea>
        <div class="inline-form"><label class="check"><input type="checkbox" id="pfLive" checked> Use today's price as cost when none is given</label><button class="btn primary" id="pfImport">Import</button></div>
        <div id="pfImpMsg" class="small" style="margin-top:6px"></div></div>
    </div>
    <h3>Holdings</h3>
    <div class="table-wrap">${p.holdings.length ? `<table>${thead([{ label: "Item" }, { label: "Qty", num: 1 }, { label: "Cost each", num: 1 }, { label: "Price", num: 1 }, { label: "Value", num: 1 }, { label: "P/L", num: 1 }, { label: "24h", num: 1 }, { label: "Outlook", title: "Do trends support holding? From the holding outlook model" }, { label: "Next 30d", num: 1, title: "Expected value change, middle outcome" }, { label: "Share" }, { label: "" }], null)}<tbody>${p.holdings.sort((a, b) => (b.value || 0) - (a.value || 0)).map((h) => `
      <tr data-id="${h.item_id}"><td>${itemCell(h)}</td><td class="num">${gp(h.qty)}</td><td class="num">${h.costEach == null ? "-" : gp(h.costEach)}</td><td class="num">${gp(h.price)}</td>
      <td class="num"><b>${short(h.value)}</b></td><td class="num ${signCls(h.pnl)}">${h.pnl == null ? "-" : signed(h.pnl, short)}</td>
      <td class="num ${signCls(h.chg24h)}">${h.chg24h == null ? "-" : (h.chg24h > 0 ? "+" : "") + pct(h.chg24h)}</td>
      <td data-ho-v="${h.item_id}"><span class="muted small">...</span></td><td class="num" data-ho-r="${h.item_id}" data-val="${h.value || 0}"></td>
      <td class="small">${h.share == null ? "-" : `<span class="share" style="width:${Math.max(2, Math.round(h.share * 80))}px"></span>${pct(h.share, 0)}`}</td>
      <td class="num"><button class="btn small danger" data-hdel="${h.hid}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No holdings yet. Add some above or paste a list.</div>`}</div>
    ${p.openFlips.length ? `<h3>Open flips</h3><div class="table-wrap"><table>${thead([{ label: "Item" }, { label: "Qty", num: 1 }, { label: "Cost", num: 1 }, { label: "Value now", num: 1 }, { label: "P/L", num: 1 }], null)}<tbody>${p.openFlips.map((f) => `<tr data-id="${f.item_id}"><td>${itemCell(f)}</td><td class="num">${gp(f.qty)}</td><td class="num">${short(f.cost)}</td><td class="num">${short(f.value)}</td><td class="num ${signCls(f.pnl)}">${signed(f.pnl, short)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
  if (p.holdings.length) {
    api("/api/hold/scan?scope=portfolio").then((d) => {
      const by = new Map((d.items || []).map((x) => [x.id, x]));
      $$("[data-ho-v]", host).forEach((el) => { const x = by.get(+el.dataset.hoV); el.innerHTML = x && x.ok ? verdictTag(x.verdict) : `<span class="muted small" title="Needs about 180 days of daily history">-</span>`; });
      $$("[data-ho-r]", host).forEach((el) => { const x = by.get(+el.dataset.hoR); if (x && x.ok) { el.className = "num " + signCls(x.ret30); el.innerHTML = `${signed(+el.dataset.val * x.ret30, short)} <span class="muted small">${sgnPct(x.ret30)}</span>`; } else el.textContent = "-"; });
    }).catch(() => {});
  }
  lineChart($("#pfChart", host), [{ name: "Net worth", cls: "l1", color: "var(--series-1)", pts: p.history.map((h) => ({ t: h.ts, v: h.total })) }], { empty: "Net worth is recorded every hour once you add holdings or cash. The chart appears after the second snapshot.", aria: "Net worth over time", height: 200, tipFmt: (v) => gp(v) + " gp" });
  let picked = null;
  makePicker($("#pfItem", host), $("#pfItemList", host), (r) => { picked = r; $("#pfItem", host).value = r.name; if (!$("#pfCost", host).value) $("#pfCost", host).placeholder = "Now " + short(r.low); });
  const msg = $("#pfMsg", host);
  $("#pfAdd", host).onclick = async () => {
    const qty = numOr($("#pfQty", host).value, NaN), cost = $("#pfCost", host).value.trim();
    if (!picked || !(qty > 0)) { msg.innerHTML = `<span class="err">Pick an item and enter a quantity.</span>`; return; }
    try { await api("/api/holdings", { method: "POST", body: { item_id: picked.id, qty: Math.round(qty), cost_each: cost ? Math.round(numOr(cost, 0)) : null } }); renderers.portfolio(host); }
    catch (e) { msg.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $("#pfCoinsSave", host).onclick = async () => { await api("/api/coins", { method: "POST", body: { coins: Math.round(numOr($("#pfCoins", host).value, 0)) } }); renderers.portfolio(host); };
  $("#pfImport", host).onclick = async () => {
    const r = await api("/api/holdings/import", { method: "POST", body: { text: $("#pfText", host).value, use_live_cost: $("#pfLive", host).checked } });
    if (r.problems.length) { await renderers.portfolio(host); $("#pfImpMsg", host).innerHTML = `Added ${r.added}. <span class="err">Couldn't read: ${esc(r.problems.slice(0, 6).join("; "))}${r.problems.length > 6 ? "..." : ""}</span>`; }
    else renderers.portfolio(host);
  };
  $$("[data-hdel]", host).forEach((b) => (b.onclick = async (e) => { e.stopPropagation(); if (!confirmInline(b)) return; await api("/api/holdings?hid=" + b.dataset.hdel, { method: "DELETE" }); renderers.portfolio(host); }));
  bindRowClicks(host);
};

// Net worth: live account value from the RuneLite plugin ----------------------------
const NW = Object.assign({ acct: "", range: "30", group: "category", show: 50, sort: { key: "value", dir: "desc" } }, store.get("nw", {}));
NW.show = 50;
const NW_RANGES = [["1", "1D"], ["7", "7D"], ["30", "1M"], ["90", "3M"], ["365", "1Y"], ["all", "All"]];
const CONF = {
  exact: ["Exact", "Arithmetic on live prices, after tax. Prices can move before you act."],
  estimate: ["Estimate", "Depends on offers filling or on an assumption stated in the text."],
  history: ["History", "Describes what has happened before. Not a prediction."],
  data: ["Data", "Your net worth is incomplete or estimated until you do this."],
};
const REC_GROUPS = [["data", "Complete the picture"], ["trading", "Trading"], ["value", "Add value to what you hold"], ["risk", "Risk"]];
const SLOT_STATE = { BUYING: "Buying", SELLING: "Selling", BOUGHT: "Bought", SOLD: "Sold", CANCELLED_BUY: "Cancelled", CANCELLED_SELL: "Cancelled", EMPTY: "Empty" };
function chgPill(label, c) {
  if (!c) return `<div class="nw-chg"><span class="k">${label}</span><span class="muted">-</span></div>`;
  return `<div class="nw-chg"><span class="k">${label}</span><span class="${signCls(c.gp)}">${signed(c.gp, short)} <small>(${c.pct > 0 ? "+" : ""}${pct(c.pct, 2)})</small></span></div>`;
}
function allocRows(rows, key, total, colors) {
  if (!rows.length) return `<div class="empty">Nothing yet.</div>`;
  return `<div class="alloc-bar">${rows.map((r, i) => `<i style="width:${Math.max(0.4, (r.value / (total || 1)) * 100)}%;background:${colors[i % colors.length]}" title="${esc(r[key])}: ${pct(r.value / (total || 1), 1)}"></i>`).join("")}</div>
    <div class="alloc-list">${rows.map((r, i) => `<div class="r"><i style="background:${colors[i % colors.length]}"></i><span>${esc(r[key])}${r.seen ? ` <span class="muted small" title="Last seen by the plugin">${ago(Date.now() / 1000 - r.seen)} ago</span>` : ""}</span><span class="v">${short(r.value)}</span><span class="p muted">${pct(r.value / (total || 1), 1)}</span></div>`).join("")}</div>`;
}
const ALLOC_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "#9a6fd6", "#d6a72c", "#4bb3c8", "#c8577e", "#7f9c3c", "#8a8f98", "#5d6fd1"];

function slotCard(s) {
  if (s.state === "EMPTY") return `<div class="slot empty-slot"><div class="muted small">Slot ${s.slot + 1}</div><div class="muted">Empty</div></div>`;
  const active = s.state === "BUYING" || s.state === "SELLING";
  const mk = s.side === "buy" ? s.market_low : s.market_high;
  const diff = mk && s.price ? s.price / mk - 1 : null;
  return `<div class="slot ${s.side} ${s.note ? "warn" : ""}" data-id="${s.item}">
    <div class="slot-top"><span class="tag ${s.side === "buy" ? "free" : "pump"}">${esc(s.side === "buy" ? "Buy" : "Sell")}</span><span class="muted small">Slot ${s.slot + 1} · ${esc(SLOT_STATE[s.state] || s.state)}</span></div>
    ${itemCell(s)}
    <div class="kv small"><span class="k">Price</span><span>${gp(s.price)}${diff != null ? ` <span class="${s.side === "buy" ? signCls(-diff) : signCls(diff)} small" title="Compared with the current ${s.side === "buy" ? "instant-sell" : "instant-buy"} price">${diff > 0 ? "+" : ""}${pct(diff, 1)} vs market</span>` : ""}</span>
      <span class="k">Filled</span><span>${gp(s.done)} / ${gp(s.total)}</span>
      ${active && s.idle != null ? `<span class="k">Last fill</span><span>${ago(s.idle)} ago</span>` : ""}</div>
    <div class="progress"><i style="width:${Math.round((s.progress || 0) * 100)}%"></i></div>
    ${s.note ? `<div class="small warn-txt">${esc(s.note)}</div>` : ""}
  </div>`;
}

renderers.networth = async function (host, soft) {
  let st, v;
  try {
    st = await api("/api/account/status");
    v = await api(`/api/networth?acct=${encodeURIComponent(NW.acct)}&days=${NW.range}`);
  } catch (e) { host.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const accts = v.accountList || [];
  if (NW.acct && !accts.some((a) => a.acct === NW.acct)) { NW.acct = ""; store.set("nw", NW); }
  const noPlugin = !accts.length;
  const ch = v.changes || {};
  const holdings = v.holdings || [];
  const scrollY = soft ? window.scrollY : null;
  const cols = [{ sort: "name", label: "Item" }, { sort: "qty", label: "Qty", num: 1 }, { sort: "each", label: "Price", num: 1, title: v.mode === "market" ? "Mid price" : "Instant-buy price after tax" },
    { sort: "value", label: "Value", num: 1 }, { sort: "share", label: "Share" }, { sort: "chg24h", label: "24h", num: 1 }, { sort: "chg24gp", label: "24h gp", num: 1 },
    { sort: "costEach", label: "Avg cost", num: 1, title: "Average price paid for GE buys (first in, first out). Items you already had get their value on the day the plugin first saw them, marked *" },
    { sort: "pnl", label: "Unrealized", num: 1, title: "Value now minus what you paid, for units with a known cost" }, { sort: "category", label: "Class" }, { label: "Where" }];
  const rows = sortRows(holdings, NW.sort.key, NW.sort.dir);
  const where = (w) => Object.entries(w).map(([k, q]) => `${esc(({ bank: "Bank", inventory: "Inv", equipment: "Worn", ge: "GE", rune_pouch: "Pouch", looting_bag: "Bag", seed_vault: "Vault", death_storage: "Death", manual: "Manual" })[k] || k)}${Object.keys(w).length > 1 ? " " + short(q) : ""}`).join(", ");
  const lastEvent = st.live && st.live.last;
  host.innerHTML = `<div class="section-head" style="margin-top:0"><h2 style="margin:0">Net worth</h2>
      <div class="right">
        <div class="chips" style="margin:0">${[["", "All accounts"]].concat(accts.map((a) => [a.acct, a.name || "Unnamed"])).map(([k, n]) => `<button class="chip ${NW.acct === k ? "on" : ""}" data-acct="${esc(k)}">${k ? `<span class="dot-s ${accts.find((a) => a.acct === k).online ? "on" : ""}"></span>` : ""}${esc(n)}</button>`).join("")}</div>
        <button class="btn small" id="nwRefresh" title="Read new plugin events now">Refresh</button>
      </div></div>
    ${noPlugin ? setupCard(st) : ""}
    <div class="nw-hero card">
      <div class="nw-main">
        <div class="muted small">${NW.acct ? esc((accts.find((a) => a.acct === NW.acct) || {}).name || "") : "All accounts"}${v.manualIncluded ? " plus manual additions" : ""} · valued at ${v.mode === "market" ? "mid price" : "sell price after tax"}</div>
        <div class="nw-total">${gp(v.total)} <span class="unit">gp</span></div>
        <div class="nw-sub"><span>${short(v.total)}</span>${lastEvent ? `<span class="muted small">Updated ${ago(Date.now() / 1000 - lastEvent)} ago from RuneLite</span>` : ""}</div>
        <div class="nw-chgs">${chgPill("24h", ch.d1)}${chgPill("7 days", ch.d7)}${chgPill("30 days", ch.d30)}${chgPill("Since tracking began", ch.all)}</div>
      </div>
      <div class="nw-side kv">
        <span class="k">Cash</span><span>${short(v.cash)} <span class="muted small">${pct(v.total ? v.cash / v.total : 0, 0)}</span></span>
        <span class="k">Items</span><span>${short(v.items)}</span>
        <span class="k">In the GE</span><span>${short(v.ge)}${v.geEstimated ? ` <span class="tag stale" title="Some collection boxes were not seen since the last trade, so they are estimated from the offer">est</span>` : ""}</span>
        <span class="k" title="How much today's price moves changed your item value (Wiki 24h change)">Market move today</span><span class="${signCls(v.chg24market)}">${signed(v.chg24market, short)}</span>
        <span class="k" title="Value now minus cost. GE buys use what you paid; items you already had use their value when the plugin first saw them">Unrealized P/L</span><span class="${signCls(v.pnl)}">${v.pnl ? signed(v.pnl, short) : "-"}${v.costSince ? ` <span class="muted small" title="Items you already had count from the day tracking began">since ${esc(new Date(v.costSince * 1000).toLocaleDateString([], { month: "short", day: "numeric" }))}</span>` : ""}</span>
      </div>
    </div>
    <div class="chart-card"><div class="chart-head"><span class="title">Portfolio value</span>
      <div class="seg">${NW_RANGES.map(([k, l]) => `<button data-range="${k}" class="${NW.range === k ? "on" : ""}">${l}</button>`).join("")}</div></div>
      <div id="nwChart"></div>
      <p class="muted small" style="margin:6px 0 0">Solid: your recorded net worth (every 5 minutes while the app runs). Dashed: what today's holdings would have been worth at past prices, for context before tracking began. It is not your real history.</p></div>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">Performance vs the market</h3><div id="nwPerf"><div class="muted small">Loading...</div></div></div>
      <div class="card"><h3 style="margin-top:0">Risk</h3><div id="nwRisk"><div class="muted small">Loading...</div></div></div>
    </div>
    <div class="card" style="margin-top:16px"><h3 style="margin-top:0">Heatmap</h3><div id="nwHeat"></div></div>
    <div class="grid2">
      <div class="card"><div class="section-head" style="margin-top:0"><h3>Allocation</h3><div class="right"><div class="seg" style="margin:0">${[["category", "By class"], ["container", "By location"], ["item", "Top items"]].map(([k, l]) => `<button data-group="${k}" class="${NW.group === k ? "on" : ""}">${l}</button>`).join("")}</div></div></div>
        <div id="nwAlloc"></div></div>
      <div class="card"><h3 style="margin-top:0">Recommendations</h3><div id="nwRecs"><div class="muted small">Working out what could add value...</div></div></div>
    </div>
    <div class="section-head"><h3>Grand Exchange slots</h3><span class="muted small">Live from the plugin. Offers that fill while you are logged out update at your next login.</span></div>
    <div id="nwSlots"><div class="muted small">Loading...</div></div>
    <div class="section-head"><h3>Holdings</h3><span class="muted small">${holdings.length} items. Click a row for charts and the holding outlook. * Cost is the value when the plugin first saw the item, so profit or loss counts from then.</span></div>
    <div class="table-wrap">${holdings.length ? `<table>${thead(cols, NW.sort)}<tbody>${rows.slice(0, NW.show).map((h) => `
      <tr data-id="${h.id}"><td>${itemCell(h, h.how !== "sell" && h.how !== "cash" && h.how !== "market" ? ` <span class="tag stale" title="How this item was priced">${esc(h.how)}</span>` : "")}</td>
      <td class="num">${gp(h.qty)}</td><td class="num">${h.how === "cash" ? "-" : gp(h.each)}</td><td class="num"><b>${short(h.value)}</b></td>
      <td class="small"><span class="share" style="width:${Math.max(2, Math.round(h.share * 80))}px"></span>${pct(h.share, h.share < 0.01 ? 2 : 1)}</td>
      <td class="num ${signCls(h.chg24h)}">${h.chg24h == null || h.how === "cash" ? "-" : (h.chg24h > 0 ? "+" : "") + pct(h.chg24h)}</td>
      <td class="num ${signCls(h.chg24gp)}">${h.chg24gp ? signed(h.chg24gp, short) : "-"}</td>
      <td class="num" ${h.costFrom && h.costFrom !== "trades" ? `title="${h.costFrom === "mixed" ? "Part GE buys, part " : ""}value when first seen${h.seededAt ? " on " + esc(fmtTime(h.seededAt, true)) : ""}: profit or loss counts from then"` : ""}>${h.costEach == null ? "-" : gp(h.costEach) + (h.costFrom && h.costFrom !== "trades" ? `<span class="muted">*</span>` : "")}</td>
      <td class="num ${signCls(h.pnl)}">${h.pnl == null ? "-" : signed(h.pnl, short)}</td>
      <td class="small muted">${esc(h.category || "")}</td><td class="small muted">${where(h.where)}</td></tr>`).join("")}
      ${rows.length > NW.show ? `<tr class="static"><td colspan="${cols.length}" class="more-row"><button class="btn small" id="nwMore">Show all ${rows.length}</button></td></tr>` : ""}</tbody></table>`
      : `<div class="empty">${noPlugin ? "No account data yet. Set up the RuneLite plugin above, or add holdings by hand in the Portfolio tab." : "Nothing held yet. Open your bank in game so the plugin can see it."}</div>`}</div>
    <div class="grid2" style="margin-top:16px">
      <div class="card"><h3 style="margin-top:0">Recent GE trades</h3><div id="nwFills" class="muted small">Loading...</div></div>
      <div class="card"><h3 style="margin-top:0">Recent loot</h3><div id="nwLoot" class="muted small">Loading...</div></div>
    </div>
    <div class="grid2" style="margin-top:16px">
      <div class="card"><h3 style="margin-top:0">What the plugin has seen</h3>
        <div class="tscroll"><table>${thead([{ label: "Storage" }, ...(NW.acct ? [] : [{ label: "Account" }]), { label: "Last seen" }, { label: "" }], null)}<tbody>${(v.coverage || []).map((c) => `<tr class="static"><td>${esc(c.label)}</td>${NW.acct ? "" : `<td class="muted">${esc(c.acctName || "")}</td>`}<td>${c.ok ? ago(c.age) + " ago" : `<span class="warn-txt">Not yet</span>`}</td><td class="small muted">${!c.ok && c.key === "bank" ? "Open your bank once" : c.ok && c.age > 7 * 86400 && c.key === "bank" ? "Open your bank to refresh" : ""}</td></tr>`).join("") || `<tr class="static"><td colspan="4" class="muted">No accounts yet.</td></tr>`}</tbody></table></div>
        <p class="muted small" style="margin:10px 0 0">Storage the plugin cannot see (POH costume room, STASH units, Tackle box, other accounts without the plugin) can be added by hand in the Portfolio tab; manual additions ${v.manualIncluded ? "are" : "are not"} included in the all accounts total (change in Settings).</p></div>
      <div class="card"><h3 style="margin-top:0">Not counted</h3>${(v.untradeable || []).length ? `<p class="muted small" style="margin-top:0">Untradeable items have no GE price, so they count as 0.</p><div class="mini-list">${v.untradeable.slice(0, 30).map((u) => `<div class="r"><span>${esc(u.name)}</span><span class="v muted">${gp(u.qty)}</span></div>`).join("")}</div>` : `<div class="muted small">Every item held has a GE price.</div>`}
        ${!noPlugin ? `<p class="muted small">Plugin folder: <code>${esc(st.folder)}</code>${st.demo ? " (demo data)" : ""}</p>` : ""}</div>
    </div>`;
  if (scrollY != null) window.scrollTo(0, scrollY);
  $$("[data-acct]", host).forEach((b) => (b.onclick = () => { NW.acct = b.dataset.acct; store.set("nw", NW); renderers.networth(host); }));
  $$("[data-range]", host).forEach((b) => (b.onclick = () => { NW.range = b.dataset.range; store.set("nw", NW); renderers.networth(host); }));
  $$("[data-group]", host).forEach((b) => (b.onclick = () => { NW.group = b.dataset.group; store.set("nw", NW); drawAlloc(); $$("[data-group]", host).forEach((x) => x.classList.toggle("on", x === b)); }));
  $("#nwRefresh", host).onclick = async () => { await api("/api/account/refresh", { method: "POST" }); renderers.networth(host); };
  if ($("#nwMore", host)) $("#nwMore", host).onclick = () => { NW.show = 1e9; renderers.networth(host, true); };
  bindSort(host, NW.sort, () => { store.set("nw", NW); renderers.networth(host, true); });
  bindRowClicks(host);
  if ($("#nwSetupSave", host)) $("#nwSetupSave", host).onclick = async () => {
    await api("/api/settings", { method: "POST", body: { runelite_folder: $("#nwFolder", host).value } });
    await api("/api/account/refresh", { method: "POST" });
    renderers.networth(host);
  };
  function drawAlloc() {
    const box = $("#nwAlloc", host);
    if (NW.group === "container") box.innerHTML = allocRows(v.containers.filter((c) => c.value > 0), "label", v.total, ALLOC_COLORS);
    else if (NW.group === "item") {
      const top = holdings.slice(0, 9).map((h) => ({ name: h.name, value: h.value }));
      const rest = holdings.slice(9).reduce((s, h) => s + h.value, 0);
      if (rest > 0) top.push({ name: `${holdings.length - 9} other items`, value: rest });
      box.innerHTML = allocRows(top, "name", v.total, ALLOC_COLORS);
    } else box.innerHTML = allocRows(v.allocation, "category", v.total, ALLOC_COLORS);
  }
  drawAlloc();
  // Chart: recorded history, and today's holdings at past prices for context.
  const since = NW.range === "all" ? 0 : Date.now() / 1000 - +NW.range * 86400;
  const hist = (v.history || []).map((h) => ({ t: h.ts, v: h.total }));
  if (hist.length) hist.push({ t: Date.now() / 1000, v: v.total });
  const back = (v.backcast || []).filter((b) => b.ts >= since).map((b) => ({ t: b.ts, v: b.total }));
  const upRange = hist.length > 1 ? hist[hist.length - 1].v >= hist[0].v : true;
  lineChart($("#nwChart", host), [
    { name: "Net worth", cls: upRange ? "lpos" : "lneg", color: upRange ? "var(--pos)" : "var(--neg)", pts: hist },
    { name: "Today's holdings", cls: "l2", color: "var(--series-2)", pts: back, dash: true },
  ], { empty: "The chart fills in as the app records your net worth. Import daily history in Settings to see today's holdings at past prices.", aria: "Net worth over time", height: 260, tipFmt: (x) => gp(x) + " gp" });
  // Slower panels load after the page is up.
  loadPerformance(host, NW.acct, NW.range);
  loadRisk(host, NW.acct);
  holdingsHeatmap(host, holdings);
  api("/api/slots").then((d) => {
    const list = d.accounts.filter((a) => !NW.acct || a.acct === NW.acct);
    $("#nwSlots", host).innerHTML = list.length ? list.map((a) => {
      const bySlot = new Map(a.slots.map((s) => [s.slot, s]));
      const cells = []; for (let i = 0; i < 8; i++) cells.push(slotCard(bySlot.get(i) || { slot: i, state: "EMPTY" }));
      return `${list.length > 1 ? `<div class="small muted" style="margin:6px 0">${esc(a.name)}</div>` : ""}<div class="slots">${cells.join("")}</div>`;
    }).join("") : `<div class="empty">No GE offers seen yet.</div>`;
    $$(".slot[data-id]", host).forEach((el) => (el.onclick = () => openItem(+el.dataset.id)));
  }).catch(() => {});
  api(`/api/networth/advice?acct=${encodeURIComponent(NW.acct)}`).then((d) => {
    const box = $("#nwRecs", host);
    if (!d.items.length) { box.innerHTML = `<div class="muted small">Nothing to suggest right now. Suggestions appear when an offer is priced away from the market, cash sits idle with free slots, or something you hold is worth more alched, decanted, combined or processed.</div>`; return; }
    box.innerHTML = REC_GROUPS.map(([g, label]) => {
      const items = d.items.filter((r) => r.group === g);
      if (!items.length) return "";
      return `<div class="rec-group"><div class="rec-h">${esc(label)}</div>${items.map((r) => `<div class="rec" ${r.item ? `data-rid="${r.item}"` : ""}>
        <div class="rec-top"><b>${esc(r.title)}</b><span class="tag conf-${r.confidence}" title="${esc(CONF[r.confidence][1])}">${CONF[r.confidence][0]}</span></div>
        <div class="small muted">${esc(r.detail)}</div></div>`).join("")}</div>`;
    }).join("");
    $$("[data-rid]", box).forEach((el) => (el.onclick = () => openItem(+el.dataset.rid)));
  }).catch((e) => { $("#nwRecs", host).innerHTML = `<span class="err small">${esc(e.message)}</span>`; });
  api(`/api/account/fills?acct=${encodeURIComponent(NW.acct)}&limit=25`).then((d) => {
    $("#nwFills", host).innerHTML = d.fills.length ? `<div class="mini-list">${d.fills.map((f) => `<div class="r" data-fid="${f.item}"><span class="tag ${f.side === "buy" ? "free" : "pump"}">${f.side}</span><img alt="" src="${esc(iconUrl(f.icon))}" onerror="this.style.visibility='hidden'"><span>${gp(f.qty)} ${esc(f.name || "Item " + f.item)}${f.caught_up ? ` <span class="muted small" title="Seen on login: filled some time before this">caught up</span>` : ""}</span><span class="v">${short(f.gp)} <span class="muted small">${ago(Date.now() / 1000 - f.t)} ago</span></span></div>`).join("")}</div>` : `<div class="muted small">No trades yet. They appear here as your GE offers fill.</div>`;
    $("#nwLoot", host).innerHTML = d.loot.length ? `<div class="mini-list">${d.loot.map((l) => `<div class="r static"><span><b>${esc(l.source || l.kind)}</b> <span class="muted small">${esc(l.items.slice(0, 3).map((x) => `${gp(x.qty)} ${x.name || "item"}`).join(", "))}${l.items.length > 3 ? "..." : ""}</span></span><span class="v">${short(l.value)} <span class="muted small">${ago(Date.now() / 1000 - l.t)} ago</span></span></div>`).join("")}</div>` : `<div class="muted small">No loot yet. Drops from the Loot Tracker appear here.</div>`;
    $$("[data-fid]", host).forEach((el) => (el.onclick = () => openItem(+el.dataset.fid)));
  }).catch(() => {});
};

function setupCard(st) {
  return `<div class="card setup" style="margin-bottom:14px"><h3 style="margin-top:0">Connect your account with the RuneLite plugin</h3>
    <p class="small">The Bankstanding plugin listens to RuneLite's own events (your GE offers, bank, inventory, equipment, loot) and writes them to a file on this computer. It never sends input to the game or reads the screen. This app reads that file, so trades and holdings update on their own.</p>
    <ol class="small">
      <li>Install Java 11 or newer (Adoptium Temurin is free).</li>
      <li>In the <code>runelite-plugin</code> folder of this app, double-click <code>run-plugin.bat</code> (or run <code>gradlew run</code>). This opens RuneLite with the plugin loaded.</li>
      <li>Log in, open your bank once, and open the Grand Exchange once. Everything after that is automatic.</li>
    </ol>
    <p class="small muted">Plugin files are read from <code>${esc(st.folder)}</code>${st.exists ? "" : " (not created yet)"}. Change it if you set a different output folder in the plugin's settings.</p>
    <div class="inline-form"><label class="field wide"><span>Plugin folder</span><input class="input" id="nwFolder" value="${esc(st.folder)}"></label><button class="btn primary" id="nwSetupSave">Save and check</button></div></div>`;
}

// Trading charts -------------------------------------------------------------------
// Bars from the Wiki's windows. The Wiki reports average instant-buy and instant-sell
// prices per window (no open or close), so candles are approximate: the close is the
// window's mid price, the open is the previous close, and the wicks reach the average
// instant-buy (high) and instant-sell (low) prices.
function buildBars(rows, bucket) {
  const out = [];
  let cur = null, prevClose = null;
  for (const r of rows) {
    const ah = r.avgHighPrice, al = r.avgLowPrice;
    if (ah == null && al == null) continue;
    const mid = ah != null && al != null ? (ah + al) / 2 : (ah != null ? ah : al);
    const bt = Math.floor(r.timestamp / bucket) * bucket;
    if (!cur || cur.t !== bt) {
      if (cur) { out.push(cur); prevClose = cur.c; }
      cur = { t: bt, o: prevClose != null ? prevClose : mid, h: -Infinity, l: Infinity, c: mid, hv: 0, lv: 0, n: 0 };
    }
    cur.h = Math.max(cur.h, ah != null ? ah : mid, mid);
    cur.l = Math.min(cur.l, al != null ? al : mid, mid);
    cur.c = mid;
    cur.hv += r.highPriceVolume || 0;
    cur.lv += r.lowPriceVolume || 0;
    cur.n++;
  }
  if (cur) out.push(cur);
  for (const b of out) { b.h = Math.max(b.h, b.o, b.c); b.l = Math.min(b.l, b.o, b.c); }
  return out;
}
function smaArr(v, n) { const o = []; let s = 0; v.forEach((x, i) => { s += x; if (i >= n) s -= v[i - n]; o.push(i >= n - 1 ? s / n : null); }); return o; }
function emaArr(v, n) { const o = [], k = 2 / (n + 1); let e = null; v.forEach((x, i) => { e = e == null ? x : x * k + e * (1 - k); o.push(i >= n - 1 ? e : null); }); return o; }
function bollArr(v, n = 20, k = 2) {
  const m = smaArr(v, n);
  return v.map((_, i) => {
    if (m[i] == null) return null;
    let s = 0; for (let j = i - n + 1; j <= i; j++) s += (v[j] - m[i]) ** 2;
    const sd = Math.sqrt(s / n); return { m: m[i], up: m[i] + k * sd, lo: m[i] - k * sd };
  });
}
function rsiArr(v, n = 14) {
  const o = new Array(v.length).fill(null); let g = 0, l = 0;
  for (let i = 1; i < v.length; i++) {
    const ch = v[i] - v[i - 1], up = Math.max(ch, 0), dn = Math.max(-ch, 0);
    if (i <= n) { g += up / n; l += dn / n; } else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; }
    if (i >= n) o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return o;
}
// Price label precision that suits the price (runes need decimals on averages).
function pfmt(v) { if (v == null) return "-"; const a = Math.abs(v); return a >= 1e5 ? short(v) : a >= 100 ? gp(v) : a >= 10 ? v.toFixed(1) : v.toFixed(2); }

// bars: [{t,o,h,l,c,hv,lv}]. opts: type ("candle" | "line"), ind (Set of "sma", "ema", "bb",
// "rsi", "vol", "events"), compare ([{name, color, pts:[{t,v}]}] shown as % change with the
// item on one axis), lines ([price]), events ([{t, title, kind}]), onPick(price) for drawing.
function tradingChart(host, bars, opts = {}) {
  const ind = opts.ind || new Set();
  if (!bars.length) { host.innerHTML = `<div class="empty">${esc(opts.empty || "No trades saved for this range yet.")}</div>`; return; }
  const W = Math.max(360, host.clientWidth || 800);
  const showVol = ind.has("vol"), showRsi = ind.has("rsi");
  const padL = 8, padR = 64, padT = 22, gap = 8, padB = 22;
  const H1 = opts.height || 340, H2 = showVol ? 72 : 0, H3 = showRsi ? 72 : 0;
  const H = padT + H1 + (H2 ? gap + H2 : 0) + (H3 ? gap + H3 : 0) + padB;
  const n = bars.length, plotW = W - padL - padR, slot = plotW / n;
  const xi = (i) => padL + slot * (i + 0.5);
  const closes = bars.map((b) => b.c);
  const cmp = (opts.compare || []).filter((s) => s.pts && s.pts.length > 1);
  const pctMode = cmp.length > 0;
  // In compare mode everything is % change from the first bar, on one axis.
  const base = closes[0];
  const conv = (v) => (pctMode ? v / base - 1 : v);
  const cmpSeries = cmp.map((s) => {
    const b0 = s.pts.find((p) => p.t >= bars[0].t) || s.pts[0];
    return Object.assign({}, s, { vals: bars.map((b) => { let best = null; for (const p of s.pts) { if (p.t <= b.t + 1) best = p; else break; } return best ? best.v / b0.v - 1 : null; }) });
  });
  const sma = ind.has("sma") ? smaArr(closes, 20) : null, ema = ind.has("ema") ? emaArr(closes, 50) : null;
  const bb = ind.has("bb") ? bollArr(closes) : null, rsi = showRsi ? rsiArr(closes) : null;
  let lo = Infinity, hi = -Infinity;
  const see = (v) => { if (v != null && isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } };
  bars.forEach((b, i) => {
    if (pctMode) see(conv(b.c)); else { see(b.l); see(b.h); }
    if (!pctMode && bb && bb[i]) { see(bb[i].up); see(bb[i].lo); }
  });
  cmpSeries.forEach((s) => s.vals.forEach(see));
  if (!pctMode) (opts.lines || []).forEach((p) => { if (p > lo * 0.8 && p < hi * 1.2) see(p); });
  const padY = (hi - lo) * 0.06 || Math.abs(hi) * 0.02 || 1; lo -= padY; hi += padY;
  const ticks = niceTicks(lo, hi, 5); lo = ticks[0]; hi = ticks[ticks.length - 1];
  const y = (v) => padT + H1 * (1 - (v - lo) / (hi - lo || 1));
  const yfmt = pctMode ? (v) => (v > 0 ? "+" : "") + pct(v, Math.abs(hi - lo) < 0.1 ? 1 : 0) : pfmt;
  const volTop = padT + H1 + gap, volBot = volTop + H2;
  const vmax = Math.max(1, ...bars.map((b) => b.hv + b.lv));
  const rsiTop = (H2 ? volBot : padT + H1) + gap, rsiBot = rsiTop + H3;
  const ry = (v) => rsiTop + H3 * (1 - v / 100);
  const bw = Math.max(1, Math.min(14, slot * 0.7));
  const line = (vals, f = (v) => v) => { let d = "", pen = false; vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + xi(i).toFixed(1) + "," + y(f(v)).toFixed(1); pen = true; }); return d; };
  let body = "";
  if (!pctMode && bb) {
    const up = [], dn = [];
    bb.forEach((b, i) => { if (b) { up.push([xi(i), y(b.up)]); dn.push([xi(i), y(b.lo)]); } });
    if (up.length > 1) body += `<path class="bb-band" d="M${up.map((p) => p.join(",")).join("L")}L${dn.reverse().map((p) => p.join(",")).join("L")}Z"/>`;
  }
  if (pctMode || opts.type === "line") {
    const upDay = closes[closes.length - 1] >= closes[0];
    body += `<path class="price-line ${upDay ? "up" : "down"}" d="${line(closes, conv)}"/>`;
  } else {
    body += bars.map((b, i) => {
      const up = b.c >= b.o, x = xi(i), yo = y(b.o), yc = y(b.c), top = Math.min(yo, yc), h = Math.max(1, Math.abs(yc - yo));
      // Up candles are hollow and down candles filled, so direction never relies on color alone.
      return `<line class="wick ${up ? "up" : "down"}" x1="${x}" x2="${x}" y1="${y(b.h)}" y2="${y(b.l)}"/><rect class="candle ${up ? "up" : "down"}" x="${(x - bw / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}"/>`;
    }).join("");
  }
  if (!pctMode && sma) body += `<path class="ov ov1" d="${line(sma)}"/>`;
  if (!pctMode && ema) body += `<path class="ov ov2" d="${line(ema)}"/>`;
  cmpSeries.forEach((s, k) => { body += `<path class="cmp" style="stroke:${s.color}" d="${line(s.vals)}"${k ? ` stroke-dasharray="5 4"` : ""}/>`; });
  if (pctMode) body += `<line class="base" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>`;
  if (!pctMode) (opts.lines || []).forEach((p, k) => { if (p >= lo && p <= hi) body += `<line class="pline" x1="${padL}" x2="${W - padR}" y1="${y(p)}" y2="${y(p)}"/><rect class="pl-tag" x="${W - padR + 2}" y="${y(p) - 8}" width="${padR - 4}" height="16" rx="3"/><text class="pl-txt" x="${W - padR + 6}" y="${y(p) + 4}">${pfmt(p)}</text>`; });
  // Last price marker on the axis.
  const lastV = conv(closes[n - 1]);
  const lastUp = closes[n - 1] >= (n > 1 ? closes[n - 2] : closes[0]);
  body += `<line class="last-line" x1="${padL}" x2="${W - padR}" y1="${y(lastV)}" y2="${y(lastV)}"/><rect class="last-tag ${lastUp ? "up" : "down"}" x="${W - padR + 2}" y="${y(lastV) - 8}" width="${padR - 4}" height="16" rx="3"/><text class="last-txt" x="${W - padR + 6}" y="${y(lastV) + 4}">${esc(yfmt(lastV))}</text>`;
  // Volume: instant buys and instant sells side by side in each slot.
  let vol = "";
  if (H2) {
    const half = Math.max(1, bw / 2 - 0.5);
    vol = bars.map((b, i) => {
      const x = xi(i), hb = (b.hv / vmax) * H2, hs = (b.lv / vmax) * H2;
      return `${hb ? `<rect class="vb up" x="${(x - half - 0.5).toFixed(1)}" y="${(volBot - hb).toFixed(1)}" width="${half.toFixed(1)}" height="${hb.toFixed(1)}"/>` : ""}${hs ? `<rect class="vb down" x="${(x + 0.5).toFixed(1)}" y="${(volBot - hs).toFixed(1)}" width="${half.toFixed(1)}" height="${hs.toFixed(1)}"/>` : ""}`;
    }).join("") + `<line class="axis" x1="${padL}" x2="${W - padR}" y1="${volBot}" y2="${volBot}"/><text x="${W - padR + 6}" y="${volTop + 10}">${short(vmax)}</text><text class="pane-lbl" x="${padL + 4}" y="${volTop + 10}">Volume: instant buys | instant sells</text>`;
  }
  let rsiSvg = "";
  if (H3) {
    rsiSvg = `<rect class="rsi-zone" x="${padL}" y="${ry(70)}" width="${plotW}" height="${ry(30) - ry(70)}"/>
      <line class="grid" x1="${padL}" x2="${W - padR}" y1="${ry(70)}" y2="${ry(70)}"/><line class="grid" x1="${padL}" x2="${W - padR}" y1="${ry(30)}" y2="${ry(30)}"/>
      <text x="${W - padR + 6}" y="${ry(70) + 4}">70</text><text x="${W - padR + 6}" y="${ry(30) + 4}">30</text>
      <path class="ov ov3" d="${rsi.map((v, i) => v == null ? "" : (i && rsi[i - 1] != null ? "L" : "M") + xi(i).toFixed(1) + "," + ry(v).toFixed(1)).join("")}"/>
      <text class="pane-lbl" x="${padL + 4}" y="${rsiTop + 10}">RSI 14</text>`;
  }
  // News flags along the bottom of the price pane.
  let flags = "";
  if (ind.has("events")) {
    (opts.events || []).forEach((ev) => {
      if (ev.t < bars[0].t || ev.t > bars[n - 1].t + 86400) return;
      let i = 0; while (i < n - 1 && bars[i + 1].t <= ev.t) i++;
      const x = xi(i);
      flags += `<g class="flag ${ev.upcoming ? "up" : ""}" data-ev="${esc(ev.title)}" data-t="${ev.t}" data-k="${esc(ev.kind || "")}"><line x1="${x}" x2="${x}" y1="${padT}" y2="${padT + H1}"/><circle cx="${x}" cy="${padT + H1 - 7}" r="6"/><text x="${x}" y="${padT + H1 - 4}" text-anchor="middle">${ev.upcoming ? "A" : "U"}</text></g>`;
    });
  }
  const span = bars[n - 1].t - bars[0].t, nx = Math.max(2, Math.min(7, Math.floor(plotW / 110)));
  const xt = []; for (let k = 0; k <= nx; k++) xt.push(Math.round((n - 1) * k / nx));
  const legend = [];
  if (pctMode) { legend.push([`var(--pos)`, opts.name || "This item"]); cmpSeries.forEach((s) => legend.push([s.color, s.name])); }
  else { if (sma) legend.push(["var(--series-2)", "SMA 20"]); if (ema) legend.push(["var(--series-3)", "EMA 50"]); if (bb) legend.push(["var(--muted)", "Bollinger 20, 2"]); }
  host.innerHTML = `<div class="tc-read" aria-live="off"></div>${legend.length ? `<div class="legend-inline tc-legend">${legend.map(([c, l]) => `<span><i style="background:${c}"></i>${esc(l)}</span>`).join("")}</div>` : ""}
    <svg class="chart tchart" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(opts.aria || "Price chart")}">
    ${ticks.map((t) => `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}"/><text x="${W - padR + 6}" y="${y(t) + 4}">${esc(yfmt(t))}</text>`).join("")}
    ${body}${flags}${vol}${rsiSvg}
    ${xt.map((i, k) => `<text x="${xi(i)}" y="${H - 5}" text-anchor="${k === 0 ? "start" : k === nx ? "end" : "middle"}">${axisTime(bars[i].t, span)}</text>`).join("")}
    <g class="hover" visibility="hidden"><line class="xhair" y1="${padT}" y2="${H - padB}"/><line class="yhair" x1="${padL}" x2="${W - padR}"/><rect class="yh-tag" x="${W - padR + 2}" width="${padR - 4}" height="16" rx="3"/><text class="yh-txt" x="${W - padR + 6}"></text></g>
    <rect x="${padL}" y="${padT}" width="${plotW}" height="${H - padT - padB}" fill="transparent" class="hit ${opts.drawing ? "drawing" : ""}"/>
  </svg>`;
  const svg = $("svg", host), g = $(".hover", svg), read = $(".tc-read", host);
  const readout = (i) => {
    const b = bars[i];
    const chg = b.c / b.o - 1;
    let s = `<span class="muted">${esc(fmtTime(b.t, true))}</span>`;
    if (pctMode) s += ` <b>${esc(opts.name || "Item")}</b> <span class="${signCls(conv(b.c))}">${yfmt(conv(b.c))}</span>` + cmpSeries.map((c) => ` <b>${esc(c.name)}</b> <span class="${signCls(c.vals[i])}">${c.vals[i] == null ? "-" : yfmt(c.vals[i])}</span>`).join("");
    else s += ` O <b>${pfmt(b.o)}</b> H <b>${pfmt(b.h)}</b> L <b>${pfmt(b.l)}</b> C <b>${pfmt(b.c)}</b> <span class="${signCls(chg)}">${chg > 0 ? "+" : ""}${pct(chg, 2)}</span>`;
    s += ` <span class="muted">Buys</span> ${short(b.hv)} <span class="muted">Sells</span> ${short(b.lv)}`;
    if (sma && sma[i] != null && !pctMode) s += ` <span class="muted">SMA</span> ${pfmt(sma[i])}`;
    if (ema && ema[i] != null && !pctMode) s += ` <span class="muted">EMA</span> ${pfmt(ema[i])}`;
    if (rsi && rsi[i] != null) s += ` <span class="muted">RSI</span> ${rsi[i].toFixed(0)}`;
    read.innerHTML = s;
  };
  readout(n - 1);
  const hit = $(".hit", svg);
  const pos = (e) => { const r = svg.getBoundingClientRect(); return [((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * H]; };
  hit.addEventListener("mousemove", (e) => {
    const [mx, my] = pos(e);
    const i = Math.max(0, Math.min(n - 1, Math.floor((mx - padL) / slot)));
    g.setAttribute("visibility", "visible");
    $(".xhair", g).setAttribute("x1", xi(i)); $(".xhair", g).setAttribute("x2", xi(i));
    const inPrice = my >= padT && my <= padT + H1;
    $(".yhair", g).style.display = $(".yh-tag", g).style.display = $(".yh-txt", g).style.display = inPrice ? "" : "none";
    if (inPrice) {
      $(".yhair", g).setAttribute("y1", my); $(".yhair", g).setAttribute("y2", my);
      $(".yh-tag", g).setAttribute("y", my - 8); $(".yh-txt", g).setAttribute("y", my + 4);
      $(".yh-txt", g).textContent = yfmt(lo + (hi - lo) * (1 - (my - padT) / H1));
    }
    readout(i);
  });
  hit.addEventListener("mouseleave", () => { g.setAttribute("visibility", "hidden"); readout(n - 1); });
  hit.addEventListener("click", (e) => {
    if (!opts.drawing || !opts.onPick || pctMode) return;
    const [, my] = pos(e);
    if (my < padT || my > padT + H1) return;
    opts.onPick(Math.round(lo + (hi - lo) * (1 - (my - padT) / H1)));
  });
  const tip = $("#tooltip");
  $$(".flag", svg).forEach((f) => {
    f.addEventListener("mousemove", (e) => tipAt(e, `<div class="muted">${esc(fmtTime(+f.dataset.t, true))} · ${esc(f.dataset.k)}</div><b>${esc(f.dataset.ev)}</b>`));
    f.addEventListener("mouseleave", () => (tip.hidden = true));
    f.addEventListener("click", () => opts.onEvent && opts.onEvent(f.dataset.ev));
  });
}

// Treemap heatmap (squarified). groups: [{name, items: [{id, name, value, chg}]}]. Size is
// value, color is the day's move on a diverging scale (down, neutral gray, up) clipped at
// +/- `range`; every tile big enough carries its signed % so color is never the only cue.
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function hexRgb(h) { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map((c) => c + c).join(""); const n = parseInt(h, 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function mixColor(a, b, t) { const x = hexRgb(a), y = hexRgb(b); return `rgb(${x.map((v, i) => Math.round(v + (y[i] - v) * t)).join(",")})`; }
function divColor(v, range) {
  const mid = cssVar("--div-mid"), pos = cssVar("--div-pos"), neg = cssVar("--div-neg");
  if (v == null) return mixColor(mid, mid, 0);
  const t = Math.max(-1, Math.min(1, v / range));
  return t >= 0 ? mixColor(mid, pos, t ** 0.75) : mixColor(mid, neg, (-t) ** 0.75);
}
function inkFor(rgb) { const [r, g, b] = rgb.match(/\d+/g).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.3 ? "#0b0c0e" : "#ffffff"; }
function squarify(items, x, y, w, h) {
  const out = [], total = items.reduce((s, d) => s + d.value, 0);
  if (!total || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let rest = items.map((d) => Object.assign({}, d, { area: d.value * scale }));
  const worst = (row, side) => { const s = row.reduce((a, d) => a + d.area, 0), mx = Math.max(...row.map((d) => d.area)), mn = Math.min(...row.map((d) => d.area)); return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn)); };
  while (rest.length) {
    const side = Math.min(w, h);
    let row = [rest[0]], i = 1;
    while (i < rest.length && worst(row.concat(rest[i]), side) <= worst(row, side)) { row.push(rest[i]); i++; }
    rest = rest.slice(i);
    const s = row.reduce((a, d) => a + d.area, 0);
    if (w >= h) { const cw = s / h; let cy = y; row.forEach((d) => { const ch = d.area / cw; out.push(Object.assign(d, { x, y: cy, w: cw, h: ch })); cy += ch; }); x += cw; w -= cw; }
    else { const ch = s / w; let cx = x; row.forEach((d) => { const cw = d.area / ch; out.push(Object.assign(d, { x: cx, y, w: cw, h: ch })); cx += cw; }); y += ch; h -= ch; }
  }
  return out;
}
function treemap(host, groups, opts = {}) {
  const W = Math.max(320, host.clientWidth || 800), H = opts.height || 380, range = opts.range || 0.05;
  groups = groups.map((g) => Object.assign({}, g, { value: g.items.reduce((s, d) => s + d.value, 0), items: g.items.filter((d) => d.value > 0).sort((a, b) => b.value - a.value) })).filter((g) => g.value > 0).sort((a, b) => b.value - a.value);
  if (!groups.length) { host.innerHTML = `<div class="empty">${esc(opts.empty || "Nothing to show yet.")}</div>`; return; }
  const cells = [];
  squarify(groups, 0, 0, W, H).forEach((g) => {
    const head = g.h > 34 && g.w > 60 ? 16 : 0;
    squarify(g.items, g.x + 1, g.y + head + 1, g.w - 2, g.h - head - 2).forEach((d) => cells.push(d));
    g.head = head;
  });
  const gl = squarify(groups, 0, 0, W, H);
  const txt = (d) => {
    if (d.w < 44 || d.h < 26) return "";
    const fs = Math.max(10, Math.min(15, Math.sqrt(d.w * d.h) / 9));
    const nm = d.name.length * fs * 0.55 > d.w - 8 ? d.name.slice(0, Math.max(3, Math.floor((d.w - 8) / (fs * 0.55)))) + "." : d.name;
    return `<text class="tm-name" x="${d.x + 5}" y="${d.y + fs + 3}" style="font-size:${fs}px">${esc(nm)}</text>${d.h > fs * 2 + 10 ? `<text class="tm-chg" x="${d.x + 5}" y="${d.y + fs * 2 + 6}" style="font-size:${fs - 1}px">${d.chg == null ? "" : (d.chg > 0 ? "+" : "") + pct(d.chg, 1)}</text>` : ""}`;
  };
  host.innerHTML = `<svg class="chart treemap" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${esc(opts.aria || "Heatmap")}">
    ${gl.map((g) => `<rect class="tm-group" x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}"/>${g.h > 34 && g.w > 60 ? `<text class="tm-gname" x="${g.x + 5}" y="${g.y + 12}">${esc(g.name.toUpperCase())}</text>` : ""}`).join("")}
    ${cells.map((d, i) => { const fill = divColor(d.chg, range); return `<g class="tm-cell" data-i="${i}" style="--ink:${inkFor(fill)}"><rect x="${d.x + 1}" y="${d.y + 1}" width="${Math.max(0, d.w - 2)}" height="${Math.max(0, d.h - 2)}" rx="2" style="fill:${fill}"/>${txt(d)}</g>`; }).join("")}
  </svg>
  <div class="heat-legend"><span>${"-" + pct(range, 0)}</span><span class="ramp div"></span><span>${"+" + pct(range, 0)}</span><span class="muted">${esc(opts.legend || "Size: value. Color: 24h change.")}</span></div>`;
  $$(".tm-cell", host).forEach((el) => {
    const d = cells[+el.dataset.i];
    el.addEventListener("mousemove", (e) => tipAt(e, `<b>${esc(d.name)}</b><div class="r"><span class="muted">${esc(opts.valueLabel || "Value")}</span><b>${short(d.value)}</b></div><div class="r"><span class="muted">24h</span><b class="${signCls(d.chg)}">${d.chg == null ? "-" : (d.chg > 0 ? "+" : "") + pct(d.chg, 2)}</b></div>${d.extra ? `<div class="muted">${esc(d.extra)}</div>` : ""}`));
    el.addEventListener("mouseleave", () => ($("#tooltip").hidden = true));
    el.addEventListener("click", () => opts.onPick && opts.onPick(d));
  });
}

// Terminal: a trading desk where every panel follows one item ----------------------
const TM = Object.assign({ id: 4151, tf: "1M", type: "candle", ind: ["vol", "events", "sma"], cmp: "none", cmpId: null, layout: "trade", list: "watch", bottom: "news" }, store.get("tm", {}));
TM.drawing = false;
const TF = {
  "1D": { res: "5m", days: 1, bucket: 900 }, "1W": { res: "1h", days: 7, bucket: 3600 },
  "1M": { res: "1h", days: 30, bucket: 14400 }, "3M": { res: "1d", days: 90, bucket: 86400 },
  "1Y": { res: "1d", days: 365, bucket: 86400 }, "2Y": { res: "1d", days: 800, bucket: 3 * 86400 },
};
const IND = [["sma", "SMA 20"], ["ema", "EMA 50"], ["bb", "Bollinger"], ["rsi", "RSI"], ["vol", "Volume"], ["events", "News flags"]];
let TM_DATA = null, TM_REPORT = null, TM_REQ = 0;
function saveTM() { const c = Object.assign({}, TM); delete c.drawing; store.set("tm", c); }
function tmLines(id) { return store.get("tm.lines." + id, []); }
function openTerminal(id) { TM.id = id; saveTM(); closeItem(); showTab("terminal"); }

async function tmHistory(id, tf) {
  const f = TF[tf];
  let d = await api(`/api/localhistory?id=${id}&res=${f.res}&days=${f.days}`);
  if (f.res === "1d" && d.data.length < 10) d = await api(`/api/localhistory?id=${id}&res=1h&days=${f.days}`);  // no daily import yet
  return buildBars(d.data, f.bucket);
}
async function tmCompare(tf) {
  const f = TF[tf];
  if (TM.cmp === "market") {
    if (f.days <= 30) {
      const d = await api(`/api/indices?days=${Math.max(1, Math.ceil(f.days))}`);
      const mk = (d.categories || []).find((c) => c.key === "market");
      return mk ? [{ name: "Market index", color: "var(--series-2)", pts: mk.series }] : [];
    }
    const d = await api(`/api/marketindex?days=${f.days}`);
    return [{ name: "Market index", color: "var(--series-2)", pts: d.series }];
  }
  if (TM.cmp === "item" && TM.cmpId) {
    const bars = await tmHistory(TM.cmpId, tf);
    const nm = (S.byId.get(TM.cmpId) || {}).name || "Item " + TM.cmpId;
    return [{ name: nm, color: "var(--series-3)", pts: bars.map((b) => ({ t: b.t, v: b.c })) }];
  }
  return [];
}

function tmListRows() {
  const liquid = S.rows.filter((r) => r.high && (r.vol24 || 0) > 500);
  if (TM.list === "watch") return [...S.watch].map((id) => S.byId.get(id)).filter(Boolean);
  if (TM.list === "hold") return (TM_DATA && TM_DATA.holdIds ? TM_DATA.holdIds : []).map((id) => S.byId.get(id)).filter(Boolean);
  if (TM.list === "gain") return liquid.filter((r) => r.chg24h != null && (r.vol24 || 0) > 2000).sort((a, b) => b.chg24h - a.chg24h).slice(0, 40);
  if (TM.list === "lose") return liquid.filter((r) => r.chg24h != null && (r.vol24 || 0) > 2000).sort((a, b) => a.chg24h - b.chg24h).slice(0, 40);
  return liquid.sort((a, b) => b.vol24 * b.high - a.vol24 * a.high).slice(0, 60);
}
function drawTmList(host) {
  const rows = tmListRows();
  $("#tmList", host).innerHTML = rows.length ? rows.map((r) => `<div class="tm-row ${r.id === TM.id ? "on" : ""}" data-id="${r.id}" tabindex="-1">
      <img alt="" src="${esc(iconUrl(r.icon))}" onerror="this.style.visibility='hidden'"><span class="nm">${esc(r.name)}</span>
      <span class="px">${pfmt(r.high)}</span><span class="ch ${signCls(r.chg24h)}">${r.chg24h == null ? "-" : (r.chg24h > 0 ? "+" : "") + pct(r.chg24h, 1)}</span></div>`).join("")
    : `<div class="empty small">${TM.list === "watch" ? "Star items to build a watchlist." : TM.list === "hold" ? "No holdings yet." : "Nothing here."}</div>`;
  $$(".tm-row", host).forEach((el) => (el.onclick = () => tmSelect(host, +el.dataset.id)));
}
function tmSelect(host, id) { TM.id = id; saveTM(); renderers.terminal(host, false); }

function tmSignalsNow(bars) {
  // Signals on the daily bars right now, named the same way as the indicator report.
  const c = bars.map((b) => b.c), n = c.length, out = [];
  if (n < 60) return out;
  const r = rsiArr(c), s20 = smaArr(c, 20), s50 = smaArr(c, 50), bb = bollArr(c);
  if (r[n - 1] < 30) out.push(["rsi_low", `RSI ${r[n - 1].toFixed(0)}: oversold`]);
  if (r[n - 1] > 70) out.push(["rsi_high", `RSI ${r[n - 1].toFixed(0)}: overbought`]);
  for (let i = Math.max(51, n - 5); i < n; i++) {
    if (s20[i - 1] <= s50[i - 1] && s20[i] > s50[i]) out.push(["golden", "20 day average crossed above the 50 day"]);
    if (s20[i - 1] >= s50[i - 1] && s20[i] < s50[i]) out.push(["death", "20 day average crossed below the 50 day"]);
  }
  if (bb[n - 1] && c[n - 1] < bb[n - 1].lo) out.push(["bb_low", "Below the lower Bollinger band"]);
  if (bb[n - 1] && c[n - 1] > bb[n - 1].up) out.push(["bb_high", "Above the upper Bollinger band"]);
  if (n > 91) { const w = c.slice(n - 91, n - 1); if (c[n - 1] > Math.max(...w)) out.push(["high90", "New 90 day high"]); if (c[n - 1] < Math.min(...w)) out.push(["low90", "New 90 day low"]); }
  return out;
}
const VERDICT_CLS = { "worked in both halves": "pos", "worked in reverse": "neg", "only in one half": "warn-txt", "no edge": "muted", "too few": "muted" };

renderers.terminal = async function (host, soft) {
  const req = ++TM_REQ;
  if (!soft || !$(".tm", host)) {
    host.innerHTML = `<div class="tm layout-${TM.layout}">
      <div class="tm-bar">
        <div class="search-wrap tm-search">${pickerField("", "tmFind", "field tm-find")}</div>
        <div class="tm-head" id="tmHead"></div>
        <div class="right">
          <div class="seg" id="tmLayout" title="Layout (L)">${[["trade", "Trade"], ["chart", "Chart"], ["research", "Research"]].map(([k, l]) => `<button data-k="${k}" class="${TM.layout === k ? "on" : ""}">${l}</button>`).join("")}</div>
        </div>
      </div>
      <div class="tm-grid">
        <aside class="tm-left panel"><div class="seg tm-tabs" id="tmListTabs">${[["watch", "Watch"], ["hold", "Held"], ["gain", "Gainers"], ["lose", "Losers"], ["top", "Top"]].map(([k, l]) => `<button data-k="${k}" class="${TM.list === k ? "on" : ""}">${l}</button>`).join("")}</div><div id="tmList" class="tm-list"></div></aside>
        <section class="tm-center panel">
          <div class="tm-tools">
            <div class="seg" id="tmTf">${Object.keys(TF).map((k, i) => `<button data-k="${k}" class="${TM.tf === k ? "on" : ""}" title="Key ${i + 1}">${k}</button>`).join("")}</div>
            <div class="seg" id="tmType">${[["candle", "Candles"], ["line", "Line"]].map(([k, l]) => `<button data-k="${k}" class="${TM.type === k ? "on" : ""}">${l}</button>`).join("")}</div>
            <div class="chips tm-ind" id="tmInd">${IND.map(([k, l]) => `<button class="chip ${TM.ind.includes(k) ? "on" : ""}" data-k="${k}">${l}</button>`).join("")}</div>
            <label class="tm-cmp">Compare <select class="input" id="tmCmp"><option value="none">None</option><option value="market" ${TM.cmp === "market" ? "selected" : ""}>Market index</option><option value="item" ${TM.cmp === "item" ? "selected" : ""}>${TM.cmp === "item" && TM.cmpId ? esc((S.byId.get(TM.cmpId) || {}).name || "Item") : "Another item..."}</option></select></label>
            <button class="btn small ${TM.drawing ? "primary" : ""}" id="tmDraw" title="Click the chart to add a price line (D)">Price line</button>
            <button class="btn small" id="tmClear" title="Remove this item's price lines">Clear lines</button>
          </div>
          <div id="tmCmpPick" hidden>${pickerField("Compare with", "tmCmpItem", "field")}</div>
          <div id="tmChart" class="tm-chart"></div>
          <div class="muted small tm-note">Candles are built from the Wiki's average prices per window: the close is the mid price and the wicks reach the average instant-buy and instant-sell prices. Hollow candles closed up, filled closed down.</div>
        </section>
        <aside class="tm-right panel" id="tmRight"></aside>
        <section class="tm-bottom panel">
          <div class="seg tm-tabs" id="tmBottomTabs">${[["news", "News and events"], ["signals", "Signals"], ["trades", "Your trades"]].map(([k, l]) => `<button data-k="${k}" class="${TM.bottom === k ? "on" : ""}">${l}</button>`).join("")}</div>
          <div id="tmBottom"></div>
        </section>
      </div>
      <div class="muted small tm-keys">Keys: / search · J and K move through the list · 1 to 6 timeframe · C candles or line · D price line · W watch · L layout</div>
    </div>`;
    bindTerminal(host);
  }
  drawTmList(host);
  let d, bars, cmp;
  try {
    [d, bars, cmp] = await Promise.all([api("/api/terminal?id=" + TM.id), tmHistory(TM.id, TM.tf), tmCompare(TM.tf)]);
  } catch (e) { $("#tmChart", host).innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  if (req !== TM_REQ) return;  // a newer selection is loading
  if (!TM_DATA || !TM_DATA.holdIds) {
    api("/api/networth").then((v) => { TM_DATA = Object.assign(TM_DATA || {}, { holdIds: v.holdings.filter((h) => h.how !== "cash").map((h) => h.id) }); if (TM.list === "hold") drawTmList(host); }).catch(() => {});
  }
  TM_DATA = Object.assign(TM_DATA || {}, { d, bars });
  const m = d.meta, r = d.row || {}, st = d.stats || {};
  const dayChg = r.chg24h;
  $("#tmHead", host).innerHTML = `<img alt="" src="${esc(iconUrl(m.icon))}" onerror="this.style.visibility='hidden'">
    <div><div class="tm-name">${esc(m.name)} ${star(m.id)}</div><div class="muted small">${m.members ? "Members" : "Free to play"} · limit ${gp(m.limit)} · ID ${m.id}</div></div>
    <div class="tm-price"><span class="big">${pfmt(r.high)}</span> <span class="${signCls(dayChg)}">${dayChg == null ? "" : (dayChg > 0 ? "+" : "") + pct(dayChg, 2) + " today"}</span></div>`;
  bindRowClicks($("#tmHead", host));
  const drawChart = () => tradingChart($("#tmChart", host), bars, {
    type: TM.type, ind: new Set(TM.ind), compare: cmp, name: m.name, lines: tmLines(TM.id), drawing: TM.drawing,
    height: TM.layout === "chart" ? 460 : 360,
    events: (d.news || []).map((n) => ({ t: n.t, title: n.title, kind: n.kindLabel, upcoming: n.upcoming })),
    onPick: (p) => { const l = tmLines(TM.id); l.push(p); store.set("tm.lines." + TM.id, l); TM.drawing = false; $("#tmDraw", host).classList.remove("primary"); drawChart(); },
    onEvent: () => { TM.bottom = "news"; saveTM(); drawTmBottom(host); },
    aria: `${m.name} price chart`,
  });
  drawChart();
  TM_DATA.redraw = drawChart;
  drawTmRight(host, d);
  drawTmBottom(host);
};

function drawTmRight(host, d) {
  const m = d.meta, r = d.row || {}, p = d.position, st = d.stats || {};
  const spread = r.high && r.low ? r.high - r.low : null;
  const rangePos = st.low52 && st.high52 && r.high ? (r.high - st.low52) / Math.max(1, st.high52 - st.low52) : null;
  $("#tmRight", host).innerHTML = `
    <div class="q-grid">
      <div class="q"><div class="k">Instant buy</div><div class="v">${pfmt(r.high)}</div><div class="s muted">sell here</div></div>
      <div class="q"><div class="k">Instant sell</div><div class="v">${pfmt(r.low)}</div><div class="s muted">buy here</div></div>
    </div>
    <div class="kv small">
      <span class="k">Spread</span><span>${spread == null ? "-" : pfmt(spread)} <span class="muted">${r.low ? pct(spread / r.low, 2) : ""}</span></span>
      <span class="k">Tax on a sale</span><span>${d.taxEach == null ? "-" : gp(d.taxEach)}</span>
      <span class="k">Flip profit each</span><span class="${signCls(r.profit)}">${r.profit == null ? "-" : signed(r.profit)} <span class="muted">${pct(r.roi, 2)}</span></span>
      <span class="k">Break-even sell</span><span>${gp(d.breakeven)}</span>
      <span class="k">Suggested offers</span><span>buy ${pfmt(r.low ? r.low + 1 : null)} · sell ${pfmt(r.high ? r.high - 1 : null)}</span>
      <span class="k">Your limit left</span><span>${d.limit ? gp(d.limit.left) + ` <span class="muted">resets ${ago(d.limit.resetAt - Date.now() / 1000)}</span>` : gp(m.limit)}</span>
      <span class="k">Fill time</span><span>${r.fillHrs != null ? (r.fillHrs < 10 ? r.fillHrs.toFixed(1) : Math.round(r.fillHrs)) + "h for a limit" : "-"}</span>
      <span class="k">Margin held</span><span>${r.stability != null ? pct(r.stability, 0) + " of recent windows" : "-"}</span>
    </div>
    <h4>Your position</h4>
    ${p ? `<div class="kv small">
      <span class="k">Held</span><span><b>${gp(p.qty)}</b> <span class="muted">${esc(Object.keys(p.where).join(", "))}</span></span>
      <span class="k">Value</span><span><b>${short(p.value)}</b> <span class="muted">${pct(p.share, 1)} of net worth</span></span>
      <span class="k">Avg cost</span><span>${p.costEach == null ? "-" : gp(p.costEach) + (p.costFrom && p.costFrom !== "trades" ? `<span class="muted" title="Value when first seen">*</span>` : "")}</span>
      <span class="k">Unrealized</span><span class="${signCls(p.pnl)}">${p.pnl == null ? "-" : signed(p.pnl, short)}</span>
      <span class="k">Today</span><span class="${signCls(p.chg24gp)}">${p.chg24gp ? signed(p.chg24gp, short) : "-"}</span></div>` : `<div class="muted small">You don't hold any.</div>`}
    ${d.slots.length ? `<h4>Your GE offers</h4>${d.slots.map((s) => `<div class="small tm-slot"><span class="tag ${s.side === "buy" ? "free" : "pump"}">${s.side}</span> ${gp(s.done)} / ${gp(s.total)} at ${pfmt(s.price)} <div class="progress"><i style="width:${Math.round(s.progress * 100)}%"></i></div>${s.note ? `<div class="warn-txt">${esc(s.note)}</div>` : ""}</div>`).join("")}` : ""}
    <h4>Key stats</h4>
    <div class="kv small">
      <span class="k">1 year range</span><span>${st.low52 ? `${pfmt(st.low52)} to ${pfmt(st.high52)}` : "-"}</span>
      ${rangePos != null ? `<span class="k"></span><span><span class="range-bar"><i style="left:${Math.max(0, Math.min(100, rangePos * 100))}%"></i></span></span>` : ""}
      <span class="k">30 / 90 days</span><span><span class="${signCls(st.chg30)}">${st.chg30 == null ? "-" : sgnPct(st.chg30)}</span> · <span class="${signCls(st.chg90)}">${st.chg90 == null ? "-" : sgnPct(st.chg90)}</span></span>
      <span class="k">1 year</span><span class="${signCls(st.chg365)}">${st.chg365 == null ? "-" : sgnPct(st.chg365)}</span>
      <span class="k" title="Standard deviation of daily moves, last 90 days">Daily swing</span><span>${st.vol == null ? "-" : pct(st.vol, 1)}</span>
      <span class="k" title="How much it tends to move when the market index moves 1%">Beta</span><span>${st.beta == null ? "-" : st.beta.toFixed(2)}${st.corr != null ? ` <span class="muted">corr ${st.corr.toFixed(2)}</span>` : ""}</span>
      <span class="k">24h volume</span><span>${short(r.vol24)} <span class="muted">${r.buyPressure != null ? pct(r.buyPressure, 0) + " buys" : ""}</span></span>
      <span class="k">High alch</span><span>${gp(m.highalch)}</span>
    </div>
    <div class="tm-actions"><button class="btn small" id="tmDetails">Item details</button> <a class="btn small" href="${wikiUrl(m.name)}" target="_blank" rel="noopener">Wiki</a></div>`;
  $("#tmDetails", host).onclick = () => openItem(m.id);
}

async function drawTmBottom(host) {
  const box = $("#tmBottom", host), d = TM_DATA && TM_DATA.d;
  if (!d) return;
  $$("#tmBottomTabs button", host).forEach((b) => b.classList.toggle("on", b.dataset.k === TM.bottom));
  if (TM.bottom === "trades") {
    box.innerHTML = d.fills.length ? `<table>${thead([{ label: "When" }, { label: "Side" }, { label: "Qty", num: 1 }, { label: "Each", num: 1 }, { label: "Total", num: 1 }], null)}<tbody>${d.fills.map((f) => `<tr class="static"><td class="muted">${esc(fmtTime(f.t, true))}</td><td><span class="tag ${f.side === "buy" ? "free" : "pump"}">${f.side}</span></td><td class="num">${gp(f.qty)}</td><td class="num">${gp(f.gp / f.qty)}</td><td class="num">${short(f.gp)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No trades in this item yet. With the RuneLite plugin running, your GE trades appear here.</div>`;
    return;
  }
  if (TM.bottom === "signals") {
    const daily = TM.tf === "1Y" || TM.tf === "3M" || TM.tf === "2Y" ? TM_DATA.bars : buildBars((await api(`/api/localhistory?id=${TM.id}&res=1d&days=400`)).data, 86400);
    const now = tmSignalsNow(daily);
    if (!TM_REPORT || TM_REPORT.running) TM_REPORT = await api("/api/indicators/report").catch(() => null);
    const rep = TM_REPORT && TM_REPORT.signals ? new Map(TM_REPORT.signals.map((s) => [s.key, s])) : new Map();
    box.innerHTML = `<p class="small muted" style="margin-top:0">Signals firing on this item's daily chart now, and what each signal has done across the market's history (move over the next 7 days after tax, beyond the market and beyond buying on a random day). A signal is only worth weight if it worked in both halves of the history.</p>
      ${now.length ? `<table>${thead([{ label: "Signal now" }, { label: "History (7 days)" }, { label: "Avg extra move", num: 1 }, { label: "Times seen", num: 1 }], null)}<tbody>${now.map(([k, txt]) => { const s = rep.get(k); const h = s && s["7"]; return `<tr class="static"><td><b>${esc(txt)}</b></td><td class="${h ? VERDICT_CLS[h.verdict] || "" : "muted"}">${h ? esc(h.verdict) : TM_REPORT && TM_REPORT.running ? "Testing..." : "-"}</td><td class="num ${h ? signCls(h.excess) : ""}">${h && h.excess != null ? sgnPct(h.excess, 2) : "-"}</td><td class="num">${h ? gp(h.n) : "-"}</td></tr>`; }).join("")}</tbody></table>` : `<div class="muted">No indicator signals on this item right now${daily.length < 60 ? " (needs about 60 days of daily history)" : ""}.</div>`}
      <p class="small"><a href="#" id="tmAllSignals">See every indicator's test</a></p>`;
    $("#tmAllSignals", box).onclick = (e) => { e.preventDefault(); BT_SHOW_IND = true; showTab("backtest"); };
    return;
  }
  box.innerHTML = `<div class="grid2 tm-news"><div id="tmNewsList"></div><div id="tmEv"><div class="muted small">Loading event history...</div></div></div>`;
  $("#tmNewsList", box).innerHTML = d.news.length ? `<div class="news-list">${d.news.map(newsCard).join("")}</div>` : `<div class="muted small">No posts have mentioned this item in the saved news.</div>`;
  bindNewsCards(box);
  const ev = await api("/api/events/item?id=" + TM.id).catch(() => null);
  if (!ev || !$("#tmEv", box)) return;
  const s7 = ev.summary && ev.summary.d7;
  $("#tmEv", box).innerHTML = ev.events.length ? `<h4 style="margin-top:0">How it moved around past posts</h4>
    <p class="small muted" style="margin-top:0">Change beyond the market: the week before, the day of, and the week and month after.${s7 && s7.n ? ` Over ${s7.n} post${s7.n > 1 ? "s" : ""}, the week after averaged <b class="${signCls(s7.mean)}">${sgnPct(s7.mean, 1)}</b>.` : ""}</p>
    <div class="tscroll"><table>${thead([{ label: "Post" }, { label: "Week before", num: 1 }, { label: "Day", num: 1 }, { label: "Week after", num: 1 }, { label: "Month after", num: 1 }], null)}<tbody>${ev.events.map((e) => `<tr class="static"><td><span class="muted small">${esc(new Date(e.t * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: "2-digit" }))}</span> ${esc(e.title)}</td>${["pre7", "d1", "d7", "d30"].map((w) => `<td class="num ${signCls(e[w])}">${e[w] == null ? "-" : sgnPct(e[w], 1)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<div class="muted small">No past posts to measure yet.</div>`;
}

function bindTerminal(host) {
  makePicker($("#tmFind", host), $("#tmFindList", host), (r) => { $("#tmFind", host).value = ""; tmSelect(host, r.id); });
  $("#tmFind", host).placeholder = "Search an item ( / )";
  makePicker($("#tmCmpItem", host), $("#tmCmpItemList", host), (r) => { TM.cmp = "item"; TM.cmpId = r.id; saveTM(); $("#tmCmpPick", host).hidden = true; renderers.terminal(host, false); });
  $$("#tmLayout button", host).forEach((b) => (b.onclick = () => { TM.layout = b.dataset.k; saveTM(); renderers.terminal(host, false); }));
  $$("#tmListTabs button", host).forEach((b) => (b.onclick = () => { TM.list = b.dataset.k; saveTM(); $$("#tmListTabs button", host).forEach((x) => x.classList.toggle("on", x === b)); drawTmList(host); }));
  $$("#tmTf button", host).forEach((b) => (b.onclick = () => { TM.tf = b.dataset.k; saveTM(); $$("#tmTf button", host).forEach((x) => x.classList.toggle("on", x === b)); renderers.terminal(host, true); }));
  $$("#tmType button", host).forEach((b) => (b.onclick = () => { TM.type = b.dataset.k; saveTM(); $$("#tmType button", host).forEach((x) => x.classList.toggle("on", x === b)); if (TM_DATA && TM_DATA.redraw) TM_DATA.redraw(); }));
  $$("#tmInd button", host).forEach((b) => (b.onclick = () => { const k = b.dataset.k; TM.ind = TM.ind.includes(k) ? TM.ind.filter((x) => x !== k) : TM.ind.concat(k); saveTM(); b.classList.toggle("on"); if (TM_DATA && TM_DATA.redraw) TM_DATA.redraw(); }));
  $("#tmCmp", host).onchange = (e) => {
    if (e.target.value === "item") { $("#tmCmpPick", host).hidden = false; $("#tmCmpItem", host).focus(); return; }
    TM.cmp = e.target.value; saveTM(); renderers.terminal(host, true);
  };
  $("#tmDraw", host).onclick = () => { TM.drawing = !TM.drawing; $("#tmDraw", host).classList.toggle("primary", TM.drawing); if (TM_DATA && TM_DATA.redraw) TM_DATA.redraw(); };
  $("#tmClear", host).onclick = () => { store.set("tm.lines." + TM.id, []); if (TM_DATA && TM_DATA.redraw) TM_DATA.redraw(); };
  $$("#tmBottomTabs button", host).forEach((b) => (b.onclick = () => { TM.bottom = b.dataset.k; saveTM(); drawTmBottom(host); }));
}

document.addEventListener("keydown", (e) => {
  if (S.tab !== "terminal" || e.ctrlKey || e.metaKey || e.altKey) return;
  const host = $("#tab-terminal");
  if (e.target.closest("input, textarea, select")) { if (e.key === "Escape") e.target.blur(); return; }
  const k = e.key.toLowerCase();
  if (k === "/") { e.preventDefault(); $("#tmFind", host).focus(); return; }
  if (k === "j" || k === "k" || k === "arrowdown" || k === "arrowup") {
    const rows = tmListRows(); if (!rows.length) return;
    e.preventDefault();
    const i = rows.findIndex((r) => r.id === TM.id), step = k === "j" || k === "arrowdown" ? 1 : -1;
    tmSelect(host, rows[Math.max(0, Math.min(rows.length - 1, (i < 0 ? -step : i) + step))].id);
    return;
  }
  const tfs = Object.keys(TF);
  if (/^[1-6]$/.test(k)) { TM.tf = tfs[+k - 1]; saveTM(); renderers.terminal(host, false); return; }
  if (k === "c") { TM.type = TM.type === "candle" ? "line" : "candle"; saveTM(); renderers.terminal(host, false); return; }
  if (k === "d") { $("#tmDraw", host).click(); return; }
  if (k === "w") { toggleWatch(TM.id); return; }
  if (k === "l") { const ls = ["trade", "chart", "research"]; TM.layout = ls[(ls.indexOf(TM.layout) + 1) % 3]; saveTM(); renderers.terminal(host, false); }
});

// News: game updates, blogs, polls and what past updates did to prices ----------------
const NS = Object.assign({ kind: "", days: 90 }, store.get("ns", {}));
const KIND_CLS = { game: "pump", devblog: "free", future: "free", poll: "free", community: "", technical: "", event: "", news: "" };
let BT_SHOW_IND = false;
function newsCard(n) {
  const held = new Set(n.held || []);
  return `<article class="news-card ${n.upcoming ? "upcoming" : ""}">
    <div class="news-top"><span class="tag ${KIND_CLS[n.kind] || ""}">${esc(n.kindLabel)}</span>${n.upcoming ? `<span class="tag spike" title="Announced, not in the game yet">Upcoming</span>` : ""}<span class="muted small">${esc(new Date(n.t * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" }))}</span></div>
    <div class="news-title">${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)}</div>
    ${n.summary ? `<div class="small muted">${esc(n.summary)}</div>` : ""}
    ${n.items.length ? `<div class="news-items">${n.items.slice(0, 12).map((i) => `<button class="item-chip ${held.has(i.id) ? "held" : ""}" data-tid="${i.id}" title="${held.has(i.id) ? "You hold this. " : ""}Open in the Terminal"><img alt="" src="${esc(iconUrl(i.icon))}" onerror="this.style.display='none'">${esc(i.name)}</button>`).join("")}${n.items.length > 12 ? `<span class="muted small">+${n.items.length - 12} more</span>` : ""}</div>` : ""}
  </article>`;
}
function bindNewsCards(box) { $$("[data-tid]", box).forEach((b) => (b.onclick = () => openTerminal(+b.dataset.tid))); }
function studyTable(st) {
  const W = [["pre7", "Week before"], ["d1", "Day of"], ["d7", "Week after"], ["d30", "Month after"]];
  const cell = (s, v) => !s || !s.n ? `<td class="num muted">-</td>` : `<td class="num" title="${s.n} item moves · higher in ${pct(s.up, 0)}${s.t != null ? ` · t ${s.t.toFixed(1)}` : ""}"><span class="${signCls(s.mean)}">${sgnPct(s.mean, 1)}</span><div class="small ${v === "rises" || v === "falls" ? (v === "rises" ? "pos" : "neg") : "muted"}">${esc(v || "")}</div></td>`;
  const ctrl = st.control || {};
  return `<div class="tscroll"><table>${thead([{ label: "" }].concat(W.map(([, l]) => ({ label: l, num: 1 }))), null)}<tbody>
    ${st.groups.map((g) => `<tr class="static"><td><b>${esc(g.label)}</b><div class="small muted">${gp(g.windows.d7.n || 0)} item moves</div></td>${W.map(([k]) => cell(g.windows[k], g.verdict[k])).join("")}</tr>`).join("")}
    <tr class="static"><td><b>Typical size of a move</b><div class="small muted">Ordinary days, either direction</div></td>${W.map(([k]) => `<td class="num muted">${ctrl[k] && ctrl[k].absMean != null ? pct(ctrl[k].absMean, 1) : "-"}</td>`).join("")}</tr>
    ${st.groups.map((g) => `<tr class="static"><td class="small">Size of moves after: ${esc(g.label.split(" (")[0].toLowerCase())}</td>${W.map(([k]) => `<td class="num small">${g.windows[k] && g.windows[k].absMean != null ? pct(g.windows[k].absMean, 1) : "-"}</td>`).join("")}</tr>`).join("")}
  </tbody></table></div>`;
}
renderers.news = async function (host) {
  host.innerHTML = `<div class="section-head" style="margin-top:0"><h2 style="margin:0">News and updates</h2>
      <div class="right"><div class="chips" style="margin:0" id="nsKind">${[["", "All"], ["game", "Game updates"], ["upcoming", "Upcoming"], ["held", "Mentions your items"]].map(([k, l]) => `<button class="chip ${NS.kind === k ? "on" : ""}" data-k="${k}">${l}</button>`).join("")}</div>
      <select class="input" id="nsDays" style="width:auto">${[30, 90, 365, 800].map((d) => `<option value="${d}" ${NS.days === d ? "selected" : ""}>${d === 800 ? "2 years" : d === 365 ? "1 year" : d + " days"}</option>`).join("")}</select></div></div>
    <p class="lede">Game updates, developer blogs, polls and Jagex news, tagged with the tradeable items each post mentions. Items you hold are highlighted. Click an item to open it in the Terminal, where its chart shows these posts as flags.</p>
    <div class="news-layout"><div id="nsFeed"><div class="muted small">Loading...</div></div>
      <aside><div class="card"><h3 style="margin-top:0">What posts did to prices</h3><div id="nsStudy" class="small muted">Measuring...</div></div>
      <div class="card" style="margin-top:14px"><h3 style="margin-top:0">By item class (week after game updates)</h3><div id="nsCats" class="small muted"></div></div></aside></div>`;
  $$("#nsKind button", host).forEach((b) => (b.onclick = () => { NS.kind = b.dataset.k; store.set("ns", NS); renderers.news(host); }));
  $("#nsDays", host).onchange = (e) => { NS.days = +e.target.value; store.set("ns", NS); renderers.news(host); };
  api(`/api/news?days=${NS.days}&kind=${NS.kind}&limit=200`).then((d) => {
    const feed = $("#nsFeed", host);
    if (!d.items.length) { feed.innerHTML = `<div class="empty">${d.status && d.status.n ? "No posts match." : "No news saved yet. The app fetches two years of posts in the background on first start."}</div>`; return; }
    let last = "";
    feed.innerHTML = d.items.map((n) => {
      const mo = new Date(n.t * 1000).toLocaleDateString([], { month: "long", year: "numeric" });
      const head = mo !== last ? `<div class="news-month">${esc(mo)}</div>` : ""; last = mo;
      return head + newsCard(n);
    }).join("");
    bindNewsCards(feed);
  }).catch((e) => ($("#nsFeed", host).innerHTML = `<div class="notice">${esc(e.message)}</div>`));
  const loadStudy = async () => {
    const st = await api("/api/events/study").catch(() => null);
    if (!$("#nsStudy", host)) return;
    if (!st || st.running) { $("#nsStudy", host).textContent = "Measuring every past post against price history..."; setTimeout(loadStudy, 3000); return; }
    if (!st.ok) { $("#nsStudy", host).textContent = st.reason || "Not enough data yet."; return; }
    $("#nsStudy", host).innerHTML = `<p style="margin-top:0">Each cell is the average move of the items a post mentioned, beyond the market over the same days (${gp(st.posts)} posts, ${gp(st.pairs)} item mentions${st.from ? ", since " + esc(new Date(st.from * 1000).toLocaleDateString([], { month: "short", year: "numeric" })) : ""}). A direction only counts when it stands out from noise (t of 2 or more over at least 15 moves).</p>${studyTable(st)}
      <p style="margin-bottom:0">Compare the size rows with the typical move: if moves around posts are bigger than on ordinary days, updates bring volatility even when the direction is a coin flip.</p>`;
    $("#nsCats", host).innerHTML = st.categories.length ? `<table>${thead([{ label: "Class" }, { label: "Moves", num: 1 }, { label: "Avg", num: 1 }, { label: "Higher", num: 1 }, { label: "" }], null)}<tbody>${st.categories.map((c) => `<tr class="static"><td>${esc(c.category)}</td><td class="num">${c.windows.d7.n}</td><td class="num ${signCls(c.windows.d7.mean)}">${sgnPct(c.windows.d7.mean, 1)}</td><td class="num">${pct(c.windows.d7.up, 0)}</td><td class="small ${c.verdict === "rises" ? "pos" : c.verdict === "falls" ? "neg" : "muted"}">${esc(c.verdict)}</td></tr>`).join("")}</tbody></table>` : "Not enough tagged posts per class yet.";
  };
  loadStudy();
};

// Net worth: performance vs the market, attribution, risk, heatmap -------------------
const ATTR = [["mkt", "Market moves", "Price changes on what you held"], ["trade", "Trading", "Buying under or selling over what items are worth"], ["loot", "Loot", "Drops recorded by the Loot Tracker"], ["other", "Other", "Skilling, alching, spending, eating, player trades, deaths"]];
function attrBars(parts) {
  const mx = Math.max(1, ...ATTR.map(([k]) => Math.abs(parts[k] || 0)));
  return `<div class="attr">${ATTR.map(([k, l, t]) => { const v = parts[k] || 0, w = Math.abs(v) / mx * 50; return `<div class="attr-row" title="${esc(t)}"><span class="lbl">${esc(l)}</span><span class="track"><i class="${v >= 0 ? "up" : "down"}" style="${v >= 0 ? `left:50%` : `left:${50 - w}%`};width:${w}%"></i></span><span class="val ${signCls(v)}">${signed(v, short)}</span></div>`; }).join("")}</div>`;
}
async function loadPerformance(host, acct, range) {
  const box = $("#nwPerf", host);
  if (!box) return;
  const days = range === "all" ? "" : range;
  const p = await api(`/api/performance?acct=${encodeURIComponent(acct)}&days=${days}`).catch(() => null);
  if (!p || !p.ok) { box.innerHTML = `<div class="muted small">Performance appears once the app has recorded your net worth twice (it records every 5 minutes while running).</div>`; return; }
  const rp = p.returnPct, mp = p.marketPct;
  box.innerHTML = `<div class="perf-head">
      <div><div class="k">Your return</div><div class="v ${signCls(rp)}">${sgnPct(rp, 2)}</div><div class="s muted">Excludes loot and other income</div></div>
      <div><div class="k">Market index</div><div class="v ${signCls(mp)}">${mp == null ? "-" : sgnPct(mp, 2)}</div><div class="s muted">Most traded items</div></div>
      <div><div class="k">Vs market</div><div class="v ${signCls(p.excess)}">${p.excess == null ? "-" : sgnPct(p.excess, 2)}</div><div class="s muted">${p.excess == null ? "" : p.excess >= 0 ? "Ahead" : "Behind"}</div></div>
      <div><div class="k">Net worth change</div><div class="v ${signCls(p.change)}">${signed(p.change, short)}</div><div class="s muted">Everything included</div></div>
    </div>
    <div id="nwPerfChart"></div>
    <h4>Where the change came from</h4>${attrBars(p.parts)}`;
  lineChart($("#nwPerfChart", host), [
    { name: "Your return", cls: "l1", color: "var(--series-1)", pts: p.series },
    { name: "Market index", cls: "l2", color: "var(--series-2)", pts: p.benchmark || [] },
  ], { base: 0, height: 200, fmt: (v) => (v > 0 ? "+" : "") + pct(v, Math.abs(v) < 0.1 ? 1 : 0), tipFmt: (v) => sgnPct(v, 2), aria: "Your return against the market index" });
}
async function loadRisk(host, acct) {
  const box = $("#nwRisk", host);
  if (!box) return;
  const r = await api(`/api/risk?acct=${encodeURIComponent(acct)}`).catch(() => null);
  if (!r || !r.ok) { box.innerHTML = `<div class="muted small">${esc((r && r.reason) || "Risk needs daily price history for what you hold.")}</div>`; return; }
  const t = (k, v, s, cls = "") => `<div class="tile"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`;
  box.innerHTML = `<div class="tiles risk-tiles">
      ${t("Daily swing", r.volDaily == null ? "-" : pct(r.volDaily, 2), "Typical day, either way")}
      ${t("Bad day", r.var1 == null ? "-" : "-" + short(r.var1), `1 day in 20 is worse (${pct(r.var1Pct, 1)})`, "neg")}
      ${t("Bad week", r.var7 == null ? "-" : "-" + short(r.var7), `1 week in 20 is worse (${pct(r.var7Pct, 1)})`, "neg")}
      ${t("Worst drawdown", pct(r.maxDrawdown, 1), "Past year, today's holdings", r.maxDrawdown < 0 ? "neg" : "")}
      ${t("Beta", r.beta == null ? "-" : r.beta.toFixed(2), "Moves per 1% of market")}
      ${t("Diversification", r.effectiveHoldings == null ? "-" : r.effectiveHoldings.toFixed(1), "Effective number of holdings")}
      ${t("Sellable in a day", pct(r.liquid1, 0), `In a week: ${pct(r.liquid7, 0)}`)}
    </div>
    <p class="small muted" style="margin:0 0 8px">From how today's holdings moved on each of the past ${r.days} days${r.coverage < 0.99 ? ` (items with history cover ${pct(r.coverage, 0)} of item value)` : ""}. Sellable assumes you get ${pct(S.fillShare || 0.2, 0)} of each item's daily instant-buy volume.</p>
    <div class="tscroll"><table>${thead([{ label: "Biggest risks" }, { label: "Weight", num: 1 }, { label: "Share of risk", num: 1, title: "How much of the account's swings come from this holding" }, { label: "Daily swing", num: 1 }, { label: "Beta", num: 1 }, { label: "Days to sell", num: 1 }], null)}<tbody>
    ${r.items.slice(0, 8).map((i) => `<tr data-id="${i.id}"><td>${itemCell(i)}</td><td class="num">${pct(i.weight, 1)}</td><td class="num"><b>${i.riskShare == null ? "-" : pct(i.riskShare, 0)}</b></td><td class="num">${i.vol == null ? "-" : pct(i.vol, 1)}</td><td class="num">${i.beta == null ? "-" : i.beta.toFixed(2)}</td><td class="num ${i.daysToSell > 3 ? "warn-txt" : ""}">${i.daysToSell == null ? "-" : i.daysToSell < 0.1 ? "<0.1" : i.daysToSell.toFixed(1)}</td></tr>`).join("")}</tbody></table></div>`;
  bindRowClicks(box);
}
function holdingsHeatmap(host, holdings) {
  const groups = {};
  holdings.filter((h) => h.how !== "cash" && h.value > 0).forEach((h) => { (groups[h.category] = groups[h.category] || []).push({ id: h.id, name: h.name, value: h.value, chg: h.chg24h, extra: `${gp(h.qty)} held` }); });
  treemap($("#nwHeat", host), Object.entries(groups).map(([name, items]) => ({ name, items })), { height: 300, range: 0.05, onPick: (d) => openTerminal(d.id), aria: "Holdings heatmap", legend: "Size: value held. Color: 24h change. Click to open in the Terminal." });
}

// Money making: recipes and item sets ---------------------------------------------
const MM = Object.assign({ patient: true, skill: "all", hideRisky: true, q: "", setDir: "all", rates: {}, smithing: "" }, store.get("mm", {}));
let MM_DATA = null;
renderers.money = async function (host) {
  host.innerHTML = `<h2>Money making</h2>
    <p class="lede">Processing and skilling methods priced live, ranked by GP per hour. Buy limits cap how many actions you can supply from the GE, which is flagged. Rates are typical for a focused player; type your own in the Per hour column. Burns, failures and travel are not counted. <b>Stale</b> or <b>Thin</b> means one leg's price is old or barely trades, so the profit may not be real.</p>
    <div class="filters">
      <label class="field wide"><span>Search</span><input class="input" id="mmQ" value="${esc(MM.q)}" placeholder="e.g. potion, bar"></label>
      <label class="field"><span>Skill</span><select class="input" id="mmSkill"></select></label>
      <label class="field" title="Barrows repair on your own armour stand costs less the higher your Smithing. Leave blank to repair at an NPC (full price)."><span>Smithing (armour stand)</span><input class="input" id="mmSmith" value="${esc(MM.smithing)}" placeholder="NPC price" inputmode="numeric"></label>
      <label class="check"><input type="checkbox" id="mmPat" ${MM.patient ? "checked" : ""}> Patient offers (buy at instant-sell, sell at instant-buy)</label>
      <label class="check"><input type="checkbox" id="mmRisky" ${MM.hideRisky ? "checked" : ""}> Hide stale and thin</label>
    </div>
    <div class="table-wrap" id="mmTable"><div class="empty">Loading...</div></div>
    <div class="section-head"><h3>Item sets</h3><div class="right"><div class="seg" id="mmDir">${[["all", "Both"], ["combine", "Combine"], ["split", "Split"]].map(([k, l]) => `<button data-k="${k}" class="${MM.setDir === k ? "on" : ""}">${l}</button>`).join("")}</div></div></div>
    <p class="lede small">Combining parts into a set, or splitting a set, is free at the GE clerk. Profit is after tax on every piece you sell. Sets have small buy limits, so profit per limit matters.</p>
    <div class="table-wrap" id="mmSets"></div>
    <details class="card" style="margin-top:16px"><summary>Your own recipe</summary>
      <p class="small muted">Anything not listed: inputs and outputs one per line as <code>qty x Item name</code>. Leave outputs empty for XP-only methods.</p>
      <div class="inline-form">
        <label class="field wide"><span>Name</span><input class="input" id="crName"></label>
        <label class="field"><span>Skill</span><input class="input" id="crSkill" placeholder="Custom"></label>
        <label class="field"><span>XP each</span><input class="input" id="crXp" inputmode="decimal"></label>
        <label class="field"><span>Per hour</span><input class="input" id="crRate" inputmode="numeric"></label>
        <label class="field"><span>Coins each</span><input class="input" id="crCoins" inputmode="numeric" placeholder="0"></label></div>
      <div class="grid2" style="margin-top:8px"><label class="field"><span>Inputs</span><textarea class="input" id="crIn" placeholder="1 x Grimy ranarr weed"></textarea></label>
        <label class="field"><span>Outputs</span><textarea class="input" id="crOut" placeholder="1 x Ranarr weed"></textarea></label></div>
      <div class="inline-form"><button class="btn primary" id="crAdd">Save recipe</button><span id="crMsg" class="small"></span></div>
      <div id="crList" style="margin-top:10px"></div>
    </details>`;
  const bind = (id, key, ev = "input") => $(id, host).addEventListener(ev, (e) => { MM[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value; store.set("mm", MM); if (key === "patient") loadMoney(host); else drawMoney(host); });
  $("#mmSmith", host).addEventListener("change", (e) => { MM.smithing = e.target.value.trim(); store.set("mm", MM); loadMoney(host); });
  bind("#mmQ", "q"); bind("#mmSkill", "skill", "change"); bind("#mmPat", "patient", "change"); bind("#mmRisky", "hideRisky", "change");
  $$("#mmDir button", host).forEach((b) => (b.onclick = () => { MM.setDir = b.dataset.k; store.set("mm", MM); $$("#mmDir button", host).forEach((x) => x.classList.toggle("on", x === b)); drawMoney(host); }));
  const parseLegs = (txt) => txt.split(/\n/).map((l) => l.trim()).filter(Boolean).map((l) => { const m = l.match(/^([\d.]+)\s*x\s*(.+)$/i) || l.match(/^(.+?)\s*x\s*([\d.]+)$/i); if (!m) return [l, 1]; return /^[\d.]+$/.test(m[1]) ? [m[2], +m[1]] : [m[1], +m[2]]; });
  $("#crAdd", host).onclick = async () => {
    try {
      await api("/api/recipes/custom", { method: "POST", body: { name: $("#crName", host).value, skill: $("#crSkill", host).value, xp: numOr($("#crXp", host).value, 0), per_hour: numOr($("#crRate", host).value, 0), coins: numOr($("#crCoins", host).value, 0), inputs: parseLegs($("#crIn", host).value), outputs: parseLegs($("#crOut", host).value) } });
      $("#crMsg", host).innerHTML = `<span class="pos">Saved.</span>`; loadMoney(host);
    } catch (e) { $("#crMsg", host).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  loadMoney(host);
};
async function loadMoney(host) {
  const smith = numOr(MM.smithing, 0);
  try { MM_DATA = await api("/api/recipes?patient=" + (MM.patient ? 1 : 0) + (smith >= 1 && smith <= 99 ? "&smithing=" + Math.round(smith) : "")); } catch (e) { $("#mmTable", host).innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const skills = [...new Set(MM_DATA.methods.map((m) => m.skill))].sort();
  const groups = [...new Set(MM_DATA.methods.map((m) => m.group).filter((g) => g && !skills.includes(g)))].sort();
  $("#mmSkill", host).innerHTML = `<option value="all">All skills</option>` + skills.map((k) => `<option ${MM.skill === k ? "selected" : ""}>${esc(k)}</option>`).join("")
    + (groups.length ? `<optgroup label="Groups">${groups.map((g) => `<option value="group:${esc(g)}" ${MM.skill === "group:" + g ? "selected" : ""}>${esc(g)}</option>`).join("")}</optgroup>` : "");
  $("#crList", host).innerHTML = MM_DATA.custom.length ? `<div class="small">${MM_DATA.custom.map((c) => `<div style="display:flex;gap:8px;align-items:center;padding:3px 0"><b>${esc(c.name)}</b><span class="muted">${esc(c.skill || "")}</span><button class="btn small danger" data-rdel="${c.rid}" style="margin-left:auto">Delete</button></div>`).join("")}</div>` : "";
  $$("[data-rdel]", host).forEach((b) => (b.onclick = async () => { if (!confirmInline(b)) return; await api("/api/recipes/custom?rid=" + b.dataset.rdel, { method: "DELETE" }); loadMoney(host); }));
  drawMoney(host);
}
const MMS = { key: "gpHrLive", dir: "desc" }, MSS = { key: "profit", dir: "desc" };
function drawMoney(host) {
  if (!MM_DATA) return;
  const q = MM.q.trim().toLowerCase();
  let rows = MM_DATA.methods.filter((m) => (MM.skill === "all" || m.skill === MM.skill || MM.skill === "group:" + m.group) && (!q || m.name.toLowerCase().includes(q) || m.inputs.concat(m.outputs).some((i) => i.name.toLowerCase().includes(q)))
    && (!MM.hideRisky || (!m.stale && !m.thin))).map((m) => {
    const rate = MM.rates[m.name] || m.perHour;
    const eff = m.limitPerHour != null ? Math.min(rate, m.limitPerHour) : rate;
    return Object.assign({}, m, { rateLive: rate, gpHrLive: eff ? m.profit * eff : null, xpHrLive: eff ? m.xp * eff : null, cappedLive: m.limitPerHour != null && rate > m.limitPerHour });
  });
  rows = sortRows(rows, MMS.key, MMS.dir);
  const legs = (arr, coins) => arr.map((i) => `${i.qty !== 1 ? gp(i.qty) + " x " : ""}${esc(i.name)}`).join(", ") + (coins ? `, ${short(coins)} gp fee` : "");
  $("#mmTable", host).innerHTML = rows.length ? `<table>${thead([{ label: "Method", sort: "name" }, { label: "Skill", sort: "skill" }, { label: "Lvl", sort: "level", num: 1 }, { label: "Uses" }, { label: "Cost", sort: "cost", num: 1 }, { label: "Profit each", sort: "profit", num: 1 }, { label: "XP", sort: "xp", num: 1 }, { label: "GP / XP", sort: "gpXp", num: 1 }, { label: "Per hour", num: 1, title: "Actions per hour; type your own" }, { label: "GP / hr", sort: "gpHrLive", num: 1 }, { label: "XP / hr", sort: "xpHrLive", num: 1 }], MMS)}<tbody>${rows.map((m) => `
    <tr data-id="${m.id}"><td>${itemCell(m, (m.cappedLive ? ` <span class="tag limit" title="Buy limits allow about ${gp(m.limitPerHour)} per hour">Limit capped</span>` : "") + (m.stale ? ` <span class="tag stale" title="A price is ${ago(m.maxAge)} old">Stale</span>` : "") + (m.thin ? ` <span class="tag thin" title="A leg trades about ${gp(m.minVol24)} a day">Thin</span>` : "") + (m.custom ? ` <span class="tag">Yours</span>` : "") + (m.note ? ` <span class="muted small">${esc(m.note)}</span>` : ""))}</td>
    <td>${esc(m.skill)}</td><td class="num">${m.level || "-"}</td><td class="small muted" style="max-width:260px">${legs(m.inputs, m.coins)}</td>
    <td class="num">${gp(m.cost)}</td><td class="num ${signCls(m.profit)}"><b>${signed(m.profit)}</b></td><td class="num">${m.xp ? gp(m.xp) : "-"}</td>
    <td class="num ${signCls(m.gpXp)}">${m.gpXp == null ? "-" : m.gpXp.toFixed(2)}</td>
    <td class="num"><input class="input rate" data-rate="${esc(m.name)}" value="${m.rateLive || ""}" inputmode="numeric"></td>
    <td class="num ${signCls(m.gpHrLive)}"><b>${m.gpHrLive == null ? "-" : signed(m.gpHrLive, short)}</b></td><td class="num">${m.xpHrLive ? short(m.xpHrLive) : "-"}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No methods match.</div>`;
  bindSort($("#mmTable", host), MMS, () => drawMoney(host));
  bindRowClicks($("#mmTable", host));
  $$("input[data-rate]", host).forEach((inp) => inp.addEventListener("change", () => {
    const v = numOr(inp.value, 0);
    if (v > 0) MM.rates[inp.dataset.rate] = v; else delete MM.rates[inp.dataset.rate];
    store.set("mm", MM); drawMoney(host);
  }));
  let sets = MM_DATA.sets.filter((r) => (MM.setDir === "all" || r.direction === MM.setDir) && (!q || r.set.toLowerCase().includes(q)) && (!MM.hideRisky || (!r.stale && !r.thin)));
  sets = sortRows(sets, MSS.key, MSS.dir).slice(0, 80);
  $("#mmSets", host).innerHTML = sets.length ? `<table>${thead([{ label: "Set", sort: "set" }, { label: "Do", sort: "direction" }, { label: "Buy for", sort: "cost", num: 1 }, { label: "Sell for (after tax)", sort: "revenue", num: 1 }, { label: "Profit", sort: "profit", num: 1 }, { label: "ROI", sort: "roi", num: 1 }, { label: "Limit", sort: "limit", num: 1 }, { label: "Profit / limit", sort: "profitPerLimit", num: 1 }, { label: "Thinnest 24h vol", sort: "minVol24", num: 1 }], MSS)}<tbody>${sets.map((r) => `
    <tr data-id="${r.setId}"><td>${itemCell({ name: r.set, icon: r.icon }, (r.stale ? ` <span class="tag stale">Stale</span>` : "") + (r.thin ? ` <span class="tag thin">Thin</span>` : ""))}</td>
    <td>${r.direction === "combine" ? `Buy ${r.parts} parts, combine` : `Buy set, split into ${r.parts}`}</td><td class="num">${gp(r.cost)}</td><td class="num">${gp(r.revenue)}</td>
    <td class="num ${signCls(r.profit)}"><b>${signed(r.profit)}</b></td><td class="num">${pct(r.roi, 2)}</td><td class="num">${gp(r.limit)}</td>
    <td class="num ${signCls(r.profitPerLimit)}">${signed(r.profitPerLimit, short)}</td><td class="num">${short(r.minVol24)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No sets match.</div>`;
  bindSort($("#mmSets", host), MSS, () => drawMoney(host));
  bindRowClicks($("#mmSets", host));
}

// Forecast -----------------------------------------------------------------------
const FC = Object.assign({ days: 30, windows: "2", share: "", minVol: "5000", q: "", sel: null }, store.get("fc", {}));
const FCS = { key: "profit", dir: "desc" };
let FC_DATA = null;
function confTag(c) {
  const t = { high: "High", medium: "Medium", low: "Low" }[c] || c;
  return `<span class="tag ${c === "low" ? "stale" : c === "high" ? "free" : ""}" title="Based on how many days of history exist and how well the model predicted recent days it had not seen">${t}</span>`;
}
function fcQuery(extra = "") {
  const share = numOr(FC.share, (S.fillShare || 0.2) * 100);
  return `days=${FC.days}&windows=${numOr(FC.windows, 2)}&share=${share}${extra}`;
}
renderers.forecast = function (host) {
  const mode = store.get("fcMode", "hold");
  host.innerHTML = `<div class="section-head" style="margin-top:0"><h2 style="margin:0">Forecast</h2>
      <div class="right"><div class="seg" id="fcModeSeg">${[["hold", "Holding outlook"], ["flip", "Flipping profit"]].map(([k, l]) => `<button data-m="${k}" class="${mode === k ? "on" : ""}">${l}</button>`).join("")}</div></div></div>
    <div id="fcModeBody"></div>`;
  $$("#fcModeSeg button", host).forEach((b) => (b.onclick = () => { store.set("fcMode", b.dataset.m); renderers.forecast(host); }));
  const body = $("#fcModeBody", host);
  if (mode === "hold") renderHold(body); else renderFlipForecast(body);
};

// Holding outlook ------------------------------------------------------------------
const HO = Object.assign({ scope: "portfolio", sel: null, qty: "" }, store.get("ho", {}));
const HOS = { key: "ret30", dir: "desc" };
let HO_DATA = null;
const VERDICT = {
  hold: { label: "Trends support holding", cls: "free", short: "Hold" },
  neutral: { label: "No reliable trend either way", cls: "", short: "Neutral" },
  sell: { label: "Trends favor selling", cls: "trap", short: "Sell" },
};
function verdictTag(v, long) { const x = VERDICT[v] || VERDICT.neutral; return `<span class="tag ${x.cls}">${long ? x.label : x.short}</span>`; }
function sgnPct(x, d = 1) { return x == null ? "-" : (x > 0 ? "+" : "") + pct(x, d); }
async function renderHold(host) {
  host.innerHTML = `<p class="lede">If you hold an item, do its trends support keeping it, and what is it likely to be worth later? The model learned from two years of the whole market: for thousands of past moments it recorded each item's trend signals (momentum, distance from its averages, where it sits in its 6 month range, volatility, volume, the market's direction) and what the price did next. It was tested on months it never saw. Values are after the 2% tax on selling.</p>
    <details class="card" id="hoAcc" style="margin-bottom:14px" ${store.get("hoAccOpen", false) ? "open" : ""}><summary>How reliable is this? <span class="muted small" id="hoAccHint"></span></summary><div id="hoAccBody" style="margin-top:10px"></div></details>
    <div class="filters">${pickerField("Look up an item", "hoItem")}
      <label class="field" title="How many you hold (defaults to your portfolio)"><span>Quantity</span><input class="input" id="hoQty" value="${esc(HO.qty)}" placeholder="1"></label></div>
    <div id="hoDetail"></div>
    <div class="section-head"><h3>Outlook for</h3><div class="right" style="margin-left:0"><div class="seg" id="hoScope">${[["portfolio", "My portfolio"], ["watch", "Watchlist"], ["market", "Most traded 600"]].map(([k, l]) => `<button data-k="${k}" class="${HO.scope === k ? "on" : ""}">${l}</button>`).join("")}</div></div></div>
    <div id="hoInfo" class="muted small" style="margin-bottom:6px"></div>
    <div class="table-wrap" id="hoTable"><div class="empty">Loading...</div></div>`;
  makePicker($("#hoItem", host), $("#hoItemList", host), (r) => { $("#hoItem", host).value = ""; HO.sel = r.id; store.set("ho", HO); loadHoldDetail(host, r.id); });
  $("#hoQty", host).onchange = (e) => { HO.qty = e.target.value.trim(); store.set("ho", HO); if (HO.sel) loadHoldDetail(host, HO.sel); };
  $$("#hoScope button", host).forEach((b) => (b.onclick = () => { HO.scope = b.dataset.k; store.set("ho", HO); $$("#hoScope button", host).forEach((x) => x.classList.toggle("on", x === b)); loadHoldScan(host); }));
  $("#hoAcc", host).addEventListener("toggle", (e) => store.set("hoAccOpen", e.target.open));
  loadHoldAccuracy(host);
  loadHoldScan(host);
  if (HO.sel) loadHoldDetail(host, HO.sel);
}
async function loadHoldScan(host) {
  const box = $("#hoTable", host);
  box.innerHTML = `<div class="empty">Loading...</div>`;
  try { HO_DATA = await api("/api/hold/scan?scope=" + HO.scope); } catch (e) { box.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  if (HO_DATA.ok === false) { box.innerHTML = `<div class="notice">${esc(HO_DATA.reason)}</div>`; return; }
  drawHoldScan(host);
}
function drawHoldScan(host) {
  const d = HO_DATA, box = $("#hoTable", host);
  const ok = d.items.filter((r) => r.ok), missing = d.items.length - ok.length;
  $("#hoInfo", host).innerHTML = `The market as a whole moved ${sgnPct(d.market30)} over the last 30 days.` + (missing ? ` ${missing} item(s) need more history.` : "");
  if (!d.items.length) { box.innerHTML = `<div class="empty">${HO.scope === "portfolio" ? "No holdings yet. Add some on the Portfolio tab, or look up any item above." : HO.scope === "watch" ? "Your watchlist is empty." : "No history imported yet."}</div>`; return; }
  const rows = sortRows(ok, HOS.key, HOS.dir);
  box.innerHTML = rows.length ? `<table>${thead([{ label: "" }, { label: "Item", sort: "name" }, { label: "Outlook", sort: "ret30" }, { label: "Price", sort: "price", num: 1 }, { label: "Last 30d", sort: "chg30", num: 1 }, { label: "Next 7d", sort: "ret7", num: 1 }, { label: "Next 30d", sort: "ret30", num: 1, title: "Expected change (middle outcome)" }, { label: "30d likely range", num: 1 }, { label: "Chance up (30d)", sort: "pUp30", num: 1 }, { label: "Next 90d", sort: "ret90", num: 1 }, { label: "Typical monthly move", sort: "monthlyMove", num: 1 }, { label: "Own record: higher after 90d", sort: "own90up", num: 1, title: "Share of this item's past 90 day periods (last two years) that ended higher, and the median change. History, not a prediction." }], HOS)}<tbody>${rows.map((r) => `
    <tr data-ho="${r.id}"><td>${star(r.id)}</td><td>${itemCell(r)}</td><td>${verdictTag(r.verdict)}</td><td class="num">${gp(r.price)}</td>
    <td class="num ${signCls(r.chg30)}">${sgnPct(r.chg30)}</td><td class="num ${signCls(r.ret7)}">${sgnPct(r.ret7)}</td>
    <td class="num ${signCls(r.ret30)}"><b>${sgnPct(r.ret30)}</b></td><td class="num small muted">${sgnPct(r.lo30, 0)} to ${sgnPct(r.hi30, 0)}</td>
    <td class="num">${pct(r.pUp30, 0)}</td><td class="num ${signCls(r.ret90)}">${sgnPct(r.ret90)}</td><td class="num muted">±${pct(r.monthlyMove, 1)}</td>
    <td class="num">${r.own90up == null ? "-" : `${pct(r.own90up, 0)} <span class="muted small">(${sgnPct(r.own90med)})</span>`}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">None of these items have enough history yet.</div>`;
  bindSort(box, HOS, () => drawHoldScan(host));
  $$("tr[data-ho]", box).forEach((tr) => tr.addEventListener("click", (e) => { if (e.target.closest("button")) return; HO.sel = +tr.dataset.ho; store.set("ho", HO); loadHoldDetail(host, HO.sel); window.scrollTo({ top: 0, behavior: "smooth" }); }));
  $$(".star", box).forEach((b) => b.addEventListener("click", async (e) => { e.stopPropagation(); await toggleWatch(+b.dataset.id); }));
}
async function loadHoldDetail(host, id) {
  const box = $("#hoDetail", host);
  box.innerHTML = `<div class="card" style="margin-bottom:14px"><div class="empty">Loading...</div></div>`;
  let qty = numOr(HO.qty, 0);
  if (!qty) {
    try { const p = await api("/api/portfolio"); qty = p.holdings.filter((h) => h.item_id === id).reduce((a, h) => a + h.qty, 0) || 1; } catch (e) { qty = 1; }
  }
  let o;
  try { o = await api(`/api/hold?id=${id}&qty=${Math.round(qty)}`); } catch (e) { box.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const r = S.byId.get(id) || { name: "Item " + id };
  if (!o.ok) { box.innerHTML = `<div class="notice">${esc(r.name)}: ${esc(o.reason)}</div>`; return; }
  const f = o.facts, v = VERDICT[o.verdict] || VERDICT.neutral;
  const now = Math.floor(Date.now() / 1000);
  box.innerHTML = `<div class="card" style="margin-bottom:14px">
    <div class="section-head" style="margin-top:0">${itemCell(r)}<div class="right"><button class="btn small" id="hoOpen">Item details</button><button class="btn small ghost" id="hoClose">Close</button></div></div>
    <div class="callout" style="font-size:14px">${verdictTag(o.verdict, true)} ${o.signal && !o.signal["30"]
      ? `Tested on two years of market history, trend signals did not reliably predict where prices went next, so no direction is assumed. What history does show: prices like this one were more often lower than higher after a month (${pct((o.points.find((p) => p.h === 30) || {}).pUp, 0)} chance it rises in 30 days), and you pay 2% tax whenever you sell.`
      : o.reasons.length ? `Mainly because it is ${o.reasons.map((x) => `<b>${esc(x.signal)}</b>`).join(", ")}.` : ""}</div>
    <div class="tiles">
      <div class="tile"><div class="k">Worth now</div><div class="v">${short(o.sellNow)}</div><div class="s">${gp(o.qty)} x ${gp(o.priceNow)}, after tax</div></div>
      ${o.points.map((p) => `<div class="tile"><div class="k">In ${p.h} days</div><div class="v ${signCls(p.ret)}">${short(p.value)}</div><div class="s">${sgnPct(p.ret)} expected · ${short(p.valueLo)} to ${short(p.valueHi)} likely · ${pct(p.pUp, 0)} chance it rises</div></div>`).join("")}
    </div>
    <div class="grid2">
      <div class="chart-card"><div class="chart-head"><span class="title">Price and outlook</span><span class="muted small">Last year, then the expected path with its likely range</span></div><div id="hoChart"></div></div>
      <div class="card"><h3 style="margin-top:0">Trend facts</h3><div class="kv">
        <span class="k">Change 7 / 30 / 90 days</span><span>${sgnPct(f.chg7)} / ${sgnPct(f.chg30)} / ${sgnPct(f.chg90)}</span>
        <span class="k">Change 6 months</span><span>${sgnPct(f.chg180)}</span>
        <span class="k">vs 30 day average</span><span>${sgnPct(f.vsMa30)}</span>
        <span class="k">vs 90 day average</span><span>${sgnPct(f.vsMa90)}</span>
        <span class="k">6 month range</span><span>${short(f.low180)} to ${short(f.high180)} (now ${pct(f.range180, 0)} of the way up)</span>
        <span class="k">Typical monthly move</span><span>±${pct(f.monthlyMove, 1)}</span>
        <span class="k">Volume, last 7 vs 60 days</span><span>${sgnPct(f.volumeTrend, 0)}</span>
        <span class="k">Whole market, 30 days</span><span>${sgnPct(f.market30)}</span>
        ${f.own30 ? `<span class="k">This item's own record</span><span>higher after 30 days in ${pct(f.own30.up, 0)} of past periods (median ${sgnPct(f.own30.median)}); after 90 days in ${f.own90 ? pct(f.own90.up, 0) : "-"} (median ${f.own90 ? sgnPct(f.own90.median) : "-"})</span>` : ""}
      </div></div>
    </div>
    <p class="small muted" style="margin:6px 0 0">Values are the middle of realistic outcomes; about 8 in 10 real outcomes landed inside the likely range. An item's own record describes its past, it is not a prediction (these patterns have flipped before). Updates, new content and bot bans can move prices in ways no trend predicts.</p></div>`;
  const pts = [{ t: now, v: o.priceNow }].concat(o.points.map((p) => ({ t: now + p.h * 86400, v: p.price })));
  const band = [{ t: now, lo: o.priceNow, hi: o.priceNow }].concat(o.points.map((p) => ({ t: now + p.h * 86400, lo: p.priceLo, hi: p.priceHi })));
  lineChart($("#hoChart", box), [
    { name: "Price", cls: "l1", color: "var(--series-1)", pts: o.history.map((h) => ({ t: h.t, v: h.v })) },
    { name: "Outlook", cls: "l2", color: "var(--series-2)", dash: true, pts, band },
  ], { height: 230, noEndLabels: true, aria: "Price history and holding outlook", tipFmt: (x) => gp(x) });
  $("#hoOpen", box).onclick = () => openItem(id);
  $("#hoClose", box).onclick = () => { HO.sel = null; store.set("ho", HO); box.innerHTML = ""; };
}
let HO_POLL = null;
async function loadHoldAccuracy(host) {
  let d;
  try { d = await api("/api/hold/model"); } catch (e) { return; }
  const body = $("#hoAccBody", host), hint = $("#hoAccHint", host);
  if (!body) return;
  const r = d.report, running = d.status.startsWith("running");
  hint.textContent = running ? "Retraining..." : r ? `tested on ${gp(r.test["30"] ? r.test["30"].cases : 0)} past moments` : "";
  let html = "";
  if (r) {
    const day = (t) => new Date(t * 1000).toLocaleDateString([], { month: "short", year: "numeric" });
    html += `<p class="small" style="margin:0 0 8px">${d.source === "bundled" ? "The shipped model" : "Your retrained model"} learned from ${gp(r.items)} of the most traded items, ${esc(day(r.from))} to ${esc(day(r.to))}. It was fitted on data before ${esc(day(r.split))} and tested on the months after, which it never saw:</p>
      <div class="table-wrap"><table>${thead([{ label: "Held out test" }, ...["7", "30", "90"].filter((h) => r.test[h]).map((h) => ({ label: h + " days", num: 1 }))], null)}<tbody>
      ${[["Better than assuming no change (above 0 is better)", (t) => (t.skill == null ? "-" : t.skill.toFixed(3))],
         ["Direction right when it expected a move", (t) => pct(t.direction, 0)],
         ["Items it rated best (top fifth): real change", (t) => sgnPct(t.topFifth)],
         ["Items it rated worst (bottom fifth): real change", (t) => sgnPct(t.bottomFifth)],
         ["Said \"hold\": rose afterwards", (t) => (t.verdicts.hold.n ? `${pct(t.verdicts.hold.rose, 0)} of ${gp(t.verdicts.hold.n)}, avg ${sgnPct(t.verdicts.hold.avg)}` : "never said it")],
         ["Said \"sell\": rose afterwards", (t) => (t.verdicts.sell.n ? `${pct(t.verdicts.sell.rose, 0)} of ${gp(t.verdicts.sell.n)}, avg ${sgnPct(t.verdicts.sell.avg)}` : "never said it")],
         ["Real outcome inside the likely range (aim 80%)", (t) => pct(t.coverage, 0)]].map(([label, fn]) => `<tr class="static"><td>${label}</td>${["7", "30", "90"].filter((h) => r.test[h]).map((h) => `<td class="num">${fn(r.test[h])}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
      <p class="small" style="margin:8px 0 0">${Object.values(r.signalUsed || {}).some(Boolean) ? `Trend signals are used for: ${Object.entries(r.signalUsed).filter(([, v]) => v).map(([h]) => h + " days").join(", ")}.` : `<b>No trend signal beat "no change" on the test months, so none is used.</b> In 2025 items that had risen tended to fall back; in 2026 they kept rising. Because the pattern flips, betting on either would have been confidently wrong for months. The outlook therefore centers on today's value, with odds and ranges from how prices really moved.`}</p>
      ${r.baseRates ? `<p class="small muted" style="margin:6px 0 0">Across the most traded items over two years, prices were higher 30 days later ${pct(r.baseRates.all["30"].up, 0)} of the time (median ${sgnPct(r.baseRates.all["30"].median)}) and 90 days later ${pct(r.baseRates.all["90"].up, 0)} of the time (median ${sgnPct(r.baseRates.all["90"].median)}). In the last six months: ${pct(r.baseRates.recent["90"].up, 0)} higher after 90 days, median ${sgnPct(r.baseRates.recent["90"].median)}.</p>` : ""}`;
  }
  html += `<div class="inline-form"><button class="btn" id="hoTrain" ${running || d.dailyDays < 270 ? "disabled" : ""}>${running ? esc(d.status.replace("running: ", "")) + "..." : "Retrain on my history"}</button><span class="small muted">${d.dailyDays < 270 ? `Needs about 270 days of imported history (you have ${d.dailyDays}).` : "Refits on your imported history, including the newest days. Takes about a minute."}</span></div>
    ${d.status.startsWith("failed") ? `<div class="small err">${esc(d.status)}</div>` : ""}`;
  body.innerHTML = html;
  const b = $("#hoTrain", host);
  if (b) b.onclick = async () => { await api("/api/hold/train", { method: "POST" }); loadHoldAccuracy(host); };
  clearTimeout(HO_POLL);
  if (running) HO_POLL = setTimeout(() => { if (S.tab === "forecast") loadHoldAccuracy(host); }, 4000);
  else if (hint.dataset.was === "running") { loadHoldScan(host); if (HO.sel) loadHoldDetail(host, HO.sel); }
  hint.dataset.was = running ? "running" : "";
}

async function renderFlipForecast(host) {
  host.innerHTML = `
    <p class="lede">Estimates what flipping each item would earn over the coming days, from its demand (instant-buy and instant-sell volume), today's margin fading toward its usual margin, and a price trend fitted to your saved history. The range comes from replaying the item's own past good and bad days. Long horizons carry more risk, so read the range, not just the middle.</p>
    <div class="filters">
      <div class="field"><span>Horizon</span><div class="seg" id="fcDays" style="margin-left:0">${[7, 30, 90].map((d) => `<button data-d="${d}" class="${FC.days === d ? "on" : ""}">${d} days</button>`).join("")}</div></div>
      <label class="field" title="How many 4 hour buy limit windows you use per day"><span>Limit windows / day</span><input class="input" id="fcWin" value="${esc(FC.windows)}"></label>
      <label class="field" title="Share of the volume you expect to win; defaults to your Settings value"><span>Fill share %</span><input class="input" id="fcShare" value="${esc(FC.share)}" placeholder="${Math.round((S.fillShare || 0.2) * 100)}"></label>
      <label class="field"><span>Min 24h volume</span><input class="input" id="fcVol" value="${esc(FC.minVol)}"></label>
      <label class="field wide"><span>Filter by name</span><input class="input" id="fcQ" value="${esc(FC.q)}"></label>
    </div>
    <details class="card" id="fcAcc" style="margin-bottom:14px" ${store.get("fcAccOpen", false) ? "open" : ""}><summary>Model accuracy <span class="muted small" id="fcAccHint"></span></summary><div id="fcAccBody" style="margin-top:10px"></div></details>
    <div id="fcDetail"></div>
    <div id="fcInfo" class="muted small" style="margin-bottom:6px"></div>
    <div class="table-wrap" id="fcTable"><div class="empty">Building forecasts...</div></div>`;
  $$("#fcDays button", host).forEach((b) => (b.onclick = () => { FC.days = +b.dataset.d; store.set("fc", FC); renderFlipForecast(host); }));
  const reload = () => { store.set("fc", FC); loadForecastRank(host); if (FC.sel) loadForecastDetail(host, FC.sel); };
  $("#fcWin", host).onchange = (e) => { FC.windows = e.target.value; reload(); };
  $("#fcShare", host).onchange = (e) => { FC.share = e.target.value; reload(); };
  $("#fcVol", host).onchange = (e) => { FC.minVol = e.target.value; store.set("fc", FC); loadForecastRank(host); };
  $("#fcQ", host).oninput = (e) => { FC.q = e.target.value; store.set("fc", FC); drawForecastRank(host); };
  $("#fcAcc", host).addEventListener("toggle", (e) => store.set("fcAccOpen", e.target.open));
  loadForecastRank(host);
  loadAccuracy(host);
  if (FC.sel) loadForecastDetail(host, FC.sel);
};
let FC_POLL = null;
async function loadAccuracy(host) {
  let d;
  try { d = await api("/api/forecast/report"); } catch (e) { return; }
  const body = $("#fcAccBody", host), hint = $("#fcAccHint", host);
  if (!body) return;
  const r = d.report, running = d.status.tune.startsWith("running");
  const importing = d.status.import !== "done" && d.status.import !== "idle";
  hint.textContent = running ? "Tuning..." : r ? `tested on ${gp(r.testCases)} past forecasts` : "not tested yet";
  const m = (x) => (x == null ? "-" : pct(x, 0));
  const row = (label, a, b, fmt, better) => `<tr class="static"><td>${label}</td><td class="num">${fmt(a)}</td><td class="num"><b>${fmt(b)}</b></td><td class="num">${a == null || b == null ? "" : (better(b, a) ? `<span class="pos">better</span>` : `<span class="muted">same or worse</span>`)}</td></tr>`;
  let html = "";
  if (r) {
    const bt = r.before.test, at = r.after.test, cb = r.coverage.before, ca = r.coverage.after;
    html += `<p class="small" style="margin:0 0 8px">${r.bundled ? "The shipped settings were tuned this way. " : ""}Backtested on <b>${r.items}</b> of the most traded items over <b>${r.days}</b> days of real market history. Forecasts were made from past dates using only earlier data, then compared with what following them really made over the next ${r.horizon} days. Settings were tuned on the older ${gp(r.trainCases)} forecasts and checked on the newer ${gp(r.testCases)} they never saw (below). ${r.bundled ? "Run it on your own imported history with the button below." : `Last run ${esc(fmtTime(r.ranAt, true))}.`}</p>
      ${r.adopted === false ? `<div class="callout">The last re-tune found settings that only fit older dates better, not the held out ones, so your current settings were kept. That is the safeguard working, not an error.</div>` : ""}
      <div class="table-wrap"><table>${thead([{ label: `Held out test, ${r.horizon} day forecasts` }, { label: "Before tuning", num: 1 }, { label: "After tuning", num: 1 }, { label: "" }], null)}<tbody>
      ${row("Top 10 picks: share of the best possible profit they really made", bt.top10, at.top10, m, (b, a) => b > a)}
      ${row("Top 10 picks: forecast vs what they really made (error)", bt.top10Error, at.top10Error, m, (b, a) => b < a)}
      ${row("Forecast trades that really made money", bt.profitableShare, at.profitableShare, m, (b, a) => b > a)}
      ${row("All forecasts: total forecast vs real", bt.bias, at.bias, (x) => (x == null ? "-" : (x > 0 ? "+" : "") + pct(x, 0)), (b, a) => Math.abs(b) < Math.abs(a))}
      ${row("Profit error per item (lower is better)", bt.nwape, at.nwape, m, (b, a) => b < a)}
      ${row("Real profit inside the likely range (aim 80%)", cb.profit, ca.profit, m, (b, a) => Math.abs(b - 0.8) < Math.abs(a - 0.8))}
      ${row("Real price inside the likely range (aim 80%)", cb.price, ca.price, m, (b, a) => Math.abs(b - 0.8) < Math.abs(a - 0.8))}
      </tbody></table></div>
      <p class="small muted" style="margin:8px 0 0">Price in ${r.horizon} days: off by ${pct(r.price.test.model, 1)} on average, vs ${pct(r.price.test.naive, 1)} assuming no change. Assuming today's margin simply lasts would have been off by ${m(r.naive.test.wape)}.
      ${Object.entries(r.otherHorizons || {}).map(([h, o]) => ` ${h} day forecasts: top picks made ${m(o.before.top10)} of the best possible before, ${m(o.after.top10)} after.`).join("")}</p>
      <p class="small muted" style="margin:6px 0 0">Tuned settings: margin smoothing ${r.params.roi_alpha}, volume smoothing ${r.params.vol_alpha}, ${r.params.window} day window, live margin half-life ${r.params.half_life} days, margin scale ${r.params.shrink}, price trend ${esc(r.params.trend)}${r.params.trend !== "off" ? ` (damping ${r.params.phi})` : ""}, minimum edge ${pct(r.params.min_edge, 2)}, range width ${r.params.band} / level ${r.params.level ?? 0} / price ${r.params.price_band ?? 1}.</p>`;
  } else {
    html += `<p class="small" style="margin:0 0 8px">The model ships with settings tuned on a year of real market history. You can re-run the backtest on your own imported history at any time.</p>`;
  }
  html += `<div class="inline-form"><button class="btn primary" id="fcTune" ${running || d.dailyDays < 180 ? "disabled" : ""}>${running ? esc(d.status.tune.replace("running: ", "")) + "..." : "Backtest and re-tune"}</button>
    <span class="small muted">${d.dailyDays < 180 ? `Needs 180+ days of imported history (you have ${d.dailyDays}${importing ? `, importing ${esc(d.status.import)}` : ""}). ` : `${gp(d.dailyDays)} days of history imported. `}Takes a few minutes; the app keeps working meanwhile.</span></div>
    ${d.status.tune.startsWith("failed") ? `<div class="small err">${esc(d.status.tune)}</div>` : ""}`;
  body.innerHTML = html;
  const b = $("#fcTune", host);
  if (b) b.onclick = async () => { await api("/api/forecast/tune", { method: "POST", body: { horizon: 30 } }); loadAccuracy(host); };
  clearTimeout(FC_POLL);
  if (running) FC_POLL = setTimeout(() => { if (S.tab === "forecast") { loadAccuracy(host); } }, 4000);
  else if (hint.dataset.was === "running") loadForecastRank(host);
  hint.dataset.was = running ? "running" : "";
}
async function loadForecastRank(host) {
  try { FC_DATA = await api(`/api/forecast/rank?${fcQuery(`&minVol=${numOr(FC.minVol, 0)}`)}`); }
  catch (e) { $("#fcTable", host).innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  drawForecastRank(host);
}
function drawForecastRank(host) {
  if (!FC_DATA) return;
  const q = FC.q.trim().toLowerCase();
  let rows = FC_DATA.items.filter((r) => !q || r.name.toLowerCase().includes(q));
  rows = sortRows(rows, FCS.key, FCS.dir).slice(0, 150);
  $("#fcInfo", host).innerHTML = FC_DATA.maxDays < 7 ? `Only ${FC_DATA.maxDays} days of hourly history saved so far, so confidence is low. Raise <b>History backfill</b> in Settings (up to 30 days) for better forecasts.` : `${FC_DATA.items.length} items forecast over ${FC_DATA.horizon} days. Click a row for the full forecast.`;
  $("#fcTable", host).innerHTML = rows.length ? `<table>${thead([{ label: "" }, { label: "Item", sort: "name" }, { label: "Confidence", sort: "days" }, { label: "Margin now", sort: "roiNow", num: 1, title: "Live ROI after tax" }, { label: "Usual margin", sort: "roiHist", num: 1, title: "Recent daily average ROI after tax" }, { label: "Qty / day", sort: "dailyQty", num: 1 }, { label: "Capital", sort: "capital", num: 1, title: "Cash tied up by one day's buying" }, { label: "Per day", sort: "perDay", num: 1 }, { label: `Next ${FC_DATA.horizon} days`, sort: "profit", num: 1, title: "Expected profit; the range covers 8 in 10 outcomes" }, { label: "Likely range", num: 1 }, { label: "Price trend", sort: "priceChange", num: 1 }], FCS)}<tbody>${rows.map((r) => `
    <tr data-fc="${r.id}"><td>${star(r.id)}</td><td>${itemCell(r)}</td><td>${confTag(r.confidence)} <span class="muted small">${r.days}d</span></td>
    <td class="num ${signCls(r.roiNow)}">${pct(r.roiNow, 2)}</td><td class="num ${signCls(r.roiHist)}">${pct(r.roiHist, 2)}</td>
    <td class="num">${short(r.dailyQty)}</td><td class="num">${short(r.capital)}</td><td class="num">${short(r.perDay)}</td>
    <td class="num pos"><b>${short(r.profit)}</b></td><td class="num small muted">${short(r.low)} to ${short(r.high)}</td>
    <td class="num ${signCls(r.priceChange)}">${r.trend ? (r.priceChange > 0 ? "+" : "") + pct(r.priceChange, 1) : `<span class="muted" title="A trend did not beat 'no change' on recent days, so none is assumed">flat</span>`}</td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">No items to forecast yet. Forecasts need at least 3 days of saved hourly history.</div>`;
  bindSort($("#fcTable", host), FCS, () => drawForecastRank(host));
  $$("tr[data-fc]", host).forEach((tr) => tr.addEventListener("click", (e) => { if (e.target.closest("button")) return; FC.sel = +tr.dataset.fc; store.set("fc", FC); loadForecastDetail(host, FC.sel); window.scrollTo({ top: 0, behavior: "smooth" }); }));
  $$("#fcTable .star", host).forEach((b) => b.addEventListener("click", async (e) => { e.stopPropagation(); await toggleWatch(+b.dataset.id); }));
}
async function loadForecastDetail(host, id) {
  const box = $("#fcDetail", host);
  box.innerHTML = `<div class="card" style="margin-bottom:14px"><div class="empty">Simulating...</div></div>`;
  let f;
  try { f = await api(`/api/forecast?id=${id}&${fcQuery()}`); } catch (e) { box.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const r = S.byId.get(id) || { name: "Item " + id };
  if (!f.ok) { box.innerHTML = `<div class="notice">${esc(r.name)}: ${esc(f.reason)}</div>`; return; }
  const s = f.summary, h = f.hold, chk = s.check;
  const modelNote = chk ? `On the last ${chk.days} days it had not seen, the trend model was off by ${pct(chk.model, 1)} on average vs ${pct(chk.naive, 1)} for assuming no change, so ${s.useTrend ? "the trend is used" : "no trend is assumed"}.` : "Too little history to test the price model yet, so no trend is assumed.";
  box.innerHTML = `<div class="card" style="margin-bottom:14px">
    <div class="section-head" style="margin-top:0">${itemCell(r)} ${confTag(f.confidence)}<span class="muted small">${f.daysOfHistory} days of history</span>
      <div class="right"><button class="btn small" id="fcOpen">Item details</button><button class="btn small ghost" id="fcClose">Close</button></div></div>
    <div class="tiles">
      <div class="tile"><div class="k">Expected, ${f.horizon} days</div><div class="v ${signCls(s.p50)}">${signed(s.p50, short)}</div><div class="s">About ${short(s.p50 / f.horizon)} a day (middle outcome)</div></div>
      <div class="tile"><div class="k">Likely range</div><div class="v" style="font-size:15px">${short(s.p10)} to ${short(s.p90)}</div><div class="s">8 in 10 simulated outcomes</div></div>
      <div class="tile"><div class="k">Chance of a loss</div><div class="v ${s.lossChance > 0.2 ? "neg" : ""}">${pct(s.lossChance, 0)}</div><div class="s">Over the whole horizon</div></div>
      <div class="tile"><div class="k">Margin</div><div class="v" style="font-size:15px">${pct(s.roiNow, 2)} now</div><div class="s">${pct(s.roiHist, 2)} usual · held ${s.held == null ? "-" : pct(s.held, 0)} of hours</div></div>
      <div class="tile"><div class="k">Demand</div><div class="v">${short(s.dailyQty)}<span class="small muted"> / day</span></div><div class="s">${s.limitCapped ? "Capped by the buy limit" : `Your share of ${short(s.demandPerDay)} traded`}</div></div>
      <div class="tile"><div class="k">Price in ${f.horizon} days</div><div class="v ${signCls(s.priceChange)}">${s.useTrend ? (s.priceChange > 0 ? "+" : "") + pct(s.priceChange, 1) : "Flat"}</div><div class="s">${short(s.priceP10)} to ${short(s.priceP90)} likely</div></div>
    </div>
    <div class="grid2">
      <div class="chart-card"><div class="chart-head"><span class="title">Price</span><span class="muted small">Daily average, forecast with likely range</span></div><div id="fcPrice"></div></div>
      <div class="chart-card"><div class="chart-head"><span class="title">Cumulative flip profit</span><span class="muted small">Middle outcome with likely range</span></div><div id="fcProfit"></div></div>
    </div>
    ${h ? `<div class="callout"><b>Holding instead:</b> buying one limit (${gp(h.qty)} for ${short(h.cost)}) now and selling in ${f.horizon} days would likely return <b class="${signCls(h.p50)}">${signed(h.p50, short)}</b> (${short(h.p10)} to ${short(h.p90)}), after tax.</div>` : ""}
    <p class="small muted" style="margin:6px 0 0">${esc(modelNote)} Assumes ${pct(f.assumptions.share, 0)} of volume and ${f.assumptions.windows} limit window(s) a day, and that you skip days after a losing margin. Game updates and bot bans can move prices in ways no history predicts.</p>
  </div>`;
  const now = Math.floor(Date.now() / 1000);
  lineChart($("#fcPrice", box), [
    { name: "History", cls: "l1", color: "var(--series-1)", pts: f.history.map((d) => ({ t: d.t, v: d.price })) },
    { name: "Forecast", cls: "l2", color: "var(--series-2)", dash: true, pts: [{ t: now, v: s.priceNow }].concat(f.bands.map((b) => ({ t: b.t, v: b.p50 }))), band: [{ t: now, lo: s.priceNow, hi: s.priceNow }].concat(f.bands.map((b) => ({ t: b.t, lo: b.p10, hi: b.p90 }))) },
  ], { height: 220, noEndLabels: true, aria: "Price history and forecast", tipFmt: (v) => gp(v) });
  lineChart($("#fcProfit", box), [
    { name: "Profit", cls: "l1", color: "var(--series-1)", pts: [{ t: now, v: 0 }].concat(f.bands.map((b) => ({ t: b.t, v: b.c50 }))), band: [{ t: now, lo: 0, hi: 0 }].concat(f.bands.map((b) => ({ t: b.t, lo: b.c10, hi: b.c90 }))) },
  ], { height: 220, zero: true, aria: "Cumulative forecast profit", tipFmt: (v) => signed(v, short) + " gp" });
  $("#fcOpen", box).onclick = () => openItem(id);
  $("#fcClose", box).onclick = () => { FC.sel = null; store.set("fc", FC); box.innerHTML = ""; };
}

// Backtest -----------------------------------------------------------------------
const BT = Object.assign({ strategy: "dip", days: "30", threshold: "5", hold: "6", lookback: "24", volMult: "3", minPrice: "1000", maxPrice: "", minVol: "5000", members: "all", watchOnly: false, share: "20" }, store.get("bt", {}));
renderers.backtest = async function (host) {
  let meta = { strategies: {}, hoursOfData: 0 };
  try { meta = await api("/api/backtest/strategies"); } catch (e) { /* offline */ }
  const f = (id, label, key, ph = "") => `<label class="field"><span>${label}</span><input class="input" id="${id}" value="${esc(BT[key])}" placeholder="${ph}"></label>`;
  host.innerHTML = `<h2>Backtest</h2>
    <p class="lede">Replay a simple trading rule over your saved hourly history to see whether it would have made money, before you trust it. Buys fill at the next hour's average instant-sell price and sells at the average instant-buy price after the hold, minus tax. One open trade per item at a time. You have <b>${gp(meta.hoursOfData)} hours</b> of history saved${meta.hoursOfData < 72 ? `; raise <b>History backfill</b> in Settings for a longer test` : ""}.</p>
    <div class="card" style="margin-bottom:14px">
      <div class="inline-form" style="margin-top:0">
        <label class="field wide" style="max-width:none"><span>Rule</span><select class="input" id="btS">${Object.entries(meta.strategies).map(([k, l]) => `<option value="${k}" ${BT.strategy === k ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label></div>
      <div class="inline-form">
        ${f("btDays", "Days of history", "days")}${f("btTh", "Threshold %", "threshold")}${f("btHold", "Hold (hours)", "hold")}${f("btLook", "Lookback (hours)", "lookback")}${f("btVm", "Volume x normal", "volMult")}
        ${f("btMinP", "Min price", "minPrice", "Any")}${f("btMaxP", "Max price", "maxPrice", "Any")}${f("btMinV", "Min 24h volume", "minVol", "Any")}${f("btShare", "Fill share %", "share")}
        <label class="field"><span>Members</span><select class="input" id="btMem"><option value="all">All</option><option value="f2p">F2P only</option><option value="p2p">Members only</option></select></label>
        <label class="check"><input type="checkbox" id="btWatch" ${BT.watchOnly ? "checked" : ""}> Watchlist only</label>
        <button class="btn primary" id="btRun">Run backtest</button></div>
      <p class="small muted" style="margin:8px 0 0">Threshold means: how far below the trailing average (dip), the 1 hour drop (dump), the minimum ROI after tax (margin), or how far above the trailing high (breakout). Volume x normal applies to dump and breakout.</p>
    </div>
    <div id="btOut"></div>
    <div class="card" id="btInd" style="margin-top:16px"><h3 style="margin-top:0">Do chart indicators work in OSRS?</h3><div class="small muted">Testing every indicator on the daily history...</div></div>`;
  $("#btMem", host).value = BT.members;
  loadIndicatorReport(host);
  $("#btRun", host).onclick = () => runBacktest(host);
};
async function loadIndicatorReport(host) {
  const box = $("#btInd", host);
  if (!box) return;
  const r = await api("/api/indicators/report").catch(() => null);
  if (!box.isConnected) return;
  if (r && r.running) { setTimeout(() => loadIndicatorReport(host), 3000); return; }
  if (BT_SHOW_IND) { BT_SHOW_IND = false; box.scrollIntoView({ behavior: "smooth" }); }
  if (!r || !r.ok) { box.innerHTML = `<h3 style="margin-top:0">Do chart indicators work in OSRS?</h3><div class="muted small">${esc((r && r.reason) || "Not available.")}</div>`; return; }
  const cell = (h) => !h || !h.n ? `<td class="num muted">-</td><td class="muted small">too few</td>` : `<td class="num ${signCls(h.excess)}" title="Older half ${h.early == null ? "-" : sgnPct(h.early, 2)} · newer half ${h.late == null ? "-" : sgnPct(h.late, 2)} · t ${h.t == null ? "-" : h.t.toFixed(1)} · ${gp(h.n)} signals">${h.excess == null ? "-" : sgnPct(h.excess, 2)}</td><td class="small ${VERDICT_CLS[h.verdict] || ""}">${esc(h.verdict)}</td>`;
  box.innerHTML = `<h3 style="margin-top:0">Do chart indicators work in OSRS?</h3>
    <p class="small muted" style="margin-top:0">Every signal replayed on ${gp(r.items)} of the most traded items' daily history (${esc(new Date(r.from * 1000).toLocaleDateString([], { month: "short", year: "numeric" }))} to ${esc(new Date(r.to * 1000).toLocaleDateString([], { month: "short", year: "numeric" }))}). The number is the average move after the signal, after tax, beyond the market and beyond buying on a random day. For "avoid" signals a negative number means the signal worked. It only counts if it worked in both the older and newer half of the history.</p>
    <div class="tscroll"><table>${thead([{ label: "Signal" }, { label: "Use" }, { label: "Next 7 days", num: 1 }, { label: "" }, { label: "Next 30 days", num: 1 }, { label: "" }], null)}<tbody>${r.signals.map((s) => `<tr class="static"><td>${esc(s.label)}</td><td class="small muted">${s.side === "buy" ? "Buy" : "Avoid"}</td>${cell(s["7"])}${cell(s["30"])}</tr>`).join("")}</tbody></table></div>
    <p class="small muted" style="margin-bottom:0">Hover a number for each half. The hourly rules above include RSI, moving average cross and Bollinger versions you can tune.</p>`;
}
async function runBacktest(host) {
  const v = (id) => $(id, host).value.trim();
  Object.assign(BT, { strategy: v("#btS"), days: v("#btDays"), threshold: v("#btTh"), hold: v("#btHold"), lookback: v("#btLook"), volMult: v("#btVm"), minPrice: v("#btMinP"), maxPrice: v("#btMaxP"), minVol: v("#btMinV"), share: v("#btShare"), members: v("#btMem"), watchOnly: $("#btWatch", host).checked });
  store.set("bt", BT);
  const out = $("#btOut", host);
  out.innerHTML = `<div class="empty">Running...</div>`;
  let r;
  try {
    r = await api("/api/backtest", { method: "POST", body: { strategy: BT.strategy, days: numOr(BT.days, 30), threshold: numOr(BT.threshold, 5), hold: numOr(BT.hold, 6), lookback: numOr(BT.lookback, 24), volMult: numOr(BT.volMult, 3), minPrice: numOr(BT.minPrice, 0), maxPrice: numOr(BT.maxPrice, 0), minVol: numOr(BT.minVol, 0), share: numOr(BT.share, 20) / 100, members: BT.members, ids: BT.watchOnly ? [...S.watch] : [] } });
  } catch (e) { out.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const s = r.summary;
  if (!s.trades) { out.innerHTML = `<div class="empty">No trades triggered over ${r.itemsTested} items and ${gp(r.hoursOfData)} hours of history. Try a smaller threshold or a longer window.</div>`; return; }
  const beat = s.avgRet != null && s.baselineRet != null ? s.avgRet - s.baselineRet : null;
  out.innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="k">Trades</div><div class="v">${gp(s.trades)}</div><div class="s">${r.itemsTested} items tested</div></div>
      <div class="tile"><div class="k">Win rate</div><div class="v">${pct(s.winRate, 0)}</div><div class="s">Profit factor ${s.profitFactor == null ? "-" : s.profitFactor.toFixed(2)}</div></div>
      <div class="tile"><div class="k">Avg return</div><div class="v ${signCls(s.avgRet)}">${(s.avgRet > 0 ? "+" : "") + pct(s.avgRet, 2)}</div><div class="s">Median ${pct(s.medianRet, 2)}</div></div>
      <div class="tile"><div class="k">Total profit</div><div class="v ${signCls(s.profit)}">${signed(s.profit, short)}</div><div class="s">At your fill share</div></div>
      <div class="tile"><div class="k">Worst drawdown</div><div class="v ${s.maxDrawdown ? "neg" : ""}">${s.maxDrawdown ? "-" + short(s.maxDrawdown) : "0"}</div><div class="s">Peak to trough</div></div>
      <div class="tile"><div class="k">Vs holding</div><div class="v ${signCls(beat)}">${beat == null ? "-" : (beat > 0 ? "+" : "") + pct(beat, 2)}</div><div class="s">Avg item did ${pct(s.baselineRet, 2)} over the window</div></div>
    </div>
    <div class="callout">${s.profit > 0 && s.winRate > 0.5 ? "This rule would have made money on this history." : "This rule would not have made money on this history."} Past fills are estimates from hourly averages; real offers can miss, and a rule that fits one week can fail the next. Test a few windows before trusting it.</div>
    <div class="grid2">
      <div class="chart-card"><div class="chart-head"><span class="title">Cumulative profit</span><span class="muted small">By exit time</span></div><div id="btEq"></div></div>
      <div class="chart-card"><div class="chart-head"><span class="title">Return per trade</span><span class="muted small">After tax, 1% buckets</span></div><div id="btHist"></div></div>
    </div>
    <div class="grid2">
      <div><h3>Best items</h3><div class="table-wrap" id="btItems"></div></div>
      <div><h3>Recent trades</h3><div class="table-wrap" id="btTrades" style="max-height:420px;overflow-y:auto"></div></div>
    </div>`;
  lineChart($("#btEq", out), [{ name: "Profit", cls: "l1", color: "var(--series-1)", pts: r.equity }], { zero: true, height: 200, aria: "Backtest cumulative profit", tipFmt: (v) => signed(v) + " gp" });
  barChart($("#btHist", out), r.histogram, "pct", "n", { label: (p) => (p % 5 ? "" : p + "%"), maxLabels: 31, cls: (d) => (d.pct < 0 ? "neg" : "posb"), aria: "Return histogram", tip: (d) => `<div class="muted">${d.pct === 15 ? "15% or more" : d.pct === -15 ? "-15% or less" : `${d.pct}% to ${d.pct + 1}%`}</div><b>${d.n} trades</b>` });
  $("#btItems", out).innerHTML = `<table>${thead([{ label: "Item" }, { label: "Trades", num: 1 }, { label: "Win", num: 1 }, { label: "Avg", num: 1 }, { label: "Profit", num: 1 }], null)}<tbody>${r.items.map((b) => `<tr data-id="${b.id}"><td>${itemCell(b)}</td><td class="num">${b.trades}</td><td class="num">${pct(b.winRate, 0)}</td><td class="num ${signCls(b.avgRet)}">${pct(b.avgRet, 2)}</td><td class="num ${signCls(b.profit)}"><b>${signed(b.profit, short)}</b></td></tr>`).join("")}</tbody></table>`;
  $("#btTrades", out).innerHTML = `<table>${thead([{ label: "Item" }, { label: "Bought" }, { label: "Buy", num: 1 }, { label: "Sell", num: 1 }, { label: "Qty", num: 1 }, { label: "Return", num: 1 }], null)}<tbody>${r.trades.slice(0, 100).map((t) => `<tr data-id="${t.id}"><td>${itemCell(t)}</td><td class="muted small">${esc(fmtTime(t.entry, true))}</td><td class="num">${gp(t.buy)}</td><td class="num">${gp(t.sell)}</td><td class="num">${gp(t.qty)}</td><td class="num ${signCls(t.ret)}">${(t.ret > 0 ? "+" : "") + pct(t.ret, 2)}</td></tr>`).join("")}</tbody></table>`;
  bindRowClicks(out);
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
  const actGain = (k) => d.activities.some((a) => a["gain_" + k] > 0);
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
        <td class="num"><button class="btn small danger" data-gdel="${g.gid}">Delete</button></td></tr>
        ${g.methods && g.methods.length ? `<tr class="static"><td></td><td colspan="8" class="small muted">Cheapest ways at your level: ${g.methods.map((m) => `<b style="color:var(--text)">${esc(m.name)}</b> ${m.cost <= 0 ? `<span class="pos">earns ${short(-m.cost)}</span>` : `costs ${short(m.cost)}`} (${m.gpXp.toFixed(2)} gp/xp${m.hours ? `, about ${Math.round(m.hours)}h` : ""})`).join(" · ")}</td></tr>` : ""}`).join("")}</tbody></table>` : `<div class="empty">No goals yet.</div>`}</div>
    </div>
    <div class="grid2">
      <div><h3>Skills</h3><div class="table-wrap"><table>${thead([{ label: "Skill" }, { label: "Level", num: 1 }, { label: "XP", num: 1 }, { label: "To next", num: 1 }, { label: "" }, ...(hasGain("day") ? [{ label: "Today", num: 1 }] : []), ...(hasGain("week") ? [{ label: "Week", num: 1 }] : []), { label: "Rank", num: 1 }], null)}<tbody>${skills.map((s) => `
        <tr class="static"><td>${esc(s.name)}</td><td class="num"><b>${s.level > 0 ? s.level : "-"}</b>${s.virtual > 99 ? ` <span class="muted small">(${s.virtual})</span>` : ""}</td><td class="num">${s.xp >= 0 ? gp(s.xp) : "-"}</td>
        <td class="num muted">${s.toNext ? short(s.toNext) : "-"}</td><td><div class="progress" style="min-width:60px"><i style="width:${(s.pctToNext || 0) * 100}%"></i></div></td>
        ${hasGain("day") ? `<td class="num ${s.gain_day > 0 ? "pos" : "muted"}">${s.gain_day > 0 ? "+" + short(s.gain_day) : "-"}</td>` : ""}
        ${hasGain("week") ? `<td class="num ${s.gain_week > 0 ? "pos" : "muted"}">${s.gain_week > 0 ? "+" + short(s.gain_week) : "-"}</td>` : ""}
        <td class="num muted">${s.rank > 0 ? gp(s.rank) : "-"}</td></tr>`).join("")}</tbody></table></div></div>
      <div><h3>Bosses, clues and activities</h3><div class="table-wrap">${d.activities.length ? `<table>${thead([{ label: "Activity" }, { label: "Score", num: 1 }, ...(actGain("day") ? [{ label: "Today", num: 1 }] : []), ...(actGain("week") ? [{ label: "Week", num: 1 }] : []), { label: "Rank", num: 1 }], null)}<tbody>${d.activities.map((a) => `<tr class="static"><td>${esc(a.name)}</td><td class="num"><b>${gp(a.score)}</b></td>
        ${actGain("day") ? `<td class="num ${a.gain_day > 0 ? "pos" : "muted"}">${a.gain_day > 0 ? "+" + gp(a.gain_day) : "-"}</td>` : ""}
        ${actGain("week") ? `<td class="num ${a.gain_week > 0 ? "pos" : "muted"}">${a.gain_week > 0 ? "+" + gp(a.gain_week) : "-"}</td>` : ""}
        <td class="num muted">${gp(a.rank)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No ranked activities.</div>`}</div></div>
    </div>
    <div id="acBoss"></div>`;
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
  loadBoss($("#acBoss", body));
}

// Boss drops and dry streaks (part of the Account tab).
async function loadBoss(box) {
  let b;
  try { b = await api(`/api/boss?player=${encodeURIComponent(ACC.player)}&mode=${ACC.mode}`); } catch (e) { box.innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const bossOpts = `<datalist id="bossList">${b.activityNames.map((n) => `<option value="${esc(n)}">`).join("")}</datalist>`;
  const oneIn = (p) => (p == null ? "-" : p >= 0.995 ? "over 99%" : pct(p, p < 0.01 ? 2 : 0));
  box.innerHTML = `${bossOpts}
    <div class="section-head"><h3>Drops and dry streaks</h3></div>
    <p class="lede small">Log drops as you get them. Kill counts come from the hiscores (look up again after a trip), so dry streaks and GP per kill stay current. Chasing an item? Add it with its drop rate to see how unlucky you are.</p>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">Log a drop</h3>
        <div class="inline-form">
          <label class="field"><span>Boss or activity</span><input class="input" id="dpBoss" list="bossList" placeholder="e.g. Zulrah"></label>
          ${pickerField("Item", "dpItem", "field")}
          <label class="field"><span>Qty</span><input class="input" id="dpQty" value="1" inputmode="numeric"></label>
          <label class="field"><span>At KC</span><input class="input" id="dpKc" inputmode="numeric" placeholder="Current"></label>
          <button class="btn primary" id="dpAdd">Log drop</button></div>
        <div id="dpMsg" class="small" style="margin-top:6px"></div></div>
      <div class="card"><h3 style="margin-top:0">Chase an item</h3>
        <div class="inline-form">
          <label class="field"><span>Boss or activity</span><input class="input" id="chBoss" list="bossList"></label>
          <label class="field"><span>Item name</span><input class="input" id="chItem" placeholder="e.g. Tanzanite fang"></label>
          <label class="field"><span>Rate: 1 in</span><input class="input" id="chRate" inputmode="numeric" placeholder="e.g. 512"></label>
          <label class="field"><span>Start KC</span><input class="input" id="chKc" inputmode="numeric" placeholder="0 = lifetime"></label>
          <button class="btn primary" id="chAdd">Add</button></div>
        <div id="chMsg" class="small" style="margin-top:6px"></div></div>
    </div>
    ${b.chases.length ? `<h3>Chasing</h3><div class="table-wrap"><table>${thead([{ label: "Item" }, { label: "Boss" }, { label: "Rate", num: 1 }, { label: "KC now", num: 1 }, { label: "Dry for", num: 1 }, { label: "Expected by now", num: 1 }, { label: "Chance of being this dry", num: 1 }, { label: "" }], null)}<tbody>${b.chases.map((c) => `
      <tr class="static"><td><b>${esc(c.item_name)}</b>${c.got ? ` <span class="tag free">Got ${c.got}</span>` : ""}</td><td>${esc(c.boss)}</td><td class="num">1/${gp(c.rate_n)}</td><td class="num">${gp(c.kc)}</td>
      <td class="num"><b>${gp(c.dry)}</b> kc</td><td class="num">${c.expected == null ? "-" : c.expected.toFixed(2)}</td>
      <td class="num" title="${c.luckier != null ? `About ${pct(c.luckier, 0)} of players would have it by now` : ""}">${oneIn(c.chanceDry)}${c.chanceDry != null && c.chanceDry < 0.25 ? ` <span class="tag trap">Very dry</span>` : ""}</td>
      <td class="num"><button class="btn small danger" data-cdel="${c.cid}">Delete</button></td></tr>`).join("")}</tbody></table></div>` : ""}
    ${b.bosses.length ? `<h3>Loot by boss</h3><div class="table-wrap"><table>${thead([{ label: "Boss" }, { label: "KC", num: 1 }, { label: "Drops logged", num: 1 }, { label: "Loot value", num: 1 }, { label: "GP per kill", num: 1, title: "Logged loot value over kills since tracking started" }], null)}<tbody>${b.bosses.map((x) => `<tr class="static"><td><b>${esc(x.boss)}</b></td><td class="num">${gp(x.kc)}</td><td class="num">${x.drops}</td><td class="num">${short(x.value)}</td><td class="num">${x.gpPerKc == null ? "-" : short(x.gpPerKc)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${b.drops.length ? `<h3>Drop log</h3><div class="table-wrap"><table>${thead([{ label: "Item" }, { label: "Boss" }, { label: "Qty", num: 1 }, { label: "KC", num: 1 }, { label: "Value now", num: 1 }, { label: "Date" }, { label: "" }], null)}<tbody>${b.drops.map((x) => `<tr ${x.item_id ? `data-id="${x.item_id}"` : `class="static"`}><td>${itemCell({ name: x.item_name, icon: x.icon })}</td><td>${esc(x.boss)}</td><td class="num">${gp(x.qty)}</td><td class="num">${x.kc == null ? "-" : gp(x.kc)}</td><td class="num">${x.value == null ? "-" : short(x.value)}</td><td class="muted">${esc(fmtTime(x.ts, true))}</td><td class="num"><button class="btn small danger" data-ddel="${x.did}">Delete</button></td></tr>`).join("")}</tbody></table></div>` : ""}`;
  let picked = null;
  makePicker($("#dpItem", box), $("#dpItemList", box), (r) => { picked = r; $("#dpItem", box).value = r.name; });
  $("#dpItem", box).addEventListener("input", () => { picked = null; });
  $("#dpBoss", box).addEventListener("change", () => { const kc = b.kc[$("#dpBoss", box).value]; if (kc != null) $("#dpKc", box).placeholder = "Current " + gp(kc); });
  $("#dpAdd", box).onclick = async () => {
    const boss = $("#dpBoss", box).value.trim(), name = $("#dpItem", box).value.trim();
    const kcTxt = $("#dpKc", box).value.trim(), kc = kcTxt ? numOr(kcTxt, null) : (b.kc[boss] ?? null);
    try {
      await api("/api/drops", { method: "POST", body: { player: ACC.player, mode: ACC.mode, boss, item_id: picked ? picked.id : null, item_name: name, qty: numOr($("#dpQty", box).value, 1), kc } });
      loadBoss(box);
    } catch (e) { $("#dpMsg", box).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $("#chAdd", box).onclick = async () => {
    const boss = $("#chBoss", box).value.trim();
    const kcTxt = $("#chKc", box).value.trim();
    try {
      await api("/api/chase", { method: "POST", body: { player: ACC.player, mode: ACC.mode, boss, item_name: $("#chItem", box).value, rate_n: numOr($("#chRate", box).value, 0), start_kc: kcTxt ? numOr(kcTxt, 0) : 0 } });
      loadBoss(box);
    } catch (e) { $("#chMsg", box).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $$("[data-cdel]", box).forEach((x) => (x.onclick = async () => { if (!confirmInline(x)) return; await api("/api/chase?cid=" + x.dataset.cdel, { method: "DELETE" }); loadBoss(box); }));
  $$("[data-ddel]", box).forEach((x) => (x.onclick = async (e) => { e.stopPropagation(); if (!confirmInline(x)) return; await api("/api/drops?did=" + x.dataset.ddel, { method: "DELETE" }); loadBoss(box); }));
  bindRowClicks(box);
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
          <label class="field" title="Hours of hourly history to fetch at startup if missing (max 720 = 30 days). One request per hour of history, a second apart."><span>History backfill (hours)</span><input class="input" id="stBack" value="${cfg.backfill_hours}"></label>
        </div>
        <h3>Flip model</h3>
        <div class="inline-form" style="margin-top:0">
          <label class="field" title="Share of an item's instant-sell and instant-buy volume you expect to win against other flippers"><span>Your fill share %</span><input class="input" id="stShare" value="${Math.round((cfg.fill_share || 0.2) * 100)}"></label>
          <label class="field" title="Flag a margin as a possible trap when its two prices traded this far apart"><span>Trap gap (min)</span><input class="input" id="stTrap" value="${cfg.trap_gap_minutes}"></label>
          <label class="field" title="Hours of 5 minute history behind the stability score"><span>Stability window (h)</span><input class="input" id="stStab" value="${cfg.stability_hours}"></label>
          <label class="field" title="Days of daily market history to import from the Wiki (bulk, one request per day of history, once). Used by the forecast and its backtest."><span>Import history (days)</span><input class="input" id="stImp" value="${cfg.history_import_days}"></label>
          <label class="field" title="Daily database copies to keep in data/backups (0 turns it off)"><span>Daily backups kept</span><input class="input" id="stBk" value="${cfg.auto_backup_days}"></label>
          <label class="check"><input type="checkbox" id="stLim" ${cfg.notify_limit_reset ? "checked" : ""}> Alert me when a buy limit resets</label>
        </div>
        <h3>Account (RuneLite plugin)</h3>
        <div class="inline-form" style="margin-top:0">
          <label class="field wide" title="Where the Bankstanding RuneLite plugin writes its files. Blank uses the default."><span>Plugin folder</span><input class="input" id="stRl" value="${esc(cfg.runelite_folder || "")}" placeholder="Default: .runelite/bankstanding in your user folder"></label>
          <label class="field" title="Sell price after tax is what you would get listing everything at the going rate. Mid price is closer to price sites."><span>Value items at</span><select class="input" id="stNwv"><option value="sell" ${cfg.networth_value !== "market" ? "selected" : ""}>Sell price after tax</option><option value="market" ${cfg.networth_value === "market" ? "selected" : ""}>Mid price</option></select></label>
          <label class="check"><input type="checkbox" id="stNwm" ${cfg.networth_include_manual !== false ? "checked" : ""}> Include Portfolio tab holdings in the all accounts net worth</label>
        </div>
        <div style="margin-top:12px"><button class="btn primary" id="stSave">Save</button> <span id="stMsg" class="small muted"></span></div>
      </div>
      <div class="card"><h3 style="margin-top:0">Status</h3><div class="kv">
        <span class="k">Items tracked</span><span>${gp(st.items)} (${gp(st.priced)} with prices)</span>
        <span class="k">Last price refresh</span><span>${st.last_latest ? esc(fmtTime(st.last_latest, true)) : "Never"}</span>
        <span class="k">History backfill</span><span>${esc(st.backfill)}</span>
        <span class="k">Hourly snapshots saved</span><span>${gp(st.h1Snapshots)} (${(st.h1Snapshots / 24).toFixed(1)} days)</span>
        <span class="k">5 minute snapshots saved</span><span>${gp(st.m5Snapshots)} (${gp(st.m5Windows)} in the stability window)</span>
        <span class="k">Database size</span><span>${short(st.dbBytes / 1024)} KB</span>
        <span class="k">GE tax</span><span>${(cfg.tax_rate * 100).toFixed(1)}%, capped at ${short(cfg.tax_cap)} per item</span>
        <span class="k">Daily history imported</span><span>${gp(st.d1Snapshots)} days${st.import !== "done" && st.import !== "idle" ? ` (importing ${esc(st.import)})` : ""}</span>
        <span class="k">Latest backups</span><span>${st.backups.length ? st.backups.slice(-3).map(esc).join("<br>") : "None yet"}</span>
      </div>
      <div class="inline-form"><button class="btn" id="stBackup">Back up now</button><a class="btn" href="/api/export">Export my data (JSON)</a><span id="stBkMsg" class="small muted"></span></div>
      ${st.errors.length ? `<h3>Recent problems</h3><div class="small err">${st.errors.map(esc).join("<br>")}</div>` : `<p class="small muted" style="margin-top:12px">No problems reported.</p>`}
      </div>
    </div>
    <p class="small muted" style="margin-top:16px">Prices come from the OSRS Wiki real-time prices API (data provided by RuneLite users). They're strong estimates, not guarantees: price check in game before big flips.</p>`;
  $("#stSave", host).onclick = async () => {
    const v = (id) => $(id, host).value;
    try {
      const r = await api("/api/settings", { method: "POST", body: { user_agent: v("#stUA"), latest_poll_seconds: v("#stPoll"), stale_minutes: v("#stStale"), alert_cooldown_minutes: v("#stCool"), keep_5m_days: v("#st5"), keep_1h_days: v("#st1"), backfill_hours: v("#stBack"), fill_share: v("#stShare"), trap_gap_minutes: v("#stTrap"), stability_hours: v("#stStab"), auto_backup_days: v("#stBk"), history_import_days: v("#stImp"), notify_limit_reset: $("#stLim", host).checked, runelite_folder: v("#stRl"), networth_value: v("#stNwv"), networth_include_manual: $("#stNwm", host).checked } });
      $("#stMsg", host).textContent = "Saved. " + (r.note || "");
      loadMarket();
    } catch (e) { $("#stMsg", host).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $("#stBackup", host).onclick = async () => {
    try { const r = await api("/api/backup", { method: "POST" }); $("#stBkMsg", host).textContent = "Saved " + r.file; }
    catch (e) { $("#stBkMsg", host).innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
};

// Item drawer --------------------------------------------------------------------
const DR = { lookback: store.get("lookback", "24h"), season: store.get("season", 30) };
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
      <button class="btn small" id="drTerm" title="Open in the Terminal">Terminal</button><button class="icon-btn close" id="drClose" aria-label="Close">✕</button></div>
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
      <div class="tile"><div class="k">Stability</div><div class="v">${r.stability != null ? pct(r.stability, 0) : "-"}</div><div class="s">${r.avgMargin5m != null ? `Avg 5m margin ${gp(r.avgMargin5m)}` : "Recent windows profitable"}</div></div>
      <div class="tile"><div class="k">Fill time</div><div class="v">${r.fillHrs != null ? (r.fillHrs < 10 ? r.fillHrs.toFixed(1) : Math.round(r.fillHrs)) + "h" : "-"}</div><div class="s">One full limit at your share</div></div>
      <div class="tile"><div class="k">Your limit</div><div class="v">${d.limit ? gp(d.limit.left) : gp(m.limit)}</div><div class="s">${d.limit ? `Left, resets in ${ago(d.limit.resetAt - Date.now() / 1000)}` : "Nothing bought this window"}</div></div>
      <div class="tile"><div class="k">Break-even sell</div><div class="v">${gp(d.breakeven)}</div><div class="s">If bought at ${gp(r.low)}</div></div>
    </div>
    ${r.trap ? `<div class="notice">Possible margin trap: ${r.gap != null && r.gap > 900 ? `the instant-buy and instant-sell prices traded ${ago(r.gap)} apart, so both sides may not be available together.` : "recent 5 minute averages show no margin after tax, so the latest prices may be a one-off."}</div>` : ""}
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
      <button class="btn" id="drHoldBtn">Add to portfolio</button>
      <span class="small muted" style="align-self:center;margin-left:auto">Break-even: buy at <input class="input" id="drBe" style="width:110px;display:inline-block" value="${r.low || ""}"> sell at least <b id="drBeOut">${gp(d.breakeven)}</b></span>
    </div>
    <div id="drForm"></div>
    <div class="chart-card" style="margin-top:14px">
      <div class="chart-head"><span class="title">Best time to trade</span><span class="muted small">Your saved hourly history, local time</span>
        <div class="seg" id="drSeason">${[14, 30, 90].map((dd) => `<button data-d="${dd}" class="${DR.season === dd ? "on" : ""}">${dd}d</button>`).join("")}</div></div>
      <div id="drHeat"><div class="empty">Loading...</div></div><div id="drHeatNote" class="small" style="margin-top:6px"></div>
    </div>
    <div class="card" style="margin-bottom:14px"><div class="section-head" style="margin-top:0"><h3>Outlook</h3><div class="right"><button class="btn small" id="drHo">Holding outlook</button><button class="btn small" id="drFc">Flipping forecast</button></div></div><div id="drHoBody" class="small" style="margin-bottom:8px"><div class="muted">Loading...</div></div><div id="drFcBody" class="small"><div class="muted">Loading...</div></div></div>
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">Moves with</h3><div id="drCorr" class="small"><div class="muted">Loading...</div></div></div>
      <div class="card"><h3 style="margin-top:0">Recipes and sets</h3><div id="drRec" class="small"><div class="muted">Loading...</div></div></div>
    </div>
    ${d.holdings.length ? `<h3>In your portfolio</h3><div class="small">${d.holdings.map((h) => `<div>${gp(h.qty)} held${h.cost_each != null ? ` at ${gp(h.cost_each)} each` : ""}</div>`).join("")}</div>` : ""}
    ${d.alerts.length ? `<h3>Alerts on this item</h3><div class="small">${d.alerts.map((a) => `<div>${esc((ALERT_KINDS[a.kind] || a.kind))} ${esc(condValue(a.kind, a.threshold))}${a.extra ? " and more" : ""}${a.enabled ? "" : " (paused)"}</div>`).join("")}</div>` : ""}
    ${d.flips.length ? `<h3>Your flips</h3><div class="small">${d.flips.map((f) => `<div>${esc(fmtTime(f.buy_ts, true))}: ${gp(f.qty)} bought at ${gp(f.buy_price)}${f.sell_price != null ? `, sold at ${gp(f.sell_price)}` : " (open)"}</div>`).join("")}</div>` : ""}`;
  $("#drClose").onclick = closeItem;
  $("#drTerm").onclick = () => openTerminal(id);
  $("#drWatch").onclick = () => toggleWatch(id);
  $$("#drLb button").forEach((b) => (b.onclick = () => { DR.lookback = b.dataset.l; store.set("lookback", DR.lookback); $$("#drLb button").forEach((x) => x.classList.toggle("on", x === b)); loadChart(id); }));
  $("#drAlertBtn").onclick = () => {
    $("#drForm").innerHTML = `<div class="card"><div class="inline-form" style="margin-top:0">
      <label class="field wide"><span>When</span><select class="input" id="daKind">${kindOptions()}</select></label>
      <label class="field"><span>Value</span><input class="input" id="daTh" value="${r.low || ""}"></label>
      <label class="check"><input type="checkbox" id="daOnce"> Only once</label>
      <button class="btn primary" id="daSave">Save alert</button></div><div id="daMsg" class="small"></div></div>`;
    $("#daSave").onclick = async () => {
      const kind = $("#daKind").value, th = numOr($("#daTh").value, NaN);
      if (!NOVAL_KINDS.has(kind) && !Number.isFinite(th)) { $("#daMsg").innerHTML = `<span class="err">Enter a number.</span>`; return; }
      await api("/api/alerts", { method: "POST", body: { item_id: id, kind, threshold: NOVAL_KINDS.has(kind) ? 0 : th, once: $("#daOnce").checked } });
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
  $("#drHoldBtn").onclick = () => {
    $("#drForm").innerHTML = `<div class="card"><div class="inline-form" style="margin-top:0">
      <label class="field"><span>Quantity</span><input class="input" id="dhQty"></label>
      <label class="field"><span>Cost each</span><input class="input" id="dhCost" value="${r.low || ""}"></label>
      <button class="btn primary" id="dhSave">Add to portfolio</button></div><div id="dhMsg" class="small"></div></div>`;
    $("#dhSave").onclick = async () => {
      const qty = numOr($("#dhQty").value, NaN), cost = $("#dhCost").value.trim();
      if (!(qty > 0)) { $("#dhMsg").innerHTML = `<span class="err">Enter a quantity.</span>`; return; }
      await api("/api/holdings", { method: "POST", body: { item_id: id, qty: Math.round(qty), cost_each: cost ? Math.round(numOr(cost, 0)) : null } });
      openItem(id);
      if (S.tab === "portfolio") renderTab("portfolio");
    };
  };
  $("#drBe").addEventListener("input", (e) => { const b = numOr(e.target.value, NaN); $("#drBeOut").textContent = Number.isFinite(b) ? gp(breakeven(b, r)) : "-"; });
  $$("#drSeason button").forEach((b) => (b.onclick = () => { DR.season = +b.dataset.d; store.set("season", DR.season); $$("#drSeason button").forEach((x) => x.classList.toggle("on", x === b)); loadSeason(id); }));
  $("#drFc").onclick = () => { FC.sel = id; FC.days = 30; store.set("fc", FC); store.set("fcMode", "flip"); closeItem(); showTab("forecast"); };
  $("#drHo").onclick = () => { HO.sel = id; store.set("ho", HO); store.set("fcMode", "hold"); closeItem(); showTab("forecast"); };
  loadChart(id);
  loadSeason(id);
  loadDrawerForecast(id);
  loadDrawerHold(id);
  loadRelated(id, r);
}
async function loadDrawerHold(id) {
  let o;
  try { o = await api(`/api/hold?id=${id}&qty=1`); } catch (e) { o = { ok: false, reason: e.message }; }
  const box = $("#drHoBody");
  if (!box || S.openId !== id) return;
  if (!o.ok) { box.innerHTML = `<span class="muted">Holding: ${esc(o.reason)}</span>`; return; }
  const p30 = o.points.find((p) => p.h === 30), p90 = o.points.find((p) => p.h === 90);
  box.innerHTML = `<b>Holding:</b> ${verdictTag(o.verdict, true)} Expected ${p30 ? `<b class="${signCls(p30.ret)}">${sgnPct(p30.ret)}</b> in 30 days (${sgnPct(p30.lo, 0)} to ${sgnPct(p30.hi, 0)}, ${pct(p30.pUp, 0)} chance it rises)` : ""}${p90 ? ` and <b class="${signCls(p90.ret)}">${sgnPct(p90.ret)}</b> in 90 days` : ""}.`;
}
async function loadDrawerForecast(id) {
  let f;
  try { f = await api(`/api/forecast?id=${id}&days=30&windows=2&share=${Math.round((S.fillShare || 0.2) * 100)}`); } catch (e) { f = { ok: false, reason: e.message }; }
  const box = $("#drFcBody");
  if (!box || S.openId !== id) return;
  if (!f.ok) { box.innerHTML = `<span class="muted">${esc(f.reason)}</span>`; return; }
  const s = f.summary;
  box.innerHTML = `<b>Flipping:</b> doing this for 30 days (2 limit windows a day) would likely earn <b class="${signCls(s.p50)}">${signed(s.p50, short)}</b>, with 8 in 10 outcomes between <b>${short(s.p10)}</b> and <b>${short(s.p90)}</b> and a ${pct(s.lossChance, 0)} chance of a loss. Price ${s.useTrend ? `trend: <b class="${signCls(s.priceChange)}">${s.priceChange > 0 ? "+" : ""}${pct(s.priceChange, 1)}</b>` : "is assumed flat"}. ${confTag(f.confidence)}`;
}
async function loadSeason(id) {
  const host = $("#drHeat");
  if (!host) return;
  try {
    const s = await api(`/api/seasonality?id=${id}&days=${DR.season}`);
    if (S.openId !== id) return;
    heatmap(host, s.grid);
    const note = $("#drHeatNote");
    note.innerHTML = s.bestBuyHour != null && s.daysUsed >= 2
      ? `Over ${s.daysUsed} days, prices ran lowest around <b>${esc(hourLabel(s.bestBuyHour))}</b> and highest around <b>${esc(hourLabel(s.bestSellHour))}</b>, a swing of <b>${pct(s.spread, 2)}</b>.${s.widestMarginHour != null ? ` Margins were widest around <b>${esc(hourLabel(s.widestMarginHour))}</b>.` : ""}${s.spread < 0.005 ? " That is small, so timing matters little for this item." : ""}`
      : `<span class="muted">Needs at least two days of saved hourly history.</span>`;
  } catch (e) { host.innerHTML = `<div class="notice">${esc(e.message)}</div>`; }
}
async function loadRelated(id, row) {
  const [c, rec] = await Promise.all([api(`/api/correlated?id=${id}&days=7`).catch(() => null), api(`/api/recipes/item?id=${id}`).catch(() => [])]);
  if (S.openId !== id) return;
  const ch = $("#drCorr"), rh = $("#drRec");
  if (ch) {
    ch.innerHTML = c && c.similar.length ? `<div class="muted" style="margin-bottom:4px">Hourly price moves over 7 days (1 = in lockstep)</div><div class="mini-list">${c.similar.map((x) => `<div class="r" data-id="${x.id}"><img alt="" src="${esc(iconUrl(x.icon))}" onerror="this.style.visibility='hidden'"><span>${esc(x.name)}</span><span class="v">${x.corr.toFixed(2)}</span></div>`).join("")}</div>${c.opposite.length ? `<div class="muted" style="margin:8px 0 4px">Moves against it</div><div class="mini-list">${c.opposite.map((x) => `<div class="r" data-id="${x.id}"><img alt="" src="${esc(iconUrl(x.icon))}" onerror="this.style.visibility='hidden'"><span>${esc(x.name)}</span><span class="v">${x.corr.toFixed(2)}</span></div>`).join("")}</div>` : ""}`
      : `<div class="muted">Needs about two days of hourly history with trades in most hours.</div>`;
    $$(".r[data-id]", ch).forEach((el) => el.addEventListener("click", () => openItem(+el.dataset.id)));
  }
  if (rh) {
    rh.innerHTML = rec.length ? `<div class="mini-list">${rec.slice(0, 10).map((x) => `<div class="r" title="${esc(x.inputs.map((i) => i.qty + " x " + i.name).join(", "))}"><span class="tag">${x.role === "input" ? "Used in" : x.role === "output" ? "Made by" : "Set"}</span><span>${esc(x.name)}</span><span class="v ${signCls(x.profit)}">${signed(x.profit)} each</span></div>`).join("")}</div><div class="muted" style="margin-top:6px">Patient prices, after tax. See Money making for GP per hour.</div>`
      : `<div class="muted">Not part of any tracked recipe or set.</div>`;
    $$(".r", rh).forEach((el) => el.addEventListener("click", () => { closeItem(); showTab("money"); }));
  }
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
function setBell(n) { const b = $("#bellCount"); b.hidden = !n; b.textContent = n; document.title = n ? `(${n}) Bankstanding` : "Bankstanding"; }
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
      if (document.hidden && desktopState() === "granted" && store.get("desktop", true)) {
        fresh.slice(0, 3).forEach((x) => {
          try {
            const nt = new Notification("Bankstanding", { body: x.message, tag: "geco-" + x.nid });
            nt.onclick = () => { window.focus(); if (x.item_id) openItem(x.item_id); nt.close(); };
          } catch (e) { /* notifications unavailable */ }
        });
      }
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
    S.limits = d.limits || {}; S.tax = d.tax; S.fillShare = d.fillShare;
    setLive();
    if (!S.loaded) { S.loaded = true; showTab(S.tab in renderers ? S.tab : "networth"); }
    else if (["flips", "movers", "alch", "market", "networth"].includes(S.tab)) renderTab(S.tab, true);
  } catch (e) {
    $("#liveStatus").className = "live bad"; $(".txt", $("#liveStatus")).textContent = "App not running";
  }
}

(async function boot() {
  try { const a = await api("/api/alerts"); ALERT_KINDS = a.kinds; PCT_KINDS = new Set(a.pctKinds); NOVAL_KINDS = new Set(a.noValueKinds); } catch (e) { /* ignore */ }
  await loadMarket();
  await pollNotifications();
  setInterval(loadMarket, 30000);
  setInterval(pollNotifications, 15000);
  setInterval(setLive, 5000);
})();
