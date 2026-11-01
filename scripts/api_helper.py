import subprocess
import requests
import click
import base64
import gzip
import io
import sys
import zipfile
import json
import os
import tempfile
import time
from urllib.parse import urlsplit
from datetime import datetime

from git import Repo
from pathlib import Path
from yt_dlp.cookies import extract_cookies_from_browser
from web_app.config import ConfigManager


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


def send_request(
    endpoint: str, payload: dict | None = None,
    *, base_url: str | None = None, timeout: float | None = None,
) -> requests.Response:
    saved = _load_config()
    token = saved.get("access_token")
    expires_at = saved.get("expires_at")
    if not isinstance(token, str) or not token or not isinstance(expires_at, (int, float)):
        raise click.ClickException("No saved API token. Run 'api-helper login'.")
    if time.time() >= expires_at:
        raise click.ClickException("API token has expired. Run 'api-helper login' again.")
    saved_url = get_base_url()
    target = _validate_base_url(base_url) if base_url else saved_url
    if target != saved_url:
        raise click.ClickException("The token belongs to a different server. Run 'api-helper login' for that server.")
    payload = payload if payload is not None else {}
    payload_size = len(json.dumps(payload))
    if payload_size > ConfigManager().api_helper_payload_confirm_bytes:
        if input(f"Payload size is {payload_size / 1000:.2f} kB. Do you want to continue? (y/n): ").strip().lower() != 'y':
            raise click.ClickException("Request cancelled.")
    try:
        response = requests.post(
            f"{target}/{endpoint}", json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout if timeout is not None else ConfigManager().api_llm_client_timeout_s,
            allow_redirects=False,
        )
    except requests.RequestException as error:
        raise click.ClickException(f"API request failed ({type(error).__name__}).") from None
    if response.status_code == 401:
        raise click.ClickException("API token was rejected or expired. Run 'api-helper login' again.")
    return response


@click.group(help="Interact with NabiCat using a one-day API token over HTTPS.")
def cli():
    pass

@cli.command()
@click.option("--model", default="gpt-6-luna", show_default=True,
              help="Codex model to request; defaults to Luna 6.0.")
@click.option("--effort", default="low", show_default=True,
              help="Codex reasoning effort to request.")
@click.option("--base-url", help="Server URL; defaults to the saved URL or production.")
@click.option("--timeout", type=click.FloatRange(min=0, min_open=True), default=130.0,
              show_default=True, help="LLM request timeout in seconds.")
@click.argument("prompt")
def llm(model: str, effort: str, base_url: str | None, timeout: float, prompt: str) -> None:
    """Ask the LLM API. Use - as PROMPT to read input from stdin."""
    try:
        if prompt == "-":
            prompt = click.get_text_stream("stdin").read()
        if not prompt.strip():
            raise click.ClickException("Prompt must not be empty.")
        response = send_request(
            "api/llm", {"model": model, "effort": effort, "input": prompt},
            base_url=base_url,
            timeout=timeout,
        )
        if response.status_code != 200:
            raise click.ClickException(f"LLM request failed (HTTP {response.status_code}): {response.text}")
        result = response.json()
        if not result.get("success") or not isinstance(result.get("output"), str):
            raise click.ClickException("Server returned an invalid LLM response.")
    except (requests.RequestException, ValueError) as error:
        raise click.ClickException(
            f"LLM request could not complete ({type(error).__name__}). "
            "Check your connection; use 'login' to obtain an API token."
        ) from None
    click.echo(result["output"])


