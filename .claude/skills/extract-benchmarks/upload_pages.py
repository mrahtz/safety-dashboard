"""Upload rasterized PDF page PNGs to Supabase Storage bucket `page-images`.

Scans var/extract/<slug>/page-*.png (pdftoppm output), strips the zero-padding
from the filename (e.g. "page-001" → page_num=1), and upserts each file to
  page-images/<source_id>/page-<N>.png
so review.html can build the URL from the integer page_num in metrics.

Requires the bucket to exist — run create_bucket.py first.

Usage:
    python3 .claude/skills/extract-benchmarks/upload_pages.py <slug> <source_id>
"""

import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

BUCKET = "page-images"


def _env() -> dict[str, str]:
    """Supabase write credentials, read straight from the environment.
    Internally we key on SUPABASE_SERVICE_ROLE; the env var is
    SUPABASE_SERVICE_ROLE_KEY (the older SUPABASE_SERVICE_ROLE name is also
    accepted)."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_SERVICE_ROLE", "")).strip()
    if not url or not key:
        raise SystemExit(
            "Missing Supabase credentials. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY in the environment."
        )
    return {"SUPABASE_URL": url, "SUPABASE_SERVICE_ROLE": key}


def _upload(env: dict[str, str], storage_path: str, data: bytes, tries: int = 4) -> None:
    url = f"{env['SUPABASE_URL']}/storage/v1/object/{BUCKET}/{storage_path}"
    for attempt in range(tries):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
                "apikey": env["SUPABASE_SERVICE_ROLE"],
                "Content-Type": "image/png",
                "x-upsert": "true",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=120)
            return
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Upload {storage_path} -> {e.code}: {e.read()[:200]!r}") from e
        except urllib.error.URLError:
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    slug, source_id = argv[1], int(argv[2])
    env = _env()
    imgs = sorted(pathlib.Path(f"var/extract/{slug}").glob("page-*.png"))
    if not imgs:
        print(f"No page-*.png files found in var/extract/{slug}/")
        return 1
    for img in imgs:
        page_num = int(img.stem.split("-")[1])  # "page-001" → 1
        storage_path = f"{source_id}/page-{page_num}.png"
        _upload(env, storage_path, img.read_bytes())
        print(f"  uploaded page {page_num} ({img.name})")
    print(f"Done — {len(imgs)} pages uploaded to {BUCKET}/{source_id}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
