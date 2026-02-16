"""
MON UI Render Components.
Bar charts, sparklines, color logic, formatting helpers.
"""

from typing import Optional
from system_logic.terminal import ansi


def draw_bar(pct: float, width: int = 14, limits: Optional[dict] = None) -> str:
    """
    Membuat bar chart text dengan color coding.

    Args:
        pct: Percentage (0-100)
        width: Bar width in characters
        limits: Dict dengan warn/crit thresholds (optional)
    """
    pct = max(0.0, min(100.0, float(pct)))
    fill = int(width * pct / 100.0)

    # Default coloring
    if limits:
        warn = limits.get('warn', 70)
        crit = limits.get('crit', 90)
    else:
        warn, crit = 70, 90

    if pct <= warn:
        col = ansi.c_green()
    elif pct <= crit:
        col = ansi.c_yellow()
    else:
        col = ansi.c_red()

    bar = "█" * fill
    empty = "░" * (width - fill)
    return f"{col}{bar}{ansi.c_dim()}{empty}{ansi.c_reset()}"


def draw_sparkline(values: list, width: int = 40, height: int = 8) -> list:
    """
    Generate ASCII sparkline untuk list values.

    Args:
        values: List of numeric values
        width: Width in characters
        height: Height levels (8 = full resolution)

    Returns:
        List of strings (lines) untuk di-print
    """
    if not values:
        return [" " * width]

    # Filter None values
    valid_vals = [v for v in values if v is not None]
    if not valid_vals:
        return [" " * width]

    min_val = min(valid_vals)
    max_val = max(valid_vals)

    if max_val == min_val:
        # Flat line
        return ["_" * width]

    # Normalize to 0-1 range
    normalized = []
    for v in values[-width:]:
        if v is None:
            normalized.append(0.0)
        else:
            normalized.append((v - min_val) / (max_val - min_val))

    # Generate bars using block elements
    bars = []
    for n in normalized:
        level = int(n * height)
        if level == 0:
            bars.append("▁")
        elif level == 1:
            bars.append("▂")
        elif level == 2:
            bars.append("▃")
        elif level == 3:
            bars.append("▄")
        elif level == 4:
            bars.append("▅")
        elif level == 5:
            bars.append("▆")
        elif level == 6:
            bars.append("▇")
        else:
            bars.append("█")

    return ["".join(bars)]


def color_by_value(value: Optional[float], thresholds: dict) -> str:
    """
    Return ANSI color code based on value and thresholds.

    Args:
        value: Numeric value to colorize
        thresholds: Dict dengan 'low', 'warn', 'crit' keys

    Returns:
        ANSI color code string
    """
    if value is None:
        return ansi.c_dim()

    low = thresholds.get('low', 0)
    warn = thresholds.get('warn', 70)
    crit = thresholds.get('crit', 90)

    if value <= low:
        return ansi.c_dim()
    elif value <= warn:
        return ansi.c_green()
    elif value <= crit:
        return ansi.c_yellow()
    else:
        return ansi.c_red()


def format_uptime(seconds: Optional[int]) -> str:
    """Format uptime seconds ke human-readable string."""
    if seconds is None:
        return "unknown"

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    mins = (seconds % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h {mins}m"
    elif hours > 0:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m"


def trim_string(s: str, maxlen: int, position: str = "middle") -> str:
    """
    Trim string dengan ellipsis.

    Args:
        s: String to trim
        maxlen: Maximum length
        position: 'start', 'middle', 'end'
    """
    s = s or ""
    if len(s) <= maxlen:
        return s

    if position == "start":
        return "…" + s[-(maxlen - 1):]
    elif position == "middle":
        half = (maxlen - 1) // 2
        return s[:half] + "…" + s[-(maxlen - half - 1):]
    else:  # end
        return s[:maxlen - 1] + "…"


def align_columns(left: str, right: str, width: int, fill_char: str = " ") -> str:
    """
    Align two strings with filler in between.
    Handles ANSI color codes correctly.
    """
    import re
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")

    left_vis = len(ansi_re.sub("", left))
    right_vis = len(ansi_re.sub("", right))

    available = width - left_vis - right_vis
    if available <= 0:
        # Truncate left if needed
        return left[:max(1, width - right_vis - 1)] + "…" + right

    return left + (fill_char * available) + right