# Working in `nextseek_api/eval/`

## Invariants

Each of these is load-bearing because this package can steer live traffic. Breaking one
is a spend, evidence or routing regression, not a refactor.

- **Observational evidence must never reach the paired fitter.** The type guard at
  `nextseek_api/eval/fit/fit_boundary.py:28-47` rejects an online row, a mixed batch and
  a forged discriminator alike. Fitting live-traffic rows would let the router's own past
  choices supply the evidence for its future ones, and the resulting posterior would look
  exactly like an experimental one.
- **A publish must carry approved paired provenance, and policy-selected provenance is
  refused outright.** `nextseek_api/eval/fit/fit_boundary.py:113-126` demands a
  `paired_run_id`, a content hash and registry approval, and
  `nextseek_api/eval/fit/fit_boundary.py:120-121` rejects any `route_source` other than
  `forced`. Relaxing this publishes a generation whose evidence was chosen by the thing
  it is about to control.
- **Swapping the active generation is a compare-and-swap, not a write.**
  `nextseek_api/eval/generation_store.py:349-350` refuses a stale expected hash under a
  row lock taken at `nextseek_api/eval/generation_store.py:309-312`. Dropping the check
  lets two concurrent activators lose one update, and production routing then follows a
  generation nobody chose.
- **Creating or publishing a generation directly is disabled on purpose.** Both entry
  points are stubs that raise — `nextseek_api/eval/generation_store.py:226-235` and
  `nextseek_api/eval/generation_store.py:238-242` — and name the authenticated publisher
  as the only route in. Making either one work again reopens a path that writes a
  posterior with no evidence identity behind it.
- **An actor whose name begins with `live:` may not publish or activate.**
  `nextseek_api/eval/generation_store.py:216-218` and
  `nextseek_api/eval/generation_store.py:221-223` raise on that prefix. Removing the
  guard lets an automated caller flip production routing with no maintainer in the loop.
- **The delivery is re-authenticated from disk after preparation and before any durable
  write.** `nextseek_api/eval/human_grade_fit.py:941-954` rebuilds the fit from the
  original path and compares hashes, so a prepared object's own hashes are never the
  authority. Trusting the prepared copy turns a swapped file between preparation and
  publish into an accepted forgery.
- **Every provider call goes through the reservation gate.** A call that skips
  `nextseek_api/eval/provider_gate.py:33-67` spends outside the approved cap and leaves
  the reconciliation unable to balance; the AST sweep at
  `nextseek_api/eval/seam_inventory.py:159-165` exists to find exactly that, so adding an
  ungated call site makes that sweep report a defect.
- **A default schedule may not enter the paid lane.**
  `nextseek_api/eval/paid_run_schedule.py:13-17` raises whether or not the caller passes
  the flag. Wiring a Celery beat straight to a judging run would bill the account with no
  approved manifest behind the charge.
- **Pair identity has to survive into fit input.** `nextseek_api/eval/fit/v14/pair_rows.py:23-24`
  refuses a route-family aggregate by type, because an aggregate has already discarded
  which arm answered which question. Feeding one in silently converts a paired design
  into an unpaired comparison.
- **`nextseek_api/eval/enums.py:1` is a vendored surface pinned to an upstream commit**
  and says so on its first line. Editing it in place makes the next upstream sync a
  silent conflict rather than a merge.

## Landmines

- **Three non-test modules hardcode another developer's absolute home directory.**
  `nextseek_api/eval/judge_human_compare.py:29-34` uses it for both CLI defaults,
  `nextseek_api/eval/v4_3_verifier.py:39-40` for the replay source, and
  `nextseek_api/eval/artifact_validity_proposal.py:71` resolves a `Downloads` folder
  under whoever is running it. On any other machine these produce a missing-file error
  that reads like a code fault, and the CLI `--help` output still advertises the default
  as if it were valid.
- **Running the artifact proposal overwrites committed data in the source tree.** Its
  output directory is the package directory itself
  (`nextseek_api/eval/artifact_validity_proposal.py:72`) and it writes both CSVs there
  (`nextseek_api/eval/artifact_validity_proposal.py:407-411`). The SHA-256 of the
  committed `artifact_validity_set3_final.csv` matches the digest pinned at
  `nextseek_api/eval/human_grade_fit.py:110` — checked with `sha256sum` on 2026-09-03 —
  so a regenerated copy fails authentication on the next fit rather than being noticed as
  a diff.
