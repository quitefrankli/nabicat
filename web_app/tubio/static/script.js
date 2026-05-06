function switchTab(tabName) {
    // Remove active class from all navbar tabs
    document.querySelectorAll('#search-nav-tab, #playlists-nav-tab').forEach(tab => {
        tab.classList.remove('active');
    });

    // Add active class to clicked tab
    const navTab = document.getElementById(tabName + '-nav-tab');
    if (navTab) {
        navTab.classList.add('active');
    }

    // Hide all tab content
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('show', 'active');
    });

    // Show the selected tab content
    const targetPane = document.getElementById(tabName);
    if (targetPane) {
        targetPane.classList.add('show', 'active');
    }

    // Search tab has no contextual actions — hide the Actions dropdown entirely.
    const actionsDropdown = document.querySelector('.actions-dropdown');
    const actionsContainer = actionsDropdown ? actionsDropdown.closest('.dropdown') : null;
    if (actionsContainer) {
        actionsContainer.classList.toggle('d-none', tabName === 'search');
    }

    // Update URL hash
    window.location.hash = '#' + tabName;
}

function displaySearchResults(data) {
    const resultsDiv = document.getElementById('search-results');
    if (!resultsDiv) return;

    const results = Array.isArray(data) ? data : (data && data.results) || [];
    const page = (data && typeof data.page === 'number') ? data.page : 0;
    const totalPages = (data && typeof data.total_pages === 'number') ? data.total_pages : 1;

    if (!results || results.length === 0) {
        resultsDiv.innerHTML = '<div class="text-center py-5"><h5 class="text-muted">No results found</h5></div>';
        return;
    }

    let html = '<div class="accordion" id="searchResultsAccordion">';

    results.forEach((video, index) => {
        const isDisabled = video.cached ? 'disabled style="background-color: #adb5bd; border-color: #adb5bd;"' : '';
        const safeTitle = escapeHtml(video.title);
        const truncatedTitle = video.title.length > 60 ? escapeHtml(video.title.substring(0, 60) + '...') : safeTitle;
        const safeDesc = escapeHtml(video.description);
        const safeViews = escapeHtml(String(video.view_count));
        const safePublished = escapeHtml(String(video.published));
        const safeLength = escapeHtml(String(video.length));
        const safeVideoId = escapeHtml(video.video_id);
        const safeThumbnail = video.thumbnail_url ? escapeHtml(video.thumbnail_url) : '';

        html += `
            <div class="accordion-item mb-3 border-0 shadow-sm">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed bg-gradient text-primary fw-semibold search-result-btn"
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#collapse-search-${index}"
                            aria-expanded="false"
                            aria-controls="collapse-search-${index}"
                            style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);">
                        <i class="bi bi-youtube me-2 flex-shrink-0"></i>
                        <span class="search-result-title">${truncatedTitle}</span>
                        <small class="badge bg-secondary ms-2 flex-shrink-0">${safeLength}</small>
                    </button>
                </h2>
                <div id="collapse-search-${index}"
                     class="accordion-collapse collapse"
                     data-bs-parent="#searchResultsAccordion">
                    <div class="accordion-body bg-light">
                        <div class="row">
                            <div class="col-md-4 mb-3 text-center">
                                ${safeThumbnail ? `<img src="${safeThumbnail}" alt="Thumbnail" class="img-fluid rounded shadow-sm" style="max-height: 180px;">` : ''}
                            </div>
                            <div class="col-md-8">
                                <div class="mb-3">
                                    <h6 class="text-primary mb-2">Full Title:</h6>
                                    <p class="text-dark fw-medium">${safeTitle}</p>
                                </div>
                                <div class="row">
                                    <div class="col-md-12 mb-3">
                                        <h6 class="text-primary mb-2">Description:</h6>
                                        <p class="text-muted small" style="max-height: 100px; overflow-y: auto;">${safeDesc}</p>
                                    </div>
                                </div>
                                <div class="row">
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Views:</h6>
                                        <p class="text-dark">${safeViews}</p>
                                    </div>
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Published:</h6>
                                        <p class="text-dark">${safePublished}</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="text-center mt-3">
                            <button onclick="downloadVideo('${safeVideoId}', '${safeTitle.replace(/'/g, "\\'")}', this)"
                                    class="btn btn-primary" ${isDisabled}>
                                <i class="bi bi-download me-1"></i>Add To Favourites
                            </button>
                            <div class="progress mt-2 d-none" style="height: 20px;">
                                <div class="progress-bar progress-bar-striped progress-bar-animated"
                                     role="progressbar" style="width: 0%;" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100">0%</div>
                            </div>
                            <small class="text-muted d-none"></small>
                        </div>
                    </div>
                </div>
            </div>
        `;
    });
    
    html += '</div>';

    if (totalPages > 1) {
        let buttons = '';
        for (let i = 0; i < totalPages; i++) {
            const isActive = i === page;
            buttons += `
                <button type="button"
                        class="btn ${isActive ? 'btn-primary' : 'btn-outline-primary'}"
                        onclick="searchPage(${i})"
                        ${isActive ? 'disabled' : ''}>
                    ${i + 1}
                </button>
            `;
        }
        html += `
            <div class="d-flex justify-content-center gap-2 mt-3 mb-4">
                ${buttons}
            </div>
        `;
    }

    resultsDiv.innerHTML = html;
}

