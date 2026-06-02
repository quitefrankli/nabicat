(function () {
  const STORAGE_KEY = 'sentinel.sidebar.width';
  const VIEW_KEY = 'sentinel.sidebar.view';
  const MIN = 200;
  const MAX = 560;

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function initViewToggle(sidebar) {
    const toggle = sidebar.querySelector('.sentinel-view-toggle');
    if (!toggle) return;
    const options = toggle.querySelectorAll('[data-view-option]');
    const thumb = toggle.querySelector('.sentinel-view-toggle-thumb');

    // data-view drives the thumb slide (immediate); data-list-view drives the
    // list + action-button swap, applied only once the slide animation ends so
    // the content changes in sync with the completed slide.
    function setListView(view) {
      sidebar.dataset.listView = view;
    }

    function applyView(view, animate) {
      if (sidebar.dataset.view === view && sidebar.dataset.listView === view) return;
      sidebar.dataset.view = view;
      options.forEach((opt) => {
        opt.setAttribute('aria-pressed', String(opt.dataset.viewOption === view));
      });
      try { localStorage.setItem(VIEW_KEY, view); } catch (_) { /* ignore */ }

      if (!animate || !thumb) {
        setListView(view);
        return;
      }
      // Swap the list when the thumb finishes sliding; guard with a timeout
      // fallback in case transitionend doesn't fire (e.g. reduced motion).
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        thumb.removeEventListener('transitionend', finish);
        setListView(view);
      };
      thumb.addEventListener('transitionend', finish);
      setTimeout(finish, 400);
    }

    // Prefer a stored choice; otherwise keep the server-rendered default
    // (which is "batches" when viewing a batch page). No animation on load.
    let stored = null;
    try { stored = localStorage.getItem(VIEW_KEY); } catch (_) { stored = null; }
    if (stored === 'individual' || stored === 'batches') applyView(stored, false);

    // A click anywhere on the slider flips to the other view — including
    // clicking the already-active side — so it behaves like a switch.
    toggle.addEventListener('click', () => {
      const next = sidebar.dataset.view === 'individual' ? 'batches' : 'individual';
      applyView(next, true);
    });
  }

  function applyWidth(shell, width) {
    const clamped = Math.max(MIN, Math.min(MAX, Math.round(width)));
    shell.style.setProperty('--sentinel-sidebar-w', clamped + 'px');
    return clamped;
  }

  document.addEventListener('DOMContentLoaded', function () {
    const sidebar = document.getElementById('sentinel-sidebar');
    if (sidebar) initViewToggle(sidebar);

    const shell = document.querySelector('.sentinel-shell-with-sidebar');
    const resizer = document.getElementById('sentinel-sidebar-resizer');
    if (!shell || !resizer) return;

    const stored = parseInt(localStorage.getItem(STORAGE_KEY) || '', 10);
    if (Number.isFinite(stored)) applyWidth(shell, stored);

    let dragging = false;
    let shellLeft = 0;

    function onPointerMove(event) {
      if (!dragging) return;
      const width = event.clientX - shellLeft;
      applyWidth(shell, width);
    }

    function onPointerUp() {
      if (!dragging) return;
      dragging = false;
      resizer.classList.remove('is-dragging');
      document.body.classList.remove('sentinel-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      const current = getComputedStyle(shell).getPropertyValue('--sentinel-sidebar-w').trim();
      const px = parseInt(current, 10);
      if (Number.isFinite(px)) localStorage.setItem(STORAGE_KEY, String(px));
    }

    resizer.addEventListener('pointerdown', function (event) {
      if (event.button !== 0) return;
      event.preventDefault();
      dragging = true;
      shellLeft = shell.getBoundingClientRect().left;
      resizer.classList.add('is-dragging');
      document.body.classList.add('sentinel-resizing');
      document.addEventListener('pointermove', onPointerMove);
      document.addEventListener('pointerup', onPointerUp);
    });

    resizer.addEventListener('keydown', function (event) {
      const step = event.shiftKey ? 32 : 8;
      const current = parseInt(getComputedStyle(shell).getPropertyValue('--sentinel-sidebar-w'), 10) || 280;
      let next = current;
      if (event.key === 'ArrowLeft') next = current - step;
      else if (event.key === 'ArrowRight') next = current + step;
      else return;
      event.preventDefault();
      const applied = applyWidth(shell, next);
      localStorage.setItem(STORAGE_KEY, String(applied));
    });

    resizer.addEventListener('dblclick', function () {
      shell.style.removeProperty('--sentinel-sidebar-w');
      localStorage.removeItem(STORAGE_KEY);
    });

    document.querySelectorAll('.sentinel-run-delete').forEach(function (button) {
      button.addEventListener('click', async function () {
        const runId = button.dataset.runId;
        if (!runId || !window.confirm('Delete this Sentinel run?')) return;
        button.disabled = true;
        try {
          const response = await fetch(`/sentinel/api/runs/${runId}/delete`, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken() }
          });
          if (!response.ok) {
            button.disabled = false;
            return;
          }
          if (button.dataset.current === '1') {
            window.location.href = '/sentinel/';
            return;
          }
          button.closest('[data-run-row]')?.remove();
        } catch (_) {
          button.disabled = false;
        }
      });
    });
  });
})();
