"""
commands/xray/modules/osint.py
===============================
Modul OSINT untuk command `xray`.

Aturan isolasi:
    BOLEH import dari luar xray/:
        - stdlib saja (socket, json, re, os)
        - third-party: requests, phonenumbers, dnspython, ipwhois
    UI via xray/ui.py saja.
    TIDAK BOLEH import dari luar xray/ selain stdlib/third-party.

Subcommands & sumber data:

    ip     → scan_ip(address)
             Layer 1: ip-api.com      → geolocation, ISP, ASN, proxy/VPN flag
             Layer 2: ipwhois RDAP    → network range, CIDR, abuse contact

    domain → scan_domain(domain)
             DNS records (A/AAAA/MX/NS/TXT/CNAME) via dnspython
             WHOIS via whois.vu API
             Subdomain enumeration via DNS brute force (common wordlist)

    email  → scan_email(email)
             Source 1: HaveIBeenPwned v3  → paling lengkap, free daftar key
             Source 2: BreachDirectory    → free, no key needed

    web    → scan_web(url)
             HTTP headers + response time + redirect chain
             Tech stack detection (server, CMS, framework) dari header & body
             Security headers analysis (HSTS, CSP, X-Frame, dll)
             Cookie security flags

    phone  → scan_phone(number)
             Layer 1: Google libphonenumber → validasi, carrier, format, timezone
             Layer 2: AbstractAPI (free, 250 req/bulan, butuh key)
             Layer 3: Spam reputation → NumLookup + PhoneValidator (free)

Semua fungsi return dict. Key '_error' menandakan scan gagal.

Exported:
    scan_ip(address)    -> dict
    scan_domain(domain) -> dict
    scan_email(email)   -> dict
    scan_web(url)       -> dict
    scan_phone(number)  -> dict
"""
from __future__ import annotations

import os
import re
import socket
from typing import Any

from system_logic.commands.xray.ui import print_xray_error, print_xray_info


# ============================================================
# [1] Internal helpers
# ============================================================

def _err(msg: str) -> dict[str, Any]:
    """
    Buat dict error standar.
    Key '_error' akan dideteksi command.py sebagai scan gagal.

    Args:
        msg: Pesan error.
    """
    print_xray_error(msg)
    return {"_error": msg}


def _get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 10,
) -> dict | list | None:
    """
    HTTP GET wrapper — return parsed JSON atau None jika gagal.
    Tidak raise exception — caller tidak perlu try/except.

    Args:
        url     : URL target.
        params  : Query parameters.
        headers : HTTP headers tambahan.
        timeout : Timeout dalam detik.
    """
    try:
        import requests
        resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


# ============================================================
# [2] IP Scanner
# ============================================================

