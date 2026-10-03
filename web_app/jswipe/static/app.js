(() => {
  'use strict';

  const root = document.getElementById('jswipeApp');
  if (!root) return;

  const byId = (id) => document.getElementById(id);
  const elements = {
    deck: byId('jobDeck'),
    deckControls: byId('deckControls'),
    deckView: byId('deckView'),
    jobList: byId('jobList'),
    openButton: byId('openButton'),
    passButton: byId('passButton'),
    shortlistedCount: byId('shortlistedCount'),
    passedCount: byId('passedCount'),
    pendingCount: byId('pendingCount'),
    scanClose: byId('scanClose'),
    scanForm: byId('scanForm'),
    scanPanel: byId('scanPanel'),
    scanSpinner: byId('scanSpinner'),
    scanSubmit: byId('scanSubmit'),
    scanSubmitLabel: byId('scanSubmitLabel'),
    scanToggle: byId('scanToggle'),
    companiesPerSource: byId('companiesPerSource'),
    companiesPerSourceValue: byId('companiesPerSourceValue'),
    companiesPerSourceEstimate: byId('companiesPerSourceEstimate'),
    shortlistButton: byId('shortlistButton'),
    statusAlert: byId('statusAlert'),
    undoBar: byId('undoBar'),
    undoButton: byId('undoButton'),
    undoMessage: byId('undoMessage'),
    personalizationPanel: byId('personalizationPanel'),
    onboardingEmpty: byId('onboardingEmpty'),
    profileStatus: byId('profileStatus'),
    resumeMeta: byId('resumeMeta'),
    resumeFilename: byId('resumeFilename'),
    resumeDetails: byId('resumeDetails'),
    resumeForm: byId('resumeForm'),
    resumeFile: byId('resumeFile'),
    resumeSubmit: byId('resumeSubmit'),
    resumeSpinner: byId('resumeSpinner'),
    resumeSubmitLabel: byId('resumeSubmitLabel'),
    resumeUploadLabel: byId('resumeUploadLabel'),
    resumeDownload: byId('resumeDownload'),
    resumeRemove: byId('resumeRemove'),
    profileForm: byId('profileForm'),
    profileSubmit: byId('profileSubmit'),
    profileSpinner: byId('profileSpinner'),
    profileSubmitLabel: byId('profileSubmitLabel'),
    reviewNote: byId('reviewNote'),
    clearProfile: byId('clearProfile'),
    clearDialog: byId('clearDialog'),
    confirmClear: byId('confirmClear'),
  };

  let state = {
    jobs: [],
    counts: { pending: 0, shortlisted: 0, passed: 0 },
  };
  let personalization = {
    candidate: null,
    preferences: {},
    resume: null,
    profile_revision: 0,
    review_recommended: false,
  };
  let activeView = 'pending';
  let lastDecision = null;
  let undoTimer = null;
  let dragStart = null;
  let decisionPending = false;

  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

  async function requestJson(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (
      options.body
      && !(options.body instanceof FormData)
      && !headers.has('Content-Type')
    ) {
      headers.set('Content-Type', 'application/json');
    }
    const token = csrfToken();
    if (token) headers.set('X-CSRFToken', token);

    const response = await fetch(url, { ...options, headers });
    const contentType = response.headers.get('content-type') || '';
    const payload = contentType.includes('json')
      ? await response.json().catch(() => ({}))
      : { error: (await response.text().catch(() => '')).trim() };
    if (!response.ok) {
      throw new Error(payload.error || `Request failed (${response.status})`);
    }
    return payload;
  }

  function jobsFor(view) {
    return state.jobs.filter((job) => job.decision === view);
  }

  function applyJobs(payload) {
    state = {
      ...state,
      ...payload,
      jobs: Array.isArray(payload.jobs) ? payload.jobs : state.jobs,
    };
  }

  function applyProfile(payload, updateScanSettings = true) {
    if (!payload || typeof payload !== 'object') return;
    personalization = {
      ...personalization,
      ...payload,
      preferences: payload.preferences || {},
    };
    populateProfile(personalization);
    renderPersonalization();
    if (updateScanSettings && personalization.candidate) {
      populateSettings(profileScanSettings(personalization));
    }
  }

  function updateCounts() {
    elements.pendingCount.textContent = state.counts.pending || 0;
    elements.shortlistedCount.textContent = state.counts.shortlisted || 0;
    elements.passedCount.textContent = state.counts.passed || 0;
  }

  function showStatus(message, kind = 'info') {
    elements.statusAlert.textContent = message;
    elements.statusAlert.className = `jswipe-alert${kind === 'info' ? '' : ` is-${kind}`}`;
    elements.statusAlert.hidden = false;
  }

  function updateScanEstimate() {
    const companies = Number(elements.companiesPerSource.value) || 0;
    const selectedSources = elements.scanForm.querySelectorAll(
      'input[name="sources"]:checked',
    ).length;
    elements.companiesPerSourceValue.value = String(companies);
    elements.companiesPerSourceValue.textContent = String(companies);
    elements.companiesPerSourceEstimate.textContent =
      `Up to ${companies * selectedSources} companies across ${selectedSources} selected sources.`;
  }

  function makeChip(icon, text) {
    const chip = document.createElement('span');
    chip.className = 'jswipe-chip';
    const glyph = document.createElement('i');
    glyph.className = `bi ${icon}`;
    glyph.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.textContent = text;
    chip.append(glyph, label);
    return chip;
  }

  function makeEmpty(view) {
    const empty = document.createElement('div');
    empty.className = 'jswipe-empty';
    const icon = document.createElement('i');
    icon.className = view === 'pending' ? 'bi bi-check2-circle' : 'bi bi-inbox';
    icon.setAttribute('aria-hidden', 'true');
    const heading = document.createElement('h2');
    heading.className = 'h4 text-forest';
    heading.textContent = view === 'pending' ? "You're caught up" : 'Nothing here yet';
    const body = document.createElement('p');
    body.className = 'mb-0';
    body.textContent = view === 'pending'
      ? 'Run a personalized scan to find more roles.'
      : 'Your decisions will appear here.';
    empty.append(icon, heading, body);
    return empty;
  }

  function fitList(label, values, icon) {
    if (!Array.isArray(values) || !values.length) return null;
    const section = document.createElement('div');
    section.className = `jswipe-fit-section is-${label}`;
    const heading = document.createElement('h4');
    heading.className = 'jswipe-fit-heading';
    heading.textContent = label;
    const list = document.createElement('ul');
    list.className = 'jswipe-fit-list';

    values.forEach((value) => {
      const item = document.createElement('li');
      item.textContent = value;
      const glyph = document.createElement('i');
      glyph.className = `bi ${icon}`;
      glyph.setAttribute('aria-hidden', 'true');
      item.prepend(glyph);
      list.append(item);
    });

    section.append(heading, list);
    return section;
  }

  function makeFit(job) {
    const fit = job.fit || {
      score: 0,
      confidence: 0,
      evidence_level: 'metadata_only',
      summary: 'Fit has not been assessed yet.',
      matches: [],
      gaps: ['Run a personalized scan to assess this role.'],
      hard_conflicts: [],
    };
    const details = document.createElement('details');
    details.className = 'jswipe-fit-details';
    const summary = document.createElement('summary');
    summary.className = 'jswipe-fit-summary';
    const score = document.createElement('span');
    score.className = 'jswipe-fit-score';
    score.textContent = `${Math.max(0, Math.min(100, Number(fit.score) || 0))}`;
    const scoreLabel = document.createElement('span');
    scoreLabel.className = 'jswipe-fit-score-label';
    scoreLabel.textContent = 'fit';
    const confidence = document.createElement('span');
    confidence.className = 'jswipe-fit-confidence';
    confidence.textContent = `${Math.max(0, Math.min(100, Number(fit.confidence) || 0))}% confidence`;
    const summaryText = document.createElement('span');
    summaryText.className = 'jswipe-fit-summary-text';
    summaryText.textContent = fit.summary || 'Fit assessment available.';
    summary.append(score, scoreLabel, confidence, summaryText);
    details.append(summary);

    const evidence = document.createElement('div');
    evidence.className = 'jswipe-fit-evidence';
    const evidenceLevel = document.createElement('p');
    evidenceLevel.className = 'jswipe-fit-evidence-level';
    evidenceLevel.textContent = `Evidence: ${(fit.evidence_level || 'metadata_only').replaceAll('_', ' ')}`;
    evidence.append(evidenceLevel);
    [
      ['matches', 'Matches', 'bi-check2'],
      ['gaps', 'Gaps', 'bi-dash-circle'],
      ['hard_conflicts', 'Conflicts', 'bi-exclamation-triangle'],
    ].forEach(([key, label, icon]) => {
      const section = fitList(label, fit[key], icon);
      if (section) evidence.append(section);
    });
    details.append(evidence);
    return details;
  }

  function makeCard(job) {
    const card = document.createElement('article');
    card.className = 'jswipe-card';
    card.dataset.jobId = job.job_id;
    card.tabIndex = 0;
    const accent = document.createElement('div');
    accent.className = 'jswipe-card-accent';
    const body = document.createElement('div');
    body.className = 'jswipe-card-body';
    const company = document.createElement('div');
    company.className = 'jswipe-card-company';
    company.textContent = job.company;
    const title = document.createElement('h2');
    title.className = 'jswipe-card-title';
    title.textContent = job.title;
    const meta = document.createElement('div');
    meta.className = 'jswipe-card-meta';
    meta.append(
      makeChip('bi-geo-alt-fill', job.location || 'Location not listed'),
      makeChip('bi-calendar3', job.posted_at || 'Date not listed'),
      makeChip('bi-database-fill', job.source),
    );
    const fit = makeFit(job);
    const hint = document.createElement('p');
    hint.className = 'jswipe-card-hint mb-0';
    hint.textContent = 'Swipe or use the controls below. Arrow keys work too.';
    body.append(company, title, meta, fit, hint);
    card.append(accent, body);
    bindSwipe(card, job);
    return card;
  }

  function renderDeck() {
    const jobs = jobsFor('pending');
    elements.deck.replaceChildren();
    if (!jobs.length) {
      elements.deck.append(makeEmpty('pending'));
      elements.deckControls.hidden = true;
      return;
    }
    const job = jobs[0];
    elements.deck.append(makeCard(job));
    elements.openButton.href = job.url;
    elements.deckControls.hidden = false;
  }

  function makeListCard(job) {
    const card = document.createElement('article');
    card.className = 'jswipe-list-card';
    const details = document.createElement('div');
    const company = document.createElement('div');
    company.className = 'jswipe-card-company';
    company.textContent = job.company;
    const title = document.createElement('h2');
    title.className = 'h5 text-forest mb-1';
    title.textContent = job.title;
    const meta = document.createElement('p');
    meta.className = 'text-muted small mb-0';
    meta.textContent = [job.location || 'Location not listed', job.posted_at, job.source]
      .filter(Boolean)
      .join(' · ');
    details.append(company, title, meta, makeFit(job));

    const actions = document.createElement('div');
    actions.className = 'jswipe-list-actions';
    const open = document.createElement('a');
    open.className = 'btn btn-sm btn-outline-primary';
    open.href = job.url;
    open.target = '_blank';
    open.rel = 'noopener noreferrer';
    open.textContent = 'Open job';
    const reset = document.createElement('button');
    reset.className = 'btn btn-sm btn-outline-secondary';
    reset.type = 'button';
    reset.textContent = 'Return to deck';
    reset.addEventListener('click', () => setDecision(job, 'pending'));
    actions.append(open, reset);
    card.append(details, actions);
    return card;
  }

  function renderList() {
    const jobs = jobsFor(activeView);
    elements.jobList.replaceChildren();
    if (!jobs.length) {
      const wrapper = document.createElement('div');
      wrapper.className = 'jswipe-list-empty';
      wrapper.append(makeEmpty(activeView));
      elements.jobList.append(wrapper);
      return;
    }
    elements.jobList.append(...jobs.map(makeListCard));
  }

  function render() {
    updateCounts();
    const isDeck = activeView === 'pending';
    elements.deckView.hidden = !isDeck;
    elements.jobList.hidden = isDeck;
    if (isDeck) renderDeck();
    else renderList();
  }

  function decisionUrl(jobId) {
    return root.dataset.decisionTemplate.replace('JOB_ID', jobId);
  }

  async function setDecision(job, decision, animate = null) {
    if (decisionPending) return;
    decisionPending = true;
    const card = elements.deck.querySelector('.jswipe-card');
    if (animate && card) {
      card.classList.add(animate === 'left' ? 'is-exiting-left' : 'is-exiting-right');
      await new Promise((resolve) => window.setTimeout(resolve, 180));
    }

    try {
      const previous = job.decision;
      const payload = await requestJson(decisionUrl(job.job_id), {
        method: 'POST',
        body: JSON.stringify({ decision }),
      });
      applyJobs(payload);
      if (decision !== 'pending') showUndo(job, previous, decision);
      render();
    } catch (error) {
      showStatus(error.message, 'error');
      render();
    } finally {
      decisionPending = false;
    }
  }

  function currentJob() {
    return jobsFor('pending')[0] || null;
  }

  function showUndo(job, previous, decision) {
    lastDecision = { job, previous };
    elements.undoMessage.textContent = decision === 'shortlisted'
      ? `${job.company} shortlisted.`
      : `${job.company} passed.`;
    elements.undoBar.hidden = false;
    window.clearTimeout(undoTimer);
    undoTimer = window.setTimeout(() => {
      elements.undoBar.hidden = true;
      lastDecision = null;
    }, 8000);
  }

  function renderScanStatus() {
    const scan = state.last_scan;
    if (scan?.coverage_warnings?.length) {
      showStatus(
        `${scan.added} new roles added. Partial coverage: ${scan.coverage_warnings.join(' ')}`,
        'warning',
      );
    }
  }

  function bindSwipe(card, job) {
    card.addEventListener('pointerdown', (event) => {
      dragStart = { pointerId: event.pointerId, x: event.clientX };
      card.setPointerCapture(event.pointerId);
      card.classList.add('is-dragging');
    });
    card.addEventListener('pointermove', (event) => {
      if (!dragStart || dragStart.pointerId !== event.pointerId) return;
      const distance = event.clientX - dragStart.x;
      card.style.transform = `translateX(${distance}px) rotate(${distance / 35}deg)`;
    });
    card.addEventListener('pointerup', (event) => {
      if (!dragStart || dragStart.pointerId !== event.pointerId) return;
      const distance = event.clientX - dragStart.x;
      dragStart = null;
      card.classList.remove('is-dragging');
      card.style.transform = '';
      if (Math.abs(distance) >= 85) {
        setDecision(
          job,
          distance > 0 ? 'shortlisted' : 'passed',
          distance > 0 ? 'right' : 'left',
        );
      }
    });
    card.addEventListener('pointercancel', () => {
      dragStart = null;
      card.classList.remove('is-dragging');
      card.style.transform = '';
    });
  }

  const listFields = {
    candidate: {
      role_titles: 'profileRoles',
      skills: 'profileSkills',
      industries: 'profileIndustries',
      education: 'profileEducation',
      certifications: 'profileCertifications',
    },
    preferences: {
      target_roles: 'preferenceRoles',
      locations: 'preferenceLocations',
      employment_types: 'preferenceEmployment',
      preferred_skills: 'preferenceSkills',
      preferred_industries: 'preferenceIndustries',
      work_authorized_regions: 'preferenceAuthorized',
      excluded_title_terms: 'preferenceExcluded',
      dealbreakers: 'preferenceDealbreakers',
    },
  };
  const value = (id) => byId(id).value;
  const splitList = (text) => text.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);

  function setInput(id, next) {
    const input = byId(id);
    if (input) input.value = next ?? '';
  }

  function populateProfile(data) {
    const candidate = data.candidate || {};
    const preferences = data.preferences || {};
    Object.entries(listFields.candidate).forEach(([key, id]) => {
      setInput(id, (candidate[key] || []).join('\n'));
    });
    Object.entries(listFields.preferences).forEach(([key, id]) => {
      setInput(id, (preferences[key] || []).join('\n'));
    });
    setInput('profileHeadline', candidate.headline);
    setInput('profileSummary', candidate.summary);
    setInput('profileYears', candidate.years_experience);
    setInput('profileSeniority', candidate.seniority);
    setInput('preferenceRemote', preferences.remote || 'any');
    setInput('preferenceCompensation', preferences.minimum_compensation);
    setInput('preferenceCurrency', preferences.currency);
    setInput(
      'preferenceSponsorship',
      preferences.requires_sponsorship == null ? '' : String(preferences.requires_sponsorship),
    );
    setInput('preferenceSinceDays', preferences.since_days || 7);
    for (const checkbox of elements.profileForm.querySelectorAll('input[name="preference_sources"]')) {
      checkbox.checked = (preferences.sources || []).includes(checkbox.value);
    }
  }

  function renderResume() {
    const resume = personalization.resume;
    const hasResume = Boolean(resume);
    elements.resumeMeta.hidden = !hasResume;
    elements.resumeDownload.hidden = !hasResume;
    elements.resumeRemove.hidden = !hasResume;
    if (hasResume) {
      elements.resumeFilename.textContent = resume.filename || 'Resume uploaded';
      elements.resumeDetails.textContent = `${Math.ceil((resume.size_bytes || 0) / 1024)} KB · PDF`;
      elements.resumeDownload.href = root.dataset.resumeUrl;
    }
    elements.resumeSubmit.disabled = !elements.resumeFile.files.length;
    elements.resumeUploadLabel.textContent = hasResume ? 'Replace PDF' : 'Choose PDF';
  }

  function renderPersonalization() {
    const hasProfile = Boolean(personalization.candidate);
    elements.onboardingEmpty.hidden = hasProfile;
    elements.profileForm.hidden = !hasProfile;
    elements.scanToggle.disabled = !hasProfile;
    elements.profileStatus.hidden = !hasProfile;
    elements.reviewNote.hidden = !personalization.review_recommended;
    if (hasProfile) {
      elements.profileStatus.textContent = 'Profile ready';
      elements.profileStatus.className = 'jswipe-profile-status is-ready';
    }
    renderResume();
  }

  function profilePayloadFromForm() {
    const candidate = {
      headline: value('profileHeadline'),
      summary: value('profileSummary'),
      role_titles: splitList(value('profileRoles')),
      skills: splitList(value('profileSkills')),
      years_experience: value('profileYears') === '' ? null : Number(value('profileYears')),
      seniority: value('profileSeniority'),
      industries: splitList(value('profileIndustries')),
      education: splitList(value('profileEducation')),
      certifications: splitList(value('profileCertifications')),
    };
    const sponsorship = value('preferenceSponsorship');
    const compensation = value('preferenceCompensation');
    const sources = Array.from(
      elements.profileForm.querySelectorAll('input[name="preference_sources"]:checked'),
      (input) => input.value,
    );
    const preferences = {
      target_roles: splitList(value('preferenceRoles')),
      locations: splitList(value('preferenceLocations')),
      remote: value('preferenceRemote'),
      employment_types: splitList(value('preferenceEmployment')),
      minimum_compensation: compensation === '' ? null : Number(compensation),
      currency: value('preferenceCurrency'),
      work_authorized_regions: splitList(value('preferenceAuthorized')),
      requires_sponsorship: sponsorship === '' ? null : sponsorship === 'true',
      preferred_skills: splitList(value('preferenceSkills')),
      preferred_industries: splitList(value('preferenceIndustries')),
      excluded_title_terms: splitList(value('preferenceExcluded')),
      dealbreakers: splitList(value('preferenceDealbreakers')),
      sources,
      since_days: Number(value('preferenceSinceDays')),
    };
    return {
      candidate,
      preferences,
      profile_revision: personalization.profile_revision,
    };
  }

  elements.resumeFile.addEventListener('change', () => {
    elements.resumeSubmit.disabled = !elements.resumeFile.files.length;
    if (elements.resumeFile.files.length) {
      elements.resumeUploadLabel.textContent = elements.resumeFile.files[0].name;
    }
  });

  elements.resumeForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!elements.resumeFile.files.length) return;
    elements.resumeSubmit.disabled = true;
    elements.resumeSpinner.hidden = false;
    elements.resumeSubmitLabel.textContent = 'Reading PDF…';
    try {
      const payload = await requestJson(root.dataset.resumeUploadUrl, {
        method: 'POST',
        body: new FormData(elements.resumeForm),
      });
      applyProfile(payload);
      showStatus('Resume saved. Review your profile before scanning.');
    } catch (error) {
      showStatus(error.message, 'error');
    } finally {
      elements.resumeSpinner.hidden = true;
      elements.resumeSubmitLabel.textContent = 'Save resume';
      renderResume();
    }
  });

  elements.resumeRemove.addEventListener('click', async () => {
    if (!window.confirm('Remove the saved resume? Your editable profile will stay.')) return;
    elements.resumeRemove.disabled = true;
    try {
      applyProfile(await requestJson(root.dataset.resumeUrl, { method: 'DELETE' }));
      showStatus('Resume removed. Your profile is still available.');
    } catch (error) {
      showStatus(error.message, 'error');
    } finally {
      elements.resumeRemove.disabled = false;
    }
  });

  elements.profileForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    elements.profileSubmit.disabled = true;
    elements.profileSpinner.hidden = false;
    elements.profileSubmitLabel.textContent = 'Saving…';
    try {
      const payload = await requestJson(root.dataset.profileUrl, {
        method: 'PATCH',
        body: JSON.stringify(profilePayloadFromForm()),
      });
      applyProfile(payload);
      showStatus(
        payload.ranking_status === 'partial'
          ? 'Profile saved. Some fit scores will update as evidence becomes available.'
          : 'Profile saved and fit scores updated.',
      );
      render();
    } catch (error) {
      showStatus(error.message, 'error');
    } finally {
      elements.profileSubmit.disabled = false;
      elements.profileSpinner.hidden = true;
      elements.profileSubmitLabel.textContent = 'Save profile';
    }
  });

  async function clearPersonalization() {
    elements.confirmClear.disabled = true;
    try {
      applyProfile(await requestJson(root.dataset.clearProfileUrl, { method: 'DELETE' }));
      showStatus('Personalization cleared. Your jobs and decisions remain.');
      render();
    } catch (error) {
      showStatus(error.message, 'error');
    } finally {
      elements.confirmClear.disabled = false;
    }
  }

  elements.clearProfile.addEventListener('click', () => {
    if (typeof elements.clearDialog.showModal === 'function') {
      elements.clearDialog.showModal();
    } else if (window.confirm('Clear your profile, preferences, and resume? Your jobs and decisions will remain.')) {
      clearPersonalization();
    }
  });

  elements.confirmClear.addEventListener('click', (event) => {
    event.preventDefault();
    elements.clearDialog.close();
    clearPersonalization();
  });

  elements.scanToggle.addEventListener('click', () => {
    elements.scanPanel.hidden = false;
    elements.scanPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
  elements.scanClose.addEventListener('click', () => {
    elements.scanPanel.hidden = true;
  });
  elements.passButton.addEventListener('click', () => {
    const job = currentJob();
    if (job) setDecision(job, 'passed', 'left');
  });
  elements.shortlistButton.addEventListener('click', () => {
    const job = currentJob();
    if (job) setDecision(job, 'shortlisted', 'right');
  });
  elements.undoButton.addEventListener('click', async () => {
    if (!lastDecision) return;
    window.clearTimeout(undoTimer);
    elements.undoBar.hidden = true;
    const decision = lastDecision;
    lastDecision = null;
    await setDecision(decision.job, decision.previous);
  });

  for (const tab of document.querySelectorAll('.jswipe-tab')) {
    tab.addEventListener('click', () => {
      activeView = tab.dataset.view;
      for (const candidate of document.querySelectorAll('.jswipe-tab')) {
        const active = candidate === tab;
        candidate.classList.toggle('is-active', active);
        candidate.setAttribute('aria-pressed', String(active));
      }
      render();
    });
  }

  elements.scanForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(elements.scanForm);
    const payload = {
      keywords: form.get('keywords'),
      locations: form.get('locations'),
      since_days: Number(form.get('since_days')),
      sources: form.getAll('sources'),
      companies_per_source: Number(form.get('companies_per_source')),
    };
    elements.scanSubmit.disabled = true;
    elements.scanSpinner.hidden = false;
    elements.scanSubmitLabel.textContent = 'Scanning…';
    showStatus('Career-Ops is checking public ATS directories. This may take a few minutes.');
    try {
      applyJobs(await requestJson(root.dataset.scanUrl, {
        method: 'POST',
        body: JSON.stringify(payload),
      }));
      render();
      const scan = state.last_scan;
      const suffix = scan?.coverage_warnings?.length
        ? ` Partial coverage: ${scan.coverage_warnings.join(' ')}`
        : '';
      showStatus(
        `${scan?.added || 0} new roles added from ${scan?.companies_scanned || 0} companies.${suffix}`,
        scan?.coverage_warnings?.length ? 'warning' : 'info',
      );
      elements.scanPanel.hidden = true;
    } catch (error) {
      showStatus(error.message, 'error');
    } finally {
      elements.scanSubmit.disabled = false;
      elements.scanSpinner.hidden = true;
      elements.scanSubmitLabel.textContent = 'Scan jobs';
    }
  });

  document.addEventListener('keydown', (event) => {
    const target = event.target;
    if (
      activeView !== 'pending'
      || (target instanceof Element && target.matches('input, textarea, select, button, summary'))
    ) return;
    const job = currentJob();
    if (!job) return;
    if (event.key === 'ArrowLeft') setDecision(job, 'passed', 'left');
    if (event.key === 'ArrowRight') setDecision(job, 'shortlisted', 'right');
  });

  async function load() {
    try {
      const [jobs, profile] = await Promise.all([
        requestJson(root.dataset.jobsUrl),
        requestJson(root.dataset.profileUrl),
      ]);
      applyJobs(jobs);
      applyProfile(profile, false);
      populateSettings(state.last_scan ? state.settings : profileScanSettings(profile));
      render();
      renderScanStatus();
    } catch (error) {
      showStatus(error.message, 'error');
    }
  }

  function profileScanSettings(profile) {
    const candidate = profile?.candidate || {};
    const preferences = profile?.preferences || {};
    return {
      keywords: preferences.target_roles?.length
        ? preferences.target_roles
        : candidate.role_titles || [],
      locations: preferences.locations || [],
      sources: preferences.sources || [],
      since_days: preferences.since_days || 7,
    };
  }

  function populateSettings(settings) {
    if (!settings) return;
    elements.scanForm.elements.keywords.value = (settings.keywords || []).join('\n');
    elements.scanForm.elements.locations.value = (settings.locations || []).join('\n');
    elements.scanForm.elements.since_days.value = settings.since_days;
    if (settings.companies_per_source != null) {
      elements.companiesPerSource.value = settings.companies_per_source;
    }
    for (const checkbox of elements.scanForm.querySelectorAll('input[name="sources"]')) {
      checkbox.checked = (settings.sources || []).includes(checkbox.value);
    }
    updateScanEstimate();
  }

  elements.companiesPerSource.addEventListener('input', updateScanEstimate);
  for (const checkbox of elements.scanForm.querySelectorAll('input[name="sources"]')) {
    checkbox.addEventListener('change', updateScanEstimate);
  }
  updateScanEstimate();

  load();
})();
