/**
 * Client-side Cache Manager
 * Provides clean API for cache operations across all subpages
 */

class CacheManager {
    constructor() {
        this.swRegistration = null;
        this.isSupported = 'serviceWorker' in navigator && 'caches' in window;
        this.MAX_CACHE_SIZE = 10 * 1024 * 1024 * 1024; // 10GB limit
    }

    /**
     * Initialize service worker and caching
     */
    async init() {
        if (!this.isSupported) {
            console.warn('[Cache] Service Worker not supported');
            return false;
        }

        try {
            this.swRegistration = await navigator.serviceWorker.register('/service-worker.js', {
                scope: '/',
            });

            console.log('[Cache] Service Worker registered');

            // Wait for activation
            if (this.swRegistration.active) {
                return true;
            }

            return new Promise((resolve) => {
                navigator.serviceWorker.addEventListener('controllerchange', () => {
                    console.log('[Cache] Service Worker activated');
                    resolve(true);
                });
            });
        } catch (error) {
            console.error('[Cache] Service Worker registration failed:', error);
            return false;
        }
    }

    /**
     * Check if caching is available
     */
    isAvailable() {
        return this.isSupported && this.swRegistration !== null;
    }

    /**
     * Download file with caching
     * @param {string} url - URL to download
     * @param {string} filename - Filename for saving
     * @param {Function} onProgress - Progress callback (percent, loaded, total, speed)
     * @returns {Promise<Blob>}
     */
    async downloadWithCache(url, filename, onProgress = null) {
        const startTime = Date.now();

        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error('Download failed');

            const reader = response.body.getReader();
            const contentLength = parseInt(response.headers.get('content-length') || '0');
            const chunks = [];
            let received = 0;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                chunks.push(value);
                received += value.length;

                if (onProgress && contentLength > 0) {
                    const percent = Math.round((received / contentLength) * 100);
                    const elapsed = (Date.now() - startTime) / 1000;
                    const speed = received / elapsed;
                    onProgress(percent, received, contentLength, speed);
                }
            }

            const blob = new Blob(chunks);

            // Trigger download
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = filename;
            a.click();
            URL.revokeObjectURL(blobUrl);

            return blob;
        } catch (error) {
            console.error('[Cache] Download failed:', error);
            throw error;
        }
    }

    /**
     * Clear entire cache
     */
    async clearCache() {
        if (!this.isAvailable()) return;

        const channel = new MessageChannel();
        const response = await this.sendMessage({ action: 'clearCache' }, channel);
        console.log('[Cache] Cache cleared');
        return response;
    }

    /**
     * Remove specific URL from cache
     */
    async removeFromCache(url) {
        if (!this.isAvailable()) return;

        const channel = new MessageChannel();
        return this.sendMessage({ action: 'removeFromCache', url }, channel);
    }

    /**
     * Get cache size information
     */
    async getCacheInfo() {
        if (!this.isAvailable()) {
            return { usage: 0, quota: 0, available: 0, maxSize: this.MAX_CACHE_SIZE };
        }

        const channel = new MessageChannel();
        const response = await this.sendMessage({ action: 'getCacheSize' }, channel);

        return {
            usage: response.usage || 0,
            quota: response.quota || 0,
            available: (response.quota || 0) - (response.usage || 0),
            maxSize: this.MAX_CACHE_SIZE,
        };
    }

    /**
     * Send message to service worker
     */
    sendMessage(message, channel) {
        return new Promise((resolve, reject) => {
            channel.port1.onmessage = (event) => {
                if (event.data.error) {
                    reject(new Error(event.data.error));
                } else {
                    resolve(event.data);
                }
            };

            if (navigator.serviceWorker.controller) {
                navigator.serviceWorker.controller.postMessage(message, [channel.port2]);
            } else {
                reject(new Error('No service worker controller'));
            }
        });
    }

    /**
     * Format bytes to human readable
     */
    static formatSize(bytes) {
        const units = ['B', 'KB', 'MB', 'GB'];
        let i = 0;
        while (bytes >= 1024 && i < units.length - 1) {
            bytes /= 1024;
            i++;
        }
        return bytes.toFixed(1) + ' ' + units[i];
    }
}

// Global instance
window.cacheManager = new CacheManager();

// Auto-initialize on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.cacheManager.init();
    });
} else {
    window.cacheManager.init();
}
