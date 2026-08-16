"""Service management endpoints."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from auth import require_admin
from services import ServiceManager

router = APIRouter(prefix="/services", tags=["services"])

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>🤖 G1 Robot Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; padding: 24px; }
  h1 { margin-bottom: 8px; }
  .subtitle { color: #888; margin-bottom: 20px; font-size: 14px; }
  .dry-run-badge { background: #ff9800; color: #000; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-left: 10px; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 24px; background: #16213e; border-radius: 8px; overflow: hidden; }
  th, td { padding: 14px 18px; text-align: left; border-bottom: 1px solid #1a1a2e; }
  th { background: #0f3460; color: #aaa; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  tr:last-child td { border-bottom: none; }
  tr:hover { background: #1a2744; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }
  .dot.on  { background: #4caf50; box-shadow: 0 0 8px #4caf50; }
  .dot.off { background: #f44336; }
  button { padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; color: #fff; font-weight: 500; transition: all 0.2s; }
  .btn-start { background: #2e7d32; }
  .btn-start:hover { background: #388e3c; }
  .btn-stop  { background: #c62828; }
  .btn-stop:hover { background: #d32f2f; }
  .btn-restart { background: #1565c0; margin-left: 8px; }
  .btn-restart:hover { background: #1976d2; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .log-box { background: #0d1117; padding: 14px; border-radius: 8px; margin-bottom: 20px;
             max-height: 250px; overflow-y: auto; font-family: 'SF Mono', Monaco, monospace; font-size: 12px;
             white-space: pre-wrap; color: #8b949e; border: 1px solid #30363d; }
  .status-text { font-size: 13px; }
  .status-text.on { color: #4caf50; }
  .status-text.off { color: #f44336; }
  .uptime { color: #888; font-size: 12px; }
  .actions { display: flex; gap: 8px; }
  .refresh-info { color: #666; font-size: 12px; margin-top: 16px; }
  .state-section { margin-bottom: 24px; }
  .state-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
  .state-card { background: #16213e; border-radius: 8px; padding: 16px; }
  .state-card h3 { font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .state-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #1a1a2e; }
  .state-row:last-child { border-bottom: none; }
  .state-label { color: #888; font-size: 13px; }
  .state-value { font-family: 'SF Mono', Monaco, monospace; font-size: 13px; color: #4caf50; }
  .state-value.disconnected { color: #f44336; }
  .control-section { margin-bottom: 24px; }
  .control-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .control-card { background: #16213e; border-radius: 8px; padding: 16px; }
  .control-card h3 { font-size: 14px; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }
  .control-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #1a1a2e; }
  .control-row:last-child { border-bottom: none; }
  .control-label { color: #888; font-size: 13px; }
  .btn-home { background: #9c27b0; }
  .btn-home:hover { background: #ab47bc; }
  .btn-action { padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; color: #fff; font-weight: 500; transition: all 0.2s; width: 100%; margin-top: 8px; }
  .btn-action:disabled { opacity: .5; cursor: not-allowed; }
  .activity-badge { font-size: 12px; padding: 4px 10px; border-radius: 4px; font-weight: 500; }
  .activity-badge.idle { background: #1b5e20; color: #4caf50; }
  .activity-badge.executing { background: #0d47a1; color: #42a5f5; animation: pulse 1.5s infinite; }
  .activity-badge.resetting { background: #4a148c; color: #ce93d8; animation: pulse 1s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
</style></head><body>
<h1>🤖 G1 Robot Dashboard<span id="dry-run-badge" class="dry-run-badge" style="display:none">DRY-RUN</span></h1>
<p class="subtitle">G1 Robot Agent Server — Service Manager
  <span style="margin-left: 16px;">
    <a href="/docs/guide/html" target="_blank" style="color: #64b5f6; text-decoration: none; margin-right: 12px;">Getting Started ↗</a>
    <a href="/docs" target="_blank" style="color: #64b5f6; text-decoration: none; margin-right: 12px;">API Docs ↗</a>
    <a href="/code/sdk/html" target="_blank" style="color: #64b5f6; text-decoration: none;">SDK Reference ↗</a>
  </span>
</p>

<!-- Row 1: Robot State -->
<div class="state-section">
  <div class="state-grid">
    <div class="state-card">
      <h3>Left Arm Joints (rad)</h3>
      <div class="state-row"><span class="state-label">Shoulder Pitch</span><span class="state-value" id="lj0">—</span></div>
      <div class="state-row"><span class="state-label">Shoulder Roll</span><span class="state-value" id="lj1">—</span></div>
      <div class="state-row"><span class="state-label">Shoulder Yaw</span><span class="state-value" id="lj2">—</span></div>
      <div class="state-row"><span class="state-label">Elbow Pitch</span><span class="state-value" id="lj3">—</span></div>
    </div>
    <div class="state-card">
      <h3>Right Arm Joints (rad)</h3>
      <div class="state-row"><span class="state-label">Shoulder Pitch</span><span class="state-value" id="rj0">—</span></div>
      <div class="state-row"><span class="state-label">Shoulder Roll</span><span class="state-value" id="rj1">—</span></div>
      <div class="state-row"><span class="state-label">Shoulder Yaw</span><span class="state-value" id="rj2">—</span></div>
      <div class="state-row"><span class="state-label">Elbow Pitch</span><span class="state-value" id="rj3">—</span></div>
    </div>
    <div class="state-card">
      <h3>Body State</h3>
      <div class="state-row"><span class="state-label">Position X</span><span class="state-value" id="body-x">—</span></div>
      <div class="state-row"><span class="state-label">Position Y</span><span class="state-value" id="body-y">—</span></div>
      <div class="state-row"><span class="state-label">Height</span><span class="state-value" id="body-z">—</span></div>
      <div class="state-row"><span class="state-label">Velocity X</span><span class="state-value" id="body-vx">—</span></div>
      <div class="state-row"><span class="state-label">Velocity Yaw</span><span class="state-value" id="body-vyaw">—</span></div>
    </div>
    <div class="state-card">
      <h3>Cameras</h3>
      <div class="state-row"><span class="state-label">D455 (head)</span><span class="state-value" id="cam-d455">—</span></div>
      <div class="state-row"><span class="state-label">D435i (chest)</span><span class="state-value" id="cam-d435i">—</span></div>
    </div>
    <div class="state-card">
      <h3>Lease</h3>
      <div class="state-row"><span class="state-label">Holder</span><span class="state-value" id="lease-holder" style="color: #888; font-style: italic;">(none)</span></div>
      <div class="state-row"><span class="state-label">Remaining</span><span class="state-value" id="lease-remaining">—</span></div>
      <div class="state-row"><span class="state-label">Queue</span><span class="state-value" id="lease-queue-len">0</span>&nbsp;waiting</div>
      <div class="state-row"><span class="state-label">Activity</span><span id="robot-activity" class="activity-badge idle" style="font-size: 11px; padding: 2px 8px;">Idle</span></div>
      <div style="margin-top: 10px; display: flex; gap: 6px; flex-wrap: wrap;">
        <button id="btn-pause-queue" style="background: #ff9800; color: #000; font-size: 11px; padding: 4px 10px; border: none; border-radius: 4px; cursor: pointer;" onclick="togglePauseQueue(this)">Pause Queue</button>
        <button id="btn-clear-queue" style="background: #b33; color:#fff; font-size: 11px; padding: 4px 10px; border: none; border-radius: 4px; cursor: pointer; display:none;" onclick="clearQueue(this)">Stop &amp; Reset</button>
      </div>
    </div>
  </div>
</div>

<!-- Row 2: Controls -->
<div class="control-section">
  <div class="control-grid">
    <div class="control-card">
      <h3>Navigation</h3>
      <div class="control-row">
        <span class="control-label">Target X (m)</span>
        <input type="number" id="nav-x" value="1.0" step="0.1" style="width: 80px; background: #0d1117; color: #eee; border: 1px solid #30363d; border-radius: 4px; padding: 4px;">
      </div>
      <div class="control-row">
        <span class="control-label">Target Y (m)</span>
        <input type="number" id="nav-y" value="0.0" step="0.1" style="width: 80px; background: #0d1117; color: #eee; border: 1px solid #30363d; border-radius: 4px; padding: 4px;">
      </div>
      <div class="control-row">
        <span class="control-label">Status</span>
        <span id="nav-status" class="state-value">Idle</span>
      </div>
      <button id="btn-nav" class="btn-action btn-primary" onclick="runNavDemo(this)">
        Navigate
      </button>
    </div>
    <div class="control-card">
      <h3>Whole-Body Control</h3>
      <div class="control-row">
        <span class="control-label">Body Height</span>
        <span id="body-height" class="state-value">0.65 m</span>
      </div>
      <div class="control-row">
        <span class="control-label">Motion State</span>
        <span id="motion-state" class="state-value">Standing</span>
      </div>
      <button id="btn-stand" class="btn-action" style="background: #2e7d32; margin-bottom: 8px;" onclick="sendCommand('stand')">Stand</button>
      <button id="btn-sit" class="btn-action" style="background: #1565c0; margin-bottom: 8px;" onclick="sendCommand('sit')">Sit</button>
      <button id="btn-walk" class="btn-action" style="background: #9c27b0;" onclick="sendCommand('walk')">Walk Forward</button>
    </div>
    <div class="control-card">
      <h3>Grasp Demo</h3>
      <div class="control-row">
        <span class="control-label">Dual-arm grasp sequence</span>
      </div>
      <div class="control-row">
        <span class="control-label">Status</span>
        <span id="grasp-status" class="state-value">Idle</span>
      </div>
      <button id="btn-grasp-demo" class="btn-action btn-primary" onclick="runGraspDemo(this)">
        Run Grasp Demo
      </button>
    </div>
    <div class="control-card">
      <h3>Spatial Memory</h3>
      <div class="control-row">
        <span class="control-label">Type</span>
        <select id="mem-type" style="background: #0d1117; color: #eee; border: 1px solid #30363d; border-radius: 4px; padding: 4px;">
          <option value="object">Object</option>
          <option value="place">Place</option>
        </select>
      </div>
      <div class="control-row">
        <span class="control-label">Name</span>
        <input type="text" id="mem-name" placeholder="e.g., red_cup" style="width: 120px; background: #0d1117; color: #eee; border: 1px solid #30363d; border-radius: 4px; padding: 4px;">
      </div>
      <div class="control-row">
        <span class="control-label">Status</span>
        <span id="mem-status" class="state-value">Idle</span>
      </div>
      <button id="btn-mem-save" class="btn-action" style="background: #2e7d32; margin-bottom: 8px;" onclick="runMemorySave(this)">Save Current Pose</button>
      <button id="btn-mem-query" class="btn-action" style="background: #1565c0; margin-bottom: 8px;" onclick="runMemoryQuery(this)">Query</button>
      <div id="mem-result" style="margin-top: 10px; font-size: 12px; color: #8b949e; max-height: 100px; overflow-y: auto;"></div>
    </div>
    <div class="control-card">
      <h3>Face Recognition</h3>
      <div class="control-row">
        <span class="control-label">Enroll Name</span>
        <input type="text" id="face-name" placeholder="Enter name" style="width: 120px; background: #0d1117; color: #eee; border: 1px solid #30363d; border-radius: 4px; padding: 4px;">
      </div>
      <div class="control-row">
        <span class="control-label">Status</span>
        <span id="face-status" class="state-value">Idle</span>
      </div>
      <button id="btn-face-enroll" class="btn-action" style="background: #2e7d32; margin-bottom: 8px;" onclick="runFaceEnroll(this)">Enroll Current Frame</button>
      <button id="btn-face-recognize" class="btn-action" style="background: #1565c0;" onclick="runFaceRecognize(this)">Recognize</button>
      <div id="face-result" style="margin-top: 10px; font-size: 12px; color: #8b949e;"></div>
    </div>
    <div class="control-card">
      <h3>Code Execution <span id="code-status-badge" style="font-size: 11px; padding: 2px 8px; border-radius: 4px; display: none;"></span></h3>
      <div id="code-execution-grid" style="max-height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; white-space: pre-wrap; color: #8b949e;"></div>
    </div>
  </div>
</div>

<!-- Row 3: Logs -->
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px;">
  <div class="control-card">
    <h3>Server Logs</h3>
    <div id="server-logs" class="log-box" style="height: 300px; font-size: 11px;"></div>
  </div>
  <div class="control-card">
    <h3>Service Logs</h3>
    <div id="service-logs-combined" class="log-box" style="height: 300px; font-size: 11px;"></div>
  </div>
</div>

<!-- Row 4: Services -->
<table>
  <thead><tr><th>Service</th><th>Status</th><th>PID</th><th>Uptime</th><th>Actions</th></tr></thead>
  <tbody id="tbl"><tr><td colspan="5" style="text-align:center;color:#666">Loading...</td></tr></tbody>
</table>

<!-- Port Reference -->
<table style="margin-top: 16px;">
  <thead><tr><th>Port</th><th>Service</th><th>Protocol</th><th>Bind</th></tr></thead>
  <tbody>
    <tr><td style="font-family: monospace;">8888</td><td>G1 Agent Server</td><td>HTTP / WebSocket</td><td>0.0.0.0</td></tr>
    <tr><td style="font-family: monospace;">enp4s0</td><td>DDS ChannelFactory</td><td>UDP</td><td>192.168.123.0/24</td></tr>
  </tbody>
</table>
<p class="refresh-info">Auto-refreshes every 2 seconds</p>
<script>
let serviceManagerEnabled = true;  // Replaced by server when disabled
let serviceKeys = [];
let queuePaused = false;

function fmt(s) {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return h + "h " + m + "m";
  return m + "m " + sec + "s";
}

async function act(method, url, btn) {
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(url, { method });
    const data = await res.json();
    if (!res.ok) alert("Error: " + (data.detail || res.status));
    return data;
  } catch (e) {
    alert("Request failed: " + e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function poll() {
  if (!serviceManagerEnabled) {
    document.getElementById("tbl").innerHTML =
      '<tr><td colspan="5" style="text-align:center;color:#666">Service manager not enabled</td></tr>';
    return;
  }
  try {
    const data = await (await fetch("/services")).json();
    serviceKeys = Object.keys(data);
    const rows = serviceKeys.map(name => {
      const s = data[name];
      const on = s.running;
      const dot = `<span class="dot ${on ? "on" : "off"}"></span>`;
      const status = `${dot}<span class="status-text ${on ? "on" : "off"}">${on ? "Running" : "Stopped"}</span>`;
      const pid = s.pid || "—";
      const uptime = s.uptime_s != null ? fmt(s.uptime_s) : "—";
      const btns = on
        ? `<div class="actions">
             <button class="btn-stop" onclick="act('POST','/services/${name}/stop',this).then(poll)">Stop</button>
             <button class="btn-restart" onclick="act('POST','/services/${name}/restart',this).then(poll)">Restart</button>
           </div>`
        : `<button class="btn-start" onclick="act('POST','/services/${name}/start',this).then(poll)">Start</button>`;
      return `<tr><td>${name}</td><td>${status}</td><td>${pid}</td><td class="uptime">${uptime}</td><td>${btns}</td></tr>`;
    }).join("");
    document.getElementById("tbl").innerHTML = rows || '<tr><td colspan="5" style="text-align:center;color:#666">No services configured</td></tr>';
  } catch (e) {
    document.getElementById("tbl").innerHTML =
      `<tr><td colspan="5" style="text-align:center;color:#f44">Error: ${e}</td></tr>`;
  }
}

async function pollState() {
  try {
    const data = await (await fetch("/state")).json();
    // Left arm joints (4 DOF)
    const leftJoints = (data.arm && data.arm.left_joint_positions) || [];
    for (let i = 0; i < 4; i++) {
      const el = document.getElementById("lj" + i);
      if (el) el.textContent = leftJoints[i] != null ? leftJoints[i].toFixed(3) : "—";
    }
    // Right arm joints (4 DOF)
    const rightJoints = (data.arm && data.arm.right_joint_positions) || [];
    for (let i = 0; i < 4; i++) {
      const el = document.getElementById("rj" + i);
      if (el) el.textContent = rightJoints[i] != null ? rightJoints[i].toFixed(3) : "—";
    }
    // Body state
    const body = data.body || {};
    const pos = body.position || [];
    const vel = body.velocity || [];
    if (document.getElementById("body-x")) document.getElementById("body-x").textContent = pos[0] != null ? pos[0].toFixed(3) : "—";
    if (document.getElementById("body-y")) document.getElementById("body-y").textContent = pos[1] != null ? pos[1].toFixed(3) : "—";
    if (document.getElementById("body-z")) document.getElementById("body-z").textContent = pos[2] != null ? pos[2].toFixed(3) : "—";
    if (document.getElementById("body-vx")) document.getElementById("body-vx").textContent = vel[0] != null ? vel[0].toFixed(2) : "—";
    if (document.getElementById("body-vyaw")) document.getElementById("body-vyaw").textContent = vel[5] != null ? vel[5].toFixed(2) : "—";
    // Cameras
    const cams = data.cameras || {};
    ["d455","d435i"].forEach(name => {
      const el = document.getElementById("cam-" + name);
      if (el) { el.textContent = cams[name] ? "✓" : "—"; el.style.color = cams[name] ? "#4caf50" : "#666"; }
    });
  } catch (e) { console.error("State poll error:", e); }
}

async function pollServerLogs() {
  try {
    const data = await (await fetch("/logs")).json();
    const el = document.getElementById("server-logs");
    if (!el) return;
    const logs = (data.logs || []).map(e => {
      const lvl = e.level || "INFO";
      const color = lvl === "ERROR" ? "#f44336" : lvl === "WARNING" ? "#ff9800" : "#8b949e";
      return `<span style="color:${color}">[${lvl}] ${e.message || e}</span>`;
    }).join("\n");
    el.innerHTML = logs;
    el.scrollTop = el.scrollHeight;
  } catch (e) { }
}

async function pollServiceLogs() {
  if (!serviceManagerEnabled || serviceKeys.length === 0) return;
  try {
    const allLogs = [];
    for (const name of serviceKeys) {
      const data = await (await fetch(`/services/${name}/logs?lines=20`)).json();
      (data.lines || []).forEach(l => allLogs.push(`[${name}] ${l}`));
    }
    const el = document.getElementById("service-logs-combined");
    if (el) { el.textContent = allLogs.slice(-100).join("\n"); el.scrollTop = el.scrollHeight; }
  } catch (e) { }
}

let lastCodeId = null;
async function pollCodeLogs() {
  try {
    const status = await (await fetch("/code/status")).json();
    const badge = document.getElementById("code-status-badge");
    const grid  = document.getElementById("code-execution-grid");
    if (!badge || !grid) return;
    const isRunning = status.is_running;
    badge.style.display = "inline";
    badge.textContent = isRunning ? "Running" : "Idle";
    badge.style.background = isRunning ? "#0d47a1" : "#1b5e20";
    badge.style.color = isRunning ? "#42a5f5" : "#4caf50";
    const actEl = document.getElementById("robot-activity");
    if (actEl) {
      actEl.textContent = isRunning ? "Executing Code" : "Idle";
      actEl.className = "activity-badge " + (isRunning ? "executing" : "idle");
    }
    if (status.execution_id) lastCodeId = status.execution_id;
    if (lastCodeId) {
      const stdout = status.stdout || "";
      const stderr = status.stderr || "";
      grid.innerHTML = stdout + (stderr ? '<span style="color:#f44">' + stderr + '</span>' : '');
      grid.scrollTop = grid.scrollHeight;
    }
  } catch (e) { }
}

async function pollLease() {
  try {
    const data = await (await fetch("/lease/status")).json();
    const holderEl = document.getElementById("lease-holder");
    if (holderEl) {
      holderEl.textContent = data.holder || "(none)";
      holderEl.style.fontStyle = data.holder ? "normal" : "italic";
      holderEl.style.color = data.holder ? "#4caf50" : "#888";
    }
    const remEl = document.getElementById("lease-remaining");
    if (remEl) {
      if (data.remaining_s != null && data.holder) {
        const m = Math.floor(data.remaining_s / 60);
        const s = Math.floor(data.remaining_s % 60);
        remEl.textContent = m > 0 ? m + "m " + s + "s" : s + "s";
      } else { remEl.textContent = "—"; }
    }
    const queueLen = data.queue_length || 0;
    const qEl = document.getElementById("lease-queue-len");
    if (qEl) qEl.textContent = queueLen;
    const clearBtn = document.getElementById("btn-clear-queue");
    if (clearBtn) clearBtn.style.display = (queueLen > 0 || data.holder) ? "" : "none";
    queuePaused = !!data.paused;
    const pauseBtn = document.getElementById("btn-pause-queue");
    if (pauseBtn) {
      pauseBtn.textContent = queuePaused ? "Resume Queue" : "Pause Queue";
      pauseBtn.style.background = queuePaused ? "#4caf50" : "#ff9800";
      pauseBtn.style.color = queuePaused ? "#fff" : "#000";
    }
  } catch (e) { }
}

async function runNavDemo(btn) {
  if (!confirm("Navigate to target?")) return;
  btn.disabled = true;
  const statusEl = document.getElementById("nav-status");
  const x = parseFloat(document.getElementById("nav-x").value) || 0;
  const y = parseFloat(document.getElementById("nav-y").value) || 0;
  let leaseId = null;
  try {
    statusEl.textContent = "Acquiring lease...";
    const acqRes = await fetch("/lease/acquire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ holder: "dashboard-nav" }),
    });
    const acqData = await acqRes.json();
    if (!acqRes.ok) {
      statusEl.textContent = "Lease error: " + (acqData.detail || acqRes.status);
      return;
    }
    leaseId = acqData.lease_id;
    if (!leaseId) {
      statusEl.textContent = "Robot busy (lease held by: " + (acqData.holder || "another agent") + ")";
      return;
    }
    statusEl.textContent = "Navigating...";
    const execRes = await fetch("/code/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Lease-Id": leaseId },
      body: JSON.stringify({ code: `from geometry_msgs.msg import PoseStamped; from robot_sdk.navigation_sdk import Nav2Anywhere; n=Nav2Anywhere(); p=PoseStamped(); p.header.frame_id="map"; p.pose.position.x=float(${x}); p.pose.position.y=float(${y}); p.pose.orientation.w=1.0; n.nav_to_pose(p); n.wait_until_reached(timeout_sec=120)`, timeout: 120 }),
    });
    const execData = await execRes.json();
    if (!execRes.ok) {
      statusEl.textContent = "Execute error: " + (execData.detail || execRes.status);
      return;
    }
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const st = await (await fetch("/code/status")).json();
      if (!st.is_running) {
        statusEl.textContent = st.exit_code === 0 ? "Done ✓" : ("Failed (exit " + st.exit_code + ")");
        break;
      }
    }
  } catch (e) {
    statusEl.textContent = "Error: " + e;
  } finally {
    if (leaseId) {
      try { await fetch("/lease/release", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lease_id: leaseId }) }); } catch (_) {}
    }
    btn.disabled = false;
    setTimeout(() => { statusEl.textContent = "Idle"; }, 4000);
  }
}

async function sendCommand(cmd) {
  const btn = document.getElementById("btn-" + cmd);
  if (btn) btn.disabled = true;
  try {
    const acqRes = await fetch("/lease/acquire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ holder: "dashboard-" + cmd }),
    });
    const acqData = await acqRes.json();
    if (!acqRes.ok || !acqData.lease_id) return;
    const leaseId = acqData.lease_id;
    let code = "";
    if (cmd === "stand") code = "env.stand()";
    else if (cmd === "sit") code = "env.sit()";
    else if (cmd === "walk") code = "env.walk(0.3, 0, 0); import time; time.sleep(3); env.stop_movement()";
    const execRes = await fetch("/code/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Lease-Id": leaseId },
      body: JSON.stringify({ code: code, timeout: 30 }),
    });
    if (execRes.ok) {
      // /code/execute is asynchronous. Keep ownership until the accepted
      // execution has actually finalized; releasing immediately would request
      // cancellation of the bound task under the lease lifecycle contract.
      for (let i = 0; i < 35; i++) {
        const st = await (await fetch("/code/status")).json();
        if (!st.is_running) break;
        await new Promise(r => setTimeout(r, 1000));
      }
    }
    await fetch("/lease/release", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lease_id: leaseId }) });
  } catch (e) { console.error(e); }
  finally { if (btn) btn.disabled = false; }
}

async function runGraspDemo(btn) {
  if (!confirm("Run dual-arm grasp demo?")) return;
  btn.disabled = true;
  const statusEl = document.getElementById("grasp-status");
  let leaseId = null;
  try {
    statusEl.textContent = "Acquiring lease...";
    const acqRes = await fetch("/lease/acquire", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ holder: "dashboard-grasp" }),
    });
    const acqData = await acqRes.json();
    if (!acqRes.ok) {
      statusEl.textContent = "Lease error: " + (acqData.detail || acqRes.status);
      return;
    }
    leaseId = acqData.lease_id;
    if (!leaseId) {
      statusEl.textContent = "Robot busy (lease held by: " + (acqData.holder || "another agent") + ")";
      return;
    }
    statusEl.textContent = "Running grasp...";
    const execRes = await fetch("/code/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Lease-Id": leaseId },
      body: JSON.stringify({ code: "from robot_sdk import grasp_target; grasp_target(right_pos=[0.471, -0.0074, 0.022], left_pos=[-0.003, 0.212, -0.004])", timeout: 60 }),
    });
    const execData = await execRes.json();
    if (!execRes.ok) {
      statusEl.textContent = "Execute error: " + (execData.detail || execRes.status);
      return;
    }
    statusEl.textContent = "Running...";
    for (let i = 0; i < 60; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const st = await (await fetch("/code/status")).json();
      if (!st.is_running) {
        statusEl.textContent = st.exit_code === 0 ? "Done ✓" : ("Failed (exit " + st.exit_code + ")");
        break;
      }
    }
  } catch (e) {
    statusEl.textContent = "Error: " + e;
  } finally {
    if (leaseId) {
      try { await fetch("/lease/release", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lease_id: leaseId }) }); } catch (_) {}
    }
    btn.disabled = false;
    setTimeout(() => { statusEl.textContent = "Idle"; }, 4000);
  }
}

async function runMemorySave(btn) {
  const type = document.getElementById("mem-type").value;
  const name = document.getElementById("mem-name").value.trim();
  if (!name) { alert("Please enter a name"); return; }
  btn.disabled = true;
  const statusEl = document.getElementById("mem-status");
  const resultEl = document.getElementById("mem-result");
  try {
    statusEl.textContent = "Saving...";
    const stateRes = await fetch("/state");
    const state = await stateRes.json();
    const body = state.body || {};
    const pos = body.position || [0, 0, 0];
    const payload = {
      robot_id: "g1_dashboard",
      robot_type: "humanoid",
      place_pose: { x: pos[0], y: pos[1], z: pos[2], yaw: 0 },
      alias: [],
      note: "Saved from dashboard",
    };
    if (type === "object") {
      payload.object_name = name;
      payload.object_pose = { x: pos[0] + 0.5, y: pos[1], z: 0.8 };
      payload.robot_pose = { x: pos[0], y: pos[1], z: pos[2] };
      payload.detect_confidence = 0.9;
    } else {
      payload.place_name = name;
    }
    const res = await fetch(`/memory/${type}/upsert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      statusEl.textContent = "Saved ✓";
      resultEl.textContent = `ID: ${data.id}`;
    } else {
      statusEl.textContent = "Failed";
      resultEl.textContent = data.detail || "Error";
    }
  } catch (e) {
    statusEl.textContent = "Error";
    resultEl.textContent = String(e);
  } finally {
    btn.disabled = false;
    setTimeout(() => { statusEl.textContent = "Idle"; }, 3000);
  }
}

async function runMemoryQuery(btn) {
  const type = document.getElementById("mem-type").value;
  const name = document.getElementById("mem-name").value.trim();
  if (!name) { alert("Please enter a name"); return; }
  btn.disabled = true;
  const statusEl = document.getElementById("mem-status");
  const resultEl = document.getElementById("mem-result");
  try {
    statusEl.textContent = "Querying...";
    const res = await fetch(`/memory/${type}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, n_results: 3 }),
    });
    const data = await res.json();
    if (res.ok && data.results && data.results.length > 0) {
      statusEl.textContent = "Found ✓";
      const r = data.results[0];
      const pose = r.pose || r.target_pose || {};
      resultEl.textContent = `Name: ${r.name}\nPosition: (${pose.x?.toFixed(2)}, ${pose.y?.toFixed(2)})`;
    } else {
      statusEl.textContent = "No match";
      resultEl.textContent = "Not found";
    }
  } catch (e) {
    statusEl.textContent = "Error";
    resultEl.textContent = String(e);
  } finally {
    btn.disabled = false;
    setTimeout(() => { statusEl.textContent = "Idle"; }, 3000);
  }
}

async function runFaceEnroll(btn) {
  const name = document.getElementById("face-name").value.trim();
  if (!name) { alert("Please enter a name"); return; }
  btn.disabled = true;
  const statusEl = document.getElementById("face-status");
  const resultEl = document.getElementById("face-result");
  try {
    statusEl.textContent = "Enrolling...";
    const res = await fetch("/face/enroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, images: ["current_frame"] }),
    });
    const data = await res.json();
    if (res.ok) {
      statusEl.textContent = "Done ✓";
      resultEl.textContent = `Enrolled: ${data.name}`;
    } else {
      statusEl.textContent = "Failed";
      resultEl.textContent = data.detail || "Error";
    }
  } catch (e) {
    statusEl.textContent = "Error";
    resultEl.textContent = String(e);
  } finally {
    btn.disabled = false;
    setTimeout(() => { statusEl.textContent = "Idle"; }, 3000);
  }
}

async function runFaceRecognize(btn) {
  btn.disabled = true;
  const statusEl = document.getElementById("face-status");
  const resultEl = document.getElementById("face-result");
  try {
    statusEl.textContent = "Recognizing...";
    // Use /code/execute with pre-created face instance
    const res = await fetch("/code/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: `face.start()\nresult = face.recognize_current_frame()\nmatches = result.get('results', [])\nif matches:\n    best = matches[0]\n    print(f"Recognized: {best['name']} ({best['match_score']:.1%})")\nelse:\n    print("No face recognized")`,
        timeout: 30
      })
    });
    const data = await res.json();
    if (res.ok && data.status === "completed" && data.stdout) {
      const output = data.stdout.trim();
      if (output.includes("Recognized:")) {
        statusEl.textContent = "Found ✓";
        resultEl.textContent = output;
      } else {
        statusEl.textContent = "No match";
        resultEl.textContent = output;
      }
    } else {
      statusEl.textContent = "Error";
      resultEl.textContent = data.stderr || "Failed to recognize";
    }
  } catch (e) {
    statusEl.textContent = "Error";
    resultEl.textContent = String(e);
  } finally {
    btn.disabled = false;
    setTimeout(() => { statusEl.textContent = "Idle"; }, 3000);
  }
}

async function togglePauseQueue(btn) {
  const action = queuePaused ? "resume" : "pause";
  await act("POST", "/lease/" + action, btn);
  await pollLease();
}

async function clearQueue(btn) {
  if (!confirm("Stop current execution and reset lease?")) return;
  await act("POST", "/code/stop", btn);
  await act("POST", "/lease/release", btn);
  await pollLease();
}

poll();
pollState();
pollServerLogs();
pollServiceLogs();
pollCodeLogs();
pollLease();
setInterval(poll, 3000);
setInterval(pollState, 500);
setInterval(pollServerLogs, 2000);
setInterval(pollServiceLogs, 3000);
setInterval(pollCodeLogs, 1000);
setInterval(pollLease, 1000);
</script></body></html>"""


