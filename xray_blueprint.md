# 🔬 XRAY — Blueprint & Design Document (Final)
> **Project:** Custom Python Framework Architecture (AI in Terminal)  
> **Command Slot:** Root Command #4 dari maksimal 5  
> **Status:** Planning Phase → Ready for Development  
> **Author:** Silvia × Livi  
> **Last Updated:** Hasil diskusi lengkap sesi pertama  

---

## 🎯 Filosofi & Jiwa `xray`

> **"Melihat lebih dalam dari yang kasat mata."**

`xray` adalah tool investigasi digital berbasis terminal. Bukan sekedar wrapper biasa — `xray` dirancang dengan rasa **"digital detective"**: setiap subcommand memberi kemampuan untuk melihat sesuatu yang tersembunyi, baik itu di jaringan, di dalam file, di dalam gambar, maupun di ranah informasi publik.

### Target Penggunaan:
- 📚 **Bahan belajar & eksplorasi mandiri** — membangun pemahaman dari bawah
- 🏆 **Persiapan CTF (Capture The Flag)** — kategori Steganography & Forensics
- 🔍 **Daily investigasi ringan** — spam number, cek domain, analisis file mencurigakan
- 🧪 **Research & pengembangan skill cybersecurity lebih dini** — siap sebelum dibutuhkan

---

## 📐 Struktur Command Global

```
xray [modul] [subcommand] [target] [--options]
```

### Flag Global:

| Flag | Fungsi |
|---|---|
| `xray --help` | Tampilkan semua modul + contoh penggunaan |
| `--pretty` | Output dengan tampilan rich/colorful + tabel |
| `--export` | Simpan hasil ke file `.txt` atau `.json` |
| `--ai` | Aktifkan AI interpreter — analisis hasil dalam bahasa manusia |

> ⚠️ **`--ai` hanya aktif kalau dipanggil eksplisit.** Tidak aktif by default — tidak ganggu output normal.

---

## 🗂️ Modul — Priority & Overview

| Priority | Modul | Tagline |
|---|---|---|
| 🥇 1st | `xray osint` | Open Source Intelligence — info dari data publik |
| 🥈 2nd | `xray steg` | Steganography — pesan tersembunyi di dalam file |
| 🥉 3rd | `xray file` | File Forensics — analisis dalam sebuah file |
| 4th | `xray net` | Network Inspector — monitoring & analisis jaringan |

> Development dilakukan **bertahap sesuai priority** di atas.

---

## 🌍 Modul 1 — `xray osint`
**Tagline:** *"Dari satu titik informasi publik, dapatkan gambaran yang lebih besar."*  
**Priority:** 🥇 Pertama dibangun

### Subcommands:

```fish
xray osint ip <address>       # IP geolocation + ISP info
xray osint domain <domain>    # Domain/subdomain enumeration + WHOIS
xray osint email <email>      # Cek data breach history
xray osint web <url>          # HTTP headers + tech stack detection
xray osint phone <number>     # Carrier, validasi format, spam reputation
```

### Detail Fitur Per Subcommand:

| Subcommand | Output yang Dihasilkan |
|---|---|
| `ip` | Negara, kota, ISP, koordinat (approx), ASN |
| `domain` | WHOIS info, registrar, expiry date, subdomain list |
| `email` | Apakah pernah bocor, di breach mana, kapan |
| `web` | Server, framework, CMS, security headers |
| `phone` | Operator/carrier, negara asal, format valid, laporan spam |

### Contoh Output `xray osint phone`:
```
📱 Phone Analysis
─────────────────────────────
Number    : +62 812-3456-7890
Country   : Indonesia 🇮🇩
Carrier   : Telkomsel
Type      : Mobile
Valid     : ✅ Yes
Spam Rep  : ⚠️ Dilaporkan 47x sebagai spam/penipu
```

### Contoh dengan `--ai` flag:
```fish
xray osint ip 185.220.101.1 --ai

📡 Raw Result: [... data IP ...]

🤖 AI Analysis:
"IP ini terdeteksi sebagai Tor exit node di Jerman.
Reputasinya buruk — sering dilaporkan terkait aktivitas
scanning. Jika muncul di log server kamu, pertimbangkan
untuk block di firewall."
```

### Library Python:
- `phonenumbers` — Google's libphonenumber, validasi & carrier
- `requests` — HTTP calls ke public API
- `dnspython` — DNS resolution & enumeration
- `ipwhois` — IP intelligence

