"use strict";
// Usage: node qstream_roundtrip.js <static dir> <options json>
// Sends random bytes through QR frames, drops some, decodes with jsQR, prints JSON stats.
const path = require("path");
const [staticDir, optionsJson] = process.argv.slice(2);
const { Encoder, Decoder } = require(path.join(staticDir, "qstream.js"));
const qrcode = require(path.join(staticDir, "vendor/qrcode.js"));
const jsQR = require(path.join(staticDir, "vendor/jsQR.js"));
const o = JSON.parse(optionsJson);

function render(frame) {
    const code = qrcode(o.qrVersion, o.qrLevel);
    code.addData(String.fromCharCode(...frame), "Byte");
    code.make();
    const count = code.getModuleCount();
    const size = (count + 2 * o.quiet) * o.pixels;
    const rgba = new Uint8ClampedArray(size * size * 4).fill(255);
    for (let y = 0; y < size; y += 1) for (let x = 0; x < size; x += 1) {
        const row = Math.floor(y / o.pixels) - o.quiet, col = Math.floor(x / o.pixels) - o.quiet;
        if (row >= 0 && col >= 0 && row < count && col < count && code.isDark(row, col)) rgba.fill(0, (y * size + x) * 4, (y * size + x) * 4 + 3);
    }
    return { rgba, size };
}

const file = Uint8Array.from({ length: o.fileBytes }, (_, i) => (i * 2654435761) >>> 24);
const encoder = new Encoder("logo ✓.png", file, o);
const decoder = new Decoder(o);
let result = null, shown = 0;
for (let seed = 0; !result && seed < encoder.chunkCount * 4; seed += 1) {
    shown += 1;
    if (seed % o.dropEvery === 0) continue;
    const { rgba, size } = render(encoder.frame(seed));
    const code = jsQR(rgba, size, size);
    if (code) result = decoder.receive(Uint8Array.from(code.binaryData));
}
console.log(JSON.stringify({
    name: result && result.name,
    matches: Boolean(result) && result.bytes.length === file.length && result.bytes.every((b, i) => b === file[i]),
    chunks: encoder.chunkCount, shown,
}));
