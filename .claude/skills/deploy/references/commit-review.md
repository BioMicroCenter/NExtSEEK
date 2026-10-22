# The commit review and the proposed CI checks

Every launch reviews what it ships before it builds. The question for each change: which test
covers it, and which post-rebuild check proves it is live on this box? Where nothing proves it,
propose the check. A launch runs exactly its brief's steps, so this step proposes and never writes
code.

## 1. List the range

```bash
git -C <workstation_repo> fetch -q origin          # the box's HEAD and the sha must be local
uv run $K/launch.py commits --brief $D/brief.json      # base = the box's HEAD from preflight.json
```

It lists the range (box HEAD to expected sha). Over 50 commits: the latest 50, and the count
skipped. For every commit it records the files, the images they need and the flags (nginx,
compose, migration, seed). It then compares the whole range with the brief: an image the range
needs that the brief does not name, or an unannounced flag, is exit 5 before any build. A flagged path the operator accepted goes in the brief's `acknowledged_flags` (`{path, reason}`); it is then reported as a warning, not a stop. It writes
`$D/commit-review-form.json` with four empty slots for you.

## 2. Fill the four slots

Read each commit's diff (`git -C <workstation_repo> show --stat <sha>`, then the diff where
the subject is not enough). Group commits into behaviour changes: one row per thing a user,
operator or test could observe.

`behaviour_changes[]`

| Field | Values |
|---|---|
| `id` | `BC1`, `BC2`, ... |
| `summary` | the change, in plain words |
| `commits` | short shas from the list (8 characters) |
| `unit_tests` | `{path, lane}`; `path` is `file` or `file::test`. `lane`: `blocking` (can fail GitHub CI: `BLOCKING_GLOBS`), `informational` (a ci-pytest lane that cannot fail CI), `harness_host` (`NessieAI/tests/nessie_tests/tests`, in no CI), `container` (app-image lane), `no_ci` (vitest, cc-runtime units) |
| `live_check` | the strongest post-rebuild check that exists, `{kind, ref, proves}`, or `null` when there is none. `kind`: `stack_health` (a line `rebuild` and `ci` print), `smoke` (a `ci/smoke` test that fails CI), `runner_check` (a line in this runner's `checks.log`, a live marker), `nessie_case` (a paid turn), `none`. Write in `proves` how far it goes: `app image code` proves the code is in the container, not that the behaviour works |
| `status` | `live` (a live check proves the behaviour itself on the box), `unit_only` (tested; at most a presence check live), `needs_paid_turn` (only a model turn can show it), `needs_fault_injection` (only an induced failure shows it: a timeout, a 503, a refused model id), `untested` (no unit test) |
| `proposals` | ids of proposed checks that close the gap |
| `accepted_gap` | instead of a proposal, when the gap is fine (say why) |
| `covers_skipped` | short shas of commits past the latest 50 that this change brings in through a listed merge (they still ship) |

A change that is not `live` needs a proposal or an `accepted_gap`.

**Commits past the latest 50 still ship.** When the range is longer than 50, the older commits
usually arrive through merge commits that are listed. Give each such merge a behaviour-change row
for what its branch brings (name the merge in `commits` and the branch's own commits in
`covers_skipped`), rather than `merge_commit`. `merge_commit` is only for a merge that brings
nothing the other rows do not already cover.

**CI and startup commits.** A commit under `ci/` or `startup/` that adds or changes a check is a
behaviour change: an operator sees its line or its red. List it with its own tests. Use
`ci_or_startup_only` for a CI or startup refactor nobody can observe.

`no_behaviour_change[]`: `{commits, reason, note}` for commits nobody can observe. `reason`:
`docs`, `tests_only`, `ci_or_startup_only`, `refactor`, `merge_commit`, `data_or_fixture`,
`revert_pair`, `other`. Every listed commit must be in exactly one behaviour change or here.

`proposed_checks[]`

| Field | Values |
|---|---|
| `id` | `P1`, `P2`, ... |
| `kind` | `stack_health`, `smoke`, `unit`, `blocking_glob` (move a test into `BLOCKING_GLOBS`), `ci_lane` (run a suite CI skips), `nessie_case`, `runner_check` |
| `where` | the file the check would live in (`startup/steps/deploy_checks.py`, `ci/smoke/test_deploy_live.py`, `ci/gate/...`) |
| `proves`, `fails_when` | what a green result shows; the exact condition that turns it red |
| `cost` | `free` or `paid` |
| `priority` | `before_next_launch`, `soon`, `later` |
| `covers` | the `BC` ids it closes |

`summary`: two or three sentences: how many behaviour changes, how many are proven live, the gaps
that matter most.

The worked example is a local CI-coverage note (rebuild 2: a
coverage matrix per change, the checks it added to `startup/steps/deploy_checks.py` and
`ci/smoke/test_deploy_live.py`, and the unit-test gaps it sent to the owners).
`examples/commit-review-filled.json` is a valid filled form from that range (16 behaviour changes,
13 proposed checks).

## 3. Validate, render, deliver

```bash
uv run $K/launch.py review --brief $D/brief.json --form $D/commit-review-form.json
```

It refuses (exit 2) an unaccounted commit, an unknown sha, a `live` row without a live check, a gap
with neither a proposal nor an accepted gap, a proposal that covers nothing, duplicate ids. It
writes `$D/commit-review.json` and `$D/commit-review.md`, and prints where the section goes:

- `DELIVER: SendMessage to '<parent>' ...`: send the full text of `commit-review.md` to that
  agent or session now, before the runner starts, so they can act on it during the build.
- `DELIVER: no parent ...`: nothing to send; the section is rendered into your own
  `LAUNCH-REPORT.md` (the report step embeds `commit-review.json` either way).
