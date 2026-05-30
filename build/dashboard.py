"""Local web dashboard: Flask + Flask-SocketIO real-time room status."""

import time
import logging

import numpy as np
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

log = logging.getLogger("ruview.dashboard")

# display sizes for the spatial panels (keeps the socket payload small)
PROF_BINS = 48     # range profile / waterfall columns
RDM_R = 48         # range-Doppler map: range cells
RDM_D = 32         # range-Doppler map: Doppler cells


def _resample(arr, m):
    """Resample a 1-D array to length m by block-max (preserves peaks)."""
    a = np.asarray(arr, dtype=np.float32)
    if a.size == 0:
        return np.zeros(m, dtype=np.float32)
    if a.size == m:
        return a
    idx = np.linspace(0, a.size, m + 1).astype(int)
    return np.array([a[idx[i]:max(idx[i] + 1, idx[i + 1])].max()
                     for i in range(m)], dtype=np.float32)


def _range_profile_payload(st):
    prof = np.asarray(st.get("range_profile", []), dtype=np.float32)
    if prof.size == 0:
        return {"prof": [], "bin_m": 0.0, "max_m": 0.0, "guard_bins": 0}
    ds = _resample(prof, PROF_BINS)
    mx = float(ds.max()) + 1e-12
    bin_m = float(st.get("bin_m", 0.0))
    return {
        "prof": [round(float(x / mx), 3) for x in ds],
        "bin_m": round(bin_m, 5),
        "max_m": round(prof.size * bin_m, 3),
        "guard_frac": round(float(st.get("guard_bins", 0)) / max(1, prof.size), 3),
    }


def _rdm_payload(st):
    """Range-Doppler map -> small dB-scaled 0..255 byte grid (RDM_D x RDM_R)."""
    rdm = np.asarray(st.get("rdm", []), dtype=np.float32)  # (k, nr)
    if rdm.ndim != 2 or rdm.size == 0:
        return {"data": [], "nr": 0, "nd": 0, "v_max": 0.0}
    k, nr = rdm.shape
    # block-reduce to (RDM_D, RDM_R)
    di = np.linspace(0, k, RDM_D + 1).astype(int)
    ri = np.linspace(0, nr, RDM_R + 1).astype(int)
    small = np.zeros((RDM_D, RDM_R), dtype=np.float32)
    for i in range(RDM_D):
        for j in range(RDM_R):
            blk = rdm[di[i]:max(di[i] + 1, di[i + 1]), ri[j]:max(ri[j] + 1, ri[j + 1])]
            small[i, j] = blk.max() if blk.size else 0.0
    db = 20.0 * np.log10(small + 1e-6)
    db -= db.max()
    db = np.clip(db, -45.0, 0.0)
    bytes_ = ((db + 45.0) / 45.0 * 255.0).astype(np.uint8)
    return {"data": bytes_.flatten().tolist(), "nr": RDM_R, "nd": RDM_D,
            "v_max": round(float(st.get("v_max", 0.0)), 4)}


def _to_list(arr, cap=400):
    try:
        a = arr
        if a is None:
            return []
        a = a.tolist() if hasattr(a, "tolist") else list(a)
        if len(a) > cap:
            step = max(1, len(a) // cap)
            a = a[::step]
        return [round(float(x), 6) for x in a]
    except Exception:
        return []


def build_payload(result, started_at):
    p = result["presence"]
    m = result["movement"]
    b = result["breathing"]
    a = result["activity"]
    st = result["state"]
    return {
        "presence": {"present": p["present"],
                     "confidence": round(p["confidence"], 3)},
        "movement": {"level": m["level"],
                     "magnitude": round(m["magnitude"], 4),
                     "direction": m["direction"]},
        "breathing": {"bpm": b["bpm"], "valid": b["valid"]},
        "activity": a["state"],
        "active": bool(st.get("active", True)),
        "waveform": _to_list(st.get("waveform")),
        "doppler_hz": round(float(st.get("doppler_hz", 0.0)), 2),
        "range": _range_profile_payload(st),
        "rdm": _rdm_payload(st),
        "target": {
            "present": bool(st.get("target_present", False)),
            "range_m": round(float(st.get("target_range", 0.0)), 3),
            "strength": round(float(st.get("target_strength", 0.0)), 3),
            "velocity": round(float(st.get("target_velocity", 0.0)), 3),
            "snr": round(float(st.get("target_snr", 0.0)), 1),
        },
        "uptime": int(time.time() - started_at),
        "ts": time.strftime("%H:%M:%S"),
    }


def create_dashboard(engine, suite, config, started_at):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "ruview-acoustic-local"
    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

    latest = {"payload": None}

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/state")
    def api_state():
        return jsonify(latest["payload"] or {})

    @app.route("/api/health")
    def health():
        return jsonify({"ok": True, "uptime": int(time.time() - started_at)})

    @app.route("/api/power", methods=["POST"])
    def power():
        """Toggle the sensing program on/off. Body: {"on": true|false}."""
        data = request.get_json(silent=True) or {}
        want = bool(data.get("on", True))
        active = engine.set_active(want)
        log.info("power toggled -> active=%s", active)
        # push the new state immediately so every client updates in real time
        if latest["payload"]:
            latest["payload"]["active"] = active
            socketio.emit("update", latest["payload"])
        return jsonify({"active": active})

    def emitter():
        while True:
            try:
                result = suite.update()
                payload = build_payload(result, started_at)
                latest["payload"] = payload
                socketio.emit("update", payload)
            except Exception as e:
                log.exception("emit error: %s", e)
            socketio.sleep(0.5)

    @socketio.on("connect")
    def on_connect():
        if latest["payload"]:
            socketio.emit("update", latest["payload"])

    socketio.start_background_task(emitter)
    return app, socketio
