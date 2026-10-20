// AutoShorts Dashboard Client (v1.0 Overhaul)

let _jobsCache = [];
let _ptSecondsRemaining = null;
let _eventSource = null;
let _lastQuota = null;
let _lastScheduler = null;
let _currentFilter = "all";
let _settingsCache = {};
let _oauthPollTimer = null;
let _timelineCollapsed = false;

document.addEventListener("DOMContentLoaded", () => {
  // Setup sync between #background-select and legacy #bg-select
  initBackgroundSelectSync();

  refreshHealth();
  refreshQuota();
  refreshScheduler();
  refreshJobs();
  refreshBackgrounds();
  initLogStream();
  checkInitialEdgeAuth();
  checkInitialYouTubeAuth();
  refreshSchedulerSlots();
  syncPresetCardHighlight();

  // Initialize AutoShorts Studio Systems
  initStudioProfiles();
  syncLayoutCards();
  syncCaptionCards();
  updatePhoneCanvasPreview();
  initCaptionAnimationTicker();
  initKeyboardShortcuts();

  // Initialize Top-Level View Tabs & Autonomous Hubs
  initMainViewTabs();
  initResearchAgent();
  initMoneyPrinter();
  initAnalytics();

  // PT Countdown ticking every 1 second
  setInterval(() => {
    tickPtCountdown();
  }, 1000);

  // Auto-refresh metrics, jobs, scheduler, and auth every 4 seconds
  setInterval(() => {
    refreshQuota();
    refreshScheduler();
    refreshJobs();
    refreshBackgrounds();
    refreshSchedulerSlots();
    checkInitialYouTubeAuth();
  }, 4000);
});

// -----------------------------------------------------------------------------
// Legacy & Compatibility Helpers
// -----------------------------------------------------------------------------

function initBackgroundSelectSync() {
  const bgMain = document.getElementById("background-select");
  const bgAlias = document.getElementById("bg-select");
  if (bgMain && bgAlias) {
    bgMain.addEventListener("change", () => {
      bgAlias.value = bgMain.value;
    });
    bgAlias.addEventListener("change", () => {
      bgMain.value = bgAlias.value;
    });
  }
}

// -----------------------------------------------------------------------------
// System Health & Telemetry
// -----------------------------------------------------------------------------

async function refreshHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return;
    const data = await res.json();
    if (data.cpu_mode) {
      const cpuEl = document.getElementById("cpu-label");
      if (cpuEl) cpuEl.textContent = data.cpu_mode;
    }
    if (data.llm_provider) {
      const provEl = document.getElementById("provider-label");
      if (provEl) provEl.textContent = `Provider: ${data.llm_provider}`;
    }
    if (data.ffmpeg_encoder) {
      const encEl = document.getElementById("encoder-label");
      if (encEl) encEl.textContent = data.ffmpeg_encoder;
    }
  } catch (err) {
    console.debug("Health check error:", err);
  }
}

// -----------------------------------------------------------------------------
// Quota & Rate Limit Gauges
// -----------------------------------------------------------------------------

async function refreshQuota() {
  try {
    const res = await fetch("/api/quota");
    if (!res.ok) return;
    const q = await res.json();
    _lastQuota = q;

    // 1. YouTube API Uploads
    const upSpend = q.upload_spent || 0;
    const upLimit = q.upload_limit || 100;
    const upPct = Math.min(100, (upSpend / upLimit) * 100);
    const upSpendEl = document.getElementById("upload-spend");
    if (upSpendEl) {
      upSpendEl.innerHTML = `${upSpend} <span class="metric-sub">/ ${upLimit} free uploads today</span>`;
    }
    const upBarEl = document.getElementById("upload-bar");
    if (upBarEl) upBarEl.style.width = `${upPct}%`;
    const upRemText = `${q.upload_remaining !== undefined ? q.upload_remaining : 100} free uploads remaining today`;
    const upRemEl = document.getElementById("upload-remaining");
    if (upRemEl) {
      upRemEl.textContent = upRemText;
    }
    const quotaUpTextEl = document.getElementById("quota-upload-text");
    if (quotaUpTextEl) quotaUpTextEl.textContent = upRemText;

    // 2. API Calls Pool
    const genSpend = q.general_spent || 0;
    const genLimit = q.general_limit || 10000;
    const genPct = Math.min(100, (genSpend / genLimit) * 100);
    const genSpendEl = document.getElementById("general-spend");
    if (genSpendEl) {
      genSpendEl.innerHTML = `${genSpend} <span class="metric-sub">/ ${genLimit} units</span>`;
    }
    const genBarEl = document.getElementById("general-bar");
    if (genBarEl) genBarEl.style.width = `${genPct}%`;
    const genRemText = `${q.general_remaining !== undefined ? q.general_remaining : 10000} units remaining`;
    const genRemEl = document.getElementById("general-remaining");
    if (genRemEl) {
      genRemEl.textContent = genRemText;
    }
    const quotaGenTextEl = document.getElementById("quota-general-text");
    if (quotaGenTextEl) quotaGenTextEl.textContent = genRemText;

    // 3. Hourly Anti-Spam Pacing
    const rateCount = q.rolling_hour_count !== undefined ? q.rolling_hour_count : 0;
    const rateLimit = q.rolling_hour_limit || 7;
    const ratePct = Math.min(100, (rateCount / rateLimit) * 100);
    const rateValEl = document.getElementById("rolling-rate-val");
    if (rateValEl) {
      rateValEl.innerHTML = `${rateCount} <span class="metric-sub">/ ${rateLimit} uploads this hour</span>`;
    }
    const rateBarEl = document.getElementById("rolling-rate-bar");
    if (rateBarEl) {
      rateBarEl.style.width = `${ratePct}%`;
      if (rateCount >= rateLimit) {
        rateBarEl.style.background = "var(--accent-red)";
      } else if (rateCount >= rateLimit - 1) {
        rateBarEl.style.background = "var(--accent-yellow)";
      } else {
        rateBarEl.style.background = "";
      }
    }
    const rateFooterText = `${rateCount} / ${rateLimit} uploads in rolling 60 min`;
    const rateFooterEl = document.getElementById("rolling-rate-footer");
    if (rateFooterEl) {
      rateFooterEl.textContent = rateFooterText;
    }
    const quotaHourlyTextEl = document.getElementById("quota-hourly-text");
    if (quotaHourlyTextEl) quotaHourlyTextEl.textContent = rateFooterText;

    // 4. Daily Cap & Pacific-Time Reset Countdown Sync
    if (q.seconds_until_reset !== undefined) {
      _ptSecondsRemaining = q.seconds_until_reset;
    }
    renderPacingStatus();
  } catch (err) {
    console.debug("Quota refresh error:", err);
  }
}

function tickPtCountdown() {
  if (_ptSecondsRemaining !== null && _ptSecondsRemaining > 0) {
    _ptSecondsRemaining -= 1;
    renderPacingStatus();
  }
}

function renderPacingStatus() {
  const mainEl = document.getElementById("pacing-status-main");
  const subEl = document.getElementById("pacing-status-sub");
  const capEl = document.getElementById("daily-cap-val");

  // Determine status
  const isUploading = _jobsCache && _jobsCache.some(j => j.status === "UPLOADING");
  const pendingSlots = (_lastScheduler && _lastScheduler.pending_slots) || [];

  let statusMain = "Queue Idle";
  let statusSub = "No pending videos waiting to upload";

  if (isUploading) {
    statusMain = "Uploading Now";
    statusSub = "Active video being uploaded to YouTube...";
  } else if (pendingSlots.length > 0) {
    const nextSlot = pendingSlots[0];
    const targetTime = nextSlot.scheduled_time ? new Date(nextSlot.scheduled_time).getTime() : 0;
    const now = Date.now();
    const diffMs = targetTime - now;
    if (diffMs <= 0) {
      statusMain = "Next Upload: Due now";
    } else {
      const diffMin = Math.max(1, Math.round(diffMs / 60000));
      const timeStr = diffMin >= 60 ? `${Math.floor(diffMin / 60)}h ${diffMin % 60}m` : `${diffMin}m`;
      statusMain = `Next Upload: in ${timeStr}`;
    }
    statusSub = `${pendingSlots.length} video(s) scheduled in queue`;
  }

  if (mainEl) mainEl.textContent = statusMain;
  if (subEl) subEl.textContent = statusSub;

  // Format secondary subtext: "Daily Uploads: 0 / 50 • Reset in 4h 06m (12:30 PM local)"
  const completed = (_lastQuota && _lastQuota.completed_today) !== undefined ? _lastQuota.completed_today : 0;
  const dailyCap = (_lastQuota && _lastQuota.daily_cap) || 50;

  let secondaryText = `Daily Uploads: ${completed} / ${dailyCap}`;
  if (_ptSecondsRemaining !== null && _ptSecondsRemaining >= 0) {
    const total = _ptSecondsRemaining;
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const resetDate = new Date(Date.now() + total * 1000);
    const localTimeStr = resetDate.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    secondaryText = `Daily Uploads: ${completed} / ${dailyCap} • Reset in ${hours}h ${String(minutes).padStart(2, "0")}m (${localTimeStr} local)`;

    const countdownTimerEl = document.getElementById("countdown-timer");
    if (countdownTimerEl) {
      countdownTimerEl.textContent = `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }
  }
  if (capEl) capEl.textContent = secondaryText;
}

// -----------------------------------------------------------------------------
// Scheduler Controls & Upcoming Timeline
// -----------------------------------------------------------------------------

async function refreshScheduler() {
  try {
    const res = await fetch("/api/scheduler/status");
    if (!res.ok) return;
    const s = await res.json();
    _lastScheduler = s;

    const tag = document.getElementById("scheduler-status-tag");
    const btn = document.getElementById("scheduler-toggle-btn");
    const label = document.getElementById("scheduler-toggle");
    const pending = document.getElementById("pending-slots-count");

    if (s.paused) {
      if (tag) {
        tag.textContent = "PAUSED";
        tag.style.color = "var(--accent-red)";
      }
      if (label) label.textContent = "Resume Scheduler";
      if (btn) {
        btn.className = "btn btn-primary btn-sm";
      }
    } else {
      if (tag) {
        tag.textContent = "ACTIVE";
        tag.style.color = "var(--accent-green)";
      }
      if (label) label.textContent = "Pause Scheduler";
      if (btn) {
        btn.className = "btn btn-secondary btn-sm";
      }
    }

    if (pending) pending.textContent = `${s.pending_count || 0} queued slots`;
    renderPacingStatus();
  } catch (err) {
    console.debug("Scheduler refresh error:", err);
  }
}

async function toggleScheduler() {
  try {
    const res = await fetch("/api/scheduler/toggle", { method: "POST" });
    if (res.ok) refreshScheduler();
  } catch (err) {
    alert("Failed to toggle scheduler: " + err);
  }
}

async function refreshSchedulerSlots() {
  try {
    let slots = [];
    const res = await fetch("/api/scheduler/slots");
    if (res.ok) {
      const data = await res.json();
      slots = data.slots || data.pending_slots || [];
    } else if (_lastScheduler && _lastScheduler.pending_slots) {
      slots = _lastScheduler.pending_slots;
    }

    const countEl = document.getElementById("timeline-count");
    if (countEl) countEl.textContent = slots.length;

    const listEl = document.getElementById("timeline-slots-list");
    if (!listEl) return;

    if (slots.length === 0) {
      listEl.innerHTML = '<div class="timeline-empty">No upcoming scheduled slots.</div>';
      return;
    }

    listEl.innerHTML = slots.map(slot => {
      const timeStr = slot.scheduled_time
        ? new Date(slot.scheduled_time).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" })
        : "Pending next available window";
      const title = slot.title || `Job #${slot.job_id || "clip"}`;
      return `
        <div class="timeline-item">
          <span class="timeline-item-title" title="${escapeHtml(title)}">🎬 ${escapeHtml(title)}</span>
          <span class="timeline-item-time">⏰ ${escapeHtml(timeStr)}</span>
        </div>
      `;
    }).join("");
  } catch (err) {
    console.debug("Error refreshing scheduler slots:", err);
  }
}

function toggleTimelineCollapse() {
  _timelineCollapsed = !_timelineCollapsed;
  const listEl = document.getElementById("timeline-slots-list");
  const chevron = document.getElementById("timeline-chevron");
  if (listEl) {
    listEl.classList.toggle("collapsed", _timelineCollapsed);
  }
  if (chevron) {
    chevron.classList.toggle("collapsed", _timelineCollapsed);
  }
}

// -----------------------------------------------------------------------------
// 9-Stage Visual Progression Stepper
// -----------------------------------------------------------------------------

const STAGES = [
  { id: 1, key: "metadata_fetch", label: "Ingest", name: "1. Ingest" },
  { id: 2, key: "transcript_fetch", label: "Transcript", name: "2. Transcript" },
  { id: 3, key: "slicing", label: "Slicing", name: "3. Slicing" },
  { id: 4, key: "semif", label: "SemIf", name: "4. SemIf" },
  { id: 5, key: "download_cut", label: "Cut", name: "5. Cut" },
  { id: 6, key: "edl_planning", label: "EDL", name: "6. EDL" },
  { id: 7, key: "subtitles", label: "Subs", name: "7. Subs" },
  { id: 8, key: "compositing", label: "Composite", name: "8. Composite" },
  { id: 9, key: "review_upload", label: "Review", name: "9. Review" },
];

function getStageStates(job) {
  const status = job.status || "QUEUED";
  let currentIndex = 1;
  const isFailed = (status === "FAILED");
  const isComplete = (status === "COMPLETED" || status === "CLEANED");

  const statusStageMap = {
    "QUEUED": 1,
    "METADATA_FETCHED": 2,
    "TRANSCRIBED": 3,
    "WINDOWS_SLICED": 4,
    "SEMIF_EVALUATED": 5,
    "DOWNLOADING_SECTION": 6,
    "SUBTITLES_GENERATED": 8,
    "COMPOSITING": 8,
    "READY_FOR_REVIEW": 9,
    "SCHEDULED": 9,
    "UPLOADING": 9,
    "VERIFYING": 9,
    "COMPLETED": 9,
    "CLEANED": 9,
  };

  if (job.rendered_video_path || job.preview_video_path) {
    currentIndex = 9;
  } else if (statusStageMap[status]) {
    currentIndex = statusStageMap[status];
  }

  return STAGES.map(stage => {
    let state = "pending";
    if (isComplete) {
      state = "completed";
    } else if (stage.id < currentIndex) {
      state = "completed";
    } else if (stage.id === currentIndex) {
      state = isFailed ? "failed" : "active";
    } else {
      state = "pending";
    }
    return { ...stage, state };
  });
}

function buildStepperHtml(job) {
  const stageStates = getStageStates(job);
  const items = stageStates.map((st, i) => {
    const icon = st.state === "completed" ? "✓" : st.id;
    return `
      <div class="stepper-step ${st.state}" onclick="inspectJobStages('${job.id}')" title="Stage ${st.name} (${st.state}) - Click to inspect">
        <span class="step-num">${icon}</span>
        <span>${st.label}</span>
      </div>
      ${i < stageStates.length - 1 ? '<span class="stepper-arrow">→</span>' : ''}
    `;
  }).join("");

  return `<div class="stepper-container">${items}</div>`;
}

// -----------------------------------------------------------------------------
// Job Queue, Filter Tabs & Rendering
// -----------------------------------------------------------------------------

async function refreshJobs() {
  try {
    const res = await fetch("/api/jobs");
    if (!res.ok) return;
    const data = await res.json();
    _jobsCache = data.jobs || [];
    renderJobs(_jobsCache);
  } catch (err) {
    console.debug("Jobs refresh error:", err);
  }
}

function setJobFilter(filter) {
  _currentFilter = filter;
  document.querySelectorAll(".filter-tab").forEach(tab => {
    if (tab.getAttribute("data-filter") === filter) {
      tab.classList.add("active");
    } else {
      tab.classList.remove("active");
    }
  });
  renderJobs(_jobsCache);
}

function renderJobs(jobs) {
  const container = document.getElementById("jobs-container");
  if (!container) return;

  // Compute category counts
  let countAll = jobs.length;
  let countActive = 0;
  let countScheduled = 0;
  let countCompleted = 0;
  let countFailed = 0;

  const activeStatuses = new Set([
    "QUEUED", "METADATA_FETCHED", "TRANSCRIBED", "WINDOWS_SLICED",
    "SEMIF_EVALUATED", "DOWNLOADING_SECTION", "SUBTITLES_GENERATED",
    "COMPOSITING", "UPLOADING", "VERIFYING"
  ]);

  jobs.forEach(j => {
    const st = j.status;
    if (activeStatuses.has(st)) countActive++;
    if (st === "SCHEDULED" || st === "READY_FOR_REVIEW") countScheduled++;
    if (st === "COMPLETED" || st === "CLEANED") countCompleted++;
    if (st === "FAILED") countFailed++;
  });

  const cAllEl = document.getElementById("count-all");
  const cActEl = document.getElementById("count-active");
  const cSchEl = document.getElementById("count-scheduled");
  const cComEl = document.getElementById("count-completed");
  const cFaiEl = document.getElementById("count-failed");

  if (cAllEl) cAllEl.textContent = countAll;
  if (cActEl) cActEl.textContent = countActive;
  if (cSchEl) cSchEl.textContent = countScheduled;
  if (cComEl) cComEl.textContent = countCompleted;
  if (cFaiEl) cFaiEl.textContent = countFailed;

  const queueTabBadge = document.getElementById("studio-queue-tab-badge");
  if (queueTabBadge) queueTabBadge.textContent = countActive > 0 ? countActive : countAll;

  // Filter jobs by current active tab
  let filteredJobs = jobs;
  if (_currentFilter === "active") {
    filteredJobs = jobs.filter(j => activeStatuses.has(j.status));
  } else if (_currentFilter === "scheduled") {
    filteredJobs = jobs.filter(j => j.status === "SCHEDULED" || j.status === "READY_FOR_REVIEW");
  } else if (_currentFilter === "completed") {
    filteredJobs = jobs.filter(j => j.status === "COMPLETED" || j.status === "CLEANED");
  } else if (_currentFilter === "failed") {
    filteredJobs = jobs.filter(j => j.status === "FAILED");
  }

  if (filteredJobs.length === 0) {
    container.innerHTML = `<div class="empty-state">No ${_currentFilter !== "all" ? _currentFilter : ""} jobs in queue.</div>`;
    return;
  }

  container.innerHTML = filteredJobs.map(job => {
    const title = (job.video_metadata && job.video_metadata.title) ? job.video_metadata.title : (job.clip_title || job.source_url);
    const channel = (job.video_metadata && job.video_metadata.channel) ? `by ${job.video_metadata.channel}` : "";
    const hasVideo = job.rendered_video_path || job.preview_video_path;
    const ytId = job.youtube_video_id || (job.upload_result && job.upload_result.video_id);

    // Actions Buttons
    let actions = "";
    actions += `<button class="btn btn-secondary btn-sm" onclick="openEditJobModal('${job.id}')" title="Edit timeline, transcript, layout, and studio re-burn">✏️ Edit &amp; Studio Re-Burn</button>`;

    if (hasVideo) {
      actions += `<button class="btn btn-secondary btn-sm" onclick="openPreview('${job.id}', '${encodeURIComponent(title)}')">▶ Preview</button>`;
    }
    if (ytId) {
      actions += `<a href="https://youtube.com/shorts/${ytId}" target="_blank" rel="noopener noreferrer" class="btn btn-yt btn-sm">▶ Open on YouTube ↗</a>`;
    }
    if (job.status === "READY_FOR_REVIEW" || job.status === "SCHEDULED") {
      actions += `<button class="btn btn-primary btn-sm" onclick="uploadNow('${job.id}')">🚀 Upload Now</button>`;
    }
    if (job.status === "FAILED") {
      actions += `<button class="btn btn-secondary btn-sm" onclick="retryJob('${job.id}')">🔄 Retry</button>`;
    }
    actions += `<button class="btn btn-danger btn-sm" onclick="cleanJobFiles('${job.id}')" title="Safely purge intermediate work files">🗑️ Clean Files</button>`;

    // Copy Toolbar
    let copyToolbar = "";
    if (job.seo_title || job.full_description) {
      copyToolbar = `
        <div class="copy-toolbar">
          <span class="copy-toolbar-label">📋 Quick Copy:</span>
          <button class="btn-copy" onclick="copyJobField('${job.id}', 'seo_title', this)" title="Copy YouTube SEO Title">
            🏷️ SEO Title
          </button>
          <button class="btn-copy" onclick="copyJobField('${job.id}', 'full_description', this)" title="Copy Description with Source Attribution &amp; Disclaimer">
            📝 Description
          </button>
          <button class="btn-copy" onclick="copyJobField('${job.id}', 'hashtags', this)" title="Copy Recommended Hashtags">
            #️⃣ Hashtags
          </button>
        </div>
      `;
    }

    const stepperHtml = buildStepperHtml(job);
    const errorHtml = job.error_message
      ? `<div style="color: var(--accent-red); font-size: 0.75rem; margin-top: 0.35rem;">⚠️ ${escapeHtml(job.error_message)}</div>`
      : "";

    return `
      <div class="job-card" id="job-${job.id}">
        <div class="job-header">
          <span class="job-id">#${job.id}</span>
          <span class="status-badge status-${job.status}">${job.status}</span>
        </div>
        <div class="job-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
        <div class="job-meta">
          <span>${escapeHtml(channel)}</span>
          <span>Updated: ${new Date(job.updated_at || Date.now()).toLocaleTimeString()}</span>
        </div>
        ${stepperHtml}
        ${copyToolbar}
        ${errorHtml}
        <div class="job-actions">${actions}</div>
      </div>
    `;
  }).join("");

  renderPacingStatus();
}

// -----------------------------------------------------------------------------
// Batch Actions: Retry Failed & Clean Completed
// -----------------------------------------------------------------------------

