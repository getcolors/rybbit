# CLAUDE.md

## Repository

`rybbit` is a Green Package Skill for a production-oriented, single-node
Rybbit analytics deployment on one DigitalOcean Droplet. OpenTofu discovers
the configured region's default VPC at runtime, manages the Droplet, firewall
and Cloudflare apex record, and Ansible converges a private Docker Compose stack.
Only Caddy ports 80/443 and key-only SSH are public. PostgreSQL, ClickHouse,
Redis, and internal Rybbit application ports remain on the private Compose network.

The Rybbit stack pairs PostgreSQL 17 for relational metadata/authentication with
ClickHouse 24.8 for high-throughput columnar analytics. Persistent data lives
under `/var/lib/rybbit`; a systemd timer takes regular database backups to R2.

## Commands

```sh
bb test
bb golden
bb golden:accept
./scripts/launcher.sh
./green build
./green create --dry-run
./green create
./green delete
```

Never read `.envrc.private`, edit `.colors/`, export `COLORS_PAR_PROFILE`, or
weaken `compute-prevent-destroy`. Build and dry-run are credential-free. A real
create/delete requires explicit authorization.

## Coupling

The package pins Green and ONCE in `deps.edn`; ONCE is used for its backend
provider registry. Use `GREEN_LIB_ROOT`, `ONCE_LIB_ROOT`, and `RYBBIT_LIB_ROOT`
for working-tree development. Final launchers use a pushed SHA managed by
`bb pin`; deployment launchers are copies, not symlinks.

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
