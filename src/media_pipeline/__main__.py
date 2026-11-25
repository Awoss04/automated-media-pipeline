"""Einstiegspunkt fuer ``python -m media_pipeline``."""

from __future__ import annotations

import sys

from media_pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
