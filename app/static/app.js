"use strict";
const $ = (s) => document.querySelector(s);
const METRICS = {
  cpu_pct: ["CPU", "%", 1], memory_pct: ["Memory", "%", 1], disk_io_util_pct: ["Disk I/O", "%", 1],
  latency_p95_ms: ["p95 latency", " ms", 1], error_rate: ["Error rate", "%", 100],
  request_rate_rps: ["Requests", "/s", 1], network_rx_mbps: ["Network in", " MB/s", 1],
  network_tx_mbps: ["Network out", " MB/s", 1],
};
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
let D = null, cur = 0, timer = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
  return j;
}
const clock = (m) => new Date(Date.parse(D.start) + m * 60000).toISOString().slice(11, 16) + " UTC";
const ranges = (flags) => {
  const out = []; let s = null;
  flags.forEach((f, i) => { if (f && s === null) s = i; if (!f && s !== null) { out.push([s, i]); s = null; } });
  if (s !== null) out.push([s, flags.length]);
  return out;
};

function setup(c) {
  const dpr = window.devicePixelRatio || 1, r = c.getBoundingClientRect();
  c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, r.width, r.height);
  return [g, r.width, r.height];
}

function plot(c, ys, o) {
  const [g, W, H] = setup(c), n = ys.length;
  const pad = o.big ? { l: 40, r: 10, t: 10, b: 24 } : { l: 2, r: 2, t: 4, b: 2 };
  const x = (i) => pad.l + (i / (n - 1)) * (W - pad.l - pad.r);
  const y = (v) => H - pad.b - ((v - o.min) / (o.max - o.min)) * (H - pad.t - pad.b);
  const band = (rs, fill) => { g.fillStyle = fill; rs.forEach(([a, b]) => g.fillRect(x(a), pad.t, Math.max(x(b - 1) - x(a), 2), H - pad.t - pad.b)); };
  if (o.warn) band(o.warn, "rgba(11,114,133,.12)");
  band(o.fail || [], "rgba(181,67,42,.26)");
  if (o.big) {
    g.font = "12px system-ui"; g.fillStyle = css("--muted"); g.strokeStyle = css("--rule"); g.lineWidth = 1;
    [0, .25, .5, .75, 1].forEach((v) => { g.beginPath(); g.moveTo(pad.l, y(v)); g.lineTo(W - pad.r, y(v)); g.stroke(); g.textAlign = "right"; g.fillText(Math.round(v * 100) + "%", pad.l - 6, y(v) + 4); });
    g.textAlign = "center";
    for (let m = 0; m < n; m += o.step || 60) g.fillText(o.label ? o.label(m) : clock(m).slice(0, 5), x(m), H - 6);
    g.setLineDash([6, 4]); g.strokeStyle = css("--ink"); g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(pad.l, y(o.thr)); g.lineTo(W - pad.r, y(o.thr)); g.stroke(); g.setLineDash([]);
  }
  (o.extra || []).forEach((e) => {
    g.strokeStyle = e.color; g.lineWidth = 1.8; g.beginPath(); let pn = false;
    e.ys.forEach((v, i) => { if (v === null) { pn = false; return; } pn ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)); pn = true; });
    g.stroke();
  });
  g.strokeStyle = o.color; g.lineWidth = o.big ? 2.2 : 1.4; g.beginPath();
  let pen = false;
  ys.forEach((v, i) => { if (v === null) { pen = false; return; } pen ? g.lineTo(x(i), y(v)) : g.moveTo(x(i), y(v)); pen = true; });
  g.stroke();
  if (!o.nocursor && cur < n && ys[cur] !== null && ys[cur] !== undefined) {
    g.strokeStyle = css("--ink"); g.lineWidth = 1; g.beginPath(); g.moveTo(x(cur), pad.t); g.lineTo(x(cur), H - pad.b); g.stroke();
    g.fillStyle = o.color; g.beginPath(); g.arc(x(cur), y(ys[cur]), o.big ? 5 : 3, 0, 7); g.fill();
  }
}