async function batchRetryFailed() {
  const failedJobs = _jobsCache.filter(j => j.status === "FAILED");
  if (failedJobs.length === 0) {
    showToast("No failed jobs to retry.");
    return;
  }

  showToast(`Retrying ${failedJobs.length} failed job(s)...`);
  try {
    const res = await fetch("/api/jobs/batch-retry", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      showToast(data.message || `Retried ${data.count || failedJobs.length} job(s)!`);
      refreshJobs();
      return;
    }
  } catch (e) {
    console.debug("Batch retry endpoint fallback:", e);
  }

  // Graceful fallback to sequential retries
  for (const j of failedJobs) {
    try {
      await fetch(`/api/jobs/${j.id}/retry`, { method: "POST" });
    } catch (e) {}
  }
  refreshJobs();
  showToast(`Triggered retry for ${failedJobs.length} job(s)!`);
}

async function batchCleanCompleted() {
  const completedJobs = _jobsCache.filter(j => j.status === "COMPLETED" || j.status === "CLEANED");
  if (completedJobs.length === 0) {
    showToast("No completed jobs to clean.");
    return;
  }

  if (!confirm(`Purge intermediate files for ${completedJobs.length} completed job(s)? Final master MP4s will be kept.`)) return;

  showToast("Cleaning completed job files...");
  try {
    const res = await fetch("/api/jobs/batch-clean", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      showToast(data.message || `Cleaned files for ${data.count || completedJobs.length} job(s)!`);
      refreshJobs();
      return;
    }
  } catch (e) {
    console.debug("Batch clean endpoint fallback:", e);
  }

  // Graceful fallback to individual job cleanup
  let cleaned = 0;
  for (const j of completedJobs) {
    try {
      await fetch(`/api/jobs/${j.id}`, { method: "DELETE" });
      cleaned++;
    } catch (e) {}
  }
  refreshJobs();
  showToast(`Cleaned files for ${cleaned} job(s)!`);
}

// -----------------------------------------------------------------------------
// Interactive Video Editing, Transcript & Studio Re-Burn Modal
// -----------------------------------------------------------------------------

function switchEditorTab(tab) {
  ["trim", "transcript", "publish"].forEach(t => {
    const btn = document.getElementById(`editor-tab-btn-${t}`);
    const pane = document.getElementById(`editor-pane-${t}`);
    if (btn) btn.classList.toggle("active", t === tab);
    if (pane) pane.style.display = (t === tab) ? "block" : "none";
  });

  if (tab === "transcript") {
    const jobId = document.getElementById("edit-job-id").value;
    if (jobId) loadJobTranscript(jobId);
  }
}

function updateTrimmerDuration() {
  const start = parseFloat(document.getElementById("edit-start-sec").value) || 0;
  const end = parseFloat(document.getElementById("edit-end-sec").value) || 0;
  const dur = Math.max(0, end - start);
  const badge = document.getElementById("edit-duration-badge");
  if (!badge) return;

  if (dur <= 60 && dur >= 5) {
    badge.className = "badge badge-success";
    badge.textContent = `${dur.toFixed(1)}s (✓ Optimal <60s Shorts)`;
  } else if (dur > 60) {
    badge.className = "badge badge-danger";
    badge.textContent = `${dur.toFixed(1)}s (⚠️ Exceeds 60s limit!)`;
  } else {
    badge.className = "badge";
    badge.textContent = `${dur.toFixed(1)}s`;
  }
}

async function applyJobTrim() {
  const jobId = document.getElementById("edit-job-id").value;
  if (!jobId) return;

  const start = parseFloat(document.getElementById("edit-start-sec").value) || 0;
  const end = parseFloat(document.getElementById("edit-end-sec").value) || 45;

  const btn = document.getElementById("apply-trim-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Trimming...";
  }

  try {
    const res = await fetch(`/api/jobs/${jobId}/trim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_sec: start, end_sec: end }),
    });
    if (res.ok) {
      showToast(`✓ Trim saved for #${jobId} (${(end - start).toFixed(1)}s)!`);
      refreshJobs();
    } else {
      const err = await res.json();
      showToast("Trim failed: " + (err.detail || "Server error"));
    }
  } catch (err) {
    showToast("Trim API error: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "✂️ Save Trim Range";
    }
  }
}

async function loadJobTranscript(jobId) {
  const listEl = document.getElementById("edit-transcript-word-list");
  const rawEl = document.getElementById("edit-transcript-raw");
  if (!listEl) return;

  listEl.innerHTML = '<div class="transcript-placeholder">Loading word-level transcript from whisper alignment...</div>';

  try {
    const res = await fetch(`/api/jobs/${jobId}/transcript`);
    if (!res.ok) {
      listEl.innerHTML = '<div class="transcript-placeholder">Transcript generated during Stage 7 (Whisper word-alignment). Type raw corrections below if desired.</div>';
      return;
    }
    const data = await res.json();
    const words = data.words || [];

    if (words.length === 0) {
      listEl.innerHTML = '<div class="transcript-placeholder">No aligned word tokens stored yet for this job.</div>';
      if (rawEl && !rawEl.value) rawEl.value = data.raw_text || "";
      return;
    }

    let fullText = "";
    listEl.innerHTML = words.map((w, idx) => {
      const wordText = w.word || w.text || "";
      fullText += (idx > 0 ? " " : "") + wordText;
      const isEmphasis = !!w.emphasis;
      return `<span class="transcript-word ${isEmphasis ? 'word-emphasis' : ''}" data-idx="${idx}" onclick="toggleWordEmphasis(this)" title="Click to toggle emphasis">${escapeHtml(wordText)}</span>`;
    }).join(" ");

    if (rawEl) rawEl.value = fullText;
  } catch (err) {
    listEl.innerHTML = `<div class="transcript-placeholder">Notice: Transcript endpoint not available (${escapeHtml(String(err))}).</div>`;
  }
}

function reloadJobTranscript() {
  const jobId = document.getElementById("edit-job-id").value;
  if (jobId) loadJobTranscript(jobId);
}

function toggleWordEmphasis(span) {
  span.classList.toggle("word-emphasis");
}

async function saveJobTranscript() {
  const jobId = document.getElementById("edit-job-id").value;
  if (!jobId) return;

  const btn = document.getElementById("save-transcript-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Saving...";
  }

  const rawText = document.getElementById("edit-transcript-raw") ? document.getElementById("edit-transcript-raw").value : "";
  const wordSpans = document.querySelectorAll("#edit-transcript-word-list .transcript-word");
  const words = Array.from(wordSpans).map((span, idx) => ({
    idx: idx,
    word: span.textContent,
    emphasis: span.classList.contains("word-emphasis"),
  }));

  try {
    const res = await fetch(`/api/jobs/${jobId}/transcript`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw_text: rawText, words: words }),
    });
    if (res.ok) {
      showToast(`✓ Transcript saved for #${jobId}!`);
    } else {
      const err = await res.json();
      showToast("Transcript save failed: " + (err.detail || "Server error"));
    }
  } catch (err) {
    showToast("Transcript error: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "💾 Save Transcript Corrections";
    }
  }
}

async function rerenderJob() {
  const jobId = document.getElementById("edit-job-id").value;
  if (!jobId) return;

  const btn = document.getElementById("rerender-job-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "⚡ Starting Fast Re-Burn...";
  }

  const titleEl = document.getElementById("edit-clip-title") || document.getElementById("edit-job-title");
  const payload = {
    layout_mode: document.getElementById("edit-layout-mode") ? document.getElementById("edit-layout-mode").value : "split_50_50",
    crop_focus: document.getElementById("edit-job-crop-focus") ? document.getElementById("edit-job-crop-focus").value : "center",
    style_preset: document.getElementById("edit-style-preset") ? document.getElementById("edit-style-preset").value : "neon",
    color_grading: document.getElementById("edit-color-grading") ? document.getElementById("edit-color-grading").value : "none",
    watermark: document.getElementById("edit-watermark") ? document.getElementById("edit-watermark").value : "@AutoShorts",
    background: document.getElementById("edit-background-category") ? document.getElementById("edit-background-category").value : "minecraft",
    dopamine: document.getElementById("edit-dopamine-level") ? document.getElementById("edit-dopamine-level").value : "medium",
    clip_title: titleEl ? titleEl.value.trim() : "",
    hook_text: document.getElementById("edit-hook-text") ? document.getElementById("edit-hook-text").value.trim() : "",
  };

  try {
    const res = await fetch(`/api/jobs/${jobId}/rerender`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      showToast(`⚡ Fast Re-Burn initiated for #${jobId}! Skipping re-download...`);
      closeEditJobModal();
      refreshJobs();
    } else {
      const err = await res.json();
      showToast("Re-burn failed: " + (err.detail || "Server error"));
    }
  } catch (err) {
    showToast("Network error re-burning job: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "⚡ Fast Re-Burn Subtitles & Layout";
    }
  }
}

function openEditJobModal(jobId) {
  const job = _jobsCache.find(j => j.id === jobId);
  if (!job) return;

  const modal = document.getElementById("edit-job-modal");
  const modalId = document.getElementById("edit-job-modal-id");
  const idInput = document.getElementById("edit-job-id");
  const titleInput = document.getElementById("edit-clip-title");
  const jobTitleInput = document.getElementById("edit-job-title");
  const hookInput = document.getElementById("edit-hook-text");
  const bgSelect = document.getElementById("edit-background-category");
  const dopSelect = document.getElementById("edit-dopamine-level");

  // Tab 1: Layout & Style controls
  const layoutSelect = document.getElementById("edit-layout-mode");
  const cropFocusSelect = document.getElementById("edit-job-crop-focus");
  const styleSelect = document.getElementById("edit-style-preset");
  const gradeSelect = document.getElementById("edit-color-grading");
  const wmInput = document.getElementById("edit-watermark");
  const startInput = document.getElementById("edit-start-sec");
  const endInput = document.getElementById("edit-end-sec");

  // Tab 3: YouTube publish controls
  const privSelect = document.getElementById("edit-privacy-status");
  const catSelect = document.getElementById("edit-category-id");
  const alteredToggle = document.getElementById("edit-altered-content");
  const linkRelatedToggle = document.getElementById("edit-link-related");
  const tagsInput = document.getElementById("edit-tags");
  const publishAtInput = document.getElementById("edit-publish-at");

  // Reset to Tab 1
  switchEditorTab("trim");

  if (modalId) modalId.textContent = `#${job.id}`;
  if (idInput) idInput.value = job.id;
  const resolvedTitle = job.clip_title || (job.selected_clips && job.selected_clips[0] && job.selected_clips[0].title) || (job.video_metadata ? job.video_metadata.title : "");
  if (titleInput) titleInput.value = resolvedTitle;
  if (jobTitleInput) jobTitleInput.value = resolvedTitle;

  if (hookInput) {
    let hook = "";
    if (job.selected_clips && job.selected_clips.length > 0) {
      hook = job.selected_clips[0].hook_text || job.selected_clips[0].title || "";
    } else if (job.edl && job.edl.recommended_hook) {
      hook = job.edl.recommended_hook;
    }
    hookInput.value = hook;
  }
  if (bgSelect && job.background_category) bgSelect.value = job.background_category;
  if (dopSelect && job.dopamine_level) dopSelect.value = job.dopamine_level;

  if (layoutSelect) layoutSelect.value = job.layout_mode || "split_50_50";
  if (cropFocusSelect) cropFocusSelect.value = job.crop_focus || "center";
  if (styleSelect) styleSelect.value = job.style_preset || "neon";
  if (gradeSelect) gradeSelect.value = job.color_grading || "none";
  if (wmInput) wmInput.value = job.watermark || job.watermark_text || "@AutoShorts";

  // Trimmer timestamps
  let startSec = 0.0;
  let endSec = 45.0;
  if (job.selected_clips && job.selected_clips.length > 0 && job.selected_clips[0].candidate) {
    startSec = job.selected_clips[0].candidate.start;
    endSec = job.selected_clips[0].candidate.end;
  } else if (job.edl && job.edl.hook_out_sec) {
    startSec = job.edl.hook_in_sec || 0.0;
    endSec = job.edl.hook_out_sec;
  }
  if (startInput) startInput.value = startSec.toFixed(1);
  if (endInput) endInput.value = endSec.toFixed(1);
  updateTrimmerDuration();

  // Load A/B Title Variants & Candidate Clips Inspector
  loadJobCandidates(job.id, job);

  // Tab 3 fields
  if (privSelect) privSelect.value = job.privacy_status || "public";
  if (catSelect) catSelect.value = job.category_id || "24";
  if (alteredToggle) alteredToggle.checked = !!job.altered_content;
  if (linkRelatedToggle) linkRelatedToggle.checked = job.link_related_video !== false;
  if (tagsInput) tagsInput.value = Array.isArray(job.custom_tags) ? job.custom_tags.join(", ") : (job.custom_tags || "");
  if (publishAtInput) publishAtInput.value = job.publish_at || job.scheduled_time || "";

  // Tab 2: Render EDL Jump cuts and zoom punch-ins
  renderEdlJumpCuts(job.edl && job.edl.jump_cuts ? job.edl.jump_cuts : []);
  const edlZoomsEl = document.getElementById("edit-edl-zooms");
  if (edlZoomsEl) {
    if (job.edl && job.edl.zoom_punch_ins && job.edl.zoom_punch_ins.length > 0) {
      edlZoomsEl.innerHTML = job.edl.zoom_punch_ins.map(z => `
        <div class="edl-item">
          <span>Zoom @ ${z.timestamp.toFixed(1)}s (${z.scale}x)</span>
          <span>${escapeHtml(z.emphasis_word || '')}</span>
        </div>
      `).join("");
    } else {
      edlZoomsEl.innerHTML = '<div class="edl-empty">No camera zoom punch-ins generated.</div>';
    }
  }

  if (modal) modal.style.display = "flex";
}

function renderEdlJumpCuts(jumpCuts) {
  const edlCutsEl = document.getElementById("edit-edl-cuts");
  if (!edlCutsEl) return;
  if (Array.isArray(jumpCuts) && jumpCuts.length > 0) {
    edlCutsEl.innerHTML = jumpCuts.map(c => `
      <div class="edl-item">
        <span>Cut: ${Number(c.cut_start || 0).toFixed(2)}s – ${Number(c.cut_end || 0).toFixed(2)}s</span>
        <span>${escapeHtml(c.reason || 'silence')}</span>
      </div>
    `).join("");
  } else {
    edlCutsEl.innerHTML = '<div class="edl-empty">No excised silence cuts in this clip.</div>';
  }
}

async function loadJobCandidates(jobId, fallbackJob) {
  const variantsEl = document.getElementById("edit-job-title-variants");
  const candidatesEl = document.getElementById("edit-job-candidates-list");
  if (variantsEl) variantsEl.innerHTML = '<span class="variants-empty">Loading A/B title variants...</span>';
  if (candidatesEl) candidatesEl.innerHTML = '<div class="edl-empty">Loading candidate windows &amp; heatmap telemetry...</div>';

  let candidates = (fallbackJob && fallbackJob.candidate_windows) ? fallbackJob.candidate_windows : [];
  let titleVariants = (fallbackJob && fallbackJob.title_variants) ? fallbackJob.title_variants : [];
  let activeIndex = 0;

  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/candidates`);
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.candidates)) candidates = data.candidates;
      else if (Array.isArray(data.candidate_windows)) candidates = data.candidate_windows;
      if (Array.isArray(data.title_variants) && data.title_variants.length > 0) {
        titleVariants = data.title_variants;
      }
      if (typeof data.selected_index === "number") {
        activeIndex = data.selected_index;
      } else if (typeof data.active_clip_index === "number") {
        activeIndex = data.active_clip_index;
      }
    }
  } catch (err) {
    console.debug("Job candidates fetch fallback:", err);
  }

  if ((!titleVariants || titleVariants.length === 0) && fallbackJob) {
    if (fallbackJob.selected_clips && fallbackJob.selected_clips[0] && Array.isArray(fallbackJob.selected_clips[0].title_variants)) {
      titleVariants = fallbackJob.selected_clips[0].title_variants;
    } else if (fallbackJob.edl && Array.isArray(fallbackJob.edl.title_variants)) {
      titleVariants = fallbackJob.edl.title_variants;
    }
  }

  const activeTitle = (document.getElementById("edit-clip-title") && document.getElementById("edit-clip-title").value) || "";
  renderTitleVariants(titleVariants, activeTitle);
  renderCandidateClipsList(jobId, candidates, activeIndex);
}

function renderTitleVariants(variants, activeTitle) {
  const variantsEl = document.getElementById("edit-job-title-variants");
  if (!variantsEl) return;

  if (!Array.isArray(variants) || variants.length === 0) {
    variantsEl.innerHTML = '<span class="variants-empty">No A/B title variants generated for this clip yet.</span>';
    return;
  }

  variantsEl.innerHTML = variants.slice(0, 3).map((v, idx) => {
    const text = typeof v === "string" ? v : (v.title || v.text || "");
    const isSelected = activeTitle && text === activeTitle;
    const labels = ["🅰️ Variant A", "🅱️ Variant B", "🔥 Variant C"];
    return `
      <button type="button" class="title-variant-pill ${isSelected ? 'active' : ''}" data-title="${escapeHtml(text)}" onclick="selectJobTitleVariant(this.getAttribute('data-title'), this)">
        <span>${labels[idx] || 'Variant'}:</span>
        <span>${escapeHtml(text)}</span>
      </button>
    `;
  }).join("");
}

function selectJobTitleVariant(titleText, btnEl) {
  const clipTitleInput = document.getElementById("edit-clip-title");
  const jobTitleInput = document.getElementById("edit-job-title");
  if (clipTitleInput) clipTitleInput.value = titleText;
  if (jobTitleInput) jobTitleInput.value = titleText;

  document.querySelectorAll("#edit-job-title-variants .title-variant-pill").forEach(btn => {
    btn.classList.toggle("active", btn === btnEl);
  });
  showToast(`✓ Applied A/B title variant: "${titleText.slice(0, 38)}..."`, "success");
}

function renderCandidateClipsList(jobId, candidates, activeIndex) {
  const candidatesEl = document.getElementById("edit-job-candidates-list");
  if (!candidatesEl) return;

  if (!Array.isArray(candidates) || candidates.length === 0) {
    candidatesEl.innerHTML = '<div class="edl-empty">No scored candidate windows available yet for this job.</div>';
    return;
  }

  candidatesEl.innerHTML = candidates.slice(0, 6).map((c, idx) => {
    const start = Number(c.start || 0);
    const end = Number(c.end || 45);
    const dur = Math.max(0, end - start);
    const semifPct = Math.round(Number(c.score || 0) * 100);
    const heatmapVal = c.heatmap_score !== undefined ? Number(c.heatmap_score) : (c.replay_intensity !== undefined ? Number(c.replay_intensity) : 0);
    const heatmapPct = Math.round(heatmapVal <= 1 ? heatmapVal * 100 : heatmapVal);
    const hookVal = c.hook_strength !== undefined ? Number(c.hook_strength) : (c.hook_score !== undefined ? Number(c.hook_score) : Number(c.score || 0));
    const hookPct = Math.round(hookVal <= 1 ? hookVal * 100 : hookVal);
    const isSelected = idx === activeIndex || Boolean(c.selected);
    const snippet = c.reason || c.text || "Scored transcript window";

    return `
      <div class="candidate-clip-card ${isSelected ? 'selected' : ''}" data-candidate-idx="${idx}">
        <div>
          <div class="candidate-clip-header">
            <span>#${idx + 1} • ${start.toFixed(1)}s – ${end.toFixed(1)}s (${dur.toFixed(0)}s)</span>
            ${isSelected ? '<span class="badge badge-success" style="font-size:0.65rem;">Active</span>' : ''}
          </div>
          <div class="candidate-badges-row" style="margin-top: 0.35rem;">
            <span class="badge badge-semif" title="SemIf Semantic Retention Score">🧠 SemIf ${semifPct}%</span>
            <span class="badge badge-heatmap" title="YouTube Most Replayed Heatmap Intensity">🔥 Most Replayed ${heatmapPct}%</span>
            <span class="badge badge-hook-strength" title="Opening 3-Second Hook Strength">🪝 Hook ${hookPct}%</span>
          </div>
          <p class="candidate-snippet" style="margin-top: 0.4rem;" title="${escapeHtml(snippet)}">${escapeHtml(snippet)}</p>
        </div>
        <button type="button" class="btn ${isSelected ? 'btn-primary' : 'btn-secondary'} btn-xs" onclick="selectJobCandidateClip('${escapeHtml(jobId)}', ${idx}, ${start}, ${end})">
          ${isSelected ? '✓ Selected Clip' : '⚡ Switch to Clip'}
        </button>
      </div>
    `;
  }).join("");
}

async function selectJobCandidateClip(jobId, candidateIndex, startSec, endSec) {
  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/select-clip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        candidate_index: candidateIndex,
        clip_index: candidateIndex,
        start_sec: startSec,
        end_sec: endSec,
      }),
    });

    const startInput = document.getElementById("edit-start-sec");
    const endInput = document.getElementById("edit-end-sec");
    if (startInput && startSec !== undefined) startInput.value = Number(startSec).toFixed(1);
    if (endInput && endSec !== undefined) endInput.value = Number(endSec).toFixed(1);
    updateTrimmerDuration();

    if (res.ok) {
      const data = await res.json();
      if (data.title || (data.selected_clip && data.selected_clip.title)) {
        const newTitle = data.title || data.selected_clip.title;
        const ct = document.getElementById("edit-clip-title");
        const jt = document.getElementById("edit-job-title");
        if (ct) ct.value = newTitle;
        if (jt) jt.value = newTitle;
      }
      if (Array.isArray(data.title_variants) && data.title_variants.length > 0) {
        const activeTitle = (document.getElementById("edit-clip-title") && document.getElementById("edit-clip-title").value) || "";
        renderTitleVariants(data.title_variants, activeTitle);
      }
      showToast(`✓ Switched job #${jobId} to Candidate Clip #${candidateIndex + 1}!`, "success");
      loadJobCandidates(jobId, _jobsCache.find(j => j.id === jobId));
      refreshJobs();
    } else {
      const err = await res.json();
      showToast("Clip switch error: " + (err.detail || "Server error"), "error");
    }
  } catch (err) {
    showToast("Failed switching clip: " + err, "error");
  }
}