async function searchPage(page) {
    const queryInput = document.getElementById('youtube-query');
    if (!queryInput) return;
    const query = queryInput.value;
    if (!query) return;

    const resultsDiv = document.getElementById('search-results');
    try {
        const formData = new FormData();
        formData.append('youtube_query', query);
        formData.append('page', String(page));
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrfToken) formData.append('csrf_token', csrfToken);

        const response = await fetch('/tubio/search', {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        const data = await response.json();
        if (response.ok) {
            displaySearchResults(data);
            if (resultsDiv) resultsDiv.scrollIntoView({ behavior: 'smooth', block: 'start' });
        } else if (resultsDiv) {
            const errorMsg = data.error || 'Search failed. Please try again.';
            resultsDiv.innerHTML = `<div class="text-center py-5"><h5 class="text-danger">${escapeHtml(errorMsg)}</h5></div>`;
        }
    } catch (error) {
        console.error('Error during search:', error);
        if (resultsDiv) {
            resultsDiv.innerHTML = '<div class="text-center py-5"><h5 class="text-danger">Error occurred while searching.</h5></div>';
        }
    }
}

// Initialize everything when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Initialize navbar tab state from URL hash
    const hash = window.location.hash.replace('#', '') || 'playlists';
    switchTab(hash);

    // Setup search form handler if it exists
    const searchForm = document.getElementById('search-form');
    if (searchForm) {
        searchForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const query = document.getElementById('youtube-query').value;
            
            try {
                const formData = new FormData();
                formData.append('youtube_query', query);
                formData.append('page', '0');
                const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
                if (csrfToken) formData.append('csrf_token', csrfToken);

                const response = await fetch('/tubio/search', {
                    method: 'POST',
                    body: formData,
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                });

                const data = await response.json();
                if (response.ok) {
                    displaySearchResults(data);
                } else {
                    console.error('Search failed:', data.error);
                    const resultsDiv = document.getElementById('search-results');
                    if (resultsDiv) {
                        const errorMsg = data.error || 'Search failed. Please try again.';
                        resultsDiv.innerHTML = `<div class="text-center py-5"><h5 class="text-danger">${escapeHtml(errorMsg)}</h5></div>`;
                    }
                }
            } catch (error) {
                console.error('Error during search:', error);
                const resultsDiv = document.getElementById('search-results');
                if (resultsDiv) {
                    resultsDiv.innerHTML = '<div class="text-center py-5"><h5 class="text-danger">Error occurred while searching.</h5></div>';
                }
            }
        });
    }
});

async function downloadVideo(videoId, title, buttonElement) {
    const originalText = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Starting...';

    // Find progress elements relative to button's parent
    const container = buttonElement.closest('.text-center');
    const progressContainer = container ? container.querySelector('.progress') : null;
    const progressBar = progressContainer ? progressContainer.querySelector('.progress-bar') : null;
    const progressStatus = container ? container.querySelector('small.text-muted') : null;

    if (progressContainer) progressContainer.classList.remove('d-none');
    if (progressStatus) progressStatus.classList.remove('d-none');

    let eventSource = null;

    function updateProgress(percent, status) {
        if (progressBar) {
            progressBar.style.width = `${percent}%`;
            progressBar.setAttribute('aria-valuenow', percent);
            progressBar.textContent = `${Math.round(percent)}%`;
        }
        if (progressStatus) {
            const statusText = status === 'downloading' ? 'Downloading...' :
                               status === 'processing' ? 'Processing audio...' :
                               status === 'complete' ? 'Complete!' : status;
            progressStatus.textContent = statusText;
        }
    }

    function hideProgress() {
        if (progressContainer) progressContainer.classList.add('d-none');
        if (progressStatus) progressStatus.classList.add('d-none');
    }

    try {
        const formData = new FormData();
        formData.append('video_id', videoId);
        formData.append('title', title);
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrfToken) formData.append('csrf_token', csrfToken);

        eventSource = new EventSource(`/tubio/download_progress/${videoId}`);
        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.status === 'not_found' || data.status === 'complete' || data.status === 'error') {
                eventSource.close();
            }
            if (data.percent !== undefined) {
                updateProgress(data.percent, data.status);
            }
        };
        eventSource.onerror = () => eventSource.close();

        const response = await fetch('/tubio/youtube_download', {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        const data = await response.json();
        if (eventSource) eventSource.close();

        if (response.ok && data.success) {
            updateProgress(100, 'complete');
            showNotification(data.message, 'success');
            await updateContent(data);

            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i>Downloaded';
            buttonElement.style.backgroundColor = '#adb5bd';
            buttonElement.style.borderColor = '#adb5bd';
            setTimeout(hideProgress, 1500);
        } else {
            throw new Error(data.error || 'Download failed');
        }

    } catch (error) {
        if (eventSource) eventSource.close();
        console.error('Error downloading video:', error);
        showNotification(error.message || 'Error downloading video', 'error');
        hideProgress();
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalText;
    }
}

