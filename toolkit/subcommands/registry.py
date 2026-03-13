from __future__ import annotations

from .types import HandleFn, Result

# name -> handle
_REGISTRY: dict[str, HandleFn] = {}


def register(name: str, handle: HandleFn) -> None:
    _REGISTRY[name] = handle


def list_subcommands() -> list[str]:
    return sorted(_REGISTRY.keys())


def dispatch(name: str, argv: list[str], cfg) -> Result:
    handle = _REGISTRY[name]  # may raise KeyError
    return handle(argv, cfg)


# --- register built-ins ---
from .help.command import handle as help_handle  # noqa: E402
from .deps.command import handle as deps_handle  # noqa: E402
from .video.command import handle as video_handle  # noqa: E402
from .audio.command import handle as audio_handle  # noqa: E402
from .image.command import handle as image_handle  # noqa: E402
from .fetch.command import handle as fetch_handle  # noqa: E402
from .batch.command import handle as batch_handle  # noqa: E402
from .extract.command import handle as extract_handle  # noqa: E402

register("help", help_handle)
register("deps", deps_handle)
register("video", video_handle)
register("audio", audio_handle)
register("image", image_handle)
register("fetch", fetch_handle)
register("batch", batch_handle)
register("extract", extract_handle)
