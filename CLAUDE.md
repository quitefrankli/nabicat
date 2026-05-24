* Minimise token usage - this directly affects cost and speed:

* Don't poll or re-read: For background tasks, wait for completion once rather than repeatedly reading output files.

* Skip redundant verification: After a tool succeeds without error, don't re-read the result to confirm.

* Match verbosity to task complexity: Routine ops (merge, deploy, simple file edits) need minimal commentary. Save detailed explanations for complex logic, architectural decisions, or when asked.

* One tool call, not three: Prefer a single well-constructed command over multiple incremental checks.

* Don't narrate tool use: Skip "Let me read the file" or "Let me check the status" ? just do it.

* Context preservation: Background tasks return completion notifications with `<result>` tags containing only the final message. Do NOT call `TaskOutput` to check results. `TaskOutput` returns the full conversation transcript (every tool call, file read, and intermediate message), which wastes massive amounts of context. Wait for each task's completion notification and use the `<result>` tag content directly.

* Minimise comments/docstrings, only add comments when they are truly helpful i.e. deep functions, surprsing logic. Don't add any readme.md files or other documentation files unless explicitly asked to.

* Do test driven development, when unit/integration tests are appropriate, first write the test that defines the expected behavior, then implement the code to pass the test.

* DO NOT write superfluous tests, add 1-2 USEFUL tests only per feature

* Constants belong in `config.py`: any named constant (limits, counts, timeouts, feature flags, model names, etc.) must be defined as an attribute of `ConfigManager` in `web_app/config.py`, not hardcoded at call sites.

* Project Architecture:
    - this project contains a collection of smaller subapps/subpages under web_app/ all of which share a similar ui/ux theme and share the same domain and host
    - Each subapp is a Flask Blueprint with its own templates/ and static/ folders
    - CSS and JS must live in the subapp's static/ folder (e.g. `metrics/static/style.css`), never inline in HTML templates
    - Link them via `{% block scripts %}` using `url_for('.static', filename='...')`

* Cross-browser / cross-device UI: this is a web app served to real users on a mix of browsers (Chrome, Firefox, Safari — including iOS Safari) and devices (desktop, tablet, phone). When making UI/CSS/JS changes:
    - Design mobile-first and verify layouts still work on narrow viewports (~375px wide phones). Use responsive units and existing `@media (max-width: 768px)` breakpoints rather than fixed pixel sizing.
    - Avoid WebKit-only / Chrome-only CSS without fallbacks. Be cautious with newer APIs (e.g. `:has()`, container queries, View Transitions) and check baseline support before relying on them.
    - Account for iOS Safari quirks: safe-area insets (`env(safe-area-inset-bottom)`), the dynamic bottom chrome hiding fixed elements, 100vh != viewport height, and touch vs. hover (`:hover` doesn't apply on touch — don't hide critical UI behind hover).
    - Touch targets should be large enough to tap (~40px min). Don't rely on hover-only tooltips for essential info.
    - Test behavior when the keyboard is open on mobile (fixed bottom bars can get covered).
    - Thumbnail/image grids should not assign all real image URLs directly in HTML. Use the established file_store/Hammock pattern: render a tiny placeholder `src`, put the real URL in a data attribute, lazy-load with `IntersectionObserver`, serialize requests with a small stagger, and retry failed loads with cache-busting query params. Define stagger/retry constants in `ConfigManager`.

* start every new session with "CLAUDE.md read!"

## Hammock subapp

- Posts live on the filesystem: `~/.nabicat/data/hammock/projects/<project>/<post>/`. Each post has a `meta.json` with `template` (`markdown`/`gallery`/`raw`), `owner` (username), `title`, `date`.
- Authorization: post owner OR admin can edit/delete. Legacy posts (no `owner` field) are admin-only.
- Templated posts store source-of-truth alongside the rendered `index.html`:
    - `markdown` → `source.md`
    - `gallery` → `gallery.json` (`title`, `description`, `images`); originals in the post dir, WebP thumbnails in `thumbs/<filename>.webp`