- **NumPyro, JAX and ArviZ are undeclared dependencies.** Grepping `pyproject.toml`,
  `uv.lock` and the root `Dockerfile` for `numpyro`, `jax` or `arviz` returns nothing,
  and probing the running app image on 2026-09-03 found all three absent while `numpy`,
  `scipy`, `polars`, `fastexcel` and `orjson` were present. The imports are lazy
  (`nextseek_api/eval/fit/v14/quality_model.py:145-147`), so the package imports fine and
  only the authoritative MCMC path dies, at call time, inside the app container.
- **The vendored HiBayes runners cannot import in any image this repo builds.** Their
  entry points import the `hibayes` library at module scope
  (`nextseek_api/eval/fit/vendor/hibayes_artifact_validity/run_hibayes.py:44-47`), and a
  case-insensitive grep for `hibayes` across every `Dockerfile`, `*.toml`, `*.lock` and
  `requirements*.txt` in the repo installs it in none of the seven Dockerfiles that
  `find . -name 'Dockerfile*'` enumerated on 2026-09-03. The only hit is a comment at
  `docker/cc-runtime/pyproject.toml:76-82` placing the dependency in an image whose name
  a repo-wide grep finds nowhere but on that same line. Treat those runners as
  reference material until that image is reconstructed.
- **Two modules name themselves proposals and must not be imported as product code.**
  `nextseek_api/eval/artifact_validity_proposal.py:5-6` names the module path reserved
  for the real implementation and says plainly not to import it, and
  `nextseek_api/eval/router_models_proposal.py:3-5` calls itself smoke-tested but wired
  into nothing. Wiring either in ships a hardcoded path and an unowned contract into the
  request path.
- **`nextseek_api/eval/seam_inventory.py:19-21` computes the repository root by walking
  two parents up, at module scope.** Move this package one level and the AST sweep
  silently scans the wrong tree instead of failing, so the paid-seam gate starts passing
  because it found nothing.
- **Collection anywhere under `nextseek_api/` needs Django already configured.**
  `nextseek_api/conftest.py:3` imports `django.contrib.auth.models` at module scope, so a
  host-side `pytest` over this package errors during collection before any test runs.
- **`--no-migrations` is what makes the two MySQL modules fail, not a code defect.**
  Under SQLite with migrations off they raise `no such table: eval_approved_run_manifest`.
  Nothing in this package creates that table: the model declares it at
  `nextseek_api/assistant/models_db.py:223` and the parent app's migration builds it at
  `nextseek_api/migrations/0014_generation_activation_and_reservation.py:79`. Reaching for
  `--create-db` over the whole tree instead of using their own lane is the slow wrong turn.
- **The router consults this package only behind an off-by-default flag.**
  `dmac/settings.py:15-18` reads it from the environment and treats anything but
  `1/true/yes/on` as off. A store change that looks inert locally becomes a live routing
  change on any box where that variable is set.
- See `DEPLOYMENT.md:78` for the golden rules governing any box this package is
  activated on.

## Test command

The package's own suite, in a throwaway container over a writable copy of the worktree,
using the app image's interpreter directly rather than `uv run`:

```
docker run --rm -v /path/to/writable/copy:/work -w /work \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint /app/.venv/bin/python nextseek-nextseek:latest \
  -m pytest nextseek_api/eval/tests/ -p no:cacheprovider --no-migrations -q
```

Measured 2026-09-03 — 368 passed, 34 failed, 1 skipped, 17 errors, 17.91s.

## See also

- See `nextseek_api/eval/README.md` for what each module does, the two edge directions,
  and why every non-passing test above is environmental.
- See `nextseek_api/cc_assistant/CLAUDE.md` for the traps at the other end of the cycle
  this package is half of.
- See `nextseek_api/assistant/CLAUDE.md` for the model module that owns these tables.
- See `nextseek_api/CLAUDE.md` for the app-wide traps that apply here too.