def scan_ip(address: str) -> dict[str, Any]:
    """
    Scan informasi lengkap sebuah IP address.

    Layer 1 — ip-api.com (free, no key):
        Geolocation, ISP, ASN, koordinat, proxy/VPN/hosting/mobile detection.

    Layer 2 — ipwhois RDAP:
        Network range, CIDR, abuse contact email dari RDAP registry.

    Args:
        address: IP address target (IPv4 atau IPv6).

    Returns:
        Dict hasil scan. Key '_error' jika gagal.
    """
    try:
        import requests
    except ImportError:
        return _err("Library 'requests' tidak terinstall. Jalankan: pip install requests")

    address = address.strip()
    if not address:
        return _err("IP address tidak boleh kosong.")

    result: dict[str, Any] = {
        "_module"    : "osint",
        "_subcommand": "ip",
        "target"     : address,
    }

    # --- Layer 1: ip-api.com ---
    try:
        import requests
        resp = requests.get(
            f"http://ip-api.com/json/{address}",
            params={
                "fields": (
                    "status,message,country,countryCode,"
                    "regionName,city,zip,lat,lon,"
                    "isp,org,as,asname,query,"
                    "proxy,hosting,mobile"
                )
            },
            timeout=10,
        )
        data = resp.json()

        if data.get("status") == "success":
            result["country"]      = f"{data.get('country', 'N/A')} ({data.get('countryCode', '??')})"
            result["region"]       = data.get("regionName", "N/A")
            result["city"]         = data.get("city", "N/A")
            result["zip_code"]     = data.get("zip", "N/A")
            result["coordinates"]  = f"{data.get('lat', '?')}, {data.get('lon', '?')}"
            result["isp"]          = data.get("isp", "N/A")
            result["organization"] = data.get("org", "N/A")
            result["asn"]          = data.get("as", "N/A")
            result["asn_name"]     = data.get("asname", "N/A")
            result["proxy_vpn"]    = "⚠️  Yes" if data.get("proxy") else "✅ No"
            result["hosting"]      = "⚠️  Yes (datacenter)" if data.get("hosting") else "✅ No"
            result["mobile"]       = "Yes" if data.get("mobile") else "No"
        else:
            result["ip_api_note"]  = data.get("message", "ip-api tidak menemukan data")

    except Exception as ex:
        result["ip_api_note"] = f"ip-api gagal: {ex}"

    # --- Layer 2: ipwhois RDAP ---
    try:
        from ipwhois import IPWhois
        rdap    = IPWhois(address).lookup_rdap(depth=1)
        network = rdap.get("network", {})

        result["network_name"]  = network.get("name", "N/A")
        result["network_cidr"]  = network.get("cidr", "N/A")
        result["network_range"] = (
            f"{network.get('start_address', '?')} → {network.get('end_address', '?')}"
        )

        # Abuse contact dari entities
        abuse_emails: list[str] = []
        for entity in rdap.get("entities", []):
            if "abuse" in entity.get("roles", []):
                for contact in entity.get("contact", {}).get("email", []):
                    val = contact.get("value", "")
                    if val:
                        abuse_emails.append(val)

        result["abuse_contact"] = ", ".join(abuse_emails) if abuse_emails else "N/A"

    except ImportError:
        result["network_detail"] = "ipwhois tidak terinstall — pip install ipwhois"
    except Exception as ex:
        result["network_detail"] = f"RDAP lookup gagal: {ex}"

    # --- Flags ---
    flags: list[str] = []
    if result.get("proxy_vpn", "").startswith("⚠️"):
        flags.append("⚠️  Terdeteksi sebagai Proxy / VPN")
    if result.get("hosting", "").startswith("⚠️"):
        flags.append("⚠️  IP dari hosting / datacenter (bukan residential)")
    result["flags"] = flags

    return result


# ============================================================
# [3] Domain Scanner
# ============================================================

