// Unified Sentinel run configurator. Both the single-run form and each batch
// item share the same field markup (_sentinel_run_fields.html), so all the
// item-level behavior — field-reveal toggles, extra-field rows, credential
// caching, populate/serialize — lives here and operates on one "item root"
// element. initSingleRun and initBatchBuilder just wire that shared item logic
// to their respective page chrome and submit endpoints.

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

// --- Shared item-level helpers --------------------------------------------

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

function wireToggle(item, checkboxSel, fieldsSel) {
  const checkbox = item.querySelector(checkboxSel);
  const fields = item.querySelector(fieldsSel);
  if (!checkbox || !fields) return;
  const sync = () => { fields.hidden = !checkbox.checked; };
  checkbox.addEventListener('change', sync);
  sync();
}

// Wire the field-reveal toggles and the "add extra field" button for one item.
function wireItem(item) {
  wireToggle(item, '.sentinel-rf-allow-external', '.sentinel-rf-additional-domains');
  wireToggle(item, '.sentinel-rf-allow-accounts', '.sentinel-rf-account-fields');
  wireToggle(item, '.sentinel-rf-allow-financial', '.sentinel-rf-card-fields');
  const extras = item.querySelector('.sentinel-rf-account-extra-fields');
  item.querySelector('.sentinel-rf-account-add-field')?.addEventListener('click', () => {
    if (extras) addExtraRow(extras);
  });
}

function populateItem(item, data) {
  const set = (name, value) => {
    const el = item.querySelector(`[name="${name}"]`);
    if (el) el.value = value;
  };
  const check = (name, on) => {
    const el = item.querySelector(`[name="${name}"]`);
    if (el) {
      el.checked = !!on;
      // Fire the bound reveal-toggle so dependent field blocks match.
      el.dispatchEvent(new Event('change'));
    }
  };
  if (data.url !== undefined) set('url', data.url || '');
  if (data.title !== undefined) set('title', data.title || '');
  if (data.prompt !== undefined) set('prompt', data.prompt || '');
  if (data.device) set('device', data.device);
  if (data.demographic) set('demographic', data.demographic);
  if (data.region) set('region', data.region);
  if (data.limit) set('limit', data.limit);
  if (data.additional_domains !== undefined) set('additional_domains', data.additional_domains || '');
  if (data.allow_accounts !== undefined) check('allow_accounts', data.allow_accounts);
  if (data.allow_external !== undefined) check('allow_external', data.allow_external);
  if (data.allow_financial !== undefined) check('allow_financial', data.allow_financial);
  if (data.remember_account !== undefined) check('remember_account', data.remember_account);
  if (data.remember_card !== undefined) check('remember_card', data.remember_card);
  if (data.account_credentials) {
    set('account_username', data.account_credentials.username || '');
    set('account_password', data.account_credentials.password || '');
    const extras = item.querySelector('.sentinel-rf-account-extra-fields');
    if (extras) {
      extras.textContent = '';
      Object.entries(data.account_credentials.extras || {}).forEach(([key, value]) => {
        addExtraRow(extras, key, value);
      });
    }
  }
  if (data.card_number) set('card_number', data.card_number);
  if (data.card_expiry) set('card_expiry', data.card_expiry);
  if (data.card_cvv) set('card_cvv', data.card_cvv);
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
  // Credentials are sent only when their permit is on; they reach the run in
  // memory and are written to disk only if the matching remember flag is set.
  if (data.allow_accounts) {
    data.remember_account = get('remember_account')?.checked || false;
    const username = (get('account_username')?.value || '').trim();
    const password = get('account_password')?.value || '';
    const extras = {};
    item.querySelectorAll('.sentinel-rf-account-extra-fields .sentinel-account-extra-row').forEach((row) => {
      const key = row.querySelector('.sentinel-account-extra-key')?.value?.trim();
      const value = row.querySelector('.sentinel-account-extra-value')?.value;
      if (key) extras[key] = value ?? '';
    });
    if (username || password || Object.keys(extras).length) {
      data.account_credentials = { username, password, extras };
    }
  }
  if (data.allow_financial) {
    data.remember_card = get('remember_card')?.checked || false;
    const cardNumber = (get('card_number')?.value || '').trim();
    if (cardNumber || (get('card_expiry')?.value || '').trim() || (get('card_cvv')?.value || '').trim()) {
      data.card_number = cardNumber;
      data.card_expiry = (get('card_expiry')?.value || '').trim();
      data.card_cvv = (get('card_cvv')?.value || '').trim();
    }
  }
  return data;
}

