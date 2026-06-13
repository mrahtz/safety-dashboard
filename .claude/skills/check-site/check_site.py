#!/usr/bin/env python3
"""Headless smoke-check of the live dashboard. Loads each page, watches for
console errors, uncaught exceptions, failed network requests, and visible
"load failed" states, screenshots each page, and exits non-zero if anything
looks wrong. Also does an authenticated check of review.html via a Supabase
Admin magic link (reads var/supabase.env, or the SUPABASE_* env vars).

Usage:  python3 .claude/skills/check-site/check_site.py [BASE_URL]
        BASE_URL defaults to the production custom domain.
"""
import glob
import json
import os
import pathlib
import sys
import urllib.request

from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://amid.fish/safety-dashboard/").rstrip("/") + "/"
PAGES = ["index.html", "sources.html", "review.html"]
OUT = "/tmp/site-check"

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent
REVIEWER_EMAIL = "matthew.rahtz@gmail.com"


def find_browser() -> str | None:
    for pat in ("/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell",
                "/opt/pw-browsers/chromium-*/chrome-linux/chrome"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None  # let Playwright use its default


def load_supabase_env() -> dict[str, str]:
    """Supabase creds for the authenticated review check. Reads var/supabase.env
    if present, then falls back to the environment (the remote Claude Code env
    ships SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY directly), so the check
    works in a fresh container with no var/supabase.env written yet."""
    result = {}
    env_file = REPO_ROOT / "var" / "supabase.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    # Fill any gaps from the environment. SUPABASE_SERVICE_ROLE_KEY is the name
    # used in the remote env; map it onto the SUPABASE_SERVICE_ROLE this script
    # (and var/supabase.env) expects.
    result.setdefault("SUPABASE_URL", os.environ.get("SUPABASE_URL", "").strip())
    result.setdefault("SUPABASE_SERVICE_ROLE",
                      (os.environ.get("SUPABASE_SERVICE_ROLE")
                       or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")).strip())
    return {k: v for k, v in result.items() if v}


def check_review_authenticated(ctx, base_url: str) -> list[tuple[str, str]]:
    env = load_supabase_env()
    if not env.get("SUPABASE_URL") or not env.get("SUPABASE_SERVICE_ROLE"):
        print("  [review authenticated] skipped — var/supabase.env missing or incomplete")
        return []

    supabase_url = env["SUPABASE_URL"]
    service_role = env["SUPABASE_SERVICE_ROLE"]
    issues: list[tuple[str, str]] = []
    label = "review.html (authenticated)"

    # Generate magic link via Admin API (no email delivery — returns action_link directly)
    body = json.dumps({"type": "magiclink", "email": REVIEWER_EMAIL}).encode()
    req = urllib.request.Request(
        f"{supabase_url}/auth/v1/admin/generate_link",
        data=body,
        headers={
            "Authorization": f"Bearer {service_role}",
            "apikey": service_role,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        action_link = data["action_link"]
    except Exception as e:
        return [(label, f"magic link generation failed: {e}")]

    # Navigate to action_link — Supabase redirects to review.html#access_token=...
    page = ctx.new_page()
    cons: list[str] = []
    perr: list[str] = []
    bad: list[str] = []
    page.on("console", lambda m, L=cons: L.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e, L=perr: L.append(str(e)))
    page.on("response", lambda r, L=bad: L.append(f"{r.status} {r.url}")
            if r.status >= 400 and "favicon" not in r.url else None)
    try:
        page.goto(action_link, wait_until="networkidle", timeout=30000)
    except Exception as e:
        return [(label, f"navigation failed: {e}")]

    # Wait for #app to become visible (boot() → render() completes)
    try:
        page.wait_for_function(
            "document.getElementById('app') && document.getElementById('app').style.display !== 'none'",
            timeout=10000,
        )
    except Exception:
        issues.append((label, "#app never became visible — auth may have failed"))

    page.wait_for_timeout(1500)

    body_text = page.inner_text("body")
    low = body_text.lower()
    if "page unknown" in low:
        issues.append((label, "visible 'Page unknown' — page_num is null for one or more groups"))
    if "load failed" in low or "no cells" in low or "no data" in low:
        snippet = next((ln.strip() for ln in body_text.splitlines()
                        if any(k in ln.lower() for k in ("load failed", "no cells", "no data"))), "")
        issues.append((label, f"visible error/empty state: {snippet[:160]}"))
    if not page.query_selector_all(".grp"):
        issues.append((label, "no .grp elements — review content did not render"))

    page.screenshot(path=f"{OUT}/review-authenticated.png", full_page=True)

    for msg in cons:
        issues.append((label, f"console error: {msg[:200]}"))
    for pe in perr:
        issues.append((label, f"uncaught exception: {pe[:200]}"))
    for r in bad:
        issues.append((label, f"failed request: {r[:200]}"))
    page.close()
    return issues


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

        issues += check_review_authenticated(ctx, BASE)
        browser.close()

    print(f"Checked {BASE} ({len(PAGES)} pages + authenticated review) — screenshots in {OUT}/\n")
    if not issues:
        print("OK: no issues found.")
        return 0
    print(f"FOUND {len(issues)} issue(s):")
    for pg, msg in issues:
        print(f"  [{pg}] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(check())
