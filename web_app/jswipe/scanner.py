from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any, Protocol

from web_app.config import ConfigManager, JswipeConfig
from .models import DescriptionStatus, Job, SearchSettings, utc_now_iso


class CareerOpsError(RuntimeError):
    pass


class CareerOpsUnavailable(CareerOpsError):
    pass


class CareerOpsTimeout(CareerOpsError):
    pass


class CareerOpsFailed(CareerOpsError):
    def __init__(self, message: str, *, stderr: str | None = None) -> None:
        super().__init__(message)
        self.stderr = stderr


class CareerOpsProtocolError(CareerOpsError):
    pass


class CareerOpsOutputLimit(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ScannerResult:
    jobs: tuple[Job, ...]
    companies_scanned: int
    unreachable_boards: int
    degraded: bool
    coverage_warnings: tuple[str, ...]
    career_ops_revision: str
    descriptions_available: int = 0
    descriptions_unavailable: int = 0
    descriptions_failed: int = 0


class JobScanner(Protocol):
    def search(self, settings: SearchSettings) -> ScannerResult: ...


class RunCommand(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        capture_output: bool,
        check: bool,
        timeout: int,
        output_limit: int,
    ) -> subprocess.CompletedProcess[bytes]: ...


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()


def _run_bounded(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    capture_output: bool,
    check: bool,
    timeout: int,
    output_limit: int,
) -> subprocess.CompletedProcess[bytes]:
    if not capture_output or check:
        raise ValueError("bounded Career-Ops execution requires captured, unchecked output")
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    streams: dict[int, tuple[IO[bytes], bytearray]] = {
        process.stdout.fileno(): (process.stdout, stdout),
        process.stderr.fileno(): (process.stderr, stderr),
    }
    deadline = time.monotonic() + timeout
    try:
        while streams:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    list(command),
                    timeout,
                    output=bytes(stdout),
                    stderr=bytes(stderr),
                )
            readable, _, _ = select.select(tuple(streams), (), (), min(remaining, 0.1))
            for descriptor in readable:
                stream, target = streams[descriptor]
                remaining_output = output_limit - len(stdout) - len(stderr)
                chunk = os.read(descriptor, min(65_536, remaining_output + 1))
                if not chunk:
                    stream.close()
                    streams.pop(descriptor)
                    continue
                if len(stdout) + len(stderr) + len(chunk) > output_limit:
                    raise CareerOpsOutputLimit
                target.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(
                list(command),
                timeout,
                output=bytes(stdout),
                stderr=bytes(stderr),
            )
        return_code = process.wait(timeout=remaining)
    except BaseException:
        _stop_process(process)
        raise
    finally:
        for stream, _target in streams.values():
            stream.close()
    return subprocess.CompletedProcess(list(command), return_code, bytes(stdout), bytes(stderr))


