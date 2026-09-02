import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf


DEFAULT_INSTRUCT = (
    "标准普通话，成年男性，沉稳自然的纪录片科普讲解。全程保持一致的音高、音量、语速和情绪强度，"
    "吐字清晰，停顿自然，情绪克制，不夸张，不使用新闻播音腔、广告腔或方言口音。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch Qwen3-TTS synthesis for cn-science-video")
    parser.add_argument("--request", required=True, help="JSON containing lines [{id,text}]")
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output-dir", default="audio/segments")
    parser.add_argument("--meta", default="audio_meta.json")
    parser.add_argument("--speaker", default="Uncle_Fu")
    parser.add_argument("--language", default="Chinese")
    parser.add_argument("--instruct", default=DEFAULT_INSTRUCT)
    parser.add_argument("--speed", type=float, default=1.2)
    parser.add_argument("--batch-size", type=int, default=0, help="0 chooses from available VRAM")
    return parser.parse_args()


def fade_edges(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    count = min(int(sample_rate * 0.012), len(audio) // 2)
    if count:
        ramp = np.linspace(0.0, 1.0, count, dtype=np.float32)
        audio[:count] *= ramp
        audio[-count:] *= ramp[::-1]
    return np.clip(audio, -1.0, 1.0)


def speed_audio(source: Path, destination: Path, speed: float) -> None:
    if abs(speed - 1.0) < 1e-6:
        source.replace(destination)
        return
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-af", f"atempo={speed}", "-ar", "48000", str(destination)],
        check=True,
    )
    source.unlink(missing_ok=True)


def main() -> None:
    args = parse_args()
    import torch
    from qwen_tts import Qwen3TTSModel

    project = Path(args.project).resolve()
    request_path = Path(args.request).resolve()
    model_path = Path(args.model).resolve()
    output_dir = project / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = json.loads(request_path.read_text(encoding="utf-8"))["lines"]
    if not lines:
        raise ValueError("request contains no lines")

    torch.set_float32_matmul_precision("high")
    torch.backends.cuda.matmul.allow_tf32 = True
    total_vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
    batch_size = args.batch_size or (4 if total_vram >= 10 else 2 if total_vram >= 7 else 1)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(model_path), device_map="cuda:0", dtype=torch.bfloat16,
        attn_implementation="sdpa", local_files_only=True,
    )
    loaded = time.perf_counter()

    def generate(items: list[dict]) -> list[np.ndarray]:
        try:
            wavs, _ = model.generate_custom_voice(
                text=[item["text"] for item in items], speaker=[args.speaker] * len(items),
                language=[args.language] * len(items), instruct=[args.instruct] * len(items),
                do_sample=True, top_k=35, top_p=0.86, temperature=0.58,
                repetition_penalty=1.06, max_new_tokens=4096,
            )
            return wavs
        except torch.OutOfMemoryError:
            if len(items) == 1:
                raise
            torch.cuda.empty_cache()
            middle = len(items) // 2
            return generate(items[:middle]) + generate(items[middle:])

    records = []
    sample_rate = 24000
    for offset in range(0, len(lines), batch_size):
        batch = lines[offset:offset + batch_size]
        batch_started = time.perf_counter()
        wavs = generate(batch)
        batch_elapsed = time.perf_counter() - batch_started
        for item, wav in zip(batch, wavs):
            raw_path = output_dir / f".{item['id']}.raw.wav"
            output_path = output_dir / f"{item['id']}.wav"
            audio = fade_edges(wav, sample_rate)
            sf.write(raw_path, audio, sample_rate, subtype="PCM_16")
            speed_audio(raw_path, output_path, args.speed)
            info = sf.info(output_path)
            records.append({
                "id": str(item["id"]), "path": output_path.relative_to(project).as_posix(),
                "text": item["text"], "speaker": args.speaker, "language": args.language,
                "duration_s": round(info.frames / info.samplerate, 3),
                "source_duration_s": round(len(audio) / sample_rate, 3),
                "speed": args.speed, "batch_generation_s": round(batch_elapsed, 3),
                "sample_rate": info.samplerate,
            })

    payload = {
        "provider": "qwen3-tts", "model": model_path.name, "speaker": args.speaker,
        "language": args.language, "device": "cuda:0", "batch_size": batch_size,
        "speed": args.speed, "model_load_s": round(loaded - started, 3),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
        "voices": records, "total_duration_s": round(sum(item["duration_s"] for item in records), 3),
    }
    (project / args.meta).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
