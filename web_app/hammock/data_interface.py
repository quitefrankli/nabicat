import html
import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from markdown_it import MarkdownIt
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from web_app.config import ConfigManager
from web_app.data_interface import DataInterface as BaseDataInterface
from web_app.errors import APIError
from web_app.users import User


_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".3gp", ".3gpp"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class Project:
    name: str
    posts: list[str]


@dataclass
class PreparedGalleryUpload:
    media_type: str
    name: str
    data: bytes
    display_name: str
    upload_suffix: str


@dataclass
class RawPost:
    project: str
    slug: str
    title: str
    date: str
    owner: str
    type: str = "raw"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "date": self.date,
            "owner": self.owner,
        }


@dataclass
class MarkdownPost:
    project: str
    slug: str
    title: str
    date: str
    owner: str
    type: str = "markdown"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "date": self.date,
            "owner": self.owner,
        }


@dataclass
class GalleryPost:
    project: str
    slug: str
    title: str
    date: str
    owner: str
    description: str = ""
    items: list[dict] = field(default_factory=list)
    type: str = "gallery"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "date": self.date,
            "owner": self.owner,
            "template-data": {
                "description": self.description,
                "items": self.items,
            },
        }


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s or "untitled"


class DataInterface(BaseDataInterface):
    def __init__(self):
        super().__init__()
        self._content_dir = ConfigManager().save_data_path / "hammock"
        self.projects_dir = self._content_dir / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._md = MarkdownIt("commonmark", {"html": False, "linkify": True, "breaks": True})

    # ---------- listing / reading ----------

    def _post_sort_key(self, post_dir: Path) -> tuple:
        meta = self.get_post_meta(post_dir.parent.name, post_dir.name)
        return (meta.get("date", ""), post_dir.name)

    def get_posts_by_project(self) -> list[Project]:
        projects: list[Project] = []
        for project_dir in sorted(self.projects_dir.iterdir(), key=lambda p: p.name):
            if not project_dir.is_dir():
                continue
            post_dirs = [d for d in project_dir.iterdir() if d.is_dir()]
            posts = [d.name for d in sorted(post_dirs, key=self._post_sort_key, reverse=True)]
            projects.append(Project(name=project_dir.name, posts=posts))
        return projects

    def get_post_content(self, project: str, post: str) -> str:
        post_dir = self._post_dir(project, post)
        meta = self.get_post_meta(project, post)
        template = meta.get("type")
        if template == "markdown":
            src = post_dir / "source.md"
            if src.exists():
                return self._render_markdown_index(meta, src.read_text(encoding="utf-8"))
        elif template == "gallery":
            return self._render_gallery_index(meta, self.get_gallery(project, post))
        content_file = post_dir / "index.html"
        if not content_file.exists():
            raise FileNotFoundError(f"Content file not found for post {project}/{post}")
        return content_file.read_text(encoding="utf-8")

    def get_asset_path(self, project: str, post: str, filename: str) -> Path | None:
        asset_path = self._post_dir(project, post) / filename
        if not asset_path.resolve().is_relative_to(self.projects_dir.resolve()):
            return None
        return asset_path

    # ---------- meta ----------

    def _post_dir(self, project: str, post: str) -> Path:
        # Trust callers to pass slugs that match existing directories. Path traversal
        # guard: resolved path must stay inside projects_dir.
        path = self.projects_dir / project / post
        if not path.resolve().is_relative_to(self.projects_dir.resolve()):
            raise APIError("Invalid path")
        return path

    @property
    def meta_file(self) -> Path:
        return self._content_dir / "meta.json"

    def _read_meta_store(self) -> dict:
        if not self.meta_file.exists():
            return {"projects": {}}
        try:
            store = json.loads(self.meta_file.read_text(encoding="utf-8"))
        except Exception:
            return {"projects": {}}
        if not isinstance(store, dict):
            return {"projects": {}}
        projects = store.get("projects")
        if not isinstance(projects, dict):
            store["projects"] = {}
        return store

    def _write_meta_store(self, store: dict) -> None:
        self._content_dir.mkdir(parents=True, exist_ok=True)
        self.atomic_write(self.meta_file, data=json.dumps(store, indent=2), mode="w", encoding="utf-8")

    def _post_entry(self, project: str, post: str) -> dict:
        store = self._read_meta_store()
        entry = store.get("projects", {}).get(project, {}).get("posts", {}).get(post, {})
        return entry if isinstance(entry, dict) else {}

    def _post_meta_for_template(self, entry: dict) -> dict:
        meta = dict(entry)
        if "type" in meta:
            meta["template"] = meta["type"]
        return meta

    def get_post_meta(self, project: str, post: str) -> dict:
        return self._post_meta_for_template(self._post_entry(project, post))

    def write_post_meta(self, project: str, post: str, meta: dict) -> None:
        store = self._read_meta_store()
        project_store = store.setdefault("projects", {}).setdefault(project, {"posts": {}})
        project_store.setdefault("posts", {})[post] = dict(meta)
        self._write_meta_store(store)

    def register_raw_post(self, project: str, post: str, title: str, owner: str, date: str) -> None:
        self.write_post_meta(project, post, RawPost(project, post, title, date, owner).to_dict())

    def user_can_edit(self, user: Optional[User], project: str, post: str) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        meta = self.get_post_meta(project, post)
        owner = meta.get("owner")
        if user.is_admin:
            return True
        return bool(owner) and owner == user.id

    # ---------- input validation ----------

    @staticmethod
    def _validate_text(value: str, field: str, max_chars: int) -> str:
        """Length-cap a user-supplied string and raise APIError if over budget."""
        if value is None:
            return ""
        if len(value) > max_chars:
            raise APIError(f"{field} is too long (max {max_chars} characters)")
        return value

    # ---------- slug helpers ----------

    def reserve_post_slug(self, project_slug: str, title: str) -> str:
        """Return the slug for `title` under `project_slug`, or raise APIError
        if a post with the same slug already exists in that project."""
        slug = slugify(title)
        if (self.projects_dir / project_slug / slug).exists():
            raise APIError(
                f'A post titled "{title}" already exists in project '
                f'"{project_slug}". Pick a different title.'
            )
        return slug

    # ---------- quota ----------

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def user_storage_bytes(self, username: str) -> int:
        total = 0
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for post_dir in project_dir.iterdir():
                if not post_dir.is_dir():
                    continue
                meta = self.get_post_meta(project_dir.name, post_dir.name)
                if meta.get("owner") == username:
                    total += self._dir_size(post_dir)
        return total

    def quota_bytes(self, user: User) -> int:
        cfg = ConfigManager()
        return cfg.hammock.admin_quota_bytes if user.is_admin else cfg.hammock.non_admin_quota_bytes

    def check_quota(self, user: User, additional_bytes: int) -> None:
        used = self.user_storage_bytes(user.id)
        limit = self.quota_bytes(user)
        if used + additional_bytes > limit:
            raise APIError(
                f"Storage quota exceeded ({used + additional_bytes} > {limit} bytes). "
                f"Free up space by deleting existing posts."
            )

    # ---------- markdown ----------

    def create_markdown_post(self, user: User, project_input: str, title: str, source_md: str) -> tuple[str, str]:
        cfg = ConfigManager()
        self._validate_text(project_input, "Project name", cfg.hammock.project_slug_max_chars)
        title = self._validate_text(title, "Title", cfg.hammock.title_max_chars)
        source_md = self._validate_text(source_md, "Markdown", cfg.hammock.markdown_max_chars)
        project_slug = slugify(project_input)
        if not title.strip():
            raise APIError("Title is required")
        post_slug = self.reserve_post_slug(project_slug, title)
        post_dir = self.projects_dir / project_slug / post_slug

        body_bytes = len(source_md.encode("utf-8"))
        self.check_quota(user, body_bytes)

        post_dir.mkdir(parents=True, exist_ok=True)
        meta = MarkdownPost(
            project_slug,
            post_slug,
            title.strip(),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            user.id,
        ).to_dict()
        self.write_post_meta(project_slug, post_slug, meta)
        self.atomic_write(post_dir / "source.md", data=source_md, mode="w", encoding="utf-8")
        return project_slug, post_slug

    def update_markdown_post(self, project: str, post: str, title: str, source_md: str) -> None:
        cfg = ConfigManager()
        title = self._validate_text(title, "Title", cfg.hammock.title_max_chars)
        source_md = self._validate_text(source_md, "Markdown", cfg.hammock.markdown_max_chars)
        post_dir = self._post_dir(project, post)
        meta = self._post_entry(project, post)
        if meta.get("type") != "markdown":
            raise APIError("Post is not a markdown post")
        if not title.strip():
            raise APIError("Title is required")
        meta["title"] = title.strip()
        self.write_post_meta(project, post, meta)
        self.atomic_write(post_dir / "source.md", data=source_md, mode="w", encoding="utf-8")

    def get_markdown_source(self, project: str, post: str) -> str:
        src = self._post_dir(project, post) / "source.md"
        return src.read_text(encoding="utf-8") if src.exists() else ""

    # ---------- gallery ----------

    def create_gallery_post(self, user: User, project_input: str, title: str, description: str) -> tuple[str, str]:
        cfg = ConfigManager()
        self._validate_text(project_input, "Project name", cfg.hammock.project_slug_max_chars)
        title = self._validate_text(title, "Title", cfg.hammock.title_max_chars)
        description = self._validate_text(description, "Description", cfg.hammock.description_max_chars)
        project_slug = slugify(project_input)
        if not title.strip():
            raise APIError("Title is required")
        post_slug = self.reserve_post_slug(project_slug, title)
        post_dir = self.projects_dir / project_slug / post_slug
        post_dir.mkdir(parents=True, exist_ok=True)
        post_meta = GalleryPost(
            project_slug,
            post_slug,
            title.strip(),
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            user.id,
            description,
        ).to_dict()
        self.write_post_meta(project_slug, post_slug, post_meta)
        return project_slug, post_slug

    @staticmethod
    def _gallery_template_data(meta: dict) -> dict:
        data = meta.get("template-data")
        return data if isinstance(data, dict) else {}

    def get_gallery(self, project: str, post: str) -> dict:
        meta = self.get_post_meta(project, post)
        if meta.get("type") != "gallery":
            return {"title": "", "description": "", "items": []}
        template_data = self._gallery_template_data(meta)
        return {
            "title": meta.get("title", ""),
            "description": template_data.get("description", ""),
            "items": template_data.get("items", []),
        }

    @staticmethod
    def _gallery_items(gallery: dict) -> list[dict]:
        items = gallery.get("items")
        if isinstance(items, list) and items:
            return [
                {"type": item.get("type", "image"), "filename": item.get("filename")}
                for item in items
                if isinstance(item, dict) and item.get("filename")
            ]
        return []

    def update_gallery_meta(self, project: str, post: str, title: str, description: str) -> None:
        cfg = ConfigManager()
        title = self._validate_text(title, "Title", cfg.hammock.title_max_chars)
        description = self._validate_text(description, "Description", cfg.hammock.description_max_chars)
        meta = self._post_entry(project, post)
        if meta.get("type") != "gallery":
            raise APIError("Post is not a gallery post")
        if not title.strip():
            raise APIError("Title is required")
        meta["title"] = title.strip()
        template_data = self._gallery_template_data(meta)
        template_data["description"] = description
        meta["template-data"] = template_data
        self.write_post_meta(project, post, meta)

    def add_gallery_images(self, user: User, project: str, post: str, files: list[FileStorage]) -> int:
        return self.add_gallery_media(user, project, post, files)

    def add_gallery_media(self, user: User, project: str, post: str, files: list[FileStorage]) -> int:
        post_dir = self._post_dir(project, post)
        meta = self._post_entry(project, post)
        if meta.get("type") != "gallery":
            raise APIError("Post is not a gallery post")

        # Gather and validate each file. Read bytes once so we can quota-check
        # before any disk writes.
        prepared: list[PreparedGalleryUpload] = []
        total_new_bytes = 0
        gallery = self.get_gallery(project, post)
        existing_names = {item["filename"] for item in self._gallery_items(gallery)}
        for fs in files:
            if not fs or not fs.filename:
                continue
            safe = secure_filename(fs.filename)
            if not safe:
                continue
            ext = Path(safe).suffix.lower()
            if ext in _ALLOWED_IMAGE_EXTS:
                media_type = "image"
                candidate_ext = ".webp"
            elif ext in _ALLOWED_VIDEO_EXTS:
                media_type = "video"
                candidate_ext = ".mp4"
            else:
                raise APIError(f"Unsupported media type: {fs.filename}")
            # disambiguate against existing names in this gallery
            stem = Path(safe).stem
            candidate = f"{stem}{candidate_ext}"
            i = 2
            while (
                candidate in existing_names
                or any(candidate == item.name for item in prepared)
            ):
                candidate = f"{stem}-{i}{candidate_ext}"
                i += 1
            data = fs.read()
            if not data:
                continue
            if media_type == "image":
                image_data = self._make_thumbnail_bytes(data, safe)
                prepared.append(PreparedGalleryUpload(media_type, candidate, image_data, safe, ext))
                total_new_bytes += len(image_data)
            else:
                if len(data) > ConfigManager().hammock.gallery_video_max_upload_bytes:
                    raise APIError(f"Video {fs.filename} is too large")
                prepared.append(PreparedGalleryUpload(media_type, candidate, data, safe, ext))
                total_new_bytes += len(data)

        if not prepared:
            return 0

        self.check_quota(user, total_new_bytes)

        added: list[dict] = []
        for item in prepared:
            if item.media_type == "image":
                self.atomic_write(post_dir / item.name, data=item.data, mode="wb")
                added.append({"type": "image", "filename": item.name})
                continue

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=item.upload_suffix)
            try:
                tmp.write(item.data)
            finally:
                tmp.close()
            upload_path = Path(tmp.name)
            output_path = post_dir / item.name
            try:
                self._validate_video(upload_path, item.display_name)
                self._transcode_video(upload_path, output_path, item.display_name)
                self._validate_video(output_path, item.display_name)
            except APIError:
                self.atomic_delete(upload_path)
                self.atomic_delete(output_path)
                raise
            self.atomic_delete(upload_path)
            added.append({"type": "video", "filename": item.name})

        gallery = self.get_gallery(project, post)
        items = self._gallery_items(gallery)
        items.extend(added)
        template_data = self._gallery_template_data(meta)
        template_data["items"] = items
        meta["template-data"] = template_data
        self.write_post_meta(project, post, meta)
        return len(added)

    def delete_gallery_image(self, project: str, post: str, filename: str) -> None:
        self.delete_gallery_media(project, post, filename)

    def delete_gallery_media(self, project: str, post: str, filename: str) -> None:
        post_dir = self._post_dir(project, post)
        meta = self._post_entry(project, post)
        if meta.get("type") != "gallery":
            raise APIError("Post is not a gallery post")
        gallery = self.get_gallery(project, post)
        items = self._gallery_items(gallery)
        item = next((item for item in items if item.get("filename") == filename), None)
        if item is None:
            raise APIError("Media not found in gallery")
        self.atomic_delete(post_dir / filename)
        items = [item for item in items if item.get("filename") != filename]
        template_data = self._gallery_template_data(meta)
        template_data["items"] = items
        meta["template-data"] = template_data
        self.write_post_meta(project, post, meta)

    # ---------- thumbnails / video processing ----------

    def _make_thumbnail(self, src: Path, dst: Path) -> None:
        image_data = self._make_thumbnail_bytes(src.read_bytes(), src.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.atomic_write(dst, data=image_data, mode="wb")

    def _make_thumbnail_bytes(self, data: bytes, filename: str) -> bytes:
        cfg = ConfigManager()
        # Enforce a per-process decoded-pixel ceiling so a small file can't
        # decompress into gigabytes of RGB data. Pillow raises
        # DecompressionBombError at 2x this value automatically.
        Image.MAX_IMAGE_PIXELS = cfg.hammock.max_image_pixels
        try:
            with Image.open(BytesIO(data)) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                max_px = cfg.hammock.gallery_thumb_max_px
                img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
                out = BytesIO()
                img.save(out, "WEBP", quality=cfg.hammock.gallery_thumb_quality, method=6)
                return out.getvalue()
        except Image.DecompressionBombError as e:
            logging.warning(f"Hammock thumbnail rejected (pixel bomb) for {filename}: {e}")
            raise APIError(f"Image {filename} is too large to process") from e
        except Exception as e:
            logging.warning(f"Hammock thumbnail failed for {filename}: {e}")
            raise APIError(f"Could not process {filename} as an image") from e

    @staticmethod
    def _run_media_command(cmd: list[str], timeout_s: int, error_message: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=True)
        except FileNotFoundError as e:
            raise APIError("Video processing requires ffmpeg") from e
        except subprocess.TimeoutExpired as e:
            raise APIError(error_message) from e
        except subprocess.CalledProcessError as e:
            logging.warning(f"Hammock media command failed: {e.stderr}")
            detail = (e.stderr or "").strip().splitlines()
            if detail:
                raise APIError(f"{error_message}: {detail[-1][:240]}") from e
            raise APIError(error_message) from e

    def _probe_video_duration(self, src: Path, display_name: str) -> float | None:
        cfg = ConfigManager()
        result = self._run_media_command(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=duration:format=duration",
                "-of", "json",
                str(src),
            ],
            cfg.hammock.gallery_video_transcode_timeout_s,
            f"Could not process {display_name} as a video",
        )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as e:
            raise APIError(f"Could not process {display_name} as a video") from e
        candidates = [payload.get("format", {}).get("duration")]
        candidates.extend(stream.get("duration") for stream in payload.get("streams", []))
        for candidate in candidates:
            try:
                duration = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(duration) and duration > 0:
                return duration
        return None

    def _validate_video(self, src: Path, display_name: str) -> None:
        cfg = ConfigManager()
        duration = self._probe_video_duration(src, display_name)
        if duration is None:
            logging.warning(f"Hammock video duration unavailable for {display_name}; continuing to transcode")
            return
        if duration > cfg.hammock.gallery_video_max_duration_s:
            raise APIError(
                f"Video {display_name} is too long "
                f"(max {cfg.hammock.gallery_video_max_duration_s} seconds)"
            )

    def _transcode_video(self, src: Path, dst: Path, display_name: str) -> None:
        cfg = ConfigManager()
        max_height = cfg.hammock.gallery_video_max_height_px
        vf = (
            f"scale='trunc(iw*min(1,{max_height}/ih)/2)*2':"
            f"'trunc(ih*min(1,{max_height}/ih)/2)*2',"
            "setsar=1"
        )
        self._run_media_command(
            [
                "ffmpeg",
                "-y",
                "-i", str(src),
                "-map", "0:v:0",
                "-map", "0:a?",
                "-dn",
                "-sn",
                "-vf", vf,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "28",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "96k",
                "-movflags", "+faststart",
                str(dst),
            ],
            cfg.hammock.gallery_video_transcode_timeout_s,
            f"Could not process {display_name} as a video",
        )

    # ---------- delete post ----------

    def delete_post(self, project: str, post: str) -> None:
        post_dir = self._post_dir(project, post)
        if not post_dir.exists():
            return
        shutil.rmtree(post_dir)
        store = self._read_meta_store()
        posts = store.get("projects", {}).get(project, {}).get("posts", {})
        posts.pop(post, None)
        if not posts:
            store.get("projects", {}).pop(project, None)
        self._write_meta_store(store)
        project_dir = post_dir.parent
        if project_dir.is_dir() and not any(project_dir.iterdir()):
            project_dir.rmdir()

    # ---------- rendering ----------

    def _render_markdown_index(self, meta: dict, source_md: str) -> str:
        title = html.escape(meta.get("title", ""))
        body = self._md.render(source_md or "")
        return (
            f'<article class="hammock-post hammock-md">'
            f'<header class="hammock-post-header">'
            f'<h1>{title}</h1>'
            f'{self._render_byline(meta)}'
            f'</header>'
            f'<div class="hammock-md-body">{body}</div>'
            f'</article>'
        )

    def _render_gallery_index(self, meta: dict, gallery: dict) -> str:
        cfg = ConfigManager()
        title = html.escape(gallery.get("title") or meta.get("title", ""))
        description = html.escape(gallery.get("description", ""))
        media_html = []
        for item in self._gallery_items(gallery):
            name = html.escape(item.get("filename", ""))
            if item.get("type") == "video":
                media_html.append(
                    f'<figure class="hammock-gallery-photo hammock-gallery-video">'
                    f'<video autoplay loop muted playsinline preload="metadata">'
                    f'<source src="{name}" type="video/mp4">'
                    f'</video>'
                    f'</figure>'
                )
            else:
                media_html.append(
                    f'<figure class="hammock-gallery-photo">'
                    f'<button type="button" class="hammock-gallery-photo-btn" data-full="{name}">'
                    f'<img loading="lazy" decoding="async" '
                    f'src="data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA=" '
                    f'data-gallery-src="{name}" alt="">'
                    f'</button>'
                    f'</figure>'
                )
        feed = "\n".join(media_html) if media_html else (
            '<p class="hammock-gallery-empty">No media yet.</p>'
        )
        desc_block = f'<p class="hammock-gallery-desc">{description}</p>' if description else ""
        return (
            f'<article class="hammock-post hammock-gallery" '
            f'data-gallery-stagger-ms="{cfg.hammock.gallery_image_stagger_ms}" '
            f'data-gallery-max-retries="{cfg.hammock.gallery_image_max_retries}" '
            f'data-gallery-retry-delay-ms="{cfg.hammock.gallery_image_retry_delay_ms}">'
            f'<header class="hammock-post-header">'
            f'<h1>{title}</h1>'
            f'{self._render_byline(meta)}'
            f'{desc_block}'
            f'</header>'
            f'<div class="hammock-gallery-feed">{feed}</div>'
            f'</article>'
        )

    @staticmethod
    def _render_byline(meta: dict) -> str:
        date = html.escape(meta.get("date", "")[:10])
        owner = html.escape(meta.get("owner", ""))
        if owner and date:
            inner = f'by <span class="hammock-post-author">{owner}</span> &middot; {date}'
        elif owner:
            inner = f'by <span class="hammock-post-author">{owner}</span>'
        elif date:
            inner = date
        else:
            return ""
        return f'<p class="hammock-post-meta">{inner}</p>'

    # ---------- base hooks ----------

    def delete_user_data(self, user: User) -> None:
        store = self._read_meta_store()
        for project, project_data in list(store.get("projects", {}).items()):
            posts = project_data.get("posts", {})
            for post, meta in list(posts.items()):
                if isinstance(meta, dict) and meta.get("owner") == user.id:
                    shutil.rmtree(self._post_dir(project, post), ignore_errors=True)
                    posts.pop(post, None)
            if not posts:
                store["projects"].pop(project, None)
                project_dir = self.projects_dir / project
                if project_dir.is_dir() and not any(project_dir.iterdir()):
                    project_dir.rmdir()
        self._write_meta_store(store)

    def backup_data(self, backup_dir: Path) -> None:
        if self._content_dir.exists():
            shutil.copytree(self._content_dir, backup_dir / "hammock", dirs_exist_ok=True)
