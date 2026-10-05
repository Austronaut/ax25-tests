"""lib/ax25 must compile cleanly for the MSP430, where int is 16 bits."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import ax25c

# Apple's clang has no MSP430 backend; Homebrew LLVM does.
CANDIDATES = [os.environ.get("MSP430_CC", ""), "/opt/homebrew/opt/llvm/bin/clang",
              "/usr/local/opt/llvm/bin/clang", shutil.which("clang") or ""]


def msp430_clang():
    for cc in filter(None, CANDIDATES):
        probe = subprocess.run([cc, "--target=msp430", "-ffreestanding", "-x", "c", "-c", "-",
                                "-o", os.devnull], input=b"int x;", capture_output=True)
        if probe.returncode == 0:
            return cc
    return None


CC = msp430_clang()
FLAGS = ["--target=msp430", "-ffreestanding", "-std=c99", "-Os", *ax25c.WARNINGS,
         f"-I{ax25c.AX25_DIR / 'include'}"]

pytestmark = pytest.mark.skipif(CC is None, reason="no clang with an MSP430 backend")


def test_int_is_16_bit():
    src = b"typedef char int_is_16_bit[(sizeof(int) == 2) ? 1 : -1];\n"
    subprocess.run([CC, *FLAGS, "-x", "c", "-c", "-", "-o", os.devnull], input=src, check=True)


@pytest.mark.parametrize("source", ax25c.library_sources(), ids=lambda p: Path(p).name)
def test_compiles_without_warnings(source):
    result = subprocess.run([CC, *FLAGS, "-c", str(source), "-o", os.devnull],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
