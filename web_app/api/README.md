# API

Encrypted and plain data-sync endpoints under `/api`, plus health, backup, update, and RSA handshake operations.

- File push, pull, list, and delete endpoints share the user's File Store files and quota.
- File Store validates paths and owns metadata, blobs, backups, and account cleanup. The API no longer uses `api_data`; legacy files must be removed manually.
- Ephemeral handshake keys are Redis-backed so encrypted requests work across gunicorn workers.
