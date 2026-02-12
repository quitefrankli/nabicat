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
    
    // Update URL hash
    window.location.hash = '#' + tabName;
}

function displaySearchResults(results) {
    const resultsDiv = document.getElementById('search-results');
    if (!resultsDiv) return;
    
    if (!results || results.length === 0) {
        resultsDiv.innerHTML = '<div class="text-center py-5"><h5 class="text-muted">No results found</h5></div>';
        return;
    }
    
    let html = '<div class="accordion" id="searchResultsAccordion">';
    
    results.forEach((video, index) => {
        const isDisabled = video.cached ? 'disabled style="background-color: #adb5bd; border-color: #adb5bd;"' : '';
        const truncatedTitle = video.title.length > 60 ? video.title.substring(0, 60) + '...' : video.title;

        html += `
            <div class="accordion-item mb-3 border-0 shadow-sm">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed bg-gradient text-primary fw-semibold"
                            type="button"
                            data-bs-toggle="collapse"
                            data-bs-target="#collapse-search-${index}"
                            aria-expanded="false"
                            aria-controls="collapse-search-${index}"
                            style="background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);">
                        <i class="bi bi-youtube me-2"></i>
                        <div class="d-flex justify-content-between align-items-center w-100 me-3">
                            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">${truncatedTitle}</span>
                            <small class="badge bg-secondary ms-2">${video.length}</small>
                        </div>
                    </button>
                </h2>
                <div id="collapse-search-${index}"
                     class="accordion-collapse collapse"
                     data-bs-parent="#searchResultsAccordion">
                    <div class="accordion-body bg-light">
                        <div class="row">
                            <div class="col-md-4 mb-3 text-center">
                                ${video.thumbnail_url ? `<img src="${video.thumbnail_url}" alt="Thumbnail" class="img-fluid rounded shadow-sm" style="max-height: 180px;">` : ''}
                            </div>
                            <div class="col-md-8">
                                <div class="mb-3">
                                    <h6 class="text-primary mb-2">Full Title:</h6>
                                    <p class="text-dark fw-medium">${video.title}</p>
                                </div>
                                <div class="row">
                                    <div class="col-md-12 mb-3">
                                        <h6 class="text-primary mb-2">Description:</h6>
                                        <p class="text-muted small" style="max-height: 100px; overflow-y: auto;">${video.description}</p>
                                    </div>
                                </div>
                                <div class="row">
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Views:</h6>
                                        <p class="text-dark">${video.view_count}</p>
                                    </div>
                                    <div class="col-6">
                                        <h6 class="text-primary mb-2">Published:</h6>
                                        <p class="text-dark">${video.published}</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="text-center mt-3">
                            <button onclick="downloadVideo('${video.video_id}', '${video.title.replace(/'/g, "\\'")}', this)"
                                    class="btn btn-primary" ${isDisabled} id="download-btn-${index}">
                                <i class="bi bi-download me-1"></i>Add To Favourites
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    });
    
    html += '</div>';
    resultsDiv.innerHTML = html;
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
                    displaySearchResults(data.results);
                } else {
                    console.error('Search failed:', data.error);
                    const resultsDiv = document.getElementById('search-results');
                    if (resultsDiv) {
                        const errorMsg = data.error || 'Search failed. Please try again.';
                        resultsDiv.innerHTML = `<div class="text-center py-5"><h5 class="text-danger">${errorMsg}</h5></div>`;
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
    buttonElement.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>Downloading...';
    
    try {
        const formData = new FormData();
        formData.append('video_id', videoId);
        formData.append('title', title);
        
        const response = await fetch('/tubio/youtube_download', {
            method: 'POST',
            body: formData,
            headers: {
                'Accept': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            }
        });
        
        const data = await response.json();
        
        if (response.ok && data.success) {
            // Show success message
            showNotification(data.message, 'success');
            
            // Update playlists content
            await updateContent(data);
            
            // Disable the button and show it's cached
            buttonElement.innerHTML = '<i class="bi bi-check-circle me-1"></i>Downloaded';
            buttonElement.style.backgroundColor = '#adb5bd';
            buttonElement.style.borderColor = '#adb5bd';
            
        } else {
            throw new Error(data.error || 'Download failed');
        }
        
    } catch (error) {
        console.error('Error downloading video:', error);
        showNotification(error.message || 'Error downloading video', 'error');
        
        // Reset button
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
            
            // Extract the playlists tab content from the response
            const newPlaylistsContent = doc.getElementById('playlists');
            const currentPlaylistsTab = document.getElementById('playlists');
            
            if (newPlaylistsContent && currentPlaylistsTab) {
                currentPlaylistsTab.innerHTML = newPlaylistsContent.innerHTML;

                // Re-attach collapse event listeners for chevron animation (only for playlist buttons)
                currentPlaylistsTab.querySelectorAll('.playlist-collapse-btn[data-bs-toggle="collapse"]').forEach(button => {
                    const targetId = button.getAttribute('data-bs-target');
                    if (targetId) {
                        const target = document.querySelector(targetId);
                        if (target) {
                            target.addEventListener('shown.bs.collapse', function() {
                                button.setAttribute('aria-expanded', 'true');
                            });
                            target.addEventListener('hidden.bs.collapse', function() {
                                button.setAttribute('aria-expanded', 'false');
                            });
                        }
                    }
                });

                // Re-initialize audio event listeners for newly added tracks
                initializeAudioEventListeners();

                // Re-initialize lazy loading for thumbnails
                initializeLazyThumbnails();
            }
        }
    } catch (error) {
        console.error('Error updating content:', error);
        // Fallback: just reload the page
        window.location.reload();
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

        // Play this track
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
        const loopMode = playlistLoopModes[currentPlaylistName] || 'off';

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

// Loop mode for playlists: 'off', 'playlist', 'single'
let playlistLoopModes = {};

// Shuffle mode for playlists
let playlistShuffleModes = {};

function toggleShuffle(playlistName) {
    const buttonId = `shuffle-toggle-${playlistName.replace(/ /g, '-').replace(/'/g, '')}`;
    const button = document.getElementById(buttonId);

    if (!button) {
        console.error(`Shuffle toggle button not found: ${buttonId}`);
        return;
    }

    const isShuffled = button.dataset.shuffle === 'true';
    const newState = !isShuffled;

    // Store the shuffle mode for this playlist
    playlistShuffleModes[playlistName] = newState;
    button.dataset.shuffle = newState.toString();

    // Update button visual state
    if (newState) {
        button.classList.add('btn-shuffle-active');
        button.title = 'Shuffle: On';
    } else {
        button.classList.remove('btn-shuffle-active');
        button.title = 'Shuffle: Off';
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

function cycleLoopMode(playlistName) {
    const buttonId = `loop-toggle-${playlistName.replace(/ /g, '-').replace(/'/g, '')}`;
    const button = document.getElementById(buttonId);

    if (!button) {
        console.error(`Loop toggle button not found: ${buttonId}`);
        return;
    }

    const currentMode = button.dataset.loopMode || 'off';
    let newMode;

    // Cycle through modes: off -> playlist -> single -> off
    if (currentMode === 'off') {
        newMode = 'playlist';
    } else if (currentMode === 'playlist') {
        newMode = 'single';
    } else {
        newMode = 'off';
    }

    // Store the loop mode for this playlist
    playlistLoopModes[playlistName] = newMode;
    button.dataset.loopMode = newMode;

    // Update button visual state
    updateLoopButtonUI(button, newMode);
}

function updateLoopButtonUI(button, mode) {
    // Reset classes
    button.classList.remove('btn-loop-playlist', 'btn-loop-single');

    if (mode === 'off') {
        button.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
        button.title = 'Loop: Off';
    } else if (mode === 'playlist') {
        button.classList.add('btn-loop-playlist');
        button.innerHTML = '<i class="bi bi-arrow-repeat"></i> <small>All</small>';
        button.title = 'Loop: Playlist (loops all songs)';
    } else if (mode === 'single') {
        button.classList.add('btn-loop-single');
        button.innerHTML = '<i class="bi bi-arrow-repeat"></i> <small>1</small>';
        button.title = 'Loop: Single (loops current song)';
    }
}

// Volume control function
function setVolume(crc, value) {
    const audioElement = document.getElementById(`audio-${crc}`);
    const volumeLabel = document.getElementById(`volume-label-${crc}`);
    
    if (!audioElement) {
        console.error(`Audio element not found for crc: ${crc}`);
        return;
    }
    
    // Set the audio element's volume (0.0 to 1.0)
    audioElement.volume = value / 100;
    
    // Update the label to show the percentage
    if (volumeLabel) {
        volumeLabel.textContent = value + '%';
    }
}

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

    // Shuffle if enabled
    const isShuffled = playlistShuffleModes[playlistName] || false;
    if (isShuffled) {
        currentPlaylistQueue = shuffleArray(currentPlaylistQueue);
    }

    currentPlaylistIndex = 0;
    isPlayingPlaylist = true;
    currentPlaylistName = playlistName;

    // Reset all playlist play buttons, then set this one to pause
    resetAllPlaylistPlayButtons();
    updatePlaylistPlayButton(playlistName, true);

    // Show notification
    const shuffleText = isShuffled ? ' (shuffled)' : '';
    showNotification(`Playing all ${currentPlaylistQueue.length} songs in "${playlistName}"${shuffleText}`, 'success');

    // Start playing first song
    playNextInQueue();
}

function playNextInQueue() {
    // Get loop mode for current playlist
    const loopMode = playlistLoopModes[currentPlaylistName] || 'off';

    if (!isPlayingPlaylist || currentPlaylistIndex >= currentPlaylistQueue.length) {
        // Check if we should loop the playlist
        if (loopMode === 'playlist' && currentPlaylistQueue.length > 0) {
            // Re-shuffle if shuffle is enabled
            const isShuffled = playlistShuffleModes[currentPlaylistName] || false;
            if (isShuffled) {
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
    
    // Play the audio using the same system as the play button
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
        const loopMode = playlistLoopModes[currentPlaylistName] || 'off';

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
        ${message}
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

        // Create and store handlers
        audio._playHandler = () => syncAudioButtonUI(crc);
        audio._pauseHandler = () => syncAudioButtonUI(crc);

        // Add event listeners for play and pause events
        audio.addEventListener('play', audio._playHandler);
        audio.addEventListener('pause', audio._pauseHandler);
    });
}

// Media Session API integration for hardware media keys
function initializeMediaSession() {
    if ('mediaSession' in navigator) {
        // Set up action handlers for hardware media keys
        navigator.mediaSession.setActionHandler('play', () => {
            const currentlyPlaying = getCurrentlyPlayingTrack();
            if (currentlyPlaying) {
                // If there's a paused track, resume it
                const audioElement = document.getElementById(`audio-${currentlyPlaying}`);
                if (audioElement && audioElement.paused) {
                    togglePlayTrack(currentlyPlaying);
                }
            }
        });

        navigator.mediaSession.setActionHandler('pause', () => {
            const currentlyPlaying = getCurrentlyPlayingTrack();
            if (currentlyPlaying) {
                const audioElement = document.getElementById(`audio-${currentlyPlaying}`);
                if (audioElement && !audioElement.paused) {
                    togglePlayTrack(currentlyPlaying);
                }
            }
        });
    }
}

// Helper function to get the currently playing track CRC
function getCurrentlyPlayingTrack() {
    // Find the audio element that is currently playing
    const playingAudio = Array.from(document.querySelectorAll('audio')).find(audio => !audio.paused);
    if (playingAudio) {
        return playingAudio.id.replace('audio-', '');
    }

    // If no audio is playing, find the last paused audio with currentTime > 0
    const pausedWithProgress = Array.from(document.querySelectorAll('audio'))
        .filter(audio => audio.currentTime > 0)
        .sort((a, b) => b.currentTime - a.currentTime)[0];

    if (pausedWithProgress) {
        return pausedWithProgress.id.replace('audio-', '');
    }

    return null;
}

// Lazy load thumbnails when track accordion is expanded
function initializeLazyThumbnails() {
    document.querySelectorAll('.accordion-collapse').forEach(collapse => {
        collapse.addEventListener('show.bs.collapse', function() {
            const lazyImg = this.querySelector('.lazy-thumbnail[data-src]');
            if (lazyImg && !lazyImg.src) {
                lazyImg.src = lazyImg.dataset.src;
            }
        }, { once: true });
    });
}

// Handle playlist collapse chevron rotation
document.addEventListener('DOMContentLoaded', function() {
    // Initialize Media Session API
    initializeMediaSession();

    // Initialize audio event listeners for UI sync
    initializeAudioEventListeners();

    // Initialize lazy loading for thumbnails
    initializeLazyThumbnails();

    // Update chevron rotation when playlist collapse events occur (only for playlist buttons, not track accordions)
    document.querySelectorAll('.playlist-collapse-btn[data-bs-toggle="collapse"]').forEach(button => {
        const targetId = button.getAttribute('data-bs-target');
        if (targetId) {
            const target = document.querySelector(targetId);
            if (target) {
                target.addEventListener('shown.bs.collapse', function() {
                    button.setAttribute('aria-expanded', 'true');
                });
                target.addEventListener('hidden.bs.collapse', function() {
                    button.setAttribute('aria-expanded', 'false');
                });
            }
        }
    });
});

// Playlist management functions
function getSelectedSongs() {
    const checkboxes = document.querySelectorAll('.song-checkbox:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

function preparePlaylistModal() {
    const selectedSongs = getSelectedSongs();
    const count = selectedSongs.length;
    
    // Set hidden inputs
    document.getElementById('move_playlist_tracks_crcs').value = selectedSongs.join(',');
    document.getElementById('delete_song_crcs').value = selectedSongs.join(',');
    
    // Disable submit if no songs selected
    const movePlaylistBtn = document.querySelector('#move-playlist-form button[type="submit"]');
    const deleteBtn = document.querySelector('#delete-selected-songs-form button[type="submit"]');
    
    if (movePlaylistBtn) movePlaylistBtn.disabled = (count === 0);
    if (deleteBtn) deleteBtn.disabled = (count === 0);
}

// Select/deselect all checkboxes in a playlist
function togglePlaylistSelection(playlistName, checked) {
    const accordionId = `audioAccordion-${playlistName.replace(/ /g, '-')}`;
    const accordion = document.getElementById(accordionId);
    if (accordion) {
        const checkboxes = accordion.querySelectorAll('.song-checkbox');
        checkboxes.forEach(cb => cb.checked = checked);
    }
}

// Clear all selections
function clearAllSelections() {
    document.querySelectorAll('.song-checkbox').forEach(cb => cb.checked = false);
}