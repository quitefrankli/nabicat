(() => {
  const canvas = document.getElementById('gol-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const wrap = canvas.parentElement;
  const playBtn = document.getElementById('gol-play');
  const playIcon = document.getElementById('gol-play-icon');
  const playLabel = document.getElementById('gol-play-label');
  const stepBtn = document.getElementById('gol-step');
  const resetBtn = document.getElementById('gol-reset');
  const randomBtn = document.getElementById('gol-random');
  const clearBtn = document.getElementById('gol-clear');
  const speedSlider = document.getElementById('gol-speed');
  const speedValue = document.getElementById('gol-speed-value');
  const dimsEl = document.getElementById('gol-dims');
  const aliveEl = document.getElementById('gol-alive');
  const genEl = document.getElementById('gol-gen');

  const CELL_PX = 14;
  const MIN_CELL_PX = 8;

  let cols = 0, rows = 0;
  let grid = null;
  let next = null;
  let generation = 0;
  let running = false;
  let gensPerSec = parseInt(speedSlider.value, 10);
  let lastStep = 0;
  let rafId = null;

  function allocate(w, h) {
    const dpr = window.devicePixelRatio || 1;
    const cellPx = w < 480 ? MIN_CELL_PX : CELL_PX;
    cols = Math.max(8, Math.floor(w / cellPx));
    rows = Math.max(8, Math.floor(h / cellPx));
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const newGrid = new Uint8Array(cols * rows);
    if (grid) {
      const oldCols = grid.__cols || cols;
      const oldRows = grid.__rows || rows;
      const copyCols = Math.min(oldCols, cols);
      const copyRows = Math.min(oldRows, rows);
      for (let y = 0; y < copyRows; y++) {
        for (let x = 0; x < copyCols; x++) {
          newGrid[y * cols + x] = grid[y * oldCols + x];
        }
      }
    }
    newGrid.__cols = cols;
    newGrid.__rows = rows;
    grid = newGrid;
    next = new Uint8Array(cols * rows);
    dimsEl.textContent = `${cols} × ${rows}`;
  }

  function seedGlider() {
    grid.fill(0);
    const cx = Math.floor(cols / 4);
    const cy = Math.floor(rows / 4);
    // Classic glider
    const pattern = [[1,0],[2,1],[0,2],[1,2],[2,2]];
    for (const [dx, dy] of pattern) {
      const x = (cx + dx) % cols;
      const y = (cy + dy) % rows;
      grid[y * cols + x] = 1;
    }
    generation = 0;
  }

  function randomize() {
    for (let i = 0; i < grid.length; i++) {
      grid[i] = Math.random() < 0.25 ? 1 : 0;
    }
    generation = 0;
  }

  function clearGrid() {
    grid.fill(0);
    generation = 0;
  }

  function step() {
    let alive = 0;
    for (let y = 0; y < rows; y++) {
      const yUp = (y - 1 + rows) % rows;
      const yDn = (y + 1) % rows;
      for (let x = 0; x < cols; x++) {
        const xLf = (x - 1 + cols) % cols;
        const xRt = (x + 1) % cols;
        const n =
          grid[yUp * cols + xLf] + grid[yUp * cols + x] + grid[yUp * cols + xRt] +
          grid[y   * cols + xLf]                          + grid[y   * cols + xRt] +
          grid[yDn * cols + xLf] + grid[yDn * cols + x] + grid[yDn * cols + xRt];
        const cur = grid[y * cols + x];
        const live = (cur && (n === 2 || n === 3)) || (!cur && n === 3) ? 1 : 0;
        next[y * cols + x] = live;
        if (live) alive++;
      }
    }
    const tmp = grid;
    next.__cols = cols; next.__rows = rows;
    grid = next;
    next = tmp;
    generation++;
    aliveEl.textContent = alive;
    genEl.textContent = generation;
  }

  function countAlive() {
    let a = 0;
    for (let i = 0; i < grid.length; i++) a += grid[i];
    return a;
  }

  function draw() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const cw = w / cols;
    const ch = h / rows;

    ctx.fillStyle = '#F0FFF0';
    ctx.fillRect(0, 0, w, h);

    ctx.fillStyle = '#2D4A3E';
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        if (grid[y * cols + x]) {
          ctx.fillRect(x * cw, y * ch, cw - 0.5, ch - 0.5);
        }
      }
    }

    // Subtle grid lines on large cells only
    if (cw >= 12) {
      ctx.strokeStyle = 'rgba(135, 168, 120, 0.12)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = 0; x <= cols; x++) {
        ctx.moveTo(x * cw, 0);
        ctx.lineTo(x * cw, h);
      }
      for (let y = 0; y <= rows; y++) {
        ctx.moveTo(0, y * ch);
        ctx.lineTo(w, y * ch);
      }
      ctx.stroke();
    }
  }

  function tick(ts) {
    rafId = requestAnimationFrame(tick);
    if (!running) return;
    const interval = 1000 / gensPerSec;
    if (ts - lastStep >= interval) {
      lastStep = ts;
      step();
      draw();
    }
  }

  function setRunning(r) {
    running = r;
    playIcon.className = r ? 'bi bi-pause-fill' : 'bi bi-play-fill';
    playLabel.textContent = r ? 'Pause' : 'Play';
  }

  function pointerCellFromEvent(ev) {
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    const cx = Math.floor(x / (rect.width / cols));
    const cy = Math.floor(y / (rect.height / rows));
    if (cx < 0 || cx >= cols || cy < 0 || cy >= rows) return null;
    return [cx, cy];
  }

  let painting = false;
  let paintValue = 1;
  canvas.addEventListener('pointerdown', (ev) => {
    const cell = pointerCellFromEvent(ev);
    if (!cell) return;
    canvas.setPointerCapture(ev.pointerId);
    painting = true;
    const [cx, cy] = cell;
    paintValue = grid[cy * cols + cx] ? 0 : 1;
    grid[cy * cols + cx] = paintValue;
    aliveEl.textContent = countAlive();
    draw();
    ev.preventDefault();
  });

  canvas.addEventListener('pointermove', (ev) => {
    if (!painting) return;
    const cell = pointerCellFromEvent(ev);
    if (!cell) return;
    const [cx, cy] = cell;
    if (grid[cy * cols + cx] !== paintValue) {
      grid[cy * cols + cx] = paintValue;
      aliveEl.textContent = countAlive();
      draw();
    }
  });

  function endPaint(ev) {
    painting = false;
    try { canvas.releasePointerCapture(ev.pointerId); } catch (e) {}
  }
  canvas.addEventListener('pointerup', endPaint);
  canvas.addEventListener('pointercancel', endPaint);

  playBtn.addEventListener('click', () => setRunning(!running));
  stepBtn.addEventListener('click', () => { step(); draw(); });
  resetBtn.addEventListener('click', () => { seedGlider(); aliveEl.textContent = countAlive(); genEl.textContent = 0; draw(); });
  randomBtn.addEventListener('click', () => { randomize(); aliveEl.textContent = countAlive(); genEl.textContent = 0; draw(); });
  clearBtn.addEventListener('click', () => { clearGrid(); aliveEl.textContent = 0; genEl.textContent = 0; draw(); });
  speedSlider.addEventListener('input', (e) => {
    gensPerSec = parseInt(e.target.value, 10);
    speedValue.textContent = gensPerSec;
  });

  const ro = new ResizeObserver(() => {
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (w > 0 && h > 0) {
      allocate(w, h);
      aliveEl.textContent = countAlive();
      draw();
    }
  });
  ro.observe(wrap);

  // Initial size + seed
  requestAnimationFrame(() => {
    allocate(wrap.clientWidth, wrap.clientHeight);
    seedGlider();
    aliveEl.textContent = countAlive();
    genEl.textContent = 0;
    draw();
    rafId = requestAnimationFrame(tick);
  });
})();
