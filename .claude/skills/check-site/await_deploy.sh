#!/usr/bin/env bash
# Wait for a GitHub Pages redeploy to actually go live, then run the site check.
#
# Bounded on purpose: instead of polling forever (the old inline loop did, and
# silently spun for minutes while the deploy had in fact FAILED), it gives up
# after a timeout with a concrete diagnostic. The usual cause of a deploy that
# never lands is the `github-pages` environment's deployment-branch policy
# blocking the branch you pushed -- see CLAUDE.md -> "Deploy facts".
#
# Usage:  await_deploy.sh [MARKER]
#   MARKER  a string that only the NEW build serves in common.js (default
#           "loadMetrics"). Env: SITE (base url), TIMEOUT (s), INTERVAL (s).
set -o pipefail
SITE="${SITE:-http://amid.fish/safety-dashboard}"
MARKER="${1:-loadMetrics}"
TIMEOUT="${TIMEOUT:-300}"
INTERVAL="${INTERVAL:-15}"
here="$(cd "$(dirname "$0")" && pwd)"
deadline=$(( $(date +%s) + TIMEOUT ))

while [ "$(date +%s)" -lt "$deadline" ]; do
  if curl -fsSL --max-time 15 -H 'Cache-Control: no-cache' "$SITE/common.js" 2>/dev/null | grep -q -- "$MARKER"; then
    echo "[$(date +%H:%M:%S)] LIVE: '$MARKER' is being served — running site check"
    exec python3 "$here/check_site.py" "$SITE/"
  fi
  echo "[$(date +%H:%M:%S)] not live yet ('$MARKER' absent); $(( deadline - $(date +%s) ))s left"
  sleep "$INTERVAL"
done

cat >&2 <<EOF

TIMEOUT after ${TIMEOUT}s — '${MARKER}' never appeared at ${SITE}/common.js.
The deploy did NOT land. Most likely it FAILED rather than ran slowly:
  • Actions tab → newest 'deploy-pages' run. A ~2s failure with zero steps
    executed == the branch was blocked by environment protection rules.
  • Settings → Environments → github-pages → "Deployment branches and tags":
    allow 'main' (see CLAUDE.md), or deploy from the already-allowed branch.
Other possibilities: the run is genuinely still building, or CDN cache lag —
re-run this script if the Actions run shows success.
EOF
exit 1
