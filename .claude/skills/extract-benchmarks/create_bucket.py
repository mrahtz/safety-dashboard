"""Create the `page-images` Supabase Storage bucket (public, idempotent).

Run once per project before the first upload_pages.py call. Safe to re-run —
a 409 (already exists) is treated as success.

Usage:
    python3 .claude/skills/extract-benchmarks/create_bucket.py
"""

import json
import pathlib
import urllib.error
import urllib.request

ENV_PATH = pathlib.Path("var/supabase.env")
BUCKET = "page-images"


def _env() -> dict[str, str]:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}. Run extract-benchmarks first to create it.")
    return dict(
        line.split("=", 1)
        for line in ENV_PATH.read_text().splitlines()
        if "=" in line
    )


def main() -> None:
    env = _env()
    url = f"{env['SUPABASE_URL']}/storage/v1/bucket"
    body = json.dumps({"id": BUCKET, "name": BUCKET, "public": True}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
            "apikey": env["SUPABASE_SERVICE_ROLE"],
            "Content-Type": "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        print(f"Bucket '{BUCKET}' created.")
    except urllib.error.HTTPError as e:
        # "Already exists" comes back inconsistently: sometimes HTTP 409, but
        # Storage also returns HTTP 400 whose JSON body carries statusCode "409"
        # / error "Duplicate". Treat any of those as success so re-runs are safe.
        detail = e.read()
        text = detail.decode("utf-8", "replace")
        if e.code == 409 or '"Duplicate"' in text or "already exists" in text:
            print(f"Bucket '{BUCKET}' already exists — OK.")
        else:
            raise RuntimeError(f"POST /storage/v1/bucket -> {e.code}: {detail[:200]!r}") from e


if __name__ == "__main__":
    main()
