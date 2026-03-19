"""
commands/xray/ui.py
===================
UI layer untuk command `xray` — sepenuhnya mandiri (self-contained).
Tidak mengimport apapun dari luar folder xray/.

Exported:
    # [1] Terminal capability detection
    term_size()                                      -> tuple[int, int]
    supports_color()                                 -> bool
    supports_unicode()                               -> bool

    # [2] ANSI color helpers
    c_reset, c_bold, c_dim, c_italic
    c_cyan, c_green, c_yellow, c_red
    c_blue, c_magenta, c_gray

    # [3] Text formatting
    tag(text, color)                                 -> str
    hr(width, char)                                  -> str
    wrap(text, width)                                -> str

    # [4] Core print conventions
    print_xray_header(module, subcommand, target)    -> None
    print_xray_error(msg)                            -> None
    print_xray_warn(msg)                             -> None
    print_xray_success(msg)                          -> None
    print_xray_info(msg)                             -> None

    # [5] Result rendering
    print_kv(key, value, pad)                        -> None
    print_section(title)                             -> None
    print_result_block(result)                       -> None
    print_flag_warnings(flags)                       -> None
    print_export_info(json_path, txt_path)           -> None

    # [6] AI output rendering
    print_ai_header()                                -> None
    print_ai_result(text)                            -> None

    # [7] Help rendering
    print_help_block(lines)                          -> None
"""
from __future__ import annotations

import os
import re
import sys
import textwrap
from shutil import get_terminal_size
from typing import Any, Optional


# ============================================================
# [1] Terminal capability detection
# ============================================================

def term_size() -> tuple[int, int]:
    """Return (columns, rows) dengan fallback stabil."""
    s = get_terminal_size((100, 24))
    return int(s.columns), int(s.lines)


def _isatty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def supports_color(stream: Any = sys.stdout) -> bool:
    """Cek apakah terminal mendukung ANSI color."""
    try:
        if os.environ.get("NO_COLOR") is not None:
            return False
        if os.environ.get("TERM") in (None, "", "dumb"):
            return False
        if hasattr(stream, "isatty") and not stream.isatty():
            return False
        return True
    except Exception:
        return False


def supports_unicode(stream: Any = sys.stdout) -> bool:
    """Cek apakah terminal mendukung unicode/emoji."""
    try:
        no_emoji = (
            os.environ.get("AI_TERM_NO_EMOJI")
            or os.environ.get("AI_TERM_ASCII")
            or ""
        )
        if no_emoji.strip().lower() in ("1", "true", "yes", "on"):
            return False
        enc = getattr(stream, "encoding", None) or ""
        return "UTF" in enc.upper()
    except Exception:
        return False


def _safe_write(s: str) -> None:
    """Write ke stdout, tidak crash jika gagal."""
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except Exception:
        pass


def _safe_write_err(s: str) -> None:
    """Write ke stderr, tidak crash jika gagal."""
    try:
        sys.stderr.write(s)
        sys.stderr.flush()
    except Exception:
        pass


# ============================================================
# [2] ANSI color helpers
# ============================================================

_CSI = "\x1b["


def _maybe(code: str) -> str:
    """Return ANSI code hanya jika terminal support color."""
    return code if supports_color() else ""


def c_reset()   -> str: return _maybe(_CSI + "0m")
def c_bold()    -> str: return _maybe(_CSI + "1m")
def c_dim()     -> str: return _maybe(_CSI + "2m")
def c_italic()  -> str: return _maybe(_CSI + "3m")
def c_cyan()    -> str: return _maybe(_CSI + "36m")
def c_green()   -> str: return _maybe(_CSI + "32m")
def c_yellow()  -> str: return _maybe(_CSI + "33m")
def c_red()     -> str: return _maybe(_CSI + "31m")
def c_blue()    -> str: return _maybe(_CSI + "34m")
def c_magenta() -> str: return _maybe(_CSI + "35m")
def c_gray()    -> str: return _maybe(_CSI + "90m")


# ============================================================
# [3] Text formatting helpers
# ============================================================

