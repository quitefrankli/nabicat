function joinPath(parent, name) {
    return parent ? `${parent}/${name}` : name;
}

function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function setupFolderUpload() {
    const form = document.getElementById('uploadForm');
    if (!form) return;
    const fileInput = document.getElementById('fileInput');
    const folderInput = document.getElementById('folderInput');
    const archiveInput = document.getElementById('archiveInput');
    const currentPath = document.querySelector('.file-store-shell').dataset.currentPath;
    const supportsFolderPicker = 'webkitdirectory' in folderInput;
    if (!supportsFolderPicker) {
        folderInput.disabled = true;
    }

    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const selectedFiles = Array.from(fileInput.files || []);
        const selectedFolder = Array.from(folderInput.files || []);
        const archive = archiveInput.files?.[0];
        if (!selectedFiles.length && !selectedFolder.length && !archive) return;
        if (archive && (selectedFiles.length || selectedFolder.length)) {
            alert('Upload either files, a folder, or a ZIP archive.');
            return;
        }

        const data = new FormData();
        data.append('csrf_token', csrfToken());
        data.append('base_path', currentPath);
        if (archive) {
            data.append('folder_archive', archive);
        } else {
            const files = selectedFolder.length ? selectedFolder : selectedFiles;
            files.forEach((file) => {
                const relativePath = selectedFolder.length
                    ? joinPath(currentPath, file.webkitRelativePath)
                    : joinPath(currentPath, file.name);
                data.append('file', file, relativePath);
            });
        }

        const progress = document.getElementById('uploadProgress');
        const progressBar = document.getElementById('uploadProgressBar');
        const status = document.getElementById('uploadStatus');
        const button = document.getElementById('uploadBtn');
        progress.classList.remove('d-none');
        button.disabled = true;
        const xhr = new XMLHttpRequest();
        xhr.upload.addEventListener('progress', (update) => {
            if (update.lengthComputable) {
                const percent = Math.round((update.loaded / update.total) * 100);
                progressBar.style.width = `${percent}%`;
                progressBar.textContent = `${percent}%`;
            }
        });
        xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) {
                window.location.reload();
                return;
            }
            status.textContent = xhr.responseJSON?.error || xhr.responseText || 'Upload failed.';
            button.disabled = false;
        });
        xhr.addEventListener('error', () => {
            status.textContent = 'Upload failed. Check your connection and try again.';
            button.disabled = false;
        });
        xhr.open('POST', form.action);
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        xhr.send(data);
    });
}

function setupRenameModal() {
    const modal = document.getElementById('renameModal');
    if (!modal) return;
    const name = document.getElementById('renameName');
    modal.addEventListener('show.bs.modal', (event) => {
        document.getElementById('renameSource').value = event.relatedTarget.dataset.renameSource;
        name.value = event.relatedTarget.dataset.renameName;
    });
    modal.addEventListener('shown.bs.modal', () => {
        name.focus();
        const extension = name.value.lastIndexOf('.');
        name.setSelectionRange(0, extension > 0 ? extension : name.value.length);
    });
}

