from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from io import BytesIO
from typing import Any

from flask import Blueprint, flash, jsonify, redirect, render_template, request, send_file, url_for
import logging
import uuid

from flask_login import current_user
from web_app.config import ConfigManager
from web_app.helpers import limiter
from web_app.logging_utils import log_event
from web_app.redis_client import _delete_if_token_owned, get_redis

from .models import AppState, Decision, ScanSummary, SearchSettings, utc_now_iso
from .personalization import (
    JSwipeApplication,
    PersonalizationBusy,
    ProfileConflict,
    ProfileGenerationFailed,
    ProfileGenerationUnavailable,
    ResumeRejected,
    ResumeUpload,
    profile_payload,
)
from .scanner import (
    CareerOpsError,
    CareerOpsScanner,
    CareerOpsTimeout,
    JobScanner,
)

_JOB_ID = re.compile(r"[0-9a-f]{20}\Z")
ScannerFactory = Callable[[object], JobScanner]


def create_blueprint(
    *,
    scanner_factory: ScannerFactory | None = None,
) -> Blueprint:
    config = ConfigManager().jswipe
    make_scanner = scanner_factory or CareerOpsScanner
    application = JSwipeApplication(config)
    blueprint = Blueprint(
        "jswipe",
        __name__,
        template_folder="templates",
        static_folder="static",
        url_prefix="/jswipe",
    )

    @blueprint.before_request
    def require_admin():
        if current_user.is_authenticated and current_user.is_admin:
            return None
        log_event(
            "jswipe",
            "jswipe.access_denied",
            level=logging.WARNING,
            reason="insufficient_access",
            required_access="admin",
        )
        if request.method != "GET" or request.path.startswith("/jswipe/api/"):
            from flask import abort
            abort(403)
        message = (
            ConfigManager().admin_access_denied_message
            if current_user.is_authenticated
            else "Log in required"
        )
        flash(message, category="error")
        return redirect(url_for(ConfigManager().access_denied_redirect_endpoint))

    @blueprint.get("/")
    def index() -> Any:
        state = application.current_user().read_jobs()
        settings = state.settings or _default_settings(config)
        return render_template(
            "jswipe_index.html",
            app_name="JSwipe",
            settings=settings,
            available_sources=config.ats_sources,
            minimum_since_days=config.minimum_since_days,
            maximum_since_days=config.maximum_since_days,
            minimum_companies_per_source=config.minimum_companies_per_source,
            maximum_companies_per_source=config.maximum_companies_per_source,
            companies_per_source_step=config.companies_per_source_step,
        )

    @blueprint.get("/api/jobs")
    def jobs_api() -> Any:
        user = application.current_user()
        profile = user.load()
        return jsonify(
            _state_payload(
                user.read_jobs(),
                config,
                active_profile_revision=(
                    profile.profile_revision if profile.candidate is not None else None
                ),
            )
        )

    resume_limit = limiter.limit(
        f"{config.resume_rate_limit_requests} per {config.personalization_rate_limit_window_minutes} minutes",
        key_func=lambda: current_user.get_id(),
    )

    @blueprint.post("/api/resume")
    @resume_limit
    def import_resume() -> Any:
        actor = current_user._get_current_object()
        uploaded = request.files.get("resume")
        if uploaded is None:
            _event(
                "jswipe.resume_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "missing"},
            )
            return jsonify({"error": "Choose a PDF resume."}), 400
        try:
            user = application.current_user()
            with user.operation():
                state, ranking = user.import_resume(
                    ResumeUpload(
                        uploaded.filename or "resume.pdf",
                        uploaded.mimetype,
                        uploaded.stream,
                    )
                )
        except PersonalizationBusy:
            _event(
                "jswipe.resume_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "user_busy"},
            )
            return (
                jsonify({"error": "Another personalization operation is running."}),
                409,
                {"Retry-After": str(config.user_operation_retry_after_seconds)},
            )
        except ResumeRejected as error:
            _event(
                "jswipe.resume_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": error.code},
            )
            status = (
                413
                if error.code == "too_large"
                else 415
                if error.code == "unsupported_type"
                else 422
            )
            return jsonify({"error": str(error), "code": error.code}), status
        except ProfileGenerationUnavailable as error:
            _event(
                "jswipe.resume_failed",
                actor=actor,
                error=error,
                fields={
                    "reason": "provider_unavailable",
                    "error_type": type(error).__name__,
                },
            )
            return (
                jsonify(
                    {
                        "error": "The profile service is temporarily unavailable.",
                        "code": "profile_provider_unavailable",
                    }
                ),
                503,
            )
        except ProfileGenerationFailed as error:
            fields = {
                "reason": "invalid_model_response",
                "error_type": type(error).__name__,
            }
            _event(
                "jswipe.resume_failed",
                actor=actor,
                level=logging.ERROR,
                error=error,
                fields=fields,
            )
            return jsonify({"error": "JSwipe could not create a profile from that resume."}), 502
        except Exception as error:
            _event(
                "jswipe.resume_failed",
                actor=actor,
                error=error,
                fields={"reason": "internal", "error_type": type(error).__name__},
            )
            return jsonify({"error": "JSwipe could not save that resume."}), 500
        _event(
            "jswipe.resume_completed",
            actor=actor,
            fields={
                "profile_revision": state.profile_revision,
                "size_bytes": state.resume.size_bytes if state.resume else 0,
                "deeply_assessed": ranking.deeply_assessed,
                "ranking_failed_batches": ranking.failed_batches,
            },
        )
        response = profile_payload(state)
        response["ranking_status"] = "partial" if ranking.failed_batches else "complete"
        return jsonify(response)

    @blueprint.get("/api/resume")
    def download_resume() -> Any:
        stored = application.current_user().read_resume()
        if stored is None:
            return jsonify({"error": "Resume not found."}), 404
        metadata, content = stored
        response = send_file(
            BytesIO(content),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=metadata.filename,
            max_age=0,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @blueprint.delete("/api/resume")
    def remove_resume() -> Any:
        actor = current_user._get_current_object()
        try:
            user = application.current_user()
            with user.operation():
                state = user.remove_resume()
        except PersonalizationBusy:
            _event(
                "jswipe.resume_remove_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "user_busy"},
            )
            return (
                jsonify({"error": "Another personalization operation is running."}),
                409,
                {"Retry-After": str(config.user_operation_retry_after_seconds)},
            )
        except Exception as error:
            _event(
                "jswipe.resume_remove_failed",
                actor=actor,
                error=error,
                fields={"reason": "internal", "error_type": type(error).__name__},
            )
            return jsonify({"error": "JSwipe could not remove that resume."}), 500
        _event(
            "jswipe.resume_removed",
            actor=actor,
            fields={"profile_revision": state.profile_revision},
        )
        return jsonify(profile_payload(state))

    @blueprint.get("/api/profile")
    def get_profile() -> Any:
        return jsonify(profile_payload(application.current_user().load()))

    profile_limit = limiter.limit(
        f"{config.profile_rate_limit_requests} per {config.personalization_rate_limit_window_minutes} minutes",
        key_func=lambda: current_user.get_id(),
    )

    @blueprint.patch("/api/profile")
    @profile_limit
    def update_profile() -> Any:
        actor = current_user._get_current_object()
        payload = request.get_json(silent=True)
        if not isinstance(payload, Mapping):
            _event(
                "jswipe.profile_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "invalid_payload"},
            )
            return jsonify({"error": "Profile update must be a JSON object."}), 400
        revision = payload.get("profile_revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            _event(
                "jswipe.profile_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "invalid_revision"},
            )
            return jsonify({"error": "Profile revision is required."}), 400
        try:
            user = application.current_user()
            with user.operation():
                state, ranking = user.update_profile(
                    payload.get("candidate"),
                    payload.get("preferences"),
                    expected_profile_revision=revision,
                )
        except PersonalizationBusy:
            _event(
                "jswipe.profile_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "user_busy"},
            )
            return (
                jsonify({"error": "Another personalization operation is running."}),
                409,
                {"Retry-After": str(config.user_operation_retry_after_seconds)},
            )
        except ProfileConflict as error:
            _event(
                "jswipe.profile_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "conflict"},
            )
            return jsonify({"error": str(error), "code": "profile_conflict"}), 409
        except (ValueError, ProfileGenerationFailed) as error:
            _event(
                "jswipe.profile_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "invalid_profile", "error_type": type(error).__name__},
            )
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            _event(
                "jswipe.profile_failed",
                actor=actor,
                error=error,
                fields={"reason": "internal", "error_type": type(error).__name__},
            )
            return jsonify({"error": "JSwipe could not save that profile."}), 500
        _event(
            "jswipe.profile_updated",
            actor=actor,
            fields={
                "profile_revision": state.profile_revision,
                "deeply_assessed": ranking.deeply_assessed,
                "ranking_failed_batches": ranking.failed_batches,
            },
        )
        response = profile_payload(state)
        response["ranking_status"] = "partial" if ranking.failed_batches else "complete"
        return jsonify(response)

    @blueprint.delete("/api/profile")
    def clear_profile() -> Any:
        actor = current_user._get_current_object()
        try:
            user = application.current_user()
            with user.operation():
                state = user.clear_personalization()
        except PersonalizationBusy:
            _event(
                "jswipe.personalization_clear_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "user_busy"},
            )
            return (
                jsonify({"error": "Another personalization operation is running."}),
                409,
                {"Retry-After": str(config.user_operation_retry_after_seconds)},
            )
        except Exception as error:
            _event(
                "jswipe.personalization_clear_failed",
                actor=actor,
                error=error,
                fields={"reason": "internal", "error_type": type(error).__name__},
            )
            return jsonify({"error": "JSwipe could not clear personalization."}), 500
        _event(
            "jswipe.personalization_cleared",
            actor=actor,
            fields={"profile_revision": state.profile_revision},
        )
        return jsonify(profile_payload(state))

    scan_limit = limiter.limit(
        f"{config.scan_rate_limit_requests} per {config.scan_rate_limit_window_minutes} minutes",
        key_func=lambda: current_user.get_id(),
    )

    @blueprint.post("/api/scans")
    @scan_limit
    def create_scan() -> Any:
        actor = current_user._get_current_object()
        user = application.current_user()
        try:
            settings = _parse_settings(request.get_json(silent=True), config)
        except ValueError as error:
            _event(
                "jswipe.scan_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "invalid_settings", "error_type": type(error).__name__},
            )
            return jsonify({"error": str(error)}), 400

        lease = _acquire_lease("scan", config.scan_lease_seconds)
        if lease is None:
            _event(
                "jswipe.scan_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "scan_busy"},
            )
            return (
                jsonify({"error": "A Career-Ops scan is already running."}),
                409,
                {"Retry-After": str(config.scan_retry_after_seconds)},
            )

        try:
            with user.operation():
                profile = user.load()
                if profile.candidate is None:
                    _event(
                        "jswipe.scan_rejected",
                        actor=actor,
                        level=logging.WARNING,
                        fields={"reason": "not_personalized"},
                    )
                    return (
                        jsonify(
                            {
                                "error": "Upload a resume before scanning for jobs.",
                                "code": "not_personalized",
                            }
                        ),
                        409,
                    )
                settings = replace(
                    settings,
                    excluded_titles=profile.preferences.excluded_title_terms,
                )
                result = make_scanner(config).search(settings)
                ranking = user.rank(result.jobs)
                summary = ScanSummary(
                    completed_at=utc_now_iso(),
                    discovered=len(result.jobs),
                    added=0,
                    companies_scanned=result.companies_scanned,
                    unreachable_boards=result.unreachable_boards,
                    degraded=result.degraded,
                    coverage_warnings=result.coverage_warnings,
                    career_ops_revision=result.career_ops_revision,
                )
                state = user.merge_scan(
                    result.jobs,
                    settings=settings,
                    summary=summary,
                    assessments=ranking.assessments,
                )
        except PersonalizationBusy:
            _event(
                "jswipe.scan_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"reason": "user_busy"},
            )
            return (
                jsonify({"error": "Another personalization operation is running."}),
                409,
                {"Retry-After": str(config.user_operation_retry_after_seconds)},
            )
        except CareerOpsTimeout as error:
            _event(
                "jswipe.scan_failed",
                actor=actor,
                error=error,
                fields={"reason": "timeout", "error_type": type(error).__name__},
            )
            return jsonify({"error": str(error)}), 504
        except CareerOpsError as error:
            fields = {"reason": "career_ops", "error_type": type(error).__name__}
            _event(
                "jswipe.scan_failed",
                actor=actor,
                error=error,
                fields=fields,
            )
            return jsonify({"error": str(error)}), 503
        except Exception as error:
            _event(
                "jswipe.scan_failed",
                actor=actor,
                error=error,
                fields={"reason": "internal", "error_type": type(error).__name__},
            )
            return jsonify({"error": "JSwipe could not save the scan results."}), 500
        finally:
            _release_lease("scan", lease)

        last_scan = state.last_scan
        _event(
            "jswipe.scan_completed",
            actor=actor,
            fields={
                "added": last_scan.added if last_scan else 0,
                "degraded": result.degraded,
                "discovered": len(result.jobs),
                "sources": len(settings.sources),
                "deeply_assessed": ranking.deeply_assessed,
                "ranking_failed_batches": ranking.failed_batches,
            },
        )
        return jsonify(
            _state_payload(
                state,
                config,
                active_profile_revision=user.load().profile_revision,
            )
        )

    @blueprint.post("/api/jobs/<job_id>/decision")
    def decide_job(job_id: str) -> Any:
        actor = current_user._get_current_object()
        user = application.current_user()
        payload = request.get_json(silent=True)
        raw_decision = payload.get("decision") if isinstance(payload, Mapping) else None
        try:
            if _JOB_ID.fullmatch(job_id) is None:
                raise KeyError(job_id)
            decision = Decision(str(raw_decision))
            state = user.decide(job_id, decision)
        except ValueError:
            _event(
                "jswipe.decision_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"job_id": job_id, "reason": "invalid_decision"},
            )
            return jsonify({"error": "Decision must be pending, shortlisted, or passed."}), 400
        except KeyError:
            _event(
                "jswipe.decision_rejected",
                actor=actor,
                level=logging.WARNING,
                fields={"job_id": job_id, "reason": "job_not_found"},
            )
            return jsonify({"error": "Job not found."}), 404
        except Exception as error:
            _event(
                "jswipe.decision_failed",
                actor=actor,
                error=error,
                fields={"job_id": job_id, "error_type": type(error).__name__},
            )
            return jsonify({"error": "JSwipe could not save that decision."}), 500

        _event(
            "jswipe.decision_completed",
            actor=actor,
            fields={"decision": decision.value, "job_id": job_id},
        )
        profile = user.load()
        return jsonify(
            _state_payload(
                state,
                config,
                active_profile_revision=(
                    profile.profile_revision if profile.candidate is not None else None
                ),
            )
        )

    return blueprint


def _default_settings(config) -> SearchSettings:
    return SearchSettings(
        keywords=config.default_keywords,
        locations=config.default_locations,
        sources=config.ats_sources,
        since_days=config.default_since_days,
        companies_per_source=config.companies_per_source,
    )


def _parse_settings(payload: object, config) -> SearchSettings:
    if not isinstance(payload, Mapping):
        raise ValueError("Scan settings must be a JSON object.")
    keywords = _parse_filter_values(
        payload.get("keywords"),
        label="keywords",
        maximum_count=config.maximum_keywords,
        maximum_chars=config.maximum_filter_chars,
    )
    if not keywords:
        raise ValueError("Add at least one target job title or keyword.")
    locations = _parse_filter_values(
        payload.get("locations"),
        label="locations",
        maximum_count=config.maximum_locations,
        maximum_chars=config.maximum_filter_chars,
    )
    sources = _parse_filter_values(
        payload.get("sources"),
        label="sources",
        maximum_count=len(config.ats_sources),
        maximum_chars=config.maximum_filter_chars,
    )
    if not sources:
        raise ValueError("Choose at least one ATS source.")
    unknown_sources = set(sources) - set(config.ats_sources)
    if unknown_sources:
        raise ValueError("One or more ATS sources are not supported.")
    raw_since_days = payload.get("since_days")
    if isinstance(raw_since_days, bool) or not isinstance(raw_since_days, (int, str)):
        raise ValueError("Freshness must be a whole number of days.")
    try:
        since_days = int(raw_since_days)
    except ValueError:
        raise ValueError("Freshness must be a whole number of days.") from None
    if not config.minimum_since_days <= since_days <= config.maximum_since_days:
        raise ValueError(
            f"Freshness must be between {config.minimum_since_days} and "
            f"{config.maximum_since_days} days."
        )
    raw_companies = payload.get("companies_per_source", config.companies_per_source)
    if isinstance(raw_companies, bool) or not isinstance(raw_companies, (int, str)):
        raise ValueError("Companies per source must be a whole number.")
    try:
        companies_per_source = int(raw_companies)
    except ValueError:
        raise ValueError("Companies per source must be a whole number.") from None
    if not (
        config.minimum_companies_per_source
        <= companies_per_source
        <= config.maximum_companies_per_source
    ):
        raise ValueError(
            f"Companies per source must be between {config.minimum_companies_per_source} and "
            f"{config.maximum_companies_per_source}."
        )
    return SearchSettings(
        keywords=keywords,
        locations=locations,
        sources=sources,
        since_days=since_days,
        companies_per_source=companies_per_source,
    )


def _parse_filter_values(
    raw: object,
    *,
    label: str,
    maximum_count: int,
    maximum_chars: int,
) -> tuple[str, ...]:
    if isinstance(raw, str):
        values = re.split(r"[,\n]", raw)
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    elif raw is None:
        values = []
    else:
        raise ValueError(f"{label.title()} must be a list or comma-separated text.")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        if len(item) > maximum_chars:
            raise ValueError(f"Each {label} value must be {maximum_chars} characters or fewer.")
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(item)
        if len(normalized) > maximum_count:
            raise ValueError(f"Use no more than {maximum_count} {label} values.")
    return tuple(normalized)


def _state_payload(
    state: AppState,
    config,
    *,
    active_profile_revision: int | None = None,
) -> dict[str, object]:
    settings = state.settings or _default_settings(config)
    jobs = []
    counts = {decision.value: 0 for decision in Decision}
    for job in state.sorted_jobs():
        decision = state.decision_for(job.job_id)
        counts[decision.value] += 1
        payload = job.to_dict()
        payload["decision"] = decision.value
        assessment = state.assessments.get(job.job_id)
        if assessment is not None and assessment.profile_revision != active_profile_revision:
            assessment = None
        payload["fit"] = assessment.to_dict() if assessment else None
        jobs.append(payload)
    return {
        "jobs": jobs,
        "counts": counts,
        "settings": settings.to_dict(),
        "last_scan": state.last_scan.to_dict() if state.last_scan else None,
    }


def _event(name, *, actor=None, level=logging.INFO, error=None, fields=None):
    details = dict(fields or {})
    if error is not None:
        details["error_type"] = type(error).__name__
    log_event("jswipe", name, level=level, user=actor, exc_info=error, **details)


def _acquire_lease(name: str, ttl: int):
    token = uuid.uuid4().hex
    return token if get_redis().set(f"nabicat:jswipe:lease:{name}", token, nx=True, ex=ttl) else None


def _release_lease(name: str, token: str | None) -> None:
    if token:
        _delete_if_token_owned(
            get_redis(), f"nabicat:jswipe:lease:{name}", token.encode()
        )
