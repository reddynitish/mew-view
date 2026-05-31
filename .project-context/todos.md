# Todos

## Active

- [ ] Create venv on WUKONG: `cd build && python -m venv venv && venv\Scripts\pip install numpy scipy flask flask-socketio sounddevice pyaudio librosa matplotlib`
- [ ] Run calibration on WUKONG (empty room, 30 s) — baseline.npy missing, config calibrated:false
- [ ] Verify audio device indices on WUKONG (mic=9, speaker=8 may differ)
- [ ] Build graphify knowledge graph once API key is available: `graphify .`
- [ ] Run `scan_project` via project-context MCP to build structural graph

## Completed

- [x] FMCW engine written and DSP self-test passing (done 2026-05-30 by claude-sonnet-4-5)
- [x] Three.js spatial dashboard with RDM heatmap and waterfall (done 2026-05-30 by claude-sonnet-4-5)
- [x] Git repo initialized, initial commit pushed to github.com/reddynitish/mew-view (done 2026-05-31 by claude-sonnet-4-5)
- [x] Rename RuView → MEW-View across all files (done 2026-05-31 by claude-sonnet-4-5)
- [x] Install project-context MCP server and register in Claude Desktop config (done 2026-05-31 by claude-sonnet-4-5)
- [x] graphify hooks installed (post-commit, post-checkout) (done 2026-05-31 by claude-sonnet-4-5)
