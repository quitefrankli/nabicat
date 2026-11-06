"""Binary file transfers with optional HTTP gzip compression."""

import gzip
import zlib
from pathlib import Path

from flask import Response, request, send_file

from web_app.api_auth import authenticate_api_token
from web_app.config import ConfigManager
from web_app.errors import APIError


def compression_for_name(name: str) -> str:
    return "identity" if Path(name).suffix.lower() in ConfigManager().api_transfer_compressed_extensions else "gzip"


def parse_upload_request():
    """Authenticate before reading a binary upload; metadata is never the body."""
    user = authenticate_api_token(request.headers.get("Authorization", ""), require_admin=True)
    if request.mimetype != "application/octet-stream":
        raise APIError("File uploads require application/octet-stream")
    encoding = request.headers.get("Content-Encoding", "identity").lower()
    if encoding not in ("identity", "gzip"):
        raise APIError("Unsupported file content encoding")
    stream = request.stream
    if encoding == "gzip":
        stream = gzip.GzipFile(fileobj=stream, mode="rb")
    return request.args.to_dict() | {"username": user.id}, stream


def file_download_response(path: Path, name: str):
    if compression_for_name(name) == "identity":
        return send_file(path, mimetype="application/octet-stream", as_attachment=True,
                         download_name=Path(name).name, conditional=False)

    def compressed_chunks():
        compressor = zlib.compressobj(wbits=ConfigManager().api_transfer_gzip_window_bits)
        with path.open("rb") as source:
            while chunk := source.read(ConfigManager().api_transfer_chunk_bytes):
                yield compressor.compress(chunk)
            yield compressor.flush()

    return Response(compressed_chunks(), mimetype="application/octet-stream",
                    headers={"Content-Encoding": "gzip"})
