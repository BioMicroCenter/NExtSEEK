---
name: deploy
description: Use when deploying, redeploying, rolling back, or verifying the NExtSEEK stack — greenfield install on a new box, shipping a code or config change to a running instance, rollback, or post-deploy verification. Routes to DEPLOYMENT.md (the authoritative runbook) and enforces the non-negotiable deployment-hygiene gates.
---

# NExtSEEK deploy

**Read the repo-root `DEPLOYMENT.md` in full before acting** — it is the
authoritative runbook and the stand-in for a CI/CD pipeline. This skill only
routes you and holds the hard gates.

## Standard verbs

- **App code**: `./startup.sh rebuild` — rebuild the shared app image and
  recreate `nextseek` plus every attribute worker/dispatcher/recovery runtime.
- **CC image**: `./startup.sh rebuild --component cc-agent` — build only; the
  next chat turn uses it, with no persistent agent container to restart.
- **Sidecar / proxy**: use `--component nextseek-sidecar` or
  `--component bedrock-proxy`.
- **Every first-party image**: use `--component custom-stack`. This never
  rebuilds or restarts nginx, databases, SEEK, or Solr.
- **Dirty shared runtime checkout**: use `--source-tree <clean-origin-dev>`.
  Images build from that verified clean checkout while recreation continues
  from the installed instance, preserving its existing bind-mounted paths.

Every rebuild verb first creates and verifies local rollback tags, uses
`--no-deps --force-recreate` for long-running targets, gates each fresh image
for baked secrets, and attempts its component-specific private GHCR baseline.
Local rollback failure aborts before building; GHCR failure remains non-fatal
but produces a loud banner and red doctor check. Do not bypass this with raw
`docker compose build` / `up`.
- **Diagnose**: `./startup.sh doctor` (read-only) before touching anything.
- **First-ever install on a box**: `./startup.sh install` (full pipeline:
  prereqs → config render → volumes → seeds → build → users → health).
- **Nuke and reinstall**: `./startup.sh reset` (DESTRUCTIVE — drops volumes).

## Route by task

| Task | Go to |
|---|---|
| Fresh install on a new box | DEPLOYMENT.md §2 (then NExtSTEPS.md before exposure) |
| Ship a code change to a running instance | DEPLOYMENT.md §3 (`./startup.sh rebuild`) |
| Config-only change (env / local_settings) | DEPLOYMENT.md §4 |
| Roll back a bad deploy | DEPLOYMENT.md §5 |
| Verify a deploy | DEPLOYMENT.md §6 (always, after every deploy) |
| Container-CC specifics / OI-3 checks | `nextseek_api/cc_assistant/DEPLOY.md` + DEPLOYMENT.md §9 |

## Non-negotiable gates (apply to every deploy)

1. Deploy only committed code from `origin/dev` — never `docker cp` fixes
   into a running container (ephemeral; lost on recreate).
   `--source-tree` refuses a dirty tree, a SHA other than `origin/dev`, a
   runtime/source SHA mismatch, or dirty runtime deployment-control files.
2. Require the rebuild CLI's verified pre-tags. If raw image work is explicitly
   approved, create and inspect equivalent tags before replacing any image.
3. mysqldump gate before any deploy whose range includes a Django migration.
   Never `migrate --fake` a wedged migration.
4. Recreate only the selected component (`--no-deps`). The app component is a
   cohort: web + all processes that execute the shared app image.
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
