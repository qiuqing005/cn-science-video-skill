import argparse
import atexit
import concurrent.futures
import ctypes
import hashlib
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
    parser.add_argument("--render-workers", type=int, help="Chrome render workers; auto-tuned when omitted")
    parser.add_argument("--no-gpu-render", action="store_true", help="Disable NVENC even when the local smoke test passes")
    parser.add_argument("--force", action="store_true", help="Ignore reusable outputs")
    parser.add_argument("--force-render", action="store_true", help="Re-render without invalidating voice, assets, captions, or assembly")
    parser.add_argument("--skip-render", action="store_true")
    return parser.parse_args()


def is_fresh(outputs: list[Path], inputs: list[Path]) -> bool:
    if not outputs or any(not path.exists() for path in outputs):
        return False
    existing_inputs = [path for path in inputs if path.exists()]
    if not existing_inputs:
        return True
    return min(path.stat().st_mtime for path in outputs) >= max(path.stat().st_mtime for path in existing_inputs)


def system_memory_gb() -> float | None:
    try:
        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong), ("avail_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended_virtual", ctypes.c_ulonglong),
                ]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.total_phys / 1024**3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (AttributeError, OSError, ValueError):
        return None


def choose_render_workers(cpu_count: int | None = None, total_memory_gb: float | None = None) -> int:
    cpus = cpu_count or os.cpu_count() or 4
    memory = total_memory_gb if total_memory_gb is not None else system_memory_gb()
    if memory is not None and memory <= 8:
        return 1
    cpu_limit = max(1, cpus // 2)
    memory_limit = 6 if memory is None else max(1, int((memory - 6) / 1.5))
    return min(6, cpu_limit, memory_limit)


def read_manifest_assets(project: Path, manifest: Path) -> list[Path]:
    if not manifest.exists():
        return []
    assets = []
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not record.get("used"):
            continue
        local_source = record.get("local_source") or record.get("local_render")
        if not local_source:
            raise RuntimeError(f"used asset has no local path at manifest line {line_number}")
        asset = (project / local_source).resolve()
        try:
            asset.relative_to(project)
        except ValueError as exc:
            raise RuntimeError(f"manifest asset escapes the project directory: {asset}") from exc
        if not asset.is_file():
            raise RuntimeError(f"manifest references missing asset: {asset}")
        assets.append(asset)
    return assets


def collect_render_inputs(
    project: Path, index: Path, timeline: Path, voice_files: list[Path],
    manifest: Path, subset_font: Path,
) -> list[Path]:
    inputs = [index, timeline, *voice_files]
    if manifest.exists():
        inputs.append(manifest)
        inputs.extend(read_manifest_assets(project, manifest))
    if subset_font.exists():
        inputs.append(subset_font)
    vendor = project / "assets" / "vendor"
    if vendor.exists():
        inputs.extend(path for path in vendor.rglob("*") if path.is_file())
    return inputs


def write_atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_verified_marker(marker: Path, target: Path) -> None:
    payload = {"size": target.stat().st_size, "sha256": file_digest(target)}
    write_atomic_text(marker, json.dumps(payload, indent=2) + "\n")


def verified_marker_matches(marker: Path, target: Path, rules: Path) -> bool:
    if not is_fresh([marker], [target, rules]):
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        return payload.get("size") == target.stat().st_size and payload.get("sha256") == file_digest(target)
    except (OSError, json.JSONDecodeError):
        return False


def gpu_encoder_available(env: dict[str, str]) -> bool:
    sink = "NUL" if os.name == "nt" else "/dev/null"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=256x256:d=0.04", "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", sink],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


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
    report = {"status": "failed", "error": "pipeline terminated before completion"}

    def write_performance() -> None:
        timings["summary"] = {
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "project": str(project),
            "render_skipped": args.skip_render,
            "total_elapsed_s": round(time.perf_counter() - pipeline_started, 3),
            **report,
        }
        try:
            write_atomic_text(work / "performance.json", json.dumps(timings, ensure_ascii=False, indent=2) + "\n")
        except OSError:
            pass

    atexit.register(write_performance)

    def run(label: str, command: list[str], required_stage: bool = True) -> int:
        started = time.perf_counter()
        log_path = work / f"{label}.log"
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
        elapsed = round(time.perf_counter() - started, 3)
        timings[label] = {"elapsed_s": elapsed, "exit_code": result.returncode, "log": log_path.relative_to(project).as_posix()}
        print(f"{label}: {elapsed:.1f}s (exit {result.returncode})", flush=True)
        if required_stage and result.returncode:
            message = f"{label} failed; see {log_path}"
            report.update({"failed_stage": label, "error": message})
            raise RuntimeError(message)
        return result.returncode

    audio_meta = project / "audio_meta.json"
    asset_manifest = project / "assets" / "manifest.jsonl"
    subset_font = project / "assets" / "fonts" / "NotoSansSC-Subset.ttf"
    parallel = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        voice_script = skill_dir / "scripts" / "qwen_batch_tts.py"
        voice_meta_matches = False
        if audio_meta.exists():
            try:
                existing_audio = json.loads(audio_meta.read_text(encoding="utf-8"))
                voice_meta_matches = existing_audio.get("model") == model.name and abs(float(existing_audio.get("speed", 0)) - args.speed) < 1e-6
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if not args.force and voice_meta_matches and is_fresh([audio_meta], [project / "audio_request.json", voice_script]):
            timings["voice"] = {"skipped": True, "reason": "fresh audio_meta.json"}
        else:
            parallel["voice"] = executor.submit(run, "voice", [
                str(tts_python), str(voice_script),
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
                return_code = future.result()
                if label == "assets" and return_code:
                    write_atomic_text(asset_manifest, "")
                    timings[label]["degraded_to_fallback"] = True
            except Exception:
                if label == "assets":
                    write_atomic_text(asset_manifest, "")
                    timings[label] = {**timings.get(label, {}), "degraded_to_fallback": True}
                    continue
                raise

    if not audio_meta.exists():
        raise RuntimeError("voice stage did not produce audio_meta.json")
    audio = json.loads(audio_meta.read_text(encoding="utf-8"))
    voice_files = [project / item["path"] for item in audio.get("voices", [])]
    if not voice_files or any(not path.exists() for path in voice_files):
        raise RuntimeError("audio_meta.json references missing voice files")

    captions = project / "captions.json"
    caption_script = skill_dir / "scripts" / "align_captions.py"
    caption_model_matches = False
    if captions.exists():
        try:
            caption_model_matches = json.loads(captions.read_text(encoding="utf-8")).get("model") == args.whisper_model
        except json.JSONDecodeError:
            pass
    if not args.force and caption_model_matches and is_fresh([captions], [audio_meta, *voice_files, caption_script]):
        timings["captions"] = {"skipped": True, "reason": "fresh captions.json"}
    else:
        run("captions", [
            str(tts_python), str(caption_script),
            "--project", str(project), "--model", args.whisper_model,
            "--cache", str(Path(args.whisper_cache).resolve()),
        ])

    index = project / "index.html"
    timeline = project / "timeline.json"
    manifest_assets = read_manifest_assets(project, asset_manifest)
    assemble_inputs = [project / "assemble.mjs", project / "segments.json", audio_meta, captions]
    if asset_manifest.exists():
        assemble_inputs.extend([asset_manifest, *manifest_assets])
    if not args.force and is_fresh([index, timeline], assemble_inputs):
        timings["assemble"] = {"skipped": True, "reason": "fresh composition"}
    else:
        run("assemble", ["node", str(project / "assemble.mjs")])

    timeline_data = json.loads(timeline.read_text(encoding="utf-8"))
    midpoints = [round(float(scene["start"]) + float(scene["duration"]) / 2, 3) for scene in timeline_data["scenes"]]
    contact_sheet = project / "snapshots" / "contact-sheet.jpg"
    visual_inputs = collect_render_inputs(project, index, timeline, voice_files, asset_manifest, subset_font)
    snapshot_fresh = not args.force and is_fresh([contact_sheet], visual_inputs)
    if snapshot_fresh:
        timings["snapshot"] = {"skipped": True, "reason": "fresh contact sheet"}
    quality_marker = work / "quality-checks.ok"
    quality_inputs = [*visual_inputs, hf_cli, Path(__file__).resolve()]
    quality_fresh = not args.force and is_fresh([quality_marker], quality_inputs)
    if quality_fresh:
        for label in ["lint", "validate", "inspect"]:
            timings[label] = {"skipped": True, "reason": "fresh strict quality gate"}
        if not snapshot_fresh:
            run("snapshot", ["node", str(hf_cli), "snapshot", "--at", ",".join(map(str, midpoints))])
    else:
        run("lint", ["node", str(hf_cli), "lint"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            checks = [
                executor.submit(run, "validate", ["node", str(hf_cli), "validate", "--timeout", "10000"]),
                executor.submit(run, "inspect", ["node", str(hf_cli), "inspect", "--strict"]),
            ]
            if not snapshot_fresh:
                checks.append(executor.submit(run, "snapshot", ["node", str(hf_cli), "snapshot", "--at", ",".join(map(str, midpoints))]))
            for future in checks:
                future.result()
        validate_log = (work / "validate.log").read_text(encoding="utf-8", errors="replace")
        validate_summary = re.search(r"(\d+) error\(s\),\s*(\d+) warning\(s\)", validate_log)
        if validate_summary:
            timings["validate"].update({"errors": int(validate_summary.group(1)), "warnings": int(validate_summary.group(2))})
        inspect_log = (work / "inspect.log").read_text(encoding="utf-8", errors="replace")
        inspect_summary = re.search(r"(\d+) layout issues?", inspect_log)
        if inspect_summary:
            timings["inspect"]["layout_issues"] = int(inspect_summary.group(1))
        write_atomic_text(quality_marker, time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n")

    rendered = renders / "rendered.mp4"
    final = renders / "final.mp4"
    if not args.skip_render:
        if not args.force and not args.force_render and is_fresh([rendered], visual_inputs):
            timings["render"] = {"skipped": True, "reason": "fresh rendered.mp4"}
        else:
            rendered_temporary = rendered.with_name(".rendered.tmp.mp4")
            rendered_temporary.unlink(missing_ok=True)
            render_workers = args.render_workers or choose_render_workers()
            use_gpu = not args.no_gpu_render and gpu_encoder_available(env)
            timings["render_config"] = {"workers": render_workers, "gpu": use_gpu}
            render_command = [
                "node", str(hf_cli), "render", "--quality", "high",
                "--workers", str(render_workers), "--output", str(rendered_temporary),
            ]
            if use_gpu:
                render_command.append("--gpu")
            return_code = run("render", render_command, False)
            if return_code and use_gpu:
                timings["render"]["fallback"] = "cpu"
                rendered_temporary.unlink(missing_ok=True)
                run("render_fallback_cpu", [
                    "node", str(hf_cli), "render", "--quality", "high",
                    "--workers", str(render_workers), "--output", str(rendered_temporary),
                ])
            elif return_code:
                raise RuntimeError(f"render failed; see {work / 'render.log'}")
            if not rendered_temporary.is_file():
                raise RuntimeError("render command succeeded without producing an output file")
            os.replace(rendered_temporary, rendered)
        if not args.force and is_fresh([final], [rendered, Path(__file__).resolve()]):
            timings["finalize"] = {"skipped": True, "reason": "fresh final.mp4"}
        else:
            final_temporary = final.with_name(".final.tmp.mp4")
            final_temporary.unlink(missing_ok=True)
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
                "-ar", "48000", "-movflags", "+faststart", str(final_temporary),
            ])
            if not final_temporary.is_file():
                raise RuntimeError("finalize command succeeded without producing an output file")
            os.replace(final_temporary, final)
        final_marker = work / "final-media.ok.json"
        if not args.force and verified_marker_matches(final_marker, final, Path(__file__).resolve()):
            timings["media_gate"] = {"skipped": True, "reason": "verified final.mp4 SHA-256"}
        else:
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
            write_verified_marker(final_marker, final)

    report.update({"status": "complete", "error": None})
    write_performance()
    atexit.unregister(write_performance)
    print(json.dumps(timings, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
