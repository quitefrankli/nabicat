class LogViewer {
    constructor() {
        this.lines = [];
        this.nextSince = null;
        this.followMode = false;
        this.followTimer = null;
        this.activeLevel = 'ALL';
        this.searchQuery = '';

        this.output = document.getElementById('log-output');
        this.searchInput = document.getElementById('log-search');
        this.searchClear = document.getElementById('search-clear');
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
        return lines.filter(line => {
            if (this.activeLevel !== 'ALL' && !this.lineMatchesLevel(line, this.activeLevel)) return false;
            if (q && !line.toLowerCase().includes(q)) return false;
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

document.addEventListener('DOMContentLoaded', () => new LogViewer());
