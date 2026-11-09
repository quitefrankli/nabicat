# JHunt search panel

The NabiCat JHunt panel requires a JHunt review API that exposes the additive `search` snapshot object (`criteria`, `sources`, `active_requests`, `latest_request`) and these admin-only commands:

- `POST /search/criteria` saves `{text, expected_criteria_id, expected_store_id, request_key}`.
- `POST /search/requests` atomically saves the displayed text and accepts a search using the same body.
- `GET /search/commands/{request_key}` reconciles the durable command receipt.
- `GET /search/requests/{id}` reads a known request.

The browser sends `expected_connection_generation` only to NabiCat; the bridge checks it locally and forwards the expected store ID to JHunt. Criteria text is limited to 20,000 characters. JHunt credentials remain server-side. Search command keys are persisted in the current browser tab for receipt lookup after a lost response and are never replayed automatically.

Use the existing local connection settings (`JHUNT_DEBUG_*` or `JHUNT_PRODUCTION_*`) and connect only to the loopback API configured for the selected environment. The panel is admin-only, uses CSRF protection for mutations, and expects the same private review API connection already used by inbox, task, and application review.

For now exactly one existing admin owns the shared configuration. Multiple users may be supported later; no per-user isolation is offered in this release. Live updates preserve drafts and focus. A conflict offers comparison and explicit reuse of the latest revision or reloading saved criteria. A lost response keeps the command key for receipt checks and an explicit, idempotent retry.
