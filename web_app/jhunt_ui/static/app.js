import { BridgeClient } from './client.js';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function details(title, data) {
  const disclosure = element('details');
  disclosure.append(element('summary', title));
  const list = element('dl', undefined, 'jhunt-details');
  for (const [key, value] of Object.entries(data || {})) {
    list.append(element('dt', key.replaceAll('_', ' ')), element('dd', typeof value === 'string' ? value : JSON.stringify(value, null, 2)));
  }
  disclosure.append(list);
  return disclosure;
}

function item(title, record) {
  const article = element('article', undefined, 'jhunt-item');
  article.dataset.recordId = record.id;
  article.append(element('h3', title), element('p', (record.state || 'updated').replaceAll('_', ' ')));
  if (record.created) {
    const date = new Date(record.created * 1000);
    if (!Number.isNaN(date.getTime())) {
      const time = element('time', date.toLocaleString());
      time.dateTime = date.toISOString();
      article.append(time);
    }
  }
  return article;
}

const questionFor = task => task.data?.question ?? task.question ?? {};
const questionKey = task => `${task.id}:${task.question_hash || JSON.stringify(questionFor(task))}`;
const packageData = record => record.data || {};
const resumeHash = data => data.resume?.pdf || data.resume_pdf || data.artifacts?.resume_pdf || null;

