from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


LocalState = Literal["READY", "SLOW", "DOWN", "ERROR"]


@dataclass(frozen=True)
class RoutingBrief:
    backend_mode: str
    api_provider: str
    api_active_model: str
    local_ask: str
    local_cmd: str


@dataclass(frozen=True)
class PathsBrief:
    logic: str
    config: str
    memory: str
    mon_history: str
    run_profile: str
    last_error: str


@dataclass(frozen=True)
class DepBrief:
    psutil: bool
    json5: bool
    prompt_toolkit: bool
    clipboard: str  # "wl-copy" | "xclip" | "xsel" | ""


@dataclass(frozen=True)
class LocalProbe:
    state: LocalState
    host: str
    latency_ms: int
    models_count: int
    selected_ok: bool
    detail: str


@dataclass(frozen=True)
class ApiProbe:
    provider: str
    active_model: str
    key_present: bool
    config_ok: bool
    note: str = ""
    detail: str = ""
    connection_tested: bool = False


@dataclass(frozen=True)
class RegistryProbe:
    ok: bool
    detail: str


@dataclass(frozen=True)
class LastErrorBrief:
    exists: bool
    time: str = ""
    stage: str = ""
    backend: str = ""
    summary: str = ""


@dataclass(frozen=True)
class Snapshot:
    timestamp: str
    workspace: str
    routing: RoutingBrief
    paths: PathsBrief
    deps: DepBrief
    local: LocalProbe
    api: ApiProbe
    registry: RegistryProbe
    conclusion: list[str]
    next_actions: list[str]
    last_error: LastErrorBrief


@dataclass
class DashState:
    message: str = ""
    message_kind: str = ""  # "ok" | "warn" | "bad" | ""
