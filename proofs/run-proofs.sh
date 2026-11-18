#!/usr/bin/env bash
# Check the OCS proofs. Tool paths come from the environment, never from this file.
#
#   LEAN   Lean 4 binary (default: `lean` on PATH). Core Lean only, no Mathlib.
#
# There is no TLA+ model: the publish gate (incomplete_attempt_reasons) is a pure
# predicate over a finished summary, not a sequence of states, so it is
# property-tested in test_ocs_invariants.py instead.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
lean="${LEAN:-lean}"

if ! command -v "$lean" >/dev/null 2>&1; then
  echo "lean not found; set LEAN to a Lean 4 binary" >&2
  exit 127
fi

"$lean" --version
out="$("$lean" "$here/lean/OcsInvariants.lean" 2>&1)" || { printf '%s\n' "$out" >&2; exit 1; }
printf '%s\n' "$out"

if grep -q "sorryAx\|declaration uses 'sorry'" <<<"$out"; then
  echo "FAIL: a theorem depends on sorry" >&2
  exit 1
fi
count="$(grep -c "^theorem " "$here/lean/OcsInvariants.lean")"
audited="$(grep -c "depends on axioms\|does not depend on any axioms" <<<"$out")"
if [ "$count" != "$audited" ]; then
  echo "FAIL: $count theorems but $audited axiom audits" >&2
  exit 1
fi
echo "OK: $count theorems checked, no sorry"
