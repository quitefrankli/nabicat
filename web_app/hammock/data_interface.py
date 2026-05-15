import html
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
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
_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class Project:
    name: str
    posts: list[str]


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
        meta = self._read_meta(post_dir)
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
        meta = self._read_meta(post_dir)
        template = meta.get("template")
        # Re-render templated posts fresh on every view so renderer changes
        # (e.g. byline format) propagate without needing a re-save.
        if template == "markdown":
            src = post_dir / "source.md"
            if src.exists():
                return self._render_markdown_index(meta, src.read_text(encoding="utf-8"))
        elif template == "gallery":
            gf = post_dir / "gallery.json"
            if gf.exists():
                try:
                    gallery = json.loads(gf.read_text(encoding="utf-8"))
                except Exception:
                    gallery = {"images": []}
                return self._render_gallery_index(meta, gallery)
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

    @staticmethod
    def _read_meta(post_dir: Path) -> dict:
        meta_file = post_dir / "meta.json"
        if not meta_file.exists():
            return {}
        try:
            return json.loads(meta_file.read_text())
        except Exception:
            return {}

    def get_post_meta(self, project: str, post: str) -> dict:
        return self._read_meta(self._post_dir(project, post))

    def write_post_meta(self, post_dir: Path, meta: dict) -> None:
        self.atomic_write(post_dir / "meta.json", data=json.dumps(meta, indent=2), mode="w", encoding="utf-8")

    def user_can_edit(self, user: Optional[User], project: str, post: str) -> bool:
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        meta = self.get_post_meta(project, post)
        owner = meta.get("owner")
        if user.is_admin:
            return True
        return bool(owner) and owner == user.id

    # ---------- slug helpers ----------

    def unique_post_slug(self, project_slug: str, title: str) -> str:
        base = slugify(title)
        project_path = self.projects_dir / project_slug
        if not project_path.exists():
            return base
        candidate = base
        i = 2
        while (project_path / candidate).exists():
            candidate = f"{base}-{i}"
            i += 1
        return candidate

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
                meta = self._read_meta(post_dir)
                if meta.get("owner") == username:
                    total += self._dir_size(post_dir)
        return total

    def quota_bytes(self, user: User) -> int:
        cfg = ConfigManager()
        return cfg.hammock_admin_quota_bytes if user.is_admin else cfg.hammock_non_admin_quota_bytes

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
        project_slug = slugify(project_input)
        if not title.strip():
            raise APIError("Title is required")
        post_slug = self.unique_post_slug(project_slug, title)
        post_dir = self.projects_dir / project_slug / post_slug

        body_bytes = len(source_md.encode("utf-8"))
        self.check_quota(user, body_bytes)

        post_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "owner": user.id,
            "template": "markdown",
            "title": title.strip(),
        }
        self.write_post_meta(post_dir, meta)
        self.atomic_write(post_dir / "source.md", data=source_md, mode="w", encoding="utf-8")
        self.atomic_write(post_dir / "index.html", data=self._render_markdown_index(meta, source_md),
                          mode="w", encoding="utf-8")
        return project_slug, post_slug

    def update_markdown_post(self, project: str, post: str, title: str, source_md: str) -> None:
        post_dir = self._post_dir(project, post)
        meta = self._read_meta(post_dir)
        if meta.get("template") != "markdown":
            raise APIError("Post is not a markdown post")
        if not title.strip():
            raise APIError("Title is required")
        meta["title"] = title.strip()
        self.write_post_meta(post_dir, meta)
        self.atomic_write(post_dir / "source.md", data=source_md, mode="w", encoding="utf-8")
        self.atomic_write(post_dir / "index.html", data=self._render_markdown_index(meta, source_md),
                          mode="w", encoding="utf-8")

    def get_markdown_source(self, project: str, post: str) -> str:
        src = self._post_dir(project, post) / "source.md"
        return src.read_text(encoding="utf-8") if src.exists() else ""

    # ---------- gallery ----------

    def create_gallery_post(self, user: User, project_input: str, title: str, description: str) -> tuple[str, str]:
        project_slug = slugify(project_input)
        if not title.strip():
            raise APIError("Title is required")
        post_slug = self.unique_post_slug(project_slug, title)
        post_dir = self.projects_dir / project_slug / post_slug
        post_dir.mkdir(parents=True, exist_ok=True)
        (post_dir / "thumbs").mkdir(exist_ok=True)
        meta = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "owner": user.id,
            "template": "gallery",
            "title": title.strip(),
        }
        gallery = {"title": title.strip(), "description": description, "images": []}
        self.write_post_meta(post_dir, meta)
        self._write_gallery(post_dir, gallery)
        self._render_and_write_gallery(post_dir, meta, gallery)
        return project_slug, post_slug

    def get_gallery(self, project: str, post: str) -> dict:
        post_dir = self._post_dir(project, post)
        gf = post_dir / "gallery.json"
        if gf.exists():
            return json.loads(gf.read_text(encoding="utf-8"))
        return {"title": "", "description": "", "images": []}

    def _write_gallery(self, post_dir: Path, gallery: dict) -> None:
        self.atomic_write(post_dir / "gallery.json", data=json.dumps(gallery, indent=2),
                          mode="w", encoding="utf-8")

    def update_gallery_meta(self, project: str, post: str, title: str, description: str) -> None:
        post_dir = self._post_dir(project, post)
        meta = self._read_meta(post_dir)
        if meta.get("template") != "gallery":
            raise APIError("Post is not a gallery post")
        if not title.strip():
            raise APIError("Title is required")
        meta["title"] = title.strip()
        self.write_post_meta(post_dir, meta)
        gallery = self.get_gallery(project, post)
        gallery["title"] = title.strip()
        gallery["description"] = description
        self._write_gallery(post_dir, gallery)
        self._render_and_write_gallery(post_dir, meta, gallery)

    def add_gallery_images(self, user: User, project: str, post: str, files: list[FileStorage]) -> int:
        post_dir = self._post_dir(project, post)
        meta = self._read_meta(post_dir)
        if meta.get("template") != "gallery":
            raise APIError("Post is not a gallery post")
        thumbs_dir = post_dir / "thumbs"
        thumbs_dir.mkdir(exist_ok=True)

        # Gather and validate each file. Read bytes once so we can quota-check
        # before any disk writes.
        prepared: list[tuple[str, bytes]] = []
        total_new_bytes = 0
        existing_names = {img for img in self.get_gallery(project, post).get("images", [])}
        for fs in files:
            if not fs or not fs.filename:
                continue
            safe = secure_filename(fs.filename)
            if not safe:
                continue
            ext = Path(safe).suffix.lower()
            if ext not in _ALLOWED_IMAGE_EXTS:
                raise APIError(f"Unsupported image type: {fs.filename}")
            # disambiguate against existing names in this gallery
            stem = Path(safe).stem
            candidate = safe
            i = 2
            while candidate in existing_names or any(candidate == name for name, _ in prepared):
                candidate = f"{stem}-{i}{ext}"
                i += 1
            data = fs.read()
            if not data:
                continue
            prepared.append((candidate, data))
            total_new_bytes += len(data)

        if not prepared:
            return 0

        self.check_quota(user, total_new_bytes)

        added: list[str] = []
        for name, data in prepared:
            self.atomic_write(post_dir / name, data=data, mode="wb")
            self._make_thumbnail(post_dir / name, thumbs_dir / f"{name}.webp")
            added.append(name)

        gallery = self.get_gallery(project, post)
        gallery["images"] = gallery.get("images", []) + added
        self._write_gallery(post_dir, gallery)
        self._render_and_write_gallery(post_dir, meta, gallery)
        return len(added)

    def delete_gallery_image(self, project: str, post: str, filename: str) -> None:
        post_dir = self._post_dir(project, post)
        meta = self._read_meta(post_dir)
        if meta.get("template") != "gallery":
            raise APIError("Post is not a gallery post")
        # only allow files actually listed in the gallery (prevents path traversal
        # and accidental deletion of meta.json / index.html / etc.)
        gallery = self.get_gallery(project, post)
        if filename not in gallery.get("images", []):
            raise APIError("Image not found in gallery")
        self.atomic_delete(post_dir / filename)
        self.atomic_delete(post_dir / "thumbs" / f"{filename}.webp")
        gallery["images"] = [n for n in gallery["images"] if n != filename]
        self._write_gallery(post_dir, gallery)
        self._render_and_write_gallery(post_dir, meta, gallery)

    # ---------- thumbnails ----------

    def _make_thumbnail(self, src: Path, dst: Path) -> None:
        cfg = ConfigManager()
        try:
            with Image.open(src) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                max_px = cfg.hammock_gallery_thumb_max_px
                img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
                dst.parent.mkdir(parents=True, exist_ok=True)
                img.save(dst, "WEBP", quality=cfg.hammock_gallery_thumb_quality, method=6)
        except Exception as e:
            logging.warning(f"Hammock thumbnail failed for {src.name}: {e}")

    # ---------- delete post ----------

    def delete_post(self, project: str, post: str) -> None:
        post_dir = self._post_dir(project, post)
        if not post_dir.exists():
            return
        shutil.rmtree(post_dir)
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

    def _render_and_write_gallery(self, post_dir: Path, meta: dict, gallery: dict) -> None:
        html_str = self._render_gallery_index(meta, gallery)
        self.atomic_write(post_dir / "index.html", data=html_str, mode="w", encoding="utf-8")

    def _render_gallery_index(self, meta: dict, gallery: dict) -> str:
        title = html.escape(gallery.get("title") or meta.get("title", ""))
        description = html.escape(gallery.get("description", ""))
        images = gallery.get("images", [])
        items = []
        for name in images:
            safe = html.escape(name)
            items.append(
                f'<figure class="hammock-gallery-photo">'
                f'<button type="button" class="hammock-gallery-photo-btn" data-full="{safe}">'
                f'<img loading="lazy" decoding="async" src="thumbs/{safe}.webp" alt="">'
                f'</button>'
                f'</figure>'
            )
        feed = "\n".join(items) if items else (
            '<p class="hammock-gallery-empty">No images yet.</p>'
        )
        desc_block = f'<p class="hammock-gallery-desc">{description}</p>' if description else ""
        return (
            f'<article class="hammock-post hammock-gallery">'
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
        # Remove every post owned by this user; prune empty projects.
        for project_dir in list(self.projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            for post_dir in list(project_dir.iterdir()):
                if not post_dir.is_dir():
                    continue
                if self._read_meta(post_dir).get("owner") == user.id:
                    shutil.rmtree(post_dir)
            if not any(project_dir.iterdir()):
                project_dir.rmdir()

    def backup_data(self, backup_dir: Path) -> None:
        if self._content_dir.exists():
            shutil.copytree(self._content_dir, backup_dir / "hammock", dirs_exist_ok=True)
