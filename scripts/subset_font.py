import argparse
from pathlib import Path


BASE_TEXT = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz，。！？：；、“”‘’（）《》【】+-→←↔↓↑%/.· "


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Subset a video font to the characters used by the project")
    parser.add_argument("--font", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--text", action="append", default=[], help="UTF-8 text file; repeatable")
    parser.add_argument("--literal", action="append", default=[], help="Additional literal text")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError as exc:
        raise SystemExit("FontTools is required in the workspace environment: python -m pip install fonttools") from exc

    font_path = Path(args.font).resolve()
    output_path = Path(args.output).resolve()
    text = BASE_TEXT + "".join(args.literal)
    for filename in args.text:
        text += Path(filename).resolve().read_text(encoding="utf-8")
    if not font_path.is_file():
        raise SystemExit(f"Font not found: {font_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recommended_glyphs = True
    font = TTFont(font_path)
    worker = subset.Subsetter(options=options)
    worker.populate(text="".join(dict.fromkeys(text)))
    worker.subset(font)
    font.save(output_path)
    print(f"subset {font_path.name} -> {output_path} ({output_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
