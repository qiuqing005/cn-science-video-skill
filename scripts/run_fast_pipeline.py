import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the cn-science-video fast pipeline")
    parser.add_argument("--project", required=True)
    parser.add_argument("--tts-python", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--whisper-cache", required=True)
    parser.add_argument("--font")
    parser.add_argument("--asset-script", help="Project-local Python asset resolver")
    parser.add_argument("--hf-cli", help="Project-local hf-cli.mjs; defaults to <project>/hf-cli.mjs")
    parser.add_argument("--speed", type=float, default=1.2)
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--force", action="store_true", help="Ignore reusable outputs")
    parser.add_argument("--skip-render", action="store_true")
    return parser.parse_args()


def is_fresh(outputs: list[Path], inputs: list[Path]) -> bool:
    if not outputs or any(not path.exists() for path in outputs):
        return False
    existing_inputs = [path for path in inputs if path.exists()]
    if not existing_inputs:
        return True
    return min(path.stat().st_mtime for path in outputs) >= max(path.stat().st_mtime for path in existing_inputs)


def main() -> None:
    pipeline_started = time.perf_counter()
    args = parse_args()
    skill_dir = Path(__file__).resolve().parents[1]
    project = Path(args.project).resolve()
    workspace = project.parent
    work = project / "work"
    renders = project / "renders"
    work.mkdir(parents=True, exist_ok=True)
    renders.mkdir(parents=True, exist_ok=True)
    required = [project / "audio_request.json", project / "segments.json", project / "assemble.mjs"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing project inputs: " + ", ".join(missing))

    tts_python = Path(args.tts_python).resolve()
    model = Path(args.model).resolve()
    hf_cli = Path(args.hf_cli).resolve() if args.hf_cli else project / "hf-cli.mjs"
    if not tts_python.exists() or not model.exists() or not hf_cli.exists():
        raise SystemExit("TTS Python, model, or hf-cli path does not exist")

    env = os.environ.copy()
    cache = workspace / ".cache"
    env.update({
        "HF_HOME": str(cache / "huggingface"),
        "HUGGINGFACE_HUB_CACHE": str(cache / "huggingface" / "hub"),
        "MODELSCOPE_CACHE": str(cache / "modelscope"),
        "TORCH_HOME": str(cache / "torch"),
        "PIP_CACHE_DIR": str(cache / "pip"),
        "XDG_CACHE_HOME": str(cache),
        "TEMP": str(cache / "temp"),
        "TMP": str(cache / "temp"),
    })
    for directory in [cache / "temp", Path(args.whisper_cache).resolve()]:
        directory.mkdir(parents=True, exist_ok=True)

    timings: dict[str, dict] = {}

    def run(label: str, command: list[str], required_stage: bool = True) -> int:
        started = time.perf_counter()
        log_path = work / f"{label}.log"
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        elapsed = round(time.perf_counter() - started, 3)
        timings[label] = {"elapsed_s": elapsed, "exit_code": result.returncode, "log": log_path.relative_to(project).as_posix()}
        print(f"{label}: {elapsed:.1f}s (exit {result.returncode})", flush=True)
        if required_stage and result.returncode:
            raise RuntimeError(f"{label} failed; see {log_path}")
        return result.returncode

    audio_meta = project / "audio_meta.json"
    asset_manifest = project / "assets" / "manifest.jsonl"
    subset_font = project / "assets" / "fonts" / "NotoSansSC-Subset.ttf"
    parallel = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        if not args.force and is_fresh([audio_meta], [project / "audio_request.json"]):
            timings["voice"] = {"skipped": True, "reason": "fresh audio_meta.json"}
        else:
            parallel["voice"] = executor.submit(run, "voice", [
                str(tts_python), str(skill_dir / "scripts" / "qwen_batch_tts.py"),
                "--request", str(project / "audio_request.json"), "--project", str(project),
                "--model", str(model), "--speed", str(args.speed),
            ])

        project_asset_script = project / "scripts" / "download_assets.py"
        asset_script = Path(args.asset_script).resolve() if args.asset_script else (project_asset_script if project_asset_script.exists() else skill_dir / "scripts" / "download_commons_assets.py")
        if asset_script.exists():
            if not args.force and is_fresh([asset_manifest], [project / "segments.json", asset_script]):
                timings["assets"] = {"skipped": True, "reason": "fresh asset manifest"}
            else:
                asset_command = [sys.executable, str(asset_script)]
                if asset_script == skill_dir / "scripts" / "download_commons_assets.py":
                    asset_command.extend(["--project", str(project)])
                parallel["assets"] = executor.submit(run, "assets", asset_command, False)
        else:
            timings["assets"] = {"skipped": True, "reason": "no asset script"}

        if args.font:
            font = Path(args.font).resolve()
            font_inputs = [font, project / "SCRIPT_SOURCE.md", project / "segments.json"]
            if not args.force and is_fresh([subset_font], font_inputs):
                timings["font"] = {"skipped": True, "reason": "fresh font subset"}
            else:
                command = [sys.executable, str(skill_dir / "scripts" / "subset_font.py"), "--font", str(font), "--output", str(subset_font)]
                for text_path in font_inputs[1:]:
                    if text_path.exists():
                        command.extend(["--text", str(text_path)])
                parallel["font"] = executor.submit(run, "font", command)
        else:
            timings["font"] = {"skipped": True, "reason": "no font supplied"}

        for label, future in parallel.items():
            try:
                future.result()
            except Exception:
                if label == "assets":
                    continue
                raise

    if not audio_meta.exists():
        raise RuntimeError("voice stage did not produce audio_meta.json")
    audio = json.loads(audio_meta.read_text(encoding="utf-8"))
    voice_files = [project / item["path"] for item in audio.get("voices", [])]
    if not voice_files or any(not path.exists() for path in voice_files):
        raise RuntimeError("audio_meta.json references missing voice files")

    captions = project / "captions.json"
    if not args.force and is_fresh([captions], [audio_meta, *voice_files]):
        timings["captions"] = {"skipped": True, "reason": "fresh captions.json"}
    else:
        run("captions", [
            str(tts_python), str(skill_dir / "scripts" / "align_captions.py"),
            "--project", str(project), "--model", args.whisper_model,
            "--cache", str(Path(args.whisper_cache).resolve()),
        ])

    index = project / "index.html"
    timeline = project / "timeline.json"
    assemble_inputs = [project / "assemble.mjs", project / "segments.json", audio_meta, captions]
    if not args.force and is_fresh([index, timeline], assemble_inputs):
        timings["assemble"] = {"skipped": True, "reason": "fresh composition"}
    else:
        run("assemble", ["node", str(project / "assemble.mjs")])

    run("lint", ["node", str(hf_cli), "lint"])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run, "validate", ["node", str(hf_cli), "validate"]),
            executor.submit(run, "inspect", ["node", str(hf_cli), "inspect"]),
        ]
        for future in futures:
            future.result()

    timeline_data = json.loads(timeline.read_text(encoding="utf-8"))
    midpoints = [round(float(scene["start"]) + float(scene["duration"]) / 2, 3) for scene in timeline_data["scenes"]]
    contact_sheet = project / "snapshots" / "contact-sheet.jpg"
    if not args.force and is_fresh([contact_sheet], [index]):
        timings["snapshot"] = {"skipped": True, "reason": "fresh contact sheet"}
    else:
        run("snapshot", ["node", str(hf_cli), "snapshot", "--at", ",".join(map(str, midpoints))])

    rendered = renders / "rendered.mp4"
    final = renders / "final.mp4"
    if not args.skip_render:
        if not args.force and is_fresh([rendered], [index, *voice_files]):
            timings["render"] = {"skipped": True, "reason": "fresh rendered.mp4"}
        else:
            run("render", ["node", str(hf_cli), "render", "--quality", "high", "--output", str(rendered)])
        if not args.force and is_fresh([final], [rendered, Path(__file__).resolve()]):
            timings["finalize"] = {"skipped": True, "reason": "fresh final.mp4"}
        else:
            run("normalize_analyze", [
                "ffmpeg", "-hide_banner", "-i", str(rendered),
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
            ])
            analysis_log = (work / "normalize_analyze.log").read_text(encoding="utf-8", errors="replace")
            blocks = re.findall(r"\{\s*\"input_i\"[\s\S]*?\}", analysis_log)
            if not blocks:
                raise RuntimeError("could not parse loudnorm analysis")
            measured = json.loads(blocks[-1])
            loudnorm = (
                "loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:print_format=summary:"
                f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
                f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
                f"offset={measured['target_offset']}"
            )
            run("finalize", [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(rendered),
                "-map", "0:v", "-map", "0:a", "-c:v", "copy",
                "-af", loudnorm, "-c:a", "aac", "-b:a", "192k",
                "-ar", "48000", "-movflags", "+faststart", str(final),
            ])
        run("ffprobe", [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size,bit_rate:stream=index,codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels,duration",
            "-of", "json", str(final),
        ])
        probe = json.loads((work / "ffprobe.log").read_text(encoding="utf-8"))
        video_streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"]
        audio_streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
        if not video_streams or not audio_streams:
            raise RuntimeError("final media is missing video or audio")
        video = video_streams[0]
        audio_stream = audio_streams[0]
        if int(video.get("width", 0)) <= 0 or int(video.get("height", 0)) <= 0:
            raise RuntimeError("final video has invalid dimensions")
        if abs(float(video.get("duration", 0)) - float(audio_stream.get("duration", 0))) > 0.25:
            raise RuntimeError("final audio/video durations differ by more than 250ms")
        run("blackdetect", ["ffmpeg", "-hide_banner", "-i", str(final), "-vf", "blackdetect=d=0.3:pix_th=0.10", "-an", "-f", "null", "NUL" if os.name == "nt" else "/dev/null"], False)
        if "black_start:" in (work / "blackdetect.log").read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError("blackdetect found a black segment of at least 0.3s")
        run("loudness", ["ffmpeg", "-hide_banner", "-i", str(final), "-filter_complex", "ebur128=peak=true", "-f", "null", "NUL" if os.name == "nt" else "/dev/null"], False)
        loudness_log = (work / "loudness.log").read_text(encoding="utf-8", errors="replace")
        matches = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", loudness_log)
        if not matches or not -17.5 <= float(matches[-1]) <= -14.5:
            raise RuntimeError("final integrated loudness is outside -16 +/- 1.5 LUFS")

    timings["summary"] = {
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "project": str(project),
        "render_skipped": args.skip_render,
        "total_elapsed_s": round(time.perf_counter() - pipeline_started, 3),
    }
    (work / "performance.json").write_text(json.dumps(timings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(timings, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
