# Decision log

## 2026-05-29 — claude-sonnet-4-5
**Decision:** Use FMCW chirp (18–22 kHz) as primary engine over steady-tone
**Reason:** FMCW provides range resolution (~4.3 cm/bin) and range-Doppler map, enabling distance estimation and MTI clutter removal. Steady-tone gives only Doppler shift with no spatial information.
**Files affected:** fmcw_engine.py, engine_factory.py, config.json, dashboard.py, spatial.js
**Alternatives considered:** Steady-tone SignalEngine retained as fallback via engine_mode:"tone"

---

## 2026-05-30 — claude-sonnet-4-5
**Decision:** WASAPI exclusive mode mandatory, not optional
**Reason:** Windows audio APOs (echo cancellation, noise suppression, AGC) operate in shared mode and completely destroy the ultrasound chirp. Exclusive mode bypasses the entire APO chain. Without it, presence detection never triggers.
**Files affected:** fmcw_engine.py, signal_engine.py, config.json
**Alternatives considered:** Disabling APOs via Windows settings (fragile, user-unfriendly, not reliable across reboots)

---

## 2026-05-30 — claude-sonnet-4-5
**Decision:** Frame-to-frame range tracking for velocity, not slow-time Doppler centroid
**Reason:** Slow-time Doppler is unambiguous only to ~0.1 m/s at 48 kHz / 2048-sample chirp. Walking speed aliases. Range tracking (dR/dt, EMA-smoothed) handles faster motion at the cost of noise. Both signals are kept; Doppler drives direction sign, range rate drives the numeric velocity display.
**Files affected:** fmcw_engine.py
**Alternatives considered:** Staggered PRF to extend unambiguous velocity range (too complex for single-channel audio)

---

## 2026-05-31 — claude-sonnet-4-5
**Decision:** Rename project from RuView to MEW-View
**Reason:** Brand rename. All logger namespaces (ruview.* → mewview.*), log filename (ruview.log → mewview.log), page title, bat launcher, and schtasks task name updated. Zero "ruview" strings remain in tracked source.
**Files affected:** main.py, dashboard.py, fmcw_engine.py, signal_engine.py, detectors.py, calibrate.py, engine_factory.py, templates/index.html, start_mewview.bat, README.md, build/README.txt
**Alternatives considered:** none

---