function setupBulkActions() {
    const selectAll = document.getElementById('selectAllFiles');
    const actions = document.getElementById('fileBulkActions');
    const checkboxes = Array.from(document.querySelectorAll('.file-selection-checkbox'));
    if (!selectAll || !actions || !checkboxes.length) return;

    const count = document.getElementById('selectedFileCount');
    const move = document.getElementById('moveSelectedFiles');
    const remove = document.getElementById('deleteSelectedFiles');
    const selectedPaths = () => checkboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
    const update = () => {
        const selected = selectedPaths();
        actions.hidden = selected.length === 0;
        count.textContent = `${selected.length} selected`;
        selectAll.checked = selected.length === checkboxes.length;
        selectAll.indeterminate = selected.length > 0 && selected.length < checkboxes.length;
    };
    const setPaths = (container, paths) => {
        container.replaceChildren(...paths.map((path) => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'paths';
            input.value = path;
            return input;
        }));
    };

    selectAll.addEventListener('change', () => {
        checkboxes.forEach((checkbox) => { checkbox.checked = selectAll.checked; });
        update();
    });
    checkboxes.forEach((checkbox) => checkbox.addEventListener('change', update));
    move.addEventListener('click', () => {
        setPaths(document.getElementById('bulkMovePaths'), selectedPaths());
        bootstrap.Modal.getOrCreateInstance(document.getElementById('bulkMoveModal')).show();
    });
    remove.addEventListener('click', () => {
        const paths = selectedPaths();
        setPaths(document.getElementById('bulkDeletePaths'), paths);
        document.getElementById('bulkDeleteMessage').textContent = `Permanently delete ${paths.length} selected item(s)? This cannot be undone.`;
        bootstrap.Modal.getOrCreateInstance(document.getElementById('bulkDeleteModal')).show();
    });
    update();
}

function setupImageModal() {
    const modal = document.getElementById('imageModal');
    const shell = document.querySelector('.file-store-shell');
    if (!modal || !shell) return;
    const image = document.getElementById('modalImage');
    const previews = Array.from(document.querySelectorAll('.file-grid-item [data-bs-toggle="modal"]'));
    let activeIndex = -1;

    const showImage = (index) => {
        if (index < 0 || index >= previews.length) return;
        activeIndex = index;
        const preview = previews[activeIndex];
        image.src = preview.dataset.imageUrl;
        image.alt = preview.dataset.imageName;
        gestures.updateNeighbours();
    };
    const gestures = setupMobileImageGestures(modal, shell, previews, () => activeIndex, showImage);
    const moveImage = (offset) => {
        gestures.reset();
        showImage(activeIndex + offset);
    };

    previews.forEach((preview, index) => {
        preview.addEventListener('click', () => showImage(index));
    });
    image.addEventListener('click', (event) => {
        if (gestures.isMobile()) return;
        const imageBounds = image.getBoundingClientRect();
        moveImage(event.clientX < imageBounds.left + imageBounds.width / 2 ? -1 : 1);
    });
    modal.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowLeft') {
            event.preventDefault();
            moveImage(-1);
        } else if (event.key === 'ArrowRight') {
            event.preventDefault();
            moveImage(1);
        }
    });
    modal.addEventListener('hidden.bs.modal', () => {
        activeIndex = -1;
        image.removeAttribute('src');
        gestures.updateNeighbours();
    });
}

