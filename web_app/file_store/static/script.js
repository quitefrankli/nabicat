function formatFileSize(bytes) {
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }
    return bytes.toFixed(1) + ' ' + units[i];
}

function setupFileSizeValidation() {
    const fileInput = document.getElementById('fileInput');
    if (!fileInput) return;

    const validateSelection = () => {
        const files = Array.from(fileInput.files || []);
        const maxSize = parseInt(fileInput.dataset.maxSize, 10);
        const maxFormatted = fileInput.dataset.maxFormatted;
        const errorDiv = document.getElementById('fileSizeError');
        const errorText = document.getElementById('fileSizeErrorText');
        const uploadBtn = document.getElementById('uploadBtn');
        const totalSize = files.reduce((sum, file) => sum + file.size, 0);

        if (files.length > 0 && totalSize > maxSize) {
            errorText.textContent = `Selected files (${formatFileSize(totalSize)}) exceed available storage (${maxFormatted})`;
            errorDiv.classList.remove('d-none');
            uploadBtn.disabled = true;
        } else {
            errorDiv.classList.add('d-none');
            uploadBtn.disabled = false;
        }
    };

    fileInput.addEventListener('change', validateSelection);

    fileInput.addEventListener('dragover', function(e) {
        e.preventDefault();
        fileInput.classList.add('border-primary');
    });

    fileInput.addEventListener('dragleave', function() {
        fileInput.classList.remove('border-primary');
    });

    fileInput.addEventListener('drop', function(e) {
        e.preventDefault();
        fileInput.classList.remove('border-primary');
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            fileInput.files = e.dataTransfer.files;
            validateSelection();
        }
    });
}

function resetUploadForm() {
    document.getElementById('uploadIcon').classList.remove('d-none');
    document.getElementById('uploadProgress').classList.add('d-none');
    document.getElementById('uploadBtn').disabled = false;
    document.getElementById('cancelBtn').disabled = false;
    document.getElementById('fileInput').disabled = false;
    document.getElementById('uploadProgressBar').style.width = '0%';
    document.getElementById('uploadPercent').textContent = '0%';
    document.getElementById('uploadStats').textContent = '';
}

function setupAsyncUpload() {
    const uploadForm = document.getElementById('uploadForm');
    if (!uploadForm) return;

    const UPLOAD_STAGGER_MS = 150;

    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

    uploadForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        const fileInput = document.getElementById('fileInput');
        const files = Array.from(fileInput.files || []);
        if (files.length === 0) return;

        // Show progress UI
        document.getElementById('uploadIcon').classList.add('d-none');
        document.getElementById('uploadProgress').classList.remove('d-none');
        document.getElementById('uploadBtn').disabled = true;
        document.getElementById('cancelBtn').disabled = true;
        fileInput.disabled = true;

        const uploadSingleFile = (file, fileIndex, totalFiles) => new Promise((resolve, reject) => {
            const formData = new FormData();
            formData.append('file', file);

            const xhr = new XMLHttpRequest();
            const startTime = Date.now();

            document.getElementById('uploadFileName').textContent = `${file.name} (${fileIndex + 1}/${totalFiles})`;

            xhr.upload.addEventListener('progress', function(progressEvent) {
                if (progressEvent.lengthComputable) {
                    const percent = Math.round((progressEvent.loaded / progressEvent.total) * 100);
                    const elapsed = Math.max((Date.now() - startTime) / 1000, 0.1);
                    const speed = progressEvent.loaded / elapsed;
                    const remaining = speed > 0 ? (progressEvent.total - progressEvent.loaded) / speed : 0;

                    document.getElementById('uploadProgressBar').style.width = percent + '%';
                    document.getElementById('uploadPercent').textContent = percent + '%';
                    document.getElementById('uploadStats').textContent =
                        `${formatFileSize(progressEvent.loaded)} / ${formatFileSize(progressEvent.total)} · ${formatFileSize(speed)}/s · ${Math.ceil(remaining)}s left`;
                }
            });

            xhr.addEventListener('load', function() {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve();
                } else {
                    reject(new Error(xhr.statusText || `HTTP ${xhr.status}`));
                }
            });

            xhr.addEventListener('error', function() {
                reject(new Error('Network error'));
            });

            xhr.open('POST', uploadForm.action);
            xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            xhr.send(formData);
        });

        try {
            for (let i = 0; i < files.length; i++) {
                await uploadSingleFile(files[i], i, files.length);
                if (i < files.length - 1 && UPLOAD_STAGGER_MS > 0) {
                    await sleep(UPLOAD_STAGGER_MS);
                }
            }
            window.location.reload();
        } catch (error) {
            alert('Upload failed: ' + error.message);
            resetUploadForm();
        }
    });
}

