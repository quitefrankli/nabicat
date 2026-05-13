function initMoodStars(container) {
	const hiddenInput = container.querySelector('input[name="mood_rating"]');
	const stars = container.querySelectorAll('.diary-star');
	const clearBtn = container.querySelector('.diary-star-clear');

	function render(value) {
		stars.forEach((starEl, idx) => {
			const starNum = idx + 1;
			const icon = starEl.querySelector('i');
			if (value >= starNum) icon.className = 'bi bi-star-fill';
			else if (value >= starNum - 0.5) icon.className = 'bi bi-star-half';
			else icon.className = 'bi bi-star';
		});
	}

	function setValue(v) {
		hiddenInput.value = v;
		render(v);
	}

	stars.forEach((starEl, idx) => {
		starEl.addEventListener('click', (e) => {
			const rect = starEl.getBoundingClientRect();
			const isHalf = (e.clientX - rect.left) < rect.width / 2;
			setValue((idx + 1) - (isHalf ? 0.5 : 0));
		});
	});

	if (clearBtn) clearBtn.addEventListener('click', () => setValue(0));
	render(parseFloat(hiddenInput.value) || 0);
}

function normalizeTag(raw) {
	return raw.trim().toLowerCase()
		.replace(/\s+/g, '-')
		.replace(/[^a-z0-9-]/g, '')
		.replace(/-+/g, '-')
		.replace(/^-|-$/g, '');
}

function initTagWidget(container) {
	const hiddenInput = container.querySelector('input[name="tags"]');
	const chipsContainer = container.querySelector('.diary-tag-chips');
	const tagInput = container.querySelector('.diary-tag-input');
	const suggestionsEl = container.querySelector('.diary-tag-suggestions');
	const recentTags = JSON.parse(container.dataset.recentTags || '[]');

	let currentTags = (hiddenInput.value || '').split(',').map(t => t.trim()).filter(Boolean);

	function syncHidden() { hiddenInput.value = currentTags.join(','); }

	function renderChips() {
		chipsContainer.innerHTML = '';
		currentTags.forEach(tag => {
			const chip = document.createElement('span');
			chip.className = 'badge diary-tag-chip diary-tag-chip-removable';
			chip.textContent = '#' + tag;
			const btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'btn-close btn-close-sm ms-1';
			btn.setAttribute('aria-label', 'Remove tag');
			btn.addEventListener('click', () => {
				currentTags = currentTags.filter(t => t !== tag);
				syncHidden();
				renderChips();
				renderSuggestions();
			});
			chip.appendChild(btn);
			chipsContainer.appendChild(chip);
		});
	}

	function renderSuggestions() {
		suggestionsEl.innerHTML = '';
		const available = recentTags.filter(t => !currentTags.includes(t));
		if (available.length === 0) {
			const li = document.createElement('li');
			li.innerHTML = '<span class="dropdown-item-text text-muted fst-italic small">No suggestions</span>';
			suggestionsEl.appendChild(li);
			return;
		}
		available.forEach(tag => {
			const li = document.createElement('li');
			const btn = document.createElement('button');
			btn.type = 'button';
			btn.className = 'dropdown-item';
			btn.textContent = '#' + tag;
			btn.addEventListener('click', () => addTag(tag));
			li.appendChild(btn);
			suggestionsEl.appendChild(li);
		});
	}

	function addTag(raw) {
		const tag = normalizeTag(raw);
		if (!tag || currentTags.includes(tag)) {
			tagInput.value = '';
			return;
		}
		currentTags.push(tag);
		syncHidden();
		renderChips();
		renderSuggestions();
		tagInput.value = '';
	}

	tagInput.addEventListener('keydown', (e) => {
		if (e.key === 'Enter' || e.key === ',') {
			e.preventDefault();
			addTag(tagInput.value);
		}
	});

	const form = container.closest('form');
	if (form) {
		form.addEventListener('submit', () => {
			if (tagInput.value.trim()) addTag(tagInput.value);
		});
	}

	renderChips();
	renderSuggestions();
}

