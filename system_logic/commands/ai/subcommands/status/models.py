from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional


LocalState = Literal["READY", "SLOW", "DOWN", "ERROR"]


@dataclass(frozen=True)
class LocalProbe:
    state: LocalState
    host: str
    latency_ms: int | None = None
    detail: str = ""
    model_ask: str = ""
    model_cmd: str = ""


@dataclass(frozen=True)
class ApiProbe:
    provider: str
    key_present: bool
    config_ok: bool
    note: str = ""
    detail: str = ""
    active_model: str = ""


@dataclass(frozen=True)
class RegistryProbe:
    ok: bool
    message: str


@dataclass(frozen=True)
class LastErrorBrief:
    exists: bool
    time: str = ""
    stage: str = ""
    backend: str = ""
    detail: str = ""


@dataclass(frozen=True)
class RoutingBrief:
    backend_mode: str
    api_provider: str
    api_active_model: str


@dataclass(frozen=True)
class PathsBrief:
    logic: str
    config: str
    memory: str
    mon_history: str
    run_profile: str
    last_error: str


@dataclass(frozen=True)
class Snapshot:
    routing: RoutingBrief
    local: LocalProbe
    api: ApiProbe
    registry: RegistryProbe
    last_error: LastErrorBrief
    paths: PathsBrief
    timestamp: str


@dataclass
class MenuItem:
    key: str
    title: str
    kind: Literal["view", "action"]
    render: Optional[Callable[[Snapshot], str]] = None
    run_action: Optional[Callable[[dict, Snapshot], tuple[bool, str]]] = None
    hint: str = ""


@dataclass
class AppState:
    selected: int = 0
    view_title: str = "Summary"
    message: str = ""
    show_message: bool = False
