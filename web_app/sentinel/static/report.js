function renderBadge(status) {
  return `<span class="sentinel-badge sentinel-badge-${status}">${status}</span>`;
}

let sentinelLightbox;
let sentinelLightboxImage;

function escapeText(value) {
  return String(value ?? '').replace(/[&<>"']/g, function (char) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char];
  });
}

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function renderReport(report) {
  const status = document.getElementById('sentinel-report-status');
  const steps = document.getElementById('sentinel-steps');
  const stepCount = document.getElementById('sentinel-step-count');
  const findings = document.getElementById('sentinel-findings');
  const findingsCount = document.getElementById('sentinel-findings-count');
  const finalReport = document.getElementById('sentinel-final-report');
  const screenshots = document.getElementById('sentinel-screenshots');
  const screenshotCount = document.getElementById('sentinel-screenshot-count');

  if (status) {
    status.className = `sentinel-badge sentinel-badge-${report.status}`;
    status.textContent = report.status;
  }
  if (stepCount) stepCount.textContent = report.steps.length;
  if (findingsCount) findingsCount.textContent = report.findings.length;
  if (screenshotCount) screenshotCount.textContent = report.screenshots.length;

  if (finalReport) {
    finalReport.innerHTML = report.final_report_html || '<div class="sentinel-empty sentinel-empty-small">Final report will appear when the run completes.</div>';
  }

  if (steps) {
    steps.innerHTML = report.steps.length ? report.steps.map(function (step) {
      return `<article class="sentinel-step">
        <span>${step.index}</span>
        <div>
          <strong>${escapeText(step.action)}</strong>
          <p>${escapeText(step.reason)}</p>
          <details class="sentinel-step-result">
            <summary>Details</summary>
            <code>${escapeText(JSON.stringify(step.result))}</code>
          </details>
        </div>
      </article>`;
    }).join('') : '<div class="sentinel-empty sentinel-empty-small">Waiting for the first browser action.</div>';
  }

  if (findings) {
    findings.innerHTML = report.findings.length ? report.findings.map(function (finding) {
      return `<p class="sentinel-finding-line">
        ${renderBadge(escapeText(finding.severity))}
        <strong>${escapeText(finding.title)}</strong>
        <span>${escapeText(finding.detail)}</span>
      </p>`;
    }).join('') : '<div class="sentinel-empty sentinel-empty-small">No diagnostics recorded yet.</div>';
  }

  if (screenshots) {
    screenshots.innerHTML = report.screenshots.length ? report.screenshots.map(function (shot) {
      const filename = shot.split('/').pop();
      const url = `/sentinel/report/${report.run_id}/screenshots/${filename}`;
      return `<button class="sentinel-screenshot-btn" type="button" data-full="${url}">
        <img src="${url}" alt="QA screenshot">
      </button>`;
    }).join('') : '<div class="sentinel-empty sentinel-empty-small">No screenshots captured yet.</div>';
    bindScreenshotButtons();
  }
}

function ensureLightbox() {
  if (sentinelLightbox) return;
  sentinelLightbox = document.createElement('div');
  sentinelLightbox.className = 'sentinel-lightbox';
  sentinelLightbox.innerHTML = '<button class="sentinel-lightbox-close" type="button" aria-label="Close">&times;</button><img alt="QA screenshot">';
  document.body.appendChild(sentinelLightbox);
  sentinelLightboxImage = sentinelLightbox.querySelector('img');
  const close = function () {
    sentinelLightbox.classList.remove('open');
    sentinelLightboxImage.src = '';
  };
  sentinelLightbox.querySelector('.sentinel-lightbox-close').addEventListener('click', close);
  sentinelLightbox.addEventListener('click', function (event) {
    if (event.target === sentinelLightbox) close();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') close();
  });
}

function bindScreenshotButtons() {
  ensureLightbox();
  document.querySelectorAll('.sentinel-screenshot-btn').forEach(function (button) {
    if (button.dataset.boundLightbox) return;
    button.dataset.boundLightbox = 'true';
    button.addEventListener('click', function () {
      sentinelLightboxImage.src = button.dataset.full;
      sentinelLightbox.classList.add('open');
    });
  });
}

document.addEventListener('DOMContentLoaded', function () {
  const shell = document.querySelector('[data-run-id]');
  if (!shell) return;
  const runId = shell.dataset.runId;
  const rerunButton = document.getElementById('sentinel-rerun-run');
  bindScreenshotButtons();

  if (rerunButton) {
    rerunButton.addEventListener('click', async function () {
      rerunButton.disabled = true;
      try {
        const response = await fetch(`/sentinel/api/runs/${runId}/rerun`, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken() }
        });
        const data = await response.json();
        if (!response.ok) {
          rerunButton.disabled = false;
          return;
        }
        window.location.href = `/sentinel/report/${data.run_id}`;
      } catch (error) {
        rerunButton.disabled = false;
      }
    });
  }

  async function poll() {
    const response = await fetch(`/sentinel/api/runs/${runId}`);
    if (!response.ok) return;
    const report = await response.json();
    renderReport(report);
    if (['queued', 'running', 'summarizing'].includes(report.status)) {
      window.setTimeout(poll, 1500);
    }
  }

  poll();
});