def scan_domain(domain: str) -> dict[str, Any]:
    """
    Scan informasi domain: WHOIS, DNS records, subdomain enumeration.

    Data yang dikumpulkan:
        - Resolve IP dari domain
        - DNS records: A, AAAA, MX, NS, TXT, CNAME (via dnspython)
        - WHOIS: registrar, created, expires, updated (via whois.vu)
        - Subdomain enumeration via DNS brute force (30 common subs)

    Args:
        domain: Domain target (contoh: google.com).

    Returns:
        Dict hasil scan. Key '_error' jika gagal.
    """
    try:
        import requests
    except ImportError:
        return _err("Library 'requests' tidak terinstall.")

    # Bersihkan input — hapus http/https dan path
    domain = re.sub(r"^https?://", "", domain.strip().lower()).split("/")[0]
    if not domain:
        return _err("Domain tidak boleh kosong.")

    result: dict[str, Any] = {
        "_module"    : "osint",
        "_subcommand": "domain",
        "target"     : domain,
    }

    # --- Resolve IP ---
    try:
        result["resolved_ip"] = socket.gethostbyname(domain)
    except Exception:
        result["resolved_ip"] = "Tidak dapat di-resolve"

    # --- DNS Records via dnspython ---
    try:
        import dns.resolver
        dns_records: dict[str, list] = {}
        for rtype in ("A", "AAAA", "MX", "NS", "TXT", "CNAME"):
            try:
                answers          = dns.resolver.resolve(domain, rtype, lifetime=5)
                dns_records[rtype] = [str(r) for r in answers]
            except Exception:
                dns_records[rtype] = []
        result["dns_records"] = dns_records
    except ImportError:
        result["dns_records"] = {"note": "dnspython tidak terinstall — pip install dnspython"}

    # --- WHOIS via whois.vu ---
    try:
        import requests
        whois_data = _get(f"https://api.whois.vu/?q={domain}")
        if whois_data and isinstance(whois_data, dict):
            result["whois"] = {
                "registrar"   : whois_data.get("registrar", "N/A"),
                "created"     : whois_data.get("created", "N/A"),
                "expires"     : whois_data.get("expires", "N/A"),
                "updated"     : whois_data.get("updated", "N/A"),
                "name_servers": whois_data.get("nameservers", "N/A"),
            }
        else:
            result["whois"] = {"note": "WHOIS tidak tersedia untuk domain ini"}
    except Exception:
        result["whois"] = {"note": "WHOIS gagal diambil"}

    # --- Subdomain Enumeration ---
    _COMMON_SUBS = [
        "www", "mail", "ftp", "admin", "api", "dev", "staging",
        "test", "blog", "shop", "app", "portal", "vpn", "remote",
        "cpanel", "webmail", "smtp", "pop", "imap", "ns1", "ns2",
        "cdn", "static", "media", "img", "assets", "m", "mobile",
        "dashboard", "beta",
    ]

    print_xray_info(f"Subdomain enumeration ({len(_COMMON_SUBS)} wordlist)...")

    found_subs: list[str] = []
    for sub in _COMMON_SUBS:
        try:
            fqdn = f"{sub}.{domain}"
            socket.gethostbyname(fqdn)
            found_subs.append(fqdn)
        except Exception:
            pass

    result["subdomains_found"] = found_subs if found_subs else ["Tidak ditemukan"]
    result["subdomains_count"] = len(found_subs)

    return result


# ============================================================
# [4] Email Breach Scanner
# ============================================================