function setupAsyncDownload() {
    document.querySelectorAll('.download-btn').forEach(btn => {
        btn.addEventListener('click', async function() {
            const url = this.dataset.url;
            const filename = this.dataset.filename;
            const expectedSize = parseInt(this.dataset.size);

            const overlay = document.getElementById('downloadOverlay');
            const progressBar = document.getElementById('downloadProgressBar');
            const percentEl = document.getElementById('downloadPercent');
            const statsEl = document.getElementById('downloadStats');
            const filenameEl = document.getElementById('downloadFileName');

            overlay.classList.remove('d-none');
            filenameEl.textContent = filename;
            progressBar.style.width = '0%';
            percentEl.textContent = '0%';

            const startTime = Date.now();

            try {
                const response = await fetch(url);
                if (!response.ok) throw new Error('Download failed');

                const reader = response.body.getReader();
                const chunks = [];
                let received = 0;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    chunks.push(value);
                    received += value.length;

                    const percent = Math.round((received / expectedSize) * 100);
                    const elapsed = (Date.now() - startTime) / 1000;
                    const speed = received / elapsed;

                    progressBar.style.width = Math.min(percent, 100) + '%';
                    percentEl.textContent = Math.min(percent, 100) + '%';
                    statsEl.textContent = `${formatFileSize(received)} / ${formatFileSize(expectedSize)} · ${formatFileSize(speed)}/s`;
                }

                const blob = new Blob(chunks);
                const blobUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = blobUrl;
                a.download = filename;
                a.click();
                URL.revokeObjectURL(blobUrl);
            } catch (err) {
                alert('Download failed: ' + err.message);
            } finally {
                overlay.classList.add('d-none');
            }
        });
    });
}

function setupImageModal() {
    const imageModal = document.getElementById('imageModal');
    if (!imageModal) return;

    // Handle clicks on grid items
    document.querySelectorAll('.file-grid-item[data-bs-toggle="modal"]').forEach(item => {
        item.addEventListener('click', function() {
            const imageUrl = this.getAttribute('data-image-url');
            const imageName = this.getAttribute('data-image-name');

            const modalImage = document.getElementById('modalImage');
            const modalImageName = document.getElementById('modalImageName');

            modalImage.src = imageUrl;
            modalImage.alt = imageName;
            modalImageName.textContent = imageName;
        });
    });

    // Reset image when modal is hidden
    imageModal.addEventListener('hidden.bs.modal', function() {
        const modalImage = document.getElementById('modalImage');
        modalImage.src = '';
    });
}