_RE_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Hapus semua ANSI escape sequence dari string."""
    return _RE_ANSI.sub("", str(text))


def visible_len(text: str) -> int:
    """Panjang string tanpa ANSI codes."""
    return len(strip_ansi(text))


def tag(text: str, color: str) -> str:
    """
    Render bracket tag berwarna: [TEXT].

    Args:
        text  : Label di dalam bracket.
        color : ANSI color string.

    Returns:
        String '[text]' dengan warna dan bold.
    """
    return f"{color}{c_bold()}[{text}]{c_reset()}"


def hr(width: Optional[int] = None, char: str = "─") -> str:
    """
    Horizontal rule string.
    Unicode char diganti '-' jika terminal tidak support unicode.

    Args:
        width : Lebar rule. Default = lebar terminal.
        char  : Karakter yang dipakai. Default '─'.
    """
    cols, _ = term_size()
    w  = int(width) if isinstance(width, int) and width > 0 else cols
    ch = char if supports_unicode() else "-"
    return ch * max(10, w)


def wrap(text: str, width: int) -> str:
    """
    Wrap text ke lebar tertentu.
    ANSI-aware: baris pendek dibiarkan, baris panjang di-strip dulu.

    Args:
        text  : Teks yang akan di-wrap.
        width : Lebar target.
    """
    width = max(50, int(width))
    lines: list[str] = []
    for ln in str(text).splitlines():
        if "\x1b[" not in ln:
            lines.append(textwrap.fill(ln, width=width))
            continue
        if visible_len(ln) <= width:
            lines.append(ln)
        else:
            lines.append(textwrap.fill(strip_ansi(ln), width=width))
    return "\n".join(lines)


# ============================================================
# [4] Core print conventions — xray style
# ============================================================

def print_xray_header(module: str, subcommand: str, target: str = "") -> None:
    """
    Render header utama di awal setiap scan.
    Format: [XRAY] [MODULE] • subcommand • target

    Args:
        module     : Nama modul ('osint'|'steg'|'file'|'net').
        subcommand : Subcommand yang dijalankan.
        target     : Target scan (IP, domain, file path, dll).
    """
    cols, _ = term_size()
    w = min(cols, 120)

    parts = [
        tag("XRAY", c_magenta()),
        tag(module.upper(), c_cyan()),
        f"{c_dim()}•{c_reset()}",
        f"{c_bold()}{subcommand}{c_reset()}",
    ]
    if target:
        parts += [f"{c_dim()}•{c_reset()}", f"{c_yellow()}{target}{c_reset()}"]

    _safe_write("\n" + wrap(" ".join(parts), width=w) + "\n")
    _safe_write(f"{c_dim()}{hr(width=min(cols, 60))}{c_reset()}\n")


def print_xray_error(msg: str) -> None:
    """
    Print error xray ke stderr.
    Format: [XRAY ERROR] pesan

    Args:
        msg: Pesan error.
    """
    cols, _ = term_size()
    _safe_write_err(
        wrap(f"{tag('XRAY ERROR', c_red())} {msg}", width=min(cols, 120)) + "\n"
    )


def print_xray_warn(msg: str) -> None:
    """
    Print warning xray ke stdout.
    Format: [WARN] pesan

    Args:
        msg: Pesan warning.
    """
    cols, _ = term_size()
    _safe_write(
        wrap(f"{tag('WARN', c_yellow())} {msg}", width=min(cols, 120)) + "\n"
    )


def print_xray_success(msg: str) -> None:
    """
    Print success message xray.
    Format: [OK] pesan

    Args:
        msg: Pesan sukses.
    """
    cols, _ = term_size()
    _safe_write(
        wrap(f"{tag('OK', c_green())} {msg}", width=min(cols, 120)) + "\n"
    )


def print_xray_info(msg: str) -> None:
    """
    Print info message xray.
    Format: [INFO] pesan

    Args:
        msg: Pesan informasi.
    """
    cols, _ = term_size()
    _safe_write(
        wrap(f"{tag('INFO', c_cyan())} {msg}", width=min(cols, 120)) + "\n"
    )


# ============================================================
# [5] Result rendering helpers
# ============================================================

def print_kv(key: str, value: Any, pad: int = 20) -> None:
    """
    Render satu baris key-value dengan format rapi.
    Format:   key (dim, padded) : value

    Args:
        key   : Label kunci.
        value : Nilai yang ditampilkan.
        pad   : Lebar kolom key (default 20).
    """
    k = f"{c_dim()}{key:<{pad}}{c_reset()}"
    _safe_write(f"  {k}: {value}\n")


def print_section(title: str) -> None:
    """
    Render judul section di dalam result block.
    Format:   ── TITLE

    Args:
        title: Judul section.
    """
    _safe_write(f"\n  {c_bold()}{c_cyan()}{title}{c_reset()}\n")
    _safe_write(f"  {c_dim()}{hr(width=40)}{c_reset()}\n")


def print_result_block(result: dict) -> None:
    """
    Render dict hasil scan menjadi tampilan terminal yang rapi.
    Otomatis skip key internal (diawali '_') dan key khusus.

    Tipe value yang didukung:
        str/int/float       → print_kv biasa
        list[str]           → bullet list
        list[dict]          → sub-block per item (numbered)
        dict                → nested key-value indent

    Args:
        result: Dict hasil dari fungsi scan modul xray.
    """
    _SKIP = {"_module", "_subcommand", "target", "flags", "_error", "error"}

    for key, value in result.items():
        if key in _SKIP:
            continue

        label = key.replace("_", " ").title()

        # --- str / int / float ---
        if isinstance(value, (str, int, float)):
            print_kv(label, str(value))

        # --- list ---
        elif isinstance(value, list):
            if not value:
                print_kv(label, f"{c_dim()}(kosong){c_reset()}")

            elif all(isinstance(i, str) for i in value):
                # List of strings — bullet
                _safe_write(f"  {c_dim()}{label:<20}{c_reset()}:\n")
                for item in value:
                    _safe_write(f"    {c_dim()}•{c_reset()} {item}\n")

            elif all(isinstance(i, dict) for i in value):
                # List of dicts — numbered sub-block
                _safe_write(f"  {c_dim()}{label:<20}{c_reset()}:\n")
                for idx, item in enumerate(value, 1):
                    _safe_write(
                        f"    {c_dim()}[{idx}]{c_reset()}\n"
                    )
                    for k, v in item.items():
                        _safe_write(
                            f"      {c_dim()}{k:<18}{c_reset()}: {v}\n"
                        )
                    _safe_write("\n")

            else:
                # Mixed list — fallback str
                print_kv(label, str(value))

        # --- dict ---
        elif isinstance(value, dict):
            _safe_write(f"  {c_dim()}{label:<20}{c_reset()}:\n")
            for k, v in value.items():
                _safe_write(f"    {c_dim()}{k:<18}{c_reset()}: {v}\n")

        else:
            print_kv(label, str(value))


def print_flag_warnings(flags: list[str]) -> None:
    """
    Render warning flags di bagian bawah result.
    Tidak tampil jika flags kosong.

    Args:
        flags: List string warning/suspicious flags.
    """
    if not flags:
        return
    cols, _ = term_size()
    _safe_write(f"\n  {c_dim()}{hr(width=min(cols, 60) - 2)}{c_reset()}\n")
    _safe_write(f"  {c_yellow()}{c_bold()}⚠  Flags Terdeteksi:{c_reset()}\n")
    for f in flags:
        _safe_write(f"    {c_yellow()}•{c_reset()} {f}\n")


def print_export_info(json_path: str, txt_path: str) -> None:
    """
    Render info path file hasil export.

    Args:
        json_path : Path file JSON export.
        txt_path  : Path file TXT export.
    """
    _safe_write(f"\n{tag('EXPORT', c_green())} Hasil disimpan:\n")
    _safe_write(f"  {c_dim()}JSON{c_reset()} : {json_path}\n")
    _safe_write(f"  {c_dim()}TXT {c_reset()} : {txt_path}\n")


# ============================================================
# [6] AI output rendering
# ============================================================

def print_ai_header() -> None:
    """Render header sebelum blok output AI interpretation."""
    cols, _ = term_size()
    _safe_write(f"\n{tag('AI', c_magenta())} {c_bold()}Interpretasi:{c_reset()}\n")
    _safe_write(f"{c_dim()}{hr(width=min(cols, 60))}{c_reset()}\n")


def print_ai_result(text: str) -> None:
    """
    Render hasil interpretasi AI ke terminal.

    Args:
        text: Teks interpretasi dari AI.
    """
    cols, _ = term_size()
    _safe_write(wrap((text or "").strip(), width=min(cols, 100)) + "\n\n")


# ============================================================
# [7] Help rendering
# ============================================================

def print_help_block(lines: list[str]) -> None:
    """
    Render blok help ke terminal.
    Setiap string dalam list = satu baris output.

    Args:
        lines: List string baris help.
    """
    cols, _ = term_size()
    w = min(cols, 100)
    for line in lines:
        _safe_write(wrap(line, width=w) + "\n")
    _safe_write("\n")