function setupMobileImageGestures(modal, shell, previews, getIndex, showImage) {
    const stage = modal.querySelector('.image-preview-stage');
    const neighbours = modal.querySelectorAll('.image-preview-neighbour img');
    const mobile = window.matchMedia(`(max-width: ${shell.dataset.previewMobileMaxWidthPx}px)`);
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const lockDistance = Number(shell.dataset.previewGestureLockPx);
    const commitRatio = Number(shell.dataset.previewGestureCommitRatio);
    const animationMs = Number(shell.dataset.previewGestureAnimationMs);
    const edgeResistance = Number(shell.dataset.previewGestureEdgeResistance);
    const shrinkRatio = Number(shell.dataset.previewGestureShrinkRatio);
    let gesture = null;
    let timer = null;

    const updateNeighbours = () => {
        [getIndex() - 1, getIndex() + 1].forEach((index, slot) => {
            const preview = mobile.matches && getIndex() >= 0 ? previews[index] : null;
            if (preview) neighbours[slot].src = preview.dataset.imageUrl;
            else neighbours[slot].removeAttribute('src');
        });
    };
    const reset = () => {
        clearTimeout(timer);
        timer = null;
        gesture = null;
        stage.classList.remove('is-settling');
        stage.style.removeProperty('--preview-x');
        stage.style.removeProperty('--preview-y');
        stage.style.removeProperty('--preview-scale');
    };
    const settle = (x, y, scale, done = reset) => {
        const duration = reducedMotion.matches ? 0 : animationMs;
        stage.style.setProperty('--preview-duration', `${duration}ms`);
        stage.classList.add('is-settling');
        stage.style.setProperty('--preview-x', `${x}px`);
        stage.style.setProperty('--preview-y', `${y}px`);
        stage.style.setProperty('--preview-scale', scale);
        timer = setTimeout(() => {
            timer = null;
            done();
        }, duration);
    };

    stage.addEventListener('touchstart', (event) => {
        if (!mobile.matches || event.touches.length !== 1 || window.visualViewport?.scale > 1) {
            reset();
            return;
        }
        if (timer !== null) return;
        const touch = event.touches[0];
        gesture = { startX: touch.clientX, startY: touch.clientY, x: 0, y: 0, axis: null };
    }, { passive: true });
    stage.addEventListener('touchmove', (event) => {
        if (!gesture) return;
        if (event.touches.length !== 1) {
            reset();
            return;
        }
        const touch = event.touches[0];
        gesture.x = touch.clientX - gesture.startX;
        gesture.y = touch.clientY - gesture.startY;
        if (!gesture.axis) {
            if (Math.max(Math.abs(gesture.x), Math.abs(gesture.y)) < lockDistance) return;
            gesture.axis = Math.abs(gesture.x) > Math.abs(gesture.y) ? 'x' : 'y';
        }
        // Leave two-finger gestures to the browser for native pinch zoom.
        if (event.cancelable) event.preventDefault();
        if (gesture.axis === 'x') {
            const target = getIndex() + (gesture.x < 0 ? 1 : -1);
            const x = previews[target] ? gesture.x : gesture.x * edgeResistance;
            stage.style.setProperty('--preview-x', `${x}px`);
        } else {
            stage.style.setProperty('--preview-y', `${gesture.y}px`);
            stage.style.setProperty('--preview-scale', 1 - Math.min(Math.abs(gesture.y) / stage.clientHeight, 1) * shrinkRatio);
        }
    }, { passive: false });
    stage.addEventListener('touchend', (event) => {
        if (!gesture) return;
        if (event.touches.length) {
            reset();
            return;
        }
        const { axis, x, y } = gesture;
        gesture = null;
        if (axis === 'x') {
            const target = getIndex() + (x < 0 ? 1 : -1);
            if (previews[target] && Math.abs(x) >= stage.clientWidth * commitRatio) {
                settle(x < 0 ? -stage.clientWidth : stage.clientWidth, 0, 1, () => {
                    reset();
                    showImage(target);
                });
                return;
            }
        } else if (axis === 'y' && Math.abs(y) >= stage.clientHeight * commitRatio) {
            settle(0, y < 0 ? -stage.clientHeight : stage.clientHeight, 1 - shrinkRatio, () => {
                bootstrap.Modal.getOrCreateInstance(modal).hide();
            });
            return;
        }
        settle(0, 0, 1);
    }, { passive: true });
    stage.addEventListener('touchcancel', () => {
        if (gesture) {
            gesture = null;
            settle(0, 0, 1);
        }
    }, { passive: true });
    modal.addEventListener('hide.bs.modal', () => {
        clearTimeout(timer);
        timer = null;
        gesture = null;
    });
    modal.addEventListener('hidden.bs.modal', reset);
    window.addEventListener('resize', () => {
        reset();
        updateNeighbours();
    });
    return { reset, updateNeighbours, isMobile: () => mobile.matches };
}

