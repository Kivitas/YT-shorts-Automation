// AutoShorts Dashboard Client

document.addEventListener("DOMContentLoaded", () => {
  refreshHealth();
  refreshQuota();
  refreshScheduler();
  refreshJobs();

  // Auto-refresh every 4 seconds
  setInterval(() => {
    refreshQuota();
    refreshScheduler();
    refreshJobs();
  }, 4000);
});

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

async function refreshQuota() {
  try {
    const res = await fetch("/api/quota");
    if (!res.ok) return;
    const q = await res.json();

    // Upload bucket
    const upSpend = q.upload_spent || 0;
    const upLimit = q.upload_limit || 100;
    const upPct = Math.min(100, (upSpend / upLimit) * 100);
    document.getElementById("upload-spend").innerHTML = `${upSpend} <span class="metric-sub">/ ${upLimit} calls</span>`;
    document.getElementById("upload-bar").style.width = `${upPct}%`;
    document.getElementById("upload-remaining").textContent = `${q.upload_remaining} remaining today (${q.date_pt || "PT"})`;

    // General bucket
    const genSpend = q.general_spent || 0;
    const genLimit = q.general_limit || 10000;
    const genPct = Math.min(100, (genSpend / genLimit) * 100);
    document.getElementById("general-spend").innerHTML = `${genSpend} <span class="metric-sub">/ ${genLimit} units</span>`;
    document.getElementById("general-bar").style.width = `${genPct}%`;
    document.getElementById("general-remaining").textContent = `${q.general_remaining} remaining pool units`;

    // Daily Cap
    const completed = q.completed_today || 0;
    const dailyCap = q.daily_cap || 50;
    document.getElementById("daily-cap-val").innerHTML = `${completed} <span class="metric-sub">/ ${dailyCap} uploaded</span>`;
  } catch (err) {
    console.debug("Quota refresh error:", err);
  }
}

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

async function refreshJobs() {
  try {
    const res = await fetch("/api/jobs");
    if (!res.ok) return;
    const data = await res.json();
    renderJobs(data.jobs || []);
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

    let actions = "";
    if (hasVideo) {
      actions += `<button class="btn btn-secondary btn-sm" onclick="openPreview('${job.id}', '${encodeURIComponent(title)}')">▶ Preview</button>`;
    }
    if (job.status === "READY_FOR_REVIEW" || job.status === "SCHEDULED") {
      actions += `<button class="btn btn-primary btn-sm" onclick="uploadNow('${job.id}')">🚀 Upload Now</button>`;
    }
    if (job.status === "FAILED") {
      actions += `<button class="btn btn-secondary btn-sm" onclick="retryJob('${job.id}')">🔄 Retry</button>`;
    }

    const errorHtml = job.error_message ? `<div style="color: var(--accent-red); font-size: 0.75rem; margin-top: 0.35rem;">⚠️ ${job.error_message}</div>` : "";

    return `
      <div class="job-card" id="job-${job.id}">
        <div class="job-header">
          <span class="job-id">#${job.id}</span>
          <span class="status-badge status-${job.status}">${job.status}</span>
        </div>
        <div class="job-title" title="${title}">${title}</div>
        <div class="job-meta">
          <span>${channel}</span>
          <span>Updated: ${new Date(job.updated_at).toLocaleTimeString()}</span>
        </div>
        ${errorHtml}
        <div class="job-actions">${actions}</div>
      </div>
    `;
  }).join("");
}

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
  } catch (err) {
    alert("Network error: " + err);
  }
}

async function retryJob(jobId) {
  try {
    const res = await fetch(`/api/jobs/${jobId}/retry`, { method: "POST" });
    if (res.ok) refreshJobs();
  } catch (err) {
    alert("Failed to retry job: " + err);
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
