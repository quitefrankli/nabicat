function sentinelCsrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('sentinel-run-form');
  const status = document.getElementById('sentinel-form-status');
  if (!form) return;

  const financialCheckbox = form.querySelector('#sentinel-allow-financial');
  const cardFields = document.getElementById('sentinel-card-fields');
  if (financialCheckbox && cardFields) {
    const toggleCardFields = () => {
      cardFields.hidden = !financialCheckbox.checked;
    };
    financialCheckbox.addEventListener('change', toggleCardFields);
    toggleCardFields();
  }

  const accountsCheckbox = form.querySelector('#sentinel-allow-accounts');
  const accountFields = document.getElementById('sentinel-account-fields');
  const accountExtras = document.getElementById('sentinel-account-extra-fields');
  if (accountsCheckbox && accountFields) {
    const toggleAccountFields = () => {
      accountFields.hidden = !accountsCheckbox.checked;
    };
    accountsCheckbox.addEventListener('change', toggleAccountFields);
    toggleAccountFields();
  }

  if (accountExtras) {
    const addButton = form.querySelector('.sentinel-account-add-field');
    const addRow = (key = '', value = '') => {
      const row = document.createElement('div');
      row.className = 'sentinel-account-extra-row';
      row.innerHTML = `
        <input class="form-control sentinel-account-extra-key" type="text" placeholder="field name" autocomplete="off">
        <input class="form-control sentinel-account-extra-value" type="text" placeholder="value" autocomplete="off">
        <button type="button" class="btn btn-sm btn-outline-secondary sentinel-account-extra-remove" aria-label="Remove field">
          <i class="bi bi-x-lg"></i>
        </button>
      `;
      row.querySelector('.sentinel-account-extra-key').value = key;
      row.querySelector('.sentinel-account-extra-value').value = value;
      row.querySelector('.sentinel-account-extra-remove').addEventListener('click', () => row.remove());
      accountExtras.appendChild(row);
    };
    if (addButton) {
      addButton.addEventListener('click', () => addRow());
    }
  }

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const payload = {
      url: form.url.value,
      prompt: form.prompt.value,
      limit: form.limit.value,
      title: form.title.value,
      allow_accounts: form.allow_accounts.checked,
      allow_external: form.allow_external.checked,
      additional_domains: form.additional_domains.value,
      allow_financial: form.allow_financial.checked,
      device: form.device.value,
      demographic: form.demographic.value
    };
    if (form.allow_financial.checked) {
      payload.card_number = form.card_number.value;
      payload.card_expiry = form.card_expiry.value;
      payload.card_cvv = form.card_cvv.value;
    }
    if (form.allow_accounts.checked) {
      const username = form.account_username?.value?.trim() || '';
      const password = form.account_password?.value || '';
      const extras = {};
      document.querySelectorAll('#sentinel-account-extra-fields .sentinel-account-extra-row').forEach(row => {
        const key = row.querySelector('.sentinel-account-extra-key')?.value?.trim();
        const value = row.querySelector('.sentinel-account-extra-value')?.value;
        if (key) extras[key] = value ?? '';
      });
      if (username || password || Object.keys(extras).length) {
        payload.account_credentials = {
          username,
          password,
          extras
        };
      }
    }
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
