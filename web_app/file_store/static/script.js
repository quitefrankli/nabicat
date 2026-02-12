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

    fileInput.addEventListener('change', function() {
        const file = this.files[0];
        const maxSize = parseInt(this.dataset.maxSize);
        const maxFormatted = this.dataset.maxFormatted;
        const errorDiv = document.getElementById('fileSizeError');
        const errorText = document.getElementById('fileSizeErrorText');
        const uploadBtn = document.getElementById('uploadBtn');

        if (file && file.size > maxSize) {
            const fileSize = formatFileSize(file.size);
            errorText.textContent = `File size (${fileSize}) exceeds available storage (${maxFormatted})`;
            errorDiv.classList.remove('d-none');
            uploadBtn.disabled = true;
        } else {
            errorDiv.classList.add('d-none');
            uploadBtn.disabled = false;
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
}

function setupAsyncUpload() {
    const uploadForm = document.getElementById('uploadForm');
    if (!uploadForm) return;

    uploadForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const fileInput = document.getElementById('fileInput');
        const file = fileInput.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append('file', file);

        const xhr = new XMLHttpRequest();
        const startTime = Date.now();

        // Show progress UI
        document.getElementById('uploadIcon').classList.add('d-none');
        document.getElementById('uploadProgress').classList.remove('d-none');
        document.getElementById('uploadFileName').textContent = file.name;
        document.getElementById('uploadBtn').disabled = true;
        document.getElementById('cancelBtn').disabled = true;
        fileInput.disabled = true;

        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                const elapsed = (Date.now() - startTime) / 1000;
                const speed = e.loaded / elapsed;
                const remaining = (e.total - e.loaded) / speed;

                document.getElementById('uploadProgressBar').style.width = percent + '%';
                document.getElementById('uploadPercent').textContent = percent + '%';
                document.getElementById('uploadStats').textContent =
                    `${formatFileSize(e.loaded)} / ${formatFileSize(e.total)} · ${formatFileSize(speed)}/s · ${Math.ceil(remaining)}s left`;
            }
        });

        xhr.addEventListener('load', function() {
            if (xhr.status >= 200 && xhr.status < 300) {
                window.location.reload();
            } else {
                alert('Upload failed: ' + xhr.statusText);
                resetUploadForm();
            }
        });

        xhr.addEventListener('error', function() {
            alert('Upload failed: Network error');
            resetUploadForm();
        });

        xhr.open('POST', uploadForm.action);
        xhr.send(formData);
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

document.addEventListener('DOMContentLoaded', function() {
    setupFileSizeValidation();
    setupAsyncUpload();
    setupAsyncDownload();
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
