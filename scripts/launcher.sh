#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
launcher="$root/skills/package-rybbit-green/green"
grep -q 'io.github.getcolors.rybbit.workflow/workflow' "$launcher"
grep -q 'def \^:private rybbit-sha' "$launcher"
[[ -L "$root/green" ]] && [[ $(readlink "$root/green") == skills/package-rybbit-green/green ]]
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
cp "$launcher" "$tmp/green"; chmod +x "$tmp/green"
sed "s#WORKDIR#.colors#" "$root/test/fixtures/colors.yml" > "$tmp/colors.yml"
(cd "$tmp" && RYBBIT_LIB_ROOT="$root" ./green build >/dev/null)
[[ -f "$tmp/.colors/rybbit-fixture/rybbit-infrastructure/main.tf" ]]
mkdir -p "$tmp/nested/path"
(cd "$tmp/nested/path" && RYBBIT_LIB_ROOT="$root" ../../green build >/dev/null)
out=$(cd "$tmp" && RYBBIT_LIB_ROOT="$root" COLORS_PAR_PROFILE=wrong ./green build 2>&1 || true)
grep -q COLORS_PAR_PROFILE <<<"$out"
[[ ! -d "$tmp/.colors/wrong" ]]
echo 'launcher: all checks passed'
