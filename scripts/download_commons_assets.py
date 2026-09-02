import argparse
import concurrent.futures
import hashlib
import html
import json
import re
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


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
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            metadata = info.get("extmetadata") or {}
            license_name = metadata.get("LicenseShortName", {}).get("value", "")
            if info.get("mime") not in ALLOWED_MIME or not license_is_allowed(license_name):
                continue
            original_url = info["url"].split("?", 1)[0]
            download_url = info.get("thumburl", original_url).split("?", 1)[0]
            extension = Path(urllib.parse.urlparse(download_url).path).suffix.lower() or ".jpg"
            destination = project / "assets" / "source" / f"commons-{segment['id']}{extension}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(download_url, headers={"User-Agent": "cn-science-video/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as output:
                    shutil.copyfileobj(response, output)
            except Exception:
                destination.unlink(missing_ok=True)
                continue
            if destination.stat().st_size < 1024:
                destination.unlink(missing_ok=True)
                continue
            return {
                "id": f"commons-{segment['id']}", "segment_id": str(segment["id"]), "kind": "image",
                "title": page["title"], "source_url": info.get("descriptionurl"), "download_url": download_url,
                "original_url": original_url,
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
    manifest.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    print(json.dumps({"resolved": sum(bool(record["used"]) for record in records), "fallbacks": sum(not record["used"] for record in records)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
