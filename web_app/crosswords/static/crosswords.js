(() => {
  const grid = document.getElementById('crossword-grid');
  const welcome = document.getElementById('crossword-welcome');
  const puzzleWrap = document.getElementById('crossword-puzzle');
  const acrossList = document.getElementById('clues-across');
  const downList = document.getElementById('clues-down');
  const statusEl = document.getElementById('crossword-status');

  const form = document.getElementById('crossword-form');
  const themeInput = document.getElementById('cw-theme');
  const themeError = document.getElementById('cw-theme-error');
  const difficultyInput = document.getElementById('cw-difficulty');
  const difficultyLabel = document.getElementById('cw-difficulty-label');
  const puzzleThemeEl = document.getElementById('puzzle-theme');
  const puzzleDifficultyEl = document.getElementById('puzzle-difficulty');
  const generateBtn = document.getElementById('btnGenerate');
  const generateBtnOriginalHTML = generateBtn?.innerHTML;

  const THEME_MIN = Number(themeInput?.dataset.minLen) || 2;
  const THEME_MAX = Number(themeInput?.dataset.maxLen) || 13;
  const THEME_CRITERIA = `Theme must be a single word (${THEME_MIN}-${THEME_MAX} letters, no spaces, hyphens, numbers, or punctuation).`;

  const actCheck = document.getElementById('actionCheck');
  const actReveal = document.getElementById('actionReveal');
  const actClear = document.getElementById('actionClear');

  const DIFFICULTY_NAMES = {
    1: 'Easy',
    2: 'Chill',
    3: 'Medium',
    4: 'Hard',
    5: 'Brutal',
  };

  let puzzle = null;
  let inputs = [];
  let activeClue = null;

  const csrfToken = document
    .querySelector('meta[name="csrf-token"]')
    ?.getAttribute('content');

  function updateDifficultyLabel() {
    const v = Number(difficultyInput.value);
    difficultyLabel.textContent = `${DIFFICULTY_NAMES[v] || v} (${v})`;
  }

  function setStatus(msg, kind = 'muted') {
    statusEl.textContent = msg || '';
    statusEl.className = `crossword-status small text-center mt-3 text-${kind}`;
  }

  function showThemeError(msg) {
    themeError.textContent = msg;
    themeError.classList.remove('d-none');
    themeInput.classList.add('is-invalid');
  }

  function clearThemeError() {
    themeError.textContent = '';
    themeError.classList.add('d-none');
    themeInput.classList.remove('is-invalid');
  }

  function validateThemeClient(theme) {
    if (!theme) return 'Theme is required.';
    if (!/^[A-Za-z]+$/.test(theme)) return 'Theme must contain letters only — no spaces, hyphens, digits, or punctuation.';
    if (theme.length < THEME_MIN || theme.length > THEME_MAX) {
      return `Theme must be between ${THEME_MIN} and ${THEME_MAX} letters long.`;
    }
    return null;
  }

  function setGenerating(isGenerating) {
    if (!generateBtn) return;
    generateBtn.disabled = isGenerating;
    if (isGenerating) {
      generateBtn.innerHTML =
        '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' +
        'Generating… (this may take up to 30s)';
    } else {
      generateBtn.innerHTML = generateBtnOriginalHTML;
    }
  }

  async function fetchNewPuzzle(evt) {
    if (evt) evt.preventDefault();
    if (generateBtn?.disabled) return;

    const theme = (themeInput.value || '').trim();
    const clientErr = validateThemeClient(theme);
    if (clientErr) {
      showThemeError(`${clientErr} ${THEME_CRITERIA}`);
      return;
    }
    clearThemeError();

    const difficulty = Number(difficultyInput.value);
    setStatus('Generating...');
    setGenerating(true);
    try {
      const res = await fetch('/crosswords/api/new', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken || '',
        },
        body: JSON.stringify({ theme, difficulty }),
      });
      if (res.status === 400) {
        const body = await res.json().catch(() => ({}));
        const reason = body.error || 'Invalid theme.';
        const criteria = body.criteria || THEME_CRITERIA;
        showThemeError(`${reason} ${criteria}`);
        setStatus('');
        return;
      }
      if (!res.ok) {
        setStatus('Failed to generate puzzle', 'danger');
        return;
      }
      puzzle = await res.json();
      renderGrid();
      renderClues();
      puzzleThemeEl.textContent = puzzle.theme || theme;
      puzzleDifficultyEl.textContent = `${DIFFICULTY_NAMES[puzzle.difficulty] || puzzle.difficulty} (${puzzle.difficulty})`;
      welcome.classList.add('d-none');
      puzzleWrap.classList.remove('d-none');
      setStatus('');
    } finally {
      setGenerating(false);
    }
  }

  function renderGrid() {
    grid.innerHTML = '';
    fitGridToViewport();
    grid.style.gridTemplateColumns = `repeat(${puzzle.cols}, var(--cw-cell))`;
    grid.style.gridTemplateRows = `repeat(${puzzle.rows}, var(--cw-cell))`;
    inputs = Array.from({ length: puzzle.rows }, () => Array(puzzle.cols).fill(null));

    for (let r = 0; r < puzzle.rows; r++) {
      for (let c = 0; c < puzzle.cols; c++) {
        const cell = puzzle.cells[r][c];
        const div = document.createElement('div');
        if (!cell) {
          div.className = 'crossword-cell blocked';
          grid.appendChild(div);
          continue;
        }
        div.className = 'crossword-cell open';
        if (cell.number !== null && cell.number !== undefined) {
          const num = document.createElement('span');
          num.className = 'cell-number';
          num.textContent = cell.number;
          div.appendChild(num);
        }
        const input = document.createElement('input');
        input.type = 'text';
        input.maxLength = 1;
        input.autocomplete = 'off';
        input.autocapitalize = 'characters';
        input.spellcheck = false;
        input.inputMode = 'text';
        input.dataset.row = r;
        input.dataset.col = c;
        input.addEventListener('input', onInput);
        input.addEventListener('keydown', onKeyDown);
        input.addEventListener('focus', onFocus);
        div.appendChild(input);
        inputs[r][c] = input;
        grid.appendChild(div);
      }
    }
  }

  // Size cells so the full grid fits within the container on every viewport.
  // CSS breakpoints alone can't react to puzzle dimensions, so an oversized
  // puzzle on a phone would overflow horizontally.
  function fitGridToViewport() {
    if (!puzzle) return;
    const container = grid.parentElement; // .crossword-container
    if (!container) return;
    const containerStyle = getComputedStyle(container);
    const padX = parseFloat(containerStyle.paddingLeft) + parseFloat(containerStyle.paddingRight);
    const available = Math.max(0, container.clientWidth - padX);
    const gap = 3; // matches .crossword-grid gap
    const totalGaps = gap * (puzzle.cols - 1);
    const computed = Math.floor((available - totalGaps) / puzzle.cols);
    const max = 44;
    const min = 22;
    const size = Math.max(min, Math.min(max, computed));
    document.documentElement.style.setProperty('--cw-cell', `${size}px`);
  }

  function renderClues() {
    const fill = (ul, clues, direction) => {
      ul.innerHTML = '';
      for (const clue of clues) {
        const li = document.createElement('li');
        li.className = 'clue-item';
        li.dataset.direction = direction;
        li.dataset.number = clue.number;
        li.innerHTML = `<span class="clue-num">${clue.number}.</span> <span class="clue-text">${escapeHtml(clue.clue)}</span> <span class="clue-len">(${clue.length})</span>`;
        li.addEventListener('click', () => focusClue(direction, clue.number));
        ul.appendChild(li);
      }
    };
    fill(acrossList, puzzle.clues.across, 'across');
    fill(downList, puzzle.clues.down, 'down');
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (m) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[m]));
  }

  function clueContainingCell(r, c, direction) {
    const list = puzzle.clues[direction];
    for (const clue of list) {
      if (direction === 'across' && clue.row === r && c >= clue.col && c < clue.col + clue.length) return clue;
      if (direction === 'down' && clue.col === c && r >= clue.row && r < clue.row + clue.length) return clue;
    }
    return null;
  }

  function cellsForClue(clue, direction) {
    const out = [];
    for (let i = 0; i < clue.length; i++) {
      const r = direction === 'across' ? clue.row : clue.row + i;
      const c = direction === 'across' ? clue.col + i : clue.col;
      out.push([r, c]);
    }
    return out;
  }

  function highlightClue() {
    for (const row of inputs) {
      for (const input of row) {
        if (input) input.parentElement.classList.remove('highlight', 'active');
      }
    }
    for (const li of document.querySelectorAll('.clue-item')) li.classList.remove('active');
    if (!activeClue) return;
    const clue = puzzle.clues[activeClue.direction].find((c) => c.number === activeClue.number);
    if (!clue) return;
    for (const [r, c] of cellsForClue(clue, activeClue.direction)) {
      const input = inputs[r][c];
      if (input) input.parentElement.classList.add('highlight');
    }
    const li = document.querySelector(`.clue-item[data-direction="${activeClue.direction}"][data-number="${activeClue.number}"]`);
    if (li) {
      li.classList.add('active');
      scrollClueIntoView(li);
    }
  }

  // Manually scroll only the clue list container — using Element.scrollIntoView
  // bubbles up the scroll chain and also scrolls the window on mobile, which
  // fights with the browser's auto-scroll of the focused input and causes the
  // viewport to oscillate.
  function scrollClueIntoView(li) {
    const list = li.closest('.clue-list');
    if (!list) return;
    const liTop = li.offsetTop;
    const liBottom = liTop + li.offsetHeight;
    if (liTop < list.scrollTop) {
      list.scrollTop = liTop;
    } else if (liBottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = liBottom - list.clientHeight;
    }
  }

  function focusClue(direction, number) {
    const clue = puzzle.clues[direction].find((c) => c.number === number);
    if (!clue) return;
    activeClue = { direction, number };
    const [[r, c]] = cellsForClue(clue, direction);
    inputs[r][c]?.focus();
    inputs[r][c]?.select();
    highlightClue();
  }

  function onFocus(e) {
    const r = Number(e.target.dataset.row);
    const c = Number(e.target.dataset.col);
    const preferred = activeClue?.direction || 'across';
    let clue = clueContainingCell(r, c, preferred);
    if (!clue) clue = clueContainingCell(r, c, preferred === 'across' ? 'down' : 'across');
    if (clue) activeClue = { direction: clue === clueContainingCell(r, c, 'across') ? 'across' : 'down', number: clue.number };
    e.target.parentElement.classList.add('active');
    highlightClue();
  }

  function onInput(e) {
    const v = e.target.value.toUpperCase().replace(/[^A-Z]/g, '');
    e.target.value = v.slice(0, 1);
    if (v) {
      advance(e.target, +1);
      if (isFullyFilled()) check();
    }
  }

  function isFullyFilled() {
    for (let r = 0; r < puzzle.rows; r++) {
      for (let c = 0; c < puzzle.cols; c++) {
        if (puzzle.cells[r][c] && !inputs[r][c].value) return false;
      }
    }
    return true;
  }

  function onKeyDown(e) {
    const r = Number(e.target.dataset.row);
    const c = Number(e.target.dataset.col);
    switch (e.key) {
      case 'Backspace':
        if (!e.target.value) {
          advance(e.target, -1);
          e.preventDefault();
        }
        break;
      case 'ArrowRight': move(r, c, 0, 1); e.preventDefault(); break;
      case 'ArrowLeft':  move(r, c, 0, -1); e.preventDefault(); break;
      case 'ArrowDown':  move(r, c, 1, 0); e.preventDefault(); break;
      case 'ArrowUp':    move(r, c, -1, 0); e.preventDefault(); break;
      case ' ':
      case 'Tab':
        if (activeClue) {
          activeClue.direction = activeClue.direction === 'across' ? 'down' : 'across';
          const clue = clueContainingCell(r, c, activeClue.direction);
          if (clue) activeClue.number = clue.number;
          highlightClue();
          e.preventDefault();
        }
        break;
    }
  }

  function advance(input, step) {
    const r = Number(input.dataset.row);
    const c = Number(input.dataset.col);
    if (!activeClue) return;
    const clue = puzzle.clues[activeClue.direction].find((cl) => cl.number === activeClue.number);
    if (!clue) return;
    const cells = cellsForClue(clue, activeClue.direction);
    const idx = cells.findIndex(([rr, cc]) => rr === r && cc === c);
    const next = cells[idx + step];
    if (next) {
      const el = inputs[next[0]][next[1]];
      if (step < 0) el.value = '';
      el.focus();
      el.select();
    }
  }

  function move(r, c, dr, dc) {
    for (let i = 1; i < Math.max(puzzle.rows, puzzle.cols); i++) {
      const nr = r + dr * i;
      const nc = c + dc * i;
      if (nr < 0 || nc < 0 || nr >= puzzle.rows || nc >= puzzle.cols) return;
      const el = inputs[nr]?.[nc];
      if (el) { el.focus(); el.select(); return; }
    }
  }

  function check() {
    let correct = 0, filled = 0, total = 0;
    for (let r = 0; r < puzzle.rows; r++) {
      for (let c = 0; c < puzzle.cols; c++) {
        const cell = puzzle.cells[r][c];
        if (!cell) continue;
        total++;
        const input = inputs[r][c];
        input.parentElement.classList.remove('correct', 'wrong');
        if (!input.value) continue;
        filled++;
        if (input.value.toUpperCase() === cell.letter) {
          input.parentElement.classList.add('correct');
          correct++;
        } else {
          input.parentElement.classList.add('wrong');
        }
      }
    }
    if (correct === total) setStatus(`Solved! ${correct}/${total} correct.`, 'success');
    else setStatus(`${correct}/${total} correct (${filled} filled)`, 'muted');
  }

  function reveal() {
    for (let r = 0; r < puzzle.rows; r++) {
      for (let c = 0; c < puzzle.cols; c++) {
        const cell = puzzle.cells[r][c];
        if (!cell) continue;
        inputs[r][c].value = cell.letter;
        inputs[r][c].parentElement.classList.add('revealed');
        inputs[r][c].parentElement.classList.remove('wrong');
      }
    }
    setStatus('Answers revealed', 'muted');
  }

  function clearAll() {
    for (const row of inputs) {
      for (const input of row) {
        if (!input) continue;
        input.value = '';
        input.parentElement.classList.remove('correct', 'wrong', 'revealed');
      }
    }
    setStatus('');
  }

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    if (!puzzle) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(fitGridToViewport, 100);
  });

  difficultyInput?.addEventListener('input', updateDifficultyLabel);
  themeInput?.addEventListener('input', clearThemeError);
  form?.addEventListener('submit', fetchNewPuzzle);
  actCheck?.addEventListener('click', () => puzzle && check());
  actReveal?.addEventListener('click', () => puzzle && reveal());
  actClear?.addEventListener('click', () => puzzle && clearAll());

  updateDifficultyLabel();
})();