function readPrefill(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try { return JSON.parse(el.textContent || 'null'); } catch (e) { return null; }
}

// --- Single-run form (sentinel_run.html) ----------------------------------

function initSingleRun() {
  const form = document.getElementById('sentinel-run-form');
  if (!form) return;
  const status = document.getElementById('sentinel-form-status');
  const item = form.querySelector('.sentinel-run-item');
  if (!item) return;

  wireItem(item);

  // Rerun prefill (from the report page) arrives as a single item blob built
  // server-side from the run, including its persisted credentials. A fresh form
  // (no prefill) is left untouched.
  const prefill = readPrefill('sentinel-run-prefill');
  if (prefill) {
    populateItem(item, prefill);
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const payload = serializeItem(item);
    status.textContent = 'Starting run...';
    button.disabled = true;
    try {
      const response = await fetch('/sentinel/api/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        status.textContent = data.error || 'Could not start run.';
        button.disabled = false;
        return;
      }
      window.location.href = `/sentinel/report/${data.run_id}`;
    } catch (error) {
      status.textContent = 'Could not start run.';
      button.disabled = false;
    }
  });
}

// --- Batch builder (sentinel_batches.html) --------------------------------

function initBatchBuilder() {
  const form = document.getElementById('sentinel-batch-form');
  if (!form) return;
  const shell = document.querySelector('.sentinel-shell');
  const itemsContainer = document.getElementById('sentinel-batch-items');
  const template = document.getElementById('sentinel-batch-item-template');
  const addButton = form.querySelector('.sentinel-batch-add-item');
  const status = document.getElementById('sentinel-batch-status');
  const maxItems = parseInt(shell?.dataset.maxBatchItems || '8', 10);

  function updateAddState() {
    const full = itemsContainer.children.length >= maxItems;
    addButton.disabled = full;
    itemsContainer.querySelectorAll('.sentinel-batch-item-duplicate').forEach((button) => {
      button.disabled = full;
    });
  }

  function wireBatchItem(item) {
    wireItem(item);
    item.querySelector('.sentinel-batch-item-duplicate')?.addEventListener('click', () => duplicateItem(item));
    item.querySelector('.sentinel-batch-item-remove')?.addEventListener('click', () => {
      item.remove();
      updateAddState();
    });
  }

  function addItem() {
    if (itemsContainer.children.length >= maxItems) return null;
    const node = template.content.firstElementChild.cloneNode(true);
    itemsContainer.appendChild(node);
    wireBatchItem(node);
    updateAddState();
    return node;
  }

  function duplicateItem(item) {
    if (itemsContainer.children.length >= maxItems) return null;
    const node = addItem();
    if (!node) return null;
    populateItem(node, serializeItem(item));
    item.after(node);
    updateAddState();
    return node;
  }

  addButton.addEventListener('click', addItem);

  // Prefill from an existing batch ("Rerun") — items (incl. persisted creds)
  // are reconstructed server-side — else start with one empty item.
  const prefill = readPrefill('sentinel-batch-prefill');
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

// --- Batch detail dashboard (sentinel_batch.html) -------------------------

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

  const initialActive = Array.from(
    document.querySelectorAll(`[data-batch-group="${batchId}"] .sentinel-batch-child .sentinel-badge`)
  ).some((el) => ACTIVE_STATUSES.has(el.textContent.trim()));
  if (initialActive) startPolling();
}

document.addEventListener('DOMContentLoaded', () => {
  initSingleRun();
  initBatchBuilder();
  initBatchDetail();
});
