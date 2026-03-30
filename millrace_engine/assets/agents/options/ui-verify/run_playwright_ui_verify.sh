#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
run_playwright_ui_verify.sh --out <OUT_DIR> [--spec <SPEC_PATH>] [--coverage <smoke|standard|broad>] [--cmd <COMMAND>] [--update-latest]

Purpose:
- Best-effort deterministic UI verification runner scaffold.
- Creates an artifact bundle (result.json + report.md + evidence/meta folders).

Notes:
- This script does not install Playwright for you.
- If Playwright (or required tooling) is missing, it writes status=BLOCKED with a precise error.
- If a Playwright command runs and exits non-zero, it writes status=FAIL.

Recommended usage in Millrace:
- Provide a project-specific Playwright test suite and invoke it via --cmd.
EOF
}

SPEC_PATH="agents/ui_verification_spec.yaml"
OUT_DIR=""
COVERAGE=""
CMD=""
UPDATE_LATEST="false"

while [ $# -gt 0 ]; do
  case "$1" in
    --spec) SPEC_PATH="${2:-}"; shift 2 ;;
    --out) OUT_DIR="${2:-}"; shift 2 ;;
    --coverage) COVERAGE="${2:-}"; shift 2 ;;
    --cmd) CMD="${2:-}"; shift 2 ;;
    --update-latest) UPDATE_LATEST="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$OUT_DIR" ]; then
  echo "Missing --out <OUT_DIR>" >&2
  usage
  exit 2
fi

mkdir -p "$OUT_DIR/evidence" "$OUT_DIR/meta"

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STATUS="BLOCKED"
EXECUTOR="playwright"
ANALYZER="none"

ERROR_TYPE=""
ERROR_MESSAGE=""

if [ -f "$SPEC_PATH" ]; then
  cp -f "$SPEC_PATH" "$OUT_DIR/meta/spec_resolved.yaml" 2>/dev/null || true
fi

if ! command -v node >/dev/null 2>&1; then
  ERROR_TYPE="ENV"
  ERROR_MESSAGE="node is not installed or not on PATH"
elif ! command -v npx >/dev/null 2>&1; then
  ERROR_TYPE="ENV"
  ERROR_MESSAGE="npx is not installed or not on PATH"
else
  if ! npx playwright --version >/dev/null 2>&1; then
    ERROR_TYPE="ENV"
    ERROR_MESSAGE="Playwright is not installed (try adding it to the project and rerun)"
  else
    set +e
    if [ -n "$CMD" ]; then
      bash -lc "$CMD"
      RC=$?
    else
      # Default: run the project's configured Playwright suite.
      npx playwright test
      RC=$?
    fi
    set -e

    if [ "$RC" -eq 0 ]; then
      STATUS="PASS"
    else
      STATUS="FAIL"
      ERROR_TYPE="ASSERTION"
      ERROR_MESSAGE="Playwright exited non-zero (rc=$RC)"
    fi
  fi
fi

ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

export UI_VERIFY_STATUS="$STATUS"
export UI_VERIFY_EXECUTOR="$EXECUTOR"
export UI_VERIFY_ANALYZER="$ANALYZER"
export UI_VERIFY_COVERAGE="$COVERAGE"
export UI_VERIFY_STARTED_AT="$STARTED_AT"
export UI_VERIFY_ENDED_AT="$ENDED_AT"
export UI_VERIFY_EVIDENCE_DIR="$OUT_DIR/evidence"
export UI_VERIFY_ERROR_TYPE="$ERROR_TYPE"
export UI_VERIFY_ERROR_MESSAGE="$ERROR_MESSAGE"

python3 - "$OUT_DIR/result.json" <<PY
import json
import os
import sys

out_path = sys.argv[1]

data = {
  "status": os.environ.get("UI_VERIFY_STATUS", ""),
  "executor": os.environ.get("UI_VERIFY_EXECUTOR", ""),
  "analyzer": os.environ.get("UI_VERIFY_ANALYZER", ""),
  "coverage": os.environ.get("UI_VERIFY_COVERAGE", ""),
  "started_at": os.environ.get("UI_VERIFY_STARTED_AT", ""),
  "ended_at": os.environ.get("UI_VERIFY_ENDED_AT", ""),
  "evidence_dir": os.environ.get("UI_VERIFY_EVIDENCE_DIR", ""),
  "checks": [],
  "errors": [],
  "quota": None,
}

err_type = (os.environ.get("UI_VERIFY_ERROR_TYPE") or "").strip()
err_msg = (os.environ.get("UI_VERIFY_ERROR_MESSAGE") or "").strip()
if err_type and err_msg:
  data["errors"].append({"type": err_type, "message": err_msg})

with open(out_path, "w", encoding="utf-8") as f:
  json.dump(data, f, indent=2, sort_keys=True)
  f.write("\n")
PY

cat >"$OUT_DIR/report.md" <<EOF
# UI Verification Report

UI_VERIFY: $STATUS

- executor: $EXECUTOR
- analyzer: $ANALYZER
- coverage: ${COVERAGE:-""}
- started_at: $STARTED_AT
- ended_at: $ENDED_AT
- evidence_dir: $OUT_DIR/evidence

## Notes

- This is a deterministic runner scaffold. Provide a project-specific Playwright suite and invoke it via \`--cmd\` for meaningful coverage.
- Spec (if present): \`$SPEC_PATH\` (copied to \`meta/spec_resolved.yaml\`).
EOF

if [ "$UPDATE_LATEST" = "true" ] && [ -d "agents" ]; then
  python3 - "$OUT_DIR" <<'PY'
import json
import shutil
import sys
import time
from pathlib import Path

current_out = Path(sys.argv[1]).resolve()
latest_result = Path("agents/ui_verification_result.json")
root = Path("agents/diagnostics/ui_verify").resolve()
archived_root = root / "archived"

if not latest_result.exists():
    raise SystemExit(0)

try:
    payload = json.loads(latest_result.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)

evidence_dir = payload.get("evidence_dir")
if not isinstance(evidence_dir, str) or not evidence_dir.strip():
    raise SystemExit(0)

previous_bundle = Path(evidence_dir).resolve().parent
if previous_bundle == current_out:
    raise SystemExit(0)
if not previous_bundle.exists():
    raise SystemExit(0)

try:
    previous_bundle.relative_to(root)
except ValueError:
    raise SystemExit(0)

archived_root.mkdir(parents=True, exist_ok=True)
dest = archived_root / previous_bundle.name
if dest.exists():
    dest = archived_root / f"{previous_bundle.name}-{int(time.time())}"
shutil.move(str(previous_bundle), str(dest))
PY
  cp -f "$OUT_DIR/result.json" "agents/ui_verification_result.json" 2>/dev/null || true
  cp -f "$OUT_DIR/report.md" "agents/ui_verification_report.md" 2>/dev/null || true
fi

case "$STATUS" in
  PASS) exit 0 ;;
  FAIL) exit 1 ;;
  BLOCKED) exit 2 ;;
  *) exit 3 ;;
esac
