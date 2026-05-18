function renderBadge(status) {
  return `<span class="sentinel-badge sentinel-badge-${status}">${status}</span>`;
}

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
    finalReport.textContent = report.final_report || 'Final report will appear when the run completes.';
  }

  if (steps) {
    steps.innerHTML = report.steps.length ? report.steps.map(function (step) {
      return `<article class="sentinel-step">
        <span>${step.index}</span>
        <div>
          <strong>${escapeText(step.action)}</strong>
          <p>${escapeText(step.reason)}</p>
          <code>${escapeText(JSON.stringify(step.result))}</code>
        </div>
      </article>`;
    }).join('') : '<div class="sentinel-empty sentinel-empty-small">Waiting for the first browser action.</div>';
  }

  if (findings) {
    findings.innerHTML = report.findings.length ? report.findings.map(function (finding) {
      return `<div class="sentinel-finding">
        ${renderBadge(escapeText(finding.severity))}
        <strong>${escapeText(finding.title)}</strong>
        <p>${escapeText(finding.detail)}</p>
      </div>`;
    }).join('') : '<div class="sentinel-empty sentinel-empty-small">No findings recorded yet.</div>';
  }

  if (screenshots) {
    screenshots.innerHTML = report.screenshots.length ? report.screenshots.map(function (shot) {
      const filename = shot.split('/').pop();
      const url = `/sentinel/report/${report.run_id}/screenshots/${filename}`;
      return `<a href="${url}"><img src="${url}" alt="QA screenshot"></a>`;
    }).join('') : '<div class="sentinel-empty sentinel-empty-small">No screenshots captured yet.</div>';
  }
}

document.addEventListener('DOMContentLoaded', function () {
  const shell = document.querySelector('[data-run-id]');
  if (!shell) return;
  const runId = shell.dataset.runId;
  const rerunButton = document.getElementById('sentinel-rerun-run');

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
