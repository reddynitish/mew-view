"""One-time empty-room calibration.

Runs the engine for a fixed window with the room empty, records per-window
band variance, and saves it to baseline.npy. Presence detection thresholds
are derived from this baseline.
"""

import os
import sys
import json
import time
import logging

import numpy as np

from engine_factory import build_engine

log = logging.getLogger("mewview.calibrate")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
BASELINE_PATH = os.path.join(HERE, "baseline.npy")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def run_calibration(seconds=30, prompt=True):
    cfg = load_config()
    if prompt:
        print("=" * 56)
        print(" MEW-View - Calibration")
        print(" Please LEAVE THE ROOM. Recording empty-room baseline")
        print(" for %d seconds. Starting in 10s..." % seconds)
        print("=" * 56)
        for i in range(10, 0, -1):
            print("  starting in %2d s" % i, end="\r", flush=True)
            time.sleep(1)
        print()

    engine = build_engine(cfg)
    engine.start()
    # let streams settle and the tone establish
    time.sleep(2.0)

    samples = []
    t_end = time.time() + seconds
    while time.time() < t_end:
        st = engine.get_state()
        v = float(st.get("band_variance", 0.0))
        if v > 0:
            samples.append(v)
        remaining = t_end - time.time()
        print("  recording baseline... %4.1fs left  (var=%.3e)"
              % (remaining, v), end="\r", flush=True)
        time.sleep(0.25)
    print()
    engine.stop()

    arr = np.asarray(samples, dtype=np.float64)
    if arr.size < 5:
        log.error("calibration captured too few samples (%d)", arr.size)
        print("ERROR: not enough audio captured. Check mic/permissions.")
        return False

    np.save(BASELINE_PATH, arr)
    cfg["calibrated"] = True
    save_config(cfg)
    print("Baseline saved: %s" % BASELINE_PATH)
    print("  samples=%d  mean=%.3e  std=%.3e"
          % (arr.size, arr.mean(), arr.std()))
    return True


def load_baseline():
    if os.path.exists(BASELINE_PATH):
        return np.load(BASELINE_PATH)
    return np.zeros(0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    ok = run_calibration(seconds=secs)
    sys.exit(0 if ok else 1)
