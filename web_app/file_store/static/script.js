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

function setupMoveDialog() {
    const source = document.getElementById('moveSource');
    const destination = document.getElementById('moveDestination');
    const modal = document.getElementById('moveModal');
    document.querySelectorAll('.move-button').forEach((button) => {
        button.addEventListener('click', () => {
            source.value = button.dataset.path;
            destination.value = button.dataset.path;
            bootstrap.Modal.getOrCreateInstance(modal).show();
        });
    });
}

function setupImageModal() {
    const modal = document.getElementById('imageModal');
    if (!modal) return;
    const image = document.getElementById('modalImage');
    const label = document.getElementById('imageModalLabel');
    document.querySelectorAll('.file-grid-preview[data-bs-toggle="modal"]').forEach((preview) => {
        preview.addEventListener('click', () => {
            image.src = preview.dataset.imageUrl;
            image.alt = preview.dataset.imageName;
            label.textContent = preview.dataset.imageName;
        });
    });
    modal.addEventListener('hidden.bs.modal', () => { image.src = ''; });
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
    const loadWithRetries = async (image) => {
        const source = image.dataset.thumbnailSrc;
        for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
            try {
                const suffix = attempt ? `${source.includes('?') ? '&' : '?'}retry=${attempt}&_ts=${Date.now()}` : '';
                await load(image, `${source}${suffix}`);
                return;
            } catch (_) {
                if (attempt < maxRetries) await delay(retryDelayMs);
            }
        }
    };
    const queue = [];
    let processing = false;
    const processQueue = async () => {
        if (processing) return;
        processing = true;
        while (queue.length) {
            await loadWithRetries(queue.shift());
            await delay(staggerMs);
        }
        processing = false;
    };
    const enqueue = (image) => { queue.push(image); processQueue(); };
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
        images.forEach(enqueue);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setupFolderUpload();
    setupMoveDialog();
    setupImageModal();
    setupStaggeredThumbnails();
});
