"""Base64 file payloads with explicit, optional gzip compression."""

import base64
import gzip
from pathlib import Path

from web_app.config import ConfigManager


def encode_file_payload(data: bytes, name: str) -> dict:
    skip_gzip = Path(name).suffix.lower() in ConfigManager().api_transfer_compressed_extensions
    compression = "none" if skip_gzip else "gzip"
    encoded = data if skip_gzip else gzip.compress(data)
    return {"data": base64.b64encode(encoded).decode("ascii"), "compression": compression}


def decode_file_payload(payload: dict) -> bytes:
    compression = payload.get("compression")
    if compression not in ("none", "gzip"):
        raise ValueError("Unsupported file compression")
    data = base64.b64decode(payload["data"], validate=True)
    return data if compression == "none" else gzip.decompress(data)
