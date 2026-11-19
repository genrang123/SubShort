"""Keep bundled resources separate from writable application state."""
import os
from pathlib import Path
import shutil
import sys


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[1]


def state_root(override: str | Path | None = None) -> Path:
    if override is not None:
        root = Path(override).expanduser().resolve()
    elif getattr(sys, "frozen", False):
        local = os.environ.get("LOCALAPPDATA")
        root = (Path(local) if local else Path.home() / "AppData" / "Local") / "SubShort"
        root = root.resolve()
    else:
        root = resource_root().parent
    if getattr(sys, "frozen", False):
        for protected in (resource_root(), Path(sys.executable).resolve().parent):
            if root == protected or protected in root.parents:
                raise ValueError("State directory must be outside the application and resource directories")
    return root


def initialize_config(root: Path) -> None:
    target = root / "config" / "settings.json"
    if target.exists():
        return
    template = resource_root() / "subshort" / "default_settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also preserves a config created by another launch.
    try:
        with template.open("rb") as source:
            with target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
    except FileExistsError:
        pass


def command_status(command: str) -> dict[str, object]:
    resolved = shutil.which(command)
    if resolved is None and Path(command).is_file():
        resolved = str(Path(command).resolve())
    return {"resolved": resolved, "exists": resolved is not None}


def artifact_directory(root: Path, output_dir: str, job_id: str) -> Path:
    output = (root / output_dir).resolve()
    return output.with_name(output.name + "_artifacts") / job_id
