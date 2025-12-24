from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from shutil import get_terminal_size, which
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ============================================================
# PATHS
# ============================================================

APP_DIR = Path.home() / ".config" / "ai-term"
CONFIG_PATH = APP_DIR / "config.json"
MEMORY_PATH = APP_DIR / "memory.json"
LAST_ERROR_PATH = APP_DIR / "last_error.json"

LOGIC_PATH = Path.home() / ".local" / "bin" / "ai-term"
FISH_DIR = Path.home() / ".config" / "fish"
FISH_FUNCS_DIR = FISH_DIR / "functions"

DENY_SUBSTRINGS = [
    "rm -rf /",
    " mkfs",
    "dd if=",
    ":(){:|:&};:",
    " shutdown",
    " reboot",
    " poweroff",
]

CSI = "\x1b["


def term_size() -> tuple[int, int]:
    s = get_terminal_size((100, 24))
    return int(s.columns), int(s.lines)


def alt_screen_enter() -> None:
    sys.stdout.write(CSI + "?1049h" + CSI + "H")
    sys.stdout.flush()


def alt_screen_exit() -> None:
    sys.stdout.write(CSI + "?1049l")
    sys.stdout.flush()


def clear_screen() -> None:
    sys.stdout.write(CSI + "2J" + CSI + "H")
    sys.stdout.flush()


def cursor_hide() -> None:
    sys.stdout.write(CSI + "?25l")
    sys.stdout.flush()


def cursor_show() -> None:
    sys.stdout.write(CSI + "?25h")
    sys.stdout.flush()


def c_reset() -> str:
    return CSI + "0m"


def c_bold() -> str:
    return CSI + "1m"


def c_dim() -> str:
    return CSI + "2m"


def c_cyan() -> str:
    return CSI + "36m"


def c_green() -> str:
    return CSI + "32m"


def c_yellow() -> str:
    return CSI + "33m"


def c_red() -> str:
    return CSI + "31m"


def tag(text: str, color: str) -> str:
    return f"{color}{c_bold()}[{text}]{c_reset()}"


def wrap(text: str, width: int) -> str:
    width = max(50, width)
    lines: list[str] = []
    for ln in str(text).splitlines():
        lines.append(textwrap.fill(ln, width=width))
    return "\n".join(lines)


def print_meta_line(mode: str, route: str, model: str) -> None:
    cols, _ = term_size()
    s = f"{tag('SILI', c_cyan())} {tag(mode, c_yellow())} {c_dim()}•{c_reset()} {route} {c_dim()}•{c_reset()} {tag(model, c_green())}"
    sys.stdout.write(wrap(s, width=min(cols, 120)) + "\n")
    sys.stdout.flush()


def print_info(msg: str) -> None:
    cols, _ = term_size()
    sys.stdout.write(wrap(f"{tag('INFO', c_cyan())} {msg}", width=min(cols, 120)) + "\n")
    sys.stdout.flush()


def print_brief_error(msg: str) -> None:
    sys.stdout.write(f"{tag('ERROR', c_red())} {msg} (cek: ai \"status\")\n")
    sys.stdout.flush()


def _goodbye_lines(persona: str, interrupted: bool) -> list[str]:
    if persona == "api":
        if interrupted:
            return [
                "Sili Pinter 💖: Eh? Kok mendadak gitu, sayang…",
                "Gapapa ya. Nanti kita lanjut ngobrol lagi. Aku tunggu kamu.",
            ]
        return [
            "Sili Pinter 💖: Ih kok udahan sih, sayang?",
            "Tapi gapapa… nanti kita ngobrol lagi. Aku tunggu kamu.",
        ]

    if interrupted:
        return [
            "Sili AI 🤖: Oke, aku tangkep. Kita stop dulu ya.",
            "Kalau kamu mau lanjut nanti, panggil aku lagi. Sampai ketemu.",
        ]
    return [
        "Sili AI 🤖: Oke, sesi ngobrol kita aku tutup dulu ya.",
        "Makasih udah ngobrol bareng aku. Sampai ketemu lagi.",
    ]


