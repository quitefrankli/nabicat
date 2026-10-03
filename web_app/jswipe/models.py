from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class Decision(StrEnum):
    PENDING = "pending"
    SHORTLISTED = "shortlisted"
    PASSED = "passed"


class DescriptionStatus(StrEnum):
    AVAILABLE = "available"
    TRUNCATED = "truncated"
    NOT_AVAILABLE = "not_available"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"


class EvidenceLevel(StrEnum):
    FULL_DESCRIPTION = "full_description"
    TRUNCATED_DESCRIPTION = "truncated_description"
    METADATA_ONLY = "metadata_only"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_job_url(value: object) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("job URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("job URL must not contain credentials")
    hostname = parsed.hostname.rstrip(".").lower()
    port = parsed.port
    if port is not None and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        hostname = f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), hostname, path, parsed.query, ""))


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    company: str
    title: str
    url: str
    location: str
    posted_at: str | None
    source: str
    discovered_at: str
    description: str | None = None
    description_status: DescriptionStatus = DescriptionStatus.NOT_REQUESTED

    @classmethod
    def from_career_ops(cls, payload: object, *, discovered_at: str) -> Job:
        if not isinstance(payload, dict):
            raise ValueError("offer must be an object")
        company = str(payload.get("company") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not company or not title:
            raise ValueError("offer must include company and title")
        url = normalize_job_url(payload.get("url"))
        job_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        raw_posted_at = payload.get("postedAt")
        posted_at = str(raw_posted_at).strip() if raw_posted_at else None
        raw_description = payload.get("description")
        description = str(raw_description).strip() if raw_description else None
        try:
            description_status = DescriptionStatus(
                str(payload.get("descriptionStatus") or DescriptionStatus.NOT_REQUESTED)
            )
        except ValueError:
            description_status = DescriptionStatus.FAILED
        if description_status in {DescriptionStatus.AVAILABLE, DescriptionStatus.TRUNCATED}:
            if not description:
                description_status = DescriptionStatus.NOT_AVAILABLE
        else:
            description = None
        return cls(
            job_id=job_id,
            company=company[:160],
            title=title[:240],
            url=url,
            location=str(payload.get("location") or "").strip()[:200],
            posted_at=posted_at[:32] if posted_at else None,
            source=str(payload.get("source") or "unknown").strip()[:40] or "unknown",
            discovered_at=discovered_at,
            description=description,
            description_status=description_status,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Job:
        return cls(
            job_id=str(payload["job_id"]),
            company=str(payload["company"]),
            title=str(payload["title"]),
            url=normalize_job_url(payload["url"]),
            location=str(payload.get("location") or ""),
            posted_at=str(payload["posted_at"]) if payload.get("posted_at") else None,
            source=str(payload.get("source") or "unknown"),
            discovered_at=str(payload["discovered_at"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "location": self.location,
            "posted_at": self.posted_at,
            "source": self.source,
            "discovered_at": self.discovered_at,
        }


@dataclass(frozen=True, slots=True)
class SearchSettings:
    keywords: tuple[str, ...]
    locations: tuple[str, ...]
    sources: tuple[str, ...]
    since_days: int
    excluded_titles: tuple[str, ...] = ()
    companies_per_source: int = 100

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        default_companies_per_source: int = 100,
    ) -> SearchSettings:
        return cls(
            keywords=tuple(str(item) for item in payload.get("keywords", ())),
            locations=tuple(str(item) for item in payload.get("locations", ())),
            sources=tuple(str(item) for item in payload.get("sources", ())),
            since_days=int(payload["since_days"]),
            excluded_titles=tuple(str(item) for item in payload.get("excluded_titles", ())),
            companies_per_source=int(
                payload.get("companies_per_source", default_companies_per_source)
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "keywords": list(self.keywords),
            "locations": list(self.locations),
            "sources": list(self.sources),
            "since_days": self.since_days,
            "excluded_titles": list(self.excluded_titles),
            "companies_per_source": self.companies_per_source,
        }


@dataclass(frozen=True, slots=True)
class FitAssessment:
    score: int
    confidence: int
    evidence_level: EvidenceLevel
    summary: str
    matches: tuple[str, ...]
    gaps: tuple[str, ...]
    hard_conflicts: tuple[str, ...]
    profile_revision: int
    evidence_fingerprint: str
    scoring_version: int = 1

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FitAssessment:
        return cls(
            score=max(0, min(100, int(payload["score"]))),
            confidence=max(0, min(100, int(payload["confidence"]))),
            evidence_level=EvidenceLevel(str(payload["evidence_level"])),
            summary=str(payload.get("summary") or "")[:400],
            matches=tuple(str(item)[:160] for item in payload.get("matches", ())[:3]),
            gaps=tuple(str(item)[:160] for item in payload.get("gaps", ())[:3]),
            hard_conflicts=tuple(str(item)[:160] for item in payload.get("hard_conflicts", ())[:3]),
            profile_revision=int(payload.get("profile_revision", 0)),
            evidence_fingerprint=str(payload.get("evidence_fingerprint") or ""),
            scoring_version=int(payload.get("scoring_version", 1)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "evidence_level": self.evidence_level.value,
            "summary": self.summary,
            "matches": list(self.matches),
            "gaps": list(self.gaps),
            "hard_conflicts": list(self.hard_conflicts),
            "profile_revision": self.profile_revision,
            "evidence_fingerprint": self.evidence_fingerprint,
            "scoring_version": self.scoring_version,
        }


@dataclass(frozen=True, slots=True)
class ScanSummary:
    completed_at: str
    discovered: int
    added: int
    companies_scanned: int
    unreachable_boards: int
    degraded: bool
    coverage_warnings: tuple[str, ...]
    career_ops_revision: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ScanSummary:
        return cls(
            completed_at=str(payload["completed_at"]),
            discovered=int(payload["discovered"]),
            added=int(payload["added"]),
            companies_scanned=int(payload["companies_scanned"]),
            unreachable_boards=int(payload["unreachable_boards"]),
            degraded=bool(payload["degraded"]),
            coverage_warnings=tuple(str(item) for item in payload.get("coverage_warnings", ())),
            career_ops_revision=str(payload["career_ops_revision"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "completed_at": self.completed_at,
            "discovered": self.discovered,
            "added": self.added,
            "companies_scanned": self.companies_scanned,
            "unreachable_boards": self.unreachable_boards,
            "degraded": self.degraded,
            "coverage_warnings": list(self.coverage_warnings),
            "career_ops_revision": self.career_ops_revision,
        }


@dataclass(slots=True)
class AppState:
    version: int = 1
    jobs: dict[str, Job] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    settings: SearchSettings | None = None
    last_scan: ScanSummary | None = None
    assessments: dict[str, FitAssessment] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        default_companies_per_source: int = 100,
    ) -> AppState:
        if int(payload.get("version", 0)) != 1:
            raise ValueError("unsupported JSwipe data version")
        jobs = {
            str(item["job_id"]): Job.from_dict(item)
            for item in payload.get("jobs", [])
            if isinstance(item, dict)
        }
        decisions = {
            str(job_id): Decision(str(decision))
            for job_id, decision in dict(payload.get("decisions", {})).items()
            if str(job_id) in jobs
        }
        settings_payload = payload.get("settings")
        scan_payload = payload.get("last_scan")
        assessments = {
            str(job_id): FitAssessment.from_dict(assessment)
            for job_id, assessment in dict(payload.get("assessments", {})).items()
            if str(job_id) in jobs and isinstance(assessment, dict)
        }
        return cls(
            jobs=jobs,
            decisions=decisions,
            settings=(
                SearchSettings.from_dict(
                    settings_payload,
                    default_companies_per_source=default_companies_per_source,
                )
                if isinstance(settings_payload, dict)
                else None
            ),
            last_scan=(
                ScanSummary.from_dict(scan_payload) if isinstance(scan_payload, dict) else None
            ),
            assessments=assessments,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "jobs": [job.to_dict() for job in self.sorted_jobs()],
            "decisions": {
                job_id: decision.value for job_id, decision in sorted(self.decisions.items())
            },
            "settings": self.settings.to_dict() if self.settings else None,
            "last_scan": self.last_scan.to_dict() if self.last_scan else None,
            "assessments": {
                job_id: assessment.to_dict()
                for job_id, assessment in sorted(self.assessments.items())
                if job_id in self.jobs
            },
        }

    def sorted_jobs(self) -> list[Job]:
        return sorted(
            self.jobs.values(),
            key=lambda item: (
                self.assessments[item.job_id].score if item.job_id in self.assessments else -1,
                self.assessments[item.job_id].confidence if item.job_id in self.assessments else -1,
                item.posted_at or "",
                item.discovered_at,
                item.job_id,
            ),
            reverse=True,
        )

    def decision_for(self, job_id: str) -> Decision:
        return self.decisions.get(job_id, Decision.PENDING)
