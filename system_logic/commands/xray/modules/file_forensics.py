"""
commands/xray/modules/file_forensics.py
========================================
Modul File Forensics untuk command `xray`.

Aturan isolasi:
    BOLEH import dari luar xray/:
        - stdlib saja (hashlib, math, subprocess, collections)
        - third-party: python-magic (opsional, ada fallback manual)
    UI via xray/ui.py saja.
    TIDAK BOLEH import dari luar xray/ selain stdlib/third-party.

Subcommands:
    analyze → analyze_file(file_path)     Analisis lengkap
    hash    → hash_file(file_path)        Generate MD5/SHA1/SHA256
    strings → extract_strings(file_path)  Ekstrak readable strings
    entropy → check_entropy(file_path)    Hitung Shannon entropy

Semua fungsi return dict. Key '_error' menandakan gagal.

Exported:
    analyze_file(file_path)     -> dict
    hash_file(file_path)        -> dict
    extract_strings(file_path)  -> dict
    check_entropy(file_path)    -> dict
"""
from __future__ import annotations

import hashlib
import math
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from system_logic.commands.xray.ui import print_xray_error


# ============================================================
# [1] Internal helpers
# ============================================================

def _err(msg: str) -> dict[str, Any]:
    """
    Buat dict error standar.

    Args:
        msg: Pesan error.
    """
    print_xray_error(msg)
    return {"_error": msg}


def _check_file(path: str) -> tuple[bool, str]:
    """
    Validasi file: exist dan merupakan file (bukan directory).

    Args:
        path: Path ke file yang akan dicek.

    Returns:
        Tuple (ok, error_message).
    """
    p = Path(path)
    if not p.exists():
        return False, f"File tidak ditemukan: {path}"
    if not p.is_file():
        return False, f"Bukan file: {path}"
    return True, ""


def _human_size(size_bytes: int) -> str:
    """
    Convert bytes ke format human readable.

    Args:
        size_bytes: Ukuran dalam bytes.

    Returns:
        String format: '1.2 MB', '340.0 KB', dll.
    """
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.1f} TB"


def _compute_hashes(file_path: str) -> dict[str, str]:
    """
    Compute MD5, SHA1, SHA256 dari file secara streaming.
    Streaming agar tidak crash untuk file besar.

    Args:
        file_path: Path ke file.

    Returns:
        Dict berisi hash values, atau error key jika gagal.
    """
    md5    = hashlib.md5()
    sha1   = hashlib.sha1()
    sha256 = hashlib.sha256()

    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
                sha1.update(chunk)
                sha256.update(chunk)
        return {
            "md5"   : md5.hexdigest(),
            "sha1"  : sha1.hexdigest(),
            "sha256": sha256.hexdigest(),
        }
    except Exception as ex:
        return {"hash_error": str(ex)}


def _compute_entropy(file_path: str) -> float:
    """
    Hitung Shannon entropy dari file.
    Entropy tinggi (>7.5) mengindikasikan file terenkripsi atau di-pack.

    Args:
        file_path: Path ke file.

    Returns:
        Float nilai entropy (0.0 - 8.0).
    """
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        if not data:
            return 0.0
        counter = Counter(data)
        total   = len(data)
        return -sum(
            (c / total) * math.log2(c / total)
            for c in counter.values()
            if c > 0
        )
    except Exception:
        return 0.0


def _entropy_label(val: float) -> str:
    """
    Label deskriptif untuk nilai entropy.

    Args:
        val: Nilai entropy.

    Returns:
        String label dengan emoji color indicator.
    """
    if val >= 7.5:
        return "🔴 Sangat tinggi — indikasi enkripsi / packing"
    elif val >= 6.0:
        return "🟠 Tinggi — mungkin ada bagian compressed"
    elif val >= 4.0:
        return "🟢 Normal — distribusi byte wajar"
    else:
        return "🔵 Rendah — file sangat repetitif atau teks biasa"


