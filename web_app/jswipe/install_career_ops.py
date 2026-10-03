from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from web_app.config import ConfigManager


class InstallationError(RuntimeError):
    pass


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


def install(
    destination: Path,
    *,
    run: CommandRunner = subprocess.run,
) -> None:
    destination = destination.expanduser().resolve()
    if destination.exists():
        _update_existing(destination, run=run)
    else:
        _create_checkout(destination, run=run)
    _run(
        run,
        [
            "npm",
            "install",
            "--omit=dev",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=destination,
    )
    (destination / ".jswipe-career-ops-revision").write_text(
        f"{ConfigManager().career_ops_revision}\n",
        encoding="utf-8",
    )


def _create_checkout(destination: Path, *, run: CommandRunner) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".career-ops-", dir=destination.parent))
    published = False
    try:
        _run(run, ["git", "init", "--quiet"], cwd=temporary)
        _run(
            run,
            ["git", "remote", "add", "origin", ConfigManager().career_ops_repository],
            cwd=temporary,
        )
        _fetch_and_checkout(temporary, run=run)
        temporary.rename(destination)
        published = True
    finally:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)


def _update_existing(destination: Path, *, run: CommandRunner) -> None:
    if not (destination / ".git").is_dir():
        raise InstallationError("Career-Ops destination exists but is not a Git checkout.")
    completed = _run(
        run,
        ["git", "remote", "get-url", "origin"],
        cwd=destination,
    )
    remote = completed.stdout.decode("utf-8").strip()
    if remote != ConfigManager().career_ops_repository:
        raise InstallationError("Career-Ops checkout has an unexpected origin remote.")
    _fetch_and_checkout(destination, run=run)


def _fetch_and_checkout(destination: Path, *, run: CommandRunner) -> None:
    _run(
        run,
        ["git", "fetch", "--quiet", "--depth", "1", "origin", ConfigManager().career_ops_revision],
        cwd=destination,
    )
    _run(
        run,
        ["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        cwd=destination,
    )


def _run(
    run: CommandRunner,
    command: Sequence[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = run(
            list(command),
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise InstallationError(f"Could not run {command[0]}.") from error
    if completed.returncode != 0:
        raise InstallationError(f"{command[0]} failed while installing Career-Ops.")
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Install JSwipe's pinned Career-Ops runtime")
    parser.add_argument("--destination", default=ConfigManager().jswipe.career_ops_root)
    args = parser.parse_args()
    destination = Path(args.destination)
    install(destination)
    print(
        f"Career-Ops {ConfigManager().career_ops_revision} is ready at "
        f"{destination.expanduser().resolve()}"
    )


if __name__ == "__main__":
    main()