async function updateContent(data) {
    // Instead of rendering HTML in JavaScript, fetch server-rendered HTML
    try {
        const response = await fetch('/tubio/', {
            method: 'GET',
            headers: {
                'Accept': 'text/html',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        if (response.ok) {
            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            const newPlaylistsContent = doc.getElementById('playlists');
            const currentPlaylistsTab = document.getElementById('playlists');

            if (newPlaylistsContent && currentPlaylistsTab) {
                currentPlaylistsTab.innerHTML = newPlaylistsContent.innerHTML;

                // Audio elements were destroyed; clear playback state
                currentTrackCrc = null;
                isPlayingPlaylist = false;
                currentPlaylistQueue = [];
                currentPlaylistIndex = 0;

                initializeAudioEventListeners();
                initializeLazyThumbnails();
                initializeTooltips();
                initializeSidebar();
                updateTrackbar(null);
                updateTrackbarScrubber();
            }
        }
    } catch (error) {
        console.error('Error updating content:', error);
        window.location.reload();
    }
}

// Sidebar / panel selection
function isMobileViewport() {
    return window.matchMedia('(max-width: 768px)').matches;
}

function setSidebarCollapsed(collapsed) {
    const layout = document.querySelector('.tubio-layout');
    if (!layout) return;
    layout.classList.toggle('sidebar-collapsed', collapsed);
    try {
        sessionStorage.setItem('tubioSidebarCollapsed', collapsed ? '1' : '0');
    } catch (e) {}
}

function toggleSidebar() {
    const layout = document.querySelector('.tubio-layout');
    if (!layout) return;
    setSidebarCollapsed(!layout.classList.contains('sidebar-collapsed'));
}

function closeSidebar() {
    setSidebarCollapsed(true);
}

function selectPlaylist(slug) {
    const sidebar = document.getElementById('playlist-sidebar-list');
    if (sidebar) {
        sidebar.querySelectorAll('.sidebar-item').forEach(item => {
            item.classList.toggle('active', item.dataset.playlistSlug === slug);
        });
    }
    document.querySelectorAll('.playlist-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `panel-${slug}`);
    });
    const emptyPanel = document.getElementById('panel-empty');
    if (emptyPanel) emptyPanel.classList.add('hidden');

    try { sessionStorage.setItem('tubioSelectedPlaylist', slug); } catch (e) {}

    if (isMobileViewport()) closeSidebar();
}

function initializeSidebar() {
    const layout = document.querySelector('.tubio-layout');
    if (layout) {
        let collapsed;
        try {
            const saved = sessionStorage.getItem('tubioSidebarCollapsed');
            collapsed = saved === null ? isMobileViewport() : saved === '1';
        } catch (e) {
            collapsed = isMobileViewport();
        }
        layout.classList.toggle('sidebar-collapsed', collapsed);
    }

    const sidebar = document.getElementById('playlist-sidebar-list');
    if (!sidebar) return;

    const items = sidebar.querySelectorAll('.sidebar-item[data-playlist-slug]');
    if (items.length === 0) return;

    let target = null;
    try {
        const saved = sessionStorage.getItem('tubioSelectedPlaylist');
        if (saved) {
            target = sidebar.querySelector(`.sidebar-item[data-playlist-slug="${CSS.escape(saved)}"]`);
        }
    } catch (e) {}

    if (!target) target = items[0];

    // selectPlaylist may auto-close the sidebar on mobile; preserve the prior collapsed
    // state by skipping autoclose during init.
    const wasMobile = isMobileViewport();
    const layoutEl = document.querySelector('.tubio-layout');
    const wasCollapsed = layoutEl && layoutEl.classList.contains('sidebar-collapsed');
    selectPlaylist(target.dataset.playlistSlug);
    if (wasMobile && layoutEl) {
        layoutEl.classList.toggle('sidebar-collapsed', wasCollapsed);
    }
}

function updateSidebarCounts() {
    const sidebar = document.getElementById('playlist-sidebar-list');
    if (!sidebar) return;
    sidebar.querySelectorAll('.sidebar-item[data-playlist-slug]').forEach(item => {
        const slug = item.dataset.playlistSlug;
        const panel = document.getElementById(`panel-${slug}`);
        if (!panel) return;
        const count = panel.querySelectorAll('.accordion-item[data-audio-crc]').length;
        const meta = item.querySelector('.sidebar-item-meta');
        if (meta) meta.textContent = `${count} song${count === 1 ? '' : 's'}`;
        const headerBadge = panel.querySelector('.playlist-panel-title .badge');
        if (headerBadge) headerBadge.textContent = `${count} songs`;
    });
}

async function removeTrack(crc, buttonElement) {
    if (!confirm('Remove this track from your playlists?')) return;

    const originalHTML = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Removing...';

    const tooltip = bootstrap.Tooltip.getInstance(buttonElement);
    if (tooltip) tooltip.dispose();

    try {
        // Stop audio if currently playing this track
        const audio = document.getElementById(`audio-${crc}`);
        if (audio && !audio.paused) audio.pause();

        const formData = new FormData();
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrfToken) formData.append('csrf_token', csrfToken);

        const response = await fetch(`/tubio/delete_audio/${crc}`, {
            method: 'POST',
            body: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });

        if (!response.ok) throw new Error('Failed to remove track');

        document.querySelectorAll(`.accordion-item[data-audio-crc="${crc}"]`).forEach(el => el.remove());
        if (String(currentTrackCrc) === String(crc)) {
            currentTrackCrc = null;
            isPlayingPlaylist = false;
            updateTrackbar(null);
            updateTrackbarScrubber();
        }
        updateSidebarCounts();
        showNotification('Track removed', 'success');
    } catch (error) {
        console.error('Error removing track:', error);
        showNotification(error.message || 'Error removing track', 'error');
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalHTML;
    }
}

// Individual track playback controls
function togglePlayTrack(crc) {
    const audioElement = document.getElementById(`audio-${crc}`);
    const playButton = document.getElementById(`play-btn-${crc}`);
    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);

    if (!audioElement || !playButton) {
        console.error(`Audio element or button not found for crc: ${crc}`);
        return;
    }

    // Get the playlist name from the track's parent accordion
    const playlistName = trackItem ? trackItem.dataset.playlist : '';

    currentTrackCrc = crc;

    if (audioElement.paused) {
        // Pause all other audio elements and reset their buttons
        document.querySelectorAll('audio').forEach(audio => {
            if (audio.id !== `audio-${crc}` && !audio.paused) {
                audio.pause();
                // Reset the other play button
                const otherCrc = audio.id.replace('audio-', '');
                const otherButton = document.getElementById(`play-btn-${otherCrc}`);
                if (otherButton) {
                    otherButton.innerHTML = '<i class="bi bi-play-fill"></i>';
                    otherButton.classList.remove('btn-success');
                    otherButton.classList.add('btn-outline-primary');
                }
            }
        });

        // Update current playlist name for loop mode
        if (playlistName) {
            currentPlaylistName = playlistName;
        }

        // Load audio if not loaded, then play
        if (audioElement.readyState === 0) {
            audioElement.load();
        }
        audioElement.play().catch(err => {
            console.error('Error playing audio:', err);
            showNotification('Error playing audio. Please try again.', 'error');
        });

        // Update button to show pause icon
        playButton.innerHTML = '<i class="bi bi-pause-fill"></i>';
        playButton.classList.remove('btn-outline-primary');
        playButton.classList.add('btn-success');
    } else {
        // Pause this track
        audioElement.pause();

        // Update button to show play icon
        playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
        playButton.classList.remove('btn-success');
        playButton.classList.add('btn-outline-primary');
    }

    // Add event listener to reset button when track ends naturally
    audioElement.onended = function() {
        // Get loop mode for current playlist
        const loopMode = globalLoopMode;

        // Handle single track looping for individual track play
        if (loopMode === 'single') {
            audioElement.currentTime = 0;
            audioElement.play().catch(err => console.error('Error replaying audio:', err));
            return;
        }

        playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
        playButton.classList.remove('btn-success');
        playButton.classList.add('btn-outline-primary');
    };
}

