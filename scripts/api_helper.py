import subprocess
import requests
import click
import io
import sys
import zipfile
import json
import os
import tempfile
import time
import binascii
import math
import zlib
from contextlib import ExitStack, redirect_stdout
from urllib.parse import urlsplit
from datetime import datetime

from git import Repo, GitError
from pathlib import Path
from yt_dlp.cookies import CookieLoadError, extract_cookies_from_browser
from yt_dlp.utils import DownloadError
from web_app.config import ConfigManager
from web_app.file_transfer import decode_file_payload, encode_file_payload


class HelperCommand(click.Command):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (OSError, ValueError, GitError, zipfile.BadZipFile) as error:
            raise click.ClickException(str(error) or type(error).__name__) from None


class HelperGroup(click.Group):
    command_class = HelperCommand


def _set_yes(ctx, param, value):
    ctx.ensure_object(dict)
    if value:
        ctx.obj["yes"] = True


def yes_option(function):
    return click.option("-y", "--yes", is_flag=True, expose_value=False,
                        callback=_set_yes, help="Accept confirmation prompts (does not skip login).")(function)


def _confirm(message):
    ctx = click.get_current_context(silent=True)
    while ctx is not None:
        if ctx.obj and ctx.obj.get("yes"):
            return True
        ctx = ctx.parent
    with redirect_stdout(click.get_text_stream("stderr")):
        return click.confirm(message, err=True)


def _prompt(message, **kwargs):
    # Click's terminal input functions can echo to stdout even with err=True.
    with redirect_stdout(click.get_text_stream("stderr")):
        return click.prompt(message, err=True, **kwargs)


def _response_json(response):
    try:
        result = response.json()
    except ValueError:
        raise click.ClickException("Server returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise click.ClickException("Server returned an invalid response; expected an object.")
    return result


def _download_data(response):
    payload = _response_json(response)
    data = payload.get("data")
    if not isinstance(data, str):
        raise click.ClickException("Server returned an invalid file response.")
    try:
        return decode_file_payload(payload)
    except (ValueError, OSError, EOFError, binascii.Error, zlib.error):
        raise click.ClickException("Server returned corrupt file data.") from None


def _token_valid(saved):
    expiry = saved.get("expires_at")
    return (isinstance(saved.get("access_token"), str) and bool(saved["access_token"])
            and isinstance(expiry, (int, float)) and math.isfinite(expiry)
            and time.time() < expiry)


def _authenticate(*, base_url=None, allow_prompt=True):
    saved = _load_config()
    saved_url = get_base_url()
    target = _validate_base_url(base_url) if base_url else saved_url
    if target != saved_url:
        raise click.ClickException("The token belongs to a different server. Run 'api-helper login' for that server.")
    if not _token_valid(saved):
        if not allow_prompt:
            raise click.ClickException("Run 'api-helper login' before reading a prompt from stdin.")
        click.echo("API token missing or expired. Please log in.", err=True)
        try:
            _login(base_url=target)
        except click.Abort:
            raise click.ClickException("Login required. Run 'api-helper login'.") from None
        saved = _load_config()
    return target, saved["access_token"]


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise click.ClickException("Invalid server URL. Run 'api-helper login' again.")
    value = value.rstrip("/")
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise click.ClickException("Invalid server URL. Run 'api-helper login' again.") from None
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        (parsed.scheme != "https" and not local_http) or not parsed.hostname
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path
    ):
        raise click.ClickException("Use an HTTPS server URL (HTTP is allowed only for localhost).")
    return value


def _load_config() -> dict:
    try:
        saved = json.loads(ConfigManager().api_helper_config_path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise click.ClickException("Cannot read saved API configuration. Run 'api-helper login' again.") from None
    if not isinstance(saved, dict):
        raise click.ClickException("Invalid API configuration. Run 'api-helper login' again.")
    return saved


def _save_config(saved: dict) -> None:
    config = ConfigManager()
    path = config.api_helper_config_path
    path.parent.mkdir(parents=True, exist_ok=True, mode=config.api_helper_config_dir_mode)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), config.api_helper_config_file_mode)
            json.dump(saved, output)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def get_base_url() -> str:
    return _validate_base_url(_load_config().get("base_url") or ConfigManager().api_helper_default_base_url)


