/**
 * Unified cache UI component
 * Can be used on any page to display and manage cache
 */

function formatFileSize(bytes) {
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + ' ' + units[i];
}

async function updateCacheInfo() {
    const cacheCard = document.getElementById('cacheInfoCard');
    if (!cacheCard || !window.cacheManager) return;

    try {
        const info = await window.cacheManager.getCacheInfo();
        if (info.quota === 0) return;

        const maxSize = 10 * 1024 * 1024 * 1024; // 10GB
        cacheCard.style.display = 'block';
        document.getElementById('cacheUsageText').textContent =
            `${formatFileSize(info.usage)} / ${formatFileSize(maxSize)}`;
    } catch (error) {
        console.error('Failed to get cache info:', error);
    }
}

function setupCacheClear() {
    const clearBtn = document.getElementById('clearCacheBtn');
    if (!clearBtn) return;

    clearBtn.addEventListener('click', async function(e) {
        e.preventDefault();
        if (!confirm('Clear all browser cache? Files will need to be re-downloaded from the server.')) return;

        try {
            await window.cacheManager.clearCache();
            location.reload();
        } catch (error) {
            alert('Failed to clear cache: ' + error.message);
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupCacheClear);
} else {
    setupCacheClear();
}
window.addEventListener('cacheManagerReady', updateCacheInfo);