def scan_email(email: str) -> dict[str, Any]:
    """
    Cek apakah email pernah bocor di data breach.

    Multi-source dengan fallback chain:
        Source 1: HaveIBeenPwned v3  — paling lengkap
                  Free daftar key di: haveibeenpwned.com/API/Key
                  Jika tidak ada key → tetap coba (rate limited)
        Source 2: BreachDirectory    — free, no key needed
                  Fallback otomatis jika HIBP tidak tersedia

    Data per breach:
        - Nama breach & domain
        - Tanggal breach
        - Jumlah akun yang bocor
        - Tipe data yang bocor (email, password, phone, dll)

    Args:
        email: Alamat email target.

    Returns:
        Dict hasil scan. Key '_error' jika gagal.
    """
    try:
        import requests
    except ImportError:
        return _err("Library 'requests' tidak terinstall.")

    email = email.strip().lower()
    if not email or "@" not in email:
        return _err("Format email tidak valid.")

    result: dict[str, Any] = {
        "_module"        : "osint",
        "_subcommand"    : "email",
        "target"         : email,
        "sources_checked": [],
    }

    breaches_all: list[dict] = []

    # --- Source 1: HaveIBeenPwned v3 ---
    try:
        import requests

        # Ambil key dari env jika ada
        hibp_key = os.environ.get("HIBP_API_KEY", "").strip()
        headers  = {"User-Agent": "xray-osint-tool"}
        if hibp_key:
            headers["hibp-api-key"] = hibp_key

        hibp_resp = requests.get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
            headers=headers,
            params={"truncateResponse": "false"},
            timeout=10,
        )

        if hibp_resp.status_code == 200:
            for b in hibp_resp.json():
                breaches_all.append({
                    "source"    : "HaveIBeenPwned",
                    "name"      : b.get("Name", "N/A"),
                    "domain"    : b.get("Domain", "N/A"),
                    "date"      : b.get("BreachDate", "N/A"),
                    "pwn_count" : f"{b.get('PwnCount', 0):,} akun bocor",
                    "data_types": ", ".join(b.get("DataClasses", [])),
                })
            result["sources_checked"].append("HaveIBeenPwned ✅")

        elif hibp_resp.status_code == 404:
            result["sources_checked"].append("HaveIBeenPwned ✅ (tidak ada hasil)")
        elif hibp_resp.status_code == 401:
            result["sources_checked"].append(
                "HaveIBeenPwned ⚠️ (butuh API key — daftar gratis di haveibeenpwned.com/API/Key)"
            )
        else:
            result["sources_checked"].append(
                f"HaveIBeenPwned ❌ (HTTP {hibp_resp.status_code})"
            )

    except Exception as ex:
        result["sources_checked"].append(f"HaveIBeenPwned ❌ ({ex})")

    # --- Source 2: BreachDirectory (free, no key) ---
    try:
        import requests
        bd_resp = requests.get(
            "https://breachdirectory.org/api",
            params={"func": "auto", "term": email},
            headers={"User-Agent": "xray-osint-tool"},
            timeout=10,
        )

        if bd_resp.status_code == 200:
            bd_data = bd_resp.json()
            if bd_data.get("success") and bd_data.get("result"):
                for entry in bd_data["result"]:
                    src_name = str(entry.get("sources", "BreachDirectory"))
                    # Skip duplikat dari HIBP
                    if not any(b.get("name") == src_name for b in breaches_all):
                        breaches_all.append({
                            "source"    : "BreachDirectory",
                            "name"      : src_name,
                            "domain"    : "N/A",
                            "date"      : entry.get("date", "N/A"),
                            "pwn_count" : "N/A",
                            "data_types": str(entry.get("fields", "N/A")),
                        })
                result["sources_checked"].append("BreachDirectory ✅")
            else:
                result["sources_checked"].append("BreachDirectory ✅ (tidak ada hasil)")
        else:
            result["sources_checked"].append(
                f"BreachDirectory ❌ (HTTP {bd_resp.status_code})"
            )

    except Exception as ex:
        result["sources_checked"].append(f"BreachDirectory ❌ ({ex})")

    # --- Rangkum hasil ---
    if breaches_all:
        result["status"]       = f"🚨 Ditemukan dalam {len(breaches_all)} breach!"
        result["breach_count"] = len(breaches_all)
        result["breaches"]     = breaches_all
        result["flags"]        = [f"🚨 Email ini bocor di {len(breaches_all)} database!"]
    else:
        result["status"]       = "✅ Tidak ditemukan dalam breach database yang dicek"
        result["breach_count"] = 0
        result["breaches"]     = []
        result["flags"]        = []

    return result


# ============================================================
# [5] Web Inspector
# ============================================================

