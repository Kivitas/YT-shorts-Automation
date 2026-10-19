// AutoShorts Dashboard Client

let _jobsCache = [];
let _ptSecondsRemaining = null;
let _eventSource = null;

document.addEventListener("DOMContentLoaded", () => {
  refreshHealth();
  refreshQuota();
  refreshScheduler();
  refreshJobs();
  initLogStream();

  // PT Countdown ticking every 1 second
  setInterval(() => {
    tickPtCountdown();
  }, 1000);

  // Auto-refresh metrics and jobs every 4 seconds
  setInterval(() => {
    refreshQuota();
    refreshScheduler();
    refreshJobs();
  }, 4000);
});

// -----------------------------------------------------------------------------
// System Health & Telemetry
// -----------------------------------------------------------------------------

async function refreshHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return;
    const data = await res.json();
    if (data.cpu_mode) document.getElementById("cpu-label").textContent = data.cpu_mode;
    if (data.llm_provider) document.getElementById("provider-label").textContent = `Provider: ${data.llm_provider}`;
    if (data.ffmpeg_encoder) document.getElementById("encoder-label").textContent = data.ffmpeg_encoder;
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

    // 1. Upload bucket
    const upSpend = q.upload_spent || 0;
    const upLimit = q.upload_limit || 100;
    const upPct = Math.min(100, (upSpend / upLimit) * 100);
    document.getElementById("upload-spend").innerHTML = `${upSpend} <span class="metric-sub">/ ${upLimit} calls</span>`;
    document.getElementById("upload-bar").style.width = `${upPct}%`;
    document.getElementById("upload-remaining").textContent = `${q.upload_remaining} remaining today (${q.date_pt || "PT"})`;

    // 2. General bucket
    const genSpend = q.general_spent || 0;
    const genLimit = q.general_limit || 10000;
    const genPct = Math.min(100, (genSpend / genLimit) * 100);
    document.getElementById("general-spend").innerHTML = `${genSpend} <span class="metric-sub">/ ${genLimit} units</span>`;
    document.getElementById("general-bar").style.width = `${genPct}%`;
    document.getElementById("general-remaining").textContent = `${q.general_remaining} remaining pool units`;

    // 3. Rolling Hour Rate Limit Gauge
    const rateCount = q.rolling_hour_count !== undefined ? q.rolling_hour_count : 0;
    const rateLimit = q.rolling_hour_limit || 7;
    const ratePct = Math.min(100, (rateCount / rateLimit) * 100);
    const rateValEl = document.getElementById("rolling-rate-val");
    if (rateValEl) {
      rateValEl.innerHTML = `${rateCount} <span class="metric-sub">/ ${rateLimit} uploads</span>`;
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
    const rateFooterEl = document.getElementById("rolling-rate-footer");
    if (rateFooterEl) {
      rateFooterEl.textContent = `${rateCount} / ${rateLimit} uploads this rolling hour`;
    }

    // 4. Daily Cap & Pacific-Time Countdown Sync
    const completed = q.completed_today || 0;
    const dailyCap = q.daily_cap || 50;
    document.getElementById("daily-cap-val").innerHTML = `${completed} <span class="metric-sub">/ ${dailyCap} uploaded today</span>`;

    if (q.seconds_until_reset !== undefined) {
      _ptSecondsRemaining = q.seconds_until_reset;
      renderPtCountdown();
    }
  } catch (err) {
    console.debug("Quota refresh error:", err);
  }
}

function tickPtCountdown() {
  if (_ptSecondsRemaining !== null && _ptSecondsRemaining > 0) {
    _ptSecondsRemaining -= 1;
    renderPtCountdown();
  }
}

function renderPtCountdown() {
  const clockEl = document.getElementById("pt-countdown-clock");
  if (!clockEl || _ptSecondsRemaining === null) return;

  const total = Math.max(0, _ptSecondsRemaining);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;

  const hStr = String(hours).padStart(2, "0");
  const mStr = String(minutes).padStart(2, "0");
  const sStr = String(seconds).padStart(2, "0");
  clockEl.textContent = `${hStr}:${mStr}:${sStr}`;
}

// -----------------------------------------------------------------------------
// Scheduler Controls
// -----------------------------------------------------------------------------

