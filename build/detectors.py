"""Detection layer: presence, movement, breathing, activity.

Each detector reads metrics from the SignalEngine and a saved empty-room
baseline, returning simple, dashboard-ready values.
"""

import time
import logging
import collections

import numpy as np

log = logging.getLogger("ruview.detect")


class PresenceDetector:
    """Compares live band variance against the empty-room baseline."""

    def __init__(self, baseline, config):
        # baseline: 1-D array of per-window band_variance from empty room
        b = np.asarray(baseline, dtype=np.float64)
        self.base_mean = float(np.mean(b)) if b.size else 0.0
        self.base_std = float(np.std(b)) if b.size else 1e-9
        self.k = float(config.get("presence_k", 4.0))
        self.threshold = self.base_mean + self.k * max(self.base_std, 1e-9)

    def update(self, state):
        var = float(state.get("band_variance", 0.0))
        present = var > self.threshold
        # confidence: how far above threshold, squashed to 0..1
        denom = max(self.threshold - self.base_mean, 1e-9)
        conf = (var - self.base_mean) / denom
        conf = float(np.clip(conf / 2.0, 0.0, 1.0))
        return {"present": bool(present), "confidence": conf, "variance": var}


class MovementTracker:
    """Maps Doppler magnitude/direction to a coarse movement level."""

    def __init__(self, config):
        m = config.get("movement_thresholds", {})
        self.slow = float(m.get("slow", 0.05))
        self.active = float(m.get("active", 0.15))
        self.rapid = float(m.get("rapid", 0.35))
        self._hist = collections.deque(maxlen=8)

    def update(self, state):
        mag = float(state.get("doppler_mag", 0.0))
        shift = float(state.get("doppler_hz", 0.0))
        self._hist.append(mag)
        smooth = float(np.mean(self._hist)) if self._hist else mag

        if smooth < self.slow:
            level = "still"
        elif smooth < self.active:
            level = "slow movement"
        elif smooth < self.rapid:
            level = "active movement"
        else:
            level = "rapid movement"

        if abs(shift) < 5:
            direction = "none"
        elif shift > 0:
            direction = "toward"
        else:
            direction = "away"
        return {"level": level, "magnitude": smooth,
                "direction": direction, "doppler_hz": shift}


class BreathingDetector:
    """FFT of the slow envelope in the 0.1-0.5 Hz band -> breaths/min."""

    def __init__(self, engine, config):
        self.engine = engine
        self.slow_fs = engine.slow_fs
        self.fmin = float(config.get("breath_fmin", 0.1))
        self.fmax = float(config.get("breath_fmax", 0.5))
        self.sensitivity = float(config.get("breath_sensitivity", 1.5))
        self._last_bpm = None

    def update(self, presence, movement):
        valid = presence.get("present") and movement.get("level") == "still"
        if not valid:
            self._last_bpm = None
            return {"bpm": None, "valid": False}

        sig = self.engine.get_slow_envelope().astype(np.float64)
        # need a full window of real data
        if sig.size < int(self.slow_fs * 20) or np.count_nonzero(sig) < sig.size // 2:
            return {"bpm": None, "valid": False}

        sig = sig - np.mean(sig)
        w = np.hanning(sig.size)
        spec = np.abs(np.fft.rfft(sig * w))
        freqs = np.fft.rfftfreq(sig.size, 1.0 / self.slow_fs)

        band = (freqs >= self.fmin) & (freqs <= self.fmax)
        if not np.any(band):
            return {"bpm": None, "valid": False}
        bspec = spec[band]
        bfreq = freqs[band]
        peak = int(np.argmax(bspec))
        # require the peak to stand out from band average
        if bspec[peak] < self.sensitivity * (np.mean(bspec) + 1e-12):
            return {"bpm": None, "valid": False}

        bpm = float(bfreq[peak] * 60.0)
        if self._last_bpm is None:
            self._last_bpm = bpm
        else:
            self._last_bpm = 0.6 * self._last_bpm + 0.4 * bpm
        return {"bpm": round(self._last_bpm, 1), "valid": True}


class ActivityClassifier:
    """Rule-based room state, including a simple fall heuristic."""

    def __init__(self, config):
        self._var_hist = collections.deque(maxlen=20)  # ~ last 10 s at 2 Hz
        self._fall_until = 0.0

    def update(self, presence, movement, state):
        var = float(state.get("band_variance", 0.0))
        self._var_hist.append((time.time(), var))

        # fall: sudden variance spike followed by a drop toward baseline
        fall = self._detect_fall()
        if fall:
            self._fall_until = time.time() + 6.0
        if time.time() < self._fall_until:
            return {"state": "possible fall"}

        if not presence.get("present"):
            return {"state": "empty"}

        level = movement.get("level")
        if level == "still":
            return {"state": "person still"}
        if level in ("slow movement", "active movement"):
            return {"state": "person moving"}
        return {"state": "person highly active"}

    def _detect_fall(self):
        if len(self._var_hist) < 8:
            return False
        vals = [v for _, v in self._var_hist]
        recent = vals[-3:]
        prior = vals[-8:-3]
        if not prior:
            return False
        peak = max(prior)
        base = np.median(vals[: max(1, len(vals) // 2)]) + 1e-12
        # big spike earlier, near-silence now
        spiked = peak > 6 * base
        quiet_now = np.mean(recent) < 1.5 * base
        return bool(spiked and quiet_now)


class DetectorSuite:
    """Convenience wrapper running all detectors per tick."""

    def __init__(self, engine, baseline, config):
        self.engine = engine
        self.presence = PresenceDetector(baseline, config)
        self.movement = MovementTracker(config)
        self.breathing = BreathingDetector(engine, config)
        self.activity = ActivityClassifier(config)

    def update(self):
        state = self.engine.get_state()
        p = self.presence.update(state)
        m = self.movement.update(state)
        b = self.breathing.update(p, m)
        a = self.activity.update(p, m, state)
        return {"presence": p, "movement": m, "breathing": b,
                "activity": a, "state": state}