export async function mountJhunt(root, client) {
  const status = root.querySelector('#jhunt-connection');
  const message = root.querySelector('#jhunt-message');
  const drafts = new Map();
  const confirmations = new Map();
  const articleCache = new Map();
  let snapshot = null;
  let stream = null;
  let streamStoreKey = null;
  let refreshTimer = null;
  let refreshInFlight = false;
  let refreshQueued = false;
  let pendingRevision = null;
  let actionPending = false;
  let pendingCommand = null;
  let pendingStorageKey = null;
  let criteriaDraft = null;
  let criteriaBaseId = null;
  let conflictResolved = false;
  let stale = true;
  let warnings = [];
  let actionOutcome = null;
  let retryDelay = Number(root.dataset.retryInitialMs) || 1000;
  const retryMaximum = Number(root.dataset.retryMaxMs) || 30000;

  const setMessage = text => { message.textContent = text; };
  const storeKey = value => `${value.store_id || ''}:${value.connection_generation || ''}`;
  const canAct = () => Boolean(snapshot?.connected && !stale && !actionPending && !pendingCommand);
  const criteriaId = criteria => criteria?.id ?? criteria?.criteria_id ?? null;
  const criteriaText = criteria => criteria?.text ?? criteria?.data?.text ?? '';
  const pendingKeyFor = value => `jhunt-search-pending:${storeKey(value)}`;
  const draftKeyFor = value => `jhunt-search-draft:${storeKey(value)}`;

  function storageRead(key) {
    try { return sessionStorage.getItem(key); } catch { return null; }
  }
  function storageWrite(key, value) {
    try { if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value); } catch { /* The in-memory reconciliation still works for this page. */ }
  }

  function hydrateSearch(inbox, backendChanged = false) {
    const input = root.querySelector('[data-criteria-input]');
    const key = draftKeyFor(inbox);
    if (backendChanged) {
      criteriaDraft = null;
      criteriaBaseId = null;
      conflictResolved = false;
      storageWrite(key, null);
    }
    if (criteriaDraft !== null) return;
    const savedDraft = storageRead(key);
    if (savedDraft !== null) {
      try {
        const draft = JSON.parse(savedDraft);
        if (typeof draft.text === 'string') {
          criteriaDraft = draft.text;
          criteriaBaseId = draft.baseId ?? null;
          return;
        }
      } catch { storageWrite(key, null); }
    }
    criteriaDraft = criteriaText(inbox.search?.criteria);
    criteriaBaseId = criteriaId(inbox.search?.criteria);
    if (input) input.value = criteriaDraft;
  }

  function persistCriteriaDraft() {
    storageWrite(draftKeyFor(snapshot), JSON.stringify({text: criteriaDraft, baseId: criteriaBaseId}));
  }

  function setStatus(text, isStale = stale) {
    stale = isStale;
    status.textContent = text;
    status.classList.toggle('is-stale', isStale);
    for (const button of root.querySelectorAll('button[data-review-action]')) button.disabled = !canAct();
    for (const link of root.querySelectorAll('a[data-review-action]')) {
      link.setAttribute('aria-disabled', String(!canAct()));
      link.classList.toggle('is-disabled', !canAct());
    }
    for (const textarea of root.querySelectorAll('[data-answer-input]')) textarea.disabled = !canAct();
    const criteriaInput = root.querySelector('[data-criteria-input]');
    if (criteriaInput) criteriaInput.disabled = !snapshot?.connected || stale || actionPending;
    for (const button of root.querySelectorAll('[data-search-action]')) {
      button.disabled = !canAct() || !criteriaInput?.value.trim() || Boolean(root.querySelector('#jhunt-search-conflict .jhunt-conflict') && !conflictResolved) || (button.id === 'jhunt-search-now' && !snapshot?.search?.sources?.length);
    }
    for (const button of root.querySelectorAll('[data-approval-required="true"]')) {
      const key = button.dataset.confirmKey;
      const confirm = [...root.querySelectorAll('[data-confirm-key]')].find(node => node.type === 'checkbox' && node.dataset.confirmKey === key);
      button.disabled = !canAct() || !confirm?.checked;
    }
  }

  function activeDraft() {
    const field = document.activeElement?.matches?.('[data-draft-key]') ? document.activeElement : null;
    return field ? {key: field.dataset.draftKey, start: field.selectionStart, end: field.selectionEnd} : null;
  }

  function noteQuestionChanges(previous, next) {
    if (!previous) return;
    const nextTasks = new Map((next.tasks || []).map(task => [task.id, task]));
    for (const task of previous.tasks || []) {
      const nextTask = nextTasks.get(task.id);
      if (drafts.has(questionKey(task)) && !nextTask) {
        warnings.push(`A saved draft for removed question ${task.id} was not sent.`);
      } else if (nextTask && questionKey(task) !== questionKey(nextTask) && drafts.has(questionKey(task))) {
        warnings.push(`Question ${task.id} changed. Its earlier draft was not sent.`);
      }
    }
  }

  function reconcileRecords(target, records, build) {
    const previous = articleCache.get(target.id) || new Map();
    const current = new Map();
    const desired = [];
    for (const record of records) {
      const signature = `${storeKey(snapshot)}:${record.id}:${JSON.stringify(record)}`;
      const article = previous.get(signature) || build(record);
      current.set(signature, article);
      desired.push(article);
    }
    articleCache.set(target.id, current);
    if (!records.length) {
      if (!target.querySelector('.jhunt-empty')) {
        target.replaceChildren(root.querySelector(target.dataset.emptyTemplate).content.cloneNode(true));
      }
      return;
    }
    const desiredSet = new Set(desired);
    for (const child of [...target.children]) {
      if (!desiredSet.has(child)) child.remove();
    }
    desired.forEach((article, index) => {
      const currentChild = target.children[index];
      if (currentChild !== article) target.insertBefore(article, currentChild || null);
    });
  }

  function renderSearch(inbox) {
    const input = root.querySelector('[data-criteria-input]');
    if (input && document.activeElement !== input) input.value = criteriaDraft ?? criteriaText(inbox.search?.criteria);
    const savedId = criteriaId(inbox.search?.criteria);
    const conflict = criteriaBaseId !== savedId;
    const conflictBox = root.querySelector('#jhunt-search-conflict');
    conflictBox.replaceChildren();
    if (conflict) {
      const box = element('div', undefined, 'jhunt-conflict');
      box.append(element('p', 'Saved criteria changed in another tab or session. Your draft is preserved. Compare it with the current saved version or reload before saving or searching.'));
      const actions = element('div', undefined, 'jhunt-actions');
      const compare = element('button', 'Compare with saved criteria', 'btn btn-outline-primary');
      compare.type = 'button';
      compare.addEventListener('click', () => {
        const comparison = element('details');
        comparison.open = true;
        comparison.append(element('summary', 'Current saved criteria'));
        comparison.append(element('pre', criteriaText(inbox.search?.criteria) || '(empty)'));
        const useDraft = element('button', 'Keep my draft and use this saved revision', 'btn btn-outline-primary');
        useDraft.type = 'button';
        useDraft.addEventListener('click', () => {
          criteriaBaseId = savedId;
          conflictResolved = false;
          persistCriteriaDraft();
          renderSearch(inbox);
          setStatus(status.textContent, stale);
        });
        box.append(comparison, useDraft);
        compare.disabled = true;
        setStatus(status.textContent, stale);
      });
      const reload = element('button', 'Reload saved criteria', 'btn btn-outline-primary');
      reload.type = 'button';
      reload.addEventListener('click', () => {
        criteriaDraft = criteriaText(inbox.search?.criteria);
        criteriaBaseId = savedId;
        conflictResolved = false;
        persistCriteriaDraft();
        if (input) input.value = criteriaDraft;
        renderSearch(inbox);
        setMessage('Saved criteria loaded.');
      });
      actions.append(compare, reload);
      box.append(actions);
      conflictBox.append(box);
    }

    const sources = root.querySelector('#jhunt-search-sources');
    sources.replaceChildren();
    const available = inbox.search?.sources || [];
    if (!available.length) sources.append(element('li', 'No search sources are configured or available.'));
    for (const source of available) {
      const name = typeof source === 'string' ? source : (source.name || source.id || 'Source');
      const state = typeof source === 'string' ? 'available' : (source.state || source.status || (source.enabled === false ? 'disabled' : 'available'));
      sources.append(element('li', `${name}: ${String(state).replaceAll('_', ' ')}`));
    }

    const active = root.querySelector('#jhunt-active-searches');
    active.replaceChildren();
    for (const request of inbox.search?.active_requests || []) active.append(searchEntry(request));
    if (!active.childElementCount) active.append(element('p', 'No active searches.'));
    const latest = root.querySelector('#jhunt-latest-search');
    latest.replaceChildren();
    if (inbox.search?.latest_request) latest.append(searchEntry(inbox.search?.latest_request));
    else latest.append(element('p', 'No searches have completed yet.'));

    const command = root.querySelector('#jhunt-search-command');
    command.replaceChildren();
    if (pendingCommand) {
      command.append(document.createTextNode(`Checking the receipt for ${pendingCommand.kind === 'search' ? 'Search now' : 'Save criteria'}. The command will not be sent again automatically. `));
      const check = element('button', 'Check receipt', 'btn btn-outline-primary');
      check.type = 'button';
      check.addEventListener('click', () => { void reconcilePending(true); });
      command.append(check);
    } else if (actionOutcome) command.textContent = actionOutcome;
  }

  function searchEntry(record) {
    const entry = element('article', undefined, 'jhunt-search-entry');
    const state = String(record.state || 'unknown').replaceAll('_', ' ');
    const data = record.data || {};
    entry.append(element('strong', state));
    if (state === 'queued') entry.append(element('p', 'Queued; waiting for a worker.'));
    if (record.created || data.accepted_at) {
      const timestamp = record.created || data.accepted_at;
      const time = element('time', new Date(Number(timestamp) * 1000).toLocaleString());
      entry.append(time);
    }
    const outcomes = data.outcomes || {};
    for (const [name, outcome] of Object.entries(outcomes)) {
      const line = element('p', `${name}: ${String(outcome.state || 'unknown').replaceAll('_', ' ')}`);
      if (outcome.error) line.append(document.createTextNode(` — ${outcome.error}`));
      if (outcome.retry_at) line.append(document.createTextNode(`; retry at ${new Date(Number(outcome.retry_at) * 1000).toLocaleString()}`));
      if (outcome.task_id) line.append(document.createTextNode(`; task ${outcome.task_id}`));
      if (outcome.observations !== undefined) line.append(document.createTextNode(`; ${outcome.observations} observations`));
      if (outcome.new_vacancies !== undefined) line.append(document.createTextNode(`; ${outcome.new_vacancies} new vacancies`));
      entry.append(line);
    }
    if (['completed', 'partial-failure', 'failed'].includes(record.state)) {
      entry.append(element('p', 'Discovery outcome only. Assessment and preparation continue separately.'));
    }
    return entry;
  }

  async function reconcilePending(manual = false) {
    if (!pendingCommand || !snapshot?.connected || storeKey(snapshot) !== `${pendingCommand.storeId}:${pendingCommand.generation}`) return;
    const checking = pendingCommand;
    try {
      const receipt = await client.searchReceipt(checking.key);
      if (pendingCommand !== checking || storeKey(snapshot) !== `${checking.storeId}:${checking.generation}`) return;
      if (!receipt || receipt.request_key !== checking.key) return;
      const kind = pendingCommand.kind;
      pendingCommand = null;
      storageWrite(pendingStorageKey, null);
      const criteria = receipt.criteria;
      if (criteria) {
        criteriaBaseId = criteriaId(criteria);
        conflictResolved = false;
      }
      persistCriteriaDraft();
      setStatus(status.textContent, stale);
      actionOutcome = kind === 'search' ? 'Search request accepted. Live progress is shown below.' : 'Criteria saved. Retained jobs will be reassessed without scanning sources.';
      renderSearch(snapshot);
      if (manual) setMessage(actionOutcome);
    } catch (error) {
      if (pendingCommand !== checking) return;
      if (error.status === 404 && manual && checking.body) {
        const command = root.querySelector('#jhunt-search-command');
        const retry = element('button', 'Retry this command', 'btn btn-outline-primary');
        retry.type = 'button';
        retry.addEventListener('click', async () => {
          if (pendingCommand !== checking || stale || storeKey(snapshot) !== `${checking.storeId}:${checking.generation}`) return;
          retry.disabled = true;
          try {
            await (checking.kind === 'search' ? client.startSearch(checking.body) : client.saveCriteria(checking.body));
          } catch (error) {
            if (error.status >= 400 && error.status < 500) {
              pendingCommand = null;
              storageWrite(pendingStorageKey, null);
              setMessage(`Request rejected. ${error.message}`);
            }
          }
          await refresh();
        });
        command.append(document.createTextNode(' No receipt found. You can explicitly retry the same command safely. '), retry);
      }
      if (error.status !== 404 && manual) setMessage(`Receipt not confirmed yet. You can check again; the command will not be replayed. ${error.message || ''}`.trim());
    }
  }

  function render(inbox, focus) {
    renderSearch(inbox);
    root.querySelector('#jhunt-count').textContent = (inbox.notifications || []).length;

    const notifications = root.querySelector('#jhunt-notifications');
    reconcileRecords(notifications, inbox.notifications || [], record => {
      const article = item(record.data?.event?.replaceAll('_', ' ') || 'Update', record);
      article.append(details('Update details', record.data));
      return article;
    });

    const questions = root.querySelector('#jhunt-questions');
    const waiting = (inbox.tasks || []).filter(record => record.state === 'needs_input');
    reconcileRecords(questions, waiting, record => {
      const article = item('JHunt needs your input', record);
      const question = questionFor(record);
      article.append(details('Question', question));
      const draftKey = questionKey(record);
      const form = element('form', undefined, 'jhunt-form');
      const label = element('label', 'Your answer');
      const input = element('textarea');
      input.required = true;
      input.dataset.answerInput = 'true';
      input.dataset.draftKey = draftKey;
      input.value = drafts.get(draftKey) || '';
      input.addEventListener('input', () => drafts.set(draftKey, input.value));
      label.append(input);
      const submit = element('button', 'Send answer', 'btn btn-primary');
      submit.type = 'submit';
      submit.dataset.reviewAction = 'true';
      submit.disabled = !canAct();
      form.append(label, submit);
      form.addEventListener('submit', event => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text || actionPending || !canAct()) return;
        const body = {
          answer: {text},
          expected_store_id: snapshot.store_id,
          expected_connection_generation: snapshot.connection_generation,
        };
        if (record.question_hash) body.expected_question_hash = record.question_hash;
        perform(submit, () => client.answerTask(record.id, body), draftKey);
      });
      article.append(form);
      return article;
    });

    const applications = root.querySelector('#jhunt-applications');
    const packages = inbox.applications || [];
    reconcileRecords(applications, packages, record => {
      const data = packageData(record);
      const article = item('Application package', record);
      article.append(element('p', `Package ${record.id}`));
      if (record.job) article.append(details('Job', record.job));
      if (record.assessment) article.append(details('Assessment and rationale', record.assessment));
      article.append(details('Package, proposed answers and evidence', data));
      if (data.manifest_hash) article.append(element('p', `Exact manifest submitted for decision: ${data.manifest_hash}`, 'jhunt-manifest'));
      const hash = resumeHash(data);
      if (hash) {
        const link = element('a', 'Open resume PDF', 'btn btn-outline-primary');
        link.href = client.artifactUrl(hash);
        link.target = '_blank';
        link.rel = 'noopener';
        link.dataset.reviewAction = 'true';
        link.addEventListener('click', event => { if (!canAct()) event.preventDefault(); });
        article.append(link);
      }
      if (record.state === 'awaiting_approval' && data.manifest_hash) {
        const form = element('form', undefined, 'jhunt-form');
        const key = `${record.id}:${data.manifest_hash}:${snapshot.store_id}:${snapshot.connection_generation}`;
        const label = element('label', undefined, 'jhunt-confirm');
        const confirm = element('input');
        confirm.type = 'checkbox';
        confirm.dataset.confirmKey = key;
        confirm.checked = confirmations.get(key) === true;
        confirm.addEventListener('change', () => {
          confirmations.set(key, confirm.checked);
          setStatus(status.textContent, stale);
        });
        label.append(confirm, document.createTextNode('I reviewed the job, rationale, resume, answers and evidence for this exact package. Approval authorizes submission.'));
        const actions = element('div', undefined, 'jhunt-actions');
        for (const decision of ['approve', 'reject']) {
          const button = element('button', decision === 'approve' ? 'Approve submission' : 'Reject package', decision === 'approve' ? 'btn btn-primary' : 'btn btn-outline-danger');
          button.type = 'button';
          button.dataset.reviewAction = 'true';
          button.dataset.confirmKey = key;
          button.dataset.approvalRequired = decision === 'approve' ? 'true' : 'false';
          button.disabled = !canAct() || (decision === 'approve' && !confirm.checked);
          button.addEventListener('click', () => {
            if (!canAct() || (decision === 'approve' && !confirm.checked)) return;
            perform(button, () => client.decideApplication(record.id, {
              manifest_hash: data.manifest_hash,
              decision,
              expected_store_id: snapshot.store_id,
              expected_connection_generation: snapshot.connection_generation,
            }));
          });
          actions.append(button);
        }
        form.append(label, actions);
        article.append(form);
      }
      return article;
    });

    for (const warning of [...new Set(warnings)]) {
      const warningBox = element('p', warning, 'jhunt-warning');
      message.before(warningBox);
      setTimeout(() => warningBox.remove(), 12_000);
    }
    warnings = [];
    if (focus) {
      const replacement = [...root.querySelectorAll('[data-draft-key]')].find(field => field.dataset.draftKey === focus.key);
      if (replacement) {
        replacement.focus({preventScroll: true});
        replacement.setSelectionRange(focus.start, focus.end);
      }
    }
  }

  async function refresh() {
    if (refreshInFlight) {
      refreshQueued = true;
      return;
    }
    refreshInFlight = true;
    try {
      const next = await client.loadInbox();
      const focus = activeDraft();
      if (snapshot && storeKey(next) === storeKey(snapshot) && next.revision < snapshot.revision) return;
      const backendChanged = snapshot && storeKey(next) !== storeKey(snapshot);
      if (backendChanged) {
        confirmations.clear();
        drafts.clear();
        pendingRevision = null;
        articleCache.clear();
        if (pendingStorageKey) storageWrite(pendingStorageKey, null);
        pendingCommand = null;
        criteriaDraft = null;
        criteriaBaseId = null;
        conflictResolved = false;
      } else {
        noteQuestionChanges(snapshot, next);
        if (snapshot && criteriaBaseId === criteriaId(snapshot.search?.criteria) && criteriaDraft === criteriaText(snapshot.search?.criteria)) {
          criteriaDraft = criteriaText(next.search?.criteria);
          criteriaBaseId = criteriaId(next.search?.criteria);
        }
      }
      snapshot = next;
      pendingStorageKey = pendingKeyFor(snapshot);
      if (!pendingCommand) {
        try { pendingCommand = JSON.parse(storageRead(pendingStorageKey) || 'null'); } catch { pendingCommand = null; }
      }
      hydrateSearch(snapshot, backendChanged);
      if (pendingCommand) await reconcilePending();
      retryDelay = Number(root.dataset.retryInitialMs) || 1000;
      render(snapshot, focus);
      setStatus('Connected', false);
      if (actionOutcome) setMessage(actionOutcome);
      else if (backendChanged) setMessage('JHunt connection changed. Saved drafts and review confirmations were cleared.');
      else if (pendingRevision && pendingRevision.store_id === snapshot.store_id && pendingRevision.revision > snapshot.revision) scheduleRefresh(0);
      else if (!actionPending && !warnings.length) setMessage('');
      if (!stream || streamStoreKey !== storeKey(snapshot) || stream.readyState === EventSource.CLOSED) connectStream();
    } catch (error) {
      if (snapshot) {
        setStatus('Stale · reconnecting', true);
        setMessage('Connection lost. Showing the last saved view; actions are disabled.');
      } else {
        setStatus('Not connected', true);
        setMessage(error.message || 'Unable to load JHunt updates.');
      }
      scheduleRefresh(retryDelay);
      retryDelay = Math.min(retryDelay * 2, retryMaximum);
    } finally {
      refreshInFlight = false;
      if (refreshQueued) {
        refreshQueued = false;
        scheduleRefresh(0);
      }
    }
  }

  async function perform(button, operation, draftKey) {
    if (actionPending || !canAct()) return;
    actionOutcome = null;
    actionPending = true;
    button.disabled = true;
    setMessage('Sending…');
    for (const item of root.querySelectorAll('button[data-review-action]')) item.disabled = true;
    try {
      await operation();
      if (draftKey) drafts.delete(draftKey);
      actionOutcome = 'Saved. Current JHunt state reconciled.';
    } catch (error) {
      actionOutcome = `Outcome unconfirmed. Checking JHunt now; this command will not be retried. ${error.message || ''}`.trim();
    } finally {
      actionPending = false;
      await refresh();
    }
  }

  const criteriaInput = root.querySelector('[data-criteria-input]');
  criteriaInput.addEventListener('input', () => {
    criteriaDraft = criteriaInput.value;
    persistCriteriaDraft();
    conflictResolved = false;
    renderSearch(snapshot || {});
    setStatus(status.textContent, stale);
  });

  async function submitCriteria(kind) {
    if (!canAct() || (criteriaBaseId !== criteriaId(snapshot.search?.criteria) && !conflictResolved)) return;
    const text = criteriaInput.value;
    if (!text.trim() || text.length > Number(criteriaInput.maxLength)) { setMessage('Enter valid search criteria before continuing.'); return; }
    let requestKey;
    try { requestKey = crypto.randomUUID(); }
    catch { setMessage('This browser cannot create a secure command key. Update the browser before continuing.'); return; }
    pendingCommand = {key: requestKey, kind, storeId: snapshot.store_id, generation: snapshot.connection_generation};
    pendingStorageKey = pendingKeyFor(snapshot);
    storageWrite(pendingStorageKey, JSON.stringify(pendingCommand));
    actionPending = true;
    setStatus(status.textContent, stale);
    setMessage(kind === 'search' ? 'Submitting search…' : 'Saving criteria…');
    const submittedBackend = storeKey(snapshot);
    const body = {
      text,
      expected_criteria_id: criteriaBaseId,
      expected_store_id: snapshot.store_id,
      expected_connection_generation: snapshot.connection_generation,
      request_key: requestKey,
    };
    pendingCommand.body = body;
    storageWrite(pendingStorageKey, JSON.stringify(pendingCommand));
    try {
      const result = kind === 'search' ? await client.startSearch(body) : await client.saveCriteria(body);
      if (storeKey(snapshot) !== submittedBackend) return;
      if (result?.request_key === requestKey) {
        pendingCommand = null;
        storageWrite(pendingStorageKey, null);
        if (result.criteria) criteriaBaseId = criteriaId(result.criteria);
        persistCriteriaDraft();
        actionOutcome = kind === 'search' ? 'Search request accepted. Live progress is shown below.' : 'Criteria saved. Retained jobs will be reassessed without scanning sources.';
      }
    } catch (error) {
      if (storeKey(snapshot) !== submittedBackend) return;
      if (error.status >= 400 && error.status < 500) {
        pendingCommand = null;
        storageWrite(pendingStorageKey, null);
        conflictResolved = false;
        actionOutcome = `Request rejected. Your draft is preserved. ${error.message || 'Compare or reload before trying again.'}`;
      } else {
        actionOutcome = `Outcome unconfirmed. Checking the saved receipt; this command will not be replayed. ${error.message || ''}`.trim();
      }
    } finally {
      actionPending = false;
      renderSearch(snapshot);
      setStatus(status.textContent, stale);
      await refresh();
    }
  }

  root.querySelector('#jhunt-save-criteria').addEventListener('click', () => { void submitCriteria('save'); });
  root.querySelector('#jhunt-search-now').addEventListener('click', () => { void submitCriteria('search'); });

  function scheduleRefresh(delay = 160) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => { void refresh(); }, delay);
  }

  function connectStream() {
    if (!snapshot?.connected) return;
    const currentKey = storeKey(snapshot);
    if (stream && streamStoreKey === currentKey && stream.readyState !== EventSource.CLOSED) return;
    stream?.close();
    streamStoreKey = currentKey;
    stream = new EventSource(client.eventsUrl(`${snapshot.store_id}:${snapshot.revision}`), {withCredentials: true});
    stream.onopen = () => { void refresh(); };
    stream.onerror = () => {
      setStatus('Stale · reconnecting', true);
      setMessage('Live updates paused. Reconnecting and checking the latest snapshot.');
      scheduleRefresh(500);
    };
    for (const eventName of ['change', 'reset', 'message']) {
      stream.addEventListener(eventName, event => {
        try {
          const cursor = JSON.parse(event.data);
          if (!pendingRevision || cursor.store_id !== pendingRevision.store_id || cursor.revision > pendingRevision.revision) pendingRevision = cursor;
        } catch { /* A reset still requires a current snapshot. */ }
        scheduleRefresh();
      });
    }
  }

  await refresh();
  window.addEventListener('pagehide', () => {
    stream?.close();
    clearTimeout(refreshTimer);
  }, {once: true});
}

const root = document.getElementById('jhunt-app');
if (root) mountJhunt(root, new BridgeClient());
