# Python and SQLite port assessment

Assessed 12 October 2026. Recommendation: proceed with a small Python deployment
controller using PocketDeploy, initially supporting Hetzner only. SQLite stores
deployment ownership and operation receipts. Rybbit's PostgreSQL, ClickHouse,
Redis, Caddy and application containers remain unchanged. Replacing those
databases with SQLite would be a different application rewrite.

**Keep it simple.** Reuse shared code, keep existing useful configuration names,
use established CLIs, and use a sequential application workflow. Do not build a
new provider framework, general task scheduler or Terraform compatibility layer.
This assessment implements no runtime changes and performs no cloud operations.

## Findings

The current Python implementation in `blue/src/package_rybbit_blue/` is already
a useful source of validation and acceptance logic, but it still orchestrates
colors-compute, OpenTofu and Ansible. Merely deleting Green and Red would not
deliver the requested simplification.

PocketDeploy already supplies local SQLite state, a deployment UUID, operation
intents/results, locking, provider boundaries, host trust, SSH, aliases and Vault
checkpoints. Alice demonstrates a small application controller on top of these;
Walter demonstrates preserving application configuration names through internal
normalization. PocketDeploy currently supports OCI, DigitalOcean and GCP, **not
Hetzner**. Its configuration validation and scope binding also need extension.

Python-only orchestration does not mean standard-library-only or no external
executables: PocketDeploy currently depends on the Python Blue package. Its
Cloudflare CLI needs Node.js. Reuse these existing dependencies initially rather
than rewrite them just to remove a runtime. The host still needs Docker Compose,
systemd, database clients and rclone; the operator needs uv, OpenSSH and hcloud.

## Reuse and new work

| Source | Reuse | Required change |
| --- | --- | --- |
| PocketDeploy `state.py`, `runtime.py` | SQLite, local lock, sessions, intent journal, initialization | Extend provider scope for Hetzner; retain one active operator |
| PocketDeploy `compute.py`, `digitalocean.py`, `gcp.py`, `common.py` | Provider contract, owned-resource reconciliation, bounded subprocesses | Add `hcloud.py`; do not copy another provider's assumptions |
| PocketDeploy `host.py`, `transport.py`, `host_trust.py`, `ssh_config.py` | Pinned SSH, JSON over stdin, owned aliases | Rybbit-specific alias marker and controller calls |
| PocketDeploy `services.py`, `dns_adoption.py` | Cloudflare record identity, collision checks, explicit adoption | Expose a small public DNS interface independent of ONCE applications |
| PocketDeploy `vault.py` | Consistent SQLite checkpoint and local authority recovery | Rybbit application secrets require separate recovery coverage |
| Alice `config.py`, `controller.py`, `cli.py`, `remote.py` | Small controller, configuration allowlist, remote result validation | Replace Transmission-specific operations with Rybbit operations |
| Walter `config.py`, `controller.py`, `remote.py` | Internal name mapping, trusted remote Python, safe diagnostics | Reuse patterns; omit user seats, tool installers and parallel scheduler |
| Rybbit Blue `validate.py`, `tools.py` and `resources/tools/ansible/` | Application settings, Compose/Caddy content, acceptance and backup semantics | Replace Ansible tasks and shell backup orchestration with Python |

Prefer importing PocketDeploy to copying its state/SSH/recovery implementation.
Shared improvements belong there and must preserve Alice/Walter behavior. Do not
make Rybbit pretend to be an ONCE application to obtain DNS support. The shared
`delete_compute` helper refuses remaining service resources, so remove owned DNS
through the service interface before calling compute deletion.

## Hetzner through hcloud

