"""Check backend status, connectivity, and configs."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from shutil import which

# === IMPORT LAMA TETAP AMAN KARENA PAKAI ABSOLUTE PATH ===
from ai_logic.common import (
    CONFIG_PATH,
    MEMORY_PATH,
    MON_HISTORY_PATH,
    LAST_ERROR_PATH,
    LOGIC_PATH,
    FISH_DIR,
    FISH_FUNCS_DIR,
    api_active_model_raw,
    api_provider,
    backend_mode,
    read_last_error,
)
from ai_logic.ui.ansi import (
    prompt_text,
    tag,
    wrap,
    term_size,
    c_cyan,
    c_dim,
    c_reset,
)

from ai_logic.backends.local_ollama import ollama_host, local_model_for, readiness as local_readiness
from ai_logic.backends.api_gemini import (
    gemini_key,
    validate_api_config,
    model_support_summary,
    generate as gemini_generate,
)

# --- ENTRY POINT BARU: Handle Argv Style ---
def handle(argv: list[str], cfg: dict) -> int:
    """
    Subcommand: ai status [flags]
    Flags:
      --last-error / last-error : Tampilkan error log terakhir.
      --info / info             : Tampilkan info mode singkat.
      (default)                 : Full status check.
    """
    # Deteksi flag di argumen untuk mendukung fitur info/last-error via status subcommand
    # Contoh: ai status --last-error
    if any(x in argv for x in ("--last-error", "last-error")):
        return run_last_error(cfg)

    if any(x in argv for x in ("--info", "info")):
        return run_info(cfg)

    # Default
    return run_status(cfg)


# --- CODE IMPLEMENTASI ASLI (DIPERTAHANKAN) ---

def _find_repo_root(start: Path) -> Path | None:
    p = start.resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists():
            return parent
    return None

def _fmt_path(p: Path) -> str:
    try:
        if p.is_symlink():
            return f"{p} -> {p.resolve()}"
        return str(p)
    except Exception:
        return str(p)

def run_info(cfg: dict) -> int:
    cols, _ = term_size()
    mode = backend_mode(cfg)
    prov = api_provider(cfg)
    sys.stdout = sys.stdout  # keep mypy calm
    print(wrap(f"{tag('INFO', c_cyan())} Mode: {mode.upper()} | API: {prov.upper()} | Model: {api_active_model_raw(cfg) or '(unset)'}", width=min(cols, 120)))
    return 0

def run_last_error(cfg: dict) -> int:
    cols, _ = term_size()
    last = read_last_error()
    if not last:
        print(wrap("Tidak ada error terakhir tercatat.", width=min(cols, 120)))
        return 0
    print(wrap(f"{tag('LAST ERROR', c_cyan())} {last.get('time','')}", width=min(cols, 120)))
    print(wrap(f"stage : {last.get('stage','')}", width=min(cols, 120)))
    print(wrap(f"backend: {last.get('backend','')}", width=min(cols, 120)))
    print(wrap(str(last.get('detail',''))[:6000], width=min(cols, 120)))
    return 0

def run_status(cfg: dict) -> int:
    cols, _ = term_size()

    def h(title: str) -> None:
        print(f"\n{tag(title, c_cyan())}")

    def kv(k: str, v: str) -> None:
        print(wrap(f"- {k:<16}: {v}", width=min(cols, 120)))

    # 0) Workspace
    repo_root = _find_repo_root(Path(__file__))
    workspace = repo_root

    h("1) Lokasi kerja & file penting")
    kv("Workspace", str(workspace) if workspace else "(tidak terdeteksi otomatis)")
    kv("Logic", _fmt_path(LOGIC_PATH))
    kv("Config", _fmt_path(CONFIG_PATH))
    kv("Memory", _fmt_path(MEMORY_PATH))
    kv("Mon history", _fmt_path(MON_HISTORY_PATH))
    kv("Last error", _fmt_path(LAST_ERROR_PATH))
    kv("Fish config", _fmt_path(FISH_DIR))
    kv("Fish funcs", _fmt_path(FISH_FUNCS_DIR))

    h("1.1) Command untuk buka workspace (manual)")
    if workspace:
        kv("VS Code", f'code "{workspace}" --verbose')
    else:
        kv("VS Code", 'code "/path/ke/workspace-kamu" --verbose')

    h("2) Setting aktif (config.json)")
    kv("backend_mode", backend_mode(cfg))
    kv("api.provider", api_provider(cfg))
    kv("api.model", api_active_model_raw(cfg) or "(kosong)")
    kv("local.ask", local_model_for(cfg, "ask") or "(kosong)")
    kv("local.cmd", local_model_for(cfg, "cmd") or "(kosong)")

    h("2.1) Dependency opsional")
    try:
        import prompt_toolkit  # noqa: F401
        kv("prompt_toolkit", "TERPASANG ✅")
    except Exception:
        kv("prompt_toolkit", "BELUM ❌")

    wl = which("wl-copy")
    xclip = which("xclip")
    xsel = which("xsel")
    if wl:
        kv("clipboard", "wl-copy ✅ (Wayland)")
    elif xclip:
        kv("clipboard", "xclip ✅ (X11)")
    elif xsel:
        kv("clipboard", "xsel ✅ (X11)")
    else:
        kv("clipboard", "tidak ada (opsional)")

    kv("code (CLI)", "ADA ✅" if which("code") else "TIDAK ❌")

    # 3) LOCAL readiness
    h("3) Kesiapan server AI LOCAL (Ollama)")
    host = ollama_host(cfg)
    kv("host", host)
    local_res = local_readiness(cfg)
    kv("status", "READY ✅" if local_res.get("ok") else "NOT READY ❌")
    kv("detail", str(local_res.get("detail") or local_res.get("catatan") or ""))

    # 4) API readiness
    h("4) Kesiapan API")
    prov = api_provider(cfg)
    ok_cfg, note_cfg, detail_cfg = validate_api_config(cfg)

    if prov == "gemini":
        key, env_name = gemini_key(cfg)
        kv("env var", f"{env_name} ({'YA' if key else 'TIDAK'})")

    kv("status", "READY ✅" if ok_cfg else "NOT READY ❌")

    api_model_ok = False
    if ok_cfg and prov == "gemini":
        api_model_ok, api_model_detail = model_support_summary(cfg)
        kv("model check", "OK ✅" if api_model_ok else "FAIL ❌")
        kv("model detail", api_model_detail)

    # 5) Self-test (Modified to use generic input/output flow for snippet brevity)
    h("5) Pengujian runtime (self-test)")
    want_api_test = False
    if ok_cfg and prov == "gemini" and api_model_ok:
        print("\nJalankan tes cepat API sekarang? (hemat kuota)")
        print("  - ketik y  : jalankan tes")
        print("  - selain itu: lewati")
        try:
            ans = prompt_text("> ").strip().lower()
            want_api_test = (ans == "y")
        except KeyboardInterrupt:
            print("")
            want_api_test = False

    api_test_ok = False
    api_test_detail = "Dilewati (user memilih skip)."
    if want_api_test and ok_cfg and prov == "gemini" and api_model_ok:
        try:
            msgs = [
                {"role": "system", "content": "Jawab 1 huruf saja: Y"},
                {"role": "user", "content": "ping"},
            ]
            _ = gemini_generate(cfg, msgs, timeout=10, max_output_tokens=8)
            api_test_ok = True
            api_test_detail = "API menjawab (uji cepat)."
        except Exception as ex:
            s = str(ex)
            m = re.search(r"HTTP\s+(\d+)", s)
            if m:
                code = m.group(1)
                meaning = {
                    "401": "Unauthorized (key salah/ditolak).",
                    "403": "Permission denied (akses dibatasi).",
                    "404": "Model/endpoint tidak cocok.",
                    "429": "Kena rate limit / kuota habis.",
                    "500": "Server error (coba lagi).",
                    "503": "Service unavailable (coba lagi).",
                }.get(code, "HTTP error.")
                api_test_detail = f"HTTP {code} • {meaning}"
            else:
                api_test_detail = f"Gagal uji: {type(ex).__name__}"

    kv("api test", "PASS ✅" if api_test_ok else "FAIL ❌" if want_api_test else "SKIP ⏭")
    kv("api test detail", api_test_detail)

    # 6) Kesimpulan
    h("6) Kesimpulan")
    mode = backend_mode(cfg)

    lines: list[str] = []
    lines.append(f"Mode aktif sekarang: {mode.upper()}.")

    if mode == "local":
        if local_res.get("ok"):
            lines.append("Aku akan selalu pakai LOCAL.")
            lines.append("Kalau respon terasa lama, biasanya model sedang loading atau terlalu berat untuk CPU.")
        else:
            lines.append("LOCAL dipilih, tapi server belum siap. ask/cmd kemungkinan gagal sampai Ollama hidup dan model terbaca.")

    elif mode == "api":
        if ok_cfg and prov == "gemini" and api_model_ok:
            lines.append("Aku akan coba API dulu setiap kali kamu pakai ask/cmd.")
            lines.append("Kalau API error (limit/kuota/model), aku akan fallback ke LOCAL supaya tool tetap jalan.")
        else:
            lines.append("API dipilih, tapi konfigurasi/model belum siap.")
            if local_res.get("ok"):
                lines.append("Jadi di runtime aku akan sering fallback ke LOCAL.")
            else:
                lines.append("Dan karena LOCAL juga belum siap, ask/cmd berpotensi gagal.")

    else:  # auto
        if ok_cfg and prov == "gemini" and api_model_ok:
            lines.append("AUTO akan memakai API selama siap.")
            lines.append("Jika API error (mis. 429/limit), AUTO akan pindah ke LOCAL tanpa drama.")
        else:
            lines.append("AUTO melihat API belum siap, jadi target utamanya LOCAL.")
            if local_res.get("ok"):
                lines.append("Tool tetap bisa dipakai via LOCAL.")
            else:
                lines.append("Tapi LOCAL juga belum siap, jadi kamu perlu betulkan Ollama dulu.")

    print(wrap(" ".join(lines), width=min(cols, 120)))

    # 7) Last error ringkas
    h("7) Last error (ringkas)")
    last = read_last_error()
    if not last:
        print(wrap("Tidak ada error terakhir tercatat.", width=min(cols, 120)))
    else:
        kv("time", str(last.get("time") or ""))
        kv("stage", str(last.get("stage") or ""))
        kv("backend", str(last.get("backend") or ""))
        d = str(last.get("detail") or "")
        d1 = d.splitlines()[0] if d else ""
        kv("ringkas", d1[:180] + ("..." if len(d1) > 180 else ""))

    # 8) Lokasi detail
    h("8) Lokasi detail (manual)")
    kv("Detail error", str(LAST_ERROR_PATH))
    if workspace:
        kv("Buka workspace", f'code "{workspace}" --verbose')
    kv("Buka error file", f'code "{LAST_ERROR_PATH}"')
    kv("Lihat cepat", f'cat "{LAST_ERROR_PATH}"')

    return 0