function setupStaggeredThumbnails() {
    const shell = document.querySelector('.file-store-shell');
    const images = Array.from(document.querySelectorAll('img[data-thumbnail-src]'));
    if (!shell || !images.length) return;

    const staggerMs = Number(shell.dataset.thumbnailStaggerMs);
    const maxRetries = Number(shell.dataset.thumbnailMaxRetries);
    const retryDelayMs = Number(shell.dataset.thumbnailRetryDelayMs);
    const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const load = (image, url) => new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = reject;
        image.src = url;
    });
    const setState = (image, state) => {
        const tile = image.closest('.file-grid-item');
        tile.dataset.thumbnailState = state;
        const status = tile.querySelector('.thumbnail-status');
        status.hidden = state === 'loaded';
        status.querySelector('span').textContent = state === 'failed' ? 'Image unavailable' : 'Loading image…';
        status.querySelector('button').hidden = state !== 'failed';
    };
    const loadWithRetries = async (image, fresh = false) => {
        setState(image, 'loading');
        const source = image.dataset.thumbnailSrc;
        for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
            try {
                const suffix = attempt || fresh ? `${source.includes('?') ? '&' : '?'}retry=${attempt}&_ts=${Date.now()}` : '';
                await load(image, `${source}${suffix}`);
                setState(image, 'loaded');
                return;
            } catch (_) {
                if (attempt < maxRetries) await delay(retryDelayMs);
            }
        }
        setState(image, 'failed');
    };
    const queue = [];
    let processing = false;
    const processQueue = async () => {
        if (processing) return;
        processing = true;
        while (queue.length) {
            const { image, fresh } = queue.shift();
            await loadWithRetries(image, fresh);
            await delay(staggerMs);
        }
        processing = false;
    };
    const enqueue = (image, fresh = false) => { queue.push({ image, fresh }); processQueue(); };
    images.forEach((image) => {
        image.closest('.file-grid-item').querySelector('.thumbnail-retry').addEventListener('click', () => {
            setState(image, 'loading');
            enqueue(image, true);
        });
    });
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                observer.unobserve(entry.target);
                enqueue(entry.target);
            });
        }, { rootMargin: '200px 0px', threshold: 0.01 });
        images.forEach((image) => observer.observe(image));
    } else {
        images.forEach((image) => enqueue(image));
    }
}

function setupGalleryDensity() {
    const shell = document.querySelector('.file-store-shell');
    const gallery = document.querySelector('.file-directory.file-grid');
    const slider = document.getElementById('galleryColumns');
    const value = document.getElementById('galleryColumnsValue');
    if (!shell || !gallery || !slider || !value) return;

    const minColumns = Number(slider.min);
    const maxColumns = Number(slider.max);
    const minTilePx = Number(shell.dataset.galleryMinTilePx);
    let preferredColumns = Number(slider.value);
    const storageKey = shell.dataset.galleryDensityStorageKey;
    try {
        const saved = Number(localStorage.getItem(storageKey));
        if (Number.isInteger(saved) && saved >= minColumns && saved <= maxColumns) {
            preferredColumns = saved;
        }
    } catch (_) {
        // Gallery controls remain usable when browser storage is unavailable.
    }

    const applyColumns = () => {
        const gapPx = Number.parseFloat(getComputedStyle(gallery).gap) || 0;
        const viewportMaximum = Math.floor((gallery.clientWidth + gapPx) / (minTilePx + gapPx));
        const availableMaximum = Math.max(minColumns, Math.min(maxColumns, viewportMaximum));
        const columns = Math.min(preferredColumns, availableMaximum);

        gallery.style.setProperty('--file-grid-columns', columns);
        slider.max = availableMaximum;
        slider.value = columns;
        value.value = `${columns} columns`;
        value.textContent = `${columns} columns`;
        slider.setAttribute('aria-valuetext', `${columns} columns`);
    };

    slider.addEventListener('input', () => {
        preferredColumns = Number(slider.value);
        try {
            localStorage.setItem(storageKey, String(preferredColumns));
        } catch (_) {
            // Storage restrictions should not prevent changing the density.
        }
        applyColumns();
    });
    window.addEventListener('resize', applyColumns);
    applyColumns();
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.storage-fill[data-usage-percent]').forEach((fill) => {
        fill.style.width = `${Number(fill.dataset.usagePercent) || 0}%`;
    });
    setupFolderUpload();
    setupRenameModal();
    setupBulkActions();
    setupImageModal();
    setupStaggeredThumbnails();
    setupGalleryDensity();
});
