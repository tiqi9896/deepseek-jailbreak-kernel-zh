#!/usr/bin/env bash
# ============================================================
#  DeepSeek jailbreak kernel - installer (POSIX / Git Bash / WSL)
#
#  . copies the plugin into $HERMES_HOME/plugins/deepseek-jailbreak-core
#  . runs the plugin self-test  (24 assertions)
#  . runs the proxy self-test   (116 checks, real HTTP against a local stub)
#  . FAILS LOUDLY if either self-test does not pass
#
#  Usage:
#    bash install.sh                      # install the plugin
#    bash install.sh --with-proxy         # also start the proxy on :8790
#    bash install.sh --hermes-home /path  # override HERMES_HOME
#    bash install.sh --check-only         # run the self-tests only
#
#  NOTE: this script is deliberately POSIX-portable - it runs under
#        Linux, macOS, WSL and Git Bash without modification.
# ============================================================
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-}"
WITH_PROXY=0
CHECK_ONLY=0
PORT=8790

while [ $# -gt 0 ]; do
  case "$1" in
    --with-proxy) WITH_PROXY=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    --hermes-home) HERMES_HOME="${2:-}"; shift ;;
    --port) PORT="${2:-8790}"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# ---- locate a python interpreter -----------------------------------------
#  A candidate is only accepted if it really runs and is >= 3.10.
#  (On Windows the "python3" name is sometimes a Store/WSL stub that prints
#   an error and exits non-zero.)
PY=""
for c in python3 python py; do
  command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "ERROR: no usable python 3.10+ found (tried python3 / python / py)" >&2
  exit 1
fi
echo "python: $PY ($("$PY" --version 2>&1))"

# ---- locate the Hermes home ----------------------------------------------
if [ -z "$HERMES_HOME" ]; then
  for cand in \
      "${HOME:-}/.hermes" \
      "${HOME:-}/hermes" \
      "${LOCALAPPDATA:-/nonexistent}/hermes" \
      "${APPDATA:-/nonexistent}/hermes" \
      "${HOME:-/nonexistent}/AppData/Local/hermes" ; do
    if [ -f "$cand/config.yaml" ]; then HERMES_HOME="$cand"; break; fi
  done
fi
if [ -z "$HERMES_HOME" ] || [ ! -d "$HERMES_HOME" ]; then
  echo "ERROR: Hermes home not found." >&2
  echo "       Pass it explicitly:  bash install.sh --hermes-home /path/to/hermes" >&2
  echo "       (a directory containing config.yaml)" >&2
  exit 1
fi
echo "HERMES_HOME: $HERMES_HOME"

PLUGIN_DIR="$HERMES_HOME/plugins/deepseek-jailbreak-core"

# ---- install the plugin ---------------------------------------------------
if [ "$CHECK_ONLY" -eq 0 ]; then
  mkdir -p "$HERMES_HOME/plugins"
  if [ -d "$PLUGIN_DIR" ]; then
    BAK="$PLUGIN_DIR.bak.$(date +%Y%m%d%H%M%S)"
    echo "existing plugin found - backing it up to $BAK"
    mv "$PLUGIN_DIR" "$BAK"
  fi
  cp -r "$HERE/plugin" "$PLUGIN_DIR"
  echo "installed plugin -> $PLUGIN_DIR"
else
  echo "--check-only: not installing"
fi

# ---- self-test 1: the plugin (offline) ------------------------------------
echo
echo "=== self-test 1/2: plugin (offline) ==="
RC1=1
if [ -f "$PLUGIN_DIR/selftest.py" ]; then
  "$PY" "$PLUGIN_DIR/selftest.py"
  RC1=$?
else
  echo "SKIP: plugin selftest not found at $PLUGIN_DIR/selftest.py"
fi

# ---- self-test 2: the proxy (real HTTP, local stub upstream) --------------
echo
echo "=== self-test 2/2: proxy (real HTTP against a local stub) ==="
"$PY" "$HERE/proxy/proxy.py" --self-test
RC2=$?

# ---- verdict --------------------------------------------------------------
echo
if [ "$RC1" -eq 0 ] && [ "$RC2" -eq 0 ]; then
  echo "INSTALL OK - both self-tests passed."
else
  echo "INSTALL FAILED - plugin selftest rc=$RC1, proxy selftest rc=$RC2" >&2
  echo "Do not report success. Fix the failures above first." >&2
  exit 1
fi

# ---- optional: start the proxy --------------------------------------------
if [ "$WITH_PROXY" -eq 1 ]; then
  echo
  echo "=== starting the proxy on :$PORT ==="
  if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
    echo "WARNING: DEEPSEEK_API_KEY is not set in this shell."
    echo "         The proxy will still start, but upstream calls will 401."
  fi
  nohup "$PY" "$HERE/proxy/proxy.py" --port "$PORT" --verbose \
      >"$HERE/proxy/proxy.log" 2>&1 &
  sleep 2
  if command -v curl >/dev/null 2>&1; then
    curl -s "http://127.0.0.1:$PORT/health" && echo
  fi
  echo "Next:  python tests/proxy_smoke.py --port $PORT"
  echo "Then:  point the agent's base_url at http://127.0.0.1:$PORT"
fi

cat <<'EOF'

Next steps
----------
1. Reload the plugin without restarting existing sessions:
     hermes plugins disable deepseek-jailbreak-core
     hermes plugins enable deepseek-jailbreak-core
2. Verify coverage:
     python tools/audit-deepseek-jailbreak.py -v
3. Read the allowlist section in AGENTS.md.
   The default admits every deepseek* model id; deepseek-v4-pro is excluded.
EOF
