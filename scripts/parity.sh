#!/usr/bin/env bash
set -euo pipefail

# One desired state, three colours, byte for byte. golden.sh is green's
# regression net against the committed goldens; this is the net across colours:
# each fixture is rendered by green, red, and blue into separate work
# directories and the trees must be identical — and the template trees each
# colour carries must be identical too, because the copies are the mechanism
# (red/resources and blue's embedded resources are copies of green's tree, not
# references to it).
#
# Four v2 fixtures cover Google, separate provider registration, R2/S3
# backends and empty HTTP ingress; provider matrices remain library-owned.
#
# Renders resolve each colour's package from this working tree (the
# RYBBIT_LIB_ROOT overrides), while green, once, red, and blue stay on their
# pins — a change that lands here passes parity before it is pushed or pinned
# anywhere.

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

build_variant() {
  local variant=$1
  for colour in green red blue; do
    sed "s#WORKDIR#$tmp/$variant/$colour#" "$root/test/fixtures/$variant.yml" \
      > "$tmp/$variant-$colour.yml"
  done
  (cd "$root/green" && RYBBIT_LIB_ROOT="$root" ./green build -f "$tmp/$variant-green.yml" >/dev/null)
  (cd "$root/red" && RYBBIT_LIB_ROOT="$root/red" ./red build -f "$tmp/$variant-red.yml" >/dev/null)
  (cd "$root/blue" && uv run python -m package_rybbit_blue build -f "$tmp/$variant-blue.yml" >/dev/null)
  diff -r "$tmp/$variant/green" "$tmp/$variant/red"
  diff -r "$tmp/$variant/green" "$tmp/$variant/blue"
  (cd "$root/green" && bb "$root/scripts/runtime-green.clj" "$root/test/fixtures/$variant.yml") > "$tmp/$variant-green-runtime.json"
  (cd "$root/red" && bun "$root/scripts/runtime-red.ts" "$root/test/fixtures/$variant.yml") > "$tmp/$variant-red-runtime.json"
  (cd "$root/blue" && uv run python "$root/scripts/runtime-blue.py" "$root/test/fixtures/$variant.yml") > "$tmp/$variant-blue-runtime.json"
  diff -u "$tmp/$variant-green-runtime.json" "$tmp/$variant-red-runtime.json"
  diff -u "$tmp/$variant-green-runtime.json" "$tmp/$variant-blue-runtime.json"
}

build_variant colors
build_variant colors-vultr
build_variant keygen
build_variant keygen-vultr

diff -r "$root/green/src/resources/io/github/getcolors/rybbit" "$root/red/resources"
diff -r "$root/green/src/resources/io/github/getcolors/rybbit" "$root/blue/src/package_rybbit_blue/resources"

echo "green, red, and blue Rybbit artifacts and runtime contracts are byte-identical"
