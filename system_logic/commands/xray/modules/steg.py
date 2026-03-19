"""
commands/xray/modules/steg.py
==============================
Modul Steganography Inspector untuk command `xray`.

Aturan isolasi:
    BOLEH import dari luar xray/:
        - stdlib + third-party (Pillow, numpy, stegano, exifread)
    UI via xray/ui.py saja.
    TIDAK BOLEH import dari luar xray/ selain stdlib/third-party.

Subcommands:
    detect  → detect_hidden(file_path)              LSB analysis
    extract → extract_hidden(file_path)             Ekstrak pesan
    embed   → embed_message(file_path, message)     Sisipkan pesan
    meta    → read_metadata(file_path)              Baca EXIF

Format file yang didukung: PNG, JPG, JPEG, BMP

Semua fungsi return dict. Key '_error' menandakan gagal.

Exported:
    detect_hidden(file_path)             -> dict
    extract_hidden(file_path)            -> dict
    embed_message(file_path, message)    -> dict
    read_metadata(file_path)             -> dict
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from system_logic.commands.xray.ui import print_xray_error


# ============================================================
# [1] Internal helpers
# ============================================================

_SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".bmp"}


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
    Validasi file: exist, adalah file, format didukung.

    Args:
        path: Path ke file yang akan dicek.

    Returns:
        Tuple (ok, error_message). ok=True jika valid.
    """
    p = Path(path)
    if not p.exists():
        return False, f"File tidak ditemukan: {path}"
    if not p.is_file():
        return False, f"Bukan file: {path}"
    if p.suffix.lower() not in _SUPPORTED_FORMATS:
        return False, (
            f"Format tidak didukung: '{p.suffix}'. "
            f"Gunakan: PNG, JPG, BMP"
        )
    return True, ""


# ============================================================
# [2] Detect hidden message
# ============================================================