class CareerOpsScanner:
    def __init__(
        self,
        config: JswipeConfig,
        *,
        run_command: RunCommand = _run_bounded,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._config = config
        self._run_command = run_command
        self._environment = dict(environment) if environment is not None else None

    def search(self, settings: SearchSettings) -> ScannerResult:
        root = Path(self._config.career_ops_root).expanduser().resolve()
        script = root / self._config.scan_script
        package = root / "package.json"
        if not script.is_file() or not package.is_file():
            raise CareerOpsUnavailable(
                "Career-Ops is not installed. Run the JSwipe Career-Ops installer first."
            )
        marker = root / ".jswipe-career-ops-revision"
        if (
            not marker.is_file()
            or marker.read_text(encoding="utf-8").strip() != ConfigManager().career_ops_revision
        ):
            raise CareerOpsUnavailable("The Career-Ops checkout does not match JSwipe's pin.")
        working_directory = root.parent / "workspace"
        try:
            (working_directory / "data" / "cache").mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise CareerOpsUnavailable("Career-Ops workspace is not writable.") from error

        portals = self._portals_payload(settings)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="jswipe-portals-",
        ) as config_file:
            json.dump(portals, config_file, ensure_ascii=False)
            config_file.flush()
            environment = self._safe_environment(config_file.name)
            command = [
                self._config.node_command,
                f"--max-old-space-size={self._config.node_max_old_space_mb}",
                str(script),
                "--dry-run",
                "--json",
                "--since",
                str(settings.since_days),
                "--limit",
                str(settings.companies_per_source),
                "--ats",
                ",".join(settings.sources),
            ]
            try:
                completed = self._run_command(
                    command,
                    cwd=working_directory,
                    env=environment,
                    capture_output=True,
                    check=False,
                    timeout=self._config.scan_timeout_seconds,
                    output_limit=self._config.scanner_output_max_bytes,
                )
            except FileNotFoundError as error:
                raise CareerOpsUnavailable(
                    "The configured Node.js executable was not found."
                ) from error
            except subprocess.TimeoutExpired as error:
                raise CareerOpsTimeout(
                    "Career-Ops did not finish within the scan timeout."
                ) from error
            except CareerOpsOutputLimit as error:
                raise CareerOpsProtocolError(
                    "Career-Ops returned more data than JSwipe accepts."
                ) from error

        if completed.returncode != 0:
            stderr = completed.stderr[: self._config.scanner_output_max_bytes].decode(
                "utf-8",
                errors="replace",
            )
            raise CareerOpsFailed(
                "Career-Ops exited without completing the scan.",
                stderr=stderr or None,
            )
        if len(completed.stdout) + len(completed.stderr) > self._config.scanner_output_max_bytes:
            raise CareerOpsProtocolError("Career-Ops returned more data than JSwipe accepts.")
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CareerOpsProtocolError("Career-Ops returned invalid JSON.") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
            raise CareerOpsProtocolError("Career-Ops returned an unexpected result shape.")
        if payload.get("saved") is not False:
            raise CareerOpsProtocolError("Career-Ops did not confirm a dry-run result.")

        discovered_at = utc_now_iso()
        unique: dict[str, Job] = {}
        for offer in payload["offers"]:
            try:
                job = Job.from_career_ops(offer, discovered_at=discovered_at)
            except (TypeError, ValueError):
                continue
            if (
                job.description is not None
                and len(job.description) > self._config.job_description_max_chars
            ):
                job = replace(
                    job,
                    description=job.description[: self._config.job_description_max_chars],
                    description_status=DescriptionStatus.TRUNCATED,
                )
            unique[job.job_id] = job
            if len(unique) >= self._config.maximum_results:
                break
        statuses = payload.get("datasetStatus")
        stale_dataset = isinstance(statuses, dict) and any(
            status != "ok" for status in statuses.values()
        )
        degraded = bool(
            payload.get("capHit")
            or payload.get("stoppedByOutage")
            or payload.get("cappedBoards")
            or stale_dataset
        )
        warnings = _coverage_warnings(payload)
        return ScannerResult(
            jobs=tuple(unique.values()),
            companies_scanned=_non_negative_int(payload.get("companiesScanned")),
            unreachable_boards=_non_negative_int(payload.get("unreachableBoards")),
            degraded=degraded,
            coverage_warnings=warnings,
            career_ops_revision=ConfigManager().career_ops_revision,
            descriptions_available=_non_negative_int(payload.get("descriptionsAvailable")),
            descriptions_unavailable=_non_negative_int(payload.get("descriptionsUnavailable")),
            descriptions_failed=_non_negative_int(payload.get("descriptionsFailed")),
        )

    def _safe_environment(self, portals_path: str) -> dict[str, str]:
        source = self._environment if self._environment is not None else os.environ
        allowed = (
            "PATH",
            "HOME",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        )
        environment = {key: source[key] for key in allowed if key in source}
        environment["CAREER_OPS_PORTALS"] = portals_path
        return environment

    def _portals_payload(self, settings: SearchSettings) -> dict[str, object]:
        payload: dict[str, object] = {
            "title_filter": {
                "positive": list(settings.keywords),
                "negative": list(settings.excluded_titles),
            }
        }
        if settings.locations:
            payload["location_filter"] = {
                "always_allow": list(settings.locations),
                "allow": list(settings.locations),
            }
        return payload


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if not isinstance(value, (str, bytes, bytearray)):
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(0, parsed)


def _coverage_warnings(payload: dict[str, Any]) -> tuple[str, ...]:
    warnings: list[str] = []
    if payload.get("capHit"):
        warnings.append("The per-source company limit was reached.")
    if payload.get("stoppedByOutage"):
        warnings.append("The scan stopped early after repeated network failures.")
    statuses = payload.get("datasetStatus")
    if isinstance(statuses, dict):
        for source, status in sorted(statuses.items()):
            if status == "stale":
                warnings.append(f"{source} used a stale company directory.")
            elif status == "empty":
                warnings.append(f"{source} had no company directory available.")
    dropped_no_date = _non_negative_int(payload.get("postingsDroppedNoDate"))
    if dropped_no_date:
        warnings.append(f"{dropped_no_date} undated postings were omitted.")
    capped_boards = _non_negative_int(payload.get("cappedBoards"))
    if capped_boards:
        warnings.append(f"{capped_boards} job boards returned partial pages.")
    unreachable = _non_negative_int(payload.get("unreachableBoards"))
    if unreachable:
        warnings.append(f"{unreachable} job boards were unreachable.")
    return tuple(warnings)
