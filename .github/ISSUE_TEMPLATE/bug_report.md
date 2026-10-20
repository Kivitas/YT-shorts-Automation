---
name: Bug Report
about: Create a report to help us improve AutoShorts v1.0
title: '[BUG] '
labels: bug
assignees: ''
---

**Describe the Bug**
A clear and concise description of what the bug is.

**Affected Component(s)**
Check all components involved:
- [ ] Dashboard & Settings Modal (`⚙️` in-app configuration)
- [ ] YouTube Data API v3 OAuth & Chunked Upload
- [ ] Playwright MS Edge Studio Fallback
- [ ] Gameplay Backgrounds Fetcher (CC0 Minecraft / Subway Surfers)
- [ ] Subtitle Style Presets (`neon` / `minimal` / `pill`)
- [ ] Spread Scheduler & Dual-Bucket Quota Tracker (50/day cap, 7/hr rolling limit)
- [ ] Ingestion, URL Validator & yt-dlp Precision Downloader
- [ ] SemIf Token-Probability Decider & Window Slicer
- [ ] FFmpeg 9:16 Dopamine Compositor
- [ ] Path-Guarded Safety Cleaner & Post-Upload Auto-Cleanup
- [ ] Gemini Spark Companion / Inbox Watcher
- [ ] CLI (`python run.py`)

**To Reproduce**
Steps to reproduce the behavior:
1. Input URL: `...`
2. Custom instructions: `...`
3. Caption Preset (if applicable): `neon` / `minimal` / `pill`
4. Pipeline stage where error occurred (e.g. Ingestion, Transcription, Compositing, YouTube OAuth, Studio Fallback): `...`
5. Error message / traceback:

**Expected Behavior**
A clear and concise description of what you expected to happen.

**Environment Information:**
- AutoShorts Version: [e.g. v1.0.0]
- OS: [e.g. Windows 11 64-bit / Ubuntu 24.04]
- CPU: [e.g. Intel Core i7-1165G7 (4 cores, 8 threads)]
- Integrated Graphics / GPU: [e.g. Intel Iris Xe / UHD Graphics]
- Python Version: [e.g. 3.12.3 via uv]
- FFmpeg Version: [e.g. 7.0-full_build]
- LLM Provider: [e.g. Local GGUF / Gemini / OpenRouter / None]

**Additional Context**
Add any other context, console output, or log excerpts from `logs/autoshorts.log` (sensitive tokens/keys are automatically redacted).