// Global playback state — controlled from the bottom trackbar
let globalShuffle = false;
let globalLoopMode = 'off'; // 'off' | 'playlist' | 'single'

function toggleShuffle() {
    globalShuffle = !globalShuffle;
    const btn = document.getElementById('trackbar-shuffle');
    if (btn) {
        btn.classList.toggle('active', globalShuffle);
        btn.title = globalShuffle ? 'Shuffle: On' : 'Shuffle: Off';
    }

    // If a playlist is currently playing, reshuffle the upcoming queue
    if (isPlayingPlaylist && currentPlaylistQueue.length > 1) {
        const played = currentPlaylistQueue.slice(0, currentPlaylistIndex + 1);
        const upcoming = currentPlaylistQueue.slice(currentPlaylistIndex + 1);
        currentPlaylistQueue = played.concat(globalShuffle ? shuffleArray(upcoming) : upcoming);
    }
}

// Fisher-Yates shuffle algorithm
function shuffleArray(array) {
    const shuffled = [...array];
    for (let i = shuffled.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
}

function cycleLoopMode() {
    const order = ['off', 'playlist', 'single'];
    globalLoopMode = order[(order.indexOf(globalLoopMode) + 1) % order.length];

    const btn = document.getElementById('trackbar-loop');
    if (!btn) return;
    btn.classList.remove('active');
    if (globalLoopMode === 'off') {
        btn.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
        btn.title = 'Loop: Off';
    } else if (globalLoopMode === 'playlist') {
        btn.classList.add('active');
        btn.innerHTML = '<i class="bi bi-arrow-repeat"></i><small>All</small>';
        btn.title = 'Loop: Playlist';
    } else {
        btn.classList.add('active');
        btn.innerHTML = '<i class="bi bi-arrow-repeat"></i><small>1</small>';
        btn.title = 'Loop: Single';
    }
}

function formatTime(seconds) {
    if (isNaN(seconds) || !isFinite(seconds)) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function seekTrack(crc, value) {
    const audio = document.getElementById(`audio-${crc}`);
    if (!audio) return;

    if (audio.readyState === 0) {
        audio.load();
        audio.addEventListener('loadedmetadata', function onMeta() {
            audio.removeEventListener('loadedmetadata', onMeta);
            audio.currentTime = (value / 100) * audio.duration;
        }, { once: true });
    } else if (audio.duration && isFinite(audio.duration)) {
        audio.currentTime = (value / 100) * audio.duration;
    }
}

// Currently active track CRC (the last track the user interacted with)
let currentTrackCrc = null;

// Playlist playback functionality
let currentPlaylistQueue = [];
let currentPlaylistIndex = 0;
let isPlayingPlaylist = false;
let currentPlaylistName = '';

function togglePlaylistPlayback(playlistName) {
    // Check if this playlist is currently playing
    if (isPlayingPlaylist && currentPlaylistName === playlistName) {
        // Find the currently playing audio
        const crc = currentPlaylistQueue[currentPlaylistIndex];
        const audioElement = document.getElementById(`audio-${crc}`);

        if (audioElement && !audioElement.paused) {
            // Pause the playlist
            pausePlaylist();
        } else {
            // Resume the playlist
            resumePlaylist();
        }
    } else {
        // Start playing this playlist
        playAllInPlaylist(playlistName);
    }
}

function pausePlaylist() {
    const crc = currentPlaylistQueue[currentPlaylistIndex];
    const audioElement = document.getElementById(`audio-${crc}`);
    const playButton = document.getElementById(`play-btn-${crc}`);

    if (audioElement) {
        audioElement.pause();
    }

    if (playButton) {
        playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
        playButton.classList.remove('btn-success');
        playButton.classList.add('btn-outline-primary');
    }

    // Update playlist play button to show play icon
    updatePlaylistPlayButton(currentPlaylistName, false);
}

function resumePlaylist() {
    const crc = currentPlaylistQueue[currentPlaylistIndex];
    const audioElement = document.getElementById(`audio-${crc}`);
    const playButton = document.getElementById(`play-btn-${crc}`);

    if (audioElement) {
        if (audioElement.readyState === 0) {
            audioElement.load();
        }
        audioElement.play().catch(err => {
            console.error('Error resuming audio:', err);
            showNotification('Error resuming playback', 'error');
        });
    }

    if (playButton) {
        playButton.innerHTML = '<i class="bi bi-pause-fill"></i>';
        playButton.classList.remove('btn-outline-primary');
        playButton.classList.add('btn-success');
    }

    // Update playlist play button to show pause icon
    updatePlaylistPlayButton(currentPlaylistName, true);
}

function updatePlaylistPlayButton(playlistName, isPlaying) {
    const buttonId = `play-all-btn-${playlistName.replace(/ /g, '-').replace(/'/g, '')}`;
    const button = document.getElementById(buttonId);

    if (button) {
        if (isPlaying) {
            button.innerHTML = '<i class="bi bi-pause-fill"></i>';
            button.title = 'Pause';
        } else {
            button.innerHTML = '<i class="bi bi-play-fill"></i>';
            button.title = 'Play All';
        }
    }
}

function resetAllPlaylistPlayButtons() {
    document.querySelectorAll('.btn-play-all').forEach(button => {
        button.innerHTML = '<i class="bi bi-play-fill"></i>';
        button.title = 'Play All';
    });
}

function playAllInPlaylist(playlistName) {
    // Find all audio items in this playlist
    const accordionId = `audioAccordion-${playlistName.replace(/ /g, '-')}`;
    const accordion = document.getElementById(accordionId);
    
    if (!accordion) {
        console.error(`Playlist accordion not found: ${accordionId}`);
        showNotification('Error: Playlist not found', 'error');
        return;
    }
    
    // Get all audio elements in this playlist
    const audioItems = accordion.querySelectorAll('.accordion-item[data-audio-crc]');
    
    if (audioItems.length === 0) {
        showNotification('No songs in this playlist', 'info');
        return;
    }
    
    // Stop any currently playing track first
    document.querySelectorAll('audio').forEach(audio => {
        if (!audio.paused) {
            const crc = audio.id.replace('audio-', '');
            // Use the same system as the play button to stop the track
            const playButton = document.getElementById(`play-btn-${crc}`);
            const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
            
            audio.pause();
            
            if (playButton) {
                playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
                playButton.classList.remove('btn-success');
                playButton.classList.add('btn-outline-primary');
            }
            
            if (trackItem) {
                trackItem.classList.remove('track-playing');
            }
        }
    });
    
    // Build queue of audio CRCs
    currentPlaylistQueue = Array.from(audioItems).map(item => item.dataset.audioCrc);

    if (globalShuffle) {
        currentPlaylistQueue = shuffleArray(currentPlaylistQueue);
    }

    currentPlaylistIndex = 0;
    isPlayingPlaylist = true;
    currentPlaylistName = playlistName;

    // Reset all playlist play buttons, then set this one to pause
    resetAllPlaylistPlayButtons();
    updatePlaylistPlayButton(playlistName, true);

    const shuffleText = globalShuffle ? ' (shuffled)' : '';
    showNotification(`Playing all ${currentPlaylistQueue.length} songs in "${playlistName}"${shuffleText}`, 'success');

    // Start playing first song
    playNextInQueue();
}

function playNextInQueue() {
    if (!isPlayingPlaylist || currentPlaylistIndex >= currentPlaylistQueue.length) {
        // Check if we should loop the playlist
        if (globalLoopMode === 'playlist' && currentPlaylistQueue.length > 0) {
            if (globalShuffle) {
                currentPlaylistQueue = shuffleArray(currentPlaylistQueue);
            }
            currentPlaylistIndex = 0;
            showNotification('Looping playlist from beginning', 'info');
        } else {
            // Playlist finished
            isPlayingPlaylist = false;
            resetAllPlaylistPlayButtons();
            showNotification('Playlist finished', 'info');
            return;
        }
    }
    
    const crc = currentPlaylistQueue[currentPlaylistIndex];
    currentTrackCrc = crc;
    const audioElement = document.getElementById(`audio-${crc}`);
    const playButton = document.getElementById(`play-btn-${crc}`);
    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);

    if (!audioElement) {
        console.error(`Audio element not found: audio-${crc}`);
        currentPlaylistIndex++;
        playNextInQueue();
        return;
    }
    
    // Scroll to the song
    const accordionItem = audioElement.closest('.accordion-item');
    if (accordionItem) {
        accordionItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
    
    // Load audio if not loaded, then play
    if (audioElement.readyState === 0) {
        audioElement.load();
    }
    audioElement.currentTime = 0;
    audioElement.play().catch(err => {
        console.error('Error playing audio:', err);
        currentPlaylistIndex++;
        playNextInQueue();
    });
    
    // Update button to show pause icon (same as togglePlayTrack)
    if (playButton) {
        playButton.innerHTML = '<i class="bi bi-pause-fill"></i>';
        playButton.classList.remove('btn-outline-primary');
        playButton.classList.add('btn-success');
    }
    
    // Highlight this track (same as togglePlayTrack)
    if (trackItem) {
        trackItem.classList.add('track-playing');
    }
    
    // Set up event listener for when song ends
    audioElement.addEventListener('ended', function onEnded() {
        // Remove this event listener
        audioElement.removeEventListener('ended', onEnded);

        // Get loop mode for current playlist
        const loopMode = globalLoopMode;

        // Handle single track looping
        if (loopMode === 'single') {
            audioElement.currentTime = 0;
            audioElement.play().catch(err => console.error('Error replaying audio:', err));
            return;
        }

        // Reset button and highlight
        if (playButton) {
            playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
            playButton.classList.remove('btn-success');
            playButton.classList.add('btn-outline-primary');
        }

        if (trackItem) {
            trackItem.classList.remove('track-playing');
        }

        // Move to next song
        if (isPlayingPlaylist) {
            currentPlaylistIndex++;
            setTimeout(() => playNextInQueue(), 500); // Small delay between songs
        }
    }, { once: true });
}

// Stop playlist playback if user manually interacts with play button
document.addEventListener('click', function(e) {
    // Check if a play button was clicked
    if (e.target.closest('.track-play-btn')) {
        // User manually interacted with a track, stop playlist mode
        isPlayingPlaylist = false;
    }
}, true);

function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `alert alert-${type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info'} alert-dismissible fade show position-fixed`;
    notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
    notification.innerHTML = `
        ${escapeHtml(message)}
        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
    `;
    
    document.body.appendChild(notification);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.remove();
        }
    }, 5000);
}

