# CLAUDE.md

## Repository

`rybbit` is a tri-colour Package Skill (green, red, blue) for a
production-oriented, single-node Rybbit analytics deployment on one
DigitalOcean Droplet or Vultr instance. On DigitalOcean, OpenTofu discovers
the configured region's default VPC at runtime and manages the Droplet and
firewall; on Vultr it manages the instance and a generated per-CIDR firewall
group. Either way it manages the Cloudflare apex record, and Ansible converges
a private Docker Compose stack. Only Caddy ports 80/443 (plus UDP 443 for
HTTP/3) and key-only SSH are public. PostgreSQL, ClickHouse, Redis, and
internal Rybbit application ports remain on the private Compose network.

The Rybbit stack pairs PostgreSQL 17 for relational metadata/authentication with
ClickHouse 24.8 for high-throughput columnar analytics. Persistent data lives
under `/var/lib/rybbit`; a systemd timer takes regular database backups to R2.

## Layout and commands

The three implementations live in the tri-colour layout, matching `netbird`:
canonical Clojure in `green/` (`green/bb.edn`, `green/deps.edn`, `green/src/`,
`green/tasks/`, tests under `green/test/clj`), TypeScript/Bun in `red/`, and
Python/uv in `blue/`. Each colour has five namespaces: `validate` (the
registry, the spec and the package's own checks), `ssh` (the keypair, wrapping
ONCE's), `ssh-config` (the `~/.ssh/config` block's alias, markers and the two
local refusals — this package's own, not ONCE's), `tools` (the stages and
acceptance) and `workflow` (the graph and `start-step`); red also carries
`once.ts`, the path-resolution shim for ONCE's unexported `ssh.ts`. The
templates live under `tools/infrastructure/<provider>/`, `tools/dns/`,
`tools/ansible/` (the converge) and `tools/ansible-local/` (the three-file
local stage that writes the `~/.ssh/config` block). Green is canonical: a
behavioural change lands in all three colours in the same commit and passes
`scripts/parity.sh`. The fixtures
and the goldens are shared across colours at the repository root —
`test/fixtures/` and `test/resources/golden/` — with `green/test/fixtures` and
`green/test/resources` symlinks pointing at them. Each colour dir holds a
launcher symlink to its skill payload (`green/green`, `red/red`, `blue/blue`).

```sh
cd green && bb test
cd green && bb golden
cd green && bb golden:accept   # regenerate after an intended change — read the diff first
cd red && bun test && bun run typecheck
cd blue && uv run pytest
./scripts/parity.sh            # three colours, two providers, two keypair modes, byte for byte
./scripts/launcher.sh          # from the repository root
cd green && ./green build
cd green && ./green create --dry-run
cd green && ./green create     # requires explicit authorization
cd green && ./green delete     # guarded and destructive
```

Never read `.envrc.private`, edit `.colors/`, export `COLORS_PAR_PROFILE`, or
weaken `compute-prevent-destroy`. Build and dry-run are credential-free. A real
create/delete requires explicit authorization.

## The Compute Provider Standard, and what is delegated

