(function () {
  "use strict";

  var conn = document.getElementById("conn");
  var roomStatus = document.getElementById("roomStatus");
  var statusCard = document.getElementById("statusCard");
  var confidence = document.getElementById("confidence");
  var moveLevel = document.getElementById("moveLevel");
  var moveBar = document.getElementById("moveBar");
  var moveDir = document.getElementById("moveDir");
  var doppler = document.getElementById("doppler");
  var bpm = document.getElementById("bpm");
  var bpmSub = document.getElementById("bpmSub");
  var activity = document.getElementById("activity");
  var uptime = document.getElementById("uptime");
  var updated = document.getElementById("updated");

  var tgtRange = document.getElementById("tgtRange");
  var tgtVel = document.getElementById("tgtVel");
  var tgtDir = document.getElementById("tgtDir");
  var tgtStr = document.getElementById("tgtStr");
  var tgtSnr = document.getElementById("tgtSnr");

  var powerToggle = document.getElementById("powerToggle");
  var powerState = document.getElementById("powerState");

  var canvas = document.getElementById("wave");
  var ctx = canvas.getContext("2d");
  var waveData = [];

  if (window.Spatial) { try { window.Spatial.init(); } catch (e) {} }

  // ---- power toggle ----
  var programActive = true;
  var lastToggleAt = 0;          // suppress payload echo briefly after a click

  function setPowerUI(on) {
    programActive = on;
    powerToggle.className = "switch " + (on ? "on" : "off");
    powerToggle.setAttribute("aria-pressed", on ? "true" : "false");
    powerState.textContent = on ? "ON" : "OFF";
    document.body.classList.toggle("paused", !on);
  }

  powerToggle.addEventListener("click", function () {
    var next = !programActive;
    lastToggleAt = Date.now();
    setPowerUI(next);            // optimistic
    fetch("/api/power", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: next })
    }).then(function (r) { return r.json(); })
      .then(function (d) { if (typeof d.active === "boolean") setPowerUI(d.active); })
      .catch(function () { setPowerUI(!next); });   // revert on failure
  });

  function fmtUptime(s) {
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    if (h) return h + "h " + m + "m";
    if (m) return m + "m " + x + "s";
    return x + "s";
  }

  function resize() {
    canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1);
    canvas.height = 160 * (window.devicePixelRatio || 1);
  }
  window.addEventListener("resize", resize);
  resize();

  function drawWave() {
    var w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#21262d";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
    if (!waveData.length) return;

    var max = 1e-9;
    for (var i = 0; i < waveData.length; i++) {
      var a = Math.abs(waveData[i]); if (a > max) max = a;
    }
    ctx.strokeStyle = "#58a6ff";
    ctx.lineWidth = 1.5 * (window.devicePixelRatio || 1);
    ctx.beginPath();
    for (var j = 0; j < waveData.length; j++) {
      var x = (j / (waveData.length - 1)) * w;
      var y = h / 2 - (waveData[j] / max) * (h / 2 - 6);
      if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  function setConn(ok) {
    conn.textContent = ok ? "live" : "offline";
    conn.className = "badge " + (ok ? "on" : "off");
  }

  function apply(d) {
    // reflect program power state (ignore for a moment right after a click)
    if (typeof d.active === "boolean" && Date.now() - lastToggleAt > 1500) {
      if (d.active !== programActive) setPowerUI(d.active);
    }

    var present = d.presence.present;
    roomStatus.textContent = present ? "OCCUPIED" : "EMPTY";
    confidence.textContent = Math.round(d.presence.confidence * 100) + "%";

    moveLevel.textContent = d.movement.level;
    var mag = Math.min(1, d.movement.magnitude / 0.5);
    moveBar.style.width = (mag * 100).toFixed(0) + "%";
    moveDir.textContent = d.movement.direction;
    doppler.textContent = d.doppler_hz;

    if (d.breathing.valid && d.breathing.bpm != null) {
      bpm.textContent = d.breathing.bpm;
      bpmSub.textContent = "breaths / min";
    } else {
      bpm.textContent = "--";
      bpmSub.textContent = present ? "subject must be still" : "no subject";
    }

    activity.textContent = d.activity;

    statusCard.className = "card status " +
      (d.activity === "possible fall" ? "fall" : (present ? "occupied" : "empty"));

    // spatial target readout
    if (d.target) {
      if (d.target.present) {
        tgtRange.textContent = d.target.range_m.toFixed(2) + " m";
        tgtVel.textContent = (d.target.velocity >= 0 ? "+" : "") +
          d.target.velocity.toFixed(2);
        tgtDir.textContent = Math.abs(d.target.velocity) < 0.05 ? "still" :
          (d.target.velocity < 0 ? "approaching" : "receding");
        tgtStr.textContent = Math.round(d.target.strength * 100) + "%";
        tgtSnr.textContent = d.target.snr.toFixed(0);
      } else {
        tgtRange.textContent = "-- m";
        tgtVel.textContent = "0.00"; tgtDir.textContent = "none";
        tgtStr.textContent = "0%"; tgtSnr.textContent = d.target.snr.toFixed(0);
      }
    }

    uptime.textContent = fmtUptime(d.uptime);
    updated.textContent = d.ts;

    if (d.waveform && d.waveform.length) { waveData = d.waveform; drawWave(); }
    if (window.Spatial) { try { window.Spatial.update(d); } catch (e) {} }
  }

  var socket = io({ transports: ["websocket", "polling"] });
  socket.on("connect", function () { setConn(true); });
  socket.on("disconnect", function () { setConn(false); });
  socket.on("update", apply);

  // polling fallback if sockets are blocked
  setInterval(function () {
    if (socket.connected) return;
    fetch("/api/state").then(function (r) { return r.json(); })
      .then(function (d) { if (d && d.presence) apply(d); })
      .catch(function () {});
  }, 1000);
})();
