function renderBadge(status) {
  return `<span class="sentinel-badge sentinel-badge-${status}">${status}</span>`;
}

let sentinelLightbox;
let sentinelLightboxImage;
let sentinelScreenshotLoader;

function escapeText(value) {
  return String(value ?? '').replace(/[&<>"']/g, function (char) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char];
  });
}

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

let sentinelCancelRequested = false;

function renderReport(report) {
  const status = document.getElementById('sentinel-report-status');
  const steps = document.getElementById('sentinel-steps');
  const stepCount = document.getElementById('sentinel-step-count');
  const findings = document.getElementById('sentinel-findings');
  const findingsCount = document.getElementById('sentinel-findings-count');
  const finalReport = document.getElementById('sentinel-final-report');
  const screenshots = document.getElementById('sentinel-screenshots');
  const screenshotCount = document.getElementById('sentinel-screenshot-count');

  const active = ['queued', 'running', 'summarizing'].includes(report.status);
  if (!active) sentinelCancelRequested = false;
  const displayStatus = sentinelCancelRequested && active ? 'cancelling' : report.status;

  if (status) {
    status.className = `sentinel-badge sentinel-badge-${displayStatus}`;
    status.textContent = displayStatus;
  }
  const title = document.getElementById('sentinel-report-title');
  if (title && report.title) {
    title.textContent = report.title;
  }
  const cancelButton = document.getElementById('sentinel-cancel-run');
  if (cancelButton) {
    cancelButton.hidden = !active;
    if (sentinelCancelRequested && active) {
      cancelButton.disabled = true;
      const label = cancelButton.querySelector('span');
      if (label) label.textContent = 'Cancelling...';
    } else if (!active) {
      cancelButton.disabled = false;
      const label = cancelButton.querySelector('span');
      if (label) label.textContent = 'Cancel';
    }
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
    syncScreenshots(screenshots, report);
    setupScreenshotLoading(report);
    bindScreenshotButtons();
  }
}

function syncScreenshots(container, report) {
  if (!report.screenshots.length) {
    container.innerHTML = '<div class="sentinel-empty sentinel-empty-small">No screenshots captured yet.</div>';
    return;
  }

  const empty = container.querySelector('.sentinel-empty');
  if (empty) empty.remove();

  const existing = container.querySelectorAll('.sentinel-screenshot-btn').length;
  for (let i = existing; i < report.screenshots.length; i++) {
    const filename = report.screenshots[i].split('/').pop();
    const url = `/sentinel/report/${report.run_id}/screenshots/${filename}`;
    const button = document.createElement('button');
    button.className = 'sentinel-screenshot-btn';
    button.type = 'button';
    button.dataset.full = url;
    button.innerHTML = `<img loading="lazy" decoding="async"
        src="data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA="
        data-screenshot-src="${url}" alt="QA screenshot">`;
    container.appendChild(button);
  }
}

function setupScreenshotLoading(report) {
  const shell = document.querySelector('[data-run-id]');
  const screenshotImages = Array.from(document.querySelectorAll('img[data-screenshot-src]'))
    .filter(img => img.dataset.loaded !== 'true');
  if (!shell || screenshotImages.length === 0) return;

  const fallback = function (value, defaultValue) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : defaultValue;
  };
  const staggerMs = fallback(report?.screenshot_load_stagger_ms ?? shell.dataset.screenshotStaggerMs, 200);
  const maxRetries = fallback(report?.screenshot_load_max_retries ?? shell.dataset.screenshotMaxRetries, 3);
  const retryDelayMs = fallback(report?.screenshot_load_retry_delay_ms ?? shell.dataset.screenshotRetryDelayMs, 1000);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  if (sentinelScreenshotLoader) {
    sentinelScreenshotLoader.disconnect();
    sentinelScreenshotLoader = null;
  }

  const loadImageAttempt = (img, url) => new Promise((resolve, reject) => {
    const onLoad = () => {
      img.removeEventListener('load', onLoad);
      img.removeEventListener('error', onError);
      img.dataset.loaded = 'true';
      resolve();
    };
    const onError = () => {
      img.removeEventListener('load', onLoad);
      img.removeEventListener('error', onError);
      reject(new Error('screenshot load failed'));
    };

    img.addEventListener('load', onLoad);
    img.addEventListener('error', onError);
    img.src = url;
  });

  const loadWithRetries = async (img, screenshotSrc) => {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      const suffix = attempt > 0
        ? `${screenshotSrc.includes('?') ? '&' : '?'}retry=${attempt}&_ts=${Date.now()}`
        : '';
      try {
        await loadImageAttempt(img, `${screenshotSrc}${suffix}`);
        return;
      } catch (_) {
        if (attempt >= maxRetries) return;
        await sleep(retryDelayMs * (attempt + 1));
      }
    }
  };

  const queue = [];
  const pending = new Set(screenshotImages);
  let isProcessing = false;

  const processQueue = async () => {
    if (isProcessing) return;
    isProcessing = true;
    while (queue.length > 0) {
      const img = queue.shift();
      if (img && img.dataset.screenshotSrc && img.dataset.loaded !== 'true') {
        await loadWithRetries(img, img.dataset.screenshotSrc);
        await sleep(staggerMs);
      }
    }
    isProcessing = false;
  };

  const enqueueImage = img => {
    if (!pending.has(img)) return;
    pending.delete(img);
    queue.push(img);
    processQueue();
  };

  if ('IntersectionObserver' in window) {
    sentinelScreenshotLoader = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        sentinelScreenshotLoader.unobserve(entry.target);
        enqueueImage(entry.target);
      }
    }, { root: null, rootMargin: '200px 0px', threshold: 0.01 });
    screenshotImages.forEach(img => sentinelScreenshotLoader.observe(img));
  } else {
    screenshotImages.forEach(img => enqueueImage(img));
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
  setupScreenshotLoading();
  bindScreenshotButtons();

  const cancelButton = document.getElementById('sentinel-cancel-run');
  if (cancelButton) {
    cancelButton.addEventListener('click', async function () {
      sentinelCancelRequested = true;
      cancelButton.disabled = true;
      const label = cancelButton.querySelector('span');
      if (label) label.textContent = 'Cancelling...';
      const status = document.getElementById('sentinel-report-status');
      if (status) {
        status.className = 'sentinel-badge sentinel-badge-cancelling';
        status.textContent = 'cancelling';
      }
      try {
        await fetch(`/sentinel/api/runs/${runId}/cancel`, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken() }
        });
      } catch (_) {
        sentinelCancelRequested = false;
        cancelButton.disabled = false;
        if (label) label.textContent = 'Cancel';
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
