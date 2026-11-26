/* global jsQR, qrcode, QStream */
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
    const receiveProgress = document.getElementById("receive-progress");
    const receivedFile = document.getElementById("received-file");
    const streamOptions = {
        chunkBytes: Number(root.dataset.streamChunkBytes),
        solitonC: Number(root.dataset.streamSolitonC),
        solitonDelta: Number(root.dataset.streamSolitonDelta),
    };
    const receiver = new QStream.Decoder(streamOptions);
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    let stream = null;
    let timer = null;
    let operation = 0;
    let cameraAvailable = Boolean(navigator.mediaDevices?.getUserMedia);

    async function updateCameraAvailability() {
        if (navigator.mediaDevices?.enumerateDevices) {
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                cameraAvailable = Boolean(navigator.mediaDevices.getUserMedia) && devices.some(device => device.kind === "videoinput");
            } catch {
                // Allow the browser to prompt for permission when detection is restricted.
            }
        }
        if (!stream && stop.hidden) start.disabled = !cameraAvailable;
    }
    updateCameraAvailability();
    navigator.mediaDevices?.addEventListener("devicechange", updateCameraAvailability);

    function stopCamera() {
        operation += 1;
        clearTimeout(timer);
        if (stream) stream.getTracks().forEach(track => track.stop());
        stream = null;
        video.srcObject = null;
        video.hidden = true;
        start.disabled = !cameraAvailable;
        stop.hidden = true;
    }

    function clearResult() {
        result.value = "";
        copy.disabled = true;
        receiver.session = null;
        receiveProgress.hidden = true;
        receivedFile.hidden = true;
        if (receivedFile.href) URL.revokeObjectURL(receivedFile.href);
        receivedFile.removeAttribute("href");
    }

    function formatBytes(count) {
        return count < 1024 ? `${count} B` : `${(count / 1024).toFixed(1)} KB`;
    }

    // Feeds one animated-QR frame to the receiver; returns true once the file is complete.
    function receiveFrame(bytes) {
        let file;
        try {
            file = receiver.receive(bytes);
        } catch {
            status.textContent = "The received file was corrupted. Keep scanning to try again.";
            return false;
        }
        if (!file) {
            const { solved, total, frames } = receiver.progress;
            receiveProgress.hidden = false;
            receiveProgress.max = total;
            receiveProgress.value = solved;
            status.textContent = `Receiving file: ${solved} of ${total} pieces (${frames} frames read). Hold steady.`;
            return false;
        }
        stopCamera();
        receiveProgress.hidden = true;
        receivedFile.href = URL.createObjectURL(new Blob([file.bytes]));
        receivedFile.download = file.name || "qread-file";
        receivedFile.hidden = false;
        result.value = `Received ${file.name || "file"} (${formatBytes(file.bytes.length)}).`;
        status.textContent = "File received. Save it to your device.";
        return true;
    }

    function showResult(code) {
        stopCamera();
        result.value = code.data;
        copy.disabled = false;
        status.textContent = "QR code decoded. Your text is ready to copy.";
    }

    function decode(source, width, height, maxDimension = Number(root.dataset.scanMaxDimension)) {
        const scale = Math.min(1, maxDimension / Math.max(width, height));
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
                    const receiving = Boolean(receiver.session);
                    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && video.videoWidth) {
                        const maxDimension = Number(receiving ? root.dataset.streamScanMaxDimension : root.dataset.scanMaxDimension);
                        const code = decode(video, video.videoWidth, video.videoHeight, maxDimension);
                        const bytes = code && Uint8Array.from(code.binaryData);
                        if (bytes && QStream.isFrame(bytes)) {
                            if (receiveFrame(bytes)) return;
                        } else if (code && !receiving) {
                            showResult(code);
                            return;
                        }
                    }
                    const interval = receiver.session ? root.dataset.streamScanIntervalMs : root.dataset.scanIntervalMs;
                    timer = setTimeout(scan, Number(interval));
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
    window.addEventListener("pagehide", () => {
        stopCamera();
        stopSending();
    });
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) return;
        if (stream || start.disabled) {
            stopCamera();
            status.textContent = "Camera stopped. Tap Open camera to scan again.";
        }
        if (sendTimer) {
            stopSending();
            sendStatus.textContent = "Sending stopped. Tap Start sending to resume.";
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
    document.getElementById("paste-image").addEventListener("click", async () => {
        try {
            if (!navigator.clipboard?.read) throw new Error("Clipboard reading unavailable");
            const items = await navigator.clipboard.read();
            for (const item of items) {
                const type = item.types.find(type => type.startsWith("image/"));
                if (type) {
                    await readPhoto(await item.getType(type));
                    return;
                }
            }
            status.textContent = "No image found in the clipboard. Copy an image and try again.";
        } catch {
            status.textContent = "Could not access the clipboard. Try your browser’s paste shortcut or upload a photo.";
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

    // Draws bytes as a QR code. QR codes need high contrast and a white quiet zone, independent of theme.
    function drawQr(target, bytes, typeNumber, level) {
        const code = qrcode(typeNumber, level);
        code.addData(String.fromCharCode(...bytes), "Byte");
        code.make();
        const margin = Number(root.dataset.qrQuietModules);
        const pixels = Number(root.dataset.qrModulePixels);
        const count = code.getModuleCount();
        target.width = target.height = (count + margin * 2) * pixels;
        const drawing = target.getContext("2d");
        drawing.fillStyle = "white";
        drawing.fillRect(0, 0, target.width, target.height);
        drawing.fillStyle = "black";
        for (let row = 0; row < count; row += 1) {
            for (let col = 0; col < count; col += 1) {
                if (code.isDark(row, col)) drawing.fillRect((col + margin) * pixels, (row + margin) * pixels, pixels, pixels);
            }
        }
    }

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
            drawQr(generated, new TextEncoder().encode(text.value), 0, "M");
            download.href = generated.toDataURL("image/png");
            generated.hidden = false;
            download.hidden = false;
            encodeStatus.textContent = "QR code ready. Scan it or download the image.";
        } catch {
            encodeStatus.textContent = "This text is too long for a QR code. Try a shorter message.";
        }
    });

    const sendFile = document.getElementById("send-file");
    const startSend = document.getElementById("start-send");
    const stopSend = document.getElementById("stop-send");
    const sendStatus = document.getElementById("send-status");
    const sendCanvas = document.getElementById("send-qr");
    let sendTimer = null;
    let sender = null;
    let sendOperation = 0;

    function stopSending() {
        sendOperation += 1;
        clearInterval(sendTimer);
        sendTimer = null;
        sendCanvas.hidden = true;
        stopSend.hidden = true;
        startSend.disabled = !sendFile.files.length;
    }

    sendFile.addEventListener("change", () => {
        stopSending();
        sender = null;
        sendStatus.textContent = "";
    });
    stopSend.addEventListener("click", () => {
        stopSending();
        sendStatus.textContent = "Sending stopped.";
    });
    startSend.addEventListener("click", async () => {
        stopSending();
        const file = sendFile.files[0];
        if (!file) return;
        if (file.size > Number(root.dataset.streamMaxFileBytes)) {
            sendStatus.textContent = "This file is too large to send. Choose a smaller file.";
            return;
        }
        const current = sendOperation;
        startSend.disabled = true;
        try {
            const bytes = new Uint8Array(await file.arrayBuffer());
            if (current !== sendOperation) return;
            sender = new QStream.Encoder(file.name, bytes, streamOptions);
        } catch {
            startSend.disabled = false;
            sendStatus.textContent = "This file could not be read. Try choosing it again.";
            return;
        }
        const typeNumber = Number(root.dataset.streamQrVersion);
        const level = root.dataset.streamQrLevel;
        const seconds = Math.ceil(sender.chunkCount * Number(root.dataset.streamFrameMs) / 1000);
        let seed = 0;
        function showFrame() {
            drawQr(sendCanvas, sender.frame(seed), typeNumber, level);
            seed += 1;
            const loop = Math.floor(seed / sender.chunkCount) + 1;
            sendStatus.textContent = `Sending ${formatBytes(file.size)} in ${sender.chunkCount} pieces (about ${seconds}s per pass, pass ${loop}). Keep this screen bright and still.`;
        }
        showFrame();
        sendCanvas.hidden = false;
        stopSend.hidden = false;
        sendTimer = setInterval(showFrame, Number(root.dataset.streamFrameMs));
    });
})();
