import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
STOP_WORDS = {
    "a", "an", "and", "close", "domain", "for", "human", "image", "in", "microscopy",
    "of", "photo", "photograph", "public", "science", "the", "up", "histology",
}


def parse_args():
    parser = argparse.ArgumentParser(description="Resolve one licensed Wikimedia Commons image per scene")
    parser.add_argument("--project", required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(value or ""))).strip()


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def request_json(url: str, timeout: int) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "cn-science-video/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def license_is_allowed(value: str) -> bool:
    return bool(re.fullmatch(r"(?:CC0(?: 1\.0)?|Public domain|CC BY(?:-SA)?(?: \d+(?:\.\d+)?)?)", value.strip(), re.IGNORECASE))


def words(value: str) -> list[str]:
    value = re.sub(r"^file:", "", value.lower())
    value = re.sub(r"\.(?:jpe?g|png|webp)$", "", value)
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value)
    normalized = []
    for token in tokens:
        if token in STOP_WORDS:
            continue
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        normalized.append(token)
    return normalized


def relevance_score(query: str, title: str) -> float:
    query = re.sub(r"\bintitle:", "", query, flags=re.IGNORECASE).replace('"', "")
    query_words = words(query)
    title_words = words(title)
    if not query_words or not title_words:
        return 0.0
    overlap = len(set(query_words) & set(title_words)) / len(set(query_words))
    if not overlap:
        return 0.0
    query_phrase = " ".join(query_words)
    title_phrase = " ".join(title_words)
    if query_phrase in title_phrase:
        return round(min(1.0, 0.7 + 0.3 * overlap), 3)
    return round(0.35 * overlap, 3)


def image_file_valid(path: Path, mime: str) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    header = path.read_bytes()[:12]
    if mime == "image/jpeg":
        return header.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return False


def resolve(project: Path, segment: dict, timeout: int) -> dict:
    queries = list(segment.get("search_en") or []) + list(segment.get("search_zh") or [])
    for query in queries[:2]:
        params = {
            "action": "query", "generator": "search", "gsrsearch": query,
            "gsrnamespace": "6", "gsrlimit": "10", "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata", "format": "json", "formatversion": "2",
            "iiurlwidth": "1920",
        }
        api_url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
        try:
            pages = request_json(api_url, timeout).get("query", {}).get("pages", [])
        except Exception:
            continue
        ranked = []
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            metadata = info.get("extmetadata") or {}
            license_name = metadata.get("LicenseShortName", {}).get("value", "")
            if info.get("mime") not in ALLOWED_MIME or not license_is_allowed(license_name):
                continue
            score = relevance_score(query, page.get("title", ""))
            if score >= 0.45:
                ranked.append((score, page, info, metadata, license_name))
        for score, page, info, metadata, license_name in sorted(ranked, key=lambda item: item[0], reverse=True):
            original_url = info["url"].split("?", 1)[0]
            download_url = info.get("thumburl", original_url).split("?", 1)[0]
            extension = Path(urllib.parse.urlparse(download_url).path).suffix.lower() or ".jpg"
            destination = project / "assets" / "source" / f"commons-{segment['id']}{extension}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".part")
            request = urllib.request.Request(download_url, headers={"User-Agent": "cn-science-video/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
            except Exception:
                temporary.unlink(missing_ok=True)
                continue
            if not image_file_valid(temporary, info["mime"]):
                temporary.unlink(missing_ok=True)
                continue
            os.replace(temporary, destination)
            return {
                "id": f"commons-{segment['id']}", "segment_id": str(segment["id"]), "kind": "image",
                "title": page["title"], "source_url": info.get("descriptionurl"), "download_url": download_url,
                "original_url": original_url,
                "query": query, "relevance_score": score,
                "creator": clean(metadata.get("Artist", {}).get("value", "Wikimedia Commons contributor")),
                "license": license_name, "license_url": metadata.get("LicenseUrl", {}).get("value", ""),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "local_source": destination.relative_to(project).as_posix(), "sha256": digest(destination), "used": True,
            }
    return {
        "id": f"commons-{segment['id']}", "segment_id": str(segment["id"]), "kind": "image",
        "used": False, "fallback": segment.get("fallback_visual", "original diagram"),
        "error": "no compatible licensed Commons image found within search budget",
    }


def main():
    args = parse_args()
    project = Path(args.project).resolve()
    segments = json.loads((project / "segments.json").read_text(encoding="utf-8"))
    worker_count = max(1, min(args.workers, len(segments)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        records = list(executor.map(lambda segment: resolve(project, segment, args.timeout), segments))
    manifest = project / "assets" / "manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_name(manifest.name + ".tmp")
    temporary.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    os.replace(temporary, manifest)
    print(json.dumps({"resolved": sum(bool(record["used"]) for record in records), "fallbacks": sum(not record["used"] for record in records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
