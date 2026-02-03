from ...ui import print_help
from ..types import Result
from .ui import render


def handle(argv, cfg) -> Result:
    data = {"help": True}
    return Result.success(data=data, render=render)
