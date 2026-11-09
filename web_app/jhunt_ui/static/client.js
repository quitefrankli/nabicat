const API = '/jhunt-ui/api';

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

export class BridgeClient {
  async request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body) headers.set('Content-Type', 'application/json');
    if (options.method && options.method !== 'GET') headers.set('X-CSRFToken', csrfToken());
    const response = await fetch(`${API}${path}`, {
      ...options,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(payload.error || `JHunt request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? {} : response.json();
  }

  async loadInbox() {
    const inbox = await this.request('/inbox');
    for (const application of inbox.applications || []) {
      application.data ||= {};
      if (application.manifest_hash && !application.data.manifest_hash) {
        application.data.manifest_hash = application.manifest_hash;
      }
    }
    return inbox;
  }

  saveCriteria(body) {
    return this.request('/search/criteria', {method: 'POST', body: JSON.stringify(body)});
  }

  startSearch(body) {
    return this.request('/search/requests', {method: 'POST', body: JSON.stringify(body)});
  }

  searchReceipt(key) {
    return this.request(`/search/commands/${encodeURIComponent(key)}`);
  }

  searchRequest(id) {
    return this.request(`/search/requests/${encodeURIComponent(id)}`);
  }

  answerTask(id, body) {
    return this.request(`/tasks/${encodeURIComponent(id)}/input`, {
      method: 'POST', body: JSON.stringify(body),
    });
  }

  decideApplication(id, body) {
    return this.request(`/applications/${encodeURIComponent(id)}/decision`, {
      method: 'POST', body: JSON.stringify(body),
    });
  }

  artifactUrl(hash) {
    return `${API}/artifacts/${encodeURIComponent(hash)}`;
  }

  eventsUrl(cursor) {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
    return `${API}/events${query}`;
  }
}
