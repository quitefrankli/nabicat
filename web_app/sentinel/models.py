from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class _ValueStr(StrEnum):
    """str-Enum whose str() is the bare value (not 'Class.MEMBER').

    This matters because templates render `{{ report.status }}` and JS compares
    `report.status === 'running'` — both must see the plain value.
    """

    __str__ = str.__str__


class RunStatus(_ValueStr):
    QUEUED = "queued"
    RUNNING = "running"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ExecutionStatus(_ValueStr):
    QUEUED = "queued"
    RUNNING = "running"
    SUMMARIZING = "summarizing"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    EXECUTION_ERROR = "execution_error"
    ABANDONED = "abandoned"
    INTERRUPTED = "interrupted"


class RunVerdict(_ValueStr):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class Severity(_ValueStr):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class StepAction(_ValueStr):
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    SCROLL = "scroll"
    GOTO = "goto"
    WAIT = "wait"
    PEEK = "peek"
    FINISH = "finish"
    INVALID = "invalid"


class ActionResult(BaseModel):
    # A normal result is {ok, url}; an invalid-step result is {agent_text};
    # blocked clicks add blocked_url; slow nav adds warning. All variants are
    # declared below, so extra="ignore" drops any stray key rather than
    # silently persisting it.
    model_config = ConfigDict(extra="ignore")

    ok: bool | None = None
    url: str = ""
    error: str | None = None
    warning: str | None = None
    blocked_url: str | None = None
    agent_text: str | None = None


class Step(BaseModel):
    index: int
    action: str
    reason: str
    result: ActionResult = Field(default_factory=ActionResult)
    created_at: str = ""


class Finding(BaseModel):
    severity: str
    title: str
    detail: str
    kind: str = ""
    url: str = ""
    method: str = ""
    status_code: int | None = None


class Report(BaseModel):
    """The persisted schema-v2 report.

    Secrets are deliberately not model fields. They live only in the
    request-scoped secret context while synchronous execution is active.
    """

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    schema_version: Literal[2] = 2
    run_id: str
    status: RunStatus = RunStatus.QUEUED
    lifecycle: ExecutionStatus = ExecutionStatus.QUEUED
    verdict: RunVerdict = RunVerdict.INCONCLUSIVE
    owner: str = ""
    batch_id: str = ""
    batch_label: str = ""
    target_url: str = ""
    target_hostname: str = ""
    allowed_hosts: list[str] = Field(default_factory=list)
    resolved_addresses: dict[str, list[str]] = Field(default_factory=dict)
    prompt: str = ""
    title: str = ""
    allow_accounts: bool = False
    allow_external: bool = False
    additional_domains: list[str] = Field(default_factory=list)
    allow_financial: bool = False
    device: str = ""
    demographic: str = ""
    limit_s: int = 0
    created_at: str = ""
    updated_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    run_outcome: str | None = None
    steps: list[Step] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    annotated_screenshots: list[str] = Field(default_factory=list)
    final_report: str = ""
    error: str | None = None
    verdict_reason: str | None = None
    verdict_reason_code: str | None = None

    # Runtime-only state, never persisted.
    _peek_pending: bool = PrivateAttr(default=False)
