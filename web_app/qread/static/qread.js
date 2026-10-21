/* global jsQR, qrcode */
"use strict";

(() => {
    const root = document.getElementById("qread");
    const video = document.getElementById("camera");
    const start = document.getElementById("start-camera");
    const stop = document.getElementById("stop-camera");
    const photo = document.getElementById("qr-photo");
    const status = document.getElementById("scan-status");
    const result = document.getElementById("qr-result");
    const copy = document.getElementById("copy-result");
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    let stream = null;
    let timer = null;
    let operation = 0;

    function stopCamera() {
        operation += 1;
        clearTimeout(timer);
        if (stream) stream.getTracks().forEach(track => track.stop());
        stream = null;
        video.srcObject = null;
        video.hidden = true;
        start.disabled = false;
        stop.hidden = true;
    }

    function clearResult() {
        result.value = "";
        copy.disabled = true;
    }

    function showResult(code) {
        stopCamera();
        result.value = code.data;
        copy.disabled = false;
        status.textContent = "QR code decoded. Your text is ready to copy.";
    }

    function decode(source, width, height) {
        const scale = Math.min(1, Number(root.dataset.scanMaxDimension) / Math.max(width, height));
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        context.fillStyle = "white";
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(source, 0, 0, canvas.width, canvas.height);
        const image = context.getImageData(0, 0, canvas.width, canvas.height);
        return jsQR(image.data, image.width, image.height, { inversionAttempts: "attemptBoth" });
    }

    start.addEventListener("click", async () => {
        stopCamera();
        clearResult();
        if (!navigator.mediaDevices?.getUserMedia) {
            status.textContent = "Camera scanning needs HTTPS and a supported browser. You can upload a photo instead.";
            return;
        }
        const current = operation;
        start.disabled = true;
        stop.hidden = false;
        status.textContent = "Allow camera access to start scanning.";
        try {
            const opened = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: "environment" } }, audio: false,
            });
            if (current !== operation) {
                opened.getTracks().forEach(track => track.stop());
                return;
            }
            stream = opened;
            video.srcObject = stream;
            video.hidden = false;
            await video.play();
            if (current !== operation) return;
            status.textContent = "Point your camera at a QR code.";
            function scan() {
                if (current !== operation) return;
                try {
                    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth) {
                        const code = decode(video, video.videoWidth, video.videoHeight);
                        if (code) {
                            showResult(code);
                            return;
                        }
                    }
                    timer = setTimeout(scan, Number(root.dataset.scanIntervalMs));
                } catch {
                    stopCamera();
                    status.textContent = "Camera scanning failed. Try uploading a photo instead.";
                }
            }
            scan();
        } catch (error) {
            if (current !== operation) return;
            stopCamera();
            status.textContent = error.name === "NotAllowedError"
                ? "Camera permission was denied. Allow access in browser settings or upload a photo."
                : "Could not start the camera. Close other camera apps or upload a photo.";
        }
    });

    stop.addEventListener("click", () => {
        stopCamera();
        status.textContent = "Camera stopped.";
    });
    window.addEventListener("pagehide", stopCamera);
    document.addEventListener("visibilitychange", () => {
        if (document.hidden && (stream || start.disabled)) {
            stopCamera();
            status.textContent = "Camera stopped. Tap Start camera to scan again.";
        }
    });

    async function readPhoto(file) {
        stopCamera();
        clearResult();
        const current = operation;
        if (file.size > Number(root.dataset.maxImageBytes)) {
            status.textContent = "This image is too large. Choose a smaller photo.";
            return;
        }
        status.textContent = "Reading image…";
        const url = URL.createObjectURL(file);
        try {
            const image = new Image();
            image.src = url;
            await image.decode();
            if (current !== operation) return;
            const code = decode(image, image.naturalWidth, image.naturalHeight);
            if (code) showResult(code);
            else status.textContent = "No QR code found. Try a clearer image with the whole code visible.";
        } catch {
            if (current === operation) status.textContent = "This image could not be opened. Try a JPEG or PNG photo.";
        } finally {
            URL.revokeObjectURL(url);
        }
    }

    photo.addEventListener("change", () => {
        const file = photo.files[0];
        photo.value = "";
        if (file) readPhoto(file);
    });
    document.addEventListener("paste", event => {
        const image = Array.from(event.clipboardData?.files || []).find(file => file.type.startsWith("image/"));
        if (image) {
            event.preventDefault();
            readPhoto(image);
        }
    });
    document.getElementById("clear-result").addEventListener("click", () => {
        stopCamera();
        clearResult();
        status.textContent = "Ready to scan.";
    });
    copy.addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(result.value);
            status.textContent = "Text copied.";
        } catch {
            result.focus();
            result.select();
            status.textContent = "Select and copy the decoded text manually.";
        }
    });

    const text = document.getElementById("qr-text");
    const generated = document.getElementById("generated-qr");
    const download = document.getElementById("download-qr");
    const encodeStatus = document.getElementById("encode-status");
    function hideGenerated() {
        generated.hidden = true;
        download.hidden = true;
        download.removeAttribute("href");
        encodeStatus.textContent = "";
    }
    text.addEventListener("input", hideGenerated);
    document.getElementById("generate-qr").addEventListener("click", () => {
        hideGenerated();
        if (!text.value) {
            encodeStatus.textContent = "Enter some text first.";
            return;
        }
        try {
            qrcode.stringToBytes = value => Array.from(new TextEncoder().encode(value));
            const code = qrcode(0, "M");
            code.addData(text.value, "Byte");
            code.make();
            const margin = Number(root.dataset.qrQuietModules);
            const pixels = Number(root.dataset.qrModulePixels);
            const count = code.getModuleCount();
            generated.width = generated.height = (count + margin * 2) * pixels;
            const drawing = generated.getContext("2d");
            // QR codes need high contrast and a white quiet zone, independent of theme.
            drawing.fillStyle = "white";
            drawing.fillRect(0, 0, generated.width, generated.height);
            drawing.fillStyle = "black";
            for (let row = 0; row < count; row += 1) {
                for (let col = 0; col < count; col += 1) {
                    if (code.isDark(row, col)) drawing.fillRect((col + margin) * pixels, (row + margin) * pixels, pixels, pixels);
                }
            }
            download.href = generated.toDataURL("image/png");
            generated.hidden = false;
            download.hidden = false;
            encodeStatus.textContent = "QR code ready. Scan it or download the image.";
        } catch {
            encodeStatus.textContent = "This text is too long for a QR code. Try a shorter message.";
        }
    });
})();
