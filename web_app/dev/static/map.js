class MapView {
    constructor() {
        this.mapEl = document.getElementById('client-map');
        this.listEl = document.getElementById('map-list');
        this.statusEl = document.getElementById('map-status');
        this.refreshBtn = document.getElementById('map-refresh-btn');
        this.uniqueEl = document.getElementById('map-unique');
        this.locatedEl = document.getElementById('map-located');
        this.requestsEl = document.getElementById('map-requests');
        this.pathInput = document.getElementById('map-path-filter');
        this.pathClear = document.getElementById('map-path-clear');
        this.ipExcludeInput = document.getElementById('map-ip-exclude');
        this.ipClear = document.getElementById('map-ip-clear');
        this.fromInput = document.getElementById('map-from');
        this.toInput = document.getElementById('map-to');
        this.intervalSelect = document.getElementById('map-interval');
        this.chartCanvas = document.getElementById('map-hit-chart');
        this.chartRange = document.getElementById('map-chart-range');
        this.chartTooltip = document.getElementById('map-chart-tooltip');
        this.chartBars = [];
        this.map = null;
        this.layer = null;
        this.loaded = false;

        this.bindEvents();
    }

    bindEvents() {
        this.refreshBtn.addEventListener('click', () => this.load());
        this.pathInput.addEventListener('keydown', e => this.loadOnEnter(e));
        this.pathInput.addEventListener('input', () => {
            this.pathClear.style.display = this.pathInput.value ? 'block' : 'none';
        });
        this.pathClear.addEventListener('click', () => {
            this.pathInput.value = '';
            this.pathClear.style.display = 'none';
            this.load();
        });
        this.ipExcludeInput.addEventListener('keydown', e => this.loadOnEnter(e));
        this.ipExcludeInput.addEventListener('input', () => {
            this.ipClear.style.display = this.ipExcludeInput.value ? 'block' : 'none';
        });
        this.ipClear.addEventListener('click', () => {
            this.ipExcludeInput.value = '';
            this.ipClear.style.display = 'none';
            this.load();
        });
        [this.fromInput, this.toInput, this.intervalSelect].forEach(control => {
            control.addEventListener('change', () => this.load());
        });
        this.chartCanvas.addEventListener('mousemove', e => this.showChartTooltip(e));
        this.chartCanvas.addEventListener('mouseleave', () => this.hideChartTooltip());
    }

    loadOnEnter(e) {
        if (e.key === 'Enter') this.load();
    }

    activate() {
        if (!this.loaded) {
            this.loaded = true;
            this.load();
            return;
        }
        if (this.map) {
            setTimeout(() => this.map.invalidateSize(), 0);
        }
    }

    async load() {
        this.statusEl.textContent = 'loading...';
        this.listEl.innerHTML = '<div class="log-status"><i class="bi bi-hourglass-split"></i> Loading map...</div>';
        try {
            const res = await fetch(this.buildDataUrl());
            if (!res.ok) throw new Error(res.status);
            const data = await res.json();
            this.render(data);
        } catch (_) {
            this.statusEl.textContent = 'failed';
            this.listEl.innerHTML = '<div class="log-status">Failed to load map data.</div>';
        }
    }

    buildDataUrl() {
        const params = new URLSearchParams();
        const pathFilter = this.pathInput.value.trim();
        const excludedIps = this.ipExcludeInput.value.trim();
        if (pathFilter) params.set('path', pathFilter);
        if (excludedIps) params.set('exclude_ips', excludedIps);
        if (this.fromInput.value) params.set('from', this.fromInput.value);
        if (this.toInput.value) params.set('to', this.toInput.value);
        if (this.intervalSelect.value !== 'auto') params.set('interval', this.intervalSelect.value);
        const qs = params.toString();
        return qs ? `/dev/map-data?${qs}` : '/dev/map-data';
    }

    render(data) {
        const points = data.points || [];
        const summary = data.summary || {};
        this.uniqueEl.textContent = this.formatNumber(summary.unique_ips || 0);
        this.locatedEl.textContent = this.formatNumber(summary.located_ips || 0);
        this.requestsEl.textContent = this.formatNumber(summary.request_count || 0);
        this.statusEl.textContent = points.length ? `${points.length} pins` : 'no public locations';
        this.renderChart(data.series || {});

        this.ensureMap();
        this.layer.clearLayers();

        if (!points.length) {
            this.map.setView([20, 0], 2);
            this.listEl.innerHTML = '<div class="log-status">No geolocated public client IPs found.</div>';
            return;
        }

        const bounds = [];
        points.forEach(point => {
            const count = Number(point.count || 0);
            const radius = Math.max(6, Math.min(28, 5 + Math.sqrt(count) * 2.2));
            const marker = L.circleMarker([point.lat, point.lon], {
                radius,
                color: '#2D4A3E',
                weight: 1.5,
                fillColor: '#E07A5F',
                fillOpacity: 0.72,
            }).bindPopup(this.popupHtml(point));
            marker.addTo(this.layer);
            bounds.push([point.lat, point.lon]);
        });

        this.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 5 });
        this.renderList(points, summary);
        setTimeout(() => this.map.invalidateSize(), 0);
    }

    ensureMap() {
        if (this.map) return;
        this.map = L.map(this.mapEl, { worldCopyJump: true }).setView([20, 0], 2);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            attribution: '&copy; OpenStreetMap contributors',
        }).addTo(this.map);
        this.layer = L.layerGroup().addTo(this.map);
    }

    renderList(points, summary) {
        const rows = points
            .slice()
            .sort((a, b) => Number(b.count || 0) - Number(a.count || 0))
            .map(point => {
                const place = [point.city, point.region, point.country].filter(Boolean).join(', ') || 'Unknown';
                return `
                    <button class="map-list-row" data-lat="${point.lat}" data-lon="${point.lon}">
                        <span class="map-list-main">
                            <strong>${this.esc(point.ip)}</strong>
                            <span>${this.esc(place)}</span>
                        </span>
                        <span class="map-list-count">${this.formatNumber(point.count)}</span>
                    </button>`;
            }).join('');
        const limited = summary.limited ? '<div class="map-note">Showing the highest-volume IPs only.</div>' : '';
        this.listEl.innerHTML = `${limited}${rows}`;
        this.listEl.querySelectorAll('.map-list-row').forEach(row => {
            row.addEventListener('click', () => {
                this.map.setView([Number(row.dataset.lat), Number(row.dataset.lon)], 6);
            });
        });
    }

    popupHtml(point) {
        const place = [point.city, point.region, point.country].filter(Boolean).join(', ') || 'Unknown location';
        const network = point.isp ? `<div>${this.esc(point.isp)}</div>` : '';
        const flags = [point.proxy ? 'Proxy' : '', point.hosting ? 'Hosting' : ''].filter(Boolean).join(' / ');
        const flagHtml = flags ? `<div class="map-popup-flags">${this.esc(flags)}</div>` : '';
        return `
            <div class="map-popup">
                <strong>${this.esc(point.ip)}</strong>
                <div>${this.esc(place)}</div>
                ${network}
                <div>${this.formatNumber(point.count)} requests</div>
                ${flagHtml}
            </div>`;
    }

    esc(value) {
        return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    formatNumber(value) {
        return Number(value || 0).toLocaleString();
    }

    renderChart(series) {
        const points = series.points || [];
        const ctx = this.chartCanvas.getContext('2d');
        const rect = this.chartCanvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const width = Math.max(320, Math.floor(rect.width || this.chartCanvas.clientWidth || 320));
        const height = Math.max(120, Math.floor(rect.height || 150));
        this.chartCanvas.width = width * dpr;
        this.chartCanvas.height = height * dpr;
        this.chartBars = [];
        this.hideChartTooltip();
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        if (!points.length) {
            this.chartRange.textContent = 'no hits';
            ctx.fillStyle = '#8A9A8A';
            ctx.font = '13px Nunito, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('No matching hits over time', width / 2, height / 2);
            return;
        }

        const counts = points.map(p => Number(p.count || 0));
        const max = Math.max(1, ...counts);
        const pad = { left: 42, right: 14, top: 14, bottom: 28 };
        const plotW = width - pad.left - pad.right;
        const plotH = height - pad.top - pad.bottom;
        const barGap = points.length > 60 ? 1 : 2;
        const barW = Math.max(2, plotW / points.length - barGap);

        ctx.strokeStyle = 'rgba(135, 168, 120, 0.28)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i <= 3; i++) {
            const y = pad.top + (plotH * i / 3);
            ctx.moveTo(pad.left, y);
            ctx.lineTo(width - pad.right, y);
        }
        ctx.stroke();

        ctx.fillStyle = '#6B8E5A';
        points.forEach((point, index) => {
            const value = Number(point.count || 0);
            const x = pad.left + index * (plotW / points.length) + barGap / 2;
            const h = Math.max(value ? 2 : 0, (value / max) * plotH);
            const y = pad.top + plotH - h;
            ctx.fillRect(x, y, barW, h);
            this.chartBars.push({ x, y, w: barW, h, point, bucket: series.bucket || 'hour' });
        });

        ctx.fillStyle = '#5A6B5A';
        ctx.font = '11px Nunito, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(this.formatNumber(max), pad.left - 8, pad.top + 4);
        ctx.fillText('0', pad.left - 8, pad.top + plotH);

        const first = new Date(points[0].time);
        const last = new Date(points[points.length - 1].time);
        const labelOptions = series.bucket === 'day'
            ? { month: 'short', day: 'numeric' }
            : { month: 'short', day: 'numeric', hour: 'numeric' };
        ctx.textAlign = 'left';
        ctx.fillText(first.toLocaleString([], labelOptions), pad.left, height - 8);
        ctx.textAlign = 'right';
        ctx.fillText(last.toLocaleString([], labelOptions), width - pad.right, height - 8);
        this.chartRange.textContent = `${points.length} ${series.bucket || 'hour'} buckets`;
    }

    showChartTooltip(e) {
        const rect = this.chartCanvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const hit = this.chartBars.find(bar =>
            x >= bar.x && x <= bar.x + bar.w && y >= Math.min(bar.y, bar.y + bar.h) && y <= bar.y + bar.h
        );
        if (!hit) {
            this.hideChartTooltip();
            return;
        }

        const when = new Date(hit.point.time);
        const dateOptions = hit.bucket === 'day'
            ? { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' }
            : { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric' };
        const ipRows = (hit.point.ips || [])
            .slice(0, 8)
            .map(item => `<div><code>${this.esc(item.ip)}</code><span>${this.formatNumber(item.count)}</span></div>`)
            .join('');
        const more = (hit.point.ips || []).length > 8 ? '<div class="map-tooltip-muted">More IPs hidden</div>' : '';
        this.chartTooltip.innerHTML = `
            <strong>${this.esc(when.toLocaleString([], dateOptions))}</strong>
            <div class="map-tooltip-total">${this.formatNumber(hit.point.count)} hits</div>
            <div class="map-tooltip-ips">${ipRows || '<div class="map-tooltip-muted">No IP details</div>'}${more}</div>`;
        this.chartTooltip.style.display = 'block';
        const tooltipW = this.chartTooltip.offsetWidth || 220;
        const tooltipH = this.chartTooltip.offsetHeight || 120;
        const left = Math.min(rect.width - tooltipW - 8, Math.max(8, x + 12));
        const top = Math.min(rect.height - tooltipH - 8, Math.max(8, y - tooltipH - 10));
        this.chartTooltip.style.left = `${left}px`;
        this.chartTooltip.style.top = `${top}px`;
    }

    hideChartTooltip() {
        if (this.chartTooltip) this.chartTooltip.style.display = 'none';
    }
}