// Sync button UI with audio element state
function syncAudioButtonUI(crc) {
    const audioElement = document.getElementById(`audio-${crc}`);
    const playButton = document.getElementById(`play-btn-${crc}`);

    if (!audioElement || !playButton) {
        return;
    }

    if (audioElement.paused) {
        playButton.innerHTML = '<i class="bi bi-play-fill"></i>';
        playButton.classList.remove('btn-success');
        playButton.classList.add('btn-outline-primary');
    } else {
        playButton.innerHTML = '<i class="bi bi-pause-fill"></i>';
        playButton.classList.remove('btn-outline-primary');
        playButton.classList.add('btn-success');
    }
}

// Initialize audio event listeners to sync UI
function initializeAudioEventListeners() {
    document.querySelectorAll('audio').forEach(audio => {
        const crc = audio.id.replace('audio-', '');

        // Remove existing listeners to avoid duplicates
        audio.removeEventListener('play', audio._playHandler);
        audio.removeEventListener('pause', audio._pauseHandler);
        audio.removeEventListener('timeupdate', audio._timeHandler);
        audio.removeEventListener('loadedmetadata', audio._metaHandler);

        // Create and store handlers
        audio._playHandler = () => {
            currentTrackCrc = crc;
            syncAudioButtonUI(crc);
            updateMediaSessionMetadata(crc);
            updateMediaSessionPlaybackState('playing');
            updateTrackbar(crc);
        };
        audio._pauseHandler = () => {
            syncAudioButtonUI(crc);
            updateMediaSessionPlaybackState('paused');
            if (crc === currentTrackCrc) updateTrackbarPlayPauseUI(false);
        };
        audio._timeHandler = () => {
            if (crc === currentTrackCrc) updateTrackbarScrubber();
        };
        audio._metaHandler = () => {
            if (crc === currentTrackCrc) updateTrackbarScrubber();
        };

        // Add event listeners
        audio.addEventListener('play', audio._playHandler);
        audio.addEventListener('pause', audio._pauseHandler);
        audio.addEventListener('timeupdate', audio._timeHandler);
        audio.addEventListener('loadedmetadata', audio._metaHandler);
    });
}

