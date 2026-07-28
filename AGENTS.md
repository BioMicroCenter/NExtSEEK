# Agent instructions — NExtSEEK

Project instructions for coding agents live in [`CLAUDE.md`](CLAUDE.md)
(stack overview, build/run, testing, conventions). Read it first.

For **deploying or operating** the stack — greenfield install, shipping a
change, rollback, verification — the authoritative runbook is
[`DEPLOYMENT.md`](DEPLOYMENT.md). Follow it exactly; it encodes the
deployment-hygiene gates (rollback tags before rebuilds, mysqldump before
migration deploys, scoped service recreation, post-deploy verification,
Container-CC isolation invariants).

Production hardening (credentials, TLS, exposure) is in
[`NExtSTEPS.md`](NExtSTEPS.md).
