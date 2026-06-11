#!/usr/bin/env python3
"""Headless smoke-check of the live dashboard. Loads each page, watches for
console errors, uncaught exceptions, failed network requests, and visible
"load failed" states, screenshots each page, and exits non-zero if anything
looks wrong.

Usage:  python3 .claude/skills/check-site/check_site.py [BASE_URL]
        BASE_URL defaults to the production custom domain.
"""
import glob
import pathlib
import sys

from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://amid.fish/safety-dashboard/").rstrip("/") + "/"
PAGES = ["index.html", "sources.html", "review.html"]
OUT = "/tmp/site-check"


def find_browser() -> str | None:
    for pat in ("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell",
                "/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None  # let Playwright use its default


def check() -> int:
    pathlib.Path(OUT).mkdir(parents=True, exist_ok=True)
    issues: list[tuple[str, str]] = []
    with sync_playwright() as p:
        # --ignore-certificate-errors + ignore_https_errors so a TLS-intercepting
        # proxy (e.g. a sandboxed runner) doesn't mask the real page behaviour.
        browser = p.chromium.launch(executable_path=find_browser(),
                                    args=["--ignore-certificate-errors"])
        ctx = browser.new_context(viewport={"width": 1400, "height": 900},
                                  ignore_https_errors=True)
        for name in PAGES:
            url = BASE + name
            page = ctx.new_page()
            cons: list[str] = []
            perr: list[str] = []
            bad: list[str] = []
            page.on("console", lambda m, L=cons: L.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e, L=perr: L.append(str(e)))
            # flag real 4xx/5xx (Supabase, JS, CSS, images). Ignore favicon noise.
            page.on("response", lambda r, L=bad: L.append(f"{r.status} {r.url}")
                    if r.status >= 400 and "favicon" not in r.url else None)
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                issues.append((name, f"navigation failed: {type(e).__name__}: {str(e)[:160]}"))
            page.wait_for_timeout(2500)  # let async Supabase loads settle
            body = page.inner_text("body")
            low = body.lower()
            if "load failed" in low or "no cells" in low or "no data" in low:
                snippet = next((ln.strip() for ln in body.splitlines()
                                if any(k in ln.lower() for k in ("load failed", "no cells", "no data"))), "")
                issues.append((name, f"visible error/empty state: {snippet[:160]}"))
            page.screenshot(path=f"{OUT}/{name.replace('.html', '')}.png", full_page=True)
            for msg in cons:
                issues.append((name, f"console error: {msg[:200]}"))
            for pe in perr:
                issues.append((name, f"uncaught exception: {pe[:200]}"))
            for r in bad:
                issues.append((name, f"failed request: {r[:200]}"))
            page.close()
        browser.close()

    print(f"Checked {BASE} ({len(PAGES)} pages) — screenshots in {OUT}/\n")
    if not issues:
        print("OK: no issues found.")
        return 0
    print(f"FOUND {len(issues)} issue(s):")
    for pg, msg in issues:
        print(f"  [{pg}] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(check())