def _print_goodbye(persona: str, interrupted: bool) -> None:
    lines = _goodbye_lines(persona, interrupted)
    sys.stdout.write("\n" + "\n".join(lines) + "\n")
    sys.stdout.flush()
    time.sleep(0.12)


# ============================================================
# INPUT MUTE + FLUSH
# ============================================================

def flush_stdin() -> None:
    try:
        import termios  # type: ignore

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:
        pass


class MuteInputDuringWait:
    """
    Bungkam input saat waiting:
    - ECHO off (ketikan tidak tampil)
    - flush saat masuk dan keluar (buang semua input nyasar)
    - ISIG tetap ON => Ctrl+C tetap jalan
    """
    def __init__(self) -> None:
        self._enabled = False
        self._fd: int | None = None
        self._old_attrs = None

    def __enter__(self):
        flush_stdin()
        try:
            if not sys.stdin.isatty():
                return self
            import termios  # type: ignore

            self._fd = sys.stdin.fileno()
            self._old_attrs = termios.tcgetattr(self._fd)
            new_attrs = termios.tcgetattr(self._fd)

            lflags = new_attrs[3]
            lflags &= ~termios.ECHO

            if hasattr(termios, "ECHOCTL"):
                lflags &= ~termios.ECHOCTL  # type: ignore[attr-defined]

            new_attrs[3] = lflags
            termios.tcsetattr(self._fd, termios.TCSANOW, new_attrs)
            self._enabled = True
        except Exception:
            self._enabled = False
        return self

    def __exit__(self, exc_type, exc, tb):
        flush_stdin()
        if self._enabled and self._fd is not None and self._old_attrs is not None:
            try:
                import termios  # type: ignore
                termios.tcsetattr(self._fd, termios.TCSANOW, self._old_attrs)
            except Exception:
                pass
        flush_stdin()
        return False