def scan_web(url: str) -> dict[str, Any]:
    """
    Inspect HTTP headers dan deteksi tech stack sebuah website.

    Data yang dikumpulkan:
        - Status code, final URL (setelah redirect), response time
        - Redirect chain jika ada
        - Tech stack: server, powered-by, CMS/framework dari header & body
        - Security headers: HSTS, X-Frame-Options, CSP, X-Content-Type, dll
        - Cookie security flags: HttpOnly, Secure, SameSite

    Args:
        url: URL target (contoh: https://example.com).

    Returns:
        Dict hasil scan. Key '_error' jika gagal.
    """
    try:
        import requests
        import time
    except ImportError:
        return _err("Library 'requests' tidak terinstall.")

    url = url.strip()
    if not url:
        return _err("URL tidak boleh kosong.")

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result: dict[str, Any] = {
        "_module"    : "osint",
        "_subcommand": "web",
        "target"     : url,
    }

    try:
        import requests, time

        t_start = time.time()
        resp    = requests.get(url, timeout=10, allow_redirects=True)
        t_end   = time.time()
        headers = dict(resp.headers)

        result["status_code"]   = resp.status_code
        result["final_url"]     = resp.url
        result["response_time"] = f"{(t_end - t_start) * 1000:.0f} ms"

        # --- Redirect chain ---
        if resp.history:
            result["redirect_chain"] = [str(r.url) for r in resp.history]

        # --- Tech stack ---
        tech: list[str] = []
        _headers_tech = {
            "Server"      : "Server",
            "X-Powered-By": "Powered-By",
            "X-Generator" : "Generator",
            "Via"         : "Via (proxy)",
        }
        for header, label in _headers_tech.items():
            val = headers.get(header, "")
            if val:
                tech.append(f"{label:<14}: {val}")

        # CMS/framework detection dari body
        body = resp.text[:8000]
        _CMS_SIGS: dict[str, list[str]] = {
            "WordPress" : ["wp-content", "wp-includes"],
            "Drupal"    : ["Drupal.settings", "/sites/default/files"],
            "Joomla"    : ["/components/com_", "Joomla!"],
            "Shopify"   : ["cdn.shopify.com", "Shopify.theme"],
            "Laravel"   : ["laravel_session", "XSRF-TOKEN"],
            "Django"    : ["csrfmiddlewaretoken"],
            "Next.js"   : ["__NEXT_DATA__", "_next/static"],
            "Nuxt.js"   : ["__NUXT__", "_nuxt/"],
            "Angular"   : ["ng-version", "ng-app"],
        }
        for fw, patterns in _CMS_SIGS.items():
            if any(p.lower() in body.lower() for p in patterns):
                tech.append(f"Framework      : {fw}")

        result["tech_stack"] = tech if tech else ["Tidak terdeteksi dari header/body"]

        # --- Security headers ---
        _SEC = {
            "Strict-Transport-Security": "HSTS",
            "X-Frame-Options"          : "X-Frame-Options",
            "X-Content-Type-Options"   : "X-Content-Type-Options",
            "Content-Security-Policy"  : "CSP",
            "X-XSS-Protection"         : "X-XSS-Protection",
            "Referrer-Policy"          : "Referrer-Policy",
            "Permissions-Policy"       : "Permissions-Policy",
        }
        sec: dict[str, str] = {}
        missing_critical: list[str] = []

        for header, label in _SEC.items():
            val = headers.get(header)
            if val:
                sec[label] = f"✅ {val[:80]}"
            else:
                sec[label] = "❌ Missing"
                if header in (
                    "Strict-Transport-Security",
                    "X-Frame-Options",
                    "Content-Security-Policy",
                ):
                    missing_critical.append(label)

        result["security_headers"] = sec

        # --- Cookie flags ---
        cookies_info: list[str] = []
        for cookie in resp.cookies:
            cflags: list[str] = []
            if cookie.secure:
                cflags.append("Secure")
            if cookie.has_nonstandard_attr("HttpOnly"):
                cflags.append("HttpOnly")
            samesite = cookie.get_nonstandard_attr("SameSite", "")
            if samesite:
                cflags.append(f"SameSite={samesite}")
            cookies_info.append(
                f"{cookie.name}: "
                + (", ".join(cflags) if cflags else "⚠️ No security flags")
            )
        if cookies_info:
            result["cookies"] = cookies_info

        # --- Flags ---
        flags: list[str] = []
        if missing_critical:
            flags.append(f"⚠️  Security headers missing: {', '.join(missing_critical)}")
        if not resp.url.startswith("https://"):
            flags.append("⚠️  Tidak menggunakan HTTPS!")
        result["flags"] = flags

    except Exception as ex:
        result["error"] = f"Gagal fetch URL: {ex}"

    return result


