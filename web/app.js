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
// net worth). series: [{name, cls: "l1" | "l2" | "l3", color, pts: [{t, v}]}].
// With two or more series there is a legend and each line is labelled at its end.
function lineChart(host, series, opts = {}) {
  series = series.filter((s) => s.pts && s.pts.length >= 2);
  const W = Math.max(320, host.clientWidth || 600), H = opts.height || 230, multi = series.length > 1;
  const padL = 62, padR = multi ? 96 : 14, padT = 10, padB = 22;
  const all = series.flatMap((s) => s.pts);
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
    ${series.map((s) => `<path class="${s.cls}" d="${path(s.pts)}"/>`).join("")}
    ${multi ? ends.map((e) => `<text class="endlbl" x="${W - padR + 6}" y="${e.yy + 4}">${esc(e.s.name.length > 13 ? e.s.name.slice(0, 12) + "." : e.s.name)}</text>`).join("") : ""}
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
    const px = x(near[0].t);
    g.setAttribute("visibility", "visible");
    $(".xhair", g).setAttribute("x1", px); $(".xhair", g).setAttribute("x2", px);
    $$("circle", g).forEach((c) => { const p = near[+c.dataset.s]; c.setAttribute("cx", x(p.t)); c.setAttribute("cy", y(p.v)); });
    tip.innerHTML = `<div class="muted">${esc(fmtTime(near[0].t, true))}</div>` + series.map((s, i) => `<div class="r"><span>${multi ? `<i style="background:${s.color}"></i>` : ""}${esc(s.name)}</span><b>${(opts.tipFmt || fmt)(near[i].v)}</b></div>`).join("");
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
    <p class="lede">Record what you actually bought and sold to track real profit after tax. Open flips (no sell price yet) are valued at the current instant-buy price. Buys you log here also count against the item's GE buy limit, so the flip finder and planner show what you have left.</p>
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
      <tr data-id="${f.item_id}"><td>${itemCell(f, f.note ? ` <span class="muted small">${esc(f.note)}</span>` : "")}</td><td class="num">${gp(f.qty)}</td><td class="num">${gp(f.buy_price)}</td>
      <td class="num">${f.open ? `<span class="muted">Open (now ${gp(f.livePrice)})</span>` : gp(f.sell_price)}</td><td class="num muted">${gp(f.taxEach)}</td>
      <td class="num ${signCls(f.open ? f.unrealized : f.profit)}">${f.open ? `<span title="Unrealized">${signed(f.unrealized)}*</span>` : signed(f.profit)}</td>
      <td class="num">${pct(f.roi, 2)}</td><td class="num small">${f.buyEdge == null && f.sellEdge == null ? "-" : `${edge(f.buyEdge)}${f.sellEdge != null ? " / " + edge(f.sellEdge) : ""}`}</td><td class="muted">${esc(fmtTime(f.sell_ts || f.buy_ts, true))}</td>
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
  host.innerHTML = `<h2>Portfolio</h2>
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
    <div class="table-wrap">${p.holdings.length ? `<table>${thead([{ label: "Item" }, { label: "Qty", num: 1 }, { label: "Cost each", num: 1 }, { label: "Price", num: 1 }, { label: "Value", num: 1 }, { label: "P/L", num: 1 }, { label: "24h", num: 1 }, { label: "Share" }, { label: "" }], null)}<tbody>${p.holdings.sort((a, b) => (b.value || 0) - (a.value || 0)).map((h) => `
      <tr data-id="${h.item_id}"><td>${itemCell(h)}</td><td class="num">${gp(h.qty)}</td><td class="num">${h.costEach == null ? "-" : gp(h.costEach)}</td><td class="num">${gp(h.price)}</td>
      <td class="num"><b>${short(h.value)}</b></td><td class="num ${signCls(h.pnl)}">${h.pnl == null ? "-" : signed(h.pnl, short)}</td>
      <td class="num ${signCls(h.chg24h)}">${h.chg24h == null ? "-" : (h.chg24h > 0 ? "+" : "") + pct(h.chg24h)}</td>
      <td class="small">${h.share == null ? "-" : `<span class="share" style="width:${Math.max(2, Math.round(h.share * 80))}px"></span>${pct(h.share, 0)}`}</td>
      <td class="num"><button class="btn small danger" data-hdel="${h.hid}">Delete</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No holdings yet. Add some above or paste a list.</div>`}</div>
    ${p.openFlips.length ? `<h3>Open flips</h3><div class="table-wrap"><table>${thead([{ label: "Item" }, { label: "Qty", num: 1 }, { label: "Cost", num: 1 }, { label: "Value now", num: 1 }, { label: "P/L", num: 1 }], null)}<tbody>${p.openFlips.map((f) => `<tr data-id="${f.item_id}"><td>${itemCell(f)}</td><td class="num">${gp(f.qty)}</td><td class="num">${short(f.cost)}</td><td class="num">${short(f.value)}</td><td class="num ${signCls(f.pnl)}">${signed(f.pnl, short)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
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

// Money making: recipes and item sets ---------------------------------------------
const MM = Object.assign({ patient: true, skill: "all", hideRisky: true, q: "", setDir: "all", rates: {} }, store.get("mm", {}));
let MM_DATA = null;
renderers.money = async function (host) {
  host.innerHTML = `<h2>Money making</h2>
    <p class="lede">Processing and skilling methods priced live, ranked by GP per hour. Buy limits cap how many actions you can supply from the GE, which is flagged. Rates are typical for a focused player; type your own in the Per hour column. Burns, failures and travel are not counted. <b>Stale</b> or <b>Thin</b> means one leg's price is old or barely trades, so the profit may not be real.</p>
    <div class="filters">
      <label class="field wide"><span>Search</span><input class="input" id="mmQ" value="${esc(MM.q)}" placeholder="e.g. potion, bar"></label>
      <label class="field"><span>Skill</span><select class="input" id="mmSkill"></select></label>
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
  try { MM_DATA = await api("/api/recipes?patient=" + (MM.patient ? 1 : 0)); } catch (e) { $("#mmTable", host).innerHTML = `<div class="notice">${esc(e.message)}</div>`; return; }
  const skills = [...new Set(MM_DATA.methods.map((m) => m.skill))].sort();
  $("#mmSkill", host).innerHTML = `<option value="all">All skills</option>` + skills.map((k) => `<option ${MM.skill === k ? "selected" : ""}>${esc(k)}</option>`).join("");
  $("#crList", host).innerHTML = MM_DATA.custom.length ? `<div class="small">${MM_DATA.custom.map((c) => `<div style="display:flex;gap:8px;align-items:center;padding:3px 0"><b>${esc(c.name)}</b><span class="muted">${esc(c.skill || "")}</span><button class="btn small danger" data-rdel="${c.rid}" style="margin-left:auto">Delete</button></div>`).join("")}</div>` : "";
  $$("[data-rdel]", host).forEach((b) => (b.onclick = async () => { if (!confirmInline(b)) return; await api("/api/recipes/custom?rid=" + b.dataset.rdel, { method: "DELETE" }); loadMoney(host); }));
  drawMoney(host);
}
const MMS = { key: "gpHrLive", dir: "desc" }, MSS = { key: "profit", dir: "desc" };
function drawMoney(host) {
  if (!MM_DATA) return;
  const q = MM.q.trim().toLowerCase();
  let rows = MM_DATA.methods.filter((m) => (MM.skill === "all" || m.skill === MM.skill) && (!q || m.name.toLowerCase().includes(q) || m.inputs.concat(m.outputs).some((i) => i.name.toLowerCase().includes(q)))
    && (!MM.hideRisky || (!m.stale && !m.thin))).map((m) => {
    const rate = MM.rates[m.name] || m.perHour;
    const eff = m.limitPerHour != null ? Math.min(rate, m.limitPerHour) : rate;
    return Object.assign({}, m, { rateLive: rate, gpHrLive: eff ? m.profit * eff : null, xpHrLive: eff ? m.xp * eff : null, cappedLive: m.limitPerHour != null && rate > m.limitPerHour });
  });
  rows = sortRows(rows, MMS.key, MMS.dir);
  const legs = (arr, key) => arr.map((i) => `${i.qty !== 1 ? gp(i.qty) + " x " : ""}${esc(i.name)}`).join(", ");
  $("#mmTable", host).innerHTML = rows.length ? `<table>${thead([{ label: "Method", sort: "name" }, { label: "Skill", sort: "skill" }, { label: "Lvl", sort: "level", num: 1 }, { label: "Uses" }, { label: "Cost", sort: "cost", num: 1 }, { label: "Profit each", sort: "profit", num: 1 }, { label: "XP", sort: "xp", num: 1 }, { label: "GP / XP", sort: "gpXp", num: 1 }, { label: "Per hour", num: 1, title: "Actions per hour; type your own" }, { label: "GP / hr", sort: "gpHrLive", num: 1 }, { label: "XP / hr", sort: "xpHrLive", num: 1 }], MMS)}<tbody>${rows.map((m) => `
    <tr data-id="${m.id}"><td>${itemCell(m, (m.cappedLive ? ` <span class="tag limit" title="Buy limits allow about ${gp(m.limitPerHour)} per hour">Limit capped</span>` : "") + (m.stale ? ` <span class="tag stale" title="A price is ${ago(m.maxAge)} old">Stale</span>` : "") + (m.thin ? ` <span class="tag thin" title="A leg trades about ${gp(m.minVol24)} a day">Thin</span>` : "") + (m.custom ? ` <span class="tag">Yours</span>` : "") + (m.note ? ` <span class="muted small">${esc(m.note)}</span>` : ""))}</td>
    <td>${esc(m.skill)}</td><td class="num">${m.level || "-"}</td><td class="small muted" style="max-width:260px">${legs(m.inputs)}</td>
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
    <div id="btOut"></div>`;
  $("#btMem", host).value = BT.members;
  $("#btRun", host).onclick = () => runBacktest(host);
};
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
          <label class="field" title="Daily database copies to keep in data/backups (0 turns it off)"><span>Daily backups kept</span><input class="input" id="stBk" value="${cfg.auto_backup_days}"></label>
          <label class="check"><input type="checkbox" id="stLim" ${cfg.notify_limit_reset ? "checked" : ""}> Alert me when a buy limit resets</label>
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
      const r = await api("/api/settings", { method: "POST", body: { user_agent: v("#stUA"), latest_poll_seconds: v("#stPoll"), stale_minutes: v("#stStale"), alert_cooldown_minutes: v("#stCool"), keep_5m_days: v("#st5"), keep_1h_days: v("#st1"), backfill_hours: v("#stBack"), fill_share: v("#stShare"), trap_gap_minutes: v("#stTrap"), stability_hours: v("#stStab"), auto_backup_days: v("#stBk"), notify_limit_reset: $("#stLim", host).checked } });
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
    <div class="grid2">
      <div class="card"><h3 style="margin-top:0">Moves with</h3><div id="drCorr" class="small"><div class="muted">Loading...</div></div></div>
      <div class="card"><h3 style="margin-top:0">Recipes and sets</h3><div id="drRec" class="small"><div class="muted">Loading...</div></div></div>
    </div>
    ${d.holdings.length ? `<h3>In your portfolio</h3><div class="small">${d.holdings.map((h) => `<div>${gp(h.qty)} held${h.cost_each != null ? ` at ${gp(h.cost_each)} each` : ""}</div>`).join("")}</div>` : ""}
    ${d.alerts.length ? `<h3>Alerts on this item</h3><div class="small">${d.alerts.map((a) => `<div>${esc((ALERT_KINDS[a.kind] || a.kind))} ${esc(condValue(a.kind, a.threshold))}${a.extra ? " and more" : ""}${a.enabled ? "" : " (paused)"}</div>`).join("")}</div>` : ""}
    ${d.flips.length ? `<h3>Your flips</h3><div class="small">${d.flips.map((f) => `<div>${esc(fmtTime(f.buy_ts, true))}: ${gp(f.qty)} bought at ${gp(f.buy_price)}${f.sell_price != null ? `, sold at ${gp(f.sell_price)}` : " (open)"}</div>`).join("")}</div>` : ""}`;
  $("#drClose").onclick = closeItem;
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
  loadChart(id);
  loadSeason(id);
  loadRelated(id, r);
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
      if (document.hidden && desktopState() === "granted" && store.get("desktop", true)) {
        fresh.slice(0, 3).forEach((x) => {
          try {
            const nt = new Notification("GE Companion", { body: x.message, tag: "geco-" + x.nid });
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
    if (!S.loaded) { S.loaded = true; showTab(S.tab in renderers ? S.tab : "flips"); }
    else if (["flips", "movers", "alch", "market"].includes(S.tab)) renderTab(S.tab, true);
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
