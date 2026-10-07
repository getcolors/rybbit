# CLAUDE.md

## Repository

`rybbit` is a tri-colour Package Skill (green, red, blue) for a
production-oriented single-node Rybbit deployment. The shared library owns
compute and firewall resources; the package manages Cloudflare DNS and converges
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
Python/uv in `blue/`. Each color has application validation, a thin `compute`
consumer, identity formatting in `ssh`, local config guards in `ssh-config`,
application stages in `tools`, and a workflow graph. Templates contain DNS,
application Ansible and the local SSH updater; provider compute templates live
only in the library. Green is canonical: a
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
./scripts/parity.sh            # three colours, v2 integration fixtures, byte for byte
./scripts/launcher.sh          # from the repository root
cd green && ./green build
cd green && ./green create --dry-run
cd green && ./green create     # requires explicit authorization
cd green && ./green delete     # guarded and destructive
```

Never read `.envrc.private`, edit `.colors/`, export `COLORS_PAR_PROFILE`, or
weaken `compute-prevent-destroy`. Build and dry-run are credential-free. A real
create/delete requires explicit authorization.

## Shared compute v2 ownership

This package requires `compute-api-version: 2` for fresh deployments only.
Existing deployments keep their installed pins, state and access identities;
no migration, adoption or compatibility tooling is supplied.

Read `../workspace/standards/compute-provider.md`, `ssh-keypair.md` and
`ssh-config.md`, prioritizing their v2 sections over older contracts. The
library pin is `ed39df40ac0bd30f4014c263ec70ae7d09ab3984`. The node is
`rybbit-compute`, its state is `<profile>/rybbit-node-0.tfstate`, and separate
public-key registration uses `<profile>/rybbit-ssh-registration.tfstate` where
required by the library registry. DNS retains `<profile>/rybbit-dns.tfstate`.
R2 and S3 remain the supported package backends.

Fresh create inspects SSH authority first. Only confirmed missing authority with
`compute-require-existing-state` disabled may call the library
`ssh-verify-absent!` / `ssh_verify_absent` helper with the node and any registration
descriptors. Pass `verified_absent: true` only for a verified result; never use
local rendered files as evidence of remote absence. Build/dry-run skip the helper.

The library owns provider templates, public identity validation, independent
resource lifecycle, and backend access. Rybbit owns graph ordering and a local
profile lock. Do not add a provider registry or compute templates here.
Application ingress is TCP22/80/443 plus UDP443 for HTTP/3. Empty HTTP sources
close HTTP and HTTP/3. Source precedence is compute-, rybbit-, then selected
provider settings. Inventory uses the returned address and user; never assume
root or substitute planned addresses for missing live results.

## SSH lifecycle and local configuration

Encrypted SSH authority is named `machine-access`, with runtime binding
`COLORS_PAR_RYBBIT_SSH_PASSPHRASE`. A workflow scope owns the temporary agent;
application processes stop before it. No private key is written to disk and
the operator's agent is never modified. Public identity caches are disposable.
Use the launcher's `ssh` command; bare aliases cannot authenticate after the
scope stops. Refuse old key paths and provider key references.

Create completes the package-owned local alias stage before DNS and remote
Ansible. Delete inspects owned state, removes aliases before compute teardown,
performs remote cleanup with scoped access, destroys DNS and compute, then
removes separate registrations. Encrypted authority is retained. Repeated
delete of confirmed destroyed compute must skip host access and agent unlock.

The locked atomic SSH-config updater remains package-owned and byte-identical
across all resource trees. It refuses unowned aliases and unsafe global options.
Build uses deterministic public identity/socket placeholders and renders under
`.colors/build/<profile>/`; dry-run never reads or writes state or operator SSH
configuration. `compute-require-existing-state` guards later creates; it is not
an import or state transfer mechanism.

## Validation and dependency pins

Run `cd green && bb test && bb golden`, Red tests/typecheck, Blue pytest,
`scripts/parity.sh` and `scripts/launcher.sh`. Shared fixtures exercise Google,
registration-backed providers, R2/S3 and closed HTTP ingress. Review golden
diffs before acceptance. Runtime behavior needs lifecycle/access tests as well
as rendered-tree parity. `scripts/check-compute-plan.py` checks v2 node and
registration ownership and backend keys. Root desired state must also build
with a temporary workdir and sanitized environment.

Align compute, SDK and ONCE helper dependencies across manifests, locks and
standalone launcher metadata. Preserve DNS R2 runtime credential mapping.
The SDK strips `COLORS_PAR_*` from Ansible child environments. Forward only
the two backup credentials through explicit `RYBBIT_BACKUP_R2_*` aliases on
create; template lookups use those child aliases. Test the actual SDK process
seam and keep values out of generated files.
After a clean source commit is pushed, `cd green && bb pin` stamps all three
Rybbit launcher pins. Then build actual copied payloads without LIB_ROOT
overrides, commit the stamps, and push. Never invent a SHA or hand-edit the
package pins. Existing deployment payloads are not refreshed by this release.

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
