#!/usr/bin/env bash
# Wait for a GitHub Pages redeploy to actually go live, then run the site check.
#
# The live domain (amid.fish) is proxied by Cloudflare, which edge-caches *.js
# for hours but passes HTML through (cf-cache-status DYNAMIC). So we detect a
# deploy by watching the *uncached* index.html for the cache-bust version it
# references (web/common.js?v=NNN) -- that flips the instant the new HTML lands,
# and the matching versioned asset is a fresh cache key, so a real browser load
# works immediately too.
#
# Bounded on purpose: instead of polling forever (the old loop did, while the
# deploy had in fact FAILED), it gives up with a concrete diagnostic. The usual
# causes are the github-pages environment blocking the branch, or a build
# failure -- see CLAUDE.md -> "Deploy facts".
#
# Usage:  await_deploy.sh [VERSION]
#   VERSION  the cache-bust token to wait for (default: read from the local
#            web/index.html we just shipped). Env: SITE, TIMEOUT(s), INTERVAL(s).
set -o pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
SITE="${SITE:-http://amid.fish/safety-dashboard}"
# -a: the HTML has multibyte chars (×) that make grep think it's binary
VERSION="${1:-$(grep -aoE 'common\.js\?v=[A-Za-z0-9._-]+' "$repo/web/index.html" | head -1 | sed 's/.*v=//')}"
TIMEOUT="${TIMEOUT:-300}"
INTERVAL="${INTERVAL:-15}"
deadline=$(( $(date +%s) + TIMEOUT ))

if [ -z "$VERSION" ]; then
  echo "warn: no ?v= cache-bust token in web/index.html; falling back to a marker check" >&2
fi

while [ "$(date +%s)" -lt "$deadline" ]; do
  # cache-bust the HTML fetch itself so we never read a stale edge copy of it
  html="$(curl -fsSL --max-time 15 -H 'Cache-Control: no-cache' "$SITE/index.html?cb=$(date +%s)" 2>/dev/null)"
  if { [ -n "$VERSION" ] && printf '%s' "$html" | grep -aq "common.js?v=$VERSION"; } \
     || { [ -z "$VERSION" ] && printf '%s' "$html" | grep -aq "loadMetrics"; }; then
    echo "[$(date +%H:%M:%S)] LIVE: deploy ${VERSION:+(v=$VERSION) }landed — running site check"
    exec python3 "$here/check_site.py" "$SITE/"
  fi
  echo "[$(date +%H:%M:%S)] not live yet${VERSION:+ (waiting for v=$VERSION)}; $(( deadline - $(date +%s) ))s left"
  sleep "$INTERVAL"
done

cat >&2 <<EOF

TIMEOUT after ${TIMEOUT}s — index.html never started referencing ${VERSION:+v=$VERSION}.
The deploy did NOT land. Most likely it FAILED rather than ran slowly:
  • Actions tab → newest 'deploy-pages' run. A ~2s failure with zero steps
    executed == the branch was blocked by environment protection rules.
  • Settings → Environments → github-pages → "Deployment branches and tags".
  • Or the build genuinely errored — open the run logs.
(Asset staleness is handled by the ?v= cache-bust, so this is about the deploy,
not Cloudflare. See CLAUDE.md → Deploy facts.)
EOF
exit 1