@cli.command()
def login() -> None:
    """Log in for one day without saving your password."""
    username = click.prompt("Enter your username")
    password = click.prompt("Enter password", hide_input=True)
    base_url = _validate_base_url(click.prompt(
        "Enter base URL", default=get_base_url(),
    ))
    try:
        response = requests.post(
            f"{base_url}/api/token", json={"username": username, "password": password},
            timeout=ConfigManager().api_helper_login_timeout_s, allow_redirects=False,
        )
        if response.status_code != 200:
            raise click.ClickException(f"Login failed (HTTP {response.status_code}).")
        result = response.json()
        if not isinstance(result, dict):
            raise click.ClickException("Server returned an invalid login response.")
        token, lifetime = result.get("access_token"), result.get("expires_in")
        if not isinstance(token, str) or not token or not isinstance(lifetime, (int, float)) or lifetime <= 0:
            raise click.ClickException("Server returned an invalid login response.")
        _save_config({
            "base_url": base_url, "username": username, "access_token": token,
            "expires_at": time.time() + lifetime,
        })
    except (requests.RequestException, ValueError, OSError) as error:
        raise click.ClickException(f"Login failed ({type(error).__name__}).") from None
    click.echo("Logged in. API token saved for one day; your password was not saved.")


@cli.command()
def logout() -> None:
    """Remove the locally saved API token."""
    ConfigManager().api_helper_config_path.unlink(missing_ok=True)
    click.echo("Saved API token removed.")


@cli.command()
@click.argument("file", type=click.Path(exists=True))
def upload(file: str) -> None:
    file = Path(file)
    if file.is_dir():
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file_obj:
            for path in file.rglob('*'):
                if path.is_file():
                    zip_file_obj.write(path, arcname=path.relative_to(file))
        zip_buffer.seek(0)
        data = zip_buffer.read()
        mb = len(data) / (1024 * 1024)
        should_send = input(f"Confirm upload of {file} ({mb:.2f} MB) (y/n): ").strip().lower()
        if should_send != 'y':
            print("Upload cancelled.")
            return
        base_filename = file.with_suffix('.zip').name
    else:
        if not file.is_file():
            raise ValueError(f"Invalid file: {file}")
        with open(file, 'rb') as f:
            data = f.read()
        base_filename = file.name

    compressed_data = gzip.compress(data)
    encoded_data = base64.b64encode(compressed_data).decode('utf-8')
    payload = {
        "name": base_filename,
        "data": encoded_data
    }
    
    response = send_request("api/push", payload)
    print(f"Response: {response.status_code} - {response.text}")

@cli.command()
@click.argument("file", type=str)
@click.option("--raw", is_flag=True, default=False, help="print to stdout instead of saving to a file")
def download(file: str, raw: bool) -> None:
    response = send_request("api/pull", { "name": file })

    if response.status_code == 200:
        compressed_data = response.json().get("data")
        if compressed_data:
            decoded_data = base64.b64decode(compressed_data)
            decompressed_data = gzip.decompress(decoded_data)
            if raw:
                sys.stdout.buffer.write(decompressed_data)
                return
            Path(file).parent.mkdir(parents=True, exist_ok=True)
            with open(file, 'wb') as f:
                f.write(decompressed_data)
            print(f"File {file} downloaded and decompressed successfully.")
        else:
            print("No data received.")
    else:
        print(f"Failed to download file: {response.status_code} - {response.text}")

@cli.command(name="ls")
@click.argument('path', default='')
def list_files(path: str) -> None:
    """List immediate files and folders in PATH (default: root), newest first."""
    response = send_request("api/list", {'path': path})

    if response.status_code == 200:
        files = response.json().get('entries')
        if files is None:
            raise click.ClickException('Server does not support directory listings; update the server first.')
        files.sort(key=lambda file: datetime.fromisoformat(file['uploaded_at']).timestamp()
                   if file.get('uploaded_at') else float('-inf'), reverse=True)
        print("Files available (newest first):")
        for file in files:
            suffix = '/' if file['kind'] == 'folder' else ''
            print(f"{file.get('uploaded_at') or 'Unknown date'}  {file['name']}{suffix}")
    else:
        print(f"Failed to list files: {response.status_code} - {response.text}")

@cli.command()
def backup() -> None:
    response = send_request("api/backup")
    if response.status_code == 200:
        print("Backup completed successfully.")
    else:
        print(f"Failed to create backup: {response.status_code} - {response.text}")