class UploadStream(io.BytesIO):
    """Report bytes as Requests reads the encoded request body."""

    def __init__(self, data, progress):
        super().__init__(data)
        self.progress = progress

    def read(self, size=-1):
        data = super().read(size)
        self.progress.update(len(data))
        return data


def _receive_download(response):
    # iter_content decodes HTTP compression, so Content-Length is only useful
    # when it describes the same bytes that the progress bar counts.
    length = response.headers.get("Content-Length", "")
    total = int(length) if length.isdigit() and not response.headers.get("Content-Encoding") else None
    chunks = response.iter_content(ConfigManager().api_helper_transfer_chunk_bytes)
    with response, io.BytesIO() as body, click.progressbar(
        iterable=chunks, length=total, label="Downloading", file=click.get_text_stream("stderr"),
        show_pos=True,
    ) as progress:
        for chunk in chunks:
            body.write(chunk)
            progress.update(len(chunk))
        # Preserve the normal Response interface for JSON validation and callers.
        response._content = body.getvalue()
        response._content_consumed = True


def send_request(
    endpoint: str, payload: dict | None = None,
    *, base_url: str | None = None, timeout: float | None = None,
    confirm_payload: bool = True,
) -> requests.Response:
    target, token = _authenticate(base_url=base_url)
    payload = payload if payload is not None else {}
    payload_size = len(json.dumps(payload))
    if confirm_payload and payload_size > ConfigManager().api_helper_payload_confirm_bytes:
        if not _confirm(f"Send payload ({payload_size:,} bytes)?"):
            raise click.ClickException("Request cancelled.")
    try:
        with ExitStack() as stack:
            headers = {"Authorization": f"Bearer {token}"}
            options = {"json": payload}
            if endpoint in {"api/push", "loft/api/upload_post"}:
                body = json.dumps(payload, allow_nan=False).encode("utf-8")
                progress = stack.enter_context(click.progressbar(
                    length=len(body), label="Uploading", file=click.get_text_stream("stderr"),
                    show_pos=True,
                ))
                options = {"data": stack.enter_context(UploadStream(body, progress))}
                headers["Content-Type"] = "application/json"
            if endpoint == "api/pull":
                options["stream"] = True
            response = requests.post(
                f"{target}/{endpoint}", **options, headers=headers,
                timeout=timeout if timeout is not None else ConfigManager().api_llm_client_timeout_s,
                allow_redirects=False,
            )
        if endpoint == "api/pull":
            _receive_download(response)
    except requests.RequestException as error:
        raise click.ClickException(f"API request failed ({type(error).__name__}).") from None
    if response.status_code == 401:
        raise click.ClickException("API token was rejected or expired. Run 'api-helper login' again.")
    if response.status_code != 200:
        raise click.ClickException(f"API request failed (HTTP {response.status_code}): {response.text}")
    result = _response_json(response)
    if result.get("success") is False or result.get("error"):
        raise click.ClickException(f"API request rejected: {result.get('error') or result.get('message') or 'unknown reason'}")
    return response


@click.group(cls=HelperGroup, help="Interact with NabiCat using a one-day API token over HTTPS.")
@yes_option
def cli():
    pass

@cli.command()
@click.option("--model", default=ConfigManager().api_helper_default_model, show_default=True,
              help="Codex model to request; defaults to Luna 6.0.")
@click.option("--effort", default=ConfigManager().api_helper_default_effort, show_default=True,
              help="Codex reasoning effort to request.")
@click.option("--base-url", help="Server URL; defaults to the saved URL or production.")
@click.option("--timeout", type=click.FloatRange(min=0, min_open=True), default=ConfigManager().api_llm_client_timeout_s,
              show_default=True, help="LLM request timeout in seconds.")
