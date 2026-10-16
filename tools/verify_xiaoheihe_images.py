"""Compare live image audit vs archived reading edition + MHTML."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "web_archive" / "xiaoheihe_image_audit.json"
CASE = ROOT / "docs" / "web_archive" / "xiaoheihe_case" / "archive"
OUT = ROOT / "docs" / "web_archive" / "xiaoheihe_image_coverage.json"


def main() -> int:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    html_path = next(CASE.rglob("article.html"))
    html = html_path.read_text(encoding="utf-8")
    media_dir = html_path.parent / "media"
    media_files = [p for p in media_dir.iterdir() if p.is_file()] if media_dir.is_dir() else []
    snap = next(CASE.rglob("snapshot.mhtml"), None)
    raw = snap.read_bytes() if snap else b""

    needed = audit["body_content_unique"]
    localized = len(re.findall(r'src="media/', html))
    placeholders = html.count("图片未下载")
    image_parts = raw.count(b"Content-Type: image")

    report = {
        "page_img_count": audit["page_img_count"],
        "body_img_count": audit["body_img_count"],
        "body_content_unique": needed,
        "by_kind": audit["by_kind"],
        "reading_edition_media_refs": localized,
        "reading_edition_media_files": len(media_files),
        "reading_edition_placeholders": placeholders,
        "mhtml_bytes": len(raw),
        "mhtml_image_content_types": image_parts,
        "pass_body_images_complete": needed == localized == len(media_files) and placeholders == 0,
        "pass_mhtml_has_images": image_parts >= needed,
        "body_content_srcs": [x["src"] for x in audit["body_content"]],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass_body_images_complete"] and report["pass_mhtml_has_images"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