The package conforms to the workspace Compute Provider Standard
(`workspace/standards/compute-provider.md`) by **delegation**: the operations
— the `:provider-compute must be one of` refusal, the required keys, secrets
and OpenTofu environment of the selected entry, the CIDR grammar and the
source rules, the per-provider checks (name rules, Vultr's numeric `os-id`,
DigitalOcean's VPC refusal, which fires only when DigitalOcean is selected),
the provider-switch and legacy-state refusals, the one up-front state read,
`fallback-params`, `resolved-compute` and `adopt-state` — live in ONCE's
`compute` namespace, called with `validate/spec`. What stays here is the data
and the wiring: the two-entry registry, the default provider, the `:sources`
map, the templates, `state-output`, `start-step`, and the graph. The
three-colour matrix of those operations is tested in ONCE; this package's
tests keep one wiring test per safety boundary and one spec-content test per
colour. `COLORS_PAR_IP` survives as a local wrapper around `compute/adopt-state`
in posthog's shape: it replaces the recorded address only after a successful
state read and never skips the read — an unreadable backend fails a real
delete closed with ONCE's wording whether or not it is set.

**The default provider is Vultr, not DigitalOcean.** The spec's default is
what a legacy state — `params` without `provider` — is taken to be, and the
only legacy state this package has is the live `rybbit-vultr` deployment
(rybbit.getcolors.ai). A DigitalOcean default would make the standard's legacy
rule refuse every real create and delete on it until it was rebuilt. Every
fixture and deployment sets `provider-compute` explicitly, so the default
changes no rendering; `workflow` reads it from `validate/default-compute-provider`.

The package also adopts keygen mode of the SSH Keypair Standard on both
providers: `<provider>-ssh-keys` and `<provider>-name` are optional, absence of
the key means the deployment generates and owns `~/.ssh/<profile>` (ONCE's
`ssh`, wrapped by `rybbit.ssh` with a build-time placeholder home), the compute
templates carry the `<% if ssh-keygen %>` branches whose opt-out side
contributes no byte, `ansible.cfg` names the private key in keygen mode, the
acceptance step's `ssh` threads `identity-args`, and the delete graph removes
the key strictly **after** the compute destroy (`:rybbit/ssh-cleanup`). The
`~/.ssh/config` block of the sibling SSH Config Standard is adopted too; it
has its own section below because its rules run the other way.

## The `~/.ssh/config` block

The package conforms to the workspace SSH Config Standard
(`../workspace/standards/ssh-config.md`) by copying its reference
implementation, `clickstack`, and it was born conforming: the marker is
`# BEGIN <profile> ANSIBLE MANAGED BLOCK` with no package prefix, so
`owned-markers` is a one-element set and no migration window exists. The
`rybbit-ansible-local` stage is one `blockinfile` task against `~/.ssh/config`,
run on `localhost` with `connection: local`, giving the operator
`ssh <profile>` instead of an address, a user and an identity file.

Two rules there are easy to undo by accident.

The play is **this package's own copy**, deliberately not shared with ONCE's,
which is the opposite choice from `ssh` above. `ssh` acts on profile-named
files only this deployment uses, so sharing it spreads fixes. The local play
writes into a file the operator shares with every host they reach, so sharing
it would let an unrelated upstream change rewrite that file at pin-bump time
(standard §7).

Address, user, alias and `block_state` arrive as **Ansible extra-vars, never
through Selmer**. That is what keeps `build` byte-identical across
workstations and keeps addresses out of the goldens; the one Selmer
conditional is the `IdentityFile`/`IdentitiesOnly` pair, rendered in keygen
mode only, because whether the package owns a key is desired state a build
does know. `scripts/golden.sh` fails if a dotted quad ever appears under
`rybbit-ansible-local`.

Create writes the block after compute and before DNS and convergence
(`:rybbit/infrastructure → :rybbit/ssh-config → :rybbit/dns`). Delete removes
it *before* the destroy, which is the reverse of the keypair: a block that
outlives its host is stale but harmless, while a key removed early locks you
out of a machine that still exists. The two orders disagree on purpose and
must not be tidied into agreement.

The block is inserted with `insertbefore: BOF`, because `ssh_config` takes the
first value it obtains and `blockinfile` anchors `insertbefore` on the *last*
match. Two local checks therefore run on a real create only, after the
keypair preflight and after the credential check, and never on `build` or
`--dry-run`, which must not read `~/.ssh/config` at all: a `Host <profile>`
stanza outside this package's markers is an error naming the file and the
line, never overwritten (the never-adopt rule); and an option standing above
the first `Host` or `Match` line is an error too, because a BOF insert would
capture that global option into one stanza. Both messages name the recovery.

For a deployment this means a hand-written `Host rybbit-vultr` stanza in the
operator's `~/.ssh/config`, outside the markers, makes a real create refuse by
design: remove or rename it if it is stale, or change `profile` if it belongs
to something else. That refusal is the standard working, not a bug to work
around.

## The four-fixture golden and parity axis

The package supports two compute providers, selected by template directory
rather than conditionals, so a build is the only thing that proves a provider's
tree renders at all — and the SSH Keypair Standard has two modes, so
conformance means both hold on both providers. There are four fixtures under
`test/fixtures/`: `colors.yml` (DigitalOcean, opt-out, profile `rybbit-fixture`)
and `colors-vultr.yml` (Vultr, opt-out, profile `rybbit-vultr-fixture`) carry
an explicit key id and a name equal to the profile; `keygen.yml`
(`rybbit-keygen-fixture`) and `keygen-vultr.yml` (`rybbit-vultr-keygen-fixture`)
carry neither. One committed golden tree per profile lives under
`test/resources/golden/local/`. **The Vultr opt-out golden is the shape of the
live `rybbit-vultr` deployment**: adopting the standard changed it by the
`params.provider` field alone (`vultr_firewall_group.rybbit`,
`vultr_instance.rybbit`, the generated `firewall.tf.json` rule names and the
UDP 443 rules are untouched), and any further change there is a plan against
a running machine. The DigitalOcean opt-out golden additionally changed when
its 80/443 TCP and 443 UDP rules became one `dynamic "inbound_rule"` block
guarded on a non-empty `digitalocean-http-sources`, so an empty list means no
public HTTP rather than an API error; no live DigitalOcean deployment existed
to move. The Vultr tree carries a generated `firewall.tf.json`, one rule per
protocol, address family and source CIDR (UDP 443 included, for HTTP/3), and
an empty `vultr-http-sources` simply emits no http, https or quic rule.
Adopting the SSH Config Standard added one `rybbit-ansible-local/` tree to
each of the four goldens and changed no other byte: the live deployment's
`rybbit-infrastructure/` is untouched. `scripts/golden.sh` checks green
against all four and asserts the keypair standard on each (a keygen tree
declares the profile-named key resource and references it by attribute; an
opt-out tree creates none and keeps the literal id; no rendered tree names
`$HOME/.ssh`) and the config standard's §6 (no dotted quad under
`rybbit-ansible-local`); `scripts/parity.sh` renders all four
through every colour and diffs the trees — and the colour template trees
(`red/resources`, blue's embedded `resources/`) — byte for byte.

## Coupling

The package pins Green and ONCE in `green/deps.edn`, the Red SDK and
`package-once-red` in `red/package.json`, and the Blue SDK and
`package-once-blue` in `blue/pyproject.toml`. All three colours pin ONCE at the
**same rev** (`38e3cd6`) — ONCE's own parity is what guarantees its colours
agree per commit. The green pin (`3f33f5d`) is a floor coupled to that ONCE
rev: ONCE 38e3cd6 trusts the SDK's step error alone when it reads state, and
green 3f33f5d is where the SDK reports a tofu launch failure (a missing stage
directory or binary) as that step error, the way red and blue always did; an
older green under this ONCE would crash a fresh-clone create instead of
reporting its credentials, so the two pins move together. ONCE supplies the backend provider registry, the `compute`
namespace (the Compute Provider Standard's operations over this package's own
registry) and the `ssh` namespace (the SSH Keypair Standard); the red launcher's
`PINS`, the blue launcher's PEP 723 block and `green/tasks/pin.clj` carry the
same rev. A pin bump is read through `scripts/golden.sh`: the opt-out goldens
render the historical shape byte for byte whatever ONCE's keypair default is,
because presence of `<provider>-ssh-keys` in those fixtures is what selects
opt-out. `blue/pyproject.toml` carries a `[tool.uv] override-dependencies`
block, now redundant because `package-once-blue` at `38e3cd6` pins the same
Blue rev, and kept because it is harmless and would make this package's Blue
pin win were ONCE ever to pin an older one again. Between a commit that moves the
ONCE pin and the `bb pin` that re-stamps the launchers, the blue launcher's
inline metadata pins `package-rybbit-blue` at the previous commit — whose
`pyproject.toml` pins the previous ONCE — and `uv run --script` refuses the
conflicting URLs before `RYBBIT_LIB_ROOT` is consulted; run blue through the
project meanwhile (`cd blue && uv run python -m package_rybbit_blue …`).

Use `RYBBIT_LIB_ROOT` (the repository root, for every colour; red also accepts
the `red/` dir directly), `GREEN_LIB_ROOT`, and `ONCE_LIB_ROOT` for
working-tree development. Final launchers use a pushed SHA managed by `bb pin`
(in `green/`), which stamps all three payloads from their unpinned birth forms;
deployment launchers are copies, not symlinks.

## Documentation

`index.html` is this repository's landing page and carries two analytics tags:
GA4 measurement ID `G-4VKP1WY4QJ`, whose explicit `page_title` must exactly
equal the decoded HTML `<title>` and stay distinct and stable so one Analytics
property can separate repositories, and the self-hosted Rybbit snippet
`<script src="https://rybbit.getcolors.ai/api/script.js" data-site-id="9fb9c41a6d49" defer></script>`,
which shares one site ID across every page because `getcolors.github.io/<repo>/`
paths already encode the repository. Never add one tag without the other.

## Git

Work on the current branch. Do not commit or push unless explicitly authorized.
The launcher pins are managed only by `bb pin` (in `green/`) after a clean
pushed commit; never invent a SHA.
