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
Python/uv in `blue/`. Green is canonical: a behavioural change lands in all
three colours in the same commit and passes `scripts/parity.sh`. The fixtures
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
./scripts/parity.sh            # three colours, two compute providers, byte for byte
./scripts/launcher.sh          # from the repository root
cd green && ./green build
cd green && ./green create --dry-run
cd green && ./green create     # requires explicit authorization
cd green && ./green delete     # guarded and destructive
```

Never read `.envrc.private`, edit `.colors/`, export `COLORS_PAR_PROFILE`, or
weaken `compute-prevent-destroy`. Build and dry-run are credential-free. A real
create/delete requires explicit authorization.

## The two-provider golden and parity axis

The package supports two compute providers, selected by template directory
rather than conditionals, so a build is the only thing that proves a provider's
tree renders at all. There is one fixture per provider —
`test/fixtures/colors.yml` (DigitalOcean, profile `rybbit-fixture`) and
`test/fixtures/colors-vultr.yml` (Vultr, profile `rybbit-vultr-fixture`) — and
one committed golden tree per profile under `test/resources/golden/local/`.
The Vultr tree additionally carries a generated `firewall.tf.json`, one rule
per protocol, address family and source CIDR (UDP 443 included, for HTTP/3).
`scripts/golden.sh` checks green against both; `scripts/parity.sh` renders both
fixtures through every colour and diffs the trees — and the colour template
trees (`red/resources`, blue's embedded `resources/`) — byte for byte.

## Coupling

The package pins Green and ONCE in `green/deps.edn`, the Red SDK and
`package-once-red` in `red/package.json`, and the Blue SDK and
`package-once-blue` in `blue/pyproject.toml`. All three colours pin ONCE at the
**same rev** (`98d3cfa`) — ONCE's own parity is what guarantees its colours
agree per commit. ONCE is used for its backend provider registry only; the
compute-provider registry is this package's own. This package deliberately
stays on that older ONCE pin: a bump would adopt the SSH-keypair default and
churn every golden, and is its own change. `blue/pyproject.toml` carries a
`[tool.uv] override-dependencies` block because `package-once-blue@98d3cfa`
pins an older Blue rev (`369c5aa`); the override makes this package's Blue pin
win.

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
