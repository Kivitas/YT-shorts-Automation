---
name: Feature Request
about: Suggest an idea or enhancement for AutoShorts & Faceless AI Studio v1.0
title: '[FEATURE] '
labels: enhancement
assignees: ''
---

**Is your feature request related to a problem? Please describe.**
A clear and concise description of what the problem is. Ex: I'm always frustrated when [...]

**Describe the Solution You'd Like**
A clear and concise description of what you want to happen.

**Target Component / Module**
- [ ] Windows Studio Launcher (`start.bat` / `bat.md`)
- [ ] Offline Resilience & Queue Recovery (`src/autoshorts/core/resilience.py`)
- [ ] Credential Guardian & Auth Circuit-Breaker (`src/autoshorts/core/credential_guardian.py`)
- [ ] Workflow Optimizer, Retention Pacing & EDL Snapshots (`src/autoshorts/edit_planner/workflow_optimizer.py`)
- [ ] Heatmap & SponsorBlock Precision Clipping (`src/autoshorts/edit_planner/planner.py`)
- [ ] Analytics 2.0 & Attribution Telemetry (`src/autoshorts/analytics/`)
- [ ] Autonomous Faceless AI Shorts Studio & Neural TTS (`src/autoshorts/research_agent/faceless_studio.py`)
- [ ] Dashboard Settings Modal (`⚙️`) & Live Link Previews
- [ ] 4-View Studio Command Center (Studio, Research, Faceless AI Studio, Analytics)
- [ ] 5 Vertical 9:16 Layout Modes & 9:16 Live Phone Previewer HUD
- [ ] 7 Studio Caption Presets & 1-Click Fast Re-Burn
- [ ] YouTube Data API v3 OAuth & Token Management
- [ ] Playwright MS Edge Studio Uploader & Fallback
- [ ] Gameplay Backgrounds Fetcher & Asset Management
- [ ] Spread Scheduler & Dual-Bucket Quota Tracker
- [ ] SemIf Token-Probability Decider & Window Slicer
- [ ] FFmpeg 9:16 Dopamine Compositor
- [ ] Path-Guarded Post-Upload Auto-Cleanup
- [ ] Other: `...`

**Hardware & Architectural Compliance**
- [ ] Operates efficiently within CPU + Intel UHD Graphics resource bounds (bounded concurrency = 1).
- [ ] Strictly respects tooling exclusivity: video download strictly via `yt-dlp`; rendering strictly via direct `FFmpeg`/`ffprobe` subprocesses; NO MoviePy; NO Playwright video stream downloads.
- [ ] Maintains the Zero Hardcoded Model policy (`model: null` dynamically resolved at runtime).
- [ ] Respects Zero-Phantom-Data mode and transactional SQLite persistence (`state_manager.py`).
- [ ] Respects path-guarded safety deletion (`assert_safe_to_delete` and `send2trash`).

**Describe Alternatives You've Considered**
A clear and concise description of any alternative solutions or features you've considered.

**Additional Context**
Add any other context, mockups, or architectural notes here.
