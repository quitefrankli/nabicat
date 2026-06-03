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
    # A normal result is {ok, url}; an invalid-step result is {agent_text};
    # blocked clicks add blocked_url; slow nav adds warning. All variants are
    # declared below, so extra="ignore" drops any stray key rather than
    # silently persisting it.
    model_config = ConfigDict(extra="ignore")

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


class CardDetails(BaseModel):
    # Validated/agent shape (see _validate_card_details): expiry is "MM/YY".
    card_number: str = ""
    expiry: str = ""
    cvv: str = ""


class Report(BaseModel):
    # status is a real RunStatus enum on the instance. validate_assignment
    # re-coerces plain-string assignments (e.g. report.status = "failed" from
    # the verdict classifier) back to the enum. _ValueStr serializes and
    # stringifies to the bare value, so JSON/templates still see 'running'.
    # extra='ignore' lets already-persisted report.json files load even if they
    # carry keys this schema doesn't know, without re-persisting them.
    model_config = ConfigDict(validate_assignment=True, extra="ignore")

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

    # Test credentials/card the agent may use during the run. They are only
    # written to disk (plaintext, for Rerun reuse) when the matching remember_*
    # flag is set — otherwise save_report drops them from the on-disk dump while
    # the in-memory Report still carries them for the run. See save_report.
    account_credentials: Optional[AccountCredentials] = None
    card_details: Optional[CardDetails] = None
    remember_account: bool = False
    remember_card: bool = False

    # Runtime-only state, never persisted (PrivateAttr is excluded from
    # model_dump / model_dump_json automatically).
    _peek_pending: bool = PrivateAttr(default=False)