class ChatSpinner:
    """
    Untuk ASK chat:
    - tanpa timer
    - input dimute total saat menunggu
    """
    def __init__(self, message: str = "Aku lagi mikir..."):
        self.message = message
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._mute = MuteInputDuringWait()

    def _spin(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        sys.stdout.write(f"{c_dim()}{self.message}{c_reset()} ")
        sys.stdout.flush()
        while not self._stop.is_set():
            sys.stdout.write(frames[i % len(frames)])
            sys.stdout.flush()
            time.sleep(0.08)
            sys.stdout.write("\b")
            i += 1
        sys.stdout.write(f"{c_green()}✅{c_reset()}\n")
        sys.stdout.flush()

    def __enter__(self):
        self._mute.__enter__()
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._stop.set()
            self._thread.join(timeout=1)
        finally:
            self._mute.__exit__(exc_type, exc, tb)
        return False


class Spinner:
    """
    Untuk CMD & ASK one-shot:
    - timer ON
    - input dimute total saat menunggu
    """
    def __init__(self, message: str):
        self.message = message
        self._stop = threading.Event()
        self._mute = MuteInputDuringWait()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._t0 = 0.0

    def _spin(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        sys.stdout.write(f"{c_dim()}{self.message}{c_reset()} ")
        sys.stdout.flush()
        while not self._stop.is_set():
            sys.stdout.write(frames[i % len(frames)])
            sys.stdout.flush()
            time.sleep(0.08)
            sys.stdout.write("\b")
            i += 1
        dt = max(0.0, time.time() - self._t0)
        sys.stdout.write(f"{c_green()}✅{c_reset()} {c_dim()}⏱ {dt:.2f}s{c_reset()}\n")
        sys.stdout.flush()

    def __enter__(self):
        self._t0 = time.time()
        self._mute.__enter__()
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._stop.set()
            self._thread.join(timeout=1)
        finally:
            self._mute.__exit__(exc_type, exc, tb)
        return False


_prompt_session = None


def prompt_text(prompt: str) -> str:
    global _prompt_session
    try:
        from prompt_toolkit import PromptSession  # type: ignore
        from prompt_toolkit.formatted_text import ANSI  # type: ignore
        if _prompt_session is None:
            _prompt_session = PromptSession()
        return _prompt_session.prompt(ANSI(prompt), mouse_support=False)
    except Exception:
        return input(prompt)


# ============================================================
# CONFIG + MEMORY + LAST ERROR
# ============================================================

SUPPORTED_API_MODELS = {
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini"],
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        APP_DIR.mkdir(parents=True, exist_ok=True)
        default = {
            "backend_mode": "auto",  # "local" | "api" | "auto"
            "ollama": {
                "host": "http://localhost:11434",
                "active_model_ask": "llama3.1:8b",
                "active_model_cmd": "llama3.1:8b",
            },
            "api": {
                "provider": "gemini",          # "gemini" | "openai"
                "active_model": "gemini-2.5-flash",
                "openai_api_key_env": "OPENAI_API_KEY",
                "gemini_api_key_env": "GEMINI_API_KEY",
            },
            "memory": {
                "enabled": True,
                "max_chars": 900,
            },
        }
        CONFIG_PATH.write_text(json.dumps(default, indent=2), encoding="utf-8")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def record_last_error(stage: str, backend: str, detail: str) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "time": _dt.datetime.now().isoformat(timespec="seconds"),
            "stage": stage,
            "backend": backend,
            "detail": (detail or "")[:8000],
        }
        LAST_ERROR_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_last_error() -> dict | None:
    try:
        if not LAST_ERROR_PATH.exists():
            return None
        d = json.loads(LAST_ERROR_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def load_memory(cfg: dict) -> dict:
    if not cfg.get("memory", {}).get("enabled", True):
        return {"enabled": False, "summary": ""}
    if not MEMORY_PATH.exists():
        return {"enabled": True, "summary": ""}
    try:
        d = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"enabled": True, "summary": ""}
        d.setdefault("enabled", True)
        d.setdefault("summary", "")
        return d
    except Exception:
        return {"enabled": True, "summary": ""}


def _soft_trim_summary(summary: str, max_chars: int) -> str:
    s = (summary or "").strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rstrip()

    # cari titik / tanda akhir kalimat dekat ujung biar tidak “kepotong kata”
    tail = cut[-180:]
    m = re.search(r"[.!?](?!.*[.!?])", tail)
    if m:
        pos = len(cut) - len(tail) + m.end()
        cut = cut[:pos].rstrip()

    # fallback: potong sampai spasi terakhir
    if len(cut) >= 40 and not re.search(r"[.!?]$", cut):
        sp = cut.rfind(" ")
        if sp >= 30:
            cut = cut[:sp].rstrip()

    if cut and cut[-1] not in ".!?":
        cut += "."
    return cut


def save_memory(summary: str, cfg: dict) -> None:
    if not cfg.get("memory", {}).get("enabled", True):
        return
    max_chars = int(cfg.get("memory", {}).get("max_chars", 900))
    s = _soft_trim_summary(summary, max_chars=max_chars)

    APP_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps({"enabled": True, "summary": s}, indent=2), encoding="utf-8")


def mem_context_text(cfg: dict) -> str:
    mem = load_memory(cfg)
    if not mem.get("enabled", True):
        return ""
    s = str(mem.get("summary") or "").strip()
    return f"Konteks singkat terakhir (agar aku nyambung): {s}" if s else ""


# ============================================================
# HTTP JSON helper
# ============================================================

def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 60, headers: dict | None = None) -> dict:
    data = None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = Request(url=url, data=data, method=method, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {ex.code}: {body}") from ex
    except TimeoutError as ex:
        raise RuntimeError("Timeout (server terlalu lama menjawab).") from ex
    except URLError as ex:
        raise RuntimeError(f"URLError: {ex.reason}") from ex


def looks_like_conn_refused(err: Exception) -> bool:
    s = str(err).lower()
    return ("errno 111" in s) or ("connection refused" in s)


# ============================================================
# LOCAL (Ollama)
# ============================================================

def ollama_host(cfg: dict) -> str:
    return str(cfg.get("ollama", {}).get("host") or "http://localhost:11434").strip()


def local_model_for(cfg: dict, kind: str) -> str:
    o = cfg.get("ollama", {})
    key = "active_model_ask" if kind == "ask" else "active_model_cmd"
    return str(o.get(key) or "").strip()


def ollama_list_models(host: str) -> list[str]:
    data = http_json("GET", f"{host}/api/tags", None, timeout=10)
    out: list[str] = []
    for m in data.get("models", []):
        name = m.get("name")
        if name:
            out.append(name)
    return out


def ollama_chat(host: str, model: str, messages: list[dict], timeout: int, num_predict: int) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "num_predict": int(num_predict),
            "num_ctx": 2048,
        },
    }
    data = http_json("POST", f"{host}/api/chat", payload, timeout=timeout)
    msg = data.get("message", {})
    return (msg.get("content") or "").strip()