function updateMediaSessionMetadata(crc) {
    if (!('mediaSession' in navigator)) return;
    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
    if (!trackItem) return;

    const title = trackItem.dataset.title || 'Unknown Track';
    const artwork = [];
    if (trackItem.dataset.hasThumbnail === 'true') {
        const thumbnailUrl = `/tubio/thumbnail/${crc}`;
        artwork.push({ src: thumbnailUrl, sizes: '512x512', type: 'image/jpeg' });
    }
    navigator.mediaSession.metadata = new MediaMetadata({ title, artwork });
}

function updateMediaSessionPlaybackState(state) {
    if ('mediaSession' in navigator) {
        navigator.mediaSession.playbackState = state;
    }
}

// Trackbar / playback control entry points
function togglePlayPause() {
    const crc = currentTrackCrc;
    if (!crc) return;
    const audio = document.getElementById(`audio-${crc}`);
    if (!audio) return;
    if (audio.paused) {
        if (audio.readyState === 0) audio.load();
        audio.play().catch(err => console.error('Error playing audio:', err));
    } else {
        audio.pause();
    }
}

function resetTrackPlayingUI(crc) {
    const button = document.getElementById(`play-btn-${crc}`);
    const item = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
    if (button) {
        button.innerHTML = '<i class="bi bi-play-fill"></i>';
        button.classList.remove('btn-success');
        button.classList.add('btn-outline-primary');
    }
    if (item) item.classList.remove('track-playing');
}

