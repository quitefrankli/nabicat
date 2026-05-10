class LogViewer {
    constructor() {
        this.lines = [];
        this.nextSince = null;
        this.followMode = false;
        this.followTimer = null;
        this.activeLevel = 'ALL';
        this.searchQuery = '';
        this.excludeQuery = '';

        this.output = document.getElementById('log-output');
        this.searchInput = document.getElementById('log-search');
        this.searchClear = document.getElementById('search-clear');
        this.excludeInput = document.getElementById('log-exclude');
        this.excludeClear = document.getElementById('exclude-clear');
        this.lineCount = document.getElementById('line-count');
        this.refreshBtn = document.getElementById('refresh-btn');
        this.jumpBottomBtn = document.getElementById('jump-bottom-btn');
        this.followBtn = document.getElementById('follow-btn');

        this.init();
    }

    async init() {
        await this.fetchAll();
        this.scrollToBottom();
        this.bindEvents();
    }

    async fetchAll() {
        try {
            const data = await this._get('/dev/logs');
            this.lines = data.lines;
            this.nextSince = data.total;
            this.render();
        } catch (_) {
            this.output.innerHTML = '<div class="log-status">Failed to load logs.</div>';
        }
    }

    async fetchNew() {
        if (this.nextSince === null) return;
        try {
            const data = await this._get(`/dev/logs?since=${this.nextSince}`);
            if (!data.lines.length) return;
            const atBottom = this.isAtBottom();
            this.lines = this.lines.concat(data.lines);
            this.nextSince = data.total;
            this.render();
            if (atBottom) this.scrollToBottom();
        } catch (_) {}
    }

    async _get(url) {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(resp.status);
        return resp.json();
    }

    render() {
        const filtered = this.filterLines(this.lines);
        this.lineCount.textContent = `${filtered.length} / ${this.lines.length}`;
        if (!filtered.length) {
            this.output.innerHTML = '<div class="log-status">No matching lines.</div>';
            return;
        }
        this.output.innerHTML = filtered.map(l => this.renderLine(l)).join('');
    }

    filterLines(lines) {
        const q = this.searchQuery.toLowerCase();
        const ex = this.excludeQuery.toLowerCase();
        return lines.filter(line => {
            if (this.activeLevel !== 'ALL' && !this.lineMatchesLevel(line, this.activeLevel)) return false;
            const lower = (q || ex) ? line.toLowerCase() : '';
            if (q && !lower.includes(q)) return false;
            if (ex && lower.includes(ex)) return false;
            return true;
        });
    }

    lineMatchesLevel(line, level) {
        // Match the log format: "YYYY-MM-DD HH:MM:SS,mmm LEVEL "
        return new RegExp(`\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2},\\d{3} ${level} `).test(line);
    }

    renderLine(raw) {
        const m = raw.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (INFO|WARNING|ERROR|DEBUG) ([\s\S]*)$/);
        if (m) {
            const level = m[2].toLowerCase();
            const safeMsg = this.highlight(this.esc(m[3]));
            const html = `<span class="log-ts">${m[1]}</span> <span class="log-lvl log-lvl-${level}">${m[2]}</span> ${safeMsg}`;
            return `<div class="log-line log-${level}">${html}</div>`;
        }
        return `<div class="log-line log-info">${this.highlight(this.esc(raw))}</div>`;
    }

    esc(s) {
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    highlight(s) {
        if (!this.searchQuery) return s;
        const pattern = this.searchQuery.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return s.replace(new RegExp(pattern, 'gi'), m => `<mark>${m}</mark>`);
    }

    scrollToBottom() {
        this.output.scrollTop = this.output.scrollHeight;
    }

    isAtBottom() {
        return (this.output.scrollHeight - this.output.scrollTop - this.output.clientHeight) < 80;
    }

    bindEvents() {
        this.searchInput.addEventListener('input', () => {
            this.searchQuery = this.searchInput.value;
            this.searchClear.style.display = this.searchQuery ? 'block' : 'none';
            this.render();
        });

        this.searchClear.addEventListener('click', () => {
            this.searchInput.value = '';
            this.searchQuery = '';
            this.searchClear.style.display = 'none';
            this.render();
        });

        this.excludeInput.addEventListener('input', () => {
            this.excludeQuery = this.excludeInput.value;
            this.excludeClear.style.display = this.excludeQuery ? 'block' : 'none';
            this.render();
        });

        this.excludeClear.addEventListener('click', () => {
            this.excludeInput.value = '';
            this.excludeQuery = '';
            this.excludeClear.style.display = 'none';
            this.render();
        });

        document.querySelectorAll('.level-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.level-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                this.activeLevel = btn.dataset.level;
                this.render();
            });
        });

        this.refreshBtn.addEventListener('click', () => this.fetchAll());
        this.jumpBottomBtn.addEventListener('click', () => this.scrollToBottom());
        this.followBtn.addEventListener('click', () => this.toggleFollow());
    }

    toggleFollow() {
        this.followMode = !this.followMode;
        this.followBtn.classList.toggle('active', this.followMode);
        if (this.followMode) {
            this.followTimer = setInterval(() => this.fetchNew(), 2000);
            this.scrollToBottom();
        } else {
            clearInterval(this.followTimer);
            this.followTimer = null;
        }
    }
}


