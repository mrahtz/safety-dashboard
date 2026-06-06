"""Content-addressed source freezer (section 4.1).

URLs rot and system cards get silently revised, so the first thing we do with a
source is snapshot its raw bytes into a store keyed by sha256, alongside the
origin URL and a retrieval timestamp. PDF extraction then runs against the frozen
copy; HTML rendering runs against the live page for faithful layout (the frozen
bytes are still kept for provenance and diffing -- a documented tradeoff, since
freezing a page's full asset bundle is out of scope for this milestone).
"""

import dataclasses
import datetime
import hashlib

from llm_metrics import fetch, paths


@dataclasses.dataclass(frozen=True)
class Frozen:
    sha256: str
    blob_path: str
    retrieved_at: str
    n_bytes: int


def _suffix(data: bytes, url: str) -> str:
    if data[:4] == b"%PDF":
        return ".pdf"
    if url.lower().split("?")[0].endswith(".pdf"):
        return ".pdf"
    return ".html"


def freeze(origin_url: str) -> Frozen:
    paths.ensure()
    data = fetch.fetch_bytes(origin_url)
    sha = hashlib.sha256(data).hexdigest()
    blob = paths.BLOBS / f"{sha}{_suffix(data, origin_url)}"
    if not blob.exists():
        blob.write_bytes(data)
    # Broken invariant (section 9): the stored bytes must hash to what we fetched.
    if hashlib.sha256(blob.read_bytes()).hexdigest() != sha:
        raise RuntimeError(f"sha256 mismatch after freezing {origin_url}")
    return Frozen(sha256=sha, blob_path=str(blob),
                  retrieved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                  n_bytes=len(data))