function nextTrack() {
    if (!isPlayingPlaylist || currentPlaylistQueue.length === 0) return;
    const oldCrc = currentPlaylistQueue[currentPlaylistIndex];
    const oldAudio = document.getElementById(`audio-${oldCrc}`);
    if (oldAudio) oldAudio.pause();
    resetTrackPlayingUI(oldCrc);
    currentPlaylistIndex++;
    playNextInQueue();
}

function prevTrack() {
    if (isPlayingPlaylist && currentPlaylistQueue.length > 0) {
        const crc = currentPlaylistQueue[currentPlaylistIndex];
        const audio = document.getElementById(`audio-${crc}`);
        if (audio && audio.currentTime > 3) {
            audio.currentTime = 0;
        } else if (currentPlaylistIndex > 0) {
            if (audio) audio.pause();
            resetTrackPlayingUI(crc);
            currentPlaylistIndex--;
            playNextInQueue();
        } else if (audio) {
            audio.currentTime = 0;
        }
    } else {
        const crc = currentTrackCrc;
        if (crc) {
            const audio = document.getElementById(`audio-${crc}`);
            if (audio) audio.currentTime = 0;
        }
    }
}

function updateTrackbar(crc) {
    const trackbar = document.getElementById('tubio-trackbar');
    const titleEl = document.getElementById('trackbar-title');
    const playlistEl = document.getElementById('trackbar-playlist');
    const thumb = document.getElementById('trackbar-thumb');
    const placeholder = document.getElementById('trackbar-thumb-placeholder');

    if (!crc) {
        if (trackbar) trackbar.dataset.active = 'false';
        if (titleEl) titleEl.textContent = 'No track playing';
        if (playlistEl) playlistEl.textContent = '';
        if (thumb) { thumb.hidden = true; thumb.removeAttribute('src'); }
        if (placeholder) placeholder.hidden = false;
        updateTrackbarPlayPauseUI(false);
        return;
    }

    const trackItem = document.querySelector(`.accordion-item[data-audio-crc="${crc}"]`);
    if (!trackItem) return;

    if (trackbar) trackbar.dataset.active = 'true';
    if (titleEl) titleEl.textContent = trackItem.dataset.title || 'Unknown Track';
    if (playlistEl) playlistEl.textContent = trackItem.dataset.playlist || '';

    if (trackItem.dataset.hasThumbnail === 'true' && thumb) {
        const url = `/tubio/thumbnail/${crc}`;
        if (thumb.src !== url) thumb.src = url;
        thumb.hidden = false;
        if (placeholder) placeholder.hidden = true;
    } else {
        if (thumb) { thumb.hidden = true; thumb.removeAttribute('src'); }
        if (placeholder) placeholder.hidden = false;
    }

    const audio = document.getElementById(`audio-${crc}`);
    updateTrackbarPlayPauseUI(audio ? !audio.paused : false);
}

function updateTrackbarPlayPauseUI(isPlaying) {
    const btn = document.getElementById('trackbar-playpause');
    if (!btn) return;
    btn.innerHTML = isPlaying
        ? '<i class="bi bi-pause-fill"></i>'
        : '<i class="bi bi-play-fill"></i>';
    btn.title = isPlaying ? 'Pause' : 'Play';
}

