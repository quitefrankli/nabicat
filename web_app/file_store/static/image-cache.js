/* Private image entries share the managed cache so logout and cache clearing remove them. */
class FileStoreImageCache {
    constructor(shell) {
        this.scope = shell.dataset.imageCacheScope;
        this.maxEntryBytes = Number(shell.dataset.imageCacheMaxEntryBytes);
        this.maxEntries = Number(shell.dataset.imageCacheMaxEntries);
        this.pending = new Map();
        this.targets = new WeakMap();
        this.objectUrls = new WeakMap();
        this.previewActive = false;
        this.thumbnailController = null;
        this.thumbnailWaiters = new Set();
        this.storage = Promise.resolve()
            .then(() => 'caches' in window ? caches.open(shell.dataset.imageCacheName) : null)
            .catch(() => null);
    }

    key(source) {
        const url = new URL(source, window.location.href);
        url.searchParams.delete('retry');
        url.searchParams.delete('_ts');
        return url.href;
    }

    owns(source) {
        const url = new URL(source, window.location.href);
        return url.origin === location.origin && url.pathname.startsWith('/file_store/') &&
            url.searchParams.get('cache_user') === this.scope && Boolean(url.searchParams.get('v'));
    }

    async read(source) {
        try {
            const cache = await this.storage;
            const response = cache && this.owns(source) && await cache.match(this.key(source));
            return response ? await response.blob() : null;
        } catch (_) {
            return null;
        }
    }

    async forget(source) {
        try {
            const cache = await this.storage;
            if (cache) await cache.delete(this.key(source));
        } catch (_) {
            // Restricted or full storage must not prevent viewing images.
        }
    }

    setPreviewActive(active) {
        this.previewActive = active;
        if (active) this.thumbnailController?.abort();
        else {
            this.thumbnailWaiters.forEach((resume) => resume());
            this.thumbnailWaiters.clear();
        }
    }

    async downloadThumbnail(source) {
        for (;;) {
            if (this.previewActive) {
                await new Promise((resolve) => this.thumbnailWaiters.add(resolve));
                continue;
            }
            const controller = new AbortController();
            this.thumbnailController = controller;
            try {
                return await this.download(source, controller.signal);
            } catch (error) {
                if (!controller.signal.aborted) throw error;
                // Viewer preemption is not a failed thumbnail attempt.
            } finally {
                if (this.thumbnailController === controller) this.thumbnailController = null;
            }
        }
    }

    async download(source, signal) {
        const key = this.key(source);
        if (this.pending.has(key)) return this.pending.get(key);
        const request = (async () => {
            const response = await fetch(source, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal });
            if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/')) {
                throw new Error('Image unavailable');
            }
            return response.blob();
        })();
        this.pending.set(key, request);
        try {
            return await request;
        } finally {
            this.pending.delete(key);
        }
    }

    async store(source, blob) {
        if (!this.owns(source) || blob.size > this.maxEntryBytes) return;
        try {
            const cache = await this.storage;
            if (!cache) return;
            const key = this.key(source);
            // Reinsert replacements at the end to preserve insertion order for eviction.
            await cache.delete(key);
            await cache.put(key, new Response(blob, { headers: { 'Content-Type': blob.type } }));
            const entries = (await cache.keys()).filter((request) => this.owns(request.url));
            await Promise.all(entries.slice(0, Math.max(0, entries.length - this.maxEntries))
                .map((request) => cache.delete(request)));
        } catch (_) {
            // Caching is optional; quota and privacy restrictions fall back to downloads.
        }
    }

    clear(image) {
        this.targets.delete(image);
        const url = this.objectUrls.get(image);
        if (url) URL.revokeObjectURL(url);
        this.objectUrls.delete(image);
        image.removeAttribute('src');
    }

    async display(image, blob) {
        const previous = this.objectUrls.get(image);
        const url = URL.createObjectURL(blob);
        this.objectUrls.set(image, url);
        if (previous) URL.revokeObjectURL(previous);
        image.src = url;
        await image.decode();
    }

    async show(image, source) {
        this.clear(image);
        image.dataset.imageSource = source || '';
        if (!source) return;
        const target = Symbol();
        this.targets.set(image, target);
        try {
            let blob = await this.read(source);
            let cached = Boolean(blob);
            if (!blob) blob = await this.download(source);
            if (this.targets.get(image) !== target) return;
            try {
                await this.display(image, blob);
            } catch (error) {
                if (!cached || this.targets.get(image) !== target) throw error;
                await this.forget(source);
                blob = await this.download(source);
                cached = false;
                if (this.targets.get(image) !== target) return;
                await this.display(image, blob);
            }
            if (!cached) await this.store(source, blob);
        } catch (_) {
            if (this.targets.get(image) === target) {
                await this.forget(source);
                // Retain the browser's standard broken-image/alt-text fallback.
                image.src = source;
            }
        }
    }
}
