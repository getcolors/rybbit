# Development and release

Keep it simple: Python for orchestration, SQLite for controller state, and
established CLIs for provider and host operations. PocketDeploy owns generic
compute, ownership, locking, SSH, DNS and controller recovery. Keep Rybbit's
application code in `src/rybbit/` and avoid a second infrastructure framework.
Alice and Walter provide reference application-controller patterns.

The Python controller replaces the previous Green/Red/Blue package runtime.
Historical deployment instructions belong to the retained legacy checkout or
Git history. Do not apply legacy parity or `bb pin` workflows to the new package.

## Local validation

```sh
uv sync --extra test
uv run pytest -q
uv build
uv run rybbit --help
```

Run tests with synthetic input and isolated state. Never connect ordinary tests
to production databases or cloud resources. Verify ownership refusal, lost
responses, pending operations, destructive guards, configuration validation,
secret-safe errors, archive validation and restoration fences. Check both fresh
state and repeated operations. Run PocketDeploy's suite for changes to its
shared library.

Use checkout execution (`uv run rybbit`) during implementation. A portable
launcher retains its immutable published dependency even when adjacent source
changes. Run a copied launcher from a directory unrelated to the checkout and
verify help, configuration loading, safe failure on missing state, and the
published package revision. Do not publish a moving Git branch as a release pin.

## Publication order

1. Test and publish PocketDeploy changes first.
2. Pin Rybbit's dependency to the published full commit, refresh its lockfile,
   and test the installed package.
3. Commit and push Rybbit source, then update the portable launcher to that
   published full commit.
4. Test the copied launcher, commit its pin and push. Confirm CI status.

Publishing new code never upgrades the legacy production controller implicitly.
Keep the old deployment's checkout, package pins, state and SSH authority intact
until migration and retirement are complete.

## Live verification

Use the separately identified `rybbit-hcloud` deployment and its staging
hostname. Keep production DNS unchanged during rehearsal. Test actual
provisioning, SSH host trust, repeated convergence, public HTTPS, restored
aggregate counts, ingestion, backup upload and archive restoration. Capture
failure/retry evidence where it can be exercised without risking source data.

Record outcomes in `docs/verification.md`: timestamp, profile, code and image
revisions, safe resource identities, checks actually performed and remaining
limitations. Avoid application records, tokens, secret files or raw provider
metadata. A green unit suite is not live deployment evidence, and a live staging
restore is not a final fenced production transfer.