def _manual_magic(file_path: str) -> str:
    """
    Deteksi tipe file via magic bytes manual (fallback jika python-magic tidak ada).
    Mendukung format umum: image, archive, executable, document.

    Args:
        file_path: Path ke file.

    Returns:
        String MIME type atau 'application/octet-stream' jika tidak dikenal.
    """
    _SIGNATURES: dict[bytes, str] = {
        b"\xff\xd8\xff"    : "image/jpeg",
        b"\x89PNG\r\n"     : "image/png",
        b"GIF87a"          : "image/gif",
        b"GIF89a"          : "image/gif",
        b"BM"              : "image/bmp",
        b"PK\x03\x04"      : "application/zip",
        b"\x1f\x8b"        : "application/gzip",
        b"Rar!"            : "application/rar",
        b"\x7fELF"         : "application/elf",
        b"MZ"              : "application/exe (Windows PE)",
        b"%PDF"            : "application/pdf",
        b"\xca\xfe\xba\xbe": "application/mach-o",
    }
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
        for magic, mime in _SIGNATURES.items():
            if header.startswith(magic):
                return mime
        return "application/octet-stream (unknown)"
    except Exception:
        return "unknown"


def _check_extension_mismatch(ext: str, detected: str) -> bool:
    """
    Cek apakah ekstensi file cocok dengan tipe yang terdeteksi via magic bytes.

    Args:
        ext     : Ekstensi file (contoh: '.jpg').
        detected: MIME type yang terdeteksi.

    Returns:
        True jika ada mismatch (mencurigakan).
    """
    _EXPECTED: dict[str, list[str]] = {
        ".jpg"  : ["image/jpeg"],
        ".jpeg" : ["image/jpeg"],
        ".png"  : ["image/png"],
        ".gif"  : ["image/gif"],
        ".bmp"  : ["image/bmp"],
        ".pdf"  : ["application/pdf"],
        ".zip"  : ["application/zip"],
        ".exe"  : ["application/exe", "application/x-dosexec"],
    }
    expected = _EXPECTED.get(ext.lower(), [])
    if not expected:
        return False  # Ekstensi tidak dikenal, skip
    return not any(e in detected for e in expected)