@cli.command()
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
        print("Repository has uncommitted changes. Please commit or stash them before updating.")
        return

    origin = repo.remotes.origin
    origin.fetch()
    local_commit = repo.head.commit
    remote_commit = origin.refs.main.commit

    payload = {}
    prompt = "Ask server to pull the latest main? (y/n): "
    if local_commit.hexsha != remote_commit.hexsha:
        ahead_commits = list(repo.iter_commits(f'{remote_commit.hexsha}..{local_commit.hexsha}'))
        if len(ahead_commits) <= 0:
            print("Invalid git state detected, aborting update")
            return
        payload["patch"] = repo.git.format_patch(f"{remote_commit.hexsha}..{local_commit.hexsha}", stdout=True)
        prompt = f"Apply update with {len(ahead_commits)} commits ahead? (y/n): "
    
    if input(prompt).strip().lower() != 'y':
        print("Update cancelled.")
        return
    
    response = send_request("api/update", payload)

    if response.status_code == 200:
        print("Update requested successfully.")
    else:
        print(f"Failed to request update: {response.status_code} - {response.text}")

@cli.command()
def upload_commit_patches() -> None:
    """
    Generates and uploads commit patches for all commits between the tracking branch and current branch (assuming current is ahead)
    """
    
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        print("Repository has uncommitted changes. Please commit or stash them before generating patches.")
        return

    origin = repo.remotes.origin
    origin.fetch()
    
    current_branch = repo.active_branch
    tracking_branch = current_branch.tracking_branch()
    
    if tracking_branch is None:
        print(f"Current branch '{current_branch.name}' has no tracking branch set.")
        return

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

    if input(f"Upload {len(ahead_commits)} patches? (y/n): ").strip().lower() != 'y':
        print("Upload cancelled.")
        return
    
    response = send_request("api/push", {
        "name": "_commit_patches.zip",
        "data": base64.b64encode(gzip.compress(zip_data)).decode('ascii')
    })
    
    if response.status_code == 200:
        print("Patches uploaded successfully.")
    else:
        print(f"Failed to upload patches: {response.status_code} - {response.text}")

@cli.command()
def apply_remote_patches() -> None:
    """
    Downloads patch file from the server and applies it to the current repo
    """
    
    repo = Repo(".")
    if repo.is_dirty(untracked_files=True):
        print("Repository has uncommitted changes. Please commit or stash them before applying patches.")
        return

    # Download the patch file from server
    response = send_request("api/pull", { "name": "_commit_patches.zip" })

    if response.status_code != 200:
        print(f"Failed to download patches: {response.status_code} - {response.text}")
        return

    compressed_data = response.json().get("data")
    if not compressed_data:
        print("No patch data received.")
        return

    # Decompress and decode
    decoded_data = base64.b64decode(compressed_data)
    decompressed_data = gzip.decompress(decoded_data)

    # Extract patches from zip
    zip_buffer = io.BytesIO(decompressed_data)
    patch_files = []
    with zipfile.ZipFile(zip_buffer, 'r') as zip_file_obj:
        patch_files = sorted(zip_file_obj.namelist())
        print(f"Found {len(patch_files)} patches to apply.")
        
        if len(patch_files) == 0:
            print("No patches found in archive.")
            return
        
        # Confirm before applying
        if input(f"Apply {len(patch_files)} patches? (y/n): ").strip().lower() != 'y':
            print("Apply cancelled.")
            return
        
        # Apply each patch
        for patch_filename in patch_files:
            patch_data = zip_file_obj.read(patch_filename).decode('utf-8')
            try:
                run_result = subprocess.run(['git', 'am'], 
                                input=patch_data, 
                                check=True, 
                                capture_output=True,
                                text=True)
                print(run_result.stderr, run_result.stdout)
                print(f"✓ Applied {patch_filename}")
            except Exception as e:
                print(f"✗ Failed to apply {patch_filename}: {e}")
                # Optionally abort the am process on first failure
                try:
                    repo.git.am(abort=True)
                except:
                    pass
                return
        
        print(f"Successfully applied all {len(patch_files)} patches.")

