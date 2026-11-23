#!/usr/bin/env python3
"""zh-reader-annotate: `zh-preprocessor skill assemble`. Run with --help for options."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import run  # noqa: E402

run("assemble")
