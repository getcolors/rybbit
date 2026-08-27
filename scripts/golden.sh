#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

# One fixture per compute provider. Providers are selected by template
# directory, so a build is the only thing that proves a provider's tree renders
# at all -- a unit test over the registry passes while its template is missing.
fixtures=(colors.yml colors-vultr.yml)
profiles=(rybbit-fixture rybbit-vultr-fixture)

status=0
for i in "${!fixtures[@]}"; do
  name=${fixtures[$i]}
  profile=${profiles[$i]}
  fixture="$tmp/$name"
  sed "s#WORKDIR#$tmp/work#" "$root/test/fixtures/$name" > "$fixture"
  (cd "$root/green" && RYBBIT_LIB_ROOT="$root" ./green build -f "$fixture" >/dev/null)
  actual="$tmp/work/$profile"
  golden="$root/test/resources/golden/local/$profile"

  # No rendered artefact may carry a real secret into a committed golden.
  # Checked before --accept copies anything. POSIX grep on purpose: a missing
  # binary inside `if` is simply false, so the guard must not depend on one that
  # may be absent.
  if grep -rEq 'client-key-data|client-certificate-data|BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY|github_pat_|ghp_|gho_|ghu_|ghs_|ghr_' "$actual"; then
    echo "golden: a credential-shaped value was rendered for $profile" >&2; exit 1
  fi

  if [[ ${1:-} == --accept ]]; then
    rm -rf "$golden"; mkdir -p "$(dirname "$golden")"; cp -a "$actual" "$golden"
    continue
  fi
  [[ -d "$golden" ]] || { echo "golden missing for $profile; inspect build then run bb golden:accept" >&2; exit 1; }
  diff -ru "$golden" "$actual" || status=1
done
exit "$status"
