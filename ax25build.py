"""Builds the C code under test against lib/ax25 from the OBC repo.

    python ax25build.py        # build everything into build/

The OBC repo is found via $OBC_REPO, defaulting to ../UNSW-Austronauts-OBC.
"""

import json
import os
import platform
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBC_REPO = Path(os.environ.get("OBC_REPO", HERE.parent / "UNSW-Austronauts-OBC")).resolve()
AX25_DIR = OBC_REPO / "lib" / "ax25"
BUILD_DIR = HERE / "build"
CC = os.environ.get("CC", "cc")

WARNINGS = ["-Wall", "-Wextra", "-Werror", "-pedantic", "-Wconversion",
            "-Wsign-conversion", "-Wshadow"]
# Trap mode needs no sanitizer runtime (works inside Python, and avoids the
# ASan start-up hang on macOS 26): undefined behaviour stops the process.
SANITIZE = ["-fsanitize=undefined", "-fsanitize-trap=undefined"]
BASE_FLAGS = ["-std=c99", "-O1", "-g", *WARNINGS, f"-I{AX25_DIR / 'include'}"]

SHIM_SRC = HERE / "shim" / "ax25_shim.c"
SENDER_SRC = HERE / "sender" / "ax25_send.c"


def library_sources():
    return sorted((AX25_DIR / "src").glob("*.c"))


def _check_repo():
    if not AX25_DIR.is_dir():
        raise FileNotFoundError(f"lib/ax25 not found under {OBC_REPO} (set OBC_REPO)")
    BUILD_DIR.mkdir(exist_ok=True)


def _compile(sources, out, extra=()):
    cmd = [CC, *BASE_FLAGS, *SANITIZE, *extra, *map(str, sources), "-o", str(out)]
    subprocess.run(cmd, check=True)
    return out


def build_shim():
    _check_repo()
    ext = "dylib" if platform.system() == "Darwin" else "so"
    return _compile([SHIM_SRC, *library_sources()], BUILD_DIR / f"libax25shim.{ext}",
                    ["-shared", "-fPIC"])


def build_sender():
    _check_repo()
    return _compile([SENDER_SRC, *library_sources()], BUILD_DIR / "ax25_send")


def write_compile_commands():
    """build/compile_commands.json lets clangd (editor) see the same include
    paths and flags as the test build, wherever OBC_REPO points."""
    _check_repo()
    entries = [{"directory": str(HERE), "file": str(src),
                "arguments": [CC, *BASE_FLAGS, "-c", str(src)]}
               for src in [SHIM_SRC, SENDER_SRC, *library_sources()]]
    (BUILD_DIR / "compile_commands.json").write_text(json.dumps(entries, indent=2) + "\n")


def obc_revision():
    """(commit, dirty) of the OBC repo, so results say exactly what was tested."""
    def git(*args):
        return subprocess.run(["git", "-C", str(OBC_REPO), *args], capture_output=True,
                              text=True).stdout.strip()
    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    dirty = bool(git("status", "--porcelain", "--", "lib/ax25"))
    return commit, dirty


if __name__ == "__main__":
    write_compile_commands()
    print(build_shim())
    print(build_sender())