function toggleGoalState(switchElement, goalId) {
	const state = switchElement.checked;

	const data = {
		"goal_id": goalId,
		"state": state
	};

	fetch("/todoist/goal/toggle_state", {
		method: "POST",
		headers: {
			"Content-Type": "application/json"
		},
		body: JSON.stringify(data)
	})
	.then(response => response.json())
	.then(data => {
		if (data["success"]) {
			console.log("Goal state toggled successfully");
		} else {
			console.error("Failed to toggle goal state");
		}
	})
	.catch((error) => {
		console.error("Failed to toggle goal state", error);
        switchElement.checked = !state; // Revert the switch state on error
	});
}

// Pagination for summary goals
let summaryCurrentPage = 0;
let summaryHasMoreGoals = false;

function loadMoreSummaryGoals() {
	const btn = document.getElementById('load-more-btn');
	if (!btn) return;
	
	btn.disabled = true;
	btn.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>Loading...';
	
	summaryCurrentPage++;
	fetch(`/todoist/api/summary_goals_page?page=${summaryCurrentPage}`)
		.then(response => response.json())
		.then(data => {
			const container = document.getElementById('goals-container');
			container.innerHTML += data.html;
			summaryHasMoreGoals = data.has_more;
			
			if (!summaryHasMoreGoals) {
				btn.remove();
			} else {
				btn.disabled = false;
				btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
			}
		})
		.catch(error => {
			console.error('Error loading more goals:', error);
			btn.disabled = false;
			btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
		});
}

// Pagination for completed goals
let completedCurrentPage = 0;
let completedHasMoreGoals = false;

function loadMoreCompletedGoals() {
	const btn = document.getElementById('load-more-btn');
	if (!btn) return;
	
	btn.disabled = true;
	btn.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>Loading...';
	
	completedCurrentPage++;
	fetch(`/todoist/api/completed_goals_page?page=${completedCurrentPage}`)
		.then(response => response.json())
		.then(data => {
			const container = document.getElementById('goals-container');
			container.innerHTML += data.html;
			completedHasMoreGoals = data.has_more;
			
			if (!completedHasMoreGoals) {
				btn.remove();
			} else {
				btn.disabled = false;
				btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
			}
		})
		.catch(error => {
			console.error('Error loading more goals:', error);
			btn.disabled = false;
			btn.innerHTML = '<i class="bi bi-arrow-down me-2"></i>Load More';
		});
}

// Initialize pagination based on which page we're on
document.addEventListener('DOMContentLoaded', function() {
	const summaryGoalsPage = document.querySelector('body').classList.contains('summary-goals-page');
	const completedGoalsPage = document.querySelector('body').classList.contains('completed-goals-page');
	
	if (summaryGoalsPage) {
		const btn = document.getElementById('load-more-btn');
		if (btn) {
			btn.onclick = loadMoreSummaryGoals;
		}
	} else if (completedGoalsPage) {
		const btn = document.getElementById('load-more-btn');
		if (btn) {
			btn.onclick = loadMoreCompletedGoals;
		}
	}

	document.querySelectorAll('textarea.diary-body-input').forEach(textarea => {
		const autosize = () => {
			textarea.style.height = 'auto';
			textarea.style.height = textarea.scrollHeight + 'px';
		};
		textarea.addEventListener('input', autosize);
	});

	document.querySelectorAll('.diary-mood-stars').forEach(initMoodStars);
	document.querySelectorAll('.diary-tags-widget').forEach(initTagWidget);

	const hash = window.location.hash;
	if (hash.startsWith('#entry-')) {
		const entryId = hash.slice('#entry-'.length);
		const collapseEl = document.getElementById('collapseEntry' + entryId);
		if (collapseEl) {
			const bsCollapse = new bootstrap.Collapse(collapseEl, { toggle: false });
			bsCollapse.show();
			collapseEl.addEventListener('shown.bs.collapse', () => {
				const textarea = document.getElementById('entryBody' + entryId);
				if (textarea) {
					textarea.style.height = 'auto';
					textarea.style.height = textarea.scrollHeight + 'px';
					textarea.focus();
				}
			}, { once: true });
		}
	}
});