@click.argument("prompt")
@yes_option
def llm(model: str, effort: str, base_url: str | None, timeout: float, prompt: str) -> None:
    """Ask the LLM API. Use - as PROMPT to read input from stdin."""
    try:
        if prompt == "-":
            _authenticate(base_url=base_url, allow_prompt=click.get_text_stream("stdin").isatty())
            prompt = click.get_text_stream("stdin").read()
        if not prompt.strip():
            raise click.ClickException("Prompt must not be empty.")
        response = send_request(
            "api/llm", {"model": model, "effort": effort, "input": prompt},
            base_url=base_url,
            timeout=timeout,
        )
        result = _response_json(response)
        if not result.get("success") or not isinstance(result.get("output"), str):
            raise click.ClickException("Server returned an invalid LLM response.")
    except (requests.RequestException, ValueError) as error:
        raise click.ClickException(
            f"LLM request could not complete ({type(error).__name__}). "
            "Check your connection; use 'login' to obtain an API token."
        ) from None
    click.echo(result["output"])


@cli.command()
@click.option("--username", help="Account username; prompted when omitted.")
@click.option("--base-url", help="HTTPS server URL; prompted when omitted.")
def login(username: str | None, base_url: str | None) -> None:
    """Log in for one day without saving your password."""
    _login(username=username, base_url=base_url, prompt_url=base_url is None)


def _login(*, username=None, base_url=None, prompt_url=False):
    saved = _load_config()
    username = username or _prompt("Enter your username", default=saved.get("username"))
    password = _prompt("Enter password", hide_input=True)
    if prompt_url:
        base_url = _prompt("Enter base URL", default=get_base_url())
    base_url = _validate_base_url(base_url or get_base_url())
    try:
        response = requests.post(
            f"{base_url}/api/token", json={"username": username, "password": password},
            timeout=ConfigManager().api_helper_login_timeout_s, allow_redirects=False,
        )
        if response.status_code != 200:
            raise click.ClickException(f"Login failed (HTTP {response.status_code}).")
        result = _response_json(response)
        token, lifetime = result.get("access_token"), result.get("expires_in")
        if not isinstance(token, str) or not token or not isinstance(lifetime, (int, float)) or not math.isfinite(lifetime) or lifetime <= 0:
            raise click.ClickException("Server returned an invalid login response.")
        _save_config({
            "base_url": base_url, "username": username, "access_token": token,
            "expires_at": time.time() + lifetime,
        })
    except (requests.RequestException, ValueError, OSError) as error:
        raise click.ClickException(f"Login failed ({type(error).__name__}).") from None
    click.echo("Logged in. API token saved; your password was not saved.", err=True)


@cli.command()
def logout() -> None:
    """Remove the locally saved API token."""
    ConfigManager().api_helper_config_path.unlink(missing_ok=True)
    click.echo("Saved API token removed.")


def _prepare_upload(path: Path):
    if not path.is_file() and not path.is_dir():
        raise click.ClickException(f"Not a regular file or directory: {path}")
    paths = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    size = sum(p.stat().st_size for p in paths)
    if path.is_dir() or size > ConfigManager().api_helper_upload_confirm_bytes:
        if not _confirm(f"Upload {path} ({size:,} bytes before compression)?"):
            raise click.ClickException("Upload cancelled.")
    click.echo(f"Preparing {path} ({size:,} bytes)...", err=True)
    if path.is_dir():
        with io.BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for item in paths:
                    compression = (zipfile.ZIP_STORED if item.suffix.lower() in
                                   ConfigManager().api_transfer_compressed_extensions else zipfile.ZIP_DEFLATED)
                    archive.write(item, arcname=item.relative_to(path), compress_type=compression)
            data = buffer.getvalue()
    else:
        data = path.read_bytes()
    return encode_file_payload(data, path.name + ".zip" if path.is_dir() else path.name)


@cli.command()
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--to", "destination", help="Remote file path, or directory ending in / for multiple files.")
@yes_option
def upload(files: tuple[Path, ...], destination: str | None) -> None:
    """Upload FILES; directories are uploaded as ZIP archives."""
    if destination and len(files) > 1 and not destination.endswith("/"):
        raise click.UsageError("With multiple files, --to must be a directory ending in /.")
    names = [(path.name or path.resolve().name) + ".zip" if path.is_dir() else path.name for path in files]
    if len(set(names)) != len(names):
        raise click.ClickException("Uploads have duplicate remote names; upload them separately with --to.")
    for path, name in zip(files, names, strict=True):
        payload = _prepare_upload(path)
        if destination:
            name = destination + name if destination.endswith("/") else destination
        click.echo(f"Uploading {name}...", err=True)
        send_request("api/push", {"name": name, **payload}, confirm_payload=False)
        click.echo(f"Uploaded {name}.")


