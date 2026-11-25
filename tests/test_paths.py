import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from subshort import cli, paths


def frozen(monkeypatch, tmp_path):
    resources = tmp_path / "bundle"
    resources.mkdir()
    executable = tmp_path / "install" / "SubShort.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(resources), raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return resources, executable.parent


def test_source_root_is_project_not_cwd(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    project = Path(__file__).resolve().parents[1]
    assert paths.resource_root() == project / "src"
    assert paths.state_root() == project
    assert paths.state_root(tmp_path / "isolated") == tmp_path / "isolated"


def test_frozen_state_is_local_appdata_not_bundle(monkeypatch, tmp_path):
    resources, install = frozen(monkeypatch, tmp_path)
    assert paths.resource_root() == resources
    assert paths.state_root() == tmp_path / "local" / "SubShort"
    assert paths.state_root(tmp_path / "isolated") == tmp_path / "isolated"
    for protected in (resources, install, resources / "state", install / "state"):
        with pytest.raises(ValueError, match="outside"):
            paths.state_root(protected)


def test_frozen_missing_local_appdata_uses_user_home(monkeypatch, tmp_path):
    frozen(monkeypatch, tmp_path)
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert paths.state_root() == tmp_path / "home" / "AppData" / "Local" / "SubShort"


def test_initialize_config_copies_template_once(monkeypatch, tmp_path):
    resources, _ = frozen(monkeypatch, tmp_path)
    config = resources / "subshort"
    config.mkdir()
    template = config / "default_settings.json"
    template.write_text('{"ffmpeg": "ffmpeg"}', encoding="utf-8")
    root = paths.state_root()
    paths.initialize_config(root)
    target = root / "config" / "settings.json"
    assert target.read_bytes() == template.read_bytes()
    target.write_text('{"custom": "user content"}', encoding="utf-8")
    template.write_text('{"changed": true}', encoding="utf-8")
    paths.initialize_config(root)
    assert target.read_text(encoding="utf-8") == '{"custom": "user content"}'
    assert list(resources.iterdir()) == [config]


def test_initialize_config_source_preserves_existing_file(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(paths, "resource_root", lambda: tmp_path)
    target = tmp_path / "config" / "settings.json"
    target.parent.mkdir()
    target.write_bytes(b'user settings remain byte-for-byte')
    paths.initialize_config(tmp_path)
    assert target.read_bytes() == b'user settings remain byte-for-byte'


def test_missing_template_does_not_create_empty_config(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "resource_root", lambda: tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        paths.initialize_config(tmp_path / "state")
    assert not (tmp_path / "state" / "config" / "settings.json").exists()


def test_command_status_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.shutil, "which", lambda command: None)
    assert paths.command_status("unavailable-tool") == {"resolved": None, "exists": False}
    tool = tmp_path / "ffmpeg.exe"
    tool.touch()
    assert paths.command_status(str(tool)) == {"resolved": str(tool), "exists": True}
    monkeypatch.setattr(paths.shutil, "which", lambda command: str(tool))
    assert paths.command_status("ffmpeg") == {"resolved": str(tool), "exists": True}


def test_diagnose_isolated_and_does_not_emit_credentials(monkeypatch, tmp_path, capsys):
    root = tmp_path / "state"
    config = root / "config" / "settings.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"ffmpeg": "missing-ffmpeg", "ffprobe": "missing-ffprobe",
                                  "openai_api_key": "must-not-leak", "nested": {"secret": "also-private"}}))
    monkeypatch.setattr(sys, "argv", ["subshort", "--state-dir", str(root), "--diagnose"])
    monkeypatch.setattr(paths.shutil, "which", lambda command: None)
    cli.main()
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result == {"state_root": str(root), "resource_root": str(paths.resource_root()),
                      "ffmpeg": {"resolved": None, "exists": False},
                      "ffprobe": {"resolved": None, "exists": False}}
    assert "must-not-leak" not in output and "also-private" not in output


def test_http_worker_dispatch_skips_path_initialization(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "subshort.http_worker", SimpleNamespace(main=lambda: calls.append("worker")))
    monkeypatch.setattr(sys, "argv", ["SubShort.exe", "--http-worker"])
    monkeypatch.setattr(cli, "state_root", lambda *_: pytest.fail("worker must not initialize UI state"))
    cli.main()
    assert calls == ["worker"]


def test_frozen_launch_passes_writable_state_to_app(monkeypatch, tmp_path):
    resources, _ = frozen(monkeypatch, tmp_path)
    (resources / "subshort").mkdir()
    (resources / "subshort" / "default_settings.json").write_text("{}", encoding="utf-8")
    roots = []
    monkeypatch.setitem(sys.modules, "subshort.app", SimpleNamespace(launch=roots.append))
    monkeypatch.setattr(sys, "argv", ["SubShort.exe"])
    cli.main()
    assert roots == [tmp_path / "local" / "SubShort"]
    assert (roots[0] / "config" / "settings.json").read_text(encoding="utf-8") == "{}"


def test_artifact_directory_at_filesystem_root(tmp_path):
    root = tmp_path.resolve().parents[-1]
    assert paths.artifact_directory(tmp_path, str(root), "job-1") == root / "_artifacts" / "job-1"