class TerminalView {
    constructor() {
        this.mount = document.getElementById('terminal-mount');
        this.statusEl = document.getElementById('term-status');
        this.clearBtn = document.getElementById('term-clear-btn');
        this.reconnectBtn = document.getElementById('term-reconnect-btn');

        this.sid = null;
        this.since = 0;
        this.alive = false;
        this.started = false;
        this.pollDelay = 80;
        this.idleStreak = 0;

        this.term = new Terminal({
            cursorBlink: true,
            fontFamily: "'SF Mono', 'Consolas', 'Monaco', monospace",
            fontSize: 13,
            scrollback: 5000,
            theme: {
                background: '#1b2a24',
                foreground: '#c8dfc8',
                cursor: '#a8c686',
                cursorAccent: '#1b2a24',
                selectionBackground: 'rgba(168, 198, 134, 0.35)',
                black: '#1b2a24',
                red: '#e07a5f',
                green: '#7dba7d',
                yellow: '#ddb96a',
                blue: '#6b8eb8',
                magenta: '#b87da6',
                cyan: '#7db8b8',
                white: '#c8dfc8',
                brightBlack: '#4a7358',
                brightRed: '#f4a261',
                brightGreen: '#a8c686',
                brightYellow: '#e9c46a',
                brightBlue: '#87a8c0',
                brightMagenta: '#c89ab8',
                brightCyan: '#9bc8c8',
                brightWhite: '#f0fff0',
            },
        });
        this.fitAddon = new FitAddon.FitAddon();
        this.term.loadAddon(this.fitAddon);
        this.term.open(this.mount);

        this.term.onData(d => this.sendInput(d));

        this.term.attachCustomKeyEventHandler(e => {
            if (e.ctrlKey && e.key === 'w' && e.type === 'keydown') {
                e.preventDefault();
                this.sendInput('\x17');
                return false;
            }
            return true;
        });

        this.resizeObserver = new ResizeObserver(() => this.fitAndResize());
        this.resizeObserver.observe(this.mount);

        this.bindEvents();
    }

    activate() {
        if (!this.started) {
            this.started = true;
            this.start();
        } else {
            this.fitAndResize();
            this.term.focus();
        }
    }

    setStatus(state, label) {
        this.statusEl.textContent = label;
        this.statusEl.classList.remove('connected', 'connecting', 'disconnected');
        this.statusEl.classList.add(state);
    }

