# Panduan Pengembangan Subcommand `ai`

Dokumen ini menjelaskan cara mengisi logika atau menambahkan fitur baru (subcommand) ke dalam folder `ai_logic/aisub/`.

## 📂 Struktur Modular
Sistem command `ai` menggunakan arsitektur **Thin Router**.
- **Router:** `ai_logic/ai.py` (hanya mengarah ke folder sub).
- **Implementasi:** `ai_logic/aisub/*.py` (semua logika ada di sini).
- **Registry:** `ai_logic/aisub/registry.py` (pendaftaran module).

---

## ⚡ 5 Aturan Emas (The Contract)

Setiap file subcommand (contoh: `run.py`, `mon.py`) **WAJIB** mematuhi aturan ini agar bisa dipanggil oleh sistem:

### 1. Fungsi Utama: `handle()`
Wajib memiliki fungsi entry-point dengan signature persis seperti ini:

```python
def handle(argv: list[str], cfg: dict) -> int:
    ...
```
- **`argv`**: List argumen string sisa (sudah dipotong prefix command).
  - Jika user ketik: `ai run build website`
  - `argv` adalah: `['build', 'website']`
- **`cfg`**: Dictionary konfigurasi penuh (backend, API key, model setting).
- **Return Value**: Harus mengembalikan **`int`**.
  - `0` = Sukses.
  - `1`, `2`, dst = Error (Exit code).

### 2. Docstring untuk Menu Help
Baris pertama di file Python (`__doc__`) otomatis menjadi deskripsi di menu `ai help`.

```python
"""Menjalankan instruksi otonom AI (Deskripsi ini akan muncul di help)."""

def handle(...): ...
```
Jika dikosongkan, menu help akan menampilkan `(No description)`.

### 3. Argument Parsing Manual
Dilarang menggunakan `sys.argv` global. Gunakan argumen `argv` yang diterima fungsi.
Anda bertanggung jawab memvalidasi input.

```python
# ✅ Benar
if not argv:
    return 2
target = argv[0]

# ❌ Salah (Dilarang)
import sys
target = sys.argv[2]
```

### 4. Konsistensi UI (Tampilan)
Gunakan library `ai_logic.ui.ansi` agar output konsisten (warna & format). Jangan sering pakai `print()` polos untuk log sistem.

```python
from ai_logic.ui import ansi

# Info standar
ansi.print_info("Sedang memproses...")

# Error singkat
ansi.print_brief_error("Parameter salah.")
```

### 5. Hindari Circular Import
Jangan mengimport `registry.py` atau `ai_logic/ai.py` di dalam logika global subcommand.
- ✅ Import utility dari: `ai_logic.common`, `ai_logic.backends`.
- ✅ Import antar sesama subcommand hanya diizinkan di dalam *fungsi* (runtime import), bukan di level atas file.

---

## 📝 Template Standar (Copy-Paste)

Gunakan template ini saat memulai file baru atau mengisi stub:

```python
"""Deskripsi command ini dam dafatr commnad, wajib jelas dan boleh panjang"""

from ai_logic.ui import ansi
# Import backend jika butuh:
# from ai_logic.backends import api_gemini

def handle(argv: list[str], cfg: dict) -> int:
    """
    Handler logika utama.
    argv: List argumen input.
    cfg: Dict konfigurasi system.
    """

    # 1. Validasi Argumen
    if not argv:
        ansi.print_brief_error("Error: Argumen kurang.")
        print("Usage: ai <command> [target]")
        return 2  # Return non-zero for error

    # 2. Ambil Data
    target = " ".join(argv)
    ansi.print_info(f"Memulai proses untuk: {target}")

    # 3. Logika (Contoh)
    try:
        # Panggil backend logic di sini...
        pass
    except Exception as e:
        ansi.print_brief_error(f"Terjadi kesalahan: {e}")
        return 1

    # 4. Sukses
    print("Selesai.")
    return 0
```

---

## 🛠 Cara Menambah Subcommand Baru
Jika file `run.py`, `mon.py` sudah ada (stubs), Anda tinggal edit isinya (tidak perlu langkah di bawah).
Tapi jika ingin membuat file baru (misal `new_tool.py`):

1. Buat file `ai_logic/aisub/new_tool.py`.
2. Isi dengan Template Standar di atas.
3. Buka `ai_logic/aisub/registry.py`.
4. Tambahkan import dan daftarkan di dict `COMMANDS`:

```python
# Di file registry.py

# ... import lain
from . import new_tool  # <--- Import modul baru

COMMANDS = {
    # ... command lain
    "new-tool": new_tool,  # <--- Daftarkan string perintah
}
```
5. Selesai. Command bisa dipanggil via: `ai new-tool`.