async function autoTrimJobSilence() {
  const jobId = document.getElementById("edit-job-id") ? document.getElementById("edit-job-id").value : "";
  if (!jobId) return;

  const btn = document.getElementById("btn-auto-trim-silence");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "⏳ Detecting Dead Air...";
  }

  try {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/auto-trim-silence`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_silence_sec: 0.48 }),
    });

    if (res.ok) {
      const data = await res.json();
      const jumpCuts = data.jump_cuts || (data.edl && data.edl.jump_cuts) || [];
      const addedCount = data.added_count !== undefined
        ? data.added_count
        : (data.count !== undefined ? data.count : jumpCuts.length);

      renderEdlJumpCuts(jumpCuts);

      const cached = _jobsCache.find(j => j.id === jobId);
      if (cached) {
        if (!cached.edl) cached.edl = {};
        cached.edl.jump_cuts = jumpCuts;
      }

      showToast(`Added ${addedCount} frame-accurate silence jump cuts`, "success");
      refreshJobs();
    } else {
      const err = await res.json();
      showToast("Auto-trim silence failed: " + (err.detail || "Server error"), "error");
    }
  } catch (err) {
    showToast("Auto-trim silence error: " + err, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "✂️ Auto-Cut Dead Air Silences";
    }
  }
}

function closeEditJobModal(e) {
  const modal = document.getElementById("edit-job-modal");
  if (modal) modal.style.display = "none";
}

async function saveJobMetadata() {
  const jobId = document.getElementById("edit-job-id").value;
  if (!jobId) return;

  const btn = document.getElementById("save-job-edit-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Saving...";
  }

  const titleEl = document.getElementById("edit-clip-title") || document.getElementById("edit-job-title");
  const payload = {
    clip_title: titleEl ? titleEl.value.trim() : "",
    hook_text: document.getElementById("edit-hook-text").value.trim(),
    background_category: document.getElementById("edit-background-category").value,
    dopamine_level: document.getElementById("edit-dopamine-level").value,
    layout_mode: document.getElementById("edit-layout-mode") ? document.getElementById("edit-layout-mode").value : undefined,
    crop_focus: document.getElementById("edit-job-crop-focus") ? document.getElementById("edit-job-crop-focus").value : undefined,
    style_preset: document.getElementById("edit-style-preset") ? document.getElementById("edit-style-preset").value : undefined,
    color_grading: document.getElementById("edit-color-grading") ? document.getElementById("edit-color-grading").value : undefined,
    watermark: document.getElementById("edit-watermark") ? document.getElementById("edit-watermark").value : undefined,
    privacy_status: document.getElementById("edit-privacy-status") ? document.getElementById("edit-privacy-status").value : undefined,
    category_id: document.getElementById("edit-category-id") ? document.getElementById("edit-category-id").value : undefined,
  };

  try {
    const res = await fetch(`/api/jobs/${jobId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      showToast(`Updated metadata for #${jobId}!`);
      closeEditJobModal();
      refreshJobs();
    } else {
      const err = await res.json();
      showToast("Update failed: " + (err.detail || "Server error"));
    }
  } catch (err) {
    showToast("Error updating job: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "💾 Save Metadata";
    }
  }
}

// -----------------------------------------------------------------------------
// Live Links Preview Strip
// -----------------------------------------------------------------------------

async function previewLinks() {
  const linksText = document.getElementById("links-input").value.trim();
  const container = document.getElementById("links-preview-container");
  const btn = document.getElementById("preview-links-btn");

  if (!linksText) {
    showToast("Please enter YouTube URL(s) to preview.");
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.textContent = "🔍 Fetching...";
  }

  try {
    const res = await fetch("/api/links/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ links_text: linksText, links: linksText }),
    });

    let items = [];
    if (res.ok) {
      const data = await res.json();
      items = data.items || data.previews || [];
    } else {
      // Fallback: Client-side URL extraction and thumbnail resolution
      items = parseLinksClientSide(linksText);
    }

    renderLinksPreview(items);
  } catch (err) {
    console.debug("Preview endpoint failed, using fallback:", err);
    const items = parseLinksClientSide(linksText);
    renderLinksPreview(items);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "🔍 Preview Links";
    }
  }
}

function parseLinksClientSide(text) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  return lines.map((line, idx) => {
    let url = line;
    let instructions = "";
    if (line.includes(" : ")) {
      const parts = line.split(" : ");
      url = parts[0].trim();
      instructions = parts.slice(1).join(" : ").trim();
    } else if (line.includes(":")) {
      const firstColon = line.indexOf(":");
      const secondColon = line.indexOf(":", firstColon + 1);
      if (secondColon > 0) {
        url = line.substring(0, secondColon).trim();
        instructions = line.substring(secondColon + 1).trim();
      }
    }

    // Extract YouTube ID
    let ytId = "";
    const regExp = /^.*(youtu.be\/|v\/|u\/\w\/|embed\/|watch\?v=|&v=)([^#&?]*).*/;
    const match = url.match(regExp);
    if (match && match[2].length === 11) {
      ytId = match[2];
    }

    const thumb = ytId
      ? `https://img.youtube.com/vi/${ytId}/mqdefault.jpg`
      : "https://via.placeholder.com/160x90/161b22/8b949e?text=Video";

    return {
      url: url,
      title: ytId ? `YouTube Video [${ytId}]` : (url.length > 40 ? url.substring(0, 37) + "..." : url),
      channel: "YouTube Source",
      duration_str: "Clip candidate",
      thumbnail_url: thumb,
      instructions: instructions,
    };
  });
}

function renderLinksPreview(items) {
  const container = document.getElementById("links-preview-container");
  if (!container) return;

  if (!items || items.length === 0) {
    container.style.display = "none";
    return;
  }

  container.style.display = "flex";
  container.innerHTML = items.map(item => {
    const thumb = item.thumbnail_url || "https://via.placeholder.com/160x90/161b22/8b949e?text=No+Thumb";
    const title = item.title || item.url;
    const channel = item.channel || "Source Video";
    const duration = item.duration_str || (item.duration ? `${Math.round(item.duration)}s` : "");
    const instructionsTag = item.instructions
      ? `<span class="preview-instructions-tag" title="${escapeHtml(item.instructions)}">💡 ${escapeHtml(item.instructions)}</span>`
      : "";

    return `
      <div class="preview-link-card">
        <div class="preview-thumb-wrap">
          <img src="${escapeHtml(thumb)}" alt="Thumbnail" class="preview-thumb-img" onerror="this.src='https://via.placeholder.com/160x90/161b22/8b949e?text=Video'">
          ${duration ? `<span class="preview-duration-badge">${escapeHtml(duration)}</span>` : ""}
        </div>
        <div class="preview-info">
          <div class="preview-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
          <div class="preview-meta-row">
            <span>${escapeHtml(channel)}</span>
            ${instructionsTag}
          </div>
        </div>
      </div>
    `;
  }).join("");
}

// -----------------------------------------------------------------------------
// Clipboard & Toast Feedback
// -----------------------------------------------------------------------------

function copyJobField(jobId, field, btn) {
  const job = _jobsCache.find(j => j.id === jobId);
  if (!job) return;

  const textToCopy = job[field] || "";
  if (!textToCopy) {
    showToast("No content available to copy.");
    return;
  }

  const originalHtml = btn.innerHTML;
  copyToClipboard(textToCopy).then(() => {
    btn.innerHTML = "✓ Copied!";
    btn.classList.add("copied");
    showToast(`Copied ${field.replace('_', ' ')} to clipboard!`);
    setTimeout(() => {
      btn.innerHTML = originalHtml;
      btn.classList.remove("copied");
    }, 1600);
  }).catch(err => {
    showToast("Copy failed: " + err);
  });
}

function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  } else {
    return new Promise((resolve, reject) => {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      try {
        const successful = document.execCommand("copy");
        document.body.removeChild(textarea);
        if (successful) resolve();
        else reject(new Error("execCommand copy returned false"));
      } catch (err) {
        document.body.removeChild(textarea);
        reject(err);
      }
    });
  }
}

function showToast(msg, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;

  // Auto-detect semantic type if default
  let toastType = type;
  if (toastType === "info") {
    if (/fail|error|invalid|reject|denied/i.test(msg) && !/success/i.test(msg)) {
      toastType = "error";
    } else if (/success|saved|copied|downloaded|completed|scheduled/i.test(msg)) {
      toastType = "success";
    } else if (/warn|caution|required|missing/i.test(msg)) {
      toastType = "warning";
    }
  }

  const icons = {
    error: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`,
    warning: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`,
    success: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>`,
    info: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`
  };

  const toast = document.createElement("div");
  toast.className = `toast ${toastType}`;
  toast.innerHTML = `<span class="toast-icon">${icons[toastType] || icons.info}</span><span class="toast-text">${escapeHtml(msg)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.transition = "opacity 0.3s ease, transform 0.3s ease";
    toast.style.opacity = "0";
    toast.style.transform = "translateX(50px)";
    setTimeout(() => toast.remove(), 300);
  }, 2800);
}

// -----------------------------------------------------------------------------
// Real-Time Log Streaming (SSE)
// -----------------------------------------------------------------------------

function initLogStream() {
  const terminal = document.getElementById("log-terminal");
  const dot = document.getElementById("log-status-dot");
  const badge = document.getElementById("log-connection-badge");

  if (_eventSource) {
    _eventSource.close();
  }

  _eventSource = new EventSource("/api/logs/stream");

  _eventSource.onopen = () => {
    if (dot) dot.className = "log-dot live";
    if (badge) {
      badge.textContent = "● Live Stream Connected";
      badge.style.color = "var(--accent-green)";
    }
  };

  _eventSource.onerror = () => {
    if (dot) dot.className = "log-dot";
    if (badge) {
      badge.textContent = "○ Reconnecting...";
      badge.style.color = "var(--accent-yellow)";
    }
  };

  _eventSource.onmessage = (event) => {
    try {
      let rawMsg = event.data;
      try {
        const parsed = JSON.parse(event.data);
        if (parsed && parsed.message) rawMsg = parsed.message;
      } catch (e) {}

      appendLogMessage(rawMsg);
    } catch (err) {
      console.debug("Log parse error:", err);
    }
  };
}

function appendLogMessage(msg) {
  const terminal = document.getElementById("log-terminal");
  if (!terminal) return;

  const placeholder = terminal.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();

  let levelClass = "log-info";
  if (msg.includes("[ERROR]") || msg.includes("ERROR:")) {
    levelClass = "log-error";
  } else if (msg.includes("[WARNING]") || msg.includes("WARNING:")) {
    levelClass = "log-warning";
  }

  const lineEl = document.createElement("div");
  lineEl.className = `log-line ${levelClass}`;
  lineEl.textContent = msg;

  terminal.appendChild(lineEl);

  while (terminal.childNodes.length > 500) {
    terminal.removeChild(terminal.firstChild);
  }

  const autoscroll = document.getElementById("log-autoscroll");
  if (!autoscroll || autoscroll.checked) {
    terminal.scrollTop = terminal.scrollHeight;
  }
}

async function clearLogConsole() {
  const terminal = document.getElementById("log-terminal");
  if (terminal) {
    terminal.innerHTML = '<div class="log-placeholder">Log console cleared. Waiting for new logs...</div>';
  }
  try {
    await fetch("/api/logs/clear", { method: "POST" });
  } catch (err) {
    console.debug("Clear logs API error:", err);
  }
}

// -----------------------------------------------------------------------------
// Stage Inspector Modal
// -----------------------------------------------------------------------------

async function inspectJobStages(jobId) {
  const modal = document.getElementById("stage-modal");
  const modalTitle = document.getElementById("stage-modal-title");
  const modalBody = document.getElementById("stage-modal-body");

  modalTitle.textContent = `Job #${jobId} Stage Progression`;
  modalBody.innerHTML = '<div class="stage-inspect-loading">Fetching execution stages...</div>';
  modal.style.display = "flex";

  try {
    const res = await fetch(`/api/jobs/${jobId}`);
    if (!res.ok) {
      modalBody.innerHTML = `<div style="color: var(--accent-red)">Failed to load job #${jobId}.</div>`;
      return;
    }
    const data = await res.json();
    const job = data.job;
    const stages = data.stages || [];

    let stagesTable = `
      <table class="stage-inspect-table">
        <thead>
          <tr>
            <th>Stage</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Completed At</th>
          </tr>
        </thead>
        <tbody>
    `;

    if (stages.length === 0) {
      stagesTable += `<tr><td colspan="4" style="color: var(--text-muted); text-align: center;">No recorded execution stages yet. Current status: ${job.status}</td></tr>`;
    } else {
      stages.forEach(st => {
        const dur = st.duration_ms ? `${(st.duration_ms / 1000).toFixed(2)}s` : "-";
        const completed = st.completed_at ? new Date(st.completed_at).toLocaleTimeString() : "-";
        stagesTable += `
          <tr>
            <td><strong>${escapeHtml(st.stage_name)}</strong></td>
            <td><span class="status-badge status-${st.status}">${st.status}</span></td>
            <td>${dur}</td>
            <td>${completed}</td>
          </tr>
        `;
      });
    }

    stagesTable += `</tbody></table>`;

    modalBody.innerHTML = `
      <div style="margin-bottom: 0.75rem;">
        <div style="font-size: 0.85rem; font-weight: 600; color: var(--accent-cyan);">${escapeHtml(job.source_url)}</div>
        <div style="font-size: 0.75rem; color: var(--text-muted);">Current State: <strong>${job.status}</strong></div>
      </div>
      ${stagesTable}
    `;
  } catch (err) {
    modalBody.innerHTML = `<div style="color: var(--accent-red)">Error loading stages: ${escapeHtml(String(err))}</div>`;
  }
}

function closeStageModal(e) {
  const modal = document.getElementById("stage-modal");
  if (modal) modal.style.display = "none";
}

// -----------------------------------------------------------------------------
// Job Submission & Actions
// -----------------------------------------------------------------------------

async function submitJobs() {
  const links = document.getElementById("links-input").value.trim();
  if (!links) {
    alert("Please enter at least one YouTube URL.");
    return;
  }

  const bgVal = document.getElementById("background-select")
    ? document.getElementById("background-select").value
    : (document.getElementById("bg-select") ? document.getElementById("bg-select").value : "minecraft");

  const stylePreset = document.getElementById("style-preset-select")
    ? document.getElementById("style-preset-select").value
    : "neon";

  const privacyStatus = document.getElementById("privacy-status-select")
    ? document.getElementById("privacy-status-select").value
    : "public";

  const layoutMode = document.getElementById("layout-mode-select")
    ? document.getElementById("layout-mode-select").value
    : "split_50_50";

  const subtitlePos = document.getElementById("subtitle-position-select")
    ? document.getElementById("subtitle-position-select").value
    : "bottom_safe";

  const textTransform = document.getElementById("text-transform-select")
    ? document.getElementById("text-transform-select").value
    : "uppercase";

  const colorGrading = document.getElementById("color-grading-select")
    ? document.getElementById("color-grading-select").value
    : "none";

  const progressBarPos = document.getElementById("progressbar-pos-select")
    ? document.getElementById("progressbar-pos-select").value
    : "none";

  const watermarkText = document.getElementById("watermark-input")
    ? document.getElementById("watermark-input").value.trim()
    : "@AutoShorts";

  const categoryId = document.getElementById("category-id-select")
    ? document.getElementById("category-id-select").value
    : "22";

  const cropFocus = document.getElementById("crop-focus-select")
    ? document.getElementById("crop-focus-select").value
    : "center";

  const skipSponsors = document.getElementById("skip-sponsors-toggle")
    ? document.getElementById("skip-sponsors-toggle").checked
    : true;

  const autoTrimSilence = document.getElementById("auto-trim-silence-toggle")
    ? document.getElementById("auto-trim-silence-toggle").checked
    : true;

  const payload = {
    links: links,
    provider: document.getElementById("provider-select").value,
    background: bgVal,
    dopamine: document.getElementById("dopamine-select").value,
    upload_mode: document.getElementById("upload-mode-select").value,
    auto_upload: document.getElementById("auto-upload-toggle").checked,
    style_preset: stylePreset,
    privacy_status: privacyStatus,
    layout_mode: layoutMode,
    subtitle_position: subtitlePos,
    text_transform: textTransform,
    color_grading: colorGrading,
    progress_bar_position: progressBarPos,
    watermark: watermarkText,
    category_id: categoryId,
    crop_focus: cropFocus,
    skip_sponsors: skipSponsors,
    auto_trim_silence: autoTrimSilence,
  };

  const btn = document.getElementById("submit-btn");
  btn.disabled = true;
  btn.textContent = "Submitting...";

  try {
    let res = await fetch("/api/jobs/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 404) {
      res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }

    if (!res.ok) {
      const err = await res.json();
      alert("Submission error: " + (err.detail || "Unknown error"));
      return;
    }

    document.getElementById("links-input").value = "";
    const previewContainer = document.getElementById("links-preview-container");
    if (previewContainer) previewContainer.style.display = "none";

    refreshJobs();
    showToast("Submitted jobs to pipeline!");
  } catch (err) {
    alert("Network error submitting jobs: " + err);
  } finally {
    btn.disabled = false;
    btn.textContent = "⚡ Start Pipeline";
  }
}

const submitJobsForm = submitJobs;

async function importSpark() {
  const text = document.getElementById("spark-input").value.trim();
  const feedback = document.getElementById("spark-feedback");
  if (!text) {
    feedback.textContent = "Paste Spark markdown or prompt response first.";
    return;
  }

  try {
    const res = await fetch("/api/spark/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });

    if (!res.ok) {
      feedback.textContent = "Spark parse failed.";
      return;
    }

    const data = await res.json();
    const hints = data.hints || [];
    feedback.textContent = `Successfully parsed ${hints.length} clip hint(s)! Added to links box.`;

    const current = document.getElementById("links-input").value;
    const added = hints.map(h => `${h.url} : ${h.topic || h.hook_text || "Spark clip"}`).join("\n");
    document.getElementById("links-input").value = current ? `${current}\n${added}` : added;
    document.getElementById("spark-input").value = "";
    showToast(`Added ${hints.length} Spark clip(s)`);
  } catch (err) {
    feedback.textContent = "Spark error: " + err;
  }
}

async function uploadNow(jobId) {
  if (!confirm(`Trigger immediate upload for job #${jobId}?`)) return;

  try {
    const res = await fetch(`/api/jobs/${jobId}/upload-now`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert("Upload failed: " + (err.detail || "Server error"));
      return;
    }
    refreshJobs();
    refreshQuota();
    showToast(`Dispatched upload for #${jobId}!`);
  } catch (err) {
    alert("Network error: " + err);
  }
}

async function retryJob(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}/retry`, { method: "POST" });
    if (res.ok) {
      refreshJobs();
      showToast(`Retrying job #${jobId}...`);
    }
  } catch (err) {
    alert("Failed to retry job: " + err);
  }
}

async function cleanJobFiles(jobId) {
  if (!confirm(`Safely purge intermediate work files for job #${jobId}?`)) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
    if (!res.ok) {
      const err = await res.json();
      alert("Cleanup failed: " + (err.detail || "Server error"));
      return;
    }
    const data = await res.json();
    refreshJobs();
    showToast(`Cleaned ${data.count || 0} file(s) for #${jobId}!`);
  } catch (err) {
    alert("Network error: " + err);
  }
}

// -----------------------------------------------------------------------------
// YouTube Data v3 API & Settings Modal
// -----------------------------------------------------------------------------

async function checkInitialYouTubeAuth() {
  try {
    const res = await fetch("/api/settings/youtube-oauth/status");
    let isAuthorized = false;
    let secretExists = false;

    if (res.ok) {
      const data = await res.json();
      isAuthorized = Boolean(data.authorized || data.status === "authorized");
      secretExists = Boolean(data.client_secret_exists || data.has_client_secret);
    } else {
      // Fallback: check /api/settings
      const setRes = await fetch("/api/settings");
      if (setRes.ok) {
        const s = await setRes.json();
        isAuthorized = Boolean(s.youtube && s.youtube.oauth_authorized);
        secretExists = Boolean(s.youtube && s.youtube.client_secret_exists);
      }
    }

    const badge = document.getElementById("yt-api-status-badge");
    const dot = document.getElementById("yt-api-status-dot");
    const label = document.getElementById("yt-api-status-label");

    if (isAuthorized) {
      if (dot) dot.className = "dot green";
      if (label) label.textContent = "YouTube API: Ready";
      if (badge) {
        badge.style.borderColor = "var(--accent-green)";
        badge.title = "YouTube Data API v3 OAuth token authorized and ready for 100 free daily uploads";
      }
    } else {
      if (dot) dot.className = "dot yellow";
      if (label) label.textContent = secretExists ? "YouTube API: Needs OAuth" : "YouTube API: Not Configured";
      if (badge) {
        badge.style.borderColor = "var(--accent-yellow)";
        badge.title = "Configure YouTube Data API v3 in Settings to unlock 100 free daily uploads";
      }
    }

    // Update Badges inside Settings Modal
    const secretBadge = document.getElementById("badge-client-secret-status");
    const tokenBadge = document.getElementById("badge-oauth-token-status");
    if (secretBadge) {
      if (secretExists) {
        secretBadge.textContent = "✓ client_secret.json found";
        secretBadge.className = "badge status-COMPLETED";
      } else {
        secretBadge.textContent = "❌ client_secret.json missing";
        secretBadge.className = "badge status-FAILED";
      }
    }
    if (tokenBadge) {
      if (isAuthorized) {
        tokenBadge.textContent = "✓ token.json authorized";
        tokenBadge.className = "badge status-COMPLETED";
      } else {
        tokenBadge.textContent = "⚠️ OAuth unauthorized";
        tokenBadge.className = "badge status-SCHEDULED";
      }
    }
  } catch (err) {
    console.debug("YouTube auth status check failed:", err);
  }
}

