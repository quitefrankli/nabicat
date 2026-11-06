"""Small factories shared by the focused Loft tests."""

import base64
import gzip
import json
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image
from werkzeug.datastructures import FileStorage

def _make_post(projects_dir: Path, project: str, post: str, date: str | None = None):
    post_dir = projects_dir / project / post
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "index.html").write_text("<h1>test</h1>")
    meta_path = projects_dir.parent / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"projects": {}}
    meta.setdefault("projects", {}).setdefault(project, {"posts": {}})["posts"][post] = {
        "type": "raw",
        "title": post,
        "date": date or "",
    }
    meta_path.write_text(json.dumps(meta))


def _post_meta(projects_dir: Path, project: str, post: str) -> dict:
    return json.loads((projects_dir.parent / "meta.json").read_text())["projects"][project]["posts"][post]


def _raw_post_archive() -> str:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("index.html", "<h1>uploaded</h1>")
    return base64.b64encode(gzip.compress(archive.getvalue())).decode()


def _png_file_storage(name: str, size: tuple[int, int] = (40, 40)) -> FileStorage:
    """Return an in-memory PNG FileStorage so we don't need real fixture images."""
    buf = BytesIO()
    Image.new("RGB", size, color=(180, 200, 160)).save(buf, format="PNG")
    buf.seek(0)
    return FileStorage(stream=buf, filename=name, content_type="image/png")


def _mp4_file_storage(tmp_path: Path, name: str = "clip.mp4", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=15",
            "-t", str(duration),
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/mp4")


def _mov_file_storage(tmp_path: Path, name: str = "iphone.MOV", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=15",
            "-t", str(duration),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/quicktime")


def _av_mov_file_storage(tmp_path: Path, name: str = "sound.MOV", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=320x240:rate=15",
            "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=44100",
            "-t", str(duration),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/quicktime")


def _portrait_mov_file_storage(tmp_path: Path, name: str = "portrait.MOV", duration: float = 0.5) -> FileStorage:
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=540x960:rate=15",
            "-t", str(duration),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return FileStorage(stream=BytesIO(path.read_bytes()), filename=name, content_type="video/quicktime")


