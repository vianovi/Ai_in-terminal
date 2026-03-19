# 📦 XRAY — Dependency Installation Guide
# =========================================
# Jalankan di terminal Fish Shell (Fedora)

## Install semua sekaligus:

```fish
pip install requests phonenumbers dnspython \
            Pillow numpy stegano exifread \
            python-magic python-nmap psutil \
            --break-system-packages
```

## Install libmagic system (untuk python-magic):

```fish
sudo dnf install file-libs
```

## Install traceroute & net-tools (untuk xray net):

```fish
sudo dnf install traceroute net-tools nmap
```

---

## Per Modul:

### xray osint:
```fish
pip install requests phonenumbers dnspython --break-system-packages
```

### xray steg:
```fish
pip install Pillow numpy stegano exifread --break-system-packages
```

### xray file:
```fish
pip install python-magic --break-system-packages
sudo dnf install file-libs
```

### xray net:
```fish
pip install python-nmap psutil dnspython --break-system-packages
sudo dnf install nmap traceroute net-tools
```

---

## Verifikasi instalasi:

```fish
python3 -c "
import requests, phonenumbers, dns, PIL, stegano, exifread, psutil
print('✅ Semua library OK')
"
```

---

## Lokasi file yang perlu di-copy ke project:

```
# File BARU (masuk ke folder xray/ yang sudah ada):
commands/xray/__init__.py
commands/xray/command.py
commands/xray/ui.py
commands/xray/ai_helper.py
commands/xray/modules/__init__.py
commands/xray/modules/osint.py
commands/xray/modules/steg.py
commands/xray/modules/file_forensics.py
commands/xray/modules/net.py

# File PATCH (replace existing):
core/paths.py       ← tambah section Xray paths di bagian bawah
```

> bridge.py sudah di-patch sebelumnya (entry xray sudah ada).
> storage.py sudah di-patch sebelumnya (duplikasi sudah dihapus).