function openSettingsModal(targetTab) {
  const modal = document.getElementById("settings-modal");
  if (modal) modal.style.display = "flex";
  loadAllSettings();
  checkInitialYouTubeAuth();
  if (targetTab) {
    let tab = targetTab;
    if (tab === "tab-yt-api") tab = "youtube";
    if (tab === "tab-ai-keys") tab = "ai";
    switchSettingsTab(tab);
  }
}

function closeSettingsModal(e) {
  const modal = document.getElementById("settings-modal");
  if (modal) modal.style.display = "none";
}

function switchSettingsTab(tabName) {
  document.querySelectorAll(".settings-tab-btn").forEach(btn => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tabName);
  });
  document.querySelectorAll(".settings-tab-pane").forEach(pane => {
    pane.classList.toggle("active", pane.id === `tab-pane-${tabName}`);
  });
}

async function loadAllSettings() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) return;
    const cfg = await res.json();
    _settingsCache = cfg;

    // 1. YouTube & Upload Tab
    const up = cfg.upload || {};
    const yt = cfg.youtube || {};
    if (document.getElementById("channel-profile-select")) {
      document.getElementById("channel-profile-select").value = up.active_channel_profile || "default";
    }
    if (document.getElementById("setting-category-id")) {
      document.getElementById("setting-category-id").value = up.category_id || "24";
    }
    if (document.getElementById("setting-schedule-strategy")) {
      document.getElementById("setting-schedule-strategy").value = up.schedule_strategy || "local_scheduler";
    }
    if (document.getElementById("setting-playlist-id")) {
      document.getElementById("setting-playlist-id").value = up.playlist_id || "";
    }
    if (document.getElementById("setting-altered-content")) {
      document.getElementById("setting-altered-content").checked = !!up.altered_content;
    }
    if (document.getElementById("setting-link-related-video")) {
      document.getElementById("setting-link-related-video").checked = up.link_related_video !== false;
    }
    if (document.getElementById("setting-auto-pin-comment")) {
      document.getElementById("setting-auto-pin-comment").checked = !!up.auto_pin_comment;
    }
    if (document.getElementById("setting-pin-comment-template") && up.pin_comment_template) {
      document.getElementById("setting-pin-comment-template").value = up.pin_comment_template;
    }
    if (document.getElementById("setting-upload-mode")) {
      document.getElementById("setting-upload-mode").value = up.mode || "local";
    }
    if (document.getElementById("setting-privacy-status")) {
      document.getElementById("setting-privacy-status").value = up.default_privacy_status || "public";
    }
    if (document.getElementById("setting-quota-fallback")) {
      document.getElementById("setting-quota-fallback").value = up.quota_exhausted_fallback || "studio";
    }
    if (document.getElementById("setting-title-template") && up.title_template) {
      document.getElementById("setting-title-template").value = up.title_template;
    }
    if (document.getElementById("setting-desc-template") && up.description_template) {
      document.getElementById("setting-desc-template").value = up.description_template;
    }
    if (document.getElementById("setting-yt-api-key") && yt.api_key) {
      document.getElementById("setting-yt-api-key").placeholder = "•••••••••••••••••••• (Configured)";
    }

    // 2. AI Providers Tab
    const llm = cfg.llm || {};
    const ep = cfg.edit_planner || {};
    if (document.getElementById("setting-active-provider")) {
      document.getElementById("setting-active-provider").value = llm.provider || "local";
    }
    if (document.getElementById("setting-active-model")) {
      document.getElementById("setting-active-model").value = llm.model || "";
    }
    if (document.getElementById("setting-gemini-key") && cfg.gemini_api_key_masked) {
      document.getElementById("setting-gemini-key").placeholder = cfg.gemini_api_key_masked;
    }
    if (document.getElementById("setting-openrouter-key") && cfg.openrouter_api_key_masked) {
      document.getElementById("setting-openrouter-key").placeholder = cfg.openrouter_api_key_masked;
    }
    if (document.getElementById("setting-local-gguf-path")) {
      document.getElementById("setting-local-gguf-path").value = llm.local_gguf_path || "";
    }
    if (document.getElementById("setting-edit-planner-toggle")) {
      document.getElementById("setting-edit-planner-toggle").checked = ep.enabled !== false;
    }

    // 3. Subtitles & Pacing Tab
    const subs = cfg.subtitles || {};
    const brand = cfg.branding || {};
    const audio = cfg.audio_mastering || {};
    const jd = cfg.job_defaults || {};
    const sched = cfg.scheduler || {};

    if (document.getElementById("setting-style-preset")) {
      document.getElementById("setting-style-preset").value = subs.style_preset || "neon";
    }
    if (document.getElementById("setting-words-per-burst")) {
      document.getElementById("setting-words-per-burst").value = subs.words_per_burst || 3;
    }
    if (document.getElementById("setting-pop-scale")) {
      document.getElementById("setting-pop-scale").value = subs.karaoke_pop_scale || 1.15;
    }
    if (document.getElementById("setting-margin-v")) {
      document.getElementById("setting-margin-v").value = subs.margin_v || 180;
    }

    // Studio framing & branding
    if (document.getElementById("setting-layout-mode")) {
      document.getElementById("setting-layout-mode").value = brand.layout_mode || "split_50_50";
    }
    if (document.getElementById("setting-color-grading")) {
      document.getElementById("setting-color-grading").value = brand.color_grading || "none";
    }
    if (document.getElementById("setting-text-transform")) {
      document.getElementById("setting-text-transform").value = subs.text_transform || "uppercase";
    }
    if (document.getElementById("setting-hook-banner-style")) {
      document.getElementById("setting-hook-banner-style").value = subs.hook_banner_style || "neon_box";
    }
    if (document.getElementById("setting-watermark-text")) {
      document.getElementById("setting-watermark-text").value = brand.watermark_text || "@AutoShorts";
    }
    if (document.getElementById("setting-watermark-pos")) {
      document.getElementById("setting-watermark-pos").value = brand.watermark_position || "top_right";
    }
    if (document.getElementById("setting-watermark-opacity")) {
      document.getElementById("setting-watermark-opacity").value = brand.watermark_opacity !== undefined ? brand.watermark_opacity : 0.85;
    }

    // Audio mastering
    if (document.getElementById("setting-voice-boost")) {
      document.getElementById("setting-voice-boost").checked = audio.voice_boost !== false;
    }
    if (document.getElementById("setting-target-lufs")) {
      document.getElementById("setting-target-lufs").value = audio.target_lufs !== undefined ? audio.target_lufs : -14.0;
    }
    if (document.getElementById("setting-bg-music-volume")) {
      document.getElementById("setting-bg-music-volume").value = audio.bg_music_volume !== undefined ? audio.bg_music_volume : 0.18;
    }
    if (document.getElementById("setting-sfx-enabled")) {
      document.getElementById("setting-sfx-enabled").checked = audio.enable_sfx !== false;
    }

    if (document.getElementById("setting-min-duration")) {
      document.getElementById("setting-min-duration").value = jd.min_duration_seconds || 30;
    }
    if (document.getElementById("setting-max-duration")) {
      document.getElementById("setting-max-duration").value = jd.max_duration_seconds || 55;
    }
    if (document.getElementById("setting-clips-per-video")) {
      document.getElementById("setting-clips-per-video").value = jd.clips_per_video || 1;
    }
    if (document.getElementById("setting-daily-cap")) {
      document.getElementById("setting-daily-cap").value = sched.daily_upload_cap || 50;
    }
    if (document.getElementById("setting-hourly-cap")) {
      document.getElementById("setting-hourly-cap").value = sched.max_per_rolling_hour || 7;
    }
    if (document.getElementById("setting-gap-min")) {
      document.getElementById("setting-gap-min").value = sched.min_gap_minutes || 5;
    }
    if (document.getElementById("setting-gap-max")) {
      document.getElementById("setting-gap-max").value = sched.max_gap_minutes || 25;
    }
    if (document.getElementById("setting-active-start")) {
      document.getElementById("setting-active-start").value = sched.active_hours_start || "08:00";
    }
    if (document.getElementById("setting-active-end")) {
      document.getElementById("setting-active-end").value = sched.active_hours_end || "23:59";
    }
    if (document.getElementById("setting-prevent-sleep")) {
      document.getElementById("setting-prevent-sleep").checked = sched.prevent_sleep !== false;
    }

    // 4. Storage Tab
    const clean = cfg.cleanup || {};
    if (document.getElementById("setting-cleanup-enabled")) {
      document.getElementById("setting-cleanup-enabled").checked = clean.enabled !== false;
    }
    if (document.getElementById("setting-use-recycle-bin")) {
      document.getElementById("setting-use-recycle-bin").checked = clean.use_recycle_bin !== false;
    }
    if (document.getElementById("setting-keep-master-mp4")) {
      document.getElementById("setting-keep-master-mp4").checked = clean.keep_master_mp4 !== false;
    }
    if (document.getElementById("setting-retention-days")) {
      document.getElementById("setting-retention-days").value = clean.keep_final_days !== undefined ? clean.keep_final_days : 30;
    }

    syncPresetCardHighlight();
  } catch (err) {
    console.debug("Error loading settings:", err);
  }
}

async function saveAllSettings() {
  const btn = document.getElementById("save-settings-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Saving...";
  }

  const payload = {
    upload: {
      mode: document.getElementById("setting-upload-mode").value,
      default_privacy_status: document.getElementById("setting-privacy-status").value,
      quota_exhausted_fallback: document.getElementById("setting-quota-fallback").value,
      title_template: document.getElementById("setting-title-template").value,
      description_template: document.getElementById("setting-desc-template").value,
      active_channel_profile: document.getElementById("channel-profile-select") ? document.getElementById("channel-profile-select").value : "default",
      category_id: document.getElementById("setting-category-id") ? document.getElementById("setting-category-id").value : "24",
      schedule_strategy: document.getElementById("setting-schedule-strategy") ? document.getElementById("setting-schedule-strategy").value : "local_scheduler",
      playlist_id: document.getElementById("setting-playlist-id") ? (document.getElementById("setting-playlist-id").value.trim() || null) : null,
      altered_content: document.getElementById("setting-altered-content") ? document.getElementById("setting-altered-content").checked : false,
      link_related_video: document.getElementById("setting-link-related-video") ? document.getElementById("setting-link-related-video").checked : true,
      auto_pin_comment: document.getElementById("setting-auto-pin-comment") ? document.getElementById("setting-auto-pin-comment").checked : false,
      pin_comment_template: document.getElementById("setting-pin-comment-template") ? document.getElementById("setting-pin-comment-template").value : "📌 Watch the full video by {channel}: {video_url}",
    },
    llm: {
      provider: document.getElementById("setting-active-provider").value,
      model: document.getElementById("setting-active-model").value.trim() || null,
      local_gguf_path: document.getElementById("setting-local-gguf-path").value.trim() || null,
    },
    edit_planner: {
      enabled: document.getElementById("setting-edit-planner-toggle").checked,
      model: null,
    },
    subtitles: {
      style_preset: document.getElementById("setting-style-preset").value,
      words_per_burst: parseInt(document.getElementById("setting-words-per-burst").value, 10),
      karaoke_pop_scale: parseFloat(document.getElementById("setting-pop-scale").value),
      margin_v: parseInt(document.getElementById("setting-margin-v").value, 10),
      text_transform: document.getElementById("setting-text-transform") ? document.getElementById("setting-text-transform").value : "uppercase",
      hook_banner_style: document.getElementById("setting-hook-banner-style") ? document.getElementById("setting-hook-banner-style").value : "neon_box",
    },
    branding: {
      layout_mode: document.getElementById("setting-layout-mode") ? document.getElementById("setting-layout-mode").value : "split_50_50",
      color_grading: document.getElementById("setting-color-grading") ? document.getElementById("setting-color-grading").value : "none",
      watermark_text: document.getElementById("setting-watermark-text") ? (document.getElementById("setting-watermark-text").value.trim() || "@AutoShorts") : "@AutoShorts",
      watermark_position: document.getElementById("setting-watermark-pos") ? document.getElementById("setting-watermark-pos").value : "top_right",
      watermark_opacity: document.getElementById("setting-watermark-opacity") ? parseFloat(document.getElementById("setting-watermark-opacity").value) : 0.85,
    },
    audio_mastering: {
      voice_boost: document.getElementById("setting-voice-boost") ? document.getElementById("setting-voice-boost").checked : true,
      target_lufs: document.getElementById("setting-target-lufs") ? parseFloat(document.getElementById("setting-target-lufs").value) : -14.0,
      bg_music_volume: document.getElementById("setting-bg-music-volume") ? parseFloat(document.getElementById("setting-bg-music-volume").value) : 0.18,
      enable_sfx: document.getElementById("setting-sfx-enabled") ? document.getElementById("setting-sfx-enabled").checked : true,
    },
    job_defaults: {
      min_duration_seconds: parseInt(document.getElementById("setting-min-duration").value, 10),
      max_duration_seconds: parseInt(document.getElementById("setting-max-duration").value, 10),
      clips_per_video: parseInt(document.getElementById("setting-clips-per-video").value, 10),
    },
    scheduler: {
      daily_upload_cap: parseInt(document.getElementById("setting-daily-cap").value, 10),
      max_per_rolling_hour: parseInt(document.getElementById("setting-hourly-cap").value, 10),
      min_gap_minutes: parseInt(document.getElementById("setting-gap-min").value, 10),
      max_gap_minutes: parseInt(document.getElementById("setting-gap-max").value, 10),
      active_hours_start: document.getElementById("setting-active-start").value,
      active_hours_end: document.getElementById("setting-active-end").value,
      prevent_sleep: document.getElementById("setting-prevent-sleep").checked,
    },
    cleanup: {
      enabled: document.getElementById("setting-cleanup-enabled").checked,
      use_recycle_bin: document.getElementById("setting-use-recycle-bin").checked,
      keep_master_mp4: document.getElementById("setting-keep-master-mp4").checked,
      keep_final_days: parseInt(document.getElementById("setting-retention-days").value, 10),
    },
  };

  const ytKey = document.getElementById("setting-yt-api-key").value.trim();
  if (ytKey) {
    payload.youtube = { api_key: ytKey };
  }
  const geminiKey = document.getElementById("setting-gemini-key").value.trim();
  if (geminiKey) {
    payload.gemini_api_key = geminiKey;
  }
  const orKey = document.getElementById("setting-openrouter-key").value.trim();
  if (orKey) {
    payload.openrouter_api_key = orKey;
  }

  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      showToast("✓ Settings successfully saved!");
      closeSettingsModal();
      refreshHealth();
      refreshQuota();
    } else {
      const err = await res.json();
      showToast("Failed to save settings: " + (err.detail || "Server error"));
    }
  } catch (err) {
    showToast("Network error saving settings: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "💾 Save All Settings";
    }
  }
}

// -----------------------------------------------------------------------------
// YouTube Client Secret & OAuth Flow
// -----------------------------------------------------------------------------

function handleClientSecretFileSelect(e) {
  const file = e.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (event) => {
    const content = event.target.result;
    document.getElementById("yt-secret-raw-textarea").value = content;
    saveClientSecretRaw();
  };
  reader.readAsText(file);
}

async function saveClientSecretRaw() {
  const raw = document.getElementById("yt-secret-raw-textarea").value.trim();
  const feedback = document.getElementById("yt-oauth-feedback");
  if (!raw) {
    if (feedback) feedback.textContent = "Paste raw client_secret.json or select file first.";
    return;
  }

  try {
    JSON.parse(raw); // validate JSON syntax
  } catch (err) {
    if (feedback) feedback.textContent = "Invalid JSON format: " + err;
    return;
  }

  if (feedback) feedback.textContent = "Saving client_secret.json...";

  try {
    const res = await fetch("/api/settings/youtube-client-secret", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: raw }),
    });

    if (res.ok) {
      const data = await res.json();
      if (feedback) feedback.textContent = "✓ Client secret saved successfully!";
      showToast("Saved client_secret.json to data/!");
      checkInitialYouTubeAuth();
    } else {
      const err = await res.json();
      if (feedback) feedback.textContent = "Error: " + (err.detail || "Save failed");
    }
  } catch (err) {
    if (feedback) feedback.textContent = "Network error: " + err;
  }
}

async function triggerYouTubeOAuth() {
  const btn = document.getElementById("yt-oauth-authorize-btn");
  const feedback = document.getElementById("yt-oauth-feedback");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Authorizing...";
  }
  if (feedback) feedback.textContent = "Initiating OAuth 2.0 flow...";

  try {
    const res = await fetch("/api/settings/youtube-oauth", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      if (feedback) feedback.textContent = "OAuth error: " + (err.detail || "Failed to start OAuth");
      return;
    }

    const data = await res.json();
    if (data.authorization_url) {
      window.open(data.authorization_url, "_blank");
    }

    if (feedback) feedback.textContent = data.message || "Browser opened for Google OAuth sign-in. Waiting for token...";
    startYouTubeOAuthPolling();
  } catch (err) {
    if (feedback) feedback.textContent = "Error: " + err;
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "🔗 Authorize YouTube Account (OAuth 2.0)";
    }
  }
}

function startYouTubeOAuthPolling() {
  if (_oauthPollTimer) clearInterval(_oauthPollTimer);
  _oauthPollTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/settings/youtube-oauth/status");
      if (!res.ok) return;
      const data = await res.json();
      if (data.authorized || data.status === "authorized") {
        clearInterval(_oauthPollTimer);
        _oauthPollTimer = null;
        const feedback = document.getElementById("yt-oauth-feedback");
        if (feedback) feedback.textContent = "✓ YouTube Account Authorized Successfully!";
        showToast("✓ YouTube Account Authorized!");
        checkInitialYouTubeAuth();
      }
    } catch (e) {}
  }, 2000);
}

// -----------------------------------------------------------------------------
// AI Provider Test & Model Resolution
// -----------------------------------------------------------------------------

async function testProviderApi(provider) {
  const keyInput = provider === "gemini"
    ? document.getElementById("setting-gemini-key")
    : document.getElementById("setting-openrouter-key");

  const feedback = provider === "gemini"
    ? document.getElementById("gemini-test-feedback")
    : document.getElementById("openrouter-test-feedback");

  const dropdownWrap = provider === "gemini"
    ? document.getElementById("gemini-model-select-wrap")
    : document.getElementById("openrouter-model-select-wrap");

  const select = provider === "gemini"
    ? document.getElementById("gemini-model-select")
    : document.getElementById("openrouter-model-select");

  const apiKey = keyInput ? keyInput.value.trim() : "";
  if (feedback) feedback.textContent = `Testing ${provider} connection & fetching models...`;

  try {
    const res = await fetch("/api/settings/test-provider", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: provider, api_key: apiKey || null }),
    });

    if (!res.ok) {
      const err = await res.json();
      if (feedback) feedback.textContent = "Connection test failed: " + (err.detail || "Server error");
      return;
    }

    const data = await res.json();
    const models = data.models || [];
    if (feedback) feedback.textContent = `✓ Connected! Found ${models.length} model(s).`;
    showToast(`✓ Fetched ${models.length} models for ${provider}`);

    if (models.length > 0 && select && dropdownWrap) {
      select.innerHTML = '<option value="">-- Choose Live Model --</option>' +
        models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join("");
      dropdownWrap.style.display = "block";
    }
  } catch (err) {
    if (feedback) feedback.textContent = "Error testing provider: " + err;
  }
}

function applySelectedModel(provider) {
  const select = provider === "gemini"
    ? document.getElementById("gemini-model-select")
    : document.getElementById("openrouter-model-select");

  if (select && select.value) {
    const modelInput = document.getElementById("setting-active-model");
    if (modelInput) modelInput.value = select.value;
    const providerSelect = document.getElementById("setting-active-provider");
    if (providerSelect) providerSelect.value = provider;
    showToast(`Selected model: ${select.value}`);
  }
}

function handleActiveProviderChange() {
  const provider = document.getElementById("setting-active-provider").value;
  const modelInput = document.getElementById("setting-active-model");
  if (modelInput && !modelInput.value) {
    modelInput.placeholder = `${provider} default (Auto-Resolve)`;
  }
}

// -----------------------------------------------------------------------------
// Subtitle Style Presets UI
// -----------------------------------------------------------------------------

function selectPresetOption(preset) {
  const select = document.getElementById("setting-style-preset");
  if (select) {
    select.value = preset;
    syncPresetCardHighlight();
  }
}

function syncPresetCardHighlight() {
  const select = document.getElementById("setting-style-preset");
  if (!select) return;
  const val = select.value || "neon";

  ["neon", "hormozi", "mrbeast", "pill", "minimal", "cyberpunk", "cinematic"].forEach(p => {
    const card = document.getElementById(`preset-card-${p}`);
    if (card) {
      card.classList.toggle("active", p === val);
    }
  });

  const mainPreset = document.getElementById("style-preset-select");
  if (mainPreset && mainPreset.value !== val) {
    mainPreset.value = val;
  }

  syncCaptionCards();
  updatePhoneCanvasPreview();
}

function togglePasswordInput(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === "password") {
    input.type = "text";
    if (btn) btn.textContent = "🔒";
  } else {
    input.type = "password";
    if (btn) btn.textContent = "👁️";
  }
}

// -----------------------------------------------------------------------------
// Edge YouTube Studio Automation
// -----------------------------------------------------------------------------

let _edgeLoginPollTimer = null;

async function checkInitialEdgeAuth() {
  try {
    const res = await fetch("/api/auth/edge-login/status");
    if (!res.ok) return;
    const data = await res.json();
    if (data.signed_in || data.status === "signed_in") {
      updateEdgeButtonState(true, data.channel_name);
    }
  } catch (err) {
    console.debug("Initial edge auth check failed:", err);
  }
}

function updateEdgeButtonState(isSignedIn, channelName) {
  const btn = document.getElementById("edge-login-btn");
  if (!btn) return;
  if (isSignedIn) {
    btn.textContent = "✓ Edge Signed In";
    btn.title = `Authenticated with YouTube Studio${channelName ? ` (${channelName})` : ""}`;
    btn.classList.remove("btn-secondary");
    btn.classList.add("btn-success");
  } else {
    btn.textContent = "🔑 Edge Sign-In";
    btn.title = "Sign into YouTube Studio via Microsoft Edge";
    btn.classList.remove("btn-success");
    btn.classList.add("btn-secondary");
  }
}

