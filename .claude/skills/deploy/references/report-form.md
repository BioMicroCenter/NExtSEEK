# The report form

`launch.py judge` reads the pulled evidence and writes two files in the launch folder:

- `facts.json`: everything mechanical. Status lines and their UTC stamps (step timings), every
  rebuild's success line, rollback tag and the sha it was built from, every stack-health red with
  its class, `checks.log` (http, restarts, OOM, labs, refresh marker, the ops road and, on the sidecar road only, the sidecar ops, images, live
  marker counts), the CI result line, counts and failed ids (known or new by the table in
  `.claude/skills/deploy/scripts/rules.py`), the stack-health lines at the top of CI, and each Nessie run's per-case lines.
- `report-form.json`: the report, prefilled from the facts, with `null` wherever judgement is
  needed. You fill only those slots and the three lists, then run
  `uv run $K/launch.py report --brief $D/brief.json`.

## What you fill

| Slot | Values |
|---|---|
| `headline.verdict` | `shipped` (on the sha, every image rebuilt, CI green apart from known reds, every live marker found, no finding), `shipped_with_findings`, `stopped` (the runner or a rule stopped the launch), `failed` |
| `headline.line` | one line after "Launch dev at 1c070c08: " |
| `summary` | two or three sentences: is the box on the sha with every named image rebuilt, is CI green apart from known reds, did the Nessie questions show the change working; the single most important finding |
| `steps[].note` | only where a step needs a word; you may downgrade a status (`ok` to `ok_with_findings`), never upgrade one the facts made `failed` or `stopped` |
| `live_markers[].note` | optional |
| `ci.failures[].first_error`, `caused_by_change` | required for every NEW red (`known: false`): the first `E ` line and `yes`, `no` or `unclear` |
| `nessie.questions[]` | per question: `question`, `expected` (what the brief said it should show), `reply_key_line`, `verdict` (`pass`, `real`, `drift`, `policy`, `masked`, `notrun`; see nessie-questions.md section 6), `evidence` (the number re-measured on this box, the task id, whether a failed criterion was content or plumbing) |
| `nessie.cost_note` | the run's cost against the estimate and the budget; NS turns are "unpriced" when they show $0.00 |
| `findings[]` | `{severity: high|medium|low|info, text, evidence: [file refs]}` |
| `anomalies[]` | same shape: anything unexpected (an OOM, a stale image, a Bedrock 503 burst) |
| `proposals[]` | `{kind: extra_step|ci_rerun|rekey|defect|ci_check|rollback|other, text, why}`: every step you would take next and did not; the supervisor decides |
| `left_behind[]` | prefilled: the tmux session, the box folder and status file, the copied runner files, each rollback tag, each Nessie run dir in `~/backups`, the local launch folder. Set each `disposition` (`keep`, `clean_up`, `running`) and a note; add anything else you left; never drop one (the handoff skill's inventory, for a launch) |

## What the script refuses (exit 2)

- Any `null` judgement slot left; a new CI red without `first_error` and `caused_by_change`; a
  Nessie question without a verdict or evidence.
- A form that contradicts `facts.json`: a failed id dropped or added, a `known` flag flipped, CI
  counts edited, a live marker count changed, a step upgraded past what the facts show.
- `shipped` when the facts carry a stop, a failed or stale image, an unexplained red, a new CI
  red, a red health line in CI, a missing live marker or a runner FINDING; any bad news with empty
  `findings` and `anomalies`; a stopped runner with a headline other than `stopped` or `failed`.
- No `commit-review.json` in the launch folder (do the commit review first).
- A prefilled `left_behind` item dropped.
- An evidence ref that names a file (it has a `/` or ends in .json, .log, .md, .out, .txt, .html,
  .tgz, .jsonl, .sh) that is not in the launch folder, its `launch-<TAG>/` evidence folder, or at
  the path as written. Free-text refs ("task 1241") are not checked.

## What it writes

`launch-report.json` (brief, derived values, the form, the facts and the commit review, in one
file) and `LAUNCH-REPORT.md`, rendered the same way every time: the headline and summary, State,
Steps (with the timings from the status stamps), Live markers, stack-health reds during the
launch, CI, Nessie questions (with verdict counts), Findings, Anomalies, Left behind, Proposals,
and the commit review with its proposed CI checks. Your final message is `LAUNCH-REPORT.md`.

A launch that stopped before any evidence existed (a refused brief, a failed preflight): write the
form by hand from `examples/report-stopped.json` and run `report --brief $D/brief.json --no-facts`.
The refused brief's problems and the preflight's stop rows are rendered into the report for you,
and the checkout line comes from `preflight.json`.
