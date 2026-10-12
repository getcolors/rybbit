# Explicit DNS cutover

The permanent target profile remains **`rybbit-hcloud`**. Rehearse at
`rybbit-hcloud.getcolors.ai`; production remains on `rybbit-hetzner` at
`rybbit.getcolors.ai` until a separately approved final write interruption and
routing transfer. Changing the application hostname never renames compute,
backup destinations or deployment identity. Do not run the old DNS manager
after ownership transfer. Source retirement is a separate decision.

## Implemented commands

| Command | Behavior |
| --- | --- |
| `application-converge` | Configures and starts the application on existing compute without compute or DNS mutation. Backups remain disabled. It starts writers and is not a fence. |
| `readiness` | Checks configured and running application hostnames, direct origin HTTPS with production SNI and normal certificate validation, backend health and frontend HTTP 200. No ingestion writes. |
| `dns-evidence` | Reads the exact configured hostname's zone, record ID, address, proxy, TTL and settings fingerprint. Refuses ambiguous/conflicting address records. |
| `dns-adopt --evidence-file FILE --previous-dns-manager-disabled` | Transfers ownership using exact private-file evidence while preserving record ID, address, proxy and TTL. The flag affirms that the operator disabled/drained the old manager; it does not disable it. |
| `dns-switch` | Requires completed adoption and freshly successful origin readiness, then routes to owned target compute while retaining adopted proxy and TTL. |
| `dns-retire --hostname OLD_HOST --record-id ID` | Removes only the exact recorded owned old-hostname A record. Refuses the currently configured hostname and supports interrupted retries. |

Save only the result object of `dns-evidence` in an owned mode-0600 JSON file;
do not include the CLI's reporting envelope. Adoption journals intent before
mutation. Retry interrupted adoption using the original evidence. Ordinary
convergence refuses to change an adopted source address implicitly: use
`dns-switch`. The adoption receipt supplies retained proxy/TTL to later
convergence. Do not clear ownership or pending-operation state to bypass refusal.

The application-only step does not prepare certificates, fence the source,
restore data, prove authenticated login/replay playback or disable old
infrastructure automation. Readiness is an origin check, not complete migration
acceptance. Tests exercise synthetic state and mocked providers; they are not
live routing-transfer evidence.

## TLS and application hostname

Caddy, `DOMAIN_NAME`, `BASE_URL` and `NEXT_PUBLIC_BACKEND_URL` must all describe
`rybbit.getcolors.ai` before routing moves. Retain database passwords and
`BETTER_AUTH_SECRET`; verify browser login, cookie and callback behavior on the
production hostname independently. Keep the legacy
`rybbit.bigconfig.online` redirect behavior and decide staging retirement
separately: one optional redirect field cannot represent both redirects.

The staging certificate does not cover production. A hosts-file override does
not redirect certificate-authority validation. Prepare and rehearse either a
scoped DNS challenge using an appropriate Caddy module or an authorized transfer
of the existing managed production certificate/key with safe renewal behavior.
The controller enables neither automatically. Preserve Cloudflare's current
proxy and TLS policy; do not weaken strict certificate validation. Verify the
target directly with production SNI, then verify public HTTPS after switching.

## Final sequence

1. Require successful recovery checkpoints, complete database rehearsal,
   authentication/replay acceptance, measured restore duration and production
   TLS readiness. Present exact source/target identities and the expected write
   interruption for final approval. See [recovery procedure](disaster-recovery.md).
2. Read exact production DNS evidence. Disable and drain legacy DNS automation;
   privately snapshot its backend and verify its binding. The historical binding
   is `cloudflare_dns_record.rybbit` in `<profile>/rybbit-dns.tfstate`; verify it
   before any state operation. Keep the manager disabled during every retry.
3. Fence source ingestion/writers and scheduled backups. Verify quiescence and
   take the final complete archive, SHA-256 and aggregate counts. Independently
   retain the archive outside scheduled retention.
4. Restore the final archive into clean, fenced staging target databases and
   compare counts. Configure the target production hostname and prepared TLS
   using `application-converge`, then run `readiness`. Source writers remain
   fenced when target application writers start.
5. Run `dns-adopt` using exact evidence and the explicit manager-disabled flag.
   Verify preserved record identity, source address, proxy and TTL. Run the
   separate `dns-switch`, then check public TLS, login, historical queries,
   replay playback and fresh ingestion.
6. Make the old DNS state forget only the transferred binding, preserving the
   provider record and private before/after snapshots. Do not run legacy
   destroy. Removing a state binding alone does not stop unchanged desired
   configuration from recreating it; remove/disable the legacy DNS management
   path before any later source maintenance.
7. Retire staging DNS explicitly with its recorded ID. Confirm the legacy
   redirect, enable normal target backups, verify backup/recovery evidence,
   checkpoint final controller state and document production ownership.

Before target writes, routing can potentially return to the intact source with
one writer enabled. After target writes, DNS-only rollback loses target-only
data: stop and assess a verified reverse data transfer. Keep source compute and
final snapshots until a separate retirement decision.

## Execution evidence

The commands and interruption guards are implemented and covered by synthetic
tests. Actual rehearsal, certificate transfer, authentication, replay, public
routing and recovery results belong in dated [verification](verification.md).
Do not interpret this runbook or successful unit tests as evidence that a live
production switch occurred.