@cli.command()
@click.argument("file", type=str)
@click.option("--raw", is_flag=True, help="Write file bytes to stdout instead of saving.")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Local destination path.")
@click.option("--force", is_flag=True, help="Replace an existing local file without prompting.")
@yes_option
def download(file: str, raw: bool, output: Path | None, force: bool) -> None:
    """Download a remote FILE, preserving its path unless -o is supplied."""
    if raw and output is not None:
        raise click.UsageError("--raw and --output cannot be used together.")
    destination = output or Path(file)
    if not raw:
        if destination.is_dir():
            raise click.ClickException(f"Destination is a directory: {destination}")
        if destination.exists() and not force and not _confirm(f"Replace {destination}?"):
            raise click.ClickException("Download cancelled.")
        click.echo(f"Downloading {file}...", err=True)
    data = _download_data(send_request("api/pull", {"name": file}))
    if raw:
        click.get_binary_stream("stdout").write(data)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    click.echo(f"Downloaded {file} to {destination}.")

@cli.command(name="ls")
@click.argument("path", default="")
@click.option("--json", "as_json", is_flag=True, help="Output directory entries as JSON.")
def list_files(path: str, as_json: bool) -> None:
    """List immediate files and folders in PATH (default: root), newest first."""
    result = _response_json(send_request("api/list", {"path": path}))
    entries = result.get("entries")
    if not isinstance(entries, list):
        raise click.ClickException("Server returned an invalid directory listing; update the server if necessary.")
    try:
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                    or entry.get("kind") not in {"file", "folder"}
                    or not isinstance(entry.get("uploaded_at", ""), str)):
                raise ValueError()
        entries.sort(key=lambda entry: datetime.fromisoformat(entry["uploaded_at"]).timestamp()
                     if entry.get("uploaded_at") else float("-inf"), reverse=True)
    except (ValueError, TypeError, OverflowError):
        raise click.ClickException("Server returned an invalid directory listing.") from None
    if as_json:
        click.echo(json.dumps(entries))
        return
    click.echo("Files available (newest first):")
    for entry in entries:
        suffix = "/" if entry["kind"] == "folder" else ""
        size = entry.get("size")
        size_text = f"  {size:,} bytes" if isinstance(size, int) else ""
        click.echo(f"{entry.get('uploaded_at') or 'Unknown date'}  {entry['name']}{suffix}{size_text}")

@cli.command()
def backup() -> None:
    send_request("api/backup")
    click.echo("Backup completed successfully.")

@cli.command()
@yes_option
def update() -> None:
    """
    Update the server automatically via the API, bypassing GitHub Actions.

    Run from a clean checkout on main. Fetches origin/main and asks for
    confirmation before requesting an update.

    When local HEAD matches origin/main, asks the server to pull the latest
    main. Otherwise, sends local commits ahead of origin/main as patches.
    """
    
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        raise click.ClickException("Repository has uncommitted changes. Commit or stash them before updating.")

    origin = repo.remotes.origin
    origin.fetch()
    local_commit = repo.head.commit
    remote_commit = origin.refs.main.commit

    payload = {}
    prompt = "Ask server to pull the latest main?"
    if local_commit.hexsha != remote_commit.hexsha:
        ahead_commits = list(repo.iter_commits(f'{remote_commit.hexsha}..{local_commit.hexsha}'))
        if len(ahead_commits) <= 0:
            raise click.ClickException("Local HEAD is not ahead of origin/main; update aborted.")
        payload["patch"] = repo.git.format_patch(f"{remote_commit.hexsha}..{local_commit.hexsha}", stdout=True)
        prompt = f"Apply update with {len(ahead_commits)} commits ahead?"
    
    if not _confirm(prompt):
        print("Update cancelled.")
        return
    
    send_request("api/update", payload, confirm_payload=False)
    click.echo("Update requested successfully.")