def create_router(service_mgr: ServiceManager | None, arm_monitor=None):
    """Create the service routes with injected dependencies.

    Args:
        service_mgr: ServiceManager instance, or None if service management is disabled.
                     When None, only the dashboard route is available (without service controls).
        arm_monitor: Ignored (kept for API compatibility).
    """
    service_manager_enabled = service_mgr is not None

    @router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False,
                dependencies=[Depends(require_admin)])
    async def dashboard(request: Request):
        """Web dashboard for service management."""
        # Inject the service_manager_enabled flag into the HTML
        html = DASHBOARD_HTML.replace(
            "let serviceManagerEnabled = true;",
            f"let serviceManagerEnabled = {'true' if service_manager_enabled else 'false'};"
        )
        # Inject API key into JS so fetch() calls include it
        api_key = request.query_params.get("api_key", "")
        auth_snippet = f"""<script>
var __apiKey = "{api_key}";
(function() {{
  var _origFetch = window.fetch;
  window.fetch = function(url, opts) {{
    if (__apiKey && typeof url === 'string' && url.startsWith('/')) {{
      opts = opts || {{}};
      opts.headers = opts.headers || {{}};
      if (opts.headers instanceof Headers) {{
        opts.headers.set('X-API-Key', __apiKey);
      }} else {{
        opts.headers['X-API-Key'] = __apiKey;
      }}
    }}
    return _origFetch.call(this, url, opts);
  }};
}})();
</script>"""
        html = html.replace("<script>", auth_snippet + "\n<script>", 1)
        return html

    @router.get("/config", include_in_schema=False,
                dependencies=[Depends(require_admin)])
    async def get_config():
        """Get dashboard configuration (service manager status, etc.)."""
        return {"service_manager_enabled": service_manager_enabled}

    # Only add service management routes if service manager is enabled
    if service_mgr is not None:
        @router.get("", include_in_schema=False,
                    dependencies=[Depends(require_admin)])
        async def list_services():
            """List all services with status, PID, uptime."""
            return service_mgr.get_status()

        @router.get("/{name}", include_in_schema=False,
                    dependencies=[Depends(require_admin)])
        async def get_service(name: str):
            """Get status of a specific service."""
            result = service_mgr.get_status(name)
            if "error" in result:
                return {"ok": False, **result}
            return result

        @router.post("/{name}/start", include_in_schema=False,
                     dependencies=[Depends(require_admin)])
        async def start_service(name: str):
            """Start a service."""
            result = await service_mgr.start_service(name)
            return result

        @router.post("/{name}/stop", include_in_schema=False,
                     dependencies=[Depends(require_admin)])
        async def stop_service(name: str):
            """Stop a service."""
            return await service_mgr.stop_service(name)

        @router.post("/{name}/restart", include_in_schema=False,
                     dependencies=[Depends(require_admin)])
        async def restart_service(name: str):
            """Restart a service."""
            result = await service_mgr.restart_service(name)
            return result

        @router.get("/{name}/logs", include_in_schema=False,
                    dependencies=[Depends(require_admin)])
        async def get_logs(name: str, lines: int = Query(default=50, ge=1, le=1000)):
            """Get recent log output for a service."""
            return service_mgr.get_logs(name, lines=lines)

    return router
