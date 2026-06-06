"""Fetch a source's raw bytes. Used by the PDF extractor (P2) and the
content-addressed freezer (P3). stdlib only -- the managed env proxies TLS, so
we rely on the system trust store that ``urllib`` already uses successfully.
"""

import pathlib
import urllib.request

_UA = "safety-dashboard/0.0 (+provenance pipeline)"


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def local_copy(source: str, cache_dir: pathlib.Path) -> pathlib.Path:
    """Return a local path for ``source`` (download http(s) sources, else passthrough)."""
    if not source.startswith(("http://", "https://")):
        p = pathlib.Path(source)
        if not p.exists():
            raise FileNotFoundError(f"source not found: {source}")
        return p
    cache_dir.mkdir(parents=True, exist_ok=True)
    import hashlib
    name = hashlib.sha256(source.encode()).hexdigest()[:16] + pathlib.Path(source).suffix
    dest = cache_dir / name
    if not dest.exists():
        dest.write_bytes(fetch_bytes(source))
    return dest
