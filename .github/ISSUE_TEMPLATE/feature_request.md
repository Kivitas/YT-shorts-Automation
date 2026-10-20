---
name: Feature Request
about: Suggest an idea or enhancement for AutoShorts v1.0
title: '[FEATURE] '
labels: enhancement
assignees: ''
---

**Is your feature request related to a problem? Please describe.**
A clear and concise description of what the problem is. Ex: I'm always frustrated when [...]

**Describe the Solution You'd Like**
A clear and concise description of what you want to happen.

**Target Component / Module**
- [ ] Dashboard Settings Modal (`⚙️`) & Live Link Previews
- [ ] YouTube Data API v3 OAuth & Token Management
- [ ] Playwright MS Edge Studio Uploader & Fallback
- [ ] Gameplay Backgrounds Fetcher & Asset Management
- [ ] Subtitle Style Presets (`neon` / `minimal` / `pill`) & ASS Styler
- [ ] Spread Scheduler & Dual-Bucket Quota Tracker
- [ ] SemIf Token-Probability Decider & Window Slicer
- [ ] FFmpeg 9:16 Dopamine Compositor
- [ ] Path-Guarded Post-Upload Auto-Cleanup
- [ ] Other: `...`

**Hardware & Architectural Compliance**
- [ ] Operates efficiently within CPU + Intel UHD Graphics resource bounds (bounded concurrency = 1).
- [ ] Strictly respects tooling exclusivity: video download strictly via `yt-dlp`; rendering strictly via `FFmpeg`/`ffprobe` subprocesses; NO MoviePy; NO Playwright video stream downloads.
- [ ] Maintains the Zero Hardcoded Model policy (`model: null` dynamically resolved at runtime).
- [ ] Respects path-guarded safety deletion (`assert_safe_to_delete` and `send2trash`).

**Describe Alternatives You've Considered**
A clear and concise description of any alternative solutions or features you've considered.

**Additional Context**
Add any other context, mockups, or architectural notes here.
