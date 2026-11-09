import { DisconnectedClient } from './client.js';

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function describe(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function details(title, data) {
  const disclosure = element('details');
  disclosure.append(element('summary', title));
  const list = element('dl', undefined, 'jhunt-details');
  for (const [key, value] of Object.entries(data || {})) {
    list.append(element('dt', key.replaceAll('_', ' ')), element('dd', describe(value)));
  }
  disclosure.append(list);
  return disclosure;
}

function item(title, record) {
  const article = element('article', undefined, 'jhunt-item');
  article.append(element('h3', title), element('p', record.state.replaceAll('_', ' ')));
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

export async function mountJhunt(root, client) {
  const status = root.querySelector('#jhunt-connection');
  const message = root.querySelector('#jhunt-message');
  let pending = false;

  function report(text) { message.textContent = text; }

  async function perform(button, operation) {
    if (pending) return;
    pending = true;
    button.disabled = true;
    report('Sending…');
    try {
      await operation();
      report('Saved.');
      await refresh();
    } catch (error) {
      report(error.message || 'Unable to save. Please try again.');
    } finally {
      pending = false;
      if (button.isConnected) button.disabled = false;
    }
  }

  function populate(id, records, render) {
    const target = root.querySelector(id);
    if (records.length) target.replaceChildren(...records.map(render));
  }

  async function refresh() {
    try {
      const inbox = await client.loadInbox();
      status.textContent = inbox.connected ? 'Connected' : 'Not connected';
      // Reset to the original empty states before applying a fresh snapshot.
      for (const target of root.querySelectorAll('[data-empty-template]')) {
        target.replaceChildren(root.querySelector(target.dataset.emptyTemplate).content.cloneNode(true));
      }
      root.querySelector('#jhunt-count').textContent = inbox.notifications.length;
      populate('#jhunt-notifications', inbox.notifications, record => {
        const article = item(record.data.event?.replaceAll('_', ' ') || 'Update', record);
        article.append(details('Update details', record.data));
        return article;
      });
      populate('#jhunt-questions', inbox.tasks.filter(record => record.state === 'needs_input'), record => {
        const article = item('JHunt needs your input', record);
        article.append(details('Question', record.data.question));
        const form = element('form', undefined, 'jhunt-form');
        const label = element('label', 'Your answer');
        const input = element('textarea');
        input.required = true;
        label.append(input);
        const submit = element('button', 'Send answer', 'btn btn-primary');
        submit.type = 'submit';
        submit.disabled = !inbox.connected;
        form.append(label, submit);
        form.addEventListener('submit', event => {
          event.preventDefault();
          if (!input.value.trim()) return;
          perform(submit, () => client.answerTask(record.id, {answer: {text: input.value.trim()}}));
        });
        article.append(form);
        return article;
      });
      populate('#jhunt-applications', inbox.applications, record => {
        const article = item('Application package', record);
        article.append(element('p', `Package ${record.id}`));
        article.append(details('Review package, answers and evidence', record.data));
        if (record.data.resume?.pdf) {
          const download = element('button', 'Open resume PDF', 'btn btn-outline-primary');
          download.type = 'button';
          download.disabled = !inbox.connected;
          download.addEventListener('click', async () => {
            download.disabled = true;
            try {
              const url = new URL(await client.artifactUrl(record.data.resume.pdf), location.origin);
              if (url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) {
                throw new Error('Document URL must be served by NabiCat.');
              }
              const link = element('a', 'View resume PDF', 'btn btn-outline-primary');
              link.href = url.href;
              link.target = '_blank';
              link.rel = 'noopener';
              download.replaceWith(link);
            } catch (error) {
              report(error.message || 'Unable to open the resume.');
              download.disabled = false;
            }
          });
          article.append(download);
        }
        if (record.state === 'awaiting_approval' && record.data.manifest_hash) {
          const form = element('form', undefined, 'jhunt-form');
          const label = element('label', undefined, 'jhunt-confirm');
          const confirm = element('input');
          confirm.type = 'checkbox';
          label.append(confirm, document.createTextNode('I reviewed the resume, answers and evidence for this exact package. Approval authorizes submission.'));
          const actions = element('div', undefined, 'jhunt-actions');
          for (const decision of ['approve', 'reject']) {
            const button = element('button', decision === 'approve' ? 'Approve submission' : 'Reject package', decision === 'approve' ? 'btn btn-primary' : 'btn btn-outline-danger');
            button.type = 'button';
            button.disabled = !inbox.connected || decision === 'approve';
            confirm.addEventListener('change', () => {
              button.disabled = !inbox.connected || (decision === 'approve' && !confirm.checked);
            });
            button.addEventListener('click', () => {
              if (decision === 'approve' && !confirm.checked) return;
              perform(button, () => client.decideApplication(record.id, {manifest_hash: record.data.manifest_hash, decision}));
            });
            actions.append(button);
          }
          form.append(label, actions);
          article.append(form);
        }
        return article;
      });
    } catch (error) {
      status.textContent = 'Connection unavailable';
      report(error.message || 'Unable to load JHunt updates.');
    }
  }
  await refresh();
}

const root = document.getElementById('jhunt-app');
if (root) mountJhunt(root, new DisconnectedClient());
