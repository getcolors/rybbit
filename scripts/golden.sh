#!/usr/bin/env bash
set -euo pipefail

# Green's regression net against the committed goldens: render every fixture
# and diff against committed output. scripts/parity.sh is the net across
# colours.
#
# Four fixtures: one per advertised compute provider per keypair mode, because
# the SSH Keypair Standard has two modes and a package conforms only if both
# hold on every provider, and because providers are selected by template
# directory, so a build is the only thing that proves a provider's tree renders
# at all -- a unit test over the registry passes while its template is missing.
# `colors.yml` (DigitalOcean) and `colors-vultr.yml` (Vultr) are opt-out mode:
# they supply an explicit key id and a name equal to the profile and must
# render the historical shape, byte for byte, creating no key resource. The
# Vultr opt-out tree is the shape of the live `rybbit-vultr` deployment, so a
# change there is a plan against a running machine. `keygen.yml` and
# `keygen-vultr.yml` carry no `<provider>-ssh-keys`: the compute template must
# declare the profile-named key resource and reference it by attribute.
#
# Keygen paths are rendered from a fixed placeholder home on :build, never from
# $HOME, so these goldens mean the same thing on every workstation.
#
#   ./scripts/golden.sh            check
#   ./scripts/golden.sh --accept   regenerate after an intended change

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

accept=0
[[ ${1:-} == --accept ]] && accept=1

key_resource() {
  case $1 in
    digitalocean) echo 'resource "digitalocean_ssh_key" "machine"' ;;
    vultr) echo 'resource "vultr_ssh_key" "machine"' ;;
  esac
}

status=0
for variant in colors colors-vultr keygen keygen-vultr; do
  fixture="$tmp/$variant.yml"
  sed "s#WORKDIR#$tmp/work#" "$root/test/fixtures/$variant.yml" > "$fixture"
  (cd "$root/green" && RYBBIT_LIB_ROOT="$root" ./green build -f "$fixture" >/dev/null)

  profile=$(sed -n 's/^profile: //p' "$fixture")
  provider=$(sed -n 's/^provider-compute: //p' "$fixture")
  actual="$tmp/work/$profile"
  golden="$root/test/resources/golden/local/$profile"
  main="$actual/rybbit-infrastructure/main.tf"

  # No rendered artefact may carry a real secret into a committed golden.
  # Checked before --accept copies anything. POSIX grep on purpose: a missing
  # binary inside `if` is simply false, so the guard must not depend on one that
  # may be absent.
  if grep -rEq 'client-key-data|client-certificate-data|BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY|github_pat_|ghp_|gho_|ghu_|ghs_|ghr_' "$actual"; then
    echo "golden: a credential-shaped value was rendered for $profile" >&2; exit 1
  fi
  # A build that reached the real ~/.ssh would leak the operator's home into
  # committed bytes and make the goldens workstation-specific.
  if grep -rq "$HOME/.ssh" "$actual"; then
    echo "golden: $profile rendered a real home directory; build must use the placeholder" >&2; exit 1
  fi
  # The converge play takes its address from the inventory at run time, so
  # the rendered play itself must carry no dotted quad — a literal address in
  # it would be a workstation- or deployment-specific byte in a golden.
  if grep -Eq '([0-9]{1,3}\.){3}[0-9]{1,3}' "$actual/rybbit-ansible/main.yml"; then
    echo "golden: $profile rendered an address into the converge play" >&2; exit 1
  fi
  # SSH Keypair Standard §4.3: in keygen mode the template declares the
  # profile-named account key and references it by attribute, never by a
  # literal id; in opt-out mode it creates nothing and keeps the literal.
  if [[ $variant == keygen* ]]; then
    grep -q "$(key_resource "$provider")" "$main" ||
      { echo "golden: $profile (keygen) declares no ${provider} key resource" >&2; exit 1; }
    grep -q "name *= \"$profile\"" "$main" ||
      { echo "golden: $profile (keygen) key resource is not named after the profile" >&2; exit 1; }
    grep -Eq "ssh_key(_id)?s = \[${provider}_ssh_key\.machine\.id\]" "$main" ||
      { echo "golden: $profile (keygen) machine does not reference the key by attribute" >&2; exit 1; }
    grep -q "ssh_key_id = ${provider}_ssh_key.machine.id" "$main" ||
      { echo "golden: $profile (keygen) params carry no ssh_key_id" >&2; exit 1; }
  else
    if grep -q '_ssh_key" "machine"' "$main"; then
      echo "golden: $profile (opt-out) must not declare a key resource" >&2; exit 1
    fi
    grep -Eq 'ssh_key(_id)?s = \["[^"]+"\]' "$main" ||
      { echo "golden: $profile (opt-out) must keep the literal key id" >&2; exit 1; }
    if grep -q 'ssh_key_id = ' "$main"; then
      echo "golden: $profile (opt-out) params must carry no ssh_key_id" >&2; exit 1
    fi
  fi

  if [[ $accept == 1 ]]; then
    rm -rf "$golden"; mkdir -p "$(dirname "$golden")"; cp -a "$actual" "$golden"; continue
  fi
  [[ -d "$golden" ]] || { echo "golden missing for $profile; inspect build then run bb golden:accept" >&2; exit 1; }
  diff -ru "$golden" "$actual" || status=1
done

exit "$status"
