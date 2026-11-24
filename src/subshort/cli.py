import argparse
import json
import sys

from subshort.paths import command_status, initialize_config, resource_root, state_root


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--http-worker":
        from subshort.http_worker import main as http_main
        http_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--tts-worker":
        from subshort.tts_worker import main as tts_main
        tts_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--separator-worker":
        from subshort.separation import main as separator_main
        result = separator_main()
        if result is not None:
            raise SystemExit(result)
        return
    parser = argparse.ArgumentParser(description="SubShort desktop application")
    parser.add_argument("--state-dir", help="Directory for configuration, queue and output")
    parser.add_argument("--diagnose", action="store_true", help="Print local path and media-tool diagnostics without launching the UI")
    parser.add_argument("--diagnose-asr", metavar="WAV", help="Transcribe a local WAV with the configured ASR, without translating or launching the UI")
    args = parser.parse_args()
    root = state_root(args.state_dir)
    initialize_config(root)
    if args.diagnose_asr:
        from dataclasses import asdict
        from pathlib import Path
        from subshort.models import Settings
        from subshort.pipeline import Pipeline

        stdout = sys.stdout or open(1, "w", encoding="utf-8", closefd=False)
        pipeline = Pipeline(Settings.load(root), None, None)
        try:
            session = pipeline._asr(pipeline.settings)
            segments = session.transcribe(Path(args.diagnose_asr), pipeline.settings.source_language or None)
            result = {"provider": pipeline.settings.asr_provider, "segments": [asdict(row) for row in segments]}
        except (OSError, RuntimeError, ValueError) as exc:
            stdout.write(json.dumps({"error": str(exc)}) + "\n")
            stdout.flush()
            raise SystemExit(1) from None
        finally:
            pipeline.close()
        stdout.write(json.dumps(result) + "\n")
        stdout.flush()
        return
    if args.diagnose:
        # Do not load Settings: that may read or migrate credentials.
        data = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
        result = {"state_root": str(root), "resource_root": str(resource_root())}
        for name in ("ffmpeg", "ffprobe"):
            result[name] = command_status(data.get(name, name))
        stdout = sys.stdout or open(1, "w", encoding="utf-8", closefd=False)
        stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
        stdout.flush()
        return
    from subshort.app import launch
    launch(root)


if __name__ == "__main__":
    main()