@cli.command()
@yes_option
def upload_commit_patches() -> None:
    """
    Generates and uploads commit patches for all commits between the tracking branch and current branch (assuming current is ahead)
    """
    
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        raise click.ClickException("Repository has uncommitted changes. Commit or stash them before generating patches.")

    origin = repo.remotes.origin
    origin.fetch()
    
    current_branch = repo.active_branch
    tracking_branch = current_branch.tracking_branch()
    
    if tracking_branch is None:
        raise click.ClickException(f"Current branch '{current_branch.name}' has no tracking branch set.")

    local_commit = repo.head.commit
    remote_commit = tracking_branch.commit

    ahead_commits = list(repo.iter_commits(f'{remote_commit.hexsha}..{local_commit.hexsha}'))
    if len(ahead_commits) <= 0:
        print("No commits ahead of tracking branch. Nothing to generate.")
        return

    # Generate patches in memory
    print(f"Generating {len(ahead_commits)} patches in memory...")
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file_obj:
        # Iterate through commits in reverse chronological order
        for i, commit in enumerate(reversed(ahead_commits)):
            patch_data = repo.git.format_patch('-1', commit.hexsha, stdout=True)
            # Name patches with leading zeros (0001-msg, 0002-msg, etc.)
            patch_filename = f"{i+1:04d}-{commit.hexsha[:7]}.patch"
            zip_file_obj.writestr(patch_filename, patch_data)
    
    zip_buffer.seek(0)
    zip_data = zip_buffer.read()

    if not _confirm(f"Upload {len(ahead_commits)} patches?"):
        print("Upload cancelled.")
        return
    
    send_request("api/push", {
        "name": ConfigManager().api_helper_patch_archive,
        **encode_file_payload(zip_data, ConfigManager().api_helper_patch_archive),
    }, confirm_payload=False)
    
    click.echo("Patches uploaded successfully.")

@cli.command()
@yes_option
def apply_remote_patches() -> None:
    """Download and apply patches; earlier successful patches remain if a later one fails."""
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        raise click.ClickException("Repository has uncommitted changes. Commit or stash them before applying patches.")
    response = send_request("api/pull", {"name": ConfigManager().api_helper_patch_archive})
    with zipfile.ZipFile(io.BytesIO(_download_data(response))) as archive:
        names = sorted(archive.namelist())
        if not names or any(not name.endswith(".patch") for name in names):
            raise click.ClickException("Archive must contain patch files only and must not be empty.")
        patches = [(name, archive.read(name).decode("utf-8")) for name in names]
    if not _confirm(f"Apply {len(patches)} patches?"):
        click.echo("Apply cancelled.")
        return
    applied = 0
    for name, patch in patches:
        try:
            subprocess.run(["git", "am"], input=patch, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or str(error)).strip()
            try:
                repo.git.am(abort=True)
                recovery = "Failed patch aborted."
            except GitError as abort_error:
                recovery = f"Could not abort: {abort_error}. Inspect git status and run git am --abort."
            raise click.ClickException(
                f"Failed to apply {name}: {details}\n{recovery}\n"
                f"{applied} earlier patch(es) remain committed; remaining patches were not applied."
            ) from None
        applied += 1
        click.echo(f"Applied {name}.")
    click.echo(f"Successfully applied all {applied} patches.")

@cli.command()
@click.argument("file", type=str)
def delete_file(file: str) -> None:
    send_request("api/delete", {"name": file})
    click.echo(f"File {file} deleted successfully.")

