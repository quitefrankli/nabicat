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
        if (info.quota === 0) return; // Not supported

        const maxSize = 10 * 1024 * 1024 * 1024; // 10GB
        const usagePercent = (info.usage / maxSize) * 100;

        // Determine progress bar color based on usage
        const progressBar = document.getElementById('cacheBarFill');
        progressBar.classList.remove('bg-success', 'bg-warning', 'bg-danger');
        if (usagePercent < 50) {
            progressBar.classList.add('bg-success');
        } else if (usagePercent < 80) {
            progressBar.classList.add('bg-warning');
        } else {
            progressBar.classList.add('bg-danger');
        }

        cacheCard.style.display = 'block';
        document.getElementById('cacheUsageText').textContent =
            `${formatFileSize(info.usage)} / ${formatFileSize(maxSize)}`;
        progressBar.style.width = `${Math.min(usagePercent, 100)}%`;
        progressBar.setAttribute('aria-valuenow', Math.min(usagePercent, 100));
        document.getElementById('cachePercentage').textContent =
            `${usagePercent.toFixed(1)}% used`;
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
            await updateCacheInfo();
            alert('Cache cleared successfully!');
        } catch (error) {
            alert('Failed to clear cache: ' + error.message);
        }
    });
}

// Auto-initialize when cache manager is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        setupCacheClear();
        // Wait for cache manager to initialize
        setTimeout(updateCacheInfo, 1000);
    });
} else {
    setupCacheClear();
    setTimeout(updateCacheInfo, 1000);
}
