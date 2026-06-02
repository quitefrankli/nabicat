(() => {
  const canvas = document.getElementById('astar-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  const wrap = canvas.parentElement;
  const playBtn = document.getElementById('astar-play');
  const playIcon = document.getElementById('astar-play-icon');
  const playLabel = document.getElementById('astar-play-label');
  const stepBtn = document.getElementById('astar-step');
  const resetBtn = document.getElementById('astar-reset');
  const mazeBtn = document.getElementById('astar-maze');
  const clearBtn = document.getElementById('astar-clear');
  const speedSlider = document.getElementById('astar-speed');
  const speedValue = document.getElementById('astar-speed-value');
  const dimsEl = document.getElementById('astar-dims');
  const openEl = document.getElementById('astar-open');
  const closedEl = document.getElementById('astar-closed');
  const pathEl = document.getElementById('astar-path');

  const CELL_PX = 26;
  const MIN_CELL_PX = 16;
  const MAZE_DENSITY = 0.3;

  // Honeydew palette (canvas can't read CSS vars)
  const COLOR_BG = '#F0FFF0';
  const COLOR_WALL = '#2D4A3E';
  const COLOR_OPEN = '#A8C686';
  const COLOR_CLOSED = 'rgba(135, 168, 120, 0.35)';
  const COLOR_PATH = '#E9C46A';
  const COLOR_START = '#6B8E5A';
  const COLOR_GOAL = '#E07A5F';
  const COLOR_GRID = 'rgba(135, 168, 120, 0.12)';

  let cols = 0, rows = 0;
  let walls = null;        // Uint8Array, 1 = wall
  let start = { x: 0, y: 0 };
  let goal = { x: 0, y: 0 };

  // A* working state
  let gScore = null;       // Float64Array
  let fScore = null;       // Float64Array
  let cameFrom = null;     // Int32Array, -1 = none
  let inOpen = null;       // Uint8Array
  let closed = null;       // Uint8Array
  let onPath = null;       // Uint8Array
  let openList = [];        // array of cell indices
  let openCount = 0, closedCount = 0;
  let status = 'idle';      // 'idle' | 'searching' | 'solved' | 'no-path'
  let pathLen = -1;

  let running = false;
  let stepsPerSec = parseInt(speedSlider.value, 10);
  let lastStep = 0;
  let rafId = null;

  const idx = (x, y) => y * cols + x;
  const heuristic = (x, y) => Math.abs(x - goal.x) + Math.abs(y - goal.y);

  function allocate(w, h) {
    const dpr = window.devicePixelRatio || 1;
    const cellPx = w < 480 ? MIN_CELL_PX : CELL_PX;
    const newCols = Math.max(6, Math.floor(w / cellPx));
    const newRows = Math.max(6, Math.floor(h / cellPx));
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const newWalls = new Uint8Array(newCols * newRows);
    if (walls) {
      const copyCols = Math.min(cols, newCols);
      const copyRows = Math.min(rows, newRows);
      for (let y = 0; y < copyRows; y++) {
        for (let x = 0; x < copyCols; x++) {
          newWalls[y * newCols + x] = walls[y * cols + x];
        }
      }
    }
    cols = newCols;
    rows = newRows;
    walls = newWalls;

    // Clamp / default start & goal into bounds
    start.x = Math.min(start.x, cols - 1);
    start.y = Math.min(start.y, rows - 1);
    goal.x = Math.min(goal.x, cols - 1);
    goal.y = Math.min(goal.y, rows - 1);

    dimsEl.textContent = `${cols} × ${rows}`;
  }

  function placeDefaultEndpoints() {
    start = { x: 1, y: 1 };
    goal = { x: cols - 2, y: rows - 2 };
    walls[idx(start.x, start.y)] = 0;
    walls[idx(goal.x, goal.y)] = 0;
  }

  function initSearch() {
    const n = cols * rows;
    gScore = new Float64Array(n).fill(Infinity);
    fScore = new Float64Array(n).fill(Infinity);
    cameFrom = new Int32Array(n).fill(-1);
    inOpen = new Uint8Array(n);
    closed = new Uint8Array(n);
    onPath = new Uint8Array(n);
    openList = [];
    openCount = 0;
    closedCount = 0;
    pathLen = -1;
    status = 'searching';
    running = false;

    const s = idx(start.x, start.y);
    gScore[s] = 0;
    fScore[s] = heuristic(start.x, start.y);
    inOpen[s] = 1;
    openList.push(s);
    openCount = 1;

    updateStatus();
    setRunning(false);
  }

  function popLowestF() {
    let best = -1, bestI = -1;
    for (let i = 0; i < openList.length; i++) {
      const c = openList[i];
      if (best === -1 || fScore[c] < fScore[best] ||
          (fScore[c] === fScore[best] && gScore[c] > gScore[best])) {
        best = c;
        bestI = i;
      }
    }
    if (bestI !== -1) {
      openList[bestI] = openList[openList.length - 1];
      openList.pop();
    }
    return best;
  }

  function reconstructPath(goalCell) {
    let c = goalCell, len = 0;
    while (c !== -1) {
      onPath[c] = 1;
      c = cameFrom[c];
      len++;
    }
    pathLen = len - 1; // edges, not nodes
  }

  function astarStep() {
    if (status !== 'searching') return;
    if (openList.length === 0) {
      status = 'no-path';
      running = false;
      setRunning(false);
      updateStatus();
      return;
    }

    const current = popLowestF();
    inOpen[current] = 0;

    const gc = idx(goal.x, goal.y);
    if (current === gc) {
      reconstructPath(current);
      status = 'solved';
      running = false;
      setRunning(false);
      updateStatus();
      return;
    }

    closed[current] = 1;
    closedCount++;

    const cx = current % cols;
    const cy = (current - cx) / cols;
    const neighbours = [
      [cx, cy - 1], [cx, cy + 1], [cx - 1, cy], [cx + 1, cy],
    ];
    for (const [nx, ny] of neighbours) {
      if (nx < 0 || nx >= cols || ny < 0 || ny >= rows) continue;
      const ni = idx(nx, ny);
      if (walls[ni] || closed[ni]) continue;
      const tentativeG = gScore[current] + 1;
      if (tentativeG < gScore[ni]) {
        cameFrom[ni] = current;
        gScore[ni] = tentativeG;
        fScore[ni] = tentativeG + heuristic(nx, ny);
        if (!inOpen[ni]) {
          inOpen[ni] = 1;
          openList.push(ni);
        }
      }
    }
    openCount = openList.length;
    updateStatus();
  }

  function updateStatus() {
    openEl.textContent = openCount;
    closedEl.textContent = closedCount;
    if (status === 'solved') pathEl.textContent = `${pathLen} steps`;
    else if (status === 'no-path') pathEl.textContent = 'no path';
    else pathEl.textContent = '—';
  }

  function randomMaze() {
    walls.fill(0);
    for (let i = 0; i < walls.length; i++) {
      walls[i] = Math.random() < MAZE_DENSITY ? 1 : 0;
    }
    walls[idx(start.x, start.y)] = 0;
    walls[idx(goal.x, goal.y)] = 0;
    initSearch();
  }

  function clearWalls() {
    walls.fill(0);
    initSearch();
  }

  function draw() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const cw = w / cols;
    const ch = h / rows;

    ctx.fillStyle = COLOR_BG;
    ctx.fillRect(0, 0, w, h);

    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const i = idx(x, y);
        let color = null;
        if (walls[i]) color = COLOR_WALL;
        else if (onPath[i]) color = COLOR_PATH;
        else if (inOpen[i]) color = COLOR_OPEN;
        else if (closed[i]) color = COLOR_CLOSED;
        if (color) {
          ctx.fillStyle = color;
          ctx.fillRect(x * cw, y * ch, cw - 0.5, ch - 0.5);
        }
      }
    }

    // Start & goal on top
    ctx.fillStyle = COLOR_START;
    ctx.fillRect(start.x * cw, start.y * ch, cw - 0.5, ch - 0.5);
    ctx.fillStyle = COLOR_GOAL;
    ctx.fillRect(goal.x * cw, goal.y * ch, cw - 0.5, ch - 0.5);

    if (cw >= 12) {
      ctx.strokeStyle = COLOR_GRID;
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

    drawScores(cw, ch);
  }

  function drawScores(cw, ch) {
    if (cw < 24 || ch < 24) return; // too small to read
    const showGH = cw >= 44 && ch >= 44;
    ctx.fillStyle = COLOR_WALL;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const i = idx(x, y);
        if (walls[i] || !isFinite(gScore[i])) continue;
        const cxp = x * cw, cyp = y * ch;
        if (showGH) {
          const small = Math.max(7, Math.floor(ch * 0.22));
          ctx.font = `${small}px 'SF Mono', ui-monospace, monospace`;
          ctx.textAlign = 'left';
          ctx.fillText(String(gScore[i]), cxp + 3, cyp + small * 0.9);
          ctx.textAlign = 'right';
          ctx.fillText(String(heuristic(x, y)), cxp + cw - 3, cyp + small * 0.9);
          ctx.textAlign = 'center';
          const big = Math.max(9, Math.floor(ch * 0.3));
          ctx.font = `bold ${big}px 'SF Mono', ui-monospace, monospace`;
          ctx.fillText(String(fScore[i]), cxp + cw / 2, cyp + ch * 0.68);
        } else {
          const fs = Math.max(8, Math.floor(ch * 0.34));
          ctx.font = `bold ${fs}px 'SF Mono', ui-monospace, monospace`;
          ctx.fillText(String(fScore[i]), cxp + cw / 2, cyp + ch / 2);
        }
      }
    }
  }

  function tick(ts) {
    rafId = requestAnimationFrame(tick);
    if (!running) return;
    const interval = 1000 / stepsPerSec;
    if (ts - lastStep >= interval) {
      lastStep = ts;
      astarStep();
      draw();
      if (status !== 'searching') running = false;
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

  let dragMode = null; // 'wall' | 'start' | 'goal'
  let paintValue = 1;

  canvas.addEventListener('pointerdown', (ev) => {
    const cell = pointerCellFromEvent(ev);
    if (!cell) return;
    canvas.setPointerCapture(ev.pointerId);
    const [cx, cy] = cell;
    if (cx === start.x && cy === start.y) {
      dragMode = 'start';
    } else if (cx === goal.x && cy === goal.y) {
      dragMode = 'goal';
    } else {
      dragMode = 'wall';
      paintValue = walls[idx(cx, cy)] ? 0 : 1;
      walls[idx(cx, cy)] = paintValue;
      initSearch();
    }
    draw();
    ev.preventDefault();
  });

  canvas.addEventListener('pointermove', (ev) => {
    if (!dragMode) return;
    const cell = pointerCellFromEvent(ev);
    if (!cell) return;
    const [cx, cy] = cell;
    if (dragMode === 'start') {
      if (walls[idx(cx, cy)] || (cx === goal.x && cy === goal.y)) return;
      if (cx === start.x && cy === start.y) return;
      start = { x: cx, y: cy };
      initSearch();
      draw();
    } else if (dragMode === 'goal') {
      if (walls[idx(cx, cy)] || (cx === start.x && cy === start.y)) return;
      if (cx === goal.x && cy === goal.y) return;
      goal = { x: cx, y: cy };
      initSearch();
      draw();
    } else {
      if (cx === start.x && cy === start.y) return;
      if (cx === goal.x && cy === goal.y) return;
      if (walls[idx(cx, cy)] !== paintValue) {
        walls[idx(cx, cy)] = paintValue;
        initSearch();
        draw();
      }
    }
  });

  function endDrag(ev) {
    dragMode = null;
    try { canvas.releasePointerCapture(ev.pointerId); } catch (e) {}
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);

  playBtn.addEventListener('click', () => {
    if (status !== 'searching') initSearch();
    setRunning(!running);
  });
  stepBtn.addEventListener('click', () => { astarStep(); draw(); });
  resetBtn.addEventListener('click', () => { initSearch(); draw(); });
  mazeBtn.addEventListener('click', () => { randomMaze(); draw(); });
  clearBtn.addEventListener('click', () => { clearWalls(); draw(); });
  speedSlider.addEventListener('input', (e) => {
    stepsPerSec = parseInt(e.target.value, 10);
    speedValue.textContent = stepsPerSec;
  });

  const ro = new ResizeObserver(() => {
    const w = wrap.clientWidth;
    const h = wrap.clientHeight;
    if (w > 0 && h > 0) {
      allocate(w, h);
      initSearch();
      draw();
    }
  });
  ro.observe(wrap);

  requestAnimationFrame(() => {
    allocate(wrap.clientWidth, wrap.clientHeight);
    placeDefaultEndpoints();
    initSearch();
    draw();
    rafId = requestAnimationFrame(tick);
  });
})();
