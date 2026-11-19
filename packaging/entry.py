"""Standalone PyInstaller entry point; package imports remain absolute."""
import os
from pathlib import Path
import sys

if getattr(sys, "frozen", False):
    cuda = Path(sys.executable).resolve().parent / "cuda"
    if cuda.is_dir():
        os.environ["PATH"] = str(cuda) + os.pathsep + os.environ.get("PATH", "")
        _cuda_dll_directory = os.add_dll_directory(str(cuda))

from subshort.cli import main


if __name__ == "__main__":
    main()
