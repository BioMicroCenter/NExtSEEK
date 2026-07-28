---
name: deploy
description: Use when deploying, redeploying, rolling back, or verifying the NExtSEEK stack — greenfield install on a new box, shipping a code or config change to a running instance, rollback, or post-deploy verification. Routes to DEPLOYMENT.md (the authoritative runbook) and enforces the non-negotiable deployment-hygiene gates.
---

# NExtSEEK deploy

**Read the repo-root `DEPLOYMENT.md` in full before acting** — it is the
authoritative runbook and the stand-in for a CI/CD pipeline. This skill only
routes you and holds the hard gates.

## Route by task

| Task | Go to |
|---|---|
| Fresh install on a new box | DEPLOYMENT.md §2 (then NExtSTEPS.md before exposure) |
| Ship a code change to a running instance | DEPLOYMENT.md §3 |
| Config-only change (env / local_settings) | DEPLOYMENT.md §4 |
| Roll back a bad deploy | DEPLOYMENT.md §5 |
| Verify a deploy | DEPLOYMENT.md §6 (always, after every deploy) |
| Container-CC specifics / OI-3 checks | `nextseek_api/cc_assistant/DEPLOY.md` + DEPLOYMENT.md §9 |

## Non-negotiable gates (apply to every deploy)

1. Deploy only committed code from `origin/dev` — never `docker cp` fixes
   into a running container (ephemeral; lost on recreate).
2. Rollback-tag the current image **before** rebuilding, and verify the tag
   exists (`docker image inspect`). A rollback script must fail loudly if its
   source tag is missing.
3. mysqldump gate before any deploy whose range includes a Django migration.
   Never `migrate --fake` a wedged migration.
4. Recreate only the services that changed (`--no-deps`); the OI-3 peers'
   uptime (`dmac-bedrock-proxy`, `nextseek-sidecar`) is part of verification.
5. Never weaken the CC agent isolation: zero shared credentials in the agent
   env, Bedrock only via the proxy, `nextseek` never joins `dmac-cc-net`.
6. Secrets exist only in the gitignored files (DEPLOYMENT.md §8) — never in
   git, images, logs, or docs. Never push a `docker commit` snapshot of a
   running container off the box (its image config embeds runtime secrets).
7. Paid/live lanes (`RUN_REALSTACK=1`, `-k realstack`) require explicit
   per-run owner approval. Free lanes and the §6 checklist do not.
8. Do not prune images/tags/volumes without per-item owner approval —
   rollback tags are backups.

After any deploy: run the DEPLOYMENT.md §6 checklist end-to-end and report
the results honestly, including anything skipped.