### Public API yang Digunakan:
- `ip-api.com` — geolocation IP (free, no key needed)
- `numverify` — validasi nomor HP (free tier)
- `HaveIBeenPwned` — data breach checker (free tier)

### Konteks Legal & Etika:
> ✅ Semua data yang diakses adalah **data publik**  
> ✅ Data hanya tersimpan **lokal** pada device Silvia  
> ✅ Dapat digunakan sebagai **bahan pelaporan** ke Kominfo / Kepolisian  
> ✅ Digunakan untuk keperluan **pribadi & pembelajaran**  
> ❌ Dilarang disebarkan atau digunakan untuk merugikan pihak lain  

---

## 🖼️ Modul 2 — `xray steg`
**Tagline:** *"Gambar biasa bisa menyimpan rahasia — dan kamu bisa membacanya."*  
**Priority:** 🥈 Kedua dibangun  
**CTF Relevance:** ⭐⭐⭐⭐⭐ Sangat relevan untuk kompetisi

### Subcommands:

```fish
xray steg detect <file>              # Deteksi apakah ada hidden message
xray steg extract <file>             # Ekstrak pesan tersembunyi dari file
xray steg embed <file> <message>     # Sisipkan pesan ke dalam gambar
xray steg meta <file>                # Analisis metadata lengkap file
```

### Detail Cara Kerja:

| Subcommand | Cara Kerja |
|---|---|
| `detect` | LSB (Least Significant Bit) analysis — deteksi anomali pixel |
| `extract` | Baca bit tersembunyi dan rekonstruksi pesan asli |
| `embed` | Modifikasi LSB pixel untuk sisipkan teks rahasia |
| `meta` | Baca EXIF data: kamera, GPS, software, timestamp |

### Format File yang Didukung:
- **PNG** ✅ — paling ideal untuk steganography (lossless)
- **JPG/JPEG** ✅
- **BMP** ✅

### Library Python:
- `stegano` — LSB steganography encode/decode
- `Pillow (PIL)` — image processing & manipulation
- `exifread` — metadata/EXIF extraction

### CTF Relevance:
> 🏆 Kategori **Steganography** adalah salah satu kategori paling umum di CTF.
> Tool ini bisa langsung dipakai untuk:
> - Solve CTF challenge yang embed flag di dalam gambar
> - Latihan teknik LSB analysis manual
> - Memahami konsep hidden data dalam media digital
> - Persiapan materi kuliah / seminar cybersecurity

### Bahan Uji Coba Legal:
| Sumber | Keterangan |
|---|---|
| **Wikimedia Commons** | Gambar bebas lisensi, langsung pakai |
| **BOSS Base dataset** | Benchmark akademik steganography, free download |
| **BOWS2 dataset** | Standard research steganography |
| **Foto milik sendiri** | Paling aman — embed pesan test ke foto sendiri |

---

## 📁 Modul 3 — `xray file`
**Tagline:** *"File tidak selalu jujur soal dirinya sendiri."*  
**Priority:** 🥉 Ketiga dibangun  
**CTF Relevance:** ⭐⭐⭐⭐ Relevan untuk kategori Forensics

### Subcommands:

```fish
xray file analyze <file>     # Analisis lengkap sebuah file
xray file hash <file>        # Generate hash MD5 + SHA256
xray file strings <file>     # Ekstrak readable strings dari binary
xray file entropy <file>     # Cek apakah file dienkripsi/di-pack
```

### Detail Cara Kerja:

| Subcommand | Cara Kerja |
|---|---|
| `analyze` | Deteksi tipe file asli via **magic bytes** (bukan dari ekstensi) |
| `hash` | Generate MD5, SHA1, SHA256 untuk verifikasi integritas |
| `strings` | Cari dan tampilkan teks readable di dalam file binary |
| `entropy` | Hitung entropi — nilai tinggi = kemungkinan file terenkripsi/packed |

### Library Python:
- `python-magic` — magic bytes detection
- `hashlib` — hashing (built-in Python)
- `binwalk` — via subprocess, deep file structure analysis

### Bahan Uji Coba Legal:
| Sumber | Keterangan |
|---|---|
| **MalwareBazaar** (abuse.ch) | Sampel file untuk research, gratis & legal |
| **theZoo (GitHub)** | Malware repository khusus education |
| **File buatan sendiri** | Rename ekstensi, test deteksi `xray file` |

> ⚠️ **PENTING:** File malware sample **JANGAN dijalankan** — hanya untuk dianalisis strukturnya!

---