async function refreshScheduler() {
  try {
    const res = await fetch("/api/scheduler/status");
    if (!res.ok) return;
    const s = await res.json();

    const tag = document.getElementById("scheduler-status-tag");
    const btn = document.getElementById("scheduler-toggle-btn");
    const pending = document.getElementById("pending-slots-count");

    if (s.paused) {
      tag.textContent = "PAUSED";
      tag.style.color = "var(--accent-red)";
      btn.textContent = "Resume Scheduler";
      btn.className = "btn btn-primary btn-sm";
    } else {
      tag.textContent = "ACTIVE";
      tag.style.color = "var(--accent-green)";
      btn.textContent = "Pause Scheduler";
      btn.className = "btn btn-secondary btn-sm";
    }

    pending.textContent = `${s.pending_count || 0} queued slots`;
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
// Job Queue & Rendering
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

function renderJobs(jobs) {
  const container = document.getElementById("jobs-container");
  if (!jobs || jobs.length === 0) {
    container.innerHTML = '<div class="empty-state">No active clipping jobs. Enter a YouTube URL to get started.</div>';
    return;
  }

  container.innerHTML = jobs.map(job => {
    const title = (job.video_metadata && job.video_metadata.title) ? job.video_metadata.title : job.source_url;
    const channel = (job.video_metadata && job.video_metadata.channel) ? `by ${job.video_metadata.channel}` : "";
    const hasVideo = job.rendered_video_path || job.preview_video_path;
    const ytId = job.youtube_video_id || (job.upload_result && job.upload_result.video_id);

    // 1. Actions Buttons
    let actions = "";
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

    // 2. Copy Toolbar (SEO Title, Description, Hashtags)
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
          <span>Updated: ${new Date(job.updated_at).toLocaleTimeString()}</span>
        </div>
        ${stepperHtml}
        ${copyToolbar}
        ${errorHtml}
        <div class="job-actions">${actions}</div>
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
    // Fallback for non-https or restricted environments
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
    dot.className = "log-dot live";
    badge.textContent = "● Live Stream Connected";
    badge.style.color = "var(--accent-green)";
  };

  _eventSource.onerror = () => {
    dot.className = "log-dot";
    badge.textContent = "○ Reconnecting...";
    badge.style.color = "var(--accent-yellow)";
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

  // Clear initial placeholder if present
  const placeholder = terminal.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();

  // Determine log level class
  let levelClass = "log-info";
  let level = "INFO";
  if (msg.includes("[ERROR]") || msg.includes("ERROR:")) {
    levelClass = "log-error";
    level = "ERROR";
  } else if (msg.includes("[WARNING]") || msg.includes("WARNING:")) {
    levelClass = "log-warning";
    level = "WARN";
  }

  const lineEl = document.createElement("div");
  lineEl.className = `log-line ${levelClass}`;
  lineEl.textContent = msg;

  terminal.appendChild(lineEl);

  // Keep max 500 lines
  while (terminal.childNodes.length > 500) {
    terminal.removeChild(terminal.firstChild);
  }

  // Auto-scroll if enabled
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

  const payload = {
    links: links,
    provider: document.getElementById("provider-select").value,
    background: document.getElementById("background-select").value,
    dopamine: document.getElementById("dopamine-select").value,
    upload_mode: document.getElementById("upload-mode-select").value,
    auto_upload: document.getElementById("auto-upload-toggle").checked,
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

    // Append to links input
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

async function triggerEdgeLogin() {
  const btn = document.getElementById("edge-login-btn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Launching Edge...";
  }
  showToast("Launching Microsoft Edge sign-in session...");
  try {
    const res = await fetch("/api/auth/edge-login", { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert("Failed to launch Edge login: " + (err.detail || "Server error"));
      return;
    }
    const data = await res.json();
    showToast(data.message || "Edge sign-in session opened!");
  } catch (err) {
    alert("Network error: " + err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "🔑 Edge Sign-In";
    }
  }
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
  video.pause();
  video.src = "";
  modal.style.display = "none";
}

function refreshDashboard() {
  refreshHealth();
  refreshQuota();
  refreshScheduler();
  refreshJobs();
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
