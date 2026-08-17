---
name: package-rybbit-green
description: Provisions and operates a production-oriented single-node Rybbit analytics service with PostgreSQL, ClickHouse, Redis and Caddy on DigitalOcean.
license: MIT
---

# Rybbit with Green

Operate one Rybbit analytics deployment from non-secret `colors.yml`. Read
[references/configuration.md](references/configuration.md) before changing
configuration or running a lifecycle operation.

## Safety

- Keep credentials in gitignored `.envrc.private` as `COLORS_PAR_*` variables.
- Never set `COLORS_PAR_PROFILE` or edit/commit `.colors/`.
- Keep `compute-prevent-destroy: true`; deletion requires separate explicit
  authorization and a one-run environment override.
- Build and dry-run before a real create.
- Only Caddy's 80/443 are public. PostgreSQL, ClickHouse, Redis and the Rybbit
  backend and client ports stay on the private Compose network.
- `rybbit-disable-signup` is desired state. Rybbit has no first-run bootstrap,
  so it must stay `false` until you have registered the first account, then be
  set `true` to close public registration.

```sh
./green build
./green create --dry-run
./green create
```

A real create ends in acceptance: HTTPS health with a verified certificate, a
synthetic event read back out of ClickHouse, and a backup drill confirmed by a
fresh object in R2.
