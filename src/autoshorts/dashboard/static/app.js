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
    actions += `<button class="btn btn-secondary btn-sm" onclick="openEditJobModal('${job.id}')" title="Edit clip title, hook, or gameplay theme">✏️ Edit</button>`;

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
// Edit Job Metadata Modal
// -----------------------------------------------------------------------------

function openEditJobModal(jobId) {
  const job = _jobsCache.find(j => j.id === jobId);
  if (!job) return;

  const modal = document.getElementById("edit-job-modal");
  const modalId = document.getElementById("edit-job-modal-id");
  const idInput = document.getElementById("edit-job-id");
  const titleInput = document.getElementById("edit-clip-title");
  const hookInput = document.getElementById("edit-hook-text");
  const bgSelect = document.getElementById("edit-background-category");
  const dopSelect = document.getElementById("edit-dopamine-level");

  if (modalId) modalId.textContent = `#${job.id}`;
  if (idInput) idInput.value = job.id;
  if (titleInput) {
    titleInput.value = job.clip_title || (job.video_metadata ? job.video_metadata.title : "");
  }
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

  if (modal) modal.style.display = "flex";
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

  const payload = {
    clip_title: document.getElementById("edit-clip-title").value.trim(),
    hook_text: document.getElementById("edit-hook-text").value.trim(),
    background_category: document.getElementById("edit-background-category").value,
    dopamine_level: document.getElementById("edit-dopamine-level").value,
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
      btn.textContent = "💾 Save Changes";
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

function showToast(msg) {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = msg;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.transition = "opacity 0.3s ease, transform 0.3s ease";
    toast.style.opacity = "0";
    toast.style.transform = "translateX(50px)";
    setTimeout(() => toast.remove(), 300);
  }, 2200);
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

  const payload = {
    links: links,
    provider: document.getElementById("provider-select").value,
    background: bgVal,
    dopamine: document.getElementById("dopamine-select").value,
    upload_mode: document.getElementById("upload-mode-select").value,
    auto_upload: document.getElementById("auto-upload-toggle").checked,
    style_preset: stylePreset,
    privacy_status: privacyStatus,
  };

  const btn = document.getElementById("submit-btn");
  btn.disabled = true;
  btn.textContent = "Submitting...";

  try {
    const res = await fetch("/api/jobs/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

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

function openSettingsModal() {
  const modal = document.getElementById("settings-modal");
  if (modal) modal.style.display = "flex";
  loadAllSettings();
  checkInitialYouTubeAuth();
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

  ["neon", "minimal", "pill"].forEach(p => {
    const card = document.getElementById(`preset-card-${p}`);
    if (card) {
      card.classList.toggle("active", p === val);
    }
  });

  const mainPreset = document.getElementById("style-preset-select");
  if (mainPreset && mainPreset.value !== val) {
    mainPreset.value = val;
  }
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