"""Locate the zh_preprocessor package and a Python that has its dependencies, then run a command."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[4]
SOURCE = REPOSITORY / "preprocessor" / "src"
_DEPENDENCY_CHECK = "import jieba, opencc, pypinyin"
_REEXEC_FLAG = "ZH_READER_SKILL_REEXEC"


def _has_dependencies(python: str) -> bool:
    result = subprocess.run(
        [python, "-c", _DEPENDENCY_CHECK], capture_output=True, env={**os.environ, "PYTHONPATH": ""}
    )
    return result.returncode == 0


def _candidate_interpreters() -> list[str]:
    candidates = [str(REPOSITORY / "preprocessor" / ".venv" / "bin" / "python")]
    entry_point = shutil.which("zh-preprocessor")
    if entry_point:
        first_line = Path(entry_point).read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
        if first_line and first_line[0].startswith("#!"):
            candidates.append(first_line[0][2:].strip())
    candidates.extend(filter(None, [shutil.which("python3")]))
    return [c for c in dict.fromkeys(candidates) if Path(c).is_file() and c != sys.executable]


def run(command: str) -> None:
    sys.path.insert(0, str(SOURCE))
    try:
        from zh_preprocessor.skill import main
    except ImportError:
        if os.environ.get(_REEXEC_FLAG) is None:
            for python in _candidate_interpreters():
                if _has_dependencies(python):
                    os.environ[_REEXEC_FLAG] = "1"
                    script = Path(sys.argv[0]).resolve()
                    os.execv(python, [python, str(script), *sys.argv[1:]])
        print(
            "Error: no Python with the preprocessor dependencies was found. Install them with:\n"
            f"  cd {REPOSITORY / 'preprocessor'} && python3 -m venv .venv && "
            ".venv/bin/python -m pip install -e .\n"
            "then run `zh-preprocessor setup-data` once.",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(main([command, *sys.argv[1:]]))
