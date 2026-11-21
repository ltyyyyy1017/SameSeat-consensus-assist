from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateRoom(Input):
    title: str = Field(min_length=1, max_length=80)
    name: str = Field(default="主持人", min_length=1, max_length=30)


class JoinRoom(Input):
    invite: str = Field(min_length=10, max_length=100)
    name: str = Field(min_length=1, max_length=30)


class Consent(Input):
    version: int = Field(ge=1)
    playback: bool
    review: bool
    camera: bool = False
    intervene: bool = False


class Expression(Input):
    kind: Literal["speak", "wait", "listen", "unknown"] | None = None
    confident: bool = False
    summary: str = Field(default="", max_length=80)


class Segment(Input):
    text: str = Field(min_length=1, max_length=1500)
    source: Literal["manual", "asr"] = "manual"
    stable: bool = True


class Revision(Input):
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1500)


class Evidence(Input):
    segment: str
    rev: int = Field(ge=1)


class Decision(Input):
    title: str = Field(min_length=1, max_length=80)
    conclusion: str = Field(min_length=1, max_length=300)
    evidence: list[Evidence] = Field(min_length=1, max_length=10)


class Version(Input):
    version: int = Field(ge=1)


class AICandidate(Decision):
    why: str = Field(default="", max_length=200)


class AISettings(Input):
    enabled: bool


class Presentation(Input):
    epoch: str
    seq: int = Field(ge=1)
    consent_version: int = Field(ge=1)
    evidence: list[Evidence] = Field(max_length=150)
    state: Literal["queued", "playing", "finished", "cancelled", "paused", "error", "review", "idle"]
    instance: str = Field(min_length=1, max_length=100)


class ManualRequest(Input):
    kind: Literal["wait", "clarify", "speak"]
    decision: str | None = None
    segment: str | None = None


class RequestAction(Input):
    action: Literal["seen", "handled", "withdraw", "skip"]


class NoticeAction(Input):
    action: Literal["confirm", "ignore"]


class Match(Input):
    text: str = Field(min_length=1, max_length=2000)


class Contribution(Match):
    decision: str | None = None
    quote: Evidence | None = None


class Resolve(Input):
    version: int = Field(ge=1)
    action: Literal["maintain", "modify", "defer", "reopen"]
    reason: str = Field(min_length=1, max_length=1000)
    conclusion: str = Field(default="", max_length=300)
    close_pending: bool = False


class Export(Input):
    revision: int = Field(ge=1)


class Reply(Input):
    reason: str = Field(min_length=1, max_length=1000)
