# Independent deployment-script review

Reviewed initial frozen script `provision_console_server.py`, SHA256 `00a6758a0d2e15cc0079567391206a881ce122fb7a316239c85a215b5203bd3a`, read-only. No import, execution, remote command, database connection or private credential read was performed by this reviewer.

Final verdict: **PASS after the narrow startup/timeout fixes; no remaining P1/P2 identified in this script review.** The initial finding below is historical and closed in the final-delta section. This verdict permits proceeding to the separately authorized preflight; it does not assert that preflight or deployment has succeeded.

## P1: umask makes copied dependency directories unreadable by the service user

The original `prepare_dependencies` creates its temporary root and package parents with `mkdir(mode=0o755)`, while `main` sets `os.umask(0o077)`. Those newly created directories therefore have mode 0700. Only copied files are subsequently chmodded to 0644; the directories remain private when renamed to `deps`. The nonroot `access_probe` and Web child cannot traverse/import the dependencies. This fails after schema/role provisioning has committed, so the first prepare cannot reach a usable service.

Required fix: explicitly chmod only the newly created public dependency directories to 0755, without broadening any old private ancestor. Add a pure temporary-directory regression running under umask077 and verifying all generated directories and files before freezing again.

## Recommended bounded-failure delta

The initial five psycopg connection sites have no connect timeout. Admin/preflight/low-role SQL also lack the statement/lock bounds used by the owner migration transaction. Fixed connection and SQL bounds would prevent preparation from hanging on an accepting-but-stalled database. This was sent to core separately from the startup blocker.

## Source-level controls verified

- Fixed root, code, old environment, container, schema, role, user and loopback port. Existing container label/image/volume/port plus actual owner/database/schema are verified. No Docker create/start/stop/restart or old-process termination exists.
- Public source manifest checks exact files, hashes and ownership; original migrations001–007 must match old source and database checksums. Only008/009 may be added. The product role grant change targets one named runtime role and is followed by both role guards.
- The schema-filtered custom pg_dump includes schema data, is server-private, checks pg_dump exit and decodes the archive to /dev/null without restoring. A published-but-unbound backup is refused on retry. Before/after aggregates remain server-side and compare one repeatable-read transaction snapshot; this is not a claim that all concurrent business activity stopped.
- New role password is generated and retained server-side. The Web environment contains only Console runtime credentials; no migration-owner DSN, provider execution secret or old product configuration is passed. Public model metadata supplies the provider registry.
- A dedicated nonlogin OS user, cleared supplementary groups, private home, fixed environment and `python -S -B` isolate the new child. Dependency copying excludes startup hooks and raw private parents. The negative access probe checks old/new private files; it also validates real imports and Console settings.
- The wrapper records PID/start-time and checks exact argv, uid and listening socket. Startup failure terminates only its newly spawned child. The log and state remain root0600. Errors expose phase/type only, not SQL, DSNs or raw responses.

No claim of actual backup, migration, process isolation, Logto deployment or administrator login is made by this source review.

## Final frozen delta — PASS

Independently read the final script SHA256 `0d4941454905b422a8efae5c7d2cd80dc227237eff6780636d4ff959ff08928f` and `test_provision_console_public_deps.py`, without importing/executing either script or accessing a server.

`copy_public_dependencies` now creates a fresh target, copies public files as0644 and explicitly sets only this new directory tree to0755 after copying. The initial mkdir rejects an existing target, so this cannot broaden an old private directory. The caller uses that helper before renaming to `deps`; existing dependencies still require exact hashes, modes and no symlinks. The pure-helper regression extracts the actual function through AST, checks every nested directory under umask077, checks file bytes/modes and verifies an existing0700 target/sentinel remain unchanged. Core reports RED1 fail/1 pass → GREEN2; this reviewer read that evidence rather than rerunning it.

All five psycopg connection sites now receive fixed `connect_timeout=5` and server options `statement_timeout=30000` / `lock_timeout=2000`. The bounded-failure recommendation is addressed. Existing subprocess, archive and child-start bounds remain unchanged. No secret/owner, scope or mutation boundary was broadened by the fix.

Root may proceed with the reviewed source upload and preflight within existing authorization. Actual backup checks, runtime import availability, low-privilege startup and health still need the script's real checks; no real administrator login is claimed. The separate identity-app transfer issue is outside this deployment-script verdict and remains a prerequisite for `client()`.

## v2 dependency repair — independent delta PASS

Root's actual v1 prepare reached `verify-nonroot-boundary` and failed because installed FastAPI's import closure required the omitted public OpenTelemetry API package; qrcode was also added for the real command/application import closure. This supersedes any assumption that the earlier generic import probe established full CLI readiness. No successful deployment or administrator login is inferred from the earlier prepare.

Reviewed v2 SHA256 `e897da14dd1ee997bd616cfcc34776ab3e631efe61293f25b70205d858b659c3`. Independently compared the complete v1/v2 diff and verified v1 remains exactly `0d4941454905b422a8efae5c7d2cd80dc227237eff6780636d4ff959ff08928f`. Changes are limited to the version description, new `deps-v2` leaf, public qrcode/opentelemetry roots plus opentelemetry_api distribution metadata, and a stronger import probe moved before database re-entry.

The v1 dependency tree is neither removed nor modified. Claimed-state password, backup binding, UID/GID and source identity retain their existing checks; v2 does not reset a recorded dependencies digest. Root confirms the failed v1 state had not persisted that digest. A preexisting conflicting digest still fails closed. Migration replay, role binding and private runtime comparisons remain unchanged.

The nonroot probe imports actual `platform_service.__main__`, qrcode, OpenTelemetry and `create_console_app`, then constructs the app without entering its lifespan. Source tracing confirms Database creates its pool with `open=False`; OIDC client construction does not perform discovery. Thus this probe exercises dependency/configuration loading without an intentional database or HTTP/model call, and retains the negative private-file access checks. The real application will still require separate startup/health verification.

The new pure-helper test reads the actual selector/copy functions through AST, checks namespace-package and dist-info inclusion, excludes .pth/direct_url/sitecustomize/private files, and imports synthetic fixtures with `-S -B`. Core reports v1 RED1 fail/2 pass → v2 GREEN3. This supports selection/copy behavior, not actual installed-library acceptance; v2's real nonroot probe supplies the latter when root executes it.

Verdict: **PASS for the minimal recovery delta; root may upload v2 under its distinct0600 script name and re-enter prepare within existing authorization.** No script, network, remote or PG action was executed by this reviewer.