## 🌐 Modul 4 — `xray net`
**Tagline:** *"Siapa yang ada di jaringanmu — dan apa yang mereka lakukan."*  
**Priority:** 4th — dibangun terakhir

### Subcommands:

```fish
xray net scan <host/range>    # Port scan + service detection
xray net monitor              # Real-time traffic monitoring
xray net arp                  # Deteksi semua device di jaringan lokal
xray net trace <host>         # Traceroute visual ke target
xray net lookup <domain/ip>   # DNS + WHOIS lookup cepat
```

### Library Python:
- `scapy` — packet crafting & sniffing
- `psutil` — system & network interface stats
- `python-nmap` — port scanning wrapper
- `socket` — low-level network operations (built-in)

---

## 🤖 AI Integration (`--ai` flag)

### Konsep:
> AI **hanya aktif** saat flag `--ai` dipanggil eksplisit.  
> Fungsinya: **interpret & jelaskan** raw output dalam bahasa manusia.  
> Fokus pada **"ini artinya apa"** — bukan advisor yang nebak langkah selanjutnya.

### Keputusan Design AI Engine:

| Aspek | Keputusan | Alasan |
|---|---|---|
| **Backend** | Always API — skip `backend_mode` config utama | i3-1315U + 16GB tidak optimal untuk LLM local |
| **API Key** | Reuse dari `config.json` existing (env variable) | Zero duplikasi, maintenance mudah |
| **Model** | Diatur sendiri di section `xray` config | Tidak ganggu model `cmd`/`ask` |
| **Provider** | Bisa diganti via config — tidak hardcode | Future-proof & fleksibel |
| **Default** | `gemini-2.5-flash` | Free tier, paling cerdas saat ini |
| **Local/Ollama** | ❌ Tidak dipakai sama sekali | Hardware constraint |

### Tambahan di `config.json`:

```json
{
  "backend_mode": "auto",
  "api": {
    "provider": "gemini",
    "active_model": "gemini-2.5-flash",
    "gemini_api_key_env": "GEMINI_API_KEY"
  },
  "xray": {
    "provider": "gemini",
    "model": "gemini-2.5-flash"
  }
}
```

> Ganti provider xray → edit `xray.provider` + `xray.model`  
> Config `cmd` dan `ask` **tidak terpengaruh sama sekali**

### Provider Roadmap:

| Provider | Free Tier | Status |
|---|---|---|
| **Gemini** | ✅ | ✅ Default — Phase 1 |
| **Groq** | ✅ | 🔜 Phase 2 |
| **OpenRouter** | ✅ limited | 🔜 Phase 2 |

### System Prompt Per Modul:

```python
XRAY_SYSTEM_PROMPTS = {
    "osint": "You are a cybersecurity OSINT analyst. Interpret the following public data findings clearly. Highlight anything suspicious or noteworthy. Be concise and informative.",
    "steg" : "You are a digital forensics expert specializing in steganography. Interpret the following analysis results. Explain what was found and its significance.",
    "file" : "You are a malware analyst and file forensics expert. Interpret the following file analysis results. Flag anything suspicious.",
    "net"  : "You are a network security analyst. Interpret the following network data. Highlight anomalies or security concerns."
}
```

### Flow `--ai`:

```
xray osint ip 8.8.8.8 --ai
        ↓
load_config()                    ← baca config.json
        ↓
xray_cfg = cfg["xray"]           ← ambil provider & model xray
        ↓
key = gemini_key(cfg)            ← reuse API key existing
        ↓
raw_result = osint.scan_ip()     ← jalankan scan biasa dulu
        ↓
generate(xray_cfg, [
  system : XRAY_SYSTEM_PROMPTS["osint"],
  user   : raw_result            ← inject hasil scan sebagai context
])
        ↓
print AI interpretation
```

---

## 🏗️ Arsitektur & Struktur Folder

```
system_logic/
├── commands/
│   ├── ask/
│   ├── cmd/
│   ├── ai/
│   ├── toolkit/
│   └── xray/                        ← BARU
│       ├── __init__.py
│       ├── command.py               ← entry point, argument parser
│       ├── ai_helper.py             ← xray AI engine (--ai flag)
│       └── modules/
│           ├── __init__.py
│           ├── osint.py             ← Modul 1
│           ├── steg.py              ← Modul 2
│           ├── file_forensics.py    ← Modul 3
│           └── net.py               ← Modul 4
└── core/
    ├── config.py                    ← reuse load_config()
    ├── paths.py                     ← tambah XRAY_ROOT
    ├── storage.py                   ← reuse atomic_write_json()
    └── ...
```

