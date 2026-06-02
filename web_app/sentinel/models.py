from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class _ValueStr(str, Enum):
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
    # Tolerant of legacy/partial shapes: a normal result is {ok, url}, an
    # invalid-step result is {agent_text}, blocked clicks add blocked_url, etc.
    model_config = ConfigDict(extra="allow")

    ok: Optional[bool] = None
    url: str = ""
    error: Optional[str] = None
    warning: Optional[str] = None
    blocked_url: Optional[str] = None
    agent_text: Optional[str] = None


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


class AccountCredentials(BaseModel):
    username: str = ""
    password: str = ""
    extras: dict[str, str] = Field(default_factory=dict)


class Report(BaseModel):
    # use_enum_values keeps enum fields as plain strings on the instance (so
    # templates/JSON see 'running'); validate_assignment re-coerces enum
    # assignments in the runner (report.status = RunStatus.RUNNING) back to the
    # string value. extra='allow' tolerates unknown keys in already-persisted
    # report.json files written before this schema existed.
    model_config = ConfigDict(use_enum_values=True, validate_assignment=True, extra="allow")

    run_id: str
    status: RunStatus = RunStatus.QUEUED
    owner: str = ""
    batch_id: str = ""
    batch_label: str = ""
    target_url: str = ""
    target_hostname: str = ""
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
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    run_outcome: Optional[str] = None
    steps: list[Step] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    annotated_screenshots: list[str] = Field(default_factory=list)
    final_report: str = ""
    error: Optional[str] = None
    verdict_reason: Optional[str] = None

    # Runtime-only state, never persisted (PrivateAttr is excluded from
    # model_dump / model_dump_json automatically).
    _card_details: Optional[dict] = PrivateAttr(default=None)
    _account_credentials: Optional[AccountCredentials] = PrivateAttr(default=None)
    _peek_pending: bool = PrivateAttr(default=False)
