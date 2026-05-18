function sentinelCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('sentinel-run-form');
  const status = document.getElementById('sentinel-form-status');
  if (!form) return;

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const payload = {
      url: form.url.value,
      prompt: form.prompt.value,
      limit: form.limit.value
    };
    status.textContent = 'Starting run...';
    button.disabled = true;

    try {
      const response = await fetch('/sentinel/api/runs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': sentinelCsrfToken()
        },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) {
        status.textContent = data.error || 'Could not start run.';
        button.disabled = false;
        return;
      }
      window.location.href = `/sentinel/report/${data.run_id}`;
    } catch (error) {
      status.textContent = 'Could not start run.';
      button.disabled = false;
    }
  });
});