### Reuse dari Existing Files:

| File Existing | Fungsi yang Direuse | Dipakai di |
|---|---|---|
| `api_gemini.py` | `generate()`, `gemini_key()` | `ai_helper.py` |
| `config.py` | `load_config()` | `command.py`, `ai_helper.py` |
| `utils.py` | `http_json()` | semua modul osint |
| `storage.py` | `atomic_write_json()` | flag `--export` |
| `paths.py` | path definitions | tambah `XRAY_ROOT` |

### Perubahan di File Existing:

**`bridge.py`** — tambah 1 entry dispatcher:
```python
if root_cmd == "xray":
    from system_logic.commands.xray.command import handle
    return handle(rest, cfg)
```

**`paths.py`** — tambah 2 path:
```python
XRAY_ROOT     = APP_DIR / "xray"
XRAY_LOG_PATH = XRAY_ROOT / "xray.log"
```

**`config.py`** — tambah default section:
```python
"xray": {
    "provider": "gemini",
    "model": "gemini-2.5-flash"
}
```

---

## ⚠️ Pre-Development Cleanup

> Ditemukan saat audit codebase sebelum planning:

**Issue: `storage.py` — Duplikasi Fungsi**
Fungsi-fungsi MON berikut muncul **dua kali** dalam satu file:
`file_lock`, `init_mon_history_db`, `snapshot_metric_sql`,
`get_metric_value_sql`, `list_metric_dates_sql`,
`get_all_metrics_sql`, `migrate_json_to_sqlite`

Diduga terjadi karena copy-paste saat penambahan fitur MON yang tidak dibersihkan.

> **Action wajib:** Bersihkan duplikasi sebelum development `xray` dimulai — sisakan **satu definisi saja** per fungsi.

---

## 🔧 Environment & Dependencies

| Komponen | Detail |
|---|---|
| Language | Python 3.x |
| OS | Fedora KDE Plasma |
| Shell | **Fish Shell** — semua contoh command dalam Fish syntax |
| Hardware | Intel i3-1315U + 16GB RAM |
| AI Backend | **API Only** — local LLM tidak digunakan untuk `xray` |

---

## 📅 Development Roadmap

```
Phase 0 → Pre-dev cleanup: fix storage.py duplikasi
        ↓
Phase 1 → xray osint    (OSINT + phone scanner)
        ↓
Phase 2 → xray steg     (Steganography + CTF prep)
        ↓
Phase 3 → xray file     (File Forensics)
        ↓
Phase 4 → xray net      (Network Inspector)
        ↓
Phase 5 → Polish: --ai integration penuh semua modul
                  + provider tambahan (Groq, OpenRouter)
```

---

## 📌 Keputusan Final yang Sudah Disepakati

| # | Keputusan | Alasan |
|---|---|---|
| 1 | `xray` dapat 1 root command slot | Identity unik, tidak overlap command lain |
| 2 | 4 modul: osint, steg, file, net | Coverage lengkap network hingga forensics |
| 3 | Priority: osint → steg → file → net | Berdasarkan interest & CTF relevance |
| 4 | `--ai` hanya via flag eksplisit | Clean UX, tidak ganggu output normal |
| 5 | AI = interpreter hasil (Opsi A) | Reliable, tidak nebak langkah selanjutnya |
| 6 | AI engine = Always API only | Hardware i3 + 16GB tidak optimal LLM local |
| 7 | API key reuse dari config existing | Zero duplikasi, satu tempat maintenance |
| 8 | Model & provider xray = section sendiri | Fleksibel, tidak ganggu config cmd/ask |
| 9 | Provider bisa diganti via config | Future-proof, tidak hardcode Gemini |
| 10 | Default model: `gemini-2.5-flash` | Free tier, paling cerdas saat ini |
| 11 | `phone` scanner masuk osint | Data publik, legal untuk personal & pelaporan |
| 12 | Fix `storage.py` sebelum dev mulai | Duplikasi fungsi MON harus dibersihkan dulu |

---

## 🔮 Future Development (Post Phase 5)

- Support provider tambahan: Groq, OpenRouter
- `xray steg` support format audio (MP3, WAV)
- `xray osint` integrasi Shodan API (level lanjut)
- Export report format PDF
- Mode interaktif `xray --interactive`

---

*Document ini adalah hasil diskusi lengkap sesi pertama — Silvia × Livi*  
*"Belajar lebih dini, siap pada waktunya."* 🌸  
*Blueprint ini adalah living document — akan diupdate seiring development*
