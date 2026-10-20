---
name: Bug Report
about: Create a report to help us improve AutoShorts & Faceless AI Studio v1.0
title: '[BUG] '
labels: bug
assignees: ''
---

**Describe the Bug**
A clear and concise description of what the bug is.

**Affected Component(s)**
Check all components involved:
- [ ] Windows Studio Launcher (`start.bat` / `bat.md`)
- [ ] Offline Recovery & Queue Resilience (`src/autoshorts/core/resilience.py`)
- [ ] Credential Guardian & Auth Circuit-Breaker (`src/autoshorts/core/credential_guardian.py`)
- [ ] Workflow Optimizer, Retention Pacing & EDL Snapshots (`src/autoshorts/edit_planner/workflow_optimizer.py`)
- [ ] Heatmap & SponsorBlock Precision Slicing (`src/autoshorts/edit_planner/planner.py`)
- [ ] Analytics 2.0 Engine & Closed-Loop Attribution (`src/autoshorts/analytics/`)
- [ ] Autonomous Faceless AI Shorts Studio (`src/autoshorts/research_agent/faceless_studio.py`)
- [ ] Dashboard & Settings Modal (`⚙️` in-app configuration)
- [ ] 4-View Studio Command Center (Studio & Queue, Viral Research, Faceless AI Studio, Analytics)
- [ ] 5 Vertical 9:16 Layout Modes & 9:16 Live Phone Previewer HUD
- [ ] 7 Studio Caption Presets & 1-Click Fast Re-Burn
- [ ] YouTube Data API v3 OAuth & Chunked Upload
- [ ] Playwright MS Edge Studio Fallback
- [ ] Gameplay Backgrounds Fetcher (CC0 Minecraft / Subway Surfers)
- [ ] Spread Scheduler & Dual-Bucket Quota Tracker (50/day cap, 7/hr rolling limit)
- [ ] Ingestion, URL Validator & yt-dlp Precision Downloader
- [ ] SemIf Token-Probability Decider & Window Slicer
- [ ] FFmpeg 9:16 Dopamine Compositor
- [ ] Path-Guarded Safety Cleaner & Post-Upload Auto-Cleanup
- [ ] Gemini Spark Companion / Inbox Watcher
- [ ] CLI (`python run.py`, `autoshorts.cli`)

**To Reproduce**
Steps to reproduce the behavior:
1. Command or Dashboard Action: `...` (e.g. `start.bat --check`, `start.bat --port 8080`, or dashboard run)
2. Input URL / Topic: `...`
3. Custom instructions / Preset: `...`
4. Pipeline stage where error occurred (e.g. Ingestion, Transcription, Compositing, YouTube OAuth, Studio Fallback, Analytics): `...`
5. Error message / traceback:

**Expected Behavior**
A clear and concise description of what you expected to happen.

**Environment Information:**
- AutoShorts Version: [e.g. v1.0.0]
- OS: [e.g. Windows 11 64-bit / Ubuntu 24.04]
- CPU: [e.g. Intel Core i7-1165G7 (4 cores, 8 threads)]
- Integrated Graphics / GPU: [e.g. Intel Iris Xe / UHD Graphics]
- Python Version: [e.g. 3.12.3 via uv]
- FFmpeg Version: [e.g. 7.0-full_build or bundled imageio-ffmpeg]
- LLM Provider: [e.g. Local GGUF / Gemini / OpenRouter / None]

**Additional Context**
Add any other context, console output, or log excerpts from `data/logs/` or `logs/autoshorts.log` (sensitive tokens/keys are automatically redacted).
