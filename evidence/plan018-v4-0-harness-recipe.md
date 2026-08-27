# Plan 018 V4-0 isolated harness recipe (corrected)

**Corrected:** 2026-08-11 after maintainer stop — do **not** hand-create or `docker cp`
`baml_client/`. That tree is **generated** (`baml-cli generate --from dmac_assistant/baml_src`
in a throwaway container when a fresh checkout needs it; path is `dmac_assistant/baml_src`,
**not** `…/router/baml_src`). See handoff
`2026-07-29-crashed-sessions-reconstructed-…` and `OPS-TESTING-HARNESSES.md`.

**Authoritative runbooks (read these; do not invent mounts):**

| Lane | Authority | Command shape |
|---|---|---|
| Host hermetic (no DB) | `DEPLOYMENT.md` §7 + `OPS-TESTING-HARNESSES.md` §3.1 | `PYTHONPATH="$PWD:$PWD/dmac_assistant/src" uv run --no-project --with pytest --with orjson --with 'pydantic>=2.13' --with 'baml-py==0.222.0' …` (+ §3.1 extras/`--ignore`s as documented). Repo root = checkout under test. |
| In-container DB clean | `DEPLOYMENT.md` §7 / §3.2 | `docker exec -w /app nextseek uv run --no-sync python -m pytest …` — tests **baked image**, not the plan-018 worktree. |
| Source-tree `host_only` | `DEPLOYMENT.md` §7 / §3.3 | `docker run --rm -v <WRITABLE checkout>:/repo -w /repo -v /usr/bin/docker:/usr/local/bin/docker:ro -v /usr/libexec/docker/cli-plugins:/usr/local/lib/docker/cli-plugins:ro` (+ sock mount per §3.3 caveat) `nextseek-nextseek:latest uv run --project /app --no-sync python -m pytest -m host_only …` — **`--project /app` keeps uv on the image env**. |

**V4-0 requirement:** baseline must exercise the **approved worktree**
`/home/taishajo/work/NExtSEEK-plan018` @ `6881b6a8`, not the live container’s `/app` alone.
Prefer §3.1 host hermetic and/or §3.3 `host_only` against that worktree (writable). Use §3.2 only
as a separate “deployed image” observation, never as worktree proof.

**Hard refuses:** inventing `baml_client/` by copying from an image; overlaying the live
`nextseek` container as if it were this worktree; paid/realstack without approval.

## Prior failed attempts (do not repeat)

Attempts 1–6 under `evidence/baseline-hermetic.*` used wrong environments (host venv without
Django; throwaway container with hand-seeded `baml_client`; incomplete docker/git mounts).
Those results are **not** a baseline pass/fail for the approved base. Gate status: **void /
superseded by this correction**.

## Next baseline run (only after maintainer go)

1. Confirm `baml_client/` is absent or produced solely by `baml-cli generate --from dmac_assistant/baml_src`.
2. Run §3.1 (and optionally §3.3) from `/home/taishajo/work/NExtSEEK-plan018` exactly as documented.
3. Write a new sidecar; do not overwrite the correction note above.
