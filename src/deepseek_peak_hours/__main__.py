"""Allow ``python -m deepseek_peak_hours``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