def detect_hidden(file_path: str) -> dict[str, Any]:
    """
    Deteksi apakah ada hidden message di dalam gambar via LSB analysis.

    Cara kerja:
        - Baca pixel RGB dari gambar
        - Hitung ratio bit paling rendah (LSB) tiap channel
        - Distribusi natural = sekitar 0.5
        - Deviasi kecil dari 0.5 = mencurigakan (indikasi steganography)

    Args:
        file_path: Path ke file gambar.

    Returns:
        Dict hasil analisis. Key '_error' jika gagal.
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return _err(
            "Library missing. Jalankan: pip install Pillow numpy"
        )

    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    result: dict[str, Any] = {
        "_module"    : "steg",
        "_subcommand": "detect",
        "target"     : file_path,
    }

    try:
        img    = Image.open(file_path).convert("RGB")
        pixels = np.array(img)

        # Ekstrak LSB tiap channel
        lsb_r = pixels[:, :, 0] & 1
        lsb_g = pixels[:, :, 1] & 1
        lsb_b = pixels[:, :, 2] & 1

        total  = pixels.shape[0] * pixels.shape[1]
        ratio_r = lsb_r.sum() / total
        ratio_g = lsb_g.sum() / total
        ratio_b = lsb_b.sum() / total
        avg     = (ratio_r + ratio_g + ratio_b) / 3
        deviation = abs(avg - 0.5)

        result["image_size"]   = f"{img.width} x {img.height} px"
        result["total_pixels"] = total
        result["lsb_ratio"]    = {
            "red"    : f"{ratio_r:.4f}",
            "green"  : f"{ratio_g:.4f}",
            "blue"   : f"{ratio_b:.4f}",
            "average": f"{avg:.4f}",
        }

        # Verdict berdasarkan deviasi
        if deviation < 0.02:
            result["verdict"]    = "⚠️  MENCURIGAKAN — distribusi LSB terlalu seragam"
            result["confidence"] = "Medium"
            result["note"]       = (
                "LSB ratio sangat mendekati 0.5 secara tidak wajar. "
                "Kemungkinan ada data tersembunyi."
            )
        elif deviation > 0.1:
            result["verdict"]    = "✅ Bersih — distribusi LSB normal"
            result["confidence"] = "High"
            result["note"]       = "Tidak ada indikasi steganography pada LSB."
        else:
            result["verdict"]    = "❓ Tidak pasti — perlu analisis lebih lanjut"
            result["confidence"] = "Low"
            result["note"]       = "Coba: xray steg extract untuk mencoba ekstraksi."

        result["suggestion"] = "Jalankan 'xray steg extract' untuk mencoba ekstrak pesan."

    except Exception as ex:
        result["error"] = f"Analisis gagal: {ex}"

    return result


# ============================================================
# [3] Extract hidden message
# ============================================================

def extract_hidden(file_path: str) -> dict[str, Any]:
    """
    Ekstrak pesan tersembunyi dari gambar menggunakan LSB steganography.
    Menggunakan library stegano.

    Args:
        file_path: Path ke file gambar.

    Returns:
        Dict berisi pesan yang diekstrak. Key '_error' jika gagal.
    """
    try:
        from stegano import lsb
    except ImportError:
        return _err(
            "Library 'stegano' tidak terinstall. "
            "Jalankan: pip install stegano"
        )

    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    result: dict[str, Any] = {
        "_module"    : "steg",
        "_subcommand": "extract",
        "target"     : file_path,
    }

    try:
        message = lsb.reveal(file_path)
        if message:
            result["status"]         = "✅ Pesan ditemukan!"
            result["message"]        = message
            result["message_length"] = f"{len(message)} karakter"
        else:
            result["status"]  = "❌ Tidak ada pesan tersembunyi"
            result["message"] = None
            result["note"]    = (
                "File tidak mengandung data steg, "
                "atau menggunakan metode selain LSB."
            )
    except Exception as ex:
        result["status"] = "❌ Ekstraksi gagal"
        result["error"]  = str(ex)
        result["note"]   = "File kemungkinan tidak mengandung steganography."

    return result


# ============================================================
# [4] Embed message
# ============================================================

def embed_message(file_path: str, message: str) -> dict[str, Any]:
    """
    Sisipkan pesan tersembunyi ke dalam gambar menggunakan LSB.
    File asli tidak dimodifikasi — output disimpan sebagai file baru.

    Naming convention output:
        foto.png → foto_steg.png

    Args:
        file_path: Path ke file gambar source.
        message  : Pesan yang akan disembunyikan.

    Returns:
        Dict berisi status dan path file output. Key '_error' jika gagal.
    """
    try:
        from stegano import lsb
    except ImportError:
        return _err(
            "Library 'stegano' tidak terinstall. "
            "Jalankan: pip install stegano"
        )

    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    if not message or not message.strip():
        return _err("Pesan tidak boleh kosong.")

    p           = Path(file_path)
    output_path = p.parent / f"{p.stem}_steg{p.suffix}"

    result: dict[str, Any] = {
        "_module"       : "steg",
        "_subcommand"   : "embed",
        "target"        : file_path,
        "message_length": f"{len(message)} karakter",
    }

    try:
        secret = lsb.hide(file_path, message)
        secret.save(str(output_path))
        result["status"]      = "✅ Pesan berhasil disembunyikan!"
        result["output_file"] = str(output_path)
        result["note"]        = "File asli tidak dimodifikasi."
    except Exception as ex:
        result["status"] = "❌ Embed gagal"
        result["error"]  = str(ex)

    return result


# ============================================================
# [5] Read metadata
# ============================================================

def read_metadata(file_path: str) -> dict[str, Any]:
    """
    Baca metadata/EXIF lengkap dari file gambar.

    Data yang dikumpulkan:
        - Format, mode, ukuran (dari Pillow)
        - EXIF: kamera, tanggal, software, GPS, copyright
        - Warning jika ada data GPS (lokasi terekspos)

    Args:
        file_path: Path ke file gambar.

    Returns:
        Dict berisi metadata. Key '_error' jika gagal.
    """
    try:
        import exifread
        from PIL import Image
    except ImportError:
        return _err(
            "Library missing. Jalankan: pip install exifread Pillow"
        )

    ok, err = _check_file(file_path)
    if not ok:
        return _err(err)

    result: dict[str, Any] = {
        "_module"    : "steg",
        "_subcommand": "meta",
        "target"     : file_path,
    }

    # --- Basic info dari Pillow ---
    try:
        img            = Image.open(file_path)
        result["format"] = img.format or "N/A"
        result["mode"]   = img.mode or "N/A"
        result["size"]   = f"{img.width} x {img.height} px"
    except Exception as ex:
        result["basic_info_error"] = str(ex)

    # --- EXIF data ---
    _IMPORTANT_TAGS = [
        "Image Make", "Image Model", "Image DateTime",
        "Image Software", "GPS GPSLatitude", "GPS GPSLongitude",
        "GPS GPSAltitude", "EXIF ExifImageWidth", "EXIF ExifImageLength",
        "Image Artist", "Image Copyright",
    ]

    try:
        with open(file_path, "rb") as f:
            tags = exifread.process_file(f, details=True)

        if not tags:
            result["exif"] = "Tidak ada data EXIF"
            result["note"] = "File tidak memiliki EXIF — mungkin sudah di-strip."
        else:
            exif_data: dict[str, str] = {}

            # Prioritas: important tags dulu
            for tag in _IMPORTANT_TAGS:
                if tag in tags:
                    exif_data[tag] = str(tags[tag])

            # Sisanya (skip thumbnail)
            for tag, val in tags.items():
                if tag not in exif_data and not tag.startswith("Thumbnail"):
                    exif_data[tag] = str(val)

            result["exif"]       = exif_data
            result["total_tags"] = len(tags)

            # GPS warning
            if "GPS GPSLatitude" in tags or "GPS GPSLongitude" in tags:
                result["gps_warning"] = (
                    "⚠️  File mengandung data GPS! "
                    "Lokasi foto terekspos dalam metadata."
                )

    except Exception as ex:
        result["exif_error"] = f"Gagal baca EXIF: {ex}"

    return result