@cli.command()
@click.argument("file", type=str)
def delete_file(file: str) -> None:
    response = send_request("api/delete", {"name": file})

    if response.status_code == 200:
        print(f"File {file} deleted successfully.")
    else:
        print(f"Failed to delete file: {response.status_code} - {response.text}")

@cli.command()
@click.option(
    "--browser",
    default="chrome",
    show_default=True,
    help="Browser name (chrome, firefox, edge, etc.)",
)
@click.option(
    "--profile",
    default="Profile 1",
    show_default=True,
    help="Browser profile name",
)
def sync_cookies(browser: str, profile: str | None) -> None:
    """
    Export YouTube cookies from the specified browser and upload to the server.
    """

    YOUTUBE_DOMAINS = {
        ".youtube.com",
        "youtube.com",
        ".google.com",
        "google.com",
        ".googlevideo.com",
    }

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
            if any(domain.endswith(yt) or domain == yt.lstrip(".") for yt in YOUTUBE_DOMAINS):
                lines.append(cookie_to_netscape(cookie))
                filtered_count += 1

        return "\n".join(lines) + "\n", filtered_count

    
    cookie_text, filtered_count = export_yt_cookies_from_browser(browser, profile)

    if filtered_count <= 0:
        print("No YouTube cookies found to export.", file=sys.stderr)
        return

    response = send_request("api/push_cookie", {"cookie": cookie_text})

    if response.status_code == 200:
        print(f"Exported {filtered_count} YouTube cookies and uploaded to server.")
    else:
        print(
            f"Exported {filtered_count} YouTube cookies but upload failed: "
            f"{response.status_code} - {response.text}"
        )

@cli.command()
@click.argument("post_folder", type=click.Path(exists=True))
@click.option("--project", required=True, help="Project name for the post")
@click.option("--name", "post_name", default=None, help="Post name (defaults to folder name)")
@click.option("--date", default=None, help="Override post date (ISO format, e.g. 2024-03-15T10:00:00)")
def upload_post(post_folder: str, project: str, post_name: str | None, date: str | None) -> None:
    post_path = Path(post_folder)
    if not post_path.is_dir():
        print(f"Error: {post_folder} is not a directory", file=sys.stderr)
        sys.exit(1)

    if post_name is None:
        post_name = post_path.name

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path in post_path.rglob('*'):
            if path.is_file():
                zf.write(path, arcname=path.relative_to(post_path))
    zip_buffer.seek(0)
    data = zip_buffer.read()

    compressed_data = gzip.compress(data)
    encoded_data = base64.b64encode(compressed_data).decode('utf-8')

    mb = len(data) / (1024 * 1024)
    print(f"Uploading post '{post_name}' to project '{project}' ({mb:.2f} MB)")

    payload = {"project": project, "post_name": post_name, "data": encoded_data}
    if date:
        payload["date"] = date
    response = send_request("loft/api/upload_post", payload)
    print(f"Response: {response.status_code} - {response.text}")

@cli.command(name="sync-data-from-prod")
@click.option("--dest", default=str(Path.home()), show_default=True,
              help="Local destination directory (receives .nabicat/)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Show what would be transferred without copying")
@click.option("--exclude", multiple=True, default=("backups/", "data/logs/"),
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
    cmd += ["nabicat.site:~/.nabicat", dest]
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
    if isinstance(expires_at, (int, float)):
        expiry = datetime.fromtimestamp(expires_at).astimezone().isoformat()
        state = "expired" if time.time() >= expires_at else "valid"
        click.echo(f"Token: {state}; expires {expiry}")
    else:
        click.echo("Token: not set; run 'api-helper login'.")


if __name__ == "__main__":
    cli()