    bindEvents() {
        this.clearBtn.addEventListener('click', () => this.term.clear());
        this.reconnectBtn.addEventListener('click', () => this.reconnect());
        window.addEventListener('beforeunload', () => this.closeBeacon());

        const SHORTCUTS = {
            'ctrl-c': '\x03',
            'ctrl-d': '\x04',
            'ctrl-z': '\x1a',
            'ctrl-l': '\x0c',
            'tab':    '\x09',
            'up':     '\x1b[A',
            'down':   '\x1b[B',
        };
        document.querySelectorAll('.shortcut-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const seq = SHORTCUTS[btn.dataset.shortcut];
                if (seq) this.sendInput(seq);
                this.term.focus();
            });
        });
    }

    async start() {
        this.setStatus('connecting', 'connecting…');
        try {
            const res = await fetch('/dev/terminal/start', { method: 'POST' });
            if (!res.ok) {
                const msg = res.status === 429 ? 'session limit reached' : `error ${res.status}`;
                this.setStatus('disconnected', msg);
                this.term.writeln(`\r\n\x1b[31m[failed to start: ${msg}]\x1b[0m`);
                return;
            }
            const data = await res.json();
            this.sid = data.sid;
            this.since = 0;
            this.alive = true;
            this.setStatus('connected', 'connected');
            this.fitAndResize();
            this.term.focus();
            this.poll();
        } catch (e) {
            this.setStatus('disconnected', 'error');
            this.term.writeln(`\r\n\x1b[31m[connection error]\x1b[0m`);
        }
    }

    async sendInput(data) {
        if (!this.alive || !this.sid) return;
        try {
            await fetch(`/dev/terminal/input?sid=${this.sid}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/octet-stream' },
                body: data,
            });
        } catch (_) {}
    }

    async poll() {
        let consecutiveErrors = 0;
        while (this.alive) {
            try {
                const res = await fetch(`/dev/terminal/output?sid=${this.sid}&since=${this.since}`);
                if (res.status === 404) {
                    this.alive = false;
                    this.setStatus('disconnected', 'session lost');
                    this.term.writeln('\r\n\x1b[31m[session ended]\x1b[0m');
                    break;
                }
                if (!res.ok) {
                    // Transient (rate-limit, gateway, etc.) — back off but keep session alive
                    consecutiveErrors++;
                    this.setStatus('connecting', `retrying (${res.status})`);
                    if (consecutiveErrors > 30) {
                        this.alive = false;
                        this.setStatus('disconnected', `error ${res.status}`);
                        this.term.writeln(`\r\n\x1b[31m[server returned ${res.status}, giving up]\x1b[0m`);
                        break;
                    }
                    await new Promise(r => setTimeout(r, Math.min(2000, 200 * consecutiveErrors)));
                    continue;
                }
                consecutiveErrors = 0;
                const data = await res.json();
                if (data.data) {
                    this.term.write(data.data);
                    this.since = data.total;
                    this.idleStreak = 0;
                    this.pollDelay = 80;
                    if (this.statusEl.classList.contains('connecting')) {
                        this.setStatus('connected', 'connected');
                    }
                } else {
                    this.idleStreak++;
                    if (this.idleStreak > 12) this.pollDelay = 400;
                }
                if (!data.alive) {
                    this.alive = false;
                    this.setStatus('disconnected', 'shell exited');
                    this.term.writeln('\r\n\x1b[33m[shell exited]\x1b[0m');
                    break;
                }
            } catch (_) {
                consecutiveErrors++;
                this.setStatus('connecting', 'reconnecting…');
                if (consecutiveErrors > 30) {
                    this.alive = false;
                    this.setStatus('disconnected', 'network error');
                    this.term.writeln('\r\n\x1b[31m[network error, giving up]\x1b[0m');
                    break;
                }
                await new Promise(r => setTimeout(r, Math.min(2000, 200 * consecutiveErrors)));
                continue;
            }
            await new Promise(r => setTimeout(r, this.pollDelay));
        }
    }

    async fitAndResize() {
        if (!this.mount.offsetParent) return;
        try {
            this.fitAddon.fit();
        } catch (_) { return; }
        if (!this.alive || !this.sid) return;
        const { rows, cols } = this.term;
        try {
            await fetch(`/dev/terminal/resize?sid=${this.sid}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rows, cols }),
            });
        } catch (_) {}
    }

    async reconnect() {
        await this.close();
        this.term.clear();
        this.start();
    }

    async close() {
        if (!this.sid) return;
        const sid = this.sid;
        this.alive = false;
        this.sid = null;
        try {
            await fetch(`/dev/terminal/close?sid=${sid}`, { method: 'POST' });
        } catch (_) {}
    }

    closeBeacon() {
        if (!this.sid || !navigator.sendBeacon) return;
        navigator.sendBeacon(`/dev/terminal/close?sid=${this.sid}`, new Blob([], { type: 'text/plain' }));
    }
}


const TAB_IDS = ['logs', 'terminal'];

let _terminalView = null;

function switchTab(tabName) {
    if (!TAB_IDS.includes(tabName)) tabName = 'logs';

    TAB_IDS.forEach(id => {
        const navTab = document.getElementById(`${id}-nav-tab`);
        if (navTab) navTab.classList.toggle('active', id === tabName);
        const pane = document.getElementById(id);
        if (pane) pane.classList.toggle('active', id === tabName);
    });

    if (history.replaceState) {
        history.replaceState(null, '', `#${tabName}`);
    } else {
        window.location.hash = '#' + tabName;
    }

    if (tabName === 'terminal' && _terminalView) {
        _terminalView.activate();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new LogViewer();
    _terminalView = new TerminalView();

    const initial = window.location.hash.replace('#', '') || 'logs';
    switchTab(initial);
});