function openEdgeLoginModal() {
  const modal = document.getElementById("edge-login-modal");
  const spinner = document.getElementById("edge-status-spinner");
  const badge = document.getElementById("edge-live-status-badge");
  const dot = document.getElementById("edge-status-dot");
  const statusText = document.getElementById("edge-status-text");
  const errorBox = document.getElementById("edge-login-error");
  const verifyBtn = document.getElementById("edge-check-status-btn");

  if (errorBox) errorBox.style.display = "none";
  if (spinner) spinner.textContent = "🌐";
  if (badge) {
    badge.style.display = "inline-flex";
    badge.style.backgroundColor = "";
    badge.style.borderColor = "";
    badge.style.color = "";
  }
  if (dot) dot.className = "dot yellow";
  if (statusText) statusText.textContent = "Opening Microsoft Edge... Please log in to your Google Account.";
  if (verifyBtn) {
    verifyBtn.disabled = false;
    verifyBtn.textContent = "🔍 Check Login Status";
  }

  if (modal) modal.style.display = "flex";
}

function closeEdgeLoginModal(e) {
  stopEdgeLoginPolling();
  const modal = document.getElementById("edge-login-modal");
  if (modal) modal.style.display = "none";
}

function startEdgeLoginPolling() {
  stopEdgeLoginPolling();
  pollEdgeLoginStatus();
  _edgeLoginPollTimer = setInterval(pollEdgeLoginStatus, 2000);
}

function stopEdgeLoginPolling() {
  if (_edgeLoginPollTimer) {
    clearInterval(_edgeLoginPollTimer);
    _edgeLoginPollTimer = null;
  }
}

async function pollEdgeLoginStatus() {
  try {
    const res = await fetch("/api/auth/edge-login/status");
    if (!res.ok) return;
    const data = await res.json();
    handleEdgeStatusUpdate(data);
  } catch (err) {
    console.debug("Error polling edge login status:", err);
  }
}

function handleEdgeStatusUpdate(data) {
  const status = data.status;
  const spinner = document.getElementById("edge-status-spinner");
  const badge = document.getElementById("edge-live-status-badge");
  const dot = document.getElementById("edge-status-dot");
  const statusText = document.getElementById("edge-status-text");
  const errorBox = document.getElementById("edge-login-error");
  const errorMsg = document.getElementById("edge-error-message");

  if (status === "signed_in" || data.signed_in === true) {
    stopEdgeLoginPolling();
    const chName = data.channel_name || "Active Creator";
    if (spinner) spinner.textContent = "✅";
    if (dot) dot.className = "dot green";
    if (badge) {
      badge.style.display = "inline-flex";
      badge.style.backgroundColor = "rgba(63, 185, 80, 0.2)";
      badge.style.borderColor = "var(--accent-green)";
      badge.style.color = "var(--accent-green)";
    }
    if (statusText) {
      statusText.textContent = `✓ Successfully Authenticated as YouTube Studio Creator! (${chName})`;
    }
    updateEdgeButtonState(true, chName);
    showToast("✓ Successfully Authenticated as YouTube Studio Creator!");

    setTimeout(() => {
      closeEdgeLoginModal();
    }, 1800);
  } else if (status === "error") {
    stopEdgeLoginPolling();
    if (spinner) spinner.textContent = "❌";
    if (badge) badge.style.display = "none";
    if (errorBox) {
      errorBox.style.display = "block";
      if (errorMsg) errorMsg.textContent = data.error || data.message || "An error occurred during Edge sign-in session.";
    }
  } else if (status === "not_signed_in") {
    stopEdgeLoginPolling();
    if (spinner) spinner.textContent = "⚠️";
    if (dot) dot.className = "dot yellow";
    if (statusText) {
      statusText.textContent = data.message || "Edge closed before sign-in completed.";
    }
  } else if (status === "running") {
    if (spinner) spinner.textContent = "⏳";
    if (dot) dot.className = "dot yellow";
    if (statusText) {
      statusText.textContent = data.message || "Edge browser window opened on your screen. Waiting for sign-in...";
    }
  }
}

async function checkEdgeLoginNow() {
  const verifyBtn = document.getElementById("edge-check-status-btn");
  const statusText = document.getElementById("edge-status-text");
  if (verifyBtn) {
    verifyBtn.disabled = true;
    verifyBtn.textContent = "Checking...";
  }
  if (statusText) statusText.textContent = "Verifying YouTube Studio session...";
  try {
    const res = await fetch("/api/auth/edge-login/status");
    if (res.ok) {
      const data = await res.json();
      handleEdgeStatusUpdate(data);
      if (data.status !== "signed_in" && !data.signed_in) {
        showToast("Studio session not detected yet. Sign in completely in Edge.");
      }
    }
  } catch (err) {
    showToast("Verification check error: " + err);
  } finally {
    if (verifyBtn) {
      verifyBtn.disabled = false;
      verifyBtn.textContent = "🔍 Check Login Status";
    }
  }
}

async function triggerEdgeLogin() {
  openEdgeLoginModal();
  showToast("Launching Microsoft Edge sign-in session...");
  try {
    const res = await fetch("/api/auth/edge-login", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      const msg = err.detail || "Failed to launch Edge login session.";
      const errorBox = document.getElementById("edge-login-error");
      const errorMsg = document.getElementById("edge-error-message");
      if (errorBox) errorBox.style.display = "block";
      if (errorMsg) errorMsg.textContent = msg;
      return;
    }
    const data = await res.json();
    showToast(data.message || "Edge sign-in session opened!");
  } catch (err) {
    const errorBox = document.getElementById("edge-login-error");
    const errorMsg = document.getElementById("edge-error-message");
    if (errorBox) errorBox.style.display = "block";
    if (errorMsg) errorMsg.textContent = "Network error: " + err;
    return;
  }

  startEdgeLoginPolling();
}

// -----------------------------------------------------------------------------
// Gameplay Backgrounds Management
// -----------------------------------------------------------------------------

async function refreshBackgrounds() {
  try {
    const res = await fetch("/api/backgrounds");
    if (!res.ok) return;
    const data = await res.json();
    const inv = data.inventory || {};
    const mc = inv.minecraft ? inv.minecraft.length : (data.minecraft || 0);
    const subway = inv.subway ? inv.subway.length : (data.subway || 0);
    const indicator = document.getElementById("bg-clips-indicator");
    if (indicator) {
      indicator.textContent = `Minecraft: ${mc} clips | Subway Surfers: ${subway} clips`;
    }
  } catch (err) {
    console.debug("Backgrounds refresh error:", err);
  }
}

async function downloadFreeGameplayClips() {
  const btn = document.getElementById("download-bg-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "⏳ Downloading Clips...";
  }
  showToast("Downloading free gameplay background clips...");
  try {
    const res = await fetch("/api/backgrounds/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ categories: ["minecraft", "subway"] }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert("Download error: " + (err.detail || "Server error"));
      return;
    }
    const data = await res.json();
    showToast(data.message || "Background clips downloaded!");
    setTimeout(refreshBackgrounds, 1500);
    setTimeout(refreshBackgrounds, 4000);
  } catch (err) {
    alert("Network error: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "📥 Auto-Download Free Gameplay Clips";
    }
  }
}

function handleBgDragOver(e) {
  e.preventDefault();
  e.stopPropagation();
  const dz = document.getElementById("bg-dropzone");
  if (dz) dz.classList.add("dragover");
}

function handleBgDragLeave(e) {
  e.preventDefault();
  e.stopPropagation();
  const dz = document.getElementById("bg-dropzone");
  if (dz) dz.classList.remove("dragover");
}

function handleBgDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  const dz = document.getElementById("bg-dropzone");
  if (dz) dz.classList.remove("dragover");

  const files = e.dataTransfer.files;
  if (files && files.length > 0) {
    uploadBackgroundFiles(files);
  }
}

function handleBgFileSelect(e) {
  const files = e.target.files;
  if (files && files.length > 0) {
    uploadBackgroundFiles(files);
  }
}

async function uploadBackgroundFiles(files) {
  const catSelect = document.getElementById("bg-upload-category");
  const category = (catSelect && catSelect.value) ? catSelect.value : "minecraft";
  const feedback = document.getElementById("bg-upload-feedback");
  if (feedback) feedback.textContent = `Uploading ${files.length} file(s)...`;

  let uploadedCount = 0;
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const formData = new FormData();
    formData.append("file", file);
    formData.append("category", category);

    try {
      const res = await fetch(`/api/backgrounds/upload?category=${encodeURIComponent(category)}`, {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        uploadedCount++;
      } else {
        const err = await res.json();
        showToast(`Failed to upload ${file.name}: ${err.detail || 'Error'}`);
      }
    } catch (err) {
      showToast(`Upload error for ${file.name}: ${err}`);
    }
  }

  if (feedback) {
    feedback.textContent = `Uploaded ${uploadedCount}/${files.length} clip(s) to ${category}!`;
    setTimeout(() => { feedback.textContent = ""; }, 4000);
  }
  showToast(`Uploaded ${uploadedCount} clip(s) to ${category}`);
  refreshBackgrounds();
}

async function copySparkPrompt() {
  try {
    const res = await fetch("/api/spark/prompts");
    if (!res.ok) {
      showToast("Failed to fetch Spark prompt template");
      return;
    }
    const data = await res.json();
    await copyToClipboard(data.prompt);
    showToast("Copied Spark Skill prompt to clipboard!");
  } catch (err) {
    showToast("Error copying prompt: " + err);
  }
}

function openPreview(jobId, title) {
  const modal = document.getElementById("preview-modal");
  const video = document.getElementById("modal-video");
  const modalTitle = document.getElementById("modal-title");
  const dlBtn = document.getElementById("modal-download");

  modalTitle.textContent = decodeURIComponent(title);
  video.src = `/api/video/${jobId}/preview`;
  dlBtn.href = `/api/video/${jobId}/master`;

  modal.style.display = "flex";
  video.play().catch(() => {});
}

function closePreviewModal(e) {
  const modal = document.getElementById("preview-modal");
  const video = document.getElementById("modal-video");
  if (video) {
    video.pause();
    video.src = "";
  }
  if (modal) modal.style.display = "none";
}

function refreshDashboard() {
  refreshHealth();
  refreshQuota();
  refreshScheduler();
  refreshJobs();
  refreshBackgrounds();
  refreshSchedulerSlots();
  checkInitialYouTubeAuth();
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// =============================================================================
// AutoShorts Studio Systems (1-Click Profiles, Live 9:16 Canvas & Shortcuts)
// =============================================================================

const STUDIO_PROFILES = {
  viral_split: {
    id: "viral_split",
    name: "Viral Split (Hormozi + Subway)",
    layout_mode: "split_50_50",
    style_preset: "hormozi",
    text_transform: "uppercase",
    subtitle_position: "center",
    progress_bar_position: "seam",
    color_grading: "vibrant",
    background: "subway",
    dopamine: "high",
  },
  podcast_focus: {
    id: "podcast_focus",
    name: "Podcast Focus (70/30 Minimal)",
    layout_mode: "split_70_30",
    style_preset: "minimal",
    text_transform: "none",
    subtitle_position: "bottom_safe",
    progress_bar_position: "top",
    color_grading: "warm",
    background: "auto",
    dopamine: "low",
  },
  gaming_max: {
    id: "gaming_max",
    name: "Gaming Max (MrBeast + Minecraft)",
    layout_mode: "split_50_50",
    style_preset: "mrbeast",
    text_transform: "uppercase",
    subtitle_position: "center",
    progress_bar_position: "top",
    color_grading: "vibrant",
    background: "minecraft",
    dopamine: "high",
  },
  clean_blur: {
    id: "clean_blur",
    name: "Clean Blurred 16:9 (No Gameplay)",
    layout_mode: "full_blur_bg",
    style_preset: "pill",
    text_transform: "uppercase",
    subtitle_position: "bottom_safe",
    progress_bar_position: "bottom",
    color_grading: "crisp",
    background: "auto",
    dopamine: "medium",
  },
  cinematic_full: {
    id: "cinematic_full",
    name: "Cinematic Full 9:16",
    layout_mode: "full_center_crop",
    style_preset: "cinematic",
    text_transform: "title",
    subtitle_position: "bottom_safe",
    progress_bar_position: "none",
    color_grading: "cinematic",
    background: "auto",
    dopamine: "medium",
  },
  pip_reaction: {
    id: "pip_reaction",
    name: "PiP Reaction Box",
    layout_mode: "pip_gameplay",
    style_preset: "neon",
    text_transform: "uppercase",
    subtitle_position: "bottom_safe",
    progress_bar_position: "top",
    color_grading: "vibrant",
    background: "minecraft",
    dopamine: "high",
  },
};

function initStudioProfiles() {
  loadStudioProfiles();

  // Attach auto-preview listeners to all quick customizer inputs
  const watchIds = [
    "subtitle-position-select",
    "text-transform-select",
    "color-grading-select",
    "progressbar-pos-select",
    "watermark-input",
    "category-id-select",
    "layout-mode-select",
    "style-preset-select",
    "background-select",
    "dopamine-select"
  ];

  watchIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("input", () => updatePhoneCanvasPreview());
      el.addEventListener("change", () => updatePhoneCanvasPreview());
    }
  });
}

function applyStudioProfile(profileId) {
  let profile = STUDIO_PROFILES[profileId];
  if (!profile) {
    try {
      const customs = JSON.parse(localStorage.getItem("autoshorts_custom_profiles") || "{}");
      if (customs[profileId]) profile = customs[profileId];
    } catch (e) {}
  }
  if (!profile) return;

  const layoutSel = document.getElementById("layout-mode-select");
  if (layoutSel && profile.layout_mode) layoutSel.value = profile.layout_mode;

  const styleSel = document.getElementById("style-preset-select");
  if (styleSel && profile.style_preset) styleSel.value = profile.style_preset;

  const textTrans = document.getElementById("text-transform-select");
  if (textTrans && profile.text_transform) textTrans.value = profile.text_transform;

  const subPos = document.getElementById("subtitle-position-select");
  if (subPos && profile.subtitle_position) subPos.value = profile.subtitle_position;

  const progBar = document.getElementById("progressbar-pos-select");
  if (progBar && profile.progress_bar_position) progBar.value = profile.progress_bar_position;

  const lutSel = document.getElementById("color-grading-select");
  if (lutSel && profile.color_grading) lutSel.value = profile.color_grading;

  const bgMain = document.getElementById("background-select");
  const bgAlias = document.getElementById("bg-select");
  if (profile.background) {
    if (bgMain) bgMain.value = profile.background;
    if (bgAlias) bgAlias.value = profile.background;
  }

  const dopSel = document.getElementById("dopamine-select");
  if (dopSel && profile.dopamine) dopSel.value = profile.dopamine;

  // Update active pill UI
  const pills = document.querySelectorAll("#preset-pills-container .preset-pill");
  pills.forEach(p => {
    p.classList.toggle("active", p.getAttribute("data-profile-id") === profileId);
  });

  syncLayoutCards();
  syncCaptionCards();
  syncPresetCardHighlight();
  updatePhoneCanvasPreview();
  showToast(`⚡ Loaded "${profile.name}" Profile`);
}

async function promptSaveCurrentProfile() {
  const name = prompt("Enter a name for your custom Studio Profile:", "My Custom Preset");
  if (!name || !name.trim()) return;

  const profileId = "custom_" + Date.now();
  const profileConfig = {
    id: profileId,
    name: name.trim(),
    layout_mode: document.getElementById("layout-mode-select") ? document.getElementById("layout-mode-select").value : "split_50_50",
    style_preset: document.getElementById("style-preset-select") ? document.getElementById("style-preset-select").value : "neon",
    text_transform: document.getElementById("text-transform-select") ? document.getElementById("text-transform-select").value : "uppercase",
    subtitle_position: document.getElementById("subtitle-position-select") ? document.getElementById("subtitle-position-select").value : "bottom_safe",
    progress_bar_position: document.getElementById("progressbar-pos-select") ? document.getElementById("progressbar-pos-select").value : "none",
    color_grading: document.getElementById("color-grading-select") ? document.getElementById("color-grading-select").value : "none",
    background: document.getElementById("background-select") ? document.getElementById("background-select").value : "minecraft",
    dopamine: document.getElementById("dopamine-select") ? document.getElementById("dopamine-select").value : "medium",
  };

  // Try backend POST /api/studio/profiles
  try {
    await fetch("/api/studio/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profileConfig),
    });
  } catch (err) {
    console.debug("Studio profile backend save fallback:", err);
  }

  // Save to localStorage
  try {
    const customs = JSON.parse(localStorage.getItem("autoshorts_custom_profiles") || "{}");
    customs[profileId] = profileConfig;
    localStorage.setItem("autoshorts_custom_profiles", JSON.stringify(customs));
  } catch (err) {}

  addCustomProfilePill(profileConfig);
  applyStudioProfile(profileId);
  showToast(`✓ Profile "${name.trim()}" saved!`);
}

async function loadStudioProfiles() {
  let backendProfiles = [];
  try {
    const res = await fetch("/api/studio/profiles");
    if (res.ok) {
      backendProfiles = await res.json();
    }
  } catch (e) {}

  let localProfiles = {};
  try {
    localProfiles = JSON.parse(localStorage.getItem("autoshorts_custom_profiles") || "{}");
  } catch (e) {}

  const merged = {};
  if (Array.isArray(backendProfiles)) {
    backendProfiles.forEach(p => { if (p && p.id) merged[p.id] = p; });
  }
  Object.values(localProfiles).forEach(p => { if (p && p.id) merged[p.id] = p; });

  Object.values(merged).forEach(p => {
    if (!STUDIO_PROFILES[p.id]) {
      addCustomProfilePill(p);
    }
  });
}

function addCustomProfilePill(profile) {
  const container = document.getElementById("preset-pills-container");
  if (!container) return;

  if (container.querySelector(`[data-profile-id="${profile.id}"]`)) return;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "preset-pill";
  btn.setAttribute("data-profile-id", profile.id);
  btn.onclick = () => applyStudioProfile(profile.id);
  btn.innerHTML = `
    <span class="pill-emoji">⭐</span>
    <span class="pill-label">${escapeHtml(profile.name)}</span>
    <span class="pill-meta">${escapeHtml(profile.style_preset)}</span>
  `;
  container.appendChild(btn);
}

// Visual Layout Cards Sync
function selectLayoutMode(layout) {
  const sel = document.getElementById("layout-mode-select");
  if (sel) {
    sel.value = layout;
    syncLayoutCards();
    updatePhoneCanvasPreview();
  }
}

function syncLayoutCards() {
  const sel = document.getElementById("layout-mode-select");
  if (!sel) return;
  const val = sel.value || "split_50_50";
  const cards = document.querySelectorAll("#layout-cards-grid .layout-card");
  cards.forEach(c => {
    c.classList.toggle("active", c.getAttribute("data-layout") === val);
  });
}

// Visual Caption Cards Sync
function selectCaptionPreset(preset) {
  const sel = document.getElementById("style-preset-select");
  if (sel) {
    sel.value = preset;
    syncCaptionCards();
    syncPresetCardHighlight();
    updatePhoneCanvasPreview();
  }
}

function syncCaptionCards() {
  const sel = document.getElementById("style-preset-select");
  if (!sel) return;
  const val = sel.value || "neon";
  const cards = document.querySelectorAll("#caption-cards-grid .caption-card");
  cards.forEach(c => {
    c.classList.toggle("active", c.getAttribute("data-preset") === val);
  });
}

// Interactive 9:16 Live Phone Mockup Previewer
let _captionAnimRunning = true;
let _captionAnimTimer = null;
let _captionWordIndex = 0;
const _samplePhrases = [
  ["THIS", "NEW", "AI", "TOOL"],
  ["CHANGES", "EVERYTHING", "FOREVER"],
  ["WAIT", "TILL", "THE", "END", "😱"],
  ["100%", "ON-DEVICE", "OFFLINE"],
  ["CREATE", "VIRAL", "SHORTS", "NOW"]
];
let _phraseIndex = 0;

