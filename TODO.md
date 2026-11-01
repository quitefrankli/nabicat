# TODO

## Tubio streaming support

- [ ] Add immediate playback for uncached YouTube tracks using their native browser-compatible audio stream.
  - Reserve the audio metadata and add the track to the user's playlist before the full media download completes, reusing the collision-safe reservation approach used by Surprise playlists.
  - Continue serving cached tracks through the existing local M4A byte-range response.
  - For uncached tracks, use yt-dlp to resolve a native M4A/AAC source without downloading it and prototype a direct redirect behind a `ConfigManager.tubio` feature flag.
  - Keep resolved signed media URLs short-lived in Redis; never persist or log them.
  - Re-resolve expired or rejected upstream URLs without interrupting cached playback.

- [ ] Validate the direct-streaming prototype across supported playback scenarios.
  - Test Chrome, Safari, and iOS Safari, including seeking, playback trims, playlist handoff, background playback, screen lock, expired URLs, upstream 403 responses, and tracks without a compatible native M4A source.
  - Measure time to first audio and server resource use against the current download-then-convert flow.
  - Confirm that authentication and authorization are enforced before exposing a temporary stream location.

- [ ] Decouple background caching and conversion from playback.
  - Keep `is_cached` as durable metadata and store transient resolving, caching, and failure state in Redis.
  - Use an idempotent Redis-backed job claim so concurrent requests do not download or convert the same track repeatedly.
  - Run materialization outside synchronous Gunicorn request workers, then atomically publish the completed local file and update metadata through `DataInterface.edit_metadata`.
  - Preserve explicit file downloads and future playback from the local cache even when first playback used the remote stream.

- [ ] Adapt the Tubio player and prefetch behavior for streaming.
  - Allow regular and Surprise tracks to begin playback without waiting for `convertTrackForPlayback`.
  - Replace full-response blob prefetching with source resolution or a small initial range so the browser and server do not download the same track twice.
  - Show unobtrusive streaming/caching state without blocking playback or replacing the persistent audio element.

- [ ] If direct redirects are unreliable, move remote byte proxying to Nginx or a dedicated streaming service.
  - Do not hold one of the four synchronous Gunicorn workers for the duration of playback.
  - Forward and validate byte ranges, content length, content type, and upstream failures while keeping signed upstream URLs behind opaque short-lived identifiers.

- [ ] Treat live FFmpeg-to-fragmented-MP4 streaming as a later fallback, not the initial implementation.
  - Evaluate browser compatibility, seeking and trim behavior, disconnect cleanup, cache finalization, and fragmented MP4 recovery before adopting it.
  - Review YouTube's terms and applicable content rights for the intended deployment before enabling streaming.