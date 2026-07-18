async function refreshMetricsPage(url, options = {}) {
	const expandedIds = [...document.querySelectorAll('#app-main-content .accordion-collapse.show')].map(el => el.id);
	const response = await fetch(url, options);
	if (!response.ok) throw new Error('The metric update could not be saved.');

	const nextDocument = new DOMParser().parseFromString(await response.text(), 'text/html');
	const nextMain = nextDocument.getElementById('app-main-content');
	const nextModals = nextDocument.getElementById('app-actions-modals');
	const nextFlash = nextDocument.getElementById('app-flash-messages');
	if (!nextMain || !nextModals || !nextFlash) throw new Error('The updated metrics could not be displayed.');

	document.getElementById('app-main-content').replaceWith(nextMain);
	document.getElementById('app-actions-modals').replaceWith(nextModals);
	document.getElementById('app-flash-messages').replaceWith(nextFlash);
	document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
	document.body.classList.remove('modal-open');
	document.body.style.removeProperty('padding-right');
	document.body.style.removeProperty('overflow');
	expandedIds.forEach(id => {
		const collapse = document.getElementById(id);
		if (collapse) bootstrap.Collapse.getOrCreateInstance(collapse, { toggle: false }).show();
	});
}

document.addEventListener('submit', async event => {
	const form = event.target.closest('form[data-async-mutation]');
	if (!form) return;
	event.preventDefault();
	const submitButton = event.submitter;
	if (submitButton) submitButton.disabled = true;
	try {
		await refreshMetricsPage(form.action, { method: form.method, body: new FormData(form) });
	} catch (error) {
		if (submitButton) submitButton.disabled = false;
		alert(error.message);
	}
});

document.addEventListener('click', async event => {
	const link = event.target.closest('a[data-async-mutation]');
	if (!link) return;
	event.preventDefault();
	try {
		await refreshMetricsPage(link.href);
	} catch (error) {
		alert(error.message);
	}
});
