"use strict";

/*
 * QStream: send a file as an endless sequence of QR frames using an LT fountain code.
 *
 * Every frame carries the XOR of a pseudo-random set of fixed-size chunks, chosen
 * deterministically from the frame's seed. A receiver can rebuild the file from any
 * slightly-more-than-k frames, so missed frames cost almost nothing and no loop
 * restart is needed. The first k seeds are the plain chunks (systematic pass).
 *
 * Frame layout (big-endian):
 *   "QS" | version u8 | payload crc32 u32 | payload length u32 | chunk size u16 | seed u32 | chunk
 * Payload layout: name length u8 | name UTF-8 | file bytes
 */
(root => {
    const MAGIC_0 = 0x51; // "Q"
    const MAGIC_1 = 0x53; // "S"
    const VERSION = 1;
    const HEADER_BYTES = 17;
    const MAX_NAME_BYTES = 255;

    const CRC_TABLE = new Uint32Array(256).map((_, n) => {
        let c = n;
        for (let bit = 0; bit < 8; bit += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        return c;
    });

    function crc32(bytes) {
        let crc = 0xffffffff;
        for (const byte of bytes) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
        return (crc ^ 0xffffffff) >>> 0;
    }

    function mulberry32(seed) {
        let state = seed >>> 0;
        return () => {
            state = (state + 0x6d2b79f5) >>> 0;
            let t = state;
            t = Math.imul(t ^ (t >>> 15), t | 1);
            t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        };
    }

    // Cumulative robust soliton distribution over degrees 1..k.
    function robustSoliton(k, c, delta) {
        const weights = new Float64Array(k + 1);
        const spread = c * Math.log(k / delta) * Math.sqrt(k);
        const pivot = Math.max(1, Math.min(k, Math.floor(k / spread)));
        for (let d = 1; d <= k; d += 1) {
            weights[d] = d === 1 ? 1 / k : 1 / (d * (d - 1));
            if (d < pivot) weights[d] += spread / (d * k);
            else if (d === pivot) weights[d] += spread * Math.log(spread / delta) / k;
        }
        const total = weights.reduce((sum, w) => sum + w, 0);
        const cumulative = new Float64Array(k + 1);
        for (let d = 1; d <= k; d += 1) cumulative[d] = cumulative[d - 1] + weights[d] / total;
        return cumulative;
    }

    function chunkIndices(seed, k, cumulative) {
        if (seed < k) return [seed];
        const random = mulberry32(seed);
        const roll = random();
        let degree = 1;
        while (degree < k && cumulative[degree] < roll) degree += 1;
        const picked = new Set();
        while (picked.size < degree) picked.add(Math.floor(random() * k));
        return Array.from(picked);
    }

    function xorInto(target, source) {
        for (let i = 0; i < target.length; i += 1) target[i] ^= source[i];
    }

    class Encoder {
        constructor(name, fileBytes, { chunkBytes, solitonC, solitonDelta }) {
            const nameBytes = new TextEncoder().encode(name).slice(0, MAX_NAME_BYTES);
            this.payload = new Uint8Array(1 + nameBytes.length + fileBytes.length);
            this.payload[0] = nameBytes.length;
            this.payload.set(nameBytes, 1);
            this.payload.set(fileBytes, 1 + nameBytes.length);
            this.chunkBytes = chunkBytes;
            this.chunkCount = Math.ceil(this.payload.length / chunkBytes);
            this.crc = crc32(this.payload);
            this.cumulative = robustSoliton(this.chunkCount, solitonC, solitonDelta);
        }

        chunk(index) {
            const out = new Uint8Array(this.chunkBytes);
            out.set(this.payload.subarray(index * this.chunkBytes, (index + 1) * this.chunkBytes));
            return out;
        }

        frame(seed) {
            const data = new Uint8Array(this.chunkBytes);
            for (const index of chunkIndices(seed, this.chunkCount, this.cumulative)) xorInto(data, this.chunk(index));
            const frame = new Uint8Array(HEADER_BYTES + this.chunkBytes);
            const view = new DataView(frame.buffer);
            frame[0] = MAGIC_0;
            frame[1] = MAGIC_1;
            frame[2] = VERSION;
            view.setUint32(3, this.crc);
            view.setUint32(7, this.payload.length);
            view.setUint16(11, this.chunkBytes);
            view.setUint32(13, seed >>> 0);
            frame.set(data, HEADER_BYTES);
            return frame;
        }
    }

    function isFrame(bytes) {
        return bytes.length > HEADER_BYTES && bytes[0] === MAGIC_0 && bytes[1] === MAGIC_1 && bytes[2] === VERSION;
    }

    class Decoder {
        constructor({ solitonC, solitonDelta }) {
            this.solitonC = solitonC;
            this.solitonDelta = solitonDelta;
            this.session = null;
        }

        start(crc, length, chunkBytes) {
            const chunkCount = Math.ceil(length / chunkBytes);
            this.session = {
                crc, length, chunkBytes, chunkCount,
                cumulative: robustSoliton(chunkCount, this.solitonC, this.solitonDelta),
                chunks: new Array(chunkCount).fill(null),
                solved: 0,
                seen: new Set(),
                waiting: new Map(), // chunk index -> pending packets that still include it
            };
        }

        get progress() {
            const s = this.session;
            return s ? { solved: s.solved, total: s.chunkCount, frames: s.seen.size } : null;
        }

        // Returns {name, bytes} once the file is complete, otherwise null.
        receive(bytes) {
            if (!isFrame(bytes)) return null;
            const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
            const crc = view.getUint32(3);
            const length = view.getUint32(7);
            const chunkBytes = view.getUint16(11);
            const seed = view.getUint32(13);
            if (!chunkBytes || bytes.length !== HEADER_BYTES + chunkBytes) return null;
            const s0 = this.session;
            if (!s0 || s0.crc !== crc || s0.length !== length || s0.chunkBytes !== chunkBytes) this.start(crc, length, chunkBytes);
            const s = this.session;
            if (s.seen.has(seed)) return null;
            s.seen.add(seed);
            const packet = {
                indices: new Set(chunkIndices(seed, s.chunkCount, s.cumulative)),
                data: bytes.slice(HEADER_BYTES),
            };
            this.reduce(packet);
            return s.solved === s.chunkCount ? this.assemble() : null;
        }

        reduce(first) {
            const s = this.session;
            const queue = [first];
            while (queue.length) {
                const packet = queue.pop();
                if (packet.done) continue;
                for (const index of Array.from(packet.indices)) {
                    if (s.chunks[index]) {
                        xorInto(packet.data, s.chunks[index]);
                        packet.indices.delete(index);
                    }
                }
                if (packet.indices.size > 1) {
                    if (!packet.waiting) {
                        packet.waiting = true;
                        for (const index of packet.indices) {
                            if (!s.waiting.has(index)) s.waiting.set(index, []);
                            s.waiting.get(index).push(packet);
                        }
                    }
                    continue;
                }
                packet.done = true;
                if (!packet.indices.size) continue;
                const [index] = packet.indices;
                s.chunks[index] = packet.data;
                s.solved += 1;
                queue.push(...(s.waiting.get(index) || []));
                s.waiting.delete(index);
            }
        }

        assemble() {
            const s = this.session;
            const payload = new Uint8Array(s.chunkCount * s.chunkBytes);
            s.chunks.forEach((chunk, index) => payload.set(chunk, index * s.chunkBytes));
            const trimmed = payload.subarray(0, s.length);
            this.session = null;
            if (crc32(trimmed) !== s.crc) throw new Error("Received file failed its checksum");
            const nameLength = trimmed[0];
            return {
                name: new TextDecoder().decode(trimmed.subarray(1, 1 + nameLength)),
                bytes: trimmed.slice(1 + nameLength),
            };
        }
    }

    const api = { Encoder, Decoder, isFrame, HEADER_BYTES };
    if (typeof module === "object" && module.exports) module.exports = api;
    else root.QStream = api;
})(typeof self !== "undefined" ? self : this);
