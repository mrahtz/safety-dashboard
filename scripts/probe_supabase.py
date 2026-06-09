"""Standalone publish probe: test the Supabase service key against PostgREST
with throwaway data, without running ingest or touching real rows.

Tests that the service key can upsert sources and metrics tables.
Reads ``SUPABASE_URL`` + ``SUPABASE_KEY`` from the environment. stdlib only.
"""

import base64
import json
import os
import urllib.error
import urllib.request

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_KEY"].strip()
DUMMY_SOURCE_ID = 999999
DUMMY_METRIC_ID = 999999


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
    print(f"key: len={len(KEY)} looks_like_jwt={KEY.count('.') == 2 and KEY.startswith('eyJ')}")
    auth = {"Authorization": f"Bearer {KEY}", "apikey": KEY}

    # 1) PostgREST upsert into sources with embedded blob.
    blob_b64 = base64.b64encode(b"<html>test</html>").decode("ascii")
    src_row = [{"id": DUMMY_SOURCE_ID, "kind": "html", "origin_url": "https://probe.invalid",
                "sha256": "probe_src", "blob": blob_b64, "retrieved_at": "2026-01-01T00:00:00Z"}]
    st, body = req("POST", "/rest/v1/sources?on_conflict=id",
                   {**auth, "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal"},
                   json.dumps(src_row).encode())
    sources_ok = st in (200, 201, 204)
    print(f"[SOURCES upsert]  HTTP {st}  {body!r}  -> {'OK' if sources_ok else 'FAIL'}")

    # 2) PostgREST upsert into metrics.
    met_row = [{"id": DUMMY_METRIC_ID, "source_id": DUMMY_SOURCE_ID,
                "model": "test", "benchmark": "test", "value": "0.5", "units": "",
                "section_key": "t0", "accepted": True}]
    st, body = req("POST", "/rest/v1/metrics?on_conflict=id",
                   {**auth, "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates,return=minimal"},
                   json.dumps(met_row).encode())
    metrics_ok = st in (200, 201, 204)
    print(f"[METRICS upsert]  HTTP {st}  {body!r}  -> {'OK' if metrics_ok else 'FAIL'}")

    # Clean up.
    req("DELETE", f"/rest/v1/metrics?id=eq.{DUMMY_METRIC_ID}", auth)
    req("DELETE", f"/rest/v1/sources?id=eq.{DUMMY_SOURCE_ID}", auth)

    print(f"\nRESULT: sources={'OK' if sources_ok else 'FAIL'} "
          f"metrics={'OK' if metrics_ok else 'FAIL'}")
    print("=> publish will succeed" if sources_ok and metrics_ok
          else "=> publish will FAIL (see the failing op above)")


if __name__ == "__main__":
    main()
