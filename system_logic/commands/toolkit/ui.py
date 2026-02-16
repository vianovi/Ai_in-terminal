def print_help() -> None:
    print(
        """toolkit — downloader + file utilities (Fedora/DNF)

Usage:
  toolkit <subcommand> [args] [options]

Subcommands:
  video    Download video (yt-dlp)
  audio    Extract/download audio (yt-dlp)
  image    Download image URL (curl)
  fetch    Download file URL (curl)
  batch    Batch download from urls.txt
  extract  Extract archives (zip/tar/7z/rar)
  deps     Check dependencies
  help     Show help

Common options:
  --to <path>      Output directory override (default per blueprint)
  --force          Allow overwrite

Examples:
  toolkit video <url> --1080p
  toolkit audio <url> --mp3
  toolkit image <url> --name cover.jpg
  toolkit fetch <url> --to .
  toolkit batch urls.txt --video
  toolkit extract data.zip

Notes:
  - Default root: ~/Downloads/toolkit/
  - No DRM/bypass behavior is included.
"""
    )


def print_error(msg: str) -> None:
    print(f"[toolkit] ERROR: {msg}")
