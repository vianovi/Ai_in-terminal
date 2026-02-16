# MON Framework - Maintenance Guide

## 📋 Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Module Responsibilities](#module-responsibilities)
3. [Data Flow](#data-flow)
4. [Critical Components](#critical-components)
5. [Common Tasks](#common-tasks)
6. [Troubleshooting](#troubleshooting)
7. [Development Guidelines](#development-guidelines)

---

## 🏗️ Architecture Overview

MON menggunakan **modular monolith pattern** dengan clear separation of concerns:

```
mon/
├── __init__.py           # Package entry point
├── command.py            # [ROUTER] CLI argument parsing & routing
├── config.py             # [CONFIG] Dynamic thresholds & settings
├── const.py              # [CONST] Path constants (history file location)
├── storage.py            # [DATABASE] Atomic writes, time-travel queries
├── sentinel.py           # [GUARDIAN] OOM Killer (3-phase logic)
├── utils.py              # [HELPERS] Shell execution, formatting, ANSI
│
├── collectors/           # [SENSORS] Read-only data collection
│   ├── __init__.py
│   ├── system.py         # CPU, RAM, Swap, Load Average
│   ├── process.py        # Top processes, process monster detection
│   ├── hardware.py       # Battery, Disk (SMART), Thermal/Fan
│   └── network.py        # Network I/O, WiFi, Ping monitoring
│
└── ui/                   # [VIEW] Rendering & display logic
    ├── __init__.py
    ├── dashboard.py      # Live monitoring HUD (main UI loop)
    ├── render.py         # Visual components (bars, sparklines, colors)
    └── reports.py        # Static reports (battery, disk, network, sensors)
```

### Design Principles

1. **Single Responsibility**: Setiap modul punya 1 tugas jelas
2. **Read-Only Collectors**: Collectors HANYA baca data, tidak memodifikasi state
3. **Separation of Concerns**:
   - Collectors = Data acquisition
   - UI = Data presentation
   - Storage = Data persistence
   - Sentinel = System protection
4. **Fail-Safe**: Semua fungsi I/O handle exceptions gracefully

---

## 🎯 Module Responsibilities

### 1. **command.py** (Router)
**Purpose**: Entry point untuk semua CLI commands

**Key Functions**:
- `handle(argv, cfg)` - Main router
- `_print_help()` - Help text generator

**Responsibilities**:
- Parse CLI arguments
- Route ke appropriate handler
- Validate psutil availability
- Handle help command

**When to Modify**:
- Menambah subcommand baru
- Mengubah argument parsing logic

---

### 2. **config.py** (Configuration Manager)
**Purpose**: Load/save dynamic configuration (thresholds, whitelist, etc)

**Key Functions**:
- `load_config()` - Load JSON config dengan fail-safe
- `save_config(data)` - Save config dengan merge logic
- `get_nested(data, keys, default)` - Safe nested dict access

**File Location**: `mon_config.json` di folder yang sama dengan script

**Configuration Structure**:
```json
{
  "version": 5.0,
  "sentinel": {
    "enabled": true,
    "thresholds": {
      "phase1_log": {"ram_pct": 87.0, "swap_pct": 50.0},
      "phase2_warn": {"ram_pct": 90.0, "swap_pct": 78.0},
      "phase3_kill": {"ram_pct": 90.0, "swap_pct": 87.0, "ram_hold_sec": 5}
    },
    "whitelist": ["systemd", "Xorg", "python3", ...]
  },
  "ui": {
    "refresh_rate": 1.0,
    "limits": {"cpu_warn": 70, "cpu_crit": 90, ...}
  }
}
```

**When to Modify**:
- Menambah config section baru
- Mengubah default values
- Menambah validation logic

---

### 3. **const.py** (Constants)
**Purpose**: Single source of truth untuk path locations

**Key Constants**:
- `MON_HISTORY_PATH` - Location of history database JSON

**When to Modify**:
- Mengubah default path locations
- Menambah constant paths baru

---

### 4. **storage.py** (Database Engine)
**Purpose**: Atomic writes, time-travel queries untuk metric history

**Key Functions**:
- `snapshot_metric(category, item_id, metric, value)` - Save daily snapshot
- `get_metric_value(category, item_id, metric, date_iso)` - Retrieve value
- `list_metric_dates(category, item_id, metric)` - List available dates
- `get_comparison_text(...)` - Generate comparison string (vs 30 days ago)

**Database Structure**:
```json
{
  "battery": {
    "BAT0": {
      "health_pct": {
        "2026-01-15": 95.0,
        "2026-01-16": 94.8,
        "2026-02-16": 94.5
      }
    }
  },
  "disk": {
    "nvme0n1": {
      "wear_pct": {"2026-01-15": 2.0, "2026-02-16": 2.1},
      "tbw": {"2026-01-15": 45.2, "2026-02-16": 45.8}
    }
  }
}
```

**Atomic Write Flow**:
1. Write to `.tmp` file
2. `fsync()` to force disk flush
3. Atomic `os.replace()` (crash-safe)

**When to Modify**:
- Menambah metric category baru
- Mengubah time-travel algorithm
- Improve month calculation logic

---

### 5. **sentinel.py** (OOM Guardian)
**Purpose**: Prevent system freeze dari Out-of-Memory dengan 3-phase logic

**Key Class**: `SentinelEngine`

**Methods**:
- `__init__()` - Load config, setup whitelist, thresholds
- `tick()` - Main loop (dipanggil setiap detik)
- `_find_victim()` - Find process to kill (largest RSS, not whitelisted)
- `_execute_kill(target, reason)` - SIGTERM → SIGKILL sequence
- `_log_forensic(level, ram, swap, message)` - Atomic forensic logging

**Phase Logic**:
```
Phase 1 (LOGGING):   RAM > 87% OR Swap > 50%  → Log to SSD
Phase 2 (WARNING):   RAM > 90% AND Swap > 78% → Visual alert
Phase 3 (EXECUTION):
  - SWAP >= 87% → KILL IMMEDIATE
  - RAM >= 90% held 5s → KILL
```

**Whitelist** (Immune processes):
- Critical: `systemd`, `init`, `Xorg`, `dbus-daemon`
- Self-protection: `python3`, `ai-term`
- Configurable via `mon_config.json`

**When to Modify**:
- Adjust thresholds (via config, not code)
- Change kill strategy
- Add telemetry/metrics

**⚠️ CRITICAL**: Never modify whitelist logic without testing!

---

### 6. **utils.py** (Helpers)
**Purpose**: Utility functions untuk shell execution, formatting, ANSI

**Key Functions**:
- `run_cmd(argv, timeout)` - Safe subprocess execution
- `sh_cmd(cmd, timeout)` - String command wrapper (shlex split)
- `read_text/int/float(path)` - Safe file readers
- `human_bytes(n)` - Format bytes → KB/MB/GB/TB
- `draw_bar(pct, width)` - ASCII bar chart
- `vis_len(s)` - ANSI-aware string length
- `align_lr(left, right, width)` - Left-right alignment
- `trim_name/tail(s, maxlen)` - String truncation

**When to Modify**:
- Menambah utility functions baru
- Improve formatting logic
- Add new color schemes

---

### 7. **collectors/** (Sensor Modules)

#### 7.1 **system.py**
**Purpose**: CPU, RAM, Swap, Load Average

**Functions**:
- `collect_system_stats()` - Return dict with all system metrics

**Returns**:
```python
{
  "ok": True,
  "cpu_pct": 45.2,
  "cpu_count": 8,
  "load_1m": 2.5, "load_5m": 2.1, "load_15m": 1.8,
  "ram_total": 16GB, "ram_used": 8GB, "ram_pct": 50.0,
  "swap_total": 8GB, "swap_used": 1GB, "swap_pct": 12.5,
  "uptime_sec": 86400
}
```

#### 7.2 **process.py**
**Purpose**: Process monitoring, top processes by RAM/CPU

**Functions**:
- `collect_process_stats(limit=10)` - Top N processes
- `find_process_monster(whitelist)` - Find victim for OOM killer

#### 7.3 **hardware.py**
**Purpose**: Battery, Disk (mounted + SMART), Thermal sensors

**Functions**:
- `collect_battery_stats()` - Battery level, plugged status, time left
- `collect_disk_stats(use_sudo=False)` - Mounted disks + optional SMART
- `collect_thermal_stats()` - CPU temp, fan RPM
- `collect_hardware_stats(use_sudo=False)` - Aggregator

**SMART Data** (requires `smartmontools` + sudo):
- NVMe wear percentage
- TBW (Total Bytes Written)

#### 7.4 **network.py**
**Purpose**: Network I/O, WiFi signal, Ping monitoring

**Functions**:
- `collect_network_stats(iface=None)` - RX/TX bytes
- `collect_wifi_stats(iface=None)` - SSID, signal strength

**Class**: `PingMonitor`
- Background threading ping monitor
- Stores history (deque dengan max window)
- Calculate avg, jitter, loss percentage

---

### 8. **ui/** (View Modules)

#### 8.1 **dashboard.py**
**Purpose**: Live monitoring HUD (main UI loop)

**Function**: `run_live_dashboard(args)`

**Flow**:
1. Parse CLI args (interval, compact, target, etc)
2. Start background threads (PingMonitor, Sentinel)
3. Enter alt-screen mode
4. Loop:
   - Collect all stats
   - Render header (hostname, uptime, sentinel status)
   - Render sections (system, hardware, network, disks)
   - Sleep(interval)
5. Cleanup on exit

#### 8.2 **render.py**
**Purpose**: Visual components (bars, sparklines, colors)

**Functions**:
- `draw_bar(pct, width, limits)` - Colored bar chart
- `draw_sparkline(values, width, height)` - ASCII sparkline graph
- `color_by_value(value, thresholds)` - Dynamic color selection
- `format_uptime(seconds)` - Human-readable uptime
- `trim_string(s, maxlen, position)` - Smart truncation
- `align_columns(left, right, width)` - Column alignment

#### 8.3 **reports.py**
**Purpose**: Static reports (battery, disk, network, sensors dump)

**Functions**:
- `run_battery_report(args)` - Battery health + history comparison
- `run_disk_report(args)` - SMART data + TBW history
- `run_network_diag(args)` - Ping test, DNS, WiFi info
- `run_sensors_dump()` - Inventory mode (show all sensors)

---

## 🔄 Data Flow

### Live Dashboard Flow
```
User runs: ai mon live

command.handle()
  ↓
ui.dashboard.run_live_dashboard()
  ↓
Start threads: PingMonitor, SentinelEngine
  ↓
Loop (every interval):
  ├─ collectors.system.collect_system_stats()
  ├─ collectors.hardware.collect_hardware_stats()
  ├─ collectors.network.collect_network_stats()
  ├─ PingMonitor.get_stats()
  ├─ SentinelEngine.tick()
  ↓
  ├─ ui.render.draw_bar/sparkline()
  └─ Print formatted output
```

### History Snapshot Flow
```
Battery/Disk collector reads metric
  ↓
storage.snapshot_metric(category, item_id, metric, value)
  ↓
Check: Is today's data already saved?
  ├─ Yes → Skip (save SSD writes)
  └─ No  → Save to history DB (atomic write)
```

### Sentinel OOM Killer Flow
```
SentinelEngine.tick() (called every second)
  ↓
Read: RAM %, Swap %
  ↓
Phase 1: RAM > 87% OR Swap > 50%
  └─ Log to forensic file (append + fsync)
  ↓
Phase 2: RAM > 90% AND Swap > 78%
  └─ Return status="WARNING" to UI
  ↓
Phase 3: SWAP >= 87% OR RAM >= 90% held 5s
  ├─ _find_victim() → Largest RSS process (not whitelisted)
  ├─ _execute_kill(victim, reason)
  │   ├─ SIGTERM
  │   ├─ wait 3s
  │   └─ SIGKILL if still alive
  └─ Log forensic: KILL_SOFT/KILL_HARD/KILL_FAIL
```

---

## ⚙️ Critical Components

### 1. Atomic Writes (storage.py)
**Why Critical**: Prevent data corruption jika sistem crash saat write

**Implementation**:
```python
def _save_db_atomic(data):
    tmp_path = MON_HISTORY_PATH.with_suffix(".tmp")

    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())  # Force kernel buffer flush

    os.replace(tmp_path, MON_HISTORY_PATH)  # Atomic operation
```

**Never**:
- ❌ Write directly ke file tanpa tmp
- ❌ Skip `fsync()` call
- ❌ Use `rename()` instead of `replace()`

### 2. Sentinel Whitelist (sentinel.py)
**Why Critical**: Prevent killing critical system processes

**Default Whitelist**:
- `systemd`, `init` - Init system
- `Xorg`, `wayland` - Display server
- `dbus-daemon` - IPC
- `NetworkManager` - Network connectivity
- `python3`, `ai-term` - Self-protection

**⚠️ WARNING**: Removing items dari whitelist dapat menyebabkan system instability!

### 3. Exception Handling
**Pattern** (semua I/O operations):
```python
try:
    # I/O operation
    result = risky_operation()
except SpecificException as e:
    # Log error (optional)
    print(f"Error: {e}")
    # Return safe default
    return {"ok": False, "error": str(e)}
```

**Never**:
- ❌ Bare `except:` (menyembunyikan KeyboardInterrupt, SystemExit)
- ❌ Crash aplikasi karena sensor unavailable
- ❌ Silent failures tanpa return value

---

## 📝 Common Tasks

### Adding New Metric to History

1. **Choose category** (battery, disk, network, etc)
2. **Add collector** di appropriate module
3. **Snapshot** at appropriate interval:

```python
# In collector or dashboard loop
from mon.storage import snapshot_metric

# Collect metric
wear_pct = read_nvme_wear()

# Save daily snapshot
snapshot_metric(
    category="disk",
    item_id="nvme0n1",
    metric="wear_pct",
    value=wear_pct
)
```

4. **Display comparison** in report:
```python
from mon.storage import get_comparison_text

comparison = get_comparison_text(
    category="disk",
    item_id="nvme0n1",
    metric="wear_pct",
    current=wear_pct,
    unit="%",
    window_months=30
)
print(comparison)
```

### Adding New Collector

1. **Create module** di `collectors/`
2. **Implement function**:
```python
def collect_my_stats() -> Dict[str, Any]:
    """Docstring with purpose."""
    try:
        # Read sensor/data
        value = read_sensor()

        return {
            "ok": True,
            "my_metric": value
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }
```

3. **Export** di `collectors/__init__.py`:
```python
from .my_module import collect_my_stats

__all__ = [..., "collect_my_stats"]
```

4. **Use** in dashboard or report:
```python
from mon.collectors import collect_my_stats

stats = collect_my_stats()
if stats.get('ok'):
    print(stats['my_metric'])
```

### Adding New UI Component

1. **Create rendering function** di `ui/render.py`:
```python
def draw_my_widget(data: dict, width: int) -> str:
    """Generate my custom widget."""
    # Format data
    # Return formatted string
    return formatted_output
```

2. **Use in dashboard** (`ui/dashboard.py`):
```python
def _render_my_section(data: dict, width: int):
    print(f"{ansi.c_bold()}MY SECTION{ansi.c_reset()}")
    widget = draw_my_widget(data, width)
    print(widget)
```

3. **Call in main loop**:
```python
# In run_live_dashboard()
_render_my_section(my_data, term_w)
```

### Adjusting Sentinel Thresholds

**Edit** `mon_config.json`:
```json
{
  "sentinel": {
    "thresholds": {
      "phase1_log": {
        "ram_pct": 85.0,    // Was: 87.0
        "swap_pct": 45.0    // Was: 50.0
      },
      "phase3_kill": {
        "ram_pct": 95.0,    // Was: 90.0
        "ram_hold_sec": 10  // Was: 5
      }
    }
  }
}
```

**Test** with artificial load:
```fish
# Generate RAM pressure
stress-ng --vm 2 --vm-bytes 2G --timeout 60s

# Monitor sentinel
ai mon live
```

---

## 🐛 Troubleshooting

### Issue: "psutil not available"
**Cause**: Missing dependency

**Fix**:
```fish
sudo dnf install python3-psutil
```

### Issue: Sentinel log not writing
**Possible Causes**:
1. Log directory doesn't exist
2. Permission denied
3. Disk full

**Debug**:
```python
# Check log path
from mon.const import MON_HISTORY_PATH
from mon.config import SENTINEL_LOG_FILE

print(f"History: {MON_HISTORY_PATH}")
print(f"Sentinel log: {SENTINEL_LOG_FILE}")

# Check permissions
ls -la (MON_HISTORY_PATH.parent)
```

**Fix**:
```fish
# Create directory manually
mkdir -p ~/.cache/ai-term
chmod 755 ~/.cache/ai-term
```

### Issue: History data not saving
**Check**:
1. File path exists and writable
2. JSON not corrupted
3. Disk space available

**Manual backup/restore**:
```fish
# Backup
cp ~/.config/ai-term/mon_history.json ~/mon_history_backup.json

# Restore from corrupted
mv ~/.config/ai-term/mon_history.json ~/.config/ai-term/mon_history.corrupted
# Let MON create new file on next run
```

### Issue: Sentinel killing wrong processes
**Possible Causes**:
1. Process not in whitelist
2. Race condition (process name changed)

**Debug**:
```python
# Check whitelist
from mon.config import load_config

cfg = load_config()
whitelist = cfg['sentinel']['whitelist']
print(whitelist)
```

**Fix**: Add process to whitelist in config:
```json
{
  "sentinel": {
    "whitelist": [
      "systemd",
      "my_important_app"  // Add here
    ]
  }
}
```

---

## 👨‍💻 Development Guidelines

### Code Style
- **Naming**: `snake_case` untuk functions/variables, `PascalCase` untuk classes
- **Docstrings**: Required untuk public functions
- **Type Hints**: Encouraged (especially function signatures)
- **ANSI**: Use `ansi.c_*()` functions, never hardcode escape codes

### Testing Pattern
```python
# Unit test example
def test_snapshot_metric():
    # Setup
    from mon.storage import snapshot_metric

    # Test
    result = snapshot_metric("test", "item1", "metric1", 42.0)

    # Verify
    assert result == True  # First save

    result = snapshot_metric("test", "item1", "metric1", 43.0)
    assert result == False  # Same day, should skip
```

### Git Workflow
```fish
# Feature branch
git checkout -b feature/my-feature

# Work
vim mon/collectors/my_module.py

# Test locally
python3 -m mon.command live

# Commit with descriptive message
git add mon/collectors/my_module.py
git commit -m "collectors: Add GPU monitoring support"

# Push and create PR
git push origin feature/my-feature
```

### Documentation
- **Update this file** when adding new modules
- **Add docstrings** to all public functions
- **Comment complex logic** inline
- **Update examples** when changing API

---

## 📚 References

### External Dependencies
- **psutil** - Cross-platform system monitoring
  - Docs: https://psutil.readthedocs.io/
- **smartmontools** (optional) - Disk SMART data
  - Fedora: `sudo dnf install smartmontools`
- **iw** (optional) - WiFi statistics
  - Fedora: `sudo dnf install iw`

### Internal References
- **ansi module** (`system_logic.terminal.ansi`)
  - Color functions: `c_red()`, `c_green()`, etc
  - Control: `alt_screen_enter()`, `cursor_hide()`, etc
  - Print: `print_info()`, `print_brief_error()`

### Related Projects
- **ai-term framework** - Parent project
  - Terminal UI primitives
  - Command routing infrastructure
  - Config management

---

## 🔐 Security Notes

### Sudo Usage
- **Minimize**: Only use sudo when absolutely necessary (SMART data)
- **Validate**: Always validate sudo availability before attempting
- **User Choice**: Make sudo optional with `--sudo` flag

### Process Killing
- **Whitelist First**: Always maintain critical system processes in whitelist
- **Logging**: Every kill action must be logged with reason
- **Grace Period**: Always try SIGTERM before SIGKILL

### File Permissions
- **History DB**: Should be user-writable only (`chmod 600`)
- **Config File**: Should be user-writable only
- **Log Files**: Should be user-writable, but readable by admin for debugging

---

## 📞 Support

### Getting Help
1. Check this maintenance guide first
2. Review relevant module docstrings
3. Check git commit history for context
4. Ask Silvia (project owner)

### Reporting Bugs
Include:
- MON version (`cat mon/__init__.py | grep __version__`)
- OS/Kernel version (`uname -a`)
- Python version (`python3 --version`)
- Error message with full traceback
- Steps to reproduce

---

**Last Updated**: 2026-02-16
**Maintainer**: Livi (Senior Auditor)
**Project Owner**: Silvia