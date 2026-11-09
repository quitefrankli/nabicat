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
  const connectForm = root.querySelector('#jhunt-connect-form');
  const tokenInput = root.querySelector('#jhunt-api-token');
  const connectButton = root.querySelector('#jhunt-connect-submit');
  const connectStatus = root.querySelector('#jhunt-connect-status');
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
  let stale = true;
  let warnings = [];
  let actionOutcome = null;
  let retryDelay = Number(root.dataset.retryInitialMs) || 1000;
  const retryMaximum = Number(root.dataset.retryMaxMs) || 30000;

  connectForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (connectButton.disabled) return;
    let token = tokenInput.value;
    tokenInput.value = '';
    if (!token) {
      connectStatus.textContent = 'Enter the local JHunt API token to connect.';
      connectStatus.className = 'jhunt-connect-status is-error';
      return;
    }
    connectButton.disabled = true;
    connectStatus.textContent = 'Connecting…';
    connectStatus.className = 'jhunt-connect-status';
    try {
      const result = await client.connect(token);
      token = '';
      if (result.connected !== true) throw new Error('JHunt did not confirm the connection.');
      connectStatus.textContent = 'Connected to JHunt.';
      connectStatus.className = 'jhunt-connect-status is-success';
      await refresh();
    } catch (error) {
      connectStatus.textContent = error.message || 'Unable to connect to JHunt. Check the token and try again.';
      connectStatus.className = 'jhunt-connect-status is-error';
    } finally {
      token = '';
      connectButton.disabled = false;
    }
  });

  const setMessage = text => { message.textContent = text; };
  const storeKey = value => `${value.store_id || ''}:${value.connection_generation || ''}`;
  const canAct = () => Boolean(snapshot?.connected && !stale && !actionPending);

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

  function render(inbox, focus) {
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
      } else {
        noteQuestionChanges(snapshot, next);
      }
      snapshot = next;
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