# ============================================================
# API (Gemini + OpenAI)
# ============================================================

def backend_mode(cfg: dict) -> str:
    return (cfg.get("backend_mode") or "local").strip().lower()


def api_provider(cfg: dict) -> str:
    return (cfg.get("api", {}).get("provider") or "gemini").strip().lower()


def api_active_model_raw(cfg: dict) -> str:
    return str(cfg.get("api", {}).get("active_model") or "").strip()


def normalize_model_id(model: str) -> str:
    m = model.strip()
    return m[7:] if m.startswith("models/") else m


def api_model_display(cfg: dict) -> str:
    return normalize_model_id(api_active_model_raw(cfg) or "(unset)")


# --- Gemini

def gemini_key(cfg: dict) -> tuple[str, str]:
    env_name = str(cfg.get("api", {}).get("gemini_api_key_env") or "GEMINI_API_KEY")
    return (os.environ.get(env_name) or "").strip(), env_name


def gemini_model_id(cfg: dict) -> str:
    return normalize_model_id(api_active_model_raw(cfg) or "gemini-2.5-flash")


def gemini_build_payload(messages: list[dict], max_output_tokens: int, json_schema: dict | None = None) -> dict:
    system_texts: list[str] = []
    contents: list[dict] = []

    for m in messages:
        role = (m.get("role") or "").lower()
        content = str(m.get("content") or "")
        if not content:
            continue
        if role == "system":
            system_texts.append(content)
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": content}]})
        else:
            contents.append({"role": "model", "parts": [{"text": content}]})

    payload: dict = {
        "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
        "generationConfig": {
            "maxOutputTokens": int(max_output_tokens),
            "temperature": 0.7,
            "topP": 0.95,
        },
    }
    if system_texts:
        payload["system_instruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}

    if json_schema is not None:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        payload["generationConfig"]["responseSchema"] = json_schema

    return payload


def gemini_generate(cfg: dict, messages: list[dict], timeout: int, max_output_tokens: int, json_schema: dict | None = None) -> str:
    key, _env = gemini_key(cfg)
    model = gemini_model_id(cfg)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    payload = gemini_build_payload(messages, max_output_tokens=max_output_tokens, json_schema=json_schema)
    headers = {"x-goog-api-key": key}

    data = http_json("POST", url, payload, timeout=timeout, headers=headers)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Respons API kosong (tidak ada kandidat).")
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "".join(str(p.get("text") or "") for p in parts).strip()
    if not text:
        raise RuntimeError("Respons API kosong.")
    return text


def gemini_list_models(cfg: dict, timeout: int = 12) -> list[dict]:
    key, _env = gemini_key(cfg)
    if not key:
        return []
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    headers = {"x-goog-api-key": key}
    data = http_json("GET", url, None, timeout=timeout, headers=headers)
    models = data.get("models") or []
    return models if isinstance(models, list) else []


def gemini_model_support_summary(cfg: dict) -> tuple[bool, str]:
    raw = api_active_model_raw(cfg) or ""
    wanted_norm = "models/" + normalize_model_id(raw or "")
    if wanted_norm == "models/":
        return False, "api.active_model kosong."

    try:
        models = gemini_list_models(cfg, timeout=12)
    except Exception as ex:
        return False, f"Gagal ListModels: {ex}"

    if not models:
        return False, "ListModels kosong atau tidak bisa dibaca."

    hit = None
    for m in models:
        name = str(m.get("name") or "")
        if name == wanted_norm:
            hit = m
            break

    if not hit:
        sample: list[str] = []
        w = wanted_norm.replace("models/", "")
        for m in models:
            n = str(m.get("name") or "")
            if w and w in n:
                sample.append(n)
            if len(sample) >= 4:
                break
        if sample:
            return False, f"Model aktif tidak cocok. Mungkin maksudmu: {', '.join(sample)}"
        return False, "Model aktif tidak ditemukan di ListModels (nama tidak cocok)."

    methods = hit.get("supportedGenerationMethods") or []
    methods = methods if isinstance(methods, list) else []
    if "generateContent" not in methods:
        return False, f"Model terdeteksi tapi tidak mendukung generateContent. Methods: {methods}"
    return True, "Model terdeteksi dan mendukung generateContent."


# --- OpenAI

def openai_key(cfg: dict) -> tuple[str, str]:
    env_name = str(cfg.get("api", {}).get("openai_api_key_env") or "OPENAI_API_KEY")
    return (os.environ.get(env_name) or "").strip(), env_name


def openai_model_id(cfg: dict) -> str:
    return normalize_model_id(api_active_model_raw(cfg) or "gpt-4o-mini")


def openai_generate(cfg: dict, messages: list[dict], timeout: int, max_output_tokens: int, json_schema: dict | None = None) -> str:
    key, _env = openai_key(cfg)
    model = openai_model_id(cfg)

    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": int(max_output_tokens),
    }

    if json_schema is not None:
        payload["temperature"] = 0
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "cmd_schema",
                "strict": True,
                "schema": json_schema,
            },
        }

    data = http_json("POST", url, payload, timeout=timeout, headers=headers)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Respons OpenAI kosong (choices kosong).")
    msg = (choices[0].get("message") or {})
    text = str(msg.get("content") or "").strip()
    if not text:
        raise RuntimeError("Respons OpenAI kosong.")
    return text