def _looks_like_ip(s: str) -> bool:
    """
    Cek apakah string terlihat seperti IPv4 address.

    Args:
        s: String yang dicek.
    """
    return bool(re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", s))


# ============================================================
# [2] Analyze file
# ============================================================

def analyze_file(file_path: str) -> dict[str, Any]:
    """
    Analisis lengkap sebuah file:
        - Tipe asli via magic bytes (bukan dari ekstensi)
        - Ukuran file
        - Hash MD5, SHA1, SHA256
        - Entropy (deteksi enkripsi/packing)
        - Warning jika ekstensi tidak cocok tipe asli

    Args:
        file_path: Path ke file target.

    Returns:
        Dict hasil analisis. Key '_error' jika gagal.
    """
    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    p = Path(file_path)

    result: dict[str, Any] = {
        "_module"    : "file",
        "_subcommand": "analyze",
        "target"     : file_path,
        "filename"   : p.name,
        "extension"  : p.suffix.lower() or "(none)",
        "size_bytes" : p.stat().st_size,
        "size_human" : _human_size(p.stat().st_size),
    }

    # --- Deteksi tipe via magic bytes ---
    try:
        import magic
        result["detected_type"]    = magic.from_file(file_path, mime=True)
        result["file_description"] = magic.from_file(file_path)
    except ImportError:
        result["detected_type"]    = _manual_magic(file_path)
        result["file_description"] = "python-magic tidak terinstall — deteksi terbatas"
    except Exception as ex:
        result["detected_type"] = f"Error: {ex}"

    # --- Extension mismatch check ---
    detected = result.get("detected_type", "")
    if _check_extension_mismatch(p.suffix, detected):
        result["warning"] = (
            f"⚠️  MISMATCH! Ekstensi '{p.suffix}' "
            f"tidak sesuai tipe asli: {detected}"
        )
    else:
        result["extension_match"] = "✅ Ekstensi sesuai tipe file"

    # --- Hash ---
    result.update(_compute_hashes(file_path))

    # --- Entropy ---
    entropy_val          = _compute_entropy(file_path)
    result["entropy"]    = f"{entropy_val:.4f}"
    result["entropy_analysis"] = _entropy_label(entropy_val)

    return result


# ============================================================
# [3] Hash file
# ============================================================

def hash_file(file_path: str) -> dict[str, Any]:
    """
    Generate hash MD5, SHA1, dan SHA256 sebuah file.
    Berguna untuk verifikasi integritas file.

    Args:
        file_path: Path ke file target.

    Returns:
        Dict berisi hash values. Key '_error' jika gagal.
    """
    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    result: dict[str, Any] = {
        "_module"    : "file",
        "_subcommand": "hash",
        "target"     : file_path,
    }
    result.update(_compute_hashes(file_path))
    result["note"] = "Gunakan hash ini untuk verifikasi integritas file."

    return result


# ============================================================
# [4] Extract strings
# ============================================================

def extract_strings(file_path: str, min_length: int = 4) -> dict[str, Any]:
    """
    Ekstrak readable ASCII strings dari file binary.
    Berguna untuk analisis malware dan CTF forensics.

    Strategi:
        1. Coba tool 'strings' sistem (lebih cepat).
        2. Fallback: baca binary dan ekstrak manual (printable ASCII).

    Hasil yang di-highlight:
        - URL (http://, https://, ftp://)
        - IP address

    Args:
        file_path  : Path ke file target.
        min_length : Panjang minimum string (default 4).

    Returns:
        Dict berisi strings yang ditemukan. Key '_error' jika gagal.
    """
    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    result: dict[str, Any] = {
        "_module"    : "file",
        "_subcommand": "strings",
        "target"     : file_path,
        "min_length" : min_length,
    }

    strings_found: list[str] = []

    # --- Strategi 1: system 'strings' tool ---
    try:
        proc = subprocess.run(
            ["strings", "-n", str(min_length), file_path],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode == 0 and proc.stdout:
            strings_found = [
                s.strip() for s in proc.stdout.splitlines() if s.strip()
            ]
        else:
            raise RuntimeError("strings tool not available")

    except Exception:
        # --- Strategi 2: manual extraction ---
        try:
            with open(file_path, "rb") as f:
                data = f.read()

            current: list[str] = []
            for byte in data:
                if 32 <= byte <= 126:  # Printable ASCII range
                    current.append(chr(byte))
                else:
                    if len(current) >= min_length:
                        strings_found.append("".join(current))
                    current = []
            if len(current) >= min_length:
                strings_found.append("".join(current))

        except Exception as ex:
            result["error"] = f"Gagal ekstrak strings: {ex}"
            return result

    # --- Kategorisasi hasil ---
    urls = [s for s in strings_found if s.startswith(("http://", "https://", "ftp://"))]
    ips  = [s for s in strings_found if _looks_like_ip(s)]

    result["total_strings"]   = len(strings_found)
    result["urls_found"]      = urls[:20]       # Limit 20 URL
    result["ips_found"]       = ips[:20]        # Limit 20 IP
    result["strings_preview"] = strings_found[:50]  # Preview 50 pertama
    result["note"]            = (
        f"Menampilkan 50 pertama dari {len(strings_found)} strings. "
        "URLs dan IPs di-highlight secara terpisah."
    )

    return result


# ============================================================
# [5] Entropy check
# ============================================================

def check_entropy(file_path: str) -> dict[str, Any]:
    """
    Hitung Shannon entropy file untuk deteksi enkripsi atau packing.

    Interpretasi nilai entropy:
        >= 7.5  → Sangat tinggi — kemungkinan dienkripsi / packed
        >= 6.0  → Tinggi — mungkin ada bagian compressed
        >= 4.0  → Normal — binary biasa
        < 4.0   → Rendah — teks biasa atau data repetitif

    Args:
        file_path: Path ke file target.

    Returns:
        Dict berisi nilai entropy dan interpretasinya.
    """
    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    entropy_val = _compute_entropy(file_path)

    result: dict[str, Any] = {
        "_module"       : "file",
        "_subcommand"   : "entropy",
        "target"        : file_path,
        "entropy_value" : f"{entropy_val:.6f}",
        "max_possible"  : "8.0 (completely random / encrypted)",
        "percentage"    : f"{(entropy_val / 8.0) * 100:.1f}% of max",
        "analysis"      : _entropy_label(entropy_val),
    }

    # Verdict + suggestion
    if entropy_val >= 7.5:
        result["verdict"]    = "⚠️  TINGGI — File kemungkinan dienkripsi / di-pack"
        result["suggestion"] = "Coba: xray file strings untuk cari petunjuk lebih lanjut"
    elif entropy_val >= 6.0:
        result["verdict"]    = "⚡ MEDIUM-HIGH — Ada bagian compressed atau mixed content"
        result["suggestion"] = "File normal dengan section compressed, atau partially encrypted"
    elif entropy_val >= 4.0:
        result["verdict"]    = "✅ NORMAL — Distribusi byte wajar untuk file biasa"
    else:
        result["verdict"]    = "✅ RENDAH — Kemungkinan teks biasa atau data sangat repetitif"

    return result