# ============================================================
# [6] Phone Scanner
# ============================================================

def scan_phone(number: str) -> dict[str, Any]:
    """
    Analisis lengkap nomor telepon: validasi, carrier, risk, dan breach history.

    Layer 1 — Google libphonenumber (phonenumbers):
        Validasi format offline, format nasional & internasional.
        Sebagai pre-check sebelum hit API.

    Layer 2 — AbstractAPI Phone Intelligence (primary, butuh key):
        Endpoint : https://phoneintelligence.abstractapi.com/v1/
        Env key  : ABSTRACT_PHONE_KEY
        Data     : carrier, line_type, location, line_status,
                   is_voip, risk_level, is_disposable,
                   is_abuse_detected, breach history

    Data yang dikumpulkan:
        - Format: nasional, internasional
        - Carrier & line type (mobile/landline/voip)
        - Lokasi: negara, region, kota, timezone
        - Status aktif/tidak
        - Risk level: low / medium / high
        - Is disposable number
        - Abuse detection
        - Breach history: jumlah breach, domain yang kena, tanggal

    Args:
        number: Nomor telepon format internasional (contoh: +6281234567890).

    Returns:
        Dict hasil scan. Key '_error' jika gagal.
    """
    try:
        import phonenumbers
        from phonenumbers import geocoder
    except ImportError:
        return _err(
            "Library 'phonenumbers' tidak terinstall. "
            "Jalankan: pip install phonenumbers"
        )

    try:
        import requests
    except ImportError:
        return _err("Library 'requests' tidak terinstall.")

    number = number.strip()
    if not number:
        return _err("Nomor telepon tidak boleh kosong.")

    result: dict[str, Any] = {
        "_module"    : "osint",
        "_subcommand": "phone",
        "target"     : number,
    }

    # --- Layer 1: libphonenumber — pre-check format ---
    try:
        parsed     = phonenumbers.parse(number, None)
        is_valid   = phonenumbers.is_valid_number(parsed)

        result["format_valid"]         = "✅ Valid" if is_valid else "❌ Tidak valid"
        result["national_format"]      = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        )
        result["international_format"] = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
        result["country_code"]         = f"+{parsed.country_code}"

        if not is_valid:
            result["note"] = "Format nomor tidak valid — hasil API mungkin tidak akurat."

    except phonenumbers.NumberParseException as ex:
        result["format_valid"] = f"❌ Parse error: {ex}"
        return result

    # --- Layer 2: AbstractAPI Phone Intelligence ---
    abstract_key = os.environ.get("ABSTRACT_PHONE_KEY", "").strip()

    if not abstract_key:
        result["api_note"] = (
            "⚠️  ABSTRACT_PHONE_KEY tidak ditemukan. "
            "Set env key dari app.abstractapi.com/api/phone-intelligence"
        )
        return result

    pi_data = _get(
        "https://phoneintelligence.abstractapi.com/v1/",
        params={"api_key": abstract_key, "phone": number},
        timeout=10,
    )

    if not pi_data or not isinstance(pi_data, dict):
        result["api_error"] = "Phone Intelligence API tidak memberikan respons."
        return result

    # --- Parse: carrier & line type ---
    carrier_data = pi_data.get("phone_carrier", {}) or {}
    result["carrier"]   = carrier_data.get("name", "N/A")
    result["line_type"] = carrier_data.get("line_type", "N/A")
    result["mcc"]       = carrier_data.get("mcc", "N/A")
    result["mnc"]       = carrier_data.get("mnc", "N/A")

    # --- Parse: location ---
    loc = pi_data.get("phone_location", {}) or {}
    result["country"]  = f"{loc.get('country_name', 'N/A')} ({loc.get('country_code', '??')})"
    result["region"]   = loc.get("region", "N/A")
    result["city"]     = loc.get("city", "N/A")
    result["timezone"] = loc.get("timezone", "N/A")

    # --- Parse: validation status ---
    val = pi_data.get("phone_validation", {}) or {}
    result["line_status"] = val.get("line_status", "N/A")
    result["is_voip"]     = "⚠️  Yes" if val.get("is_voip") else "✅ No"
    result["minimum_age"] = val.get("minimum_age") or "N/A"

    # --- Parse: registration ---
    reg = pi_data.get("phone_registration", {}) or {}
    result["registered_name"] = reg.get("name") or "N/A"
    result["registered_type"] = reg.get("type") or "N/A"

    # --- Parse: risk assessment ---
    risk = pi_data.get("phone_risk", {}) or {}
    risk_level   = risk.get("risk_level", "unknown")
    is_disposable = risk.get("is_disposable", False)
    is_abuse      = risk.get("is_abuse_detected", False)

    # Risk level dengan emoji
    risk_labels = {
        "low"    : "✅ Low — nomor terlihat aman",
        "medium" : "🟡 Medium — perlu waspada",
        "high"   : "🔴 High — nomor berisiko tinggi!",
        "unknown": "❓ Unknown",
    }
    result["risk_level"]    = risk_labels.get(risk_level, f"❓ {risk_level}")
    result["is_disposable"] = "⚠️  Yes (nomor sekali pakai!)" if is_disposable else "✅ No"
    result["abuse_detected"] = "🔴 Yes — terdeteksi penyalahgunaan!" if is_abuse else "✅ No"

    # --- Parse: breach history ---
    breach = pi_data.get("phone_breaches", {}) or {}
    total_breaches    = breach.get("total_breaches") or 0
    date_first        = breach.get("date_first_breached") or "N/A"
    date_last         = breach.get("date_last_breached") or "N/A"
    breached_domains  = breach.get("breached_domains") or []

    result["breach_history"] = {
        "total_breaches"   : total_breaches,
        "first_breached"   : date_first,
        "last_breached"    : date_last,
        "breached_domains" : breached_domains if breached_domains else ["Tidak ada"],
    }

    # --- Kumpulkan flags ---
    flags: list[str] = []
    if risk_level in ("medium", "high"):
        flags.append(f"⚠️  Risk level: {risk_level.upper()}!")
    if is_disposable:
        flags.append("⚠️  Nomor disposable / sekali pakai!")
    if is_abuse:
        flags.append("🔴 Nomor terdeteksi penyalahgunaan (abuse detected)!")
    if val.get("is_voip"):
        flags.append("⚠️  Nomor VoIP — bisa jadi nomor virtual/palsu")
    if total_breaches and int(total_breaches) > 0:
        flags.append(f"🔴 Nomor pernah muncul di {total_breaches} data breach!")

    result["flags"] = flags
    return result


def _phone_type_label(ntype: Any) -> str:
    """
    Convert PhoneNumberType enum ke label readable dengan emoji.

    Args:
        ntype: PhoneNumberType value dari library phonenumbers.
    """
    try:
        from phonenumbers import PhoneNumberType
        labels = {
            PhoneNumberType.MOBILE             : "📱 Mobile",
            PhoneNumberType.FIXED_LINE         : "📞 Fixed Line",
            PhoneNumberType.FIXED_LINE_OR_MOBILE: "📱/📞 Fixed or Mobile",
            PhoneNumberType.TOLL_FREE          : "☎️  Toll Free",
            PhoneNumberType.PREMIUM_RATE       : "💰 Premium Rate",
            PhoneNumberType.VOIP               : "🌐 VoIP",
            PhoneNumberType.PAGER              : "📟 Pager",
            PhoneNumberType.UNKNOWN            : "❓ Unknown",
        }
        return labels.get(ntype, "❓ Unknown")
    except Exception:
        return "Unknown"