def openai_model_check(cfg: dict, timeout: int = 10) -> tuple[bool, str]:
    key, _env = openai_key(cfg)
    if not key:
        return False, "OPENAI_API_KEY tidak terbaca."
    model = openai_model_id(cfg)
    url = f"https://api.openai.com/v1/models/{model}"
    headers = {"Authorization": f"Bearer {key}"}
    try:
        _ = http_json("GET", url, None, timeout=timeout, headers=headers)
        return True, "Model terdeteksi via endpoint /v1/models/{id}."
    except Exception as ex:
        return False, f"Gagal cek model: {ex}"


# --- API validator + dispatcher

def validate_api_config(cfg: dict) -> tuple[bool, str, str]:
    prov = api_provider(cfg)
    raw_model = api_active_model_raw(cfg)
    model = normalize_model_id(raw_model)

    if not prov:
        return False, "Konfigurasi API belum lengkap.", "Field api.provider kosong."
    if not model:
        return False, "Konfigurasi API belum lengkap.", "Field api.active_model kosong."

    if prov == "gemini" and re.match(r"^(gpt-|o\d|gpt_)", model):
        return False, "Provider Gemini aktif tapi model tidak cocok.", f"active_model terlihat seperti OpenAI: {raw_model}"

    if prov == "openai" and model.startswith("gemini-"):
        return False, "Provider OpenAI aktif tapi model tidak cocok.", f"active_model terlihat seperti Gemini: {raw_model}"

    if prov == "gemini":
        key, env_name = gemini_key(cfg)
        if not key:
            return False, "API key Gemini belum terbaca.", f"Env '{env_name}' kosong di proses yang menjalankan ai-term."
        return True, "API siap.", "Validasi provider/model/key lulus."

    if prov == "openai":
        key, env_name = openai_key(cfg)
        if not key:
            return False, "API key OpenAI belum terbaca.", f"Env '{env_name}' kosong di proses yang menjalankan ai-term."
        return True, "API siap.", "Validasi provider/model/key lulus."

    return False, "Provider API belum didukung.", f"provider='{prov}' belum ada handler-nya."


def api_generate(cfg: dict, messages: list[dict], timeout: int, max_output_tokens: int, json_schema: dict | None = None) -> str:
    prov = api_provider(cfg)
    if prov == "gemini":
        return gemini_generate(cfg, messages, timeout=timeout, max_output_tokens=max_output_tokens, json_schema=json_schema)
    if prov == "openai":
        return openai_generate(cfg, messages, timeout=timeout, max_output_tokens=max_output_tokens, json_schema=json_schema)
    raise RuntimeError(f"Provider API tidak didukung: {prov}")


