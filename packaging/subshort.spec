from pathlib import Path
import shutil
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
source = root / "src"
# Discovery runs before Analysis adds pathex.
sys.path.insert(0, str(source))
# Collect application protocol workers; models, caches, and provider runtimes stay external.
application_modules = collect_submodules("subshort")

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(source)],
    hiddenimports=application_modules,
    datas=[(str(source / "subshort" / "default_settings.json"), "subshort")] + collect_data_files("faster_whisper"),
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='SubShort', console=False)

# Keep GPU libraries beside the executable instead of unpacking gigabytes on every launch.
cuda_source = root / ".venv" / "Lib" / "site-packages" / "nvidia"
cuda_output = Path(DISTPATH) / "cuda"
for package in ("cublas", "cudnn", "cuda_nvrtc"):
    libraries = list((cuda_source / package / "bin").glob("*.dll"))
    if not libraries:
        raise RuntimeError(f"Missing CUDA runtime DLLs: {cuda_source / package}")
    cuda_output.mkdir(parents=True, exist_ok=True)
    for library in libraries:
        shutil.copy2(library, cuda_output / library.name)
