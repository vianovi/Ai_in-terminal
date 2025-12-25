from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path
from shutil import which

from ai_logic.common import (
    CONFIG_PATH,
    FISH_DIR,
    FISH_FUNCS_DIR,
    LAST_ERROR_PATH,
    LOGIC_PATH,
    MEMORY_PATH,
    api_model_display,
    api_provider,
    backend_mode,
    c_cyan,
    c_reset,
    find_repo_root,
    fmt_path,
    gemini_key,
    gemini_model_support_summary,
    load_config,
    ollama_host,
    ollama_list_models,
    ollama_chat,
    local_model_for,
    looks_like_conn_refused,
    openai_key,
    openai_model_check,
    print_info,
    prompt_text,
    read_last_error,
    tag,
    term_size,
    validate_api_config,
    wrap,
    record_last_error,
    api_generate,
)


def mode_ai(text: str, cfg: dict) -> int:
    raw = (text or "").strip()

    def help_ai() -> int:
        print('Gunakan:')
        print('  ai "--help"')
        print('  ai "status"                (alias cepat)')
        print('  ai "run status"            (router)')
        print('  ai "gitx ..."              (stub/akan diisi)')
        print('  ai "ghx ..."               (stub/akan diisi)')
        print('  ai "mon ..."               (stub/akan diisi)')
        return 0

    # kalau user cuma ketik: ai
    if not raw:
        return help_ai()

    # tokenizing aman (support quoted args)
    try:
        args = shlex.split(raw)
    except Exception:
        # fallback sederhana kalau ada input aneh
        args = raw.split()

    if not args:
        return help_ai()

    head = args[0].strip().lower()

    # help
    if head in ("--help", "-h", "help"):
        return help_ai()

    # alias backward-compatible
    if head in ("status", "debug"):
        return ai_status(cfg)

    # support gaya "ai:run status" kalau suatu saat kamu bikin alias
    if head.startswith("ai:"):
        head = head[3:]

    # subcommand: run
    if head == "run":
        if len(args) < 2:
            print('Gunakan: ai "run status"')
            return 2
        sub = args[1].strip().lower()

        if sub in ("status", "debug", "diag", "diagnose"):
            return ai_status(cfg)

        print(f'Gunakan: ai "run status" (subcommand tidak dikenal: {args[1]!r})')
        return 2

    # subcommand: gitx / ghx / mon (dispatch ke modul lain, tapi aman kalau belum ada)
    if head in ("gitx", "ghx", "mon"):
        rest = args[1:]

        try:
            if head == "gitx":
                from ai_logic.gitx import handle_gitx  # type: ignore
                return int(handle_gitx(rest, cfg))
            if head == "ghx":
                from ai_logic.ghx import handle_ghx  # type: ignore
                return int(handle_ghx(rest, cfg))
            if head == "mon":
                from ai_logic.mon import handle_mon  # type: ignore
                return int(handle_mon(rest, cfg))
        except ImportError:
            print(f'Router "{head}" belum diimplementasi. (file ai_logic/{head}.py ada, tapi handler belum ada)')
            return 0
        except Exception as ex:
            print(f'Router "{head}" crash: {type(ex).__name__}: {ex}')
            return 2

    print('Gunakan: ai "status" atau ai "--help"')
    return 2


