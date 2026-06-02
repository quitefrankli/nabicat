function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

// --- Builder (sentinel_batches.html) --------------------------------------

function initBatchBuilder() {
  const form = document.getElementById('sentinel-batch-form');
  if (!form) return;
  const shell = document.querySelector('.sentinel-shell');
  const itemsContainer = document.getElementById('sentinel-batch-items');
  const template = document.getElementById('sentinel-batch-item-template');
  const addButton = form.querySelector('.sentinel-batch-add-item');
  const status = document.getElementById('sentinel-batch-status');
  const maxItems = parseInt(shell?.dataset.maxBatchItems || '8', 10);

  function wireToggle(item, checkboxSel, fieldsSel) {
    const checkbox = item.querySelector(checkboxSel);
    const fields = item.querySelector(fieldsSel);
    if (!checkbox || !fields) return;
    const sync = () => { fields.hidden = !checkbox.checked; };
    checkbox.addEventListener('change', sync);
    sync();
  }

  function addExtraRow(container, key = '', value = '') {
    const row = document.createElement('div');
    row.className = 'sentinel-account-extra-row';
    row.innerHTML = `
      <input class="form-control sentinel-account-extra-key" type="text" placeholder="field name" autocomplete="off">
      <input class="form-control sentinel-account-extra-value" type="text" placeholder="value" autocomplete="off">
      <button type="button" class="btn btn-sm btn-outline-secondary sentinel-account-extra-remove" aria-label="Remove field">
        <i class="bi bi-x-lg"></i>
      </button>
    `;
    row.querySelector('.sentinel-account-extra-key').value = key;
    row.querySelector('.sentinel-account-extra-value').value = value;
    row.querySelector('.sentinel-account-extra-remove').addEventListener('click', () => row.remove());
    container.appendChild(row);
  }

  function wireItem(item) {
    item.querySelector('.sentinel-batch-item-remove')?.addEventListener('click', () => {
      item.remove();
      updateAddState();
    });
    wireToggle(item, '.sentinel-batch-allow-external', '.sentinel-batch-additional-domains');
    wireToggle(item, '.sentinel-batch-allow-accounts', '.sentinel-batch-account-fields');
    wireToggle(item, '.sentinel-batch-allow-financial', '.sentinel-batch-card-fields');
    const extras = item.querySelector('.sentinel-account-extra-fields');
    item.querySelector('.sentinel-account-add-field')?.addEventListener('click', () => {
      if (extras) addExtraRow(extras);
    });
  }

  function addItem() {
    if (itemsContainer.children.length >= maxItems) return null;
    const node = template.content.firstElementChild.cloneNode(true);
    itemsContainer.appendChild(node);
    wireItem(node);
    updateAddState();
    return node;
  }

  function populateItem(node, data) {
    const set = (name, value) => {
      const el = node.querySelector(`[name="${name}"]`);
      if (el) el.value = value;
    };
    const check = (name, on) => {
      const el = node.querySelector(`[name="${name}"]`);
      if (el) {
        el.checked = !!on;
        // Fire the bound reveal-toggle so dependent field blocks match.
        el.dispatchEvent(new Event('change'));
      }
    };
    set('url', data.url || '');
    set('title', data.title || '');
    set('prompt', data.prompt || '');
    if (data.device) set('device', data.device);
    if (data.demographic) set('demographic', data.demographic);
    if (data.limit) set('limit', data.limit);
    set('additional_domains', data.additional_domains || '');
    check('allow_accounts', data.allow_accounts);
    check('allow_external', data.allow_external);
    check('allow_financial', data.allow_financial);
  }

  function updateAddState() {
    addButton.disabled = itemsContainer.children.length >= maxItems;
  }

  function serializeItem(item) {
    const get = (name) => item.querySelector(`[name="${name}"]`);
    const data = {
      url: get('url')?.value || '',
      title: get('title')?.value || '',
      prompt: get('prompt')?.value || '',
      device: get('device')?.value || '',
      demographic: get('demographic')?.value || '',
      region: get('region')?.value || '',
      limit: get('limit')?.value || '',
      allow_accounts: get('allow_accounts')?.checked || false,
      allow_external: get('allow_external')?.checked || false,
      allow_financial: get('allow_financial')?.checked || false,
      additional_domains: get('additional_domains')?.value || ''
    };
    // Inline credentials — forwarded to start_run in memory only, never saved.
    if (data.allow_accounts) {
      const username = (get('account_username')?.value || '').trim();
      const password = get('account_password')?.value || '';
      const extras = {};
      item.querySelectorAll('.sentinel-account-extra-fields .sentinel-account-extra-row').forEach((row) => {
        const key = row.querySelector('.sentinel-account-extra-key')?.value?.trim();
        const value = row.querySelector('.sentinel-account-extra-value')?.value;
        if (key) extras[key] = value ?? '';
      });
      if (username || password || Object.keys(extras).length) {
        data.account_credentials = { username, password, extras };
      }
    }
    if (data.allow_financial) {
      const cardNumber = (get('card_number')?.value || '').trim();
      if (cardNumber || (get('card_expiry')?.value || '').trim() || (get('card_cvv')?.value || '').trim()) {
        data.card_number = cardNumber;
        data.card_expiry = (get('card_expiry')?.value || '').trim();
        data.card_cvv = (get('card_cvv')?.value || '').trim();
      }
    }
    return data;
  }

  addButton.addEventListener('click', addItem);

  // Prefill from an existing batch ("Rerun"), else start with one empty item.
  let prefill = null;
  const prefillEl = document.getElementById('sentinel-batch-prefill');
  if (prefillEl) {
    try { prefill = JSON.parse(prefillEl.textContent || 'null'); } catch (e) { prefill = null; }
  }
  if (prefill && Array.isArray(prefill.items) && prefill.items.length) {
    if (prefill.name) form.name.value = prefill.name;
    prefill.items.slice(0, maxItems).forEach((data) => {
      const node = addItem();
      if (node) populateItem(node, data);
    });
  } else {
    addItem();
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const items = Array.from(itemsContainer.children).map(serializeItem);
    const payload = { name: form.name.value, items };
    status.textContent = 'Queuing…';
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const response = await fetch('/sentinel/api/batches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        status.textContent = data.error || 'Could not queue runs.';
        button.disabled = false;
        return;
      }
      window.location.href = `/sentinel/batch/${data.batch_id}`;
    } catch (error) {
      status.textContent = 'Could not queue runs.';
      button.disabled = false;
    }
  });
}