const LV = { warn: "rgba(94,110,123,.35)", alert: "rgba(181,67,42,.5)", remediate: "#b5432a" };
function drawStrip() {
  const [g, W, H] = setup($("#strip")), n = D.minutes, x = (i) => 40 + (i / (n - 1)) * (W - 50);
  D.policy_level.forEach((l, i) => { if (LV[l]) { g.fillStyle = LV[l]; g.fillRect(x(i), 0, Math.max(x(1) - x(0), 1.5), H); } });
  g.strokeStyle = css("--ink"); g.beginPath(); g.moveTo(x(cur), 0); g.lineTo(x(cur), H); g.stroke();
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const when = (ts) => esc(ts.slice(5, 16).replace("T", " "));
function table(rows, cols, empty) {
  if (!rows.length) return `<p class="empty">${empty}</p>`;
  return "<table><thead><tr>" + cols.map((c) => `<th>${c[0]}</th>`).join("") + "</tr></thead><tbody>" +
    rows.map((r) => "<tr>" + cols.map((c) => `<td>${c[1](r)}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
}
async function loadTables() {
  try {
    const [al, au] = await Promise.all([api("/v1/alerts?limit=8"), api("/v1/audit?limit=8")]);
    $("#alerts").innerHTML = table(al, [["Time (UTC)", (r) => when(r.ts)], ["Series", (r) => esc(r.series_id)],
      ["Level", (r) => `<span class="tag ${r.level === "resolved" ? "" : "bad"}">${esc(r.level)}</span>`],
      ["Risk", (r) => Math.round(r.probability * 100) + "%"]], "No alerts yet. Send the scenario through live ingestion.");
    $("#audit").innerHTML = table(au, [["Time (UTC)", (r) => when(r.ts)], ["Action", (r) => esc(r.action) + " on " + esc(r.target || "no target")],
      ["Outcome", (r) => `<span class="tag">${esc(r.outcome)}</span>`], ["Why", (r) => esc(r.reason)]],
      "No remediation decisions yet.");
  } catch (e) { $("#alerts").textContent = e.message; }
}
async function loadPolicy() {
  const p = (await api("/v1/policy")).config;
  const mode = !p.remediation_enabled ? "Remediation is off" : p.dry_run ? "Remediation is on in dry-run mode: actions are recorded, never executed" : "Remediation is live";
  $("#polstate").textContent = `${mode}. An alert needs ${p.sustain_alert} sustained minutes above the model threshold; remediation needs ${p.sustain_remediate} minutes at ${Math.round(p.remediate_threshold * 100)}% or more, then respects a ${Math.round(p.cooldown_seconds / 60)}-minute cooldown and ${p.max_actions_per_hour} actions per hour.`;
}
let liveClock = null;
async function sendLive() {
  const btn = $("#sendlive"), msg = $("#livemsg"); btn.disabled = true;
  try {
    if (liveClock === null) liveClock = Math.floor(Date.now() / 60000) - D.minutes;
    liveClock += 2; // a short gap resets the series history between runs
    const names = Object.keys(D.metrics), N = D.minutes; let sent = 0, scored = 0;
    for (let s = 0; s < N; s += 60) {
      const samples = [];
      for (let i = s; i < Math.min(s + 60, N); i++) {
        const o = { timestamp: new Date((liveClock + i) * 60000).toISOString() };
        names.forEach((k) => { o[k] = D.metrics[k][i]; });
        samples.push(o);
      }
      msg.textContent = `Sending minutes ${s + 1} to ${s + samples.length} of ${N}…`;
      const r = await api("/v1/metrics/ingest", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ series_id: "demo-live", samples }) });
      sent += r.accepted; scored += r.scored_samples;
    }
    liveClock += N;
    msg.textContent = `Sent ${sent} samples, scored ${scored} minutes.`;
    await loadTables();
  } catch (e) { msg.textContent = "Could not send: " + e.message; } finally { btn.disabled = false; }
}

let C = null;
function drawCanary() {
  if (!C) return;
  const fail = ranges(C.candidate_unhealthy), step = C.minutes > 240 ? 60 : 30;
  plot($("#canchart"), C.candidate_probability, { min: 0, max: 1, color: css("--teal"), thr: C.threshold, big: true, fail,
    nocursor: true, step, label: (m) => m + " min", extra: [{ ys: C.baseline_probability, color: css("--muted") }] });
  const [g, W, H] = setup($("#canstrip")), n = C.minutes, x = (i) => 40 + (i / (n - 1)) * (W - 50);
  const col = { hold: "rgba(94,110,123,.35)", promote: css("--teal"), rollback: css("--rust") };
  C.checkpoints.forEach((k) => { g.fillStyle = col[k.decision]; g.fillRect(x(k.minute), 0, Math.max(x(5) - x(0) - 1, 2), H); });
}
async function runCanary(ev) {
  ev.preventDefault(); const btn = $("#canrun"); btn.disabled = true; btn.textContent = "Checking…";
  try {
    const late = $("#canlate").checked;
    C = await api("/v1/canary/simulate", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ candidate_fault: $("#canfault").value, seed: 42, minutes: late ? 300 : 180, fault_start: late ? 120 : 10 }) });
    const f = C.final, h = $("#canhead"); h.className = "canhead";
    if (f.decision === "rollback") {
      h.textContent = `Rolled back after ${f.observed_minutes} minutes of observation`; h.className = "canhead bad";
      $("#cansub").textContent = f.reasons.join(". ") + `. The rollback of ${f.rollback_container} was ${f.rollback_outcome === "dry_run" ? "recorded in dry-run mode, not executed" : f.rollback_outcome}.`;
    } else if (f.decision === "promote") {
      h.textContent = `Promoted after ${f.observed_minutes} minutes of observation`;
      const fail = C.candidate_unhealthy.indexOf(true);
      $("#cansub").textContent = f.reasons.join(". ") + "." + (fail >= 0 ? ` The release did fail later in this run, at minute ${fail}: this window was too short to see it. A longer minimum observation period would have caught it.` : "");
    } else {
      h.textContent = "Held: not enough evidence yet"; $("#cansub").textContent = f.reasons.join(". ") + ".";
    }
    drawCanary();
  } catch (e) { $("#canhead").textContent = "Could not run the canary check"; $("#cansub").textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = "Run canary check"; }
}

function render() {
  if (!D) return;
  const fail = ranges(D.is_unhealthy), warn = ranges(D.label_failure_within_h.map(Boolean));
  const shown = D.probability.map((v, i) => (D.is_unhealthy[i] ? null : v)); // no forecast once already failing
  plot($("#hero"), shown, { min: 0, max: 1, color: css("--teal"), thr: D.threshold, big: true, fail, warn });
  $("#hero").setAttribute("aria-label", `Failure probability from 0 to 100 percent over ${D.minutes} minutes`);
  for (const [k, [, , mult]] of Object.entries(METRICS)) {
    const ys = D.metrics[k].map((v) => v * mult), lo = Math.min(...ys), hi = Math.max(...ys);
    plot($("#m-" + k), ys, { min: Math.min(lo, 0), max: hi * 1.08 || 1, color: css("--muted"), fail });
    $("#v-" + k).textContent = ys[cur].toFixed(mult === 100 ? 2 : 1) + METRICS[k][1];
  }
  drawStrip();
  const p = D.probability[cur], lvl = D.policy_level[cur];
  $("#readout").innerHTML = p === null
    ? `${clock(cur)}: collecting history`
    : D.is_unhealthy[cur] ? `${clock(cur)}: service <b>failing</b>; forecast applies only while healthy`
    : `${clock(cur)}: <b>${(p * 100).toFixed(1)}%</b> failure risk, policy level <b>${lvl}</b>; service healthy`;
}

function policyLine(ps, onset) {
  const early = ps.first_alert_minute !== null
    ? `the first alert came ${ps.lead_time_minutes} minutes ahead` : "no alert came ahead of the failure";
  const rem = ps.would_remediate_lead_minutes !== null
    ? `, and a dry-run restart would have been planned ${ps.would_remediate_lead_minutes} minutes before it` : "";
  if (onset === null) return `With the alert policy there were ${ps.alert_minutes} alert minutes.`;
  return `With the alert policy (${ps.sustain_alert} sustained minutes), ${early}${rem}. ${ps.false_alert_minutes} alert minutes fell outside the ${D.horizon_minutes}-minute warning window.`;
}
function headline(s, fault) {
  const h = $("#headline"), sub = $("#sub");
  h.className = "";
  const fa = s.failure_start_minute, other = s.false_alert_minutes;
  if (fault === "none" || fa === null) {
    h.textContent = s.alert_minutes ? `${s.alert_minutes} alert minutes in a run with no failure` : "No alerts in a run with no failure";
    sub.textContent = fault === "none" ? "Healthy service with occasional harmless spikes." : "The fault never reached a failing state in this run.";
  } else if (s.lead_time_minutes !== null) {
    h.textContent = `Warned ${s.lead_time_minutes} minutes before the service failed`;
    sub.textContent = `The service failed at ${clock(fa)}; the first alert fired at ${clock(s.first_alert_minute)}. ` +
      (other ? `${other} other minutes raised alerts with no failure ahead.` : "No other alerts fired.");
  } else {
    h.textContent = "The failure was not predicted"; h.className = "bad";
    sub.textContent = `The service failed at ${clock(fa)} without an alert in the ${D.horizon_minutes} minutes before it.`;
  }
}

async function run(ev) {
  if (ev) ev.preventDefault();
  stop(); const btn = $("#run"); btn.disabled = true; btn.textContent = "Running…";
  try {
    const fault = $("#fault").value;
    D = await api("/v1/simulate", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ fault_type: fault, ramp_minutes: +$("#ramp").value, seed: +$("#seed").value, spikes: $("#spikes").checked }) });
    const fa = D.summary.failure_start_minute;
    cur = fa !== null ? Math.max(fa - 45, 0) : Math.floor(D.minutes / 2);
    const sl = $("#slider"); sl.max = D.minutes - 1; sl.value = cur; sl.disabled = false; $("#play").disabled = false;
    headline(D.summary, fault); $("#sub2").textContent = policyLine(D.policy_summary, D.summary.failure_start_minute);
    $("#sendlive").disabled = false; render();
  } catch (e) {
    $("#headline").textContent = "Could not run the scenario"; $("#headline").className = "bad";
    $("#sub").textContent = e.message;
  } finally { btn.disabled = false; btn.textContent = "Run scenario"; }
}

function stop() { clearInterval(timer); timer = null; $("#play").textContent = "Play"; }
function play() {
  if (timer) return stop();
  if (cur >= D.minutes - 1) cur = 0;
  $("#play").textContent = "Pause";
  timer = setInterval(() => { cur++; $("#slider").value = cur; render(); if (cur >= D.minutes - 1) stop(); }, 40);
}

async function init() {
  $("#grid").innerHTML = Object.entries(METRICS).map(([k, [n]]) =>
    `<div class="cell"><header><span>${n}</span><b id="v-${k}"></b></header><canvas id="m-${k}" aria-hidden="true"></canvas></div>`).join("");
  $("#controls").addEventListener("submit", run);
  $("#play").addEventListener("click", play);
  $("#slider").addEventListener("input", (e) => { stop(); cur = +e.target.value; render(); });
  $("#canform").addEventListener("submit", runCanary);
  window.addEventListener("resize", () => { render(); drawCanary(); });
  const chip = $("#model");
  try {
    const m = await api("/v1/model");
    chip.className = "chip ok";
    chip.textContent = `Model ${m.version}, trained on ${m.data_source} data, alerts at ${(m.threshold * 100).toFixed(0)}% risk`;
    $("#sendlive").addEventListener("click", sendLive);
    loadPolicy().catch(() => {}); loadTables(); run();
    $("#canform").requestSubmit();
  } catch (e) {
    chip.className = "chip bad"; chip.textContent = "No model loaded";
    $("#headline").textContent = "Train a model first";
    $("#sub").textContent = "Run: python scripts/train.py --promote, then restart the API.";
    $("#run").disabled = true;
  }
}
init();
