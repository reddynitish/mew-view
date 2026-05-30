"""RuView Acoustic — entry point.

Loads config, ensures a calibration baseline exists, starts the signal engine
and detector suite, then serves the dashboard. A watchdog restarts audio
streams after sleep/wake or device dropout.
"""

import os
import sys
import json
import time
import logging
import threading
from logging.handlers import TimedRotatingFileHandler

from engine_factory import build_engine
from detectors import DetectorSuite
from dashboard import create_dashboard
import calibrate

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
LOG_DIR = os.path.join(HERE, "logs")

log = logging.getLogger("ruview")


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    handler = TimedRotatingFileHandler(
        os.path.join(LOG_DIR, "ruview.log"),
        when="midnight", backupCount=7, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(console)


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def watchdog(engine):
    """Detect a stalled mic buffer (sleep/wake or device drop) and recover."""
    last_ts = 0.0
    stalls = 0
    while True:
        time.sleep(5.0)
        st = engine.get_state()
        ts = st.get("ts", 0.0)
        if ts == last_ts:
            stalls += 1
            log.warning("engine stalled (%d)", stalls)
            if stalls >= 2:
                try:
                    engine.restart_streams()
                except Exception as e:
                    log.error("restart failed: %s", e)
                stalls = 0
        else:
            stalls = 0
        last_ts = ts


def main():
    setup_logging()
    cfg = load_config()
    log.info("RuView Acoustic starting")

    # ensure calibration baseline exists
    if not cfg.get("calibrated") or not os.path.exists(
            os.path.join(HERE, "baseline.npy")):
        log.info("no baseline found — running calibration")
        ok = calibrate.run_calibration(seconds=30, prompt=True)
        if not ok:
            log.error("calibration failed; exiting")
            sys.exit(1)
        cfg = load_config()

    baseline = calibrate.load_baseline()

    engine = build_engine(cfg)
    engine.start()
    time.sleep(2.0)  # let tone + capture settle

    suite = DetectorSuite(engine, baseline, cfg)
    started_at = time.time()

    threading.Thread(target=watchdog, args=(engine,), daemon=True).start()

    port = int(cfg.get("dashboard_port", 5000))
    app, socketio = create_dashboard(engine, suite, cfg, started_at)
    log.info("dashboard at http://localhost:%d", port)
    try:
        socketio.run(app, host="0.0.0.0", port=port,
                     allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        log.info("shutdown complete")


if __name__ == "__main__":
    main()
