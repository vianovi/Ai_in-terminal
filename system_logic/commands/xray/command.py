"""
commands/xray/command.py
========================
Entry point dan dispatcher utama untuk command `xray`.

Aturan isolasi:
    BOLEH import dari luar xray/:
        - system_logic.core.last_error  → record_last_error
        - system_logic.core.paths       → XRAY_EXPORT_DIR
        - system_logic.core.config      → load_config (via ai_helper)
    TIDAK BOLEH import dari luar xray/ selain di atas.
    Semua UI via xray/ui.py.
    Semua AI via xray/ai_helper.py.

Entry point:
    handle(argv, cfg) -> int

Alur eksekusi:
    1. Parse argv → modul + subcommand + target + flags.
    2. Dispatch ke handler modul yang sesuai.
    3. Modul return dict hasil scan.
    4. ui.py render hasil ke terminal.
    5. Jika --export, simpan ke file JSON + TXT.
    6. Jika --ai, kirim ke ai_helper.interpret() lalu render.

Flags global:
    --pretty   Output colorful dengan tabel (default sudah pretty).
    --export   Simpan hasil ke file JSON + TXT di XRAY_EXPORT_DIR.
    --ai       Aktifkan AI interpreter — analisis hasil dalam BI.

Exit codes:
    0   = sukses
    1   = error saat scan (target tidak bisa diakses, dll)
    2   = error argumen / subcommand tidak dikenal
    130 = Ctrl+C / KeyboardInterrupt
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from system_logic.core.last_error import record_last_error
from system_logic.core.paths import XRAY_EXPORT_DIR

from system_logic.commands.xray import ui
from system_logic.commands.xray.ai_helper import interpret


# ============================================================
# [1] Help strings
# ============================================================

_HELP_ROOT = [
    f"{ui.tag('XRAY', ui.c_magenta())} — Digital Investigation Tool 🔬",
    "",
    "USAGE:",
    "  xray <modul> <subcommand> <target> [--flags]",
    "",
    "MODULS:",
    "  osint    Open Source Intelligence (ip, domain, email, web, phone)",
    "  steg     Steganography Inspector  (detect, extract, embed, meta)",
    "  file     File Forensics           (analyze, hash, strings, entropy)",
    "  net      Network Inspector        (scan, monitor, arp, trace, lookup)",
    "",
    "GLOBAL FLAGS:",
    "  --export   Simpan hasil ke file JSON + TXT",
    "  --ai       Aktifkan AI interpreter untuk analisis hasil",
    "  --help     Tampilkan halaman ini",
    "",
    "CONTOH:",
    "  xray osint ip 8.8.8.8",
    "  xray osint phone +6281234567890 --ai",
    "  xray steg detect foto.png",
    "  xray file analyze suspicious.pdf --export",
    "  xray net scan 192.168.1.1",
    "",
    "  Gunakan: xray <modul> --help untuk detail tiap modul.",
]

_HELP_OSINT = [
    f"{ui.tag('XRAY', ui.c_magenta())} {ui.tag('OSINT', ui.c_cyan())} — Open Source Intelligence",
    "",
    "SUBCOMMANDS:",
    "  ip <address>      IP geolocation + ISP + ASN info",
    "  domain <domain>   Domain enumeration + WHOIS + DNS records",
    "  email <email>     Cek data breach history (HaveIBeenPwned)",
    "  web <url>         HTTP headers + tech stack + security headers",
    "  phone <number>    Carrier, validasi format, reputasi spam",
    "",
    "CONTOH:",
    "  xray osint ip 8.8.8.8",
    "  xray osint ip 8.8.8.8 --ai",
    "  xray osint domain google.com --export",
    "  xray osint email user@example.com",
    "  xray osint web https://example.com",
    "  xray osint phone +6281234567890 --ai",
]

_HELP_STEG = [
    f"{ui.tag('XRAY', ui.c_magenta())} {ui.tag('STEG', ui.c_cyan())} — Steganography Inspector",
    "",
    "SUBCOMMANDS:",
    "  detect <file>              Deteksi hidden message via LSB analysis",
    "  extract <file>             Ekstrak pesan tersembunyi dari file",
    "  embed <file> <message>     Sisipkan pesan ke dalam gambar",
    "  meta <file>                Analisis metadata / EXIF lengkap",
    "",
    "FORMAT YANG DIDUKUNG: PNG, JPG, BMP",
    "",
    "CONTOH:",
    "  xray steg detect foto.png",
    "  xray steg extract foto.png --ai",
    "  xray steg embed foto.png \"pesan rahasia\"",
    "  xray steg meta foto.jpg",
]

_HELP_FILE = [
    f"{ui.tag('XRAY', ui.c_magenta())} {ui.tag('FILE', ui.c_cyan())} — File Forensics",
    "",
    "SUBCOMMANDS:",
    "  analyze <file>    Analisis lengkap: tipe asli, hash, entropy",
    "  hash <file>       Generate MD5 + SHA1 + SHA256",
    "  strings <file>    Ekstrak readable strings dari binary",
    "  entropy <file>    Cek apakah file dienkripsi / di-pack",
    "",
    "CONTOH:",
    "  xray file analyze suspicious.pdf",
    "  xray file hash document.pdf --export",
    "  xray file strings binary.exe --ai",
    "  xray file entropy packed.bin",
]

_HELP_NET = [
    f"{ui.tag('XRAY', ui.c_magenta())} {ui.tag('NET', ui.c_cyan())} — Network Inspector",
    "",
    "SUBCOMMANDS:",
    "  scan <host>       Port scan + service detection (1-1024)",
    "  monitor           Real-time traffic stats per interface",
    "  arp               Deteksi device aktif di jaringan lokal",
    "  trace <host>      Traceroute visual ke target",
    "  lookup <target>   DNS + WHOIS lookup",
    "",
    "CONTOH:",
    "  xray net scan 192.168.1.1",
    "  xray net monitor",
    "  xray net arp",
    "  xray net trace google.com --ai",
    "  xray net lookup example.com",
]


# ============================================================
# [2] Flag parser
# ============================================================

def _parse_flags(args: list[str]) -> tuple[list[str], dict]:
    """
    Pisahkan positional args dari flags (--flag).

    Args:
        args: Raw argv setelah nama modul.

    Returns:
        Tuple (positional_args, flags_dict).
        flags_dict keys: 'export', 'ai'.
    """
    positional: list[str] = []
    flags = {"export": False, "ai": False}

    for a in args:
        if a == "--export":
            flags["export"] = True
        elif a == "--ai":
            flags["ai"] = True
        elif a in ("--help", "-h", "help"):
            # Treat help as positional agar handler bisa cek
            positional.insert(0, "--help")
        elif a.startswith("--"):
            # Unknown flag — abaikan dengan warning
            ui.print_xray_warn(f"Flag tidak dikenal diabaikan: {a}")
        else:
            positional.append(a)

    return positional, flags


# ============================================================
# [3] Output pipeline (render + export + AI)
# ============================================================

def _render_result(result: dict, flags: dict, cfg: dict, module: str) -> None:
    """
    Pipeline output setelah scan selesai:
        1. Render header + result block ke terminal.
        2. Export ke file jika --export.
        3. Kirim ke AI jika --ai.

    Args:
        result : Dict hasil scan dari modul.
        flags  : Dict flags {'export': bool, 'ai': bool}.
        cfg    : Config dict dari framework.
        module : Nama modul untuk AI system prompt.
    """
    # --- Render ke terminal ---
    ui.print_result_block(result)
    ui.print_flag_warnings(result.get("flags", []))

    # --- Export ---
    if flags.get("export"):
        _do_export(result, module)

    # --- AI interpretation ---
    if flags.get("ai"):
        _do_ai(result, cfg=cfg, module=module)


def _do_export(result: dict, module: str) -> None:
    """
    Simpan hasil scan ke file JSON dan TXT di XRAY_EXPORT_DIR.

    Args:
        result : Dict hasil scan.
        module : Nama modul (dipakai sebagai prefix filename).
    """
    try:
        export_dir = Path(XRAY_EXPORT_DIR)
        export_dir.mkdir(parents=True, exist_ok=True)

        ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = export_dir / f"{module}_{ts}.json"
        txt_path  = export_dir / f"{module}_{ts}.txt"

        # JSON export
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        # TXT export — flat key-value
        lines = []
        for k, v in result.items():
            if not k.startswith("_"):
                lines.append(f"{k}: {v}")
        txt_path.write_text("\n".join(lines), encoding="utf-8")

        ui.print_export_info(str(json_path), str(txt_path))

    except Exception as ex:
        record_last_error("xray_export", "file", str(ex))
        ui.print_xray_warn(f"Export gagal: {ex}")


def _do_ai(result: dict, *, cfg: dict, module: str) -> None:
    """
    Kirim hasil scan ke AI untuk interpretasi lalu render.

    Args:
        result : Dict hasil scan.
        cfg    : Config dict dari framework.
        module : Nama modul untuk pemilihan system prompt.
    """
    ui.print_ai_header()
    try:
        text = interpret(result, cfg=cfg, module=module)
        ui.print_ai_result(text)
    except KeyboardInterrupt:
        ui.print_xray_warn("AI interpretation dibatalkan.")
    except Exception as ex:
        record_last_error("xray_ai", "api", str(ex))
        ui.print_xray_error(f"AI tidak tersedia: {ex}")


# ============================================================
# [4] Modul dispatchers
# ============================================================

def _handle_osint(args: list[str], cfg: dict) -> int:
    """
    Dispatcher untuk modul osint.
    Subcommands: ip, domain, email, web, phone.

    Args:
        args : argv setelah 'osint'.
        cfg  : Config dict.

    Returns:
        Exit code.
    """
    positional, flags = _parse_flags(args)

    if not positional or positional[0] in ("--help", "-h", "help"):
        ui.print_help_block(_HELP_OSINT)
        return 0

    subcmd = positional[0].strip().lower()
    target = positional[1] if len(positional) > 1 else ""

    if not target:
        ui.print_xray_error(f"Target untuk 'osint {subcmd}' tidak diberikan.")
        ui.print_help_block(_HELP_OSINT)
        return 2

    from system_logic.commands.xray.modules.osint import (
        scan_ip, scan_domain, scan_email, scan_web, scan_phone,
    )

    ui.print_xray_header("osint", subcmd, target)

    result: dict | None = None

    if subcmd == "ip":
        result = scan_ip(target)
    elif subcmd == "domain":
        result = scan_domain(target)
    elif subcmd == "email":
        result = scan_email(target)
    elif subcmd == "web":
        result = scan_web(target)
    elif subcmd == "phone":
        result = scan_phone(target)
    else:
        ui.print_xray_error(f"Subcommand osint '{subcmd}' tidak dikenal.")
        ui.print_help_block(_HELP_OSINT)
        return 2

    if result is None or result.get("_error"):
        return 1

    _render_result(result, flags=flags, cfg=cfg, module="osint")
    return 0


def _handle_steg(args: list[str], cfg: dict) -> int:
    """
    Dispatcher untuk modul steg.
    Subcommands: detect, extract, embed, meta.

    Args:
        args : argv setelah 'steg'.
        cfg  : Config dict.

    Returns:
        Exit code.
    """
    positional, flags = _parse_flags(args)

    if not positional or positional[0] in ("--help", "-h", "help"):
        ui.print_help_block(_HELP_STEG)
        return 0

    subcmd = positional[0].strip().lower()
    target = positional[1] if len(positional) > 1 else ""

    if not target:
        ui.print_xray_error(f"Target file untuk 'steg {subcmd}' tidak diberikan.")
        ui.print_help_block(_HELP_STEG)
        return 2

    from system_logic.commands.xray.modules.steg import (
        detect_hidden, extract_hidden, embed_message, read_metadata,
    )

    ui.print_xray_header("steg", subcmd, target)

    result: dict | None = None

    if subcmd == "detect":
        result = detect_hidden(target)
    elif subcmd == "extract":
        result = extract_hidden(target)
    elif subcmd == "embed":
        message = positional[2] if len(positional) > 2 else ""
        if not message:
            ui.print_xray_error("Pesan untuk 'steg embed' tidak diberikan.")
            return 2
        result = embed_message(target, message)
    elif subcmd == "meta":
        result = read_metadata(target)
    else:
        ui.print_xray_error(f"Subcommand steg '{subcmd}' tidak dikenal.")
        ui.print_help_block(_HELP_STEG)
        return 2

    if result is None or result.get("_error"):
        return 1

    _render_result(result, flags=flags, cfg=cfg, module="steg")
    return 0


def _handle_file(args: list[str], cfg: dict) -> int:
    """
    Dispatcher untuk modul file forensics.
    Subcommands: analyze, hash, strings, entropy.

    Args:
        args : argv setelah 'file'.
        cfg  : Config dict.

    Returns:
        Exit code.
    """
    positional, flags = _parse_flags(args)

    if not positional or positional[0] in ("--help", "-h", "help"):
        ui.print_help_block(_HELP_FILE)
        return 0

    subcmd = positional[0].strip().lower()
    target = positional[1] if len(positional) > 1 else ""

    if not target:
        ui.print_xray_error(f"Target file untuk 'file {subcmd}' tidak diberikan.")
        ui.print_help_block(_HELP_FILE)
        return 2

    from system_logic.commands.xray.modules.file_forensics import (
        analyze_file, hash_file, extract_strings, check_entropy,
    )

    ui.print_xray_header("file", subcmd, target)

    result: dict | None = None

    if subcmd == "analyze":
        result = analyze_file(target)
    elif subcmd == "hash":
        result = hash_file(target)
    elif subcmd == "strings":
        result = extract_strings(target)
    elif subcmd == "entropy":
        result = check_entropy(target)
    else:
        ui.print_xray_error(f"Subcommand file '{subcmd}' tidak dikenal.")
        ui.print_help_block(_HELP_FILE)
        return 2

    if result is None or result.get("_error"):
        return 1

    _render_result(result, flags=flags, cfg=cfg, module="file")
    return 0


def _handle_net(args: list[str], cfg: dict) -> int:
    """
    Dispatcher untuk modul network inspector.
    Subcommands: scan, monitor, arp, trace, lookup.

    Args:
        args : argv setelah 'net'.
        cfg  : Config dict.

    Returns:
        Exit code.
    """
    positional, flags = _parse_flags(args)

    if not positional or positional[0] in ("--help", "-h", "help"):
        ui.print_help_block(_HELP_NET)
        return 0

    subcmd = positional[0].strip().lower()
    target = positional[1] if len(positional) > 1 else ""

    from system_logic.commands.xray.modules.net import (
        port_scan, monitor_traffic, arp_scan, traceroute, dns_lookup,
    )

    ui.print_xray_header("net", subcmd, target or "local")

    result: dict | None = None

    if subcmd == "scan":
        if not target:
            ui.print_xray_error("Target host untuk 'net scan' tidak diberikan.")
            return 2
        result = port_scan(target)
    elif subcmd == "monitor":
        result = monitor_traffic()
    elif subcmd == "arp":
        result = arp_scan()
    elif subcmd == "trace":
        if not target:
            ui.print_xray_error("Target host untuk 'net trace' tidak diberikan.")
            return 2
        result = traceroute(target)
    elif subcmd == "lookup":
        if not target:
            ui.print_xray_error("Target untuk 'net lookup' tidak diberikan.")
            return 2
        result = dns_lookup(target)
    else:
        ui.print_xray_error(f"Subcommand net '{subcmd}' tidak dikenal.")
        ui.print_help_block(_HELP_NET)
        return 2

    if result is None or result.get("_error"):
        return 1

    _render_result(result, flags=flags, cfg=cfg, module="net")
    return 0


# ============================================================
# [5] Main entry point
# ============================================================

def handle(argv: list[str], cfg: dict) -> int:
    """
    Entry point utama untuk command `xray`.
    Dipanggil oleh app/bridge.py.

    Args:
        argv : Argumen setelah 'xray' (list of strings).
        cfg  : Config dict dari load_config().

    Returns:
        Exit code: 0=sukses, 1=scan error, 2=arg error, 130=Ctrl+C.
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        ui.print_help_block(_HELP_ROOT)
        return 0

    modul = argv[0].strip().lower()
    rest  = argv[1:]

    try:
        if modul == "osint":
            return _handle_osint(rest, cfg)
        elif modul == "steg":
            return _handle_steg(rest, cfg)
        elif modul == "file":
            return _handle_file(rest, cfg)
        elif modul == "net":
            return _handle_net(rest, cfg)
        else:
            ui.print_xray_error(f"Modul '{modul}' tidak dikenal.")
            ui.print_help_block(_HELP_ROOT)
            return 2

    except KeyboardInterrupt:
        ui.print_xray_warn("Dibatalkan.")
        return 130
    except Exception as ex:
        record_last_error("xray_handle", modul, str(ex))
        ui.print_xray_error(f"Unexpected error: {ex}")
        return 1
