import argparse
import json
import re
import time
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Align known TTS script text to Whisper word timings")
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", default="base")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--max-chars", type=int, default=16)
    parser.add_argument("--max-seconds", type=float, default=3.2)
    return parser.parse_args()


def normalize_word(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def group_words(words: list[dict], max_chars: int, max_seconds: float) -> list[dict]:
    groups = []
    current = []
    for word in words:
        token = normalize_word(word.get("word", ""))
        if not token:
            continue
        proposed = "".join(item["text"] for item in current) + token
        span = float(word["end"]) - (current[0]["start"] if current else float(word["start"]))
        if current and (len(proposed) > max_chars or span > max_seconds):
            groups.append({"text": "".join(item["text"] for item in current), "start": current[0]["start"], "end": current[-1]["end"]})
            current = []
        current.append({"text": token, "start": round(float(word["start"]), 3), "end": round(float(word["end"]), 3)})
    if current:
        groups.append({"text": "".join(item["text"] for item in current), "start": current[0]["start"], "end": current[-1]["end"]})
    return groups


def restore_script_text(groups: list[dict], script: str) -> list[dict]:
    target = re.sub(r"\s+", "", script)
    weights = [max(1, len(re.sub(r"[\W_]", "", group["text"]))) for group in groups]
    total = sum(weights)
    cursor = 0
    consumed = 0
    punctuation = "，。；！？、："
    for index, (group, weight) in enumerate(zip(groups, weights)):
        consumed += weight
        if index == len(groups) - 1:
            boundary = len(target)
        else:
            proposed = max(cursor + 1, round(len(target) * consumed / total))
            candidates = [position + 1 for position in range(max(cursor + 1, proposed - 3), min(len(target), proposed + 4)) if target[position] in punctuation]
            boundary = min(candidates, key=lambda value: abs(value - proposed)) if candidates else proposed
        group["text"] = target[cursor:boundary]
        cursor = boundary
    return groups


def main():
    args = parse_args()
    import torch
    import whisper

    project = Path(args.project).resolve()
    audio_meta = json.loads((project / "audio_meta.json").read_text(encoding="utf-8"))
    started = time.perf_counter()
    model = whisper.load_model(args.model, device="cuda" if torch.cuda.is_available() else "cpu", download_root=args.cache)
    output = {"model": args.model, "language": "zh", "segments": {}}
    for voice in audio_meta["voices"]:
        result = model.transcribe(str(project / voice["path"]), language="zh", word_timestamps=True, fp16=torch.cuda.is_available(), temperature=0)
        words = [word for segment in result["segments"] for word in segment.get("words", [])]
        groups = group_words(words, args.max_chars, args.max_seconds)
        output["segments"][voice["id"]] = restore_script_text(groups, voice["text"])
    output["elapsed_s"] = round(time.perf_counter() - started, 3)
    (project / "captions.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"elapsed_s": output["elapsed_s"], "groups": sum(len(value) for value in output["segments"].values())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
