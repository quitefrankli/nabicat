/**
 * Replace this adapter when the authenticated NabiCat bridge is available.
 * Records retain jhunt's {id, state, created, data} response shape.
 * Credentials and direct access to jhunt stay outside the browser.
 */
export class DisconnectedClient {
  async loadInbox() {
    return { connected: false, notifications: [], tasks: [], applications: [] };
  }

  // POST /tasks/{id}/input: {answer: {text}}
  async answerTask(id, body) {
    throw new Error('JHunt is not connected. Your answer has not been sent.');
  }

  // POST /applications/{id}/decision: {manifest_hash, decision}
  async decideApplication(id, body) {
    throw new Error('JHunt is not connected. Your decision has not been sent.');
  }

  // GET /artifacts/{hash}: return an authenticated, same-origin download URL.
  async artifactUrl(hash) {
    throw new Error('JHunt is not connected. Documents are unavailable.');
  }
}