def ai_status(cfg: dict) -> int:
    cols, _ = term_size()

    def h(title: str) -> None:
        sys.stdout.write(f"\n{tag(title, c_cyan())}\n")
        sys.stdout.flush()

    def kv(k: str, v: str) -> None:
        sys.stdout.write(wrap(f"- {k:<16}: {v}", width=min(cols, 120)) + "\n")
        sys.stdout.flush()

    repo_root = find_repo_root(Path(__file__))
    fallback_ws = Path.home() / "Documents" / "MyPlaygraund" / "AI_in-terminal"
    workspace = repo_root if repo_root else (fallback_ws if fallback_ws.exists() else None)

    # 1) Lokasi
    h("1) Lokasi kerja & file penting")
    kv("Workspace", str(workspace) if workspace else "(tidak terdeteksi otomatis)")
    kv("Logic", fmt_path(LOGIC_PATH))
    kv("Config", fmt_path(CONFIG_PATH))
    kv("Memory", fmt_path(MEMORY_PATH))
    kv("Last error", fmt_path(LAST_ERROR_PATH))
    kv("Fish config", fmt_path(FISH_DIR))
    kv("Fish funcs", fmt_path(FISH_FUNCS_DIR))

    # 1.1) Command buka workspace
    h("1.1) Command untuk buka workspace (manual)")
    if workspace:
        kv("VS Code", f'code "{workspace}" --verbose')
    else:
        kv("VS Code", 'code "/path/ke/workspace-kamu" --verbose')

    # 2) Config aktif
    h("2) Setting aktif (config.json)")
    kv("backend_mode", backend_mode(cfg))
    kv("api.provider", api_provider(cfg))
    kv("api.model", api_model_display(cfg))
    kv("local.ask", local_model_for(cfg, "ask") or "(kosong)")
    kv("local.cmd", local_model_for(cfg, "cmd") or "(kosong)")

    # 2.1) Dependency opsional
    h("2.1) Dependency opsional (tidak wajib)")
    try:
        import prompt_toolkit  # type: ignore  # noqa: F401
        kv("prompt_toolkit", "TERPASANG ✅ (input editor enak)")
    except Exception:
        kv("prompt_toolkit", "BELUM ❌ (fallback input biasa)")
        kv("saran dnf", "sudo dnf install -y python3-prompt-toolkit")

    # (opsional tapi sering kepakai)
    kv("code (VS Code CLI)", "ADA ✅" if which("code") else "TIDAK ❌ (opsional)")
    if which("wl-copy"):
        kv("clipboard", "wl-copy ✅ (Wayland)")
    elif which("xclip"):
        kv("clipboard", "xclip ✅ (X11)")
    elif which("xsel"):
        kv("clipboard", "xsel ✅ (X11)")
    else:
        kv("clipboard", "tidak ada (opsional)")
        kv("saran dnf", "sudo dnf install -y wl-clipboard  # atau xclip/xsel")

    # 3) Kesiapan LOCAL (server + model list)
    h("3) Kesiapan server AI LOCAL (Ollama)")
    host = ollama_host(cfg)
    sel_ask = local_model_for(cfg, "ask")
    sel_cmd = local_model_for(cfg, "cmd")

    local_ok = False
    local_detail = ""
    local_models: list[str] = []

    try:
        local_models = ollama_list_models(host)
        if local_models:
            selected = {m.strip() for m in (sel_ask, sel_cmd) if m.strip()}
            available = set(local_models)
            if selected and not selected.issubset(available):
                local_ok = False
                local_detail = f"Server OK, tapi model aktif tidak ketemu. Aktif: {sorted(selected)}"
            else:
                local_ok = True
                local_detail = f"Server OK, model terdeteksi: {len(local_models)}"
        else:
            local_ok = False
            local_detail = "Server OK, tapi belum ada model terdeteksi."
    except Exception as ex:
        local_ok = False
        if looks_like_conn_refused(ex):
            local_detail = "Tidak bisa konek ke Ollama (connection refused / service mati)."
        else:
            local_detail = f"Gagal cek Ollama: {ex}"

    kv("host", host)
    kv("status", "READY ✅" if local_ok else "NOT READY ❌")
    kv("detail", local_detail)
    if local_models:
        sys.stdout.write("  Model terdeteksi (ringkas):\n")
        for m in local_models[:12]:
            sys.stdout.write(f"   - {m}\n")
        if len(local_models) > 12:
            sys.stdout.write("   - ...\n")
        sys.stdout.flush()

    # 4) Kesiapan API (konfigurasi + model check)
    h("4) Kesiapan API (konfigurasi + koneksi ringan)")
    prov = api_provider(cfg)
    ok_cfg, note_cfg, detail_cfg = validate_api_config(cfg)

    if prov == "gemini":
        key, env_name = gemini_key(cfg)
        kv("env var", f"{env_name} (terdeteksi: {'YA ✅' if key else 'TIDAK ❌'})")
    elif prov == "openai":
        key, env_name = openai_key(cfg)
        kv("env var", f"{env_name} (terdeteksi: {'YA ✅' if key else 'TIDAK ❌'})")
    else:
        kv("env var", "(provider belum didukung)")

    kv("status", "READY ✅" if ok_cfg else "NOT READY ❌")
    kv("detail", detail_cfg if ok_cfg else f"{note_cfg} • {detail_cfg}")

    api_model_ok = False
    api_model_detail = ""
    if ok_cfg and prov == "gemini":
        api_model_ok, api_model_detail = gemini_model_support_summary(cfg)
        kv("model check", "OK ✅" if api_model_ok else "FAIL ❌")
        kv("model detail", api_model_detail)
    elif ok_cfg and prov == "openai":
        api_model_ok, api_model_detail = openai_model_check(cfg)
        kv("model check", "OK ✅" if api_model_ok else "FAIL ❌")
        kv("model detail", api_model_detail)

    # tanya: tes API?
    want_api_test = False
    if ok_cfg and api_model_ok:
        sys.stdout.write("\nJalankan tes cepat API sekarang? (hemat kuota)\n")
        sys.stdout.write("  - ketik y  : jalankan tes\n")
        sys.stdout.write("  - selain itu: lewati\n")
        sys.stdout.flush()
        try:
            ans = prompt_text("> ").strip().lower()
            want_api_test = (ans == "y")
        except KeyboardInterrupt:
            sys.stdout.write("\n")
            want_api_test = False

    # 5) Self-test runtime
    h("5) Pengujian runtime (self-test)")

    # 5.1 LOCAL (selalu jalan kalau ready)
    local_test_ok = False
    local_test_detail = ""

    if local_ok and local_models:
        test_model = sel_ask or local_models[0]
        try:
            _ = ollama_chat(host, test_model, [{"role": "user", "content": "Y"}], timeout=45, num_predict=2)
            local_test_ok = True
            local_test_detail = "Server menjawab (uji ringan)."
        except Exception as ex:
            if "timeout" in str(ex).lower():
                try:
                    _ = ollama_chat(host, test_model, [{"role": "user", "content": "Y"}], timeout=90, num_predict=2)
                    local_test_ok = True
                    local_test_detail = "Server menjawab setelah retry (cold start)."
                except Exception:
                    local_test_ok = False
                    local_test_detail = "Timeout juga setelah retry (model masih loading/terlalu berat)."
            else:
                local_test_ok = False
                local_test_detail = f"Gagal uji ringan: {type(ex).__name__}"
    else:
        local_test_ok = False
        local_test_detail = "Dilewati (server belum ready)."

    kv("local test", "PASS ✅" if local_test_ok else "FAIL ❌")
    kv("local test detail", local_test_detail)

    # 5.2 API (opsional)
    api_test_ok = False
    api_test_detail = "Dilewati (user memilih skip)."
    if want_api_test and ok_cfg and api_model_ok:
        try:
            msgs = [
                {"role": "system", "content": "Jawab 1 huruf saja: Y"},
                {"role": "user", "content": "ping"},
            ]
            _ = api_generate(cfg, msgs, timeout=10, max_output_tokens=8)
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
            record_last_error("api_selftest", "api", s)

    kv("api test", "PASS ✅" if api_test_ok else "FAIL ❌" if want_api_test else "SKIP ⏭")
    kv("api test detail", api_test_detail)

    # 6) Kesimpulan (lebih “jelas”)
    h("6) Kesimpulan")
    mode = backend_mode(cfg)
    lines: list[str] = []
    lines.append(f"Mode aktif sekarang: {mode.upper()}.")

    if mode == "local":
        if local_ok:
            lines.append("ask/cmd akan selalu pakai LOCAL.")
            if not local_test_ok:
                lines.append("Namun runtime test LOCAL gagal: indikasi umum = cold start / model berat / CPU penuh.")
        else:
            lines.append("LOCAL dipilih, tapi server belum siap. ask/cmd kemungkinan gagal sampai Ollama hidup dan model terbaca.")

    elif mode == "api":
        if ok_cfg and api_model_ok:
            lines.append("ask/cmd akan mencoba API dulu.")
            lines.append("Jika API error (mis. 429/limit), tool bisa fallback ke LOCAL bila mode kamu mengizinkan (auto).")
        else:
            lines.append("API dipilih, tapi konfigurasi/model belum siap.")
            lines.append("Kalau LOCAL ready, kamu akan tetap bisa kerja dengan ganti mode ke AUTO/LOCAL.")

    else:  # auto
        if ok_cfg and api_model_ok:
            lines.append("AUTO akan memakai API selama siap.")
            lines.append("Jika API error (mis. 429/limit), AUTO akan pindah ke LOCAL otomatis.")
            if local_ok and not local_test_ok:
                lines.append("Catatan: LOCAL terlihat ready, tapi runtime test gagal => fallback bisa terasa lambat/timeout.")
        else:
            lines.append("AUTO melihat API belum siap, jadi target utamanya LOCAL.")
            if local_ok:
                lines.append("Tool tetap bisa dipakai via LOCAL.")
            else:
                lines.append("Tapi LOCAL juga belum siap, jadi kamu perlu betulkan Ollama dulu.")

    sys.stdout.write(wrap(" ".join(lines), width=min(cols, 120)) + "\n")
    sys.stdout.flush()

    # 7) Last error ringkas
    h("7) Last error (ringkas)")
    last = read_last_error()
    if not last:
        sys.stdout.write(wrap("Tidak ada error terakhir tercatat.", width=min(cols, 120)) + "\n")
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

def handle_ai(argv: list[str], cfg: dict) -> int:
    text = " ".join(argv).strip()
    return mode_ai(text, cfg)