// --- Detail dashboard (sentinel_batch.html) -------------------------------

const ACTIVE_STATUSES = new Set(['queued', 'running', 'summarizing']);
const POLL_INTERVAL_MS = 1500;

// Refresh the live status badges of a batch's child rows in the sidebar.
function updateSidebarChildStatuses(batchId, runs) {
  const group = document.querySelector(`[data-batch-group="${batchId}"]`);
  if (!group) return;
  const byId = new Map(runs.map((r) => [r.run_id, r]));
  group.querySelectorAll('.sentinel-batch-child').forEach((row) => {
    const href = row.getAttribute('href') || '';
    const runId = href.split('/').pop();
    const run = byId.get(runId);
    if (!run) return;
    const badge = row.querySelector('.sentinel-badge');
    if (badge) {
      badge.className = `sentinel-badge sentinel-badge-${run.status}`;
      badge.textContent = run.status;
    }
  });
}

function initBatchDetail() {
  const shell = document.querySelector('.sentinel-shell[data-batch-id]');
  if (!shell || document.getElementById('sentinel-batch-form')) return;
  const batchId = shell.dataset.batchId;
  if (!batchId) return;
  let pollTimer = null;

  async function poll() {
    try {
      const response = await fetch(`/sentinel/api/batch/${batchId}`);
      if (!response.ok) return;
      const data = await response.json();
      const runs = data.child_runs || [];
      updateSidebarChildStatuses(batchId, runs);
      const anyActive = runs.some((r) => ACTIVE_STATUSES.has(r.status));
      if (!anyActive && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch (error) { /* transient; keep polling */ }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
  }

  // Poll while any child run in the sidebar is still active.
  const initialActive = Array.from(
    document.querySelectorAll(`[data-batch-group="${batchId}"] .sentinel-batch-child .sentinel-badge`)
  ).some((el) => ACTIVE_STATUSES.has(el.textContent.trim()));
  if (initialActive) startPolling();
}

document.addEventListener('DOMContentLoaded', () => {
  initBatchBuilder();
  initBatchDetail();
});