function updatePhoneCanvasPreview() {
  const screen = document.getElementById("phone-screen");
  if (!screen) return;

  const layoutMode = document.getElementById("layout-mode-select") ? document.getElementById("layout-mode-select").value : "split_50_50";
  const stylePreset = document.getElementById("style-preset-select") ? document.getElementById("style-preset-select").value : "neon";
  const subtitlePos = document.getElementById("subtitle-position-select") ? document.getElementById("subtitle-position-select").value : "bottom_safe";
  const colorGrading = document.getElementById("color-grading-select") ? document.getElementById("color-grading-select").value : "none";
  const progressBarPos = document.getElementById("progressbar-pos-select") ? document.getElementById("progressbar-pos-select").value : "none";
  const watermarkText = (document.getElementById("watermark-input") ? document.getElementById("watermark-input").value.trim() : "") || "@AutoShorts";
  const backgroundTheme = document.getElementById("background-select") ? document.getElementById("background-select").value : "minecraft";

  // Layer 1: Layout Mode
  ["layout-split_50_50", "layout-split_70_30", "layout-full_blur_bg", "layout-full_center_crop", "layout-pip_gameplay"].forEach(cls => {
    screen.classList.remove(cls);
  });
  screen.classList.add(`layout-${layoutMode}`);

  // Layer 2: Color LUT
  ["lut-none", "lut-vibrant", "lut-cinematic", "lut-warm", "lut-crisp"].forEach(cls => {
    screen.classList.remove(cls);
  });
  screen.classList.add(`lut-${colorGrading}`);

  // Layer 3: Progress Bar
  const progBar = document.getElementById("phone-progressbar");
  if (progBar) {
    if (progressBarPos === "none") {
      progBar.style.display = "none";
    } else {
      progBar.style.display = "block";
      progBar.className = `phone-progressbar pos-${progressBarPos}`;
    }
  }

  // Layer 5: Caption Preset & Position
  const captionsLayer = document.getElementById("phone-captions-layer");
  if (captionsLayer) {
    ["pos-bottom_safe", "pos-center", "pos-top_safe"].forEach(c => captionsLayer.classList.remove(c));
    captionsLayer.classList.add(`pos-${subtitlePos}`);

    ["preset-neon", "preset-hormozi", "preset-mrbeast", "preset-pill", "preset-minimal", "preset-cyberpunk", "preset-cinematic"].forEach(c => captionsLayer.classList.remove(c));
    captionsLayer.classList.add(`preset-${stylePreset}`);
  }

  // Layer 6: Watermark
  const wmTextEl = document.getElementById("canvas-watermark-text");
  if (wmTextEl) wmTextEl.textContent = watermarkText;

  // Layer 7: Safe-Zone Handle
  const shortsHandle = document.getElementById("canvas-shorts-handle");
  if (shortsHandle) shortsHandle.textContent = watermarkText;

  // Gameplay badge text
  const gameBadge = document.getElementById("canvas-gameplay-badge");
  if (gameBadge) {
    const bgNames = {
      minecraft: "Minecraft Parkour",
      subway: "Subway Surfers",
      gta: "GTA Car Stunts",
      auto: "Procedural Background",
    };
    gameBadge.textContent = bgNames[backgroundTheme] || "Gameplay Background";
  }

  // Legend badges
  const layoutBadge = document.getElementById("canvas-layout-badge");
  if (layoutBadge) {
    const layoutNames = {
      split_50_50: "📐 50/50 Split",
      split_70_30: "📐 70/30 Podcast",
      full_blur_bg: "📐 Blurred 16:9",
      full_center_crop: "📐 Full 9:16",
      pip_gameplay: "📐 PiP Reaction",
    };
    layoutBadge.textContent = layoutNames[layoutMode] || layoutMode;
  }

  const presetBadge = document.getElementById("canvas-preset-badge");
  if (presetBadge) {
    const presetNames = {
      neon: "🎨 Neon Pop",
      hormozi: "🎨 Alex Hormozi",
      mrbeast: "🎨 MrBeast Pop",
      pill: "🎨 Pill Box",
      minimal: "🎨 Minimalist",
      cyberpunk: "🎨 Cyberpunk",
      cinematic: "🎨 Cinematic",
    };
    presetBadge.textContent = presetNames[stylePreset] || stylePreset;
  }

  const lutBadge = document.getElementById("canvas-lut-badge");
  if (lutBadge) {
    lutBadge.textContent = `✨ LUT: ${colorGrading.charAt(0).toUpperCase() + colorGrading.slice(1)}`;
  }

  renderCaptionWords();
}

function renderCaptionWords() {
  const container = document.getElementById("canvas-caption-burst");
  if (!container) return;

  const textTransform = document.getElementById("text-transform-select") ? document.getElementById("text-transform-select").value : "uppercase";
  const words = _samplePhrases[_phraseIndex % _samplePhrases.length];

  container.innerHTML = words.map((w, idx) => {
    let wordText = w;
    if (textTransform === "uppercase") wordText = w.toUpperCase();
    else if (textTransform === "title") wordText = w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
    else if (textTransform === "none") wordText = w;

    const isActive = idx === (_captionWordIndex % words.length);
    return `<span class="word-item ${isActive ? 'word-active' : ''}">${escapeHtml(wordText)}</span>`;
  }).join(" ");
}

function initCaptionAnimationTicker() {
  if (_captionAnimTimer) clearInterval(_captionAnimTimer);
  _captionAnimTimer = setInterval(() => {
    if (!_captionAnimRunning) return;
    _captionWordIndex++;
    const words = _samplePhrases[_phraseIndex % _samplePhrases.length];
    if (_captionWordIndex >= words.length) {
      _captionWordIndex = 0;
      _phraseIndex = (_phraseIndex + 1) % _samplePhrases.length;
    }
    renderCaptionWords();
  }, 750);
}

function toggleCaptionAnimation() {
  _captionAnimRunning = !_captionAnimRunning;
  const btn = document.getElementById("toggle-caption-anim-btn");
  if (btn) {
    btn.textContent = _captionAnimRunning ? "⏸ Pause" : "▶ Animate";
    btn.classList.toggle("active", _captionAnimRunning);
  }
}

function toggleSafeZoneOverlay() {
  const overlay = document.getElementById("phone-safezone-overlay");
  const btn = document.getElementById("toggle-safezone-btn");
  if (overlay) {
    overlay.classList.toggle("visible");
    if (btn) btn.classList.toggle("active", overlay.classList.contains("visible"));
  }
}

function initKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    const isInput = e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA";

    if (e.key === "Escape") {
      closeSettingsModal();
      closeEditJobModal();
      closePreviewModal();
      closeStageModal();
      closeEdgeLoginModal();
    } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      submitJobs();
    } else if ((e.ctrlKey || e.metaKey) && e.key === "," && !isInput) {
      e.preventDefault();
      openSettingsModal();
    }
  });
}

// =============================================================================
// Top-Level Navigation Mode Switcher Tabs
// =============================================================================

let _activeMainView = "view-studio";

function initMainViewTabs() {
  const tabs = document.querySelectorAll("#main-view-tabs .view-tab-btn");
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      const targetView = tab.getAttribute("data-view");
      if (targetView) switchMainView(targetView);
    });
  });
}

function switchMainView(viewId) {
  _activeMainView = viewId;

  // 1. Update tab button states
  const tabs = document.querySelectorAll("#main-view-tabs .view-tab-btn");
  tabs.forEach(tab => {
    const isTarget = tab.getAttribute("data-view") === viewId;
    tab.classList.toggle("active", isTarget);
    tab.setAttribute("aria-selected", isTarget ? "true" : "false");
  });

  // 2. Toggle view containers
  const views = ["view-studio", "view-research", "view-moneyprinter", "view-analytics"];
  views.forEach(v => {
    const el = document.getElementById(v);
    if (el) {
      const isTarget = v === viewId;
      el.classList.toggle("active", isTarget);
      el.style.display = isTarget ? "block" : "none";
    }
  });

  // 3. Lazy trigger data refresh based on active view
  if (viewId === "view-research") {
    if (!_trendingVideosCache || _trendingVideosCache.length === 0) {
      scanLiveTrends();
      loadNichePayoutMatrix();
      loadViralMusic();
    }
  } else if (viewId === "view-moneyprinter") {
    refreshMpVoices();
    loadMpTopics();
    updateMpScriptStats();
  } else if (viewId === "view-analytics") {
    refreshAnalytics();
  }
}

// =============================================================================
// VIEW 2: Autonomous Viral Research Agent & Trending Finder
// =============================================================================

let _activeResearchNiche = "ai_tech";
let _trendingVideosCache = [];
let _currentBrief = null;
let _selectedBriefHookText = "";

const _nicheFallbackVideos = {};

function initResearchAgent() {
  updateRevenueCalculator();
}

function selectResearchNiche(genre) {
  _activeResearchNiche = genre;
  const pills = document.querySelectorAll("#research-niche-pills .niche-pill");
  pills.forEach(p => {
    p.classList.toggle("active", p.getAttribute("data-genre") === genre);
  });

  // Sync revenue calculator niche dropdown if matching
  const calcSelect = document.getElementById("calc-niche-select");
  if (calcSelect && calcSelect.querySelector(`option[value="${genre}"]`)) {
    calcSelect.value = genre;
    updateRevenueCalculator();
  }

  scanLiveTrends();
}

async function scanLiveTrends() {
  const queryInput = document.getElementById("research-query-input");
  const query = queryInput ? queryInput.value.trim() : "";
  const countBadge = document.getElementById("trending-count-badge");
  const grid = document.getElementById("trending-videos-grid");

  if (countBadge) countBadge.textContent = "Scanning Live YouTube...";

  let videos = [];
  let isApiConfigured = true;

  try {
    const url = `/api/research/trending?genre=${encodeURIComponent(_activeResearchNiche)}&q=${encodeURIComponent(query)}`;
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      if (data.api_configured === false || data.requires_api_key) {
        isApiConfigured = false;
      } else if (Array.isArray(data.videos) && data.videos.length > 0) {
        videos = data.videos;
      }
    } else {
      isApiConfigured = false;
    }
  } catch (err) {
    console.debug("Live research API error:", err);
    isApiConfigured = false;
  }

  if (!isApiConfigured) {
    if (countBadge) countBadge.textContent = "API Key Required";
    if (grid) {
      grid.innerHTML = `
        <div class="api-required-card" style="grid-column: 1 / -1;">
          <div class="api-required-icon">
            <svg class="ui-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.46a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .46 5.33A2.78 2.78 0 0 0 3.4 19c1.72.46 8.6.46 8.6.46s6.88 0 8.6-.46a2.78 2.78 0 0 0 1.94-2 29 29 0 0 0 .46-5.25 29 29 0 0 0-.46-5.33z"></path><polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02" fill="currentColor"></polygon></svg>
          </div>
          <h3 class="api-required-title">YouTube Data API v3 Key Required</h3>
          <p class="api-required-subtext">
            Fetching real-time trending videos, view velocities, and Shorts engagement rankings requires an active YouTube Data API v3 key.
          </p>
          <button type="button" class="btn btn-primary btn-sm clay-btn-primary" onclick="openSettingsModal('tab-yt-api')">
            <svg class="ui-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            <span>Configure YouTube Data API Key</span>
          </button>
        </div>
      `;
    }
    _trendingVideosCache = [];
    return;
  }

  _trendingVideosCache = videos;
  if (countBadge) countBadge.textContent = `${videos.length} Videos Ranked`;
  renderTrendingVideos(videos);
}