@cli.command()
@click.option(
    "--browser",
    default=ConfigManager().api_helper_default_browser,
    show_default=True,
    help="Browser name (chrome, firefox, edge, etc.)",
)
@click.option(
    "--profile",
    default=ConfigManager().api_helper_default_browser_profile,
    show_default=True,
    help="Browser profile name",
)
@yes_option
def sync_cookies(browser: str, profile: str | None) -> None:
    """
    Export YouTube cookies from the specified browser and upload to the server.
    """

    def cookie_to_netscape(cookie) -> str:
        secure = "TRUE" if cookie.secure else "FALSE"
        domain_initial_dot = "TRUE" if cookie.domain.startswith(".") else "FALSE"
        expires = str(int(cookie.expires)) if cookie.expires else "0"
        return f"{cookie.domain}\t{domain_initial_dot}\t{cookie.path}\t{secure}\t{expires}\t{cookie.name}\t{cookie.value}"

    def export_yt_cookies_from_browser(browser: str, profile: str | None) -> tuple[str, int]:
        jar = extract_cookies_from_browser(browser, profile=profile)
        lines = [
            "# Netscape HTTP Cookie File",
            "# https://curl.haxx.se/docs/http-cookies.html",
            "# This file was generated by api_helper.py",
            "",
        ]
        filtered_count = 0

        for cookie in jar:
            domain = cookie.domain.lower()
            if any(domain == yt or domain.endswith("." + yt) for yt in ConfigManager().api_helper_cookie_domains):
                lines.append(cookie_to_netscape(cookie))
                filtered_count += 1

        return "\n".join(lines) + "\n", filtered_count

    
    try:
        cookie_text, filtered_count = export_yt_cookies_from_browser(browser, profile)
    except (CookieLoadError, DownloadError) as error:
        raise click.ClickException(
            f"Cannot read browser cookies ({type(error).__name__}); check --browser and --profile."
        ) from None

    if filtered_count <= 0:
        raise click.ClickException("No YouTube cookies found to export.")

    send_request("api/push_cookie", {"cookie": cookie_text})
    click.echo(f"Exported {filtered_count} YouTube cookies and uploaded to server.")

@cli.command()
@click.argument("post_folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--project", required=True, help="Project name for the post")
@click.option("--name", "post_name", default=None, help="Post name (defaults to folder name)")
@click.option("--date", default=None, help="Override post date in ISO format")
@yes_option
def upload_post(post_folder: Path, project: str, post_name: str | None, date: str | None) -> None:
    if date:
        try:
            datetime.fromisoformat(date)
        except ValueError:
            raise click.BadParameter("Use an ISO date, e.g. 2026-11-04T10:00:00.", param_hint="--date") from None
    transfer = _prepare_upload(post_folder)
    post_name = post_name or post_folder.name
    click.echo(f"Uploading post '{post_name}' to project '{project}'...", err=True)
    payload = {"project": project, "post_name": post_name, **transfer}
    if date:
        payload["date"] = date
    send_request("loft/api/upload_post", payload, confirm_payload=False)
    click.echo(f"Uploaded post '{post_name}'.")

@cli.command(name="sync-data-from-prod")
@click.option("--dest", default=str(Path.home()), show_default=True,
              help="Local destination directory (receives .nabicat/)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be transferred without copying")
@click.option("--exclude", multiple=True, default=ConfigManager().api_helper_sync_excludes,
              show_default=True, help="Rsync exclusion pattern; repeat to supply multiple patterns.")
def sync_data_from_prod(dest: str, dry_run: bool, exclude: tuple[str, ...]) -> None:
    """
    Rsync durable production data without backups or runtime logs.
    """
    cmd = ["rsync", "-azhP"]
    cmd.extend(
        f"--exclude={path}"
        for path in exclude
    )
    if dry_run:
        cmd.append("--dry-run")
    cmd += [ConfigManager().api_helper_sync_source, dest]
    print("$", " ".join(cmd))
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


@cli.command(name="config")
def show_config() -> None:
    """Show the saved server, username, and token expiry without exposing the token."""
    saved = _load_config()
    click.echo(f"Base URL: {get_base_url()}")
    click.echo(f"Username: {saved.get('username') or '(not set)'}")
    expires_at = saved.get("expires_at")
    if isinstance(expires_at, (int, float)) and math.isfinite(expires_at):
        expiry = datetime.fromtimestamp(expires_at).astimezone().isoformat()
        state = "expired" if time.time() >= expires_at else "valid"
        click.echo(f"Token: {state}; expires {expiry}")
    else:
        click.echo("Token: not set; run 'api-helper login'.")


if __name__ == "__main__":
    cli()
