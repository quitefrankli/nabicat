document.addEventListener('DOMContentLoaded', () => {
    const modalElement = document.getElementById('filePreviewModal');
    if (!modalElement) return;
    const modal = new bootstrap.Modal(modalElement);
    const title = document.getElementById('filePreviewTitle');
    const status = document.getElementById('filePreviewStatus');
    const content = document.getElementById('filePreviewContent');
    const download = document.getElementById('filePreviewDownload');
    let controller;

    function clearPreview() {
        controller?.abort();
        content.querySelectorAll('audio, video').forEach((media) => {
            media.pause();
            media.removeAttribute('src');
            media.load();
        });
        content.replaceChildren();
    }

    modalElement.addEventListener('hide.bs.modal', clearPreview);
    document.querySelectorAll('.file-preview-button').forEach((button) => {
        if (button.dataset.previewKind === 'pdf') return;
        button.addEventListener('click', async (event) => {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            clearPreview();
            controller = new AbortController();
            const signal = controller.signal;
            const kind = button.dataset.previewKind;
            title.textContent = button.dataset.previewName;
            download.href = button.dataset.downloadUrl;
            status.textContent = 'Loading preview…';
            modal.show();

            if (kind === 'text') {
                try {
                    const response = await fetch(button.href, {signal});
                    if (!response.ok || !response.headers.get('Content-Type')?.startsWith('text/plain')) {
                        throw new Error('Preview unavailable');
                    }
                    const text = await response.text();
                    if (signal.aborted) return;
                    const pre = document.createElement('pre');
                    pre.textContent = text;
                    content.replaceChildren(pre);
                    status.textContent = response.headers.get('X-Preview-Truncated') === 'true'
                        ? 'Showing the beginning of this file. Download it to read the full contents.' : '';
                } catch (error) {
                    if (!signal.aborted) status.textContent = 'Preview unavailable. You can download this file instead.';
                }
                return;
            }

            const media = document.createElement(kind === 'image' ? 'img' : kind);
            media.hidden = true;
            media.addEventListener(kind === 'image' ? 'load' : 'loadedmetadata', () => {
                if (signal.aborted) return;
                media.hidden = false;
                status.textContent = '';
            }, {signal});
            media.addEventListener('error', () => {
                if (signal.aborted) return;
                media.hidden = true;
                status.textContent = 'This file could not be displayed in your browser. You can download it instead.';
            }, {signal});
            if (kind === 'image') {
                media.alt = button.dataset.previewName;
            } else {
                media.controls = true;
                media.preload = 'metadata';
                media.setAttribute('playsinline', '');
                media.setAttribute('aria-label', button.dataset.previewName);
            }
            content.append(media);
            media.src = button.href;
        });
    });
});
