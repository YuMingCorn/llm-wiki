"""Tests for the 4-thread / 8 GB limits applied by every tool."""

import os
import subprocess
import sys
from pathlib import Path

import _limits

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def test_thread_variables_are_capped_at_four() -> None:
    assert _limits.THREADS == 4
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        assert int(os.environ[name]) <= 4


def test_lower_thread_setting_is_kept() -> None:
    env = {**os.environ, "OMP_NUM_THREADS": "2"}
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import sys; sys.path.insert(0, {str(TOOLS)!r}); import _limits, os;"
         "print(os.environ['OMP_NUM_THREADS'], _limits._applied)"],
        env=env, capture_output=True, text=True, check=True)
    threads, applied = out.stdout.split()
    assert threads == "2" and applied == "True"


def test_memory_over_the_cap_is_refused() -> None:
    code = (f"import sys; sys.path.insert(0, {str(TOOLS)!r}); import _limits\n"
            "try:\n    bytearray(9 * 2**30)\n    print('allocated')\n"
            "except MemoryError:\n    print('refused')\n")
    out = subprocess.run([sys.executable, "-I", "-c", code],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "refused"