function setupStaggeredThumbnails() {
    const THUMBNAIL_STAGGER_MS = 200;
    const THUMBNAIL_MAX_RETRIES = 3;
    const THUMBNAIL_RETRY_DELAY_MS = 1000;
    const thumbnailImages = Array.from(document.querySelectorAll('img[data-thumbnail-src]'));
    if (thumbnailImages.length === 0) return;

    const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

    const loadImageAttempt = (img, url) => new Promise((resolve, reject) => {
        const onLoad = () => {
            img.removeEventListener('load', onLoad);
            img.removeEventListener('error', onError);
            resolve();
        };

        const onError = () => {
            img.removeEventListener('load', onLoad);
            img.removeEventListener('error', onError);
            reject(new Error('thumbnail load failed'));
        };

        img.addEventListener('load', onLoad);
        img.addEventListener('error', onError);
        img.src = url;
    });

    const loadWithRetries = async (img, thumbnailSrc) => {
        for (let attempt = 0; attempt <= THUMBNAIL_MAX_RETRIES; attempt++) {
            const attemptSuffix = attempt > 0
                ? `${thumbnailSrc.includes('?') ? '&' : '?'}retry=${attempt}&_ts=${Date.now()}`
                : '';
            const attemptUrl = `${thumbnailSrc}${attemptSuffix}`;

            try {
                await loadImageAttempt(img, attemptUrl);
                return;
            } catch (_) {
                if (attempt >= THUMBNAIL_MAX_RETRIES) {
                    return;
                }
                await sleep(THUMBNAIL_RETRY_DELAY_MS * (attempt + 1));
            }
        }
    };

    const queue = [];
    const pending = new Set(thumbnailImages);
    let isProcessing = false;

    const processQueue = async () => {
        if (isProcessing) return;
        isProcessing = true;

        while (queue.length > 0) {
            const img = queue.shift();
            if (!img) continue;
            const thumbnailSrc = img.dataset.thumbnailSrc;
            if (!thumbnailSrc) continue;

            await loadWithRetries(img, thumbnailSrc);
            await sleep(THUMBNAIL_STAGGER_MS);
        }

        isProcessing = false;
    };

    const enqueueImage = (img) => {
        if (!pending.has(img)) return;
        pending.delete(img);
        queue.push(img);
        processQueue();
    };

    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                if (!entry.isIntersecting) continue;
                observer.unobserve(entry.target);
                enqueueImage(entry.target);
            }
        }, {
            root: null,
            rootMargin: '200px 0px',
            threshold: 0.01,
        });

        thumbnailImages.forEach((img) => observer.observe(img));
    } else {
        thumbnailImages.forEach((img) => enqueueImage(img));
    }
}

document.addEventListener('DOMContentLoaded', function() {
    setupFileSizeValidation();
    setupAsyncUpload();
    setupAsyncDownload();
    setupStaggeredThumbnails();
    setupImageModal();

    function setupFileModal({modalId, listId, searchId, actionType}) {
        const modal = document.getElementById(modalId);
        if (!modal) return;
        const fileListDiv = modal.querySelector('#' + listId);
        const fileSearch = modal.querySelector('#' + searchId);
        let files = [];
        function renderFileList(filter = "") {
            fileListDiv.innerHTML = '';
            files.filter(f => f.toLowerCase().includes(filter.toLowerCase())).forEach(file => {
                const item = document.createElement('div');
                item.className = 'd-flex justify-content-between align-items-center mb-1';
                if (actionType === 'download') {
                    item.innerHTML = `<span>${file}</span> <a href="/file_store/download/${encodeURIComponent(file)}" class="btn btn-success btn-sm">Download</a>`;
                } else if (actionType === 'delete') {
                    item.innerHTML = `<span>${file}</span> <form method="post" action="/file_store/delete/${encodeURIComponent(file)}" style="display:inline;"><button type="submit" class="btn btn-danger btn-sm" onclick="return confirm('Delete this file?');">Delete</button></form>`;
                }
                fileListDiv.appendChild(item);
            });
            if (fileListDiv.innerHTML === '') {
                fileListDiv.innerHTML = '<div class="text-muted">No files found.</div>';
            }
        }
        modal.addEventListener('show.bs.modal', function () {
            fetch('/file_store/files_list').then(r => r.json()).then(data => {
                files = data.files || [];
                renderFileList();
            });
        });
        if (fileSearch) {
            fileSearch.addEventListener('input', function() {
                renderFileList(this.value);
            });
        }
    }
    setupFileModal({
        modalId: 'downloadModal',
        listId: 'fileList',
        searchId: 'fileSearch',
        actionType: 'download'
    });
    setupFileModal({
        modalId: 'deleteModal',
        listId: 'deleteFileList',
        searchId: 'deleteFileSearch',
        actionType: 'delete'
    });
});
