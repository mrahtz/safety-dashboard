"""Standalone publish probe: test the Supabase service key against Storage and
PostgREST with throwaway data, without running ingest or touching real rows.

Exercises the exact two operations publish.py performs:
  1. a Storage upload to the ``crops`` bucket (the step that was failing with
     "Invalid Compact JWS" when the key isn't a JWT), and
  2. a PostgREST upsert into ``sources``.

Both use dummy data under an isolated id/key and are deleted afterwards. Reads
``SUPABASE_URL`` + ``SUPABASE_KEY`` from the environment. stdlib only.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_KEY"].strip()
DUMMY_SOURCE_ID = 999999  # far above any real source id; deleted after the test


def req(method: str, path: str, headers: dict, data: bytes | None = None):
    r = urllib.request.Request(URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read()[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]
    except Exception as e:  # noqa: BLE001 - surface connection errors verbatim
        return -1, repr(e).encode()


def main() -> None:
    # Don't print the key; just its shape (CI also masks the secret anyway).
    print(f"key: len={len(KEY)} looks_like_jwt={KEY.count('.') == 2 and KEY.startswith('eyJ')}")
    auth = {"Authorization": f"Bearer {KEY}", "apikey": KEY}

    # 1) Storage upload (the exact failing op): a 1x1 PNG under a probe key.
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                        "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082")
    testkey = "__probe__/" + hashlib.sha256(png).hexdigest() + ".png"
    st, body = req("POST", f"/storage/v1/object/crops/{testkey}",
                   {**auth, "Content-Type": "image/png", "x-upsert": "true"}, png)
    storage_ok = st in (200, 201)
    print(f"[STORAGE upload]   HTTP {st}  {body!r}  -> {'OK' if storage_ok else 'FAIL'}")
    if storage_ok:
        req("DELETE", f"/storage/v1/object/crops/{testkey}", auth)

    # 2) PostgREST upsert into sources, then clean up.
    row = [{"id": DUMMY_SOURCE_ID, "kind": "pdf", "origin_url": "https://probe.invalid",
            "sha256": "probe", "retrieved_at": "2026-01-01T00:00:00+00:00", "blob_path": "/probe"}]
    st, body = req("POST", "/rest/v1/sources?on_conflict=id",
                   {**auth, "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal"},
                   json.dumps(row).encode())
    rest_ok = st in (200, 201, 204)
    print(f"[POSTGREST upsert] HTTP {st}  {body!r}  -> {'OK' if rest_ok else 'FAIL'}")
    req("DELETE", f"/rest/v1/sources?id=eq.{DUMMY_SOURCE_ID}", auth)

    print(f"\nRESULT: storage={'OK' if storage_ok else 'FAIL'} "
          f"postgrest={'OK' if rest_ok else 'FAIL'}")
    print("=> publish will succeed" if storage_ok and rest_ok
          else "=> publish will FAIL (see the failing op above)")


if __name__ == "__main__":
    main()