function updateTrackbarScrubber() {
    const crc = currentTrackCrc;
    const range = document.getElementById('trackbar-scrubber');
    const currEl = document.getElementById('trackbar-time-current');
    const durEl = document.getElementById('trackbar-time-duration');
    if (!range) return;

    if (!crc) {
        range.value = 0;
        range.disabled = true;
        if (currEl) currEl.textContent = '0:00';
        if (durEl) durEl.textContent = '0:00';
        return;
    }

    range.disabled = false;
    const audio = document.getElementById(`audio-${crc}`);
    if (!audio) return;

    if (audio.duration && isFinite(audio.duration)) {
        range.value = (audio.currentTime / audio.duration) * 100;
        if (currEl) currEl.textContent = formatTime(audio.currentTime);
        if (durEl) durEl.textContent = formatTime(audio.duration);
    }
}

// Media Session API integration for hardware media keys
function initializeMediaSession() {
    if (!('mediaSession' in navigator)) return;

    navigator.mediaSession.setActionHandler('play', () => {
        const crc = getCurrentlyPlayingTrack();
        if (crc) {
            const audio = document.getElementById(`audio-${crc}`);
            if (audio && audio.paused) {
                audio.play();
                updateMediaSessionPlaybackState('playing');
            }
        }
    });

    navigator.mediaSession.setActionHandler('pause', () => {
        const crc = getCurrentlyPlayingTrack();
        if (crc) {
            const audio = document.getElementById(`audio-${crc}`);
            if (audio && !audio.paused) {
                audio.pause();
                updateMediaSessionPlaybackState('paused');
            }
        }
    });

    navigator.mediaSession.setActionHandler('nexttrack', () => nextTrack());

    navigator.mediaSession.setActionHandler('previoustrack', () => {
        if (isPlayingPlaylist && currentPlaylistQueue.length > 0) {
            prevTrack();
        } else {
            const crc = getCurrentlyPlayingTrack();
            if (crc) {
                const audio = document.getElementById(`audio-${crc}`);
                if (audio) audio.currentTime = 0;
            }
        }
    });
}

function getCurrentlyPlayingTrack() {
    return currentTrackCrc;
}

async function resyncTrack(crc, buttonElement) {
    const originalHTML = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Syncing...';

    // Dispose tooltip so it doesn't linger while disabled
    const tooltip = bootstrap.Tooltip.getInstance(buttonElement);
    if (tooltip) tooltip.dispose();

    try {
        const formData = new FormData();
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrfToken) formData.append('csrf_token', csrfToken);

        const response = await fetch(`/tubio/resync/${crc}`, {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });

        const data = await response.json();
        if (response.ok && data.success) {
            showNotification(data.message, 'success');
            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i>Done';
            // Reload the audio element to pick up the new file
            const audio = document.getElementById(`audio-${crc}`);
            if (audio) {
                audio.load();
            }
            setTimeout(() => {
                buttonElement.disabled = false;
                buttonElement.innerHTML = originalHTML;
                new bootstrap.Tooltip(buttonElement);
            }, 2000);
        } else {
            throw new Error(data.error || 'Resync failed');
        }
    } catch (error) {
        console.error('Error resyncing track:', error);
        showNotification(error.message || 'Error resyncing track', 'error');
        buttonElement.disabled = false;
        buttonElement.innerHTML = originalHTML;
        new bootstrap.Tooltip(buttonElement);
    }
}

function initializeTooltips() {
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
        if (!bootstrap.Tooltip.getInstance(el)) {
            new bootstrap.Tooltip(el);
        }
    });
}

// Lazy load thumbnails and audio metadata when track accordion is expanded
function initializeLazyThumbnails() {
    document.querySelectorAll('.accordion-collapse').forEach(collapse => {
        collapse.addEventListener('show.bs.collapse', function() {
            const lazyImg = this.querySelector('.lazy-thumbnail[data-src]');
            if (lazyImg && !lazyImg.src) {
                lazyImg.src = lazyImg.dataset.src;
            }
            // Load audio metadata for scrubber
            const audio = this.querySelector('audio');
            if (audio && audio.readyState === 0) {
                audio.load();
            }
            // Initialize tooltips within this expanded section
            this.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
                if (!bootstrap.Tooltip.getInstance(el)) {
                    new bootstrap.Tooltip(el);
                }
            });
        }, { once: true });
    });
}

document.addEventListener('DOMContentLoaded', function() {
    initializeMediaSession();
    initializeAudioEventListeners();
    initializeLazyThumbnails();
    initializeTooltips();
    initializeSidebar();
    updateTrackbar(null);
    updateTrackbarScrubber();

    const trackbarScrubber = document.getElementById('trackbar-scrubber');
    if (trackbarScrubber) {
        trackbarScrubber.addEventListener('input', function() {
            if (currentTrackCrc) seekTrack(currentTrackCrc, this.value);
        });
    }
});

// Playlist management functions
function getSelectedSongs() {
    const checkboxes = document.querySelectorAll('.song-checkbox:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

function preparePlaylistModal() {
    const selectedSongs = getSelectedSongs();
    const count = selectedSongs.length;

    const movePlaylistInput = document.getElementById('move_playlist_tracks_crcs');
    if (movePlaylistInput) movePlaylistInput.value = selectedSongs.join(',');

    const movePlaylistBtn = document.querySelector('#move-playlist-form button[type="submit"]');
    if (movePlaylistBtn) movePlaylistBtn.disabled = (count === 0);
}