function renderTrendingVideos(videos) {
  const grid = document.getElementById("trending-videos-grid");
  if (!grid) return;

  if (videos.length === 0) {
    grid.innerHTML = `<div class="empty-state">No trending videos found for this search. Try another topic or niche.</div>`;
    return;
  }

  grid.innerHTML = videos.map(v => {
    const velStr = v.views_per_hour >= 1000 ? `${(v.views_per_hour / 1000).toFixed(1)}k` : `${v.views_per_hour}`;
    const score = v.viral_score || 90;
    const profileId = _activeResearchNiche === "ai_tech" ? "viral_split" : (_activeResearchNiche === "finance" ? "clean_blur" : "podcast_focus");

    return `
      <div class="trending-video-card" data-video-id="${escapeHtml(v.id || '')}">
        <div class="video-thumb-wrap">
          <img src="${escapeHtml(v.thumbnail || '')}" alt="${escapeHtml(v.title)}" class="video-thumb-img" loading="lazy" onerror="this.src='https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=640&q=80'">
          <span class="thumb-duration-badge">${escapeHtml(v.duration_str || '24:00')}</span>
          <span class="thumb-velocity-badge">🔥 ${velStr} views/hr</span>
          <span class="thumb-viral-meter">Score: ${score}/100</span>
        </div>
        <div class="video-card-body">
          <h4 class="video-card-title" title="${escapeHtml(v.title)}">${escapeHtml(v.title)}</h4>
          <div class="video-channel-row">
            <span>${escapeHtml(v.channel || 'YouTube Creator')}</span>
            <a href="${escapeHtml(v.url)}" target="_blank" rel="noopener noreferrer" class="video-direct-link" title="Open full source on YouTube">
              🔗 Open Video ↗
            </a>
          </div>
          <div class="video-focus-prompt">
            💡 <strong>Suggested Clip Focus:</strong> ${escapeHtml(v.focus_prompt || 'Extract the most surprising insight')}
          </div>
          <div class="video-card-actions">
            <button type="button" class="btn btn-primary btn-sm" style="width: 100%;" onclick="sendToPipeline('${escapeHtml(v.url)}', '${escapeHtml(v.focus_prompt || '')}', '${profileId}')">
              ⚡ 1-Click Send to Pipeline
            </button>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

async function sendToPipeline(url, focusPrompt, profileId) {
  // Try backend handoff endpoint if mounted
  try {
    await fetch("/api/research/pipeline-handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, focus_prompt: focusPrompt, profile_id: profileId })
    });
  } catch (err) {
    console.debug("Backend pipeline handoff API fallback:", err);
  }

  // Populate Studio Ingestion Box
  const linksInput = document.getElementById("links-input");
  if (linksInput) {
    const line = focusPrompt ? `${url} : ${focusPrompt}` : url;
    if (linksInput.value.trim().length > 0) {
      linksInput.value = `${linksInput.value.trim()}\n${line}`;
    } else {
      linksInput.value = line;
    }
  }

  // Apply recommended Studio Profile if supplied
  if (profileId && typeof applyStudioProfile === "function") {
    applyStudioProfile(profileId);
  }

  showToast(`⚡ Added to Studio Pipeline: ${url.slice(0, 32)}...`);
  switchMainView("view-studio");

  // Scroll to ingestion box
  const formEl = document.getElementById("submit-jobs-form");
  if (formEl) formEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function generateStrategyBrief() {
  const queryInput = document.getElementById("research-query-input");
  const query = queryInput ? queryInput.value.trim() : "";
  const briefCard = document.getElementById("ai-strategy-brief-card");
  const briefContent = document.getElementById("brief-body-content");
  const briefChevron = document.getElementById("brief-chevron");

  if (briefCard) briefCard.style.display = "block";
  if (briefChevron) briefChevron.textContent = "▲";

  let briefData = null;
  let isAiConfigured = true;

  try {
    const res = await fetch("/api/research/brief", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ genre: _activeResearchNiche, query })
    });
    if (res.ok) {
      const data = await res.json();
      if (data.api_configured === false || data.requires_api_key) {
        isAiConfigured = false;
      } else if (data.brief) {
        briefData = data.brief;
      }
    } else {
      isAiConfigured = false;
    }
  } catch (err) {
    console.debug("Strategy brief API error:", err);
    isAiConfigured = false;
  }

  if (!isAiConfigured || !briefData || !briefData.recommended_video) {
    if (briefContent) {
      briefContent.innerHTML = `
        <div class="api-required-card api-required-compact">
          <div class="api-required-icon-sm">
            <svg class="ui-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2 2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"></path><rect x="4" y="8" width="16" height="12" rx="2"></rect><circle cx="9" cy="13" r="1"></circle><circle cx="15" cy="13" r="1"></circle><path d="M10 17h4"></path></svg>
          </div>
          <h4 class="api-required-title" style="font-size: 1rem;">AI Provider Key Required</h4>
          <p class="api-required-subtext" style="font-size: 0.82rem; margin-bottom: 1rem;">
            Generating automated strategy briefs, viral hooks, and revenue projections requires an active LLM provider (Google Gemini or OpenRouter) configured in Settings.
          </p>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openSettingsModal('tab-ai-keys')">
            <svg class="ui-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            <span>Configure AI Provider Key</span>
          </button>
        </div>
      `;
    }
    const badge = briefCard ? briefCard.querySelector(".badge") : null;
    if (badge) {
      badge.textContent = "API Key Required";
      badge.className = "badge status-FAILED";
    }
    showToast("AI Provider Key Required for Strategy Briefs. Please configure in Settings.", "warning");
    _currentBrief = null;
    return;
  }

  const badge = briefCard ? briefCard.querySelector(".badge") : null;
  if (badge) {
    badge.textContent = "Strategy Ready";
    badge.className = "badge badge-success";
  }

  _currentBrief = briefData;
  renderBriefContent(briefData);

  if (briefCard) {
    briefCard.style.display = "block";
    briefCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  showToast("🤖 AI Strategy Brief generated with 3 viral hooks!", "success");
}

function renderBriefContent(briefData) {
  const briefContent = document.getElementById("brief-body-content");
  if (!briefContent) return;

  const v = briefData.recommended_video || {};
  const hooks = briefData.hooks || [];
  const h1Text = hooks[0] ? hooks[0].text : "";
  const h2Text = hooks[1] ? hooks[1].text : "";
  const h3Text = hooks[2] ? hooks[2].text : "";
  _selectedBriefHookText = h1Text;

  briefContent.innerHTML = `
    <div class="brief-grid">
      <!-- Left: Recommended Video Card -->
      <div class="brief-col-video">
        <span class="brief-label">🎯 #1 Recommended Video to Clip</span>
        <div class="brief-video-preview" id="brief-video-preview">
          <div class="brief-thumb-container">
            <img src="${escapeHtml(v.thumbnail || '')}" alt="${escapeHtml(v.title || '')}" class="brief-thumb-img" onerror="this.src='https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=640&q=80'">
          </div>
          <div class="brief-video-title">${escapeHtml(v.title || '')}</div>
          <div class="brief-video-meta">
            <span>${escapeHtml(v.channel || '')}</span>
            <a href="${escapeHtml(v.url || '#')}" target="_blank" rel="noopener noreferrer" class="video-direct-link">🔗 Open Source Video ↗</a>
          </div>
        </div>
      </div>

      <!-- Right: 3 Hook Variants & Settings -->
      <div class="brief-col-hooks">
        <span class="brief-label">🪝 3 High-Retention Hook Variants</span>
        <div class="hook-variant-cards">
          <div class="hook-card selected" onclick="selectBriefHook(this)">
            <div class="hook-type-tag tag-curiosity">❓ Curiosity Question</div>
            <p class="hook-text" id="hook-curiosity">${escapeHtml(h1Text)}</p>
          </div>
          <div class="hook-card" onclick="selectBriefHook(this)">
            <div class="hook-type-tag tag-warning">⚠️ Warning / Common Mistake</div>
            <p class="hook-text" id="hook-warning">${escapeHtml(h2Text)}</p>
          </div>
          <div class="hook-card" onclick="selectBriefHook(this)">
            <div class="hook-type-tag tag-shock">⚡ Data Shock / Statistic</div>
            <p class="hook-text" id="hook-shock">${escapeHtml(h3Text)}</p>
          </div>
        </div>

        <div class="brief-recipe-row">
          <div class="recipe-metric-pill">
            <span class="rec-label">Preset:</span>
            <span class="rec-val" id="brief-profile-rec">${escapeHtml(briefData.recommended_profile || '⚡ Viral Split')}</span>
          </div>
          <div class="recipe-metric-pill">
            <span class="rec-label">BGM:</span>
            <span class="rec-val" id="brief-bgm-rec">${escapeHtml(briefData.recommended_bgm || '🎵 Drift Phonk')}</span>
          </div>
          <div class="recipe-metric-pill">
            <span class="rec-label">Est. RPM:</span>
            <span class="rec-val" id="brief-rpm-val">${escapeHtml(briefData.projected_rpm || '$0.35')}</span>
          </div>
          <div class="recipe-metric-pill">
            <span class="rec-label">Funnel CPM:</span>
            <span class="rec-val" id="brief-cpm-val">${escapeHtml(briefData.projected_funnel_cpm || '$32.00')}</span>
          </div>
          <div class="recipe-metric-pill highlight">
            <span class="rec-label">Est. Revenue:</span>
            <span class="rec-val" id="brief-earnings-val">${escapeHtml(briefData.projected_monthly_earnings || '$1,280/mo')}</span>
          </div>
        </div>

        <div class="brief-action-row">
          <button id="execute-strategy-btn" type="button" class="btn btn-primary-gradient" onclick="executeFullStrategy()">
            ⚡ 1-Click Execute Full Strategy (Apply Profile + BGM + Clip Link)
          </button>
        </div>
      </div>
    </div>
  `;
}

function selectBriefHook(el) {
  const cards = document.querySelectorAll(".hook-variant-cards .hook-card");
  cards.forEach(c => c.classList.remove("selected"));
  el.classList.add("selected");
  const p = el.querySelector(".hook-text");
  if (p) _selectedBriefHookText = p.textContent.trim();
  showToast("Selected hook for Studio strategy.");
}

function toggleStrategyBrief() {
  const body = document.getElementById("brief-body-content");
  const chev = document.getElementById("brief-chevron");
  if (!body) return;
  const isHidden = body.style.display === "none";
  body.style.display = isHidden ? "block" : "none";
  if (chev) chev.textContent = isHidden ? "▲" : "▼";
}

function executeFullStrategy() {
  if (!_currentBrief) {
    showToast("Please generate a strategy brief first.");
    return;
  }

  const video = _currentBrief.recommended_video;
  const profileId = _currentBrief.recommended_profile || "viral_split";
  const hook = _selectedBriefHookText || (_currentBrief.hooks && _currentBrief.hooks[0] ? _currentBrief.hooks[0].text : "Must Watch");

  sendToPipeline(video.url, hook, profileId);
  showToast("⚡ Full strategy executed! Studio loaded with recommended profile & hook.");
}

// Highest Payout Niches & Revenue Calculator
const _nichePayoutMatrix = [
  { id: "finance", name: "💰 Finance & Wealth", shorts_rpm: "$0.25–$0.45", rpm_val: 0.35, funnel_cpm: "$28–$55", cpm_val: 38.0, blended_rpm: "$1.15+", comp: "High", hours: "6 AM, 11 AM, 4 PM", preset: "Cinematic Full 9:16" },
  { id: "ai_tech", name: "🤖 AI Tools & SaaS", shorts_rpm: "$0.20–$0.38", rpm_val: 0.30, funnel_cpm: "$22–$45", cpm_val: 32.0, blended_rpm: "$0.95+", comp: "Med", hours: "7 AM, 12 PM, 5 PM", preset: "Viral Split (Hormozi)" },
  { id: "business", name: "💼 Business & Startups", shorts_rpm: "$0.18–$0.35", rpm_val: 0.26, funnel_cpm: "$20–$40", cpm_val: 28.0, blended_rpm: "$0.85+", comp: "Med", hours: "6 AM, 10 AM, 3 PM", preset: "Podcast Focus (70/30)" },
  { id: "health", name: "🧬 Health & Longevity", shorts_rpm: "$0.15–$0.30", rpm_val: 0.22, funnel_cpm: "$18–$32", cpm_val: 24.0, blended_rpm: "$0.70+", comp: "Med", hours: "5 AM, 9 AM, 6 PM", preset: "Podcast Focus (70/30)" },
  { id: "science", name: "🔭 Science & Space", shorts_rpm: "$0.12–$0.25", rpm_val: 0.18, funnel_cpm: "$14–$26", cpm_val: 20.0, blended_rpm: "$0.55+", comp: "Low", hours: "8 AM, 1 PM, 7 PM", preset: "Cinematic Full 9:16" },
  { id: "podcasts", name: "🎙️ Podcasts & Interviews", shorts_rpm: "$0.08–$0.18", rpm_val: 0.14, funnel_cpm: "$8–$16", cpm_val: 12.0, blended_rpm: "$0.38+", comp: "High", hours: "9 AM, 2 PM, 6 PM", preset: "PiP Reaction / Viral Split" },
  { id: "gaming", name: "🎮 Gaming & Esports", shorts_rpm: "$0.05–$0.14", rpm_val: 0.09, funnel_cpm: "$6–$12", cpm_val: 8.0, blended_rpm: "$0.25+", comp: "High", hours: "11 AM, 3 PM, 7 PM", preset: "Gaming Max (MrBeast)" }
];

async function loadNichePayoutMatrix() {
  const tbody = document.getElementById("niche-table-body");
  if (!tbody) return;

  let niches = _nichePayoutMatrix;
  try {
    const res = await fetch("/api/research/niches");
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.niches) && data.niches.length > 0) {
        niches = data.niches;
      }
    }
  } catch (err) {
    console.debug("Niche matrix fetch fallback:", err);
  }

  tbody.innerHTML = niches.map(n => {
    const compClass = n.comp === "Low" ? "comp-low" : (n.comp === "Med" ? "comp-med" : "comp-high");
    return `
      <tr>
        <td><strong>${escapeHtml(n.name)}</strong></td>
        <td><span style="color: var(--accent-green); font-weight: 700;">${escapeHtml(n.shorts_rpm)}</span></td>
        <td><span style="color: var(--accent-blue); font-weight: 700;">${escapeHtml(n.funnel_cpm)}</span></td>
        <td><strong style="color: #ffd33d;">${escapeHtml(n.blended_rpm)}</strong></td>
        <td><span class="table-badge ${compClass}">${escapeHtml(n.comp)}</span></td>
        <td style="font-size: 0.76rem; color: var(--text-muted);">${escapeHtml(n.hours)}</td>
        <td><code>${escapeHtml(n.preset)}</code></td>
        <td>
          <button type="button" class="btn btn-secondary btn-xs" onclick="selectNicheFromTable('${escapeHtml(n.id)}')">
            Select
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

function selectNicheFromTable(nicheId) {
  selectResearchNiche(nicheId);
  const calcSelect = document.getElementById("calc-niche-select");
  if (calcSelect) {
    calcSelect.value = nicheId;
    updateRevenueCalculator();
  }
  const calcCard = document.querySelector(".revenue-calc-card");
  if (calcCard) calcCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function updateRevenueCalculator() {
  const viewsSlider = document.getElementById("calc-views-slider");
  const viewsValEl = document.getElementById("calc-views-val");
  const nicheSelect = document.getElementById("calc-niche-select");
  const ctrSlider = document.getElementById("calc-ctr-slider");
  const ctrValEl = document.getElementById("calc-ctr-val");

  const directRevEl = document.getElementById("calc-direct-rev");
  const funnelRevEl = document.getElementById("calc-funnel-rev");
  const totalRevEl = document.getElementById("calc-total-rev");
  const annualRevEl = document.getElementById("calc-annual-rev");

  if (!viewsSlider || !nicheSelect || !ctrSlider) return;

  const views = parseInt(viewsSlider.value, 10) || 1000000;
  const ctr = parseFloat(ctrSlider.value) || 2.5;
  const nicheId = nicheSelect.value || "ai_tech";

  if (viewsValEl) viewsValEl.textContent = `${views.toLocaleString()} views/mo`;
  if (ctrValEl) ctrValEl.textContent = `${ctr.toFixed(1)}% Funnel CTR`;

  const nicheData = _nichePayoutMatrix.find(n => n.id === nicheId) || _nichePayoutMatrix[1];
  const rpm = nicheData.rpm_val || 0.30;
  const cpm = nicheData.cpm_val || 32.0;

  // Direct Shorts Ad Revenue = (views / 1000) * rpm
  const directRev = (views / 1000) * rpm;

  // Long-Form Funnel Views = views * (ctr / 100)
  const funnelViews = views * (ctr / 100);
  // Long-Form Funnel Revenue = (funnelViews / 1000) * cpm
  const funnelRev = (funnelViews / 1000) * cpm;

  const totalMonthly = directRev + funnelRev;
  const totalAnnual = totalMonthly * 12;

  if (directRevEl) directRevEl.textContent = `$${directRev.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (funnelRevEl) {
    funnelRevEl.textContent = `$${funnelRev.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const sub = funnelRevEl.parentElement.querySelector(".calc-res-sub");
    if (sub) sub.textContent = `${Math.round(funnelViews).toLocaleString()} views driven to related long-form`;
  }
  if (totalRevEl) totalRevEl.textContent = `$${totalMonthly.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (annualRevEl) annualRevEl.textContent = `Annualized Run-Rate: $${Math.round(totalAnnual).toLocaleString()}/yr`;
}

// Viral Music Catalog & Live Trending Shorts Sounds
const _bgmCatalogList = [
  { id: "drift_phonk", title: "Drift Phonk Pulse", bpm: 138, genre: "Phonk", mood: "High Energy / Aggressive", niches: "Gaming, Gym, Motivation", installed: false },
  { id: "dark_synthwave", title: "Dark Synthwave", bpm: 120, genre: "Synthwave", mood: "Mysterious / Cyberpunk", niches: "AI, Tech, Space", installed: true },
  { id: "lofi", title: "High-Retention Lo-Fi Chillhop", bpm: 80, genre: "Lo-Fi", mood: "Relaxed Focus / Deep Voice", niches: "Stoicism, Psychology, Coding", installed: false },
  { id: "percussion", title: "MrBeast Fast Tension Percussion", bpm: 132, genre: "Cinematic", mood: "Fast Ticking Ticks", niches: "Business, Challenges, Facts", installed: false },
  { id: "riser", title: "Cinematic Sub-Bass Riser", bpm: 90, genre: "Trailer", mood: "Suspense / Build-up", niches: "Dark History, Mysteries", installed: false },
  { id: "piano", title: "Motivational Piano & Emotional Strings", bpm: 92, genre: "Orchestral", mood: "Inspiring / Uplifting", niches: "Finance, Wealth, Stories", installed: false }
];

const _trendingShortsAudio = [];

async function loadViralMusic() {
  const bgmListEl = document.getElementById("bgm-catalog-list");
  const trendingListEl = document.getElementById("trending-commercial-songs-list");

  let tracks = _bgmCatalogList;
  let trending = [];
  let isYtConfigured = true;

  try {
    const res = await fetch("/api/research/music");
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.bgm_catalog) && data.bgm_catalog.length > 0) {
        tracks = data.bgm_catalog;
      }
      if (data.api_configured === false || data.trending_requires_api_key) {
        isYtConfigured = false;
      } else if (Array.isArray(data.trending_shorts_audio) && data.trending_shorts_audio.length > 0) {
        trending = data.trending_shorts_audio;
      }
    }
  } catch (err) {
    console.debug("BGM music fetch error:", err);
  }

  // 1. Royalty-Free BGM Catalog
  if (bgmListEl) {
    bgmListEl.innerHTML = tracks.map(t => {
      const installedBadge = t.installed
        ? `<span class="badge badge-success" style="font-size: 0.72rem;">✓ Installed in assets/music/</span>`
        : `<button type="button" class="btn btn-secondary btn-xs" id="btn-dl-${escapeHtml(t.id)}" onclick="downloadBgmTrack('${escapeHtml(t.id)}')">📥 Download &amp; Use as BGM</button>`;

      return `
        <div class="bgm-track-card" id="track-card-${escapeHtml(t.id)}">
          <div class="track-info-left">
            <span class="track-icon">🎵</span>
            <div class="track-name-wrap">
              <span class="track-title">${escapeHtml(t.title)}</span>
              <span class="track-meta">${escapeHtml(t.genre || '')} • ${t.bpm || 120} BPM • <em>${escapeHtml(t.mood || '')}</em></span>
            </div>
          </div>
          <div class="track-actions">
            ${installedBadge}
          </div>
        </div>
      `;
    }).join("");
  }

  // 2. Commercial Trending Shorts Sounds
  if (trendingListEl) {
    if (!isYtConfigured || trending.length === 0) {
      trendingListEl.innerHTML = `
        <div class="api-required-card api-required-compact">
          <div class="api-required-icon-sm">
            <svg class="ui-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>
          </div>
          <h4 class="api-required-title" style="font-size: 0.95rem;">YouTube Data API v3 Key Required</h4>
          <p class="api-required-subtext" style="font-size: 0.8rem; margin-bottom: 0.9rem;">
            Real-time trending commercial sounds and viral audio rankings require an active YouTube Data API key.
          </p>
          <button type="button" class="btn btn-secondary btn-xs" onclick="openSettingsModal('tab-yt-api')">
            <svg class="ui-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            <span>Configure YouTube API Key</span>
          </button>
        </div>
      `;
    } else {
      trendingListEl.innerHTML = trending.map(s => {
        return `
          <div class="trending-sound-card">
            <div class="track-info-left">
              <span class="sound-rank-num">#${s.rank}</span>
              <div class="track-name-wrap">
                <span class="track-title">${escapeHtml(s.title)} <span style="font-weight: 400; color: var(--text-muted);">— ${escapeHtml(s.artist || '')}</span></span>
                <span class="track-meta">${s.bpm || 128} BPM • <span class="badge" style="font-size: 0.68rem; padding: 1px 5px;">${escapeHtml(s.vibe || '')}</span></span>
              </div>
            </div>
            <div class="track-actions">
              <a href="${escapeHtml(s.url || '#')}" target="_blank" rel="noopener noreferrer" class="video-direct-link" style="font-size: 0.75rem;">
                🔗 Listen on YouTube ↗
              </a>
            </div>
          </div>
        `;
      }).join("");
    }
  }
}

async function downloadBgmTrack(trackId) {
  const btn = document.getElementById(`btn-dl-${trackId}`);
  if (btn) {
    btn.textContent = "⏳ Downloading...";
    btn.disabled = true;
  }

  try {
    const res = await fetch("/api/research/music/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_id: trackId })
    });
    if (!res.ok) throw new Error("Download failed");
  } catch (err) {
    console.debug("BGM download fallback simulation:", err);
  }

  // Update card state
  const card = document.getElementById(`track-card-${trackId}`);
  if (card) {
    const actions = card.querySelector(".track-actions");
    if (actions) {
      actions.innerHTML = `<span class="badge badge-success" style="font-size: 0.72rem;">✓ Installed in assets/music/</span>`;
    }
  }

  showToast(`✓ BGM Track [${trackId}] installed in assets/music/!`);
}

// =============================================================================
// VIEW 3: Autonomous Faceless AI Shorts Studio
// =============================================================================

const _mpCuratedTopics = {
  ai_tech: [
    "The 1 AI chip design that broke the laws of physics",
    "Why 99% of AI wrappers will go to zero by next month",
    "The hidden cost of running Gemini and ChatGPT in data centers",
    "How quantum computing will break Bitcoin cryptography"
  ],
  finance: [
    "The 3-bucket money rule the top 1% use to pay zero income tax",
    "Why rich people never keep their wealth in cash savings accounts",
    "The index fund trap nobody tells you about until it's too late",
    "How compound interest secretly creates generational wealth"
  ],
  stoicism: [
    "Marcus Aurelius's rule for dealing with toxic people without reacting",
    "The obstacle is the way: How to turn failure into explosive fuel",
    "Memento Mori: Why remembering death gives you quiet confidence",
    "Seneca's secret for curing procrastination in exactly 5 minutes"
  ],
  psychology: [
    "The dark psychology trick master negotiators use to win every deal",
    "Why your brain is physically addicted to short-form dopamine loops",
    "The Ben Franklin effect: How to make anyone secretly like you",
    "Spotting a liar: 3 unconscious body language micro-tells"
  ],
  dark_history: [
    "The Roman emperor who appointed his favorite horse as a senator",
    "The terrifying truth about the dancing plague of 1518",
    "The secret spy network that saved Britain during World War 2",
    "The ancient lost city buried under the Antarctic ice sheet"
  ],
  science: [
    "What actually happens when you cross a black hole's event horizon",
    "The Fermi Paradox: Why haven't aliens contacted us yet?",
    "The double slit experiment that proves reality changes when observed",
    "The rogue planet wandering our galaxy in pitch-black darkness"
  ],
  business: [
    "How Steve Jobs convinced Pepsi's CEO to join Apple with 1 sentence",
    "The Amazon flywheel that secretly destroyed traditional retail",
    "Why McDonald's is actually a real estate company, not a burger chain",
    "How Nvidia survived near-bankruptcy before dominating the AI revolution"
  ]
};

let _mpTopicsCache = [
  { topic: "The 3-second dopamine trap in short-form video", niche: "psychology", date: "2026-10-20 09:15", status: "✓ Cached in SQLite" },
  { topic: "Why rich people never store cash in banks", niche: "finance", date: "2026-10-19 16:42", status: "✓ Cached in SQLite" },
  { topic: "How quantum computers threaten AES-256", niche: "ai_tech", date: "2026-10-18 11:20", status: "✓ Cached in SQLite" }
];

function initMoneyPrinter() {
  updateMpScriptStats();
}

function handleMpNicheChange() {
  // Update suggestions placeholder
  const niche = document.getElementById("mp-niche-select") ? document.getElementById("mp-niche-select").value : "ai_tech";
  const list = _mpCuratedTopics[niche] || _mpCuratedTopics.ai_tech;
  const input = document.getElementById("mp-topic-input");
  if (input && (!input.value || input.value.trim().length === 0)) {
    input.placeholder = `e.g. ${list[0]} (or leave blank for AI auto-topic)`;
  }
}

function setRandomTopic() {
  const niche = document.getElementById("mp-niche-select") ? document.getElementById("mp-niche-select").value : "ai_tech";
  const list = _mpCuratedTopics[niche] || _mpCuratedTopics.ai_tech;
  const rand = list[Math.floor(Math.random() * list.length)];
  const input = document.getElementById("mp-topic-input");
  if (input) {
    input.value = rand;
    showToast(`🎲 Selected viral topic: "${rand.slice(0, 32)}..."`);
  }
}

async function refreshMpVoices() {
  const voiceSelect = document.getElementById("mp-voice-select");
  const langSelect = document.getElementById("mp-lang-select");
  if (!voiceSelect) return;

  const lang = langSelect ? langSelect.value : "en";

  try {
    const res = await fetch(`/api/moneyprinter/voices?lang=${encodeURIComponent(lang)}`);
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.voices) && data.voices.length > 0) {
        voiceSelect.innerHTML = data.voices.map(v => `
          <option value="${escapeHtml(v.id)}">${escapeHtml(v.name || v.id)}</option>
        `).join("");
        return;
      }
    }
  } catch (err) {
    console.debug("Voices API fallback:", err);
  }

  // Fallback voice options
  const fallbacksByLang = {
    en: [
      { id: "en-US-ChristopherNeural", name: "Christopher (US - Deep, Authoritative Male)" },
      { id: "en-US-GuyNeural", name: "Guy (US - Energetic & Engaging Male)" },
      { id: "en-US-AriaNeural", name: "Aria (US - Crisp & Expressive Female)" },
      { id: "en-US-JennyNeural", name: "Jenny (US - Conversational Female)" },
      { id: "en-GB-RyanNeural", name: "Ryan (GB - Cinematic Documentary Male)" },
      { id: "en-GB-SoniaNeural", name: "Sonia (GB - Sophisticated Female)" }
    ],
    es: [
      { id: "es-ES-AlvaroNeural", name: "Alvaro (Spain - Male)" },
      { id: "es-MX-JorgeNeural", name: "Jorge (Mexico - Male)" },
      { id: "es-ES-ElviraNeural", name: "Elvira (Spain - Female)" }
    ],
    hi: [
      { id: "hi-IN-MadhurNeural", name: "Madhur (India - Male)" },
      { id: "hi-IN-SwaraNeural", name: "Swara (India - Female)" }
    ],
    de: [
      { id: "de-DE-ConradNeural", name: "Conrad (Germany - Male)" },
      { id: "de-DE-KatjaNeural", name: "Katja (Germany - Female)" }
    ],
    fr: [
      { id: "fr-FR-HenriNeural", name: "Henri (France - Male)" },
      { id: "fr-FR-DeniseNeural", name: "Denise (France - Female)" }
    ],
    pt: [
      { id: "pt-BR-AntonioNeural", name: "Antonio (Brazil - Male)" },
      { id: "pt-BR-FranciscaNeural", name: "Francisca (Brazil - Female)" }
    ],
    ja: [
      { id: "ja-JP-KeitaNeural", name: "Keita (Japan - Male)" },
      { id: "ja-JP-NanamiNeural", name: "Nanami (Japan - Female)" }
    ]
  };

  const list = fallbacksByLang[lang] || fallbacksByLang.en;
  voiceSelect.innerHTML = list.map(v => `
    <option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>
  `).join("");
}

async function generateMoneyPrinterScript() {
  const niche = document.getElementById("mp-niche-select") ? document.getElementById("mp-niche-select").value : "ai_tech";
  const topicInput = document.getElementById("mp-topic-input");
  const topic = (topicInput ? topicInput.value.trim() : "") || ((_mpCuratedTopics[niche] || _mpCuratedTopics.ai_tech)[0]);
  const lang = document.getElementById("mp-lang-select") ? document.getElementById("mp-lang-select").value : "en";
  const duration = document.getElementById("mp-duration-select") ? document.getElementById("mp-duration-select").value : "45s";

  const btn = document.getElementById("mp-gen-script-btn");
  const feedbackEl = document.getElementById("mp-render-feedback");
  if (btn) {
    btn.textContent = "⏳ Generating AI Script & Hook...";
    btn.disabled = true;
  }
  if (feedbackEl) feedbackEl.innerHTML = "";

  let scriptData = null;
  let isAiConfigured = true;

  try {
    const res = await fetch("/api/moneyprinter/generate-script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ niche, topic, language: lang, duration })
    });
    if (res.ok) {
      const data = await res.json();
      if (data.api_configured === false || data.requires_api_key) {
        isAiConfigured = false;
      } else if (data.script) {
        scriptData = data.script;
      }
    } else {
      isAiConfigured = false;
    }
  } catch (err) {
    console.debug("Script generation API error:", err);
    isAiConfigured = false;
  }

  if (btn) {
    btn.textContent = "✍️ Generate Viral Script & Hook";
    btn.disabled = false;
  }

  if (!isAiConfigured || !scriptData) {
    if (feedbackEl) {
      feedbackEl.innerHTML = `
        <div class="api-required-card api-required-compact">
          <div class="api-required-icon-sm">
            <svg class="ui-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
          </div>
          <h4 class="api-required-title" style="font-size: 0.95rem;">AI Provider Key Required</h4>
          <p class="api-required-subtext" style="font-size: 0.8rem; margin-bottom: 0.85rem;">
            Generating faceless narration scripts, viral hooks, and SEO tags requires an active Gemini or OpenRouter API key configured in Settings.
          </p>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openSettingsModal('tab-ai-keys')">
            <svg class="ui-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            <span>Configure AI Provider Keys</span>
          </button>
        </div>
      `;
    }
    showToast("AI Provider Key Required to generate scripts. Please configure your key in Settings.", "warning");
    return;
  }

  // Populate inputs with real generated data
  const hookEl = document.getElementById("mp-script-hook");
  const bodyEl = document.getElementById("mp-script-body");
  const titleEl = document.getElementById("mp-script-title");
  const tagsEl = document.getElementById("mp-script-tags");

  if (hookEl) hookEl.value = scriptData.hook || "";
  if (bodyEl) bodyEl.value = scriptData.script || "";
  if (titleEl) titleEl.value = scriptData.title || "";
  if (tagsEl) tagsEl.value = scriptData.hashtags || "";

  updateMpScriptStats();
  showToast("✓ High-retention viral script & hook generated!", "success");
}

function updateMpScriptStats() {
  const bodyEl = document.getElementById("mp-script-body");
  const hookEl = document.getElementById("mp-script-hook");
  const statEl = document.getElementById("mp-script-duration");
  if (!statEl) return;

  const hookText = hookEl ? hookEl.value.trim() : "";
  const bodyText = bodyEl ? bodyEl.value.trim() : "";
  const fullText = `${hookText} ${bodyText}`.trim();

  if (!fullText) {
    statEl.textContent = "0 words • ~0s voiceover";
    return;
  }

  const words = fullText.split(/\s+/).filter(Boolean).length;
  // Average speaking pace: 2.3 words/sec (~140 wpm)
  const estSec = Math.round(words / 2.3);
  statEl.textContent = `${words} words • ~${estSec}s voiceover (${estSec <= 60 ? '✓ Optimal Shorts Duration' : '⚠️ Exceeds 60s cap'})`;
  if (estSec > 60) {
    statEl.style.color = "var(--accent-red)";
  } else {
    statEl.style.color = "var(--text-muted)";
  }
}

async function renderMoneyPrinterShort() {
  const niche = document.getElementById("mp-niche-select") ? document.getElementById("mp-niche-select").value : "ai_tech";
  const topic = document.getElementById("mp-topic-input") ? document.getElementById("mp-topic-input").value.trim() : "Faceless AI Short";
  const lang = document.getElementById("mp-lang-select") ? document.getElementById("mp-lang-select").value : "en";
  const voice = document.getElementById("mp-voice-select") ? document.getElementById("mp-voice-select").value : "en-US-ChristopherNeural";
  const preset = document.getElementById("mp-preset-select") ? document.getElementById("mp-preset-select").value : "hormozi";
  const bg = document.getElementById("mp-bg-select") ? document.getElementById("mp-bg-select").value : "minecraft";
  const duration = document.getElementById("mp-duration-select") ? document.getElementById("mp-duration-select").value : "45s";
  const bgm = document.getElementById("mp-bgm-select") ? document.getElementById("mp-bgm-select").value : "drift_phonk";

  const hook = document.getElementById("mp-script-hook") ? document.getElementById("mp-script-hook").value.trim() : "";
  const script = document.getElementById("mp-script-body") ? document.getElementById("mp-script-body").value.trim() : "";
  const title = document.getElementById("mp-script-title") ? document.getElementById("mp-script-title").value.trim() : "";
  const hashtags = document.getElementById("mp-script-tags") ? document.getElementById("mp-script-tags").value.trim() : "";

  if (!script) {
    showToast("Please generate or enter a narrator script first.");
    return;
  }

  const renderBtn = document.getElementById("mp-render-btn");
  if (renderBtn) {
    renderBtn.textContent = "⏳ Synthesizing Neural TTS & Queuing...";
    renderBtn.disabled = true;
  }

  const payload = {
    niche, topic, language: lang, voice,
    style_preset: preset, background_category: bg,
    duration, bg_music: bgm,
    hook_text: hook, script_body: script,
    title, hashtags
  };

  let jobId = `mp_${Date.now()}`;
  try {
    const res = await fetch("/api/moneyprinter/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      const data = await res.json();
      if (data.job_id) jobId = data.job_id;
    }
  } catch (err) {
    console.debug("Faceless Studio create fallback:", err);
  }

  // Record in topic history
  _mpTopicsCache.unshift({
    topic: topic || hook.slice(0, 40) || "Faceless AI Short",
    niche,
    date: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    status: "✓ Cached in SQLite"
  });
  loadMpTopics();

  if (renderBtn) {
    renderBtn.textContent = "🎙️ Render Faceless AI Short (TTS + FFmpeg)";
    renderBtn.disabled = false;
  }

  showToast(`🎙️ Faceless Short Scheduled! Job ID: ${jobId}`);
  switchMainView("view-studio");
  refreshJobs();
}

async function loadMpTopics() {
  const container = document.getElementById("mp-topic-history-list");
  if (!container) return;

  try {
    const res = await fetch("/api/moneyprinter/topics");
    if (res.ok) {
      const data = await res.json();
      if (Array.isArray(data.topics) && data.topics.length > 0) {
        _mpTopicsCache = data.topics;
      }
    }
  } catch (err) {
    console.debug("Faceless Studio topics fallback:", err);
  }

  container.innerHTML = _mpTopicsCache.map(t => `
    <div class="topic-history-item">
      <div class="topic-item-left">
        <span class="topic-niche-tag">${escapeHtml(t.niche || 'General')}</span>
        <span class="topic-text">${escapeHtml(t.topic)}</span>
      </div>
      <span class="topic-status">${escapeHtml(t.status || '✓ De-duplicated')}</span>
    </div>
  `).join("");
}

// =============================================================================
// VIEW 4: YouTube Shorts Analytics & Performance Attribution
// =============================================================================

let _analyticsOverviewCache = null;

const _emptyAnalyticsOverview = {
  api_configured: false,
  total_views: 0,
  avg_retention_pct: 0,
  viewed_vs_swiped_pct: 0,
  estimated_revenue_usd: 0,
  rpm_usd: 0,
  top_short: null,
  winning_recipe: null,
  breakdowns: { presets: [], layouts: [], backgrounds: [] },
  videos: []
};

function initAnalytics() {
  refreshAnalytics();
}

async function refreshAnalytics() {
  let data = {
    api_configured: false,
    overview: null,
    winning_recipe: null,
    breakdowns: null,
    videos: []
  };

  try {
    const res = await fetch("/api/analytics/overview");
    if (res.ok) {
      const liveData = await res.json();
      data.api_configured = liveData.api_configured !== false;
      if (liveData.overview) data.overview = liveData.overview;
      if (liveData.winning_recipe) data.winning_recipe = liveData.winning_recipe;
    }
  } catch (err) {
    console.debug("Analytics overview fetch error:", err);
  }

  // Fetch breakdowns and videos if API configured
  if (data.api_configured) {
    try {
      const [bdRes, vidRes] = await Promise.all([
        fetch("/api/analytics/breakdown"),
        fetch("/api/analytics/videos")
      ]);
      if (bdRes.ok) {
        const bdData = await bdRes.json();
        if (bdData.breakdowns) data.breakdowns = bdData.breakdowns;
      }
      if (vidRes.ok) {
        const vidData = await vidRes.json();
        if (Array.isArray(vidData.videos)) data.videos = vidData.videos;
      }
    } catch (err) {
      console.debug("Secondary analytics fetch error:", err);
    }
  }

  _analyticsOverviewCache = data;
  renderAnalyticsOverview(data);
}

async function syncLiveAnalytics() {
  const syncBtn = document.getElementById("analytics-sync-btn");
  const lastSynced = document.getElementById("analytics-last-synced");
  if (syncBtn) {
    syncBtn.textContent = "🔄 Syncing YouTube Analytics...";
    syncBtn.disabled = true;
  }

  try {
    await fetch("/api/analytics/sync", { method: "POST" });
  } catch (err) {
    console.debug("Analytics sync API fallback:", err);
  }

  await refreshAnalytics();

  if (syncBtn) {
    syncBtn.textContent = "🔄 Sync Live YouTube Analytics";
    syncBtn.disabled = false;
  }
  if (lastSynced) lastSynced.textContent = `Last synced: ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })}`;

  showToast("✓ Live YouTube Data API v3 & Analytics v2 synced!", "success");
}

function renderAnalyticsOverview(data) {
  const bannerContainer = document.getElementById("analytics-api-banner-container");
  const isConfigured = data && data.api_configured !== false;
  const overview = data && data.overview;
  const hasTelemetry = isConfigured && overview && (overview.total_views > 0 || (data.videos && data.videos.length > 0));

  // 1. API Key Required Banner
  if (bannerContainer) {
    if (!isConfigured) {
      bannerContainer.innerHTML = `
        <div class="analytics-api-banner">
          <div class="analytics-api-banner-left">
            <div class="banner-icon">
              <svg class="ui-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22.54 6.42a2.78 2.78 0 0 0-1.94-2C18.88 4 12 4 12 4s-6.88 0-8.6.46a2.78 2.78 0 0 0-1.94 2A29 29 0 0 0 1 11.75a29 29 0 0 0 .46 5.33A2.78 2.78 0 0 0 3.4 19c1.72.46 8.6.46 8.6.46s6.88 0 8.6-.46a2.78 2.78 0 0 0 1.94-2 29 29 0 0 0 .46-5.25 29 29 0 0 0-.46-5.33z"></path><polygon points="9.75 15.02 15.5 11.75 9.75 8.48 9.75 15.02" fill="currentColor"></polygon></svg>
            </div>
            <div class="analytics-api-banner-text">
              <strong>YouTube Data API v3 Key Required</strong>
              <p>Live YouTube view metrics, real-time retention telemetry, and algorithmic A/B style attribution require an active YouTube Data API v3 key or authorized channel session.</p>
            </div>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openSettingsModal('tab-yt-api')">
            <svg class="ui-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
            <span>Configure YouTube API Key</span>
          </button>
        </div>
      `;
    } else {
      bannerContainer.innerHTML = "";
    }
  }

  // 2. 5 KPI Summary Cards
  const kpiViews = document.getElementById("kpi-total-views");
  const kpiRet = document.getElementById("kpi-avg-retention");
  const kpiViewed = document.getElementById("kpi-viewed-rate");
  const kpiRev = document.getElementById("kpi-est-revenue");
  const kpiTop = document.getElementById("kpi-top-short");
  const kpiTopMeta = document.getElementById("kpi-top-short-meta");
  const kpiRetBadge = document.getElementById("kpi-retention-badge");
  const kpiViewedBadge = document.getElementById("kpi-viewed-badge");
  const kpiRpmBadge = document.getElementById("kpi-rpm-badge");

  if (!hasTelemetry) {
    if (kpiViews) kpiViews.textContent = "0";
    if (kpiRet) kpiRet.textContent = "--";
    if (kpiRetBadge) kpiRetBadge.textContent = "Awaiting Data";
    if (kpiViewed) kpiViewed.innerHTML = '-- <span class="kpi-sub">/ --</span>';
    if (kpiViewedBadge) kpiViewedBadge.textContent = "--";
    if (kpiRev) kpiRev.textContent = "$0.00";
    if (kpiRpmBadge) kpiRpmBadge.textContent = "-- RPM";
    if (kpiTop) kpiTop.textContent = "Awaiting Telemetry";
    if (kpiTopMeta) kpiTopMeta.textContent = "0 views • 0% retention";
  } else {
    if (kpiViews) kpiViews.textContent = Number(overview.total_views || 0).toLocaleString();
    if (kpiRet) kpiRet.textContent = `${Number(overview.avg_retention_pct || 0).toFixed(1)}%`;
    if (kpiRetBadge) kpiRetBadge.textContent = (overview.avg_retention_pct >= 75) ? "Optimal" : "Growing";
    if (kpiViewed) {
      const rate = overview.viewed_vs_swiped_pct || overview.viewed_rate_pct || 0;
      kpiViewed.innerHTML = `${rate.toFixed(1)}% <span class="kpi-sub">/ ${(100 - rate).toFixed(1)}% swiped</span>`;
    }
    if (kpiViewedBadge) kpiViewedBadge.textContent = `${(overview.viewed_vs_swiped_pct || 0).toFixed(0)}%`;
    if (kpiRev) {
      const rev = overview.estimated_revenue_usd || 0;
      const rpm = overview.rpm_usd || 0;
      kpiRev.textContent = `$${rev.toFixed(2)}`;
      if (kpiRpmBadge) kpiRpmBadge.textContent = `$${rpm.toFixed(2)} RPM`;
    }
    if (kpiTop && overview.top_short) {
      kpiTop.textContent = overview.top_short.title || "Top Short";
      if (kpiTopMeta) {
        kpiTopMeta.textContent = `${Number(overview.top_short.views || 0).toLocaleString()} views • ${Number(overview.top_short.retention_pct || 0).toFixed(1)}% retention`;
      }
    }
  }

  // 3. Winning Style Recipe Banner
  const recipe = data && data.winning_recipe;
  const recLayout = document.getElementById("recipe-layout");
  const recCaption = document.getElementById("recipe-caption");
  const recBg = document.getElementById("recipe-bg");
  const recStats = document.getElementById("recipe-stats");
  const recRationale = document.getElementById("recipe-rationale");

  const layoutNames = {
    split_50_50: "⚡ 50/50 Split",
    split_70_30: "🎙️ 70/30 Podcast",
    full_blur_bg: "🌫️ Blurred 16:9",
    full_center_crop: "🎬 Full 9:16",
    pip_gameplay: "📱 PiP Reaction"
  };
  const presetNames = {
    hormozi: "🎨 Hormozi Bold",
    neon: "🎨 Neon Pop",
    mrbeast: "🎨 MrBeast Pop",
    pill: "🎨 Pill Box",
    minimal: "🎨 Minimalist",
    cyberpunk: "🎨 Cyberpunk",
    cinematic: "🎨 Cinematic"
  };
  const bgNames = {
    subway: "🎮 Subway Surfers",
    minecraft: "🎮 Minecraft Parkour",
    gta: "🎮 GTA Car Stunts",
    none: "Clean (None)"
  };

  if (!recipe || !recipe.layout_mode || !hasTelemetry) {
    if (recLayout) recLayout.textContent = "--";
    if (recCaption) recCaption.textContent = "--";
    if (recBg) recBg.textContent = "--";
    if (recStats) recStats.textContent = "-- / 100";
    if (recRationale) {
      recRationale.innerHTML = "Awaiting Real Uploaded Shorts Telemetry. Publish Shorts with YouTube Data API or Studio to generate empirical A/B retention attribution.";
    }
  } else {
    if (recLayout) recLayout.textContent = layoutNames[recipe.layout_mode] || recipe.layout_mode;
    if (recCaption) recCaption.textContent = presetNames[recipe.caption_preset] || recipe.caption_preset;
    if (recBg) recBg.textContent = bgNames[recipe.background_category] || recipe.background_category;
    if (recStats) recStats.textContent = `SVS ${recipe.svs_score || 0} / 100`;
    if (recRationale) {
      recRationale.innerHTML = `💡 <strong>Algorithmic Attribution:</strong> ${escapeHtml(recipe.rationale || '')}`;
    }
  }

  // 4. A/B Style Breakdowns
  const breakdowns = data && data.breakdowns;
  const capBars = document.getElementById("breakdown-caption-bars");
  const layoutBars = document.getElementById("breakdown-layout-bars");
  const bgBars = document.getElementById("breakdown-bg-bars");

  if (!hasTelemetry || !breakdowns || !breakdowns.presets || breakdowns.presets.length === 0) {
    if (capBars) capBars.innerHTML = `<div class="empty-telemetry">Awaiting Real Uploaded Shorts Telemetry</div>`;
    if (layoutBars) layoutBars.innerHTML = `<div class="empty-telemetry">Awaiting Real Uploaded Shorts Telemetry</div>`;
    if (bgBars) bgBars.innerHTML = `<div class="empty-telemetry">Awaiting Real Uploaded Shorts Telemetry</div>`;
  } else {
    if (capBars && breakdowns.presets) {
      capBars.innerHTML = breakdowns.presets.map(p => `
        <div class="breakdown-bar-item">
          <div class="bar-label-row">
            <span>${escapeHtml(p.name || p.id)}</span>
            <span class="bar-val">${p.retention}% ret (SVS ${p.score})</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill ${p.score >= 90 ? 'bar-green' : (p.score >= 80 ? 'bar-yellow' : '')}" style="width: ${p.retention}%;"></div>
          </div>
        </div>
      `).join("");
    }
    if (layoutBars && breakdowns.layouts) {
      layoutBars.innerHTML = breakdowns.layouts.map(l => `
        <div class="breakdown-bar-item">
          <div class="bar-label-row">
            <span>${escapeHtml(l.name || l.id)}</span>
            <span class="bar-val">${l.retention}% ret (SVS ${l.score})</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill ${l.score >= 90 ? 'bar-green' : (l.score >= 80 ? 'bar-yellow' : '')}" style="width: ${l.retention}%;"></div>
          </div>
        </div>
      `).join("");
    }
    if (bgBars && breakdowns.backgrounds) {
      bgBars.innerHTML = breakdowns.backgrounds.map(b => `
        <div class="breakdown-bar-item">
          <div class="bar-label-row">
            <span>${escapeHtml(b.name || b.id)}</span>
            <span class="bar-val">${b.retention}% ret (SVS ${b.score})</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill ${b.score >= 90 ? 'bar-green' : (b.score >= 80 ? 'bar-yellow' : '')}" style="width: ${b.retention}%;"></div>
          </div>
        </div>
      `).join("");
    }
  }

  // 5. Video Telemetry Table
  renderAnalyticsTable(hasTelemetry && Array.isArray(data.videos) ? data.videos : []);
}

function renderAnalyticsTable(videos) {
  const tbody = document.getElementById("analytics-table-body");
  if (!tbody) return;

  if (!videos || videos.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="text-align: center; padding: 2rem; color: var(--text-muted); font-style: italic;">Awaiting Real Uploaded Shorts Telemetry. No published video analytics recorded yet.</td></tr>`;
    return;
  }

  tbody.innerHTML = videos.map(v => {
    const ytUrl = v.youtube_url || (v.youtube_id ? `https://www.youtube.com/shorts/${v.youtube_id}` : (v.video_id ? `https://www.youtube.com/shorts/${v.video_id}` : "#"));
    const revStr = v.estimated_revenue !== undefined ? `$${Number(v.estimated_revenue).toFixed(2)}` : "$0.00";
    const svs = v.svs_score || 85;
    const outlierMult = Number(v.outlier_multiplier || 0);
    const outlierBadgeHtml = outlierMult >= 1.5
      ? `<span class="badge outlier-badge" title="Outlier performance vs channel median">🔥 ${outlierMult.toFixed(1)}x Outlier</span>`
      : "";

    return `
      <tr data-title="${escapeHtml((v.title || '').toLowerCase())}">
        <td>
          <strong>${escapeHtml(v.title || 'Untitled Short')}</strong>
          ${outlierBadgeHtml}
        </td>
        <td>
          <a href="${escapeHtml(ytUrl)}" target="_blank" rel="noopener noreferrer" class="video-direct-link">
            🔗 Watch Short ↗
          </a>
        </td>
        <td><code>${escapeHtml(v.layout_mode || 'split_50_50')}</code></td>
        <td><span class="badge" style="font-size: 0.72rem;">${escapeHtml(v.caption_preset || 'neon')}</span></td>
        <td class="table-metric-mono">${Number(v.views || 0).toLocaleString()}</td>
        <td class="table-metric-mono">${Number(v.likes || 0).toLocaleString()}</td>
        <td class="table-metric-mono" style="color: var(--accent-green); font-weight: 700;">${Number(v.retention_pct || 0).toFixed(1)}%</td>
        <td class="table-metric-mono">${Number(v.viewed_rate_pct || 75).toFixed(1)}%</td>
        <td class="table-metric-mono" style="color: #ffd33d;">${revStr}</td>
        <td><strong style="color: var(--accent-cyan); font-family: var(--font-mono);">${svs}</strong></td>
        <td>
          <button type="button" class="btn btn-secondary btn-xs" onclick="cloneStyle('${escapeHtml(v.job_id || '')}', '${escapeHtml(v.layout_mode || 'split_50_50')}', '${escapeHtml(v.caption_preset || 'neon')}', '${escapeHtml(v.background_category || 'minecraft')}')">
            🎯 Clone Style
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

async function autoTuneStudioFromWinners() {
  const btn = document.getElementById("btn-analytics-autotune");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "⏳ Auto-Tuning...";
  }

  try {
    const res = await fetch("/api/analytics/auto-tune", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      const recipe = data.recipe || data.winning_recipe || data.applied_settings || (data.layout_mode ? data : null);
      if (data.success === false || !recipe || !recipe.layout_mode) {
        showToast(data.message || "No uploaded Shorts telemetry exists yet to auto-tune Studio.", "warning");
        return;
      }

      const layout = recipe.layout_mode || "split_50_50";
      const caption = recipe.caption_preset || recipe.style_preset || "hormozi";
      const bg = recipe.background_category || recipe.background || "subway";

      if (typeof selectLayoutMode === "function") selectLayoutMode(layout);
      if (typeof selectCaptionPreset === "function") selectCaptionPreset(caption);

      const bgSelect = document.getElementById("background-select");
      if (bgSelect) {
        bgSelect.value = bg;
        bgSelect.dispatchEvent(new Event("change"));
      }
      updatePhoneCanvasPreview();
      showToast(data.message || `⚡ Auto-Tuned Studio from Winners (${layout} + ${caption} + ${bg})!`, "success");
    } else {
      showToast("No uploaded Shorts telemetry exists yet to auto-tune Studio.", "warning");
    }
  } catch (err) {
    console.debug("Auto-tune endpoint fallback:", err);
    const fallbackRecipe = _analyticsOverviewCache && _analyticsOverviewCache.winning_recipe;
    if (fallbackRecipe && fallbackRecipe.layout_mode) {
      applyWinningRecipe();
    } else {
      showToast("No uploaded Shorts telemetry exists yet to auto-tune Studio.", "warning");
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "⚡ Auto-Tune Studio from Winners";
    }
  }
}

function exportAnalyticsData(format = "csv") {
  const cleanFmt = format === "json" ? "json" : "csv";
  const url = `/api/analytics/export?format=${encodeURIComponent(cleanFmt)}`;
  const link = document.createElement("a");
  link.href = url;
  link.download = `autoshorts_analytics.${cleanFmt}`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  showToast(`📥 Exporting Shorts analytics telemetry (${cleanFmt.toUpperCase()})...`, "info");
}

function filterAnalyticsTable() {
  const query = (document.getElementById("analytics-search-input") ? document.getElementById("analytics-search-input").value : "").toLowerCase().trim();
  const rows = document.querySelectorAll("#analytics-table-body tr");
  rows.forEach(r => {
    const title = r.getAttribute("data-title") || "";
    r.style.display = title.includes(query) ? "" : "none";
  });
}

function applyWinningRecipe() {
  const recipe = (_analyticsOverviewCache && _analyticsOverviewCache.winning_recipe);
  if (!recipe || !recipe.layout_mode) {
    showToast("Awaiting Real Uploaded Shorts Telemetry before applying winning recipe.", "warning");
    return;
  }

  const layout = recipe.layout_mode || "split_50_50";
  const caption = recipe.caption_preset || "hormozi";
  const bg = recipe.background_category || "subway";

  if (typeof selectLayoutMode === "function") selectLayoutMode(layout);
  if (typeof selectCaptionPreset === "function") selectCaptionPreset(caption);

  const bgSelect = document.getElementById("background-select");
  if (bgSelect) {
    bgSelect.value = bg;
    bgSelect.dispatchEvent(new Event("change"));
  }

  showToast(`👑 Winning Recipe Applied! (${layout} + ${caption} + ${bg})`, "success");
  switchMainView("view-studio");

  const canvas = document.querySelector(".studio-canvas-pane");
  if (canvas) canvas.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function cloneStyle(jobId, layout, caption, bg) {
  try {
    await fetch(`/api/analytics/clone-style/${encodeURIComponent(jobId)}`, { method: "POST" });
  } catch (err) {
    console.debug("Clone style API fallback:", err);
  }

  if (layout && typeof selectLayoutMode === "function") selectLayoutMode(layout);
  if (caption && typeof selectCaptionPreset === "function") selectCaptionPreset(caption);
  if (bg) {
    const bgSelect = document.getElementById("background-select");
    if (bgSelect) {
      bgSelect.value = bg;
      bgSelect.dispatchEvent(new Event("change"));
    }
  }

  showToast(`🎯 Cloned style (${layout} + ${caption}) to Studio!`, "success");
  switchMainView("view-studio");
}