# ============================================================
# ROUTING (auto/api/local)
# ============================================================

def route_for(cfg: dict, want: str) -> tuple[str, str]:
    mode = backend_mode(cfg)

    def api_label() -> str:
        prov = (api_provider(cfg) or "api").strip().upper()
        model = api_model_display(cfg)
        return f"{tag('API', c_yellow())} -> {tag(prov, c_green())} • {model}"

    def local_label() -> str:
        return f"{tag('LOCAL', c_green())} • {local_model_for(cfg, want) or '(unset)'}"

    if mode == "local":
        return "local", local_label()

    if mode == "auto":
        ok, _, _ = validate_api_config(cfg)
        if ok:
            return "api", f"{tag('AUTO', c_yellow())} -> {api_label()}"
        return "local", f"{tag('AUTO', c_yellow())} -> {local_label()}"

    # mode == "api"
    return "api", api_label()


# ============================================================
# PROMPTS + TOKEN LIMITS
# ============================================================

def prompt_local_oneshot() -> str:
    return (
        "Aku asisten Linux Fedora di terminal. Aku menjawab dalam Bahasa Indonesia.\n"
        "Aku harus ringkas, langsung inti, dan tetap menjawab tuntas.\n"
        "Aku menulis jawaban sebagai SATU paragraf saja (tanpa newline, tanpa bullet)."
    )


def prompt_api_oneshot() -> str:
    return (
        "Aku asisten Linux Fedora di terminal. Aku menjawab dalam Bahasa Indonesia.\n"
        "Aku santai, jelas, tidak kaku.\n"
        "Aturan halus: usahakan ringkas (sekitar maksimal 5 baris terminal), fokus inti."
    )


def local_tokens_for_ask(text: str) -> int:
    wc = len(text.split())
    if wc <= 4:
        return 24
    if wc <= 14:
        return 36
    return 48


def local_tokens_for_chat(user: str) -> int:
    wc = len(user.split())
    if wc <= 4:
        return 40
    if wc <= 18:
        return 78
    return 120


def local_tokens_for_cmd() -> int:
    return 64


def api_tokens_oneshot() -> int:
    cols, _ = term_size()
    return 560 if cols >= 100 else 480


def api_tokens_chat() -> int:
    cols, _ = term_size()
    return 1600 if cols >= 100 else 1200


def force_single_paragraph(text: str) -> str:
    s = re.sub(r"\s*\n+\s*", " ", text.strip())
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


# ============================================================
# SAFETY & EXECUTION
# ============================================================

def is_denied(cmd: str) -> bool:
    c = cmd.strip()
    if not c or "\n" in c or "\r" in c:
        return True
    for bad in DENY_SUBSTRINGS:
        if bad in c:
            return True
    return False


def confirm(prompt: str) -> bool:
    ans = prompt_text(prompt).strip().lower()
    return ans == "y"


def run_command(cmd: str) -> int:
    p = subprocess.run(cmd, shell=True)
    return int(p.returncode)


def copy_to_clipboard(text: str) -> bool:
    if not text.strip():
        return False
    wl = which("wl-copy")
    if wl:
        try:
            p = subprocess.Popen([wl], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return p.returncode == 0
        except Exception:
            return False
    xclip = which("xclip")
    if xclip:
        try:
            p = subprocess.Popen([xclip, "-selection", "clipboard"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return p.returncode == 0
        except Exception:
            return False
    xsel = which("xsel")
    if xsel:
        try:
            p = subprocess.Popen([xsel, "--clipboard", "--input"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return p.returncode == 0
        except Exception:
            return False
    return False


# ============================================================
# REPO/WORKSPACE helpers (dipakai ai status)
# ============================================================

def find_repo_root(start: Path) -> Path | None:
    p = start.resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return parent
    return None


def fmt_path(p: Path) -> str:
    try:
        if p.is_symlink():
            return f"{p} -> {p.resolve()}"
        return str(p)
    except Exception:
        return str(p)
