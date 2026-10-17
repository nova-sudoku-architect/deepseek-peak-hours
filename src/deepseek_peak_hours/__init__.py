"""deepseek-peak-hours — is this instant inside DeepSeek's 2x peak-rate window?"""

__version__ = "1.0.0"

from .core import (  # noqa: F401
    DEFAULT_DISPLAY_TZ,
    evaluate,
    load_config,
    next_boundary,
    parse_footnote,
    parse_holidays_txt,
    resolve_config_path,
)

__all__ = [
    "__version__",
    "DEFAULT_DISPLAY_TZ",
    "evaluate",
    "load_config",
    "next_boundary",
    "parse_footnote",
    "parse_holidays_txt",
    "resolve_config_path",
]