Use Python subprocess argument lists and machine-readable JSON. The official
[server-create interface](https://github.com/hetznercloud/cli/blob/main/docs/reference/manual/hcloud_server_create.md)
supports location, image ID, labels, firewall attachment, protection and user
data on stdin. The [describe interface](https://github.com/hetznercloud/cli/blob/main/docs/reference/manual/hcloud_server_describe.md)
supports JSON; [firewall creation](https://github.com/hetznercloud/cli/blob/main/docs/reference/manual/hcloud_firewall_create.md)
accepts rules on stdin. Pin and test a CLI version during implementation; this
review has not installed or exercised hcloud against an account.

The adapter should:

- Record server, firewall, SSH registration and any independently owned primary
  IP identities, including deletion/auto-delete behavior. Do not assume deleting
  the server removes all resources or all charges.
- Use durable ownership labels plus exact recorded provider IDs. Resolve names
  only as desired labels, never as authority to adopt existing resources.
- Journal intent before mutation, wait for provider actions, then verify actual
  resources. After a lost response, reconcile the pending operation and ownership;
  refuse ambiguous matches instead of repeating create blindly.
- Preserve `compute-network-mode: none`, image pins and destruction protection.
  Refuse unsupported resize/replacement changes instead of rebuilding production.
- Preserve SSH TCP 22 and HTTP TCP 80/443 plus UDP 443. Empty HTTP sources close
  both HTTP and HTTP/3. Apply IPv4/IPv6 policy deliberately and test both.
- Keep tokens in the child environment, user data on stdin, and raw JSON/stdout/
  stderr in memory. Emit only allowlisted results and fixed diagnostics. Do not
  log commands containing sensitive arguments or enable provider debug output.

Project identity needs an explicit design/test: the adapter must detect a changed
credential scope without hashing a rotating secret into state or inventing a
project-identity API. If hcloud cannot expose a stable project identifier, document
the binding and use verified recorded resource identities to fail closed; add one
scope setting only if required. This is an implementation question, not an assumed
capability of the CLI.

## Minimal colors.yml changes

Leave the production file unchanged during assessment and development. Preserve
its comments, ordering, values and digest pins when making the eventual patch.

| Existing fields | Proposed treatment |
| --- | --- |
| `profile`, `workdir`, `provider-compute: hcloud`, `provider-dns` | Keep names; handle normalization internally |
| `hcloud-server-type`, `hcloud-location`, `hcloud-image` | Keep unchanged |
| `compute-prevent-destroy`, `compute-require-existing-state` | Keep fail-closed meaning; missing SQLite never permits implicit adoption |
| `compute-network-mode`, `compute-ssh-sources`, `compute-http-sources` | Keep and validate; normalize HTTP/3 internally |
| Hostnames, signup policy, six image references and data directories | Keep unchanged |
| All `rybbit-backup-*` fields | Keep; R2 remains the application-backup destination |
| `compute-api-version`, `provider-backend`, `r2-bucket`, `r2-endpoint` | Remove the four obsolete Terraform-state settings at final cutover |

Default SQLite to `.colors.sqlite` and SSH authority to `.ssh/` beside the chosen
configuration, as in PocketDeploy. Do not add settings for defaults. Optional Vault
configuration is a separate small addition once the actual recovery destination
is chosen. Preserve existing `COLORS_PAR_*` credential names through child-only
mapping to CLI-native names; never source private shell files inside the controller.
Do not silently accept retired state-backend fields as if they still protect state.

Keeping YAML vocabulary is configuration continuity, not compatibility with old
Terraform state. New state must never be bootstrapped over the production profile
just because the old file parses. Use a separate directory/profile for testing.

## Application workflow

Expose `init`, `plan`, `converge`, `status`, `ssh`, `delete`, `vault-save` and
`vault-restore`, following the references. Proposed convergence is:

1. Validate configuration, credential availability and owned resources.
2. Prepare/recover local authority, reconcile compute, establish verified SSH.
3. Run trusted remote Python to install prerequisites and render the stack.
4. Reconcile owned DNS, start/update Compose, verify HTTPS and redirect behavior.
5. Verify ingestion, run and verify a fresh backup, record completion.

Use explicit sequential functions rather than a new graph engine. Offline config
validation stays credential-free; document that a live `plan` reads providers.
Plans must not create authority or mutate cloud resources. A missing-state plan
must not imply an existing server can safely be adopted.

Preserve `stack.env` secrets across convergence, including `BETTER_AUTH_SECRET`.
Update non-secret hostname/signup settings separately. Keep database ports private,
retain ClickHouse tuning and archive settings, and recreate/reload containers when
bind-mounted configuration changes; atomic file replacement alone can leave Caddy
reading an old inode. Payload secrets travel on SSH stdin and stay out of SQLite,
desired-state hashes and generated local artifacts.

Port the scheduled backup driver to Python while retaining PostgreSQL logical
dumps, ClickHouse native BACKUP, PostgreSQL scratch-restore validation, rclone
upload and prefix-scoped retention after successful upload. A ClickHouse failure
must fail the backup; do not fall back to copying live database files. Preserve
the existing archive layout. Add a backup lock and guaranteed temporary cleanup.
Ordinary backups still exclude Redis and do not establish a coordinated snapshot
across databases or point-in-time recovery.

Reuse ingestion acceptance but strengthen its evidence: a uniquely marked event
must be found in ClickHouse; an increase in the total event count can be unrelated
traffic. HTTP 200 alone is insufficient. No configured site should be reported as
not tested. Status remains read-only; it does not send tracking probes.

## Production ownership is separate work

The supplied research note and an authenticated WikiContext read agree that
Hetzner is production and Vultr was deleted on 10 October. The old rollback
instructions in AGENTS/README are historical. Core owns deployment and backup
accountability; next review is 10 November. A full replacement-host recovery drill
remains unproven. See the [published deployment revision](https://wiki.pocketcontext.com/#/page/deployment-profile-rybbit-hetzner?publication=mpbn396vtuz8c4d),
[retirement evidence](https://wiki.pocketcontext.com/#/passages/uy08fro7tc865t1)
and [ownership evidence](https://wiki.pocketcontext.com/#/passages/1guobknbl6fczxm).
These are dated records, not a fresh provider inspection by this assessment.

For an eventual in-place transfer, separately authorize and review a bounded
handoff: checkpoint old state and secrets, disable/drain the old manager, verify
exact compute/firewall/registration/IP/DNS identities, establish new SSH authority
through existing trusted access, journal ownership transfer, verify a no-change
plan and repeat convergence, then checkpoint the new controller. Preserve existing
DNS record identity, target, TTL and proxy setting. The redirect hostname remains
externally managed. Never infer ownership from its name or change both managers
to run concurrently. Do not hand-edit SQLite or delete old state as migration.

PocketDeploy's default local SSH private keys differ materially from today's
encrypted remote authority and memory-only unlock. Adopting that model must be
explicit in the implementation decision and recovery plan; simply copying its
`init` function is not an equivalent security model. Retain old authority until
new access and recovery are verified. SQLite plus a local lock coordinates one
operator host only, not two restored copies on different machines.

A fresh replacement avoids in-place infrastructure adoption but requires a real
data restore and DNS cutover. Implement an explicit staged restore that fences
ingestion and backup timers until secrets/databases are restored and verified.
Ordinary convergence is not restoration. Controller Vault checkpoints alone omit
remote database archives and retained application secrets; cover both explicitly.
Do not treat retired Vultr as a rollback destination.

## Delivery and validation

1. Extend PocketDeploy with tested hcloud support and a reusable DNS interface.
2. Build the small Rybbit Python controller and remote/backup programs using the
   existing templates and Python acceptance logic. Keep production launchers pinned.
3. Test synthetic interruption/retry, ownership mismatch, missing state, immutable
   drift, CIDRs, host trust, secret exclusion, DNS collisions, backup failure,
   restore ordering and deletion protection. Repeat convergence must preserve
   secrets and avoid resource recreation.
4. Run PocketDeploy, Alice and Walter regression suites where shared code changes;
   run Rybbit package/build/copied-launcher checks from an unrelated directory.
   Validate Compose/Caddy output and isolated container backup/restore behavior.
5. With separate cloud authorization, verify a disposable Hetzner lifecycle,
   HTTPS, ingestion, reboot, backup restoration and interruption recovery. Then
   review the distinct production-transfer procedure.
6. Publish shared/package commits before pinning the new launcher. At the intended
   runtime switch remove obsolete tri-colour code, Ansible/Tofu templates, parity
   scripts and dependencies; rewrite README/AGENTS/skill instructions consistently.
   Preserve historical deployed artifacts and state outside the new runtime.

The controller port is feasible and substantially reusable. Hetzner reconciliation,
application convergence/backup parity, SSH-authority transition and production
ownership transfer are the real work. Treat it as a controller rewrite with a
separate operational handoff, not a mechanical language conversion.

## Review provenance

Inspected clean local checkouts: Rybbit `b5b63ca`, PocketDeploy
`950cfa39c0403ff920f3e92620824ed451292943`, Alice
`5863c767c8a06dd7fa178bc23187f015623b989b`, Walter
`85d4fc489e0901ff7ef6fec5e8a91839b292b3aa`.
Read the supplied `rybbit-hetzner-wiki-research-2026-10-12.md`, WikiContext skill,
published deployment revision `v65s5h1gmcmezkf` at publication `mpbn396vtuz8c4d`,
and the two full evidence passages linked above. Consulted official hcloud docs.
No credentials, private state, provider inventory or live containers were inspected.
This documentation assessment ran no implementation or live deployment tests.