- `DataInterface.get_post_content` re-renders templated posts on every view, so renderer/style changes propagate without re-saving existing posts.
- Per-user storage quotas: `hammock_non_admin_quota_bytes`, `hammock_admin_quota_bytes`; thumb size: `hammock_gallery_thumb_max_px`.

## UI/UX Design System — Honeydew Theme

All UI work must stay consistent with the established design system in `web_app/static/style.css`.

### Design Tokens (CSS variables — always use these, never hardcode values)

**Colors:**
- `--hw-bg-primary`: #F0FFF0 | `--hw-bg-secondary`: #FAF9F6 | `--hw-bg-cream`: #F5F5DC
- `--hw-sage`: #87A878 | `--hw-sage-light`: #A8C686 | `--hw-sage-dark`: #6B8E5A
- `--hw-forest`: #2D4A3E | `--hw-moss`: #4A5D4A
- `--hw-peach`: #F4A261 | `--hw-gold`: #E9C46A | `--hw-coral`: #E07A5F | `--hw-terracotta`: #D4866A
- `--hw-text-primary`: #2D4A3E | `--hw-text-secondary`: #5A6B5A | `--hw-text-muted`: #8A9A8A
- `--hw-border`: rgba(135, 168, 120, 0.2)

**Gradients:** `--hw-gradient-warm`, `--hw-gradient-sage`, `--hw-gradient-golden`, `--hw-gradient-soft`

**Shadows:** `--hw-shadow-sm` / `--hw-shadow-md` / `--hw-shadow-lg` / `--hw-shadow-glow`

**Border radius:** `--hw-radius-sm` 8px | `--hw-radius-md` 12px | `--hw-radius-lg` 16px | `--hw-radius-xl` 24px | `--hw-radius-full` 9999px

**Transitions:** `--hw-transition-fast` 0.2s | `--hw-transition-base` 0.3s | `--hw-transition-slow` 0.5s — all use `cubic-bezier(0.4, 0, 0.2, 1)`

### Typography
- Body: `'Nunito'` (400/500/600/700), sans-serif
- Headings: `'Playfair Display'` (600/700), serif
- Code/mono: `'SF Mono'`, monospace
- Both loaded from Google Fonts in `root_base.html`

### Component Conventions
- **Buttons:** `--hw-radius-md`, padding `0.625rem 1.25rem`, gradient fills, `translateY(-2px)` on hover
- **Cards:** `rgba(255,255,255,0.9)`, `--hw-radius-lg` (16px), `--hw-shadow-md`, `translateY(-4px)` on hover
- **Forms:** `rgba(255,255,255,0.8)` bg, 2px sage border, `--hw-radius-md`, focus ring `rgba(135,168,120,0.15)`
- **Navbar:** glassmorphism — `rgba(255,255,255,0.7)` + `backdrop-filter: blur(12px)`, `--hw-radius-lg`
- **Dropdowns/Modals:** `rgba(255,255,255,0.98)` + `backdrop-filter: blur(12px)`, `--hw-radius-md`
- **Empty states:** centered icon (4rem, 0.3 opacity), muted text, vertical flex

### Animation Patterns
- Hover lift: buttons `translateY(-2px)`, cards `translateY(-4px)`
- Entry: `@keyframes fadeInUp` — opacity 0→1 + translateY(20px)→0, 0.5s
- Pulse: `@keyframes pulse-glow` — box-shadow expansion, 2s infinite
- All interactive element transitions: `var(--hw-transition-fast)` or `var(--hw-transition-base)`

### Template Hierarchy
`root_base.html` → `subpage_base.html` → per-subapp base (e.g. `metrics_base.html`) → page templates

### Utility Classes
`.text-sage`, `.text-forest`, `.text-peach`, `.bg-honeydew`, `.bg-sage`, `.border-sage`, `.shadow-soft`, `.rounded-xl`
