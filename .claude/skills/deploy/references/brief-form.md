# The brief form

Turn the supervisor's brief into this JSON form, save it as `$S/brief-form.json`, and run
`uv run $K/launch.py brief --form $S/brief-form.json`. The script validates it (pydantic, unknown
keys refused), applies the stop rules in `.claude/skills/deploy/scripts/rules.py`, and writes `<launch dir>/brief.json`,
the only brief every later step reads. Copy `examples/brief-dev-rebuild.json` or
`examples/brief-dev-nessie.json` and edit.

Write down only what the brief says. Where the brief is silent, leave the field at its default:
the script decides the default, not you.

## Fields

| Field | Required | Default | What it holds |
|---|---|---|---|
| `instance` | yes | | `dev`, `prod` or `local` |
| `expected_sha` | yes | | 7 to 40 hex; must be origin/dev (the preflight proves it) |
| `change` | yes | | one paragraph: what the range ships |
| `parent` | yes, may be `null` | | the agent or session name that spawned this launch (it gets the commit review and the report); `null` when you were not spawned by one |
| `images` | no | `null` | subset of `bedrock-proxy`, `app`, `cc-agent`, `nextseek-sidecar`; `null` = derive from the range (the commits step prints the result: report it before building) |
| `live_markers` | no | `[]` | `{pattern, file, container, proves}`: a fixed string the change added, the file inside the container that holds it, what it proves. None given: pick one from the diff and say you picked it |
| `static_changed` | no | `null` | informational: collectstatic runs after every app rebuild anyway |
| `ci` | no | `true` | `./startup.sh ci --no-nessie` after the rebuilds |
| `ci_nessie_lane` | no | `false` | drop `--no-nessie` (paid; never on prod) |
| `nessie` | no | `null` | `{cases: [{file, cc_turns_estimate}], force_route, pace_s}`: each cases file (a local path) and how many of its turns will likely go to Container-CC. A file whose name ends `-member.json` runs as the box's non-admin smoke login (`CI_SMOKE_USER`), every other file as the admin login (`CI_WRITE_USER`): nessie-questions.md section 4 |
| `paid` | with any Nessie | `null` | `{approved, budget_usd, approved_by}` (`approved_by` in the approver's words) |
| `prod_nessie` | no | `false` | the explicit yes that allows Nessie on prod |
| `allowed_extras` | no | `[]` | only from: `seek restart ok`, `nginx recreate ok`, `db backup ok`, `bedrock-proxy rebuild ok`, `compose recreate ok` |
| `ruled_out` | no | `[]` | free text: what the operator forbade |
| `continue_on_failure` | no | `[]` | components whose failed build keeps the old image and lets the run go on to CI (the operator's call, for example a risky base-image bump) |
| `migrations_expected`, `nginx_change_expected`, `compose_change_expected` | no | `false` | set only when the brief names the migration, the nginx.conf change or the compose change |
| `local_ruling` | no | `null` | on `local` only: the operator's words that allow a rebuild or CI here, quoted |
| `acknowledged_flags` | no | `[]` | `{path, reason}`: a path the `commits` step flags (for example a seed dump under `startup/seed/`) that the operator accepts, in their words. `path` is exact, as `commits` lists it. The step still reports the flag as a warning; an acknowledged path no longer stops the launch. A path in the range that is not listed still stops it |
| `waivers` | no | `[]` | `{check, reason}`: a preflight check the operator explicitly accepted (for example `oom` for an old SEEK OOM), in their words |
| `tag` | no | UTC now | `YYYYMMDD-HHMM`; names the launch folder, the tmux session and the files on the box |
| `start_utc` | no | now | a planned start, for the window check (`2026-09-26T13:00:00Z`) |

## What a refusal writes

The script prints every problem at once, the stop rules and the window together (exit 5 when any
stop rule fired, else 6). It still writes `brief.json`, with `"verdict": "stop"` or `"window"` and
the problems, so the stop can be reported with `report --no-facts`; every other step refuses a
brief whose verdict is not `ok`. Fix the form and rerun `brief --force`.

## The stop rules (exit 5, the whole list printed)

- `local` with any image, CI or Nessie and no `local_ruling`.
- `prod_nessie` on a non-prod brief; Nessie on prod without `prod_nessie`; the CI Nessie lane on prod.
- Any Nessie without `paid.approved` and a budget above 0; the estimate (CC turns x $0.50, plus
  $0.60 for the CI lane) over the budget.
- The same item both allowed and ruled out.
- `bedrock-proxy` in `images` without `bedrock-proxy rebuild ok`.
- A prod migration without `db backup ok`.
- `continue_on_failure` naming a component that is not rebuilt; a brief that asks for nothing.
- A waiver for an unknown check, or for one that decides what ships (`complete`, `branch`,
  `expected`, `origin_match`, `ff`, `busy`, `fetch`).
- A cases file that is missing or not JSON; on prod, any case from `entity_write`,
  `pipeline_launch`, `pipeline_output_reingest`, `batch_upload_preparation` or a `write.*` id.

Later steps add their own: the preflight table (`launch.py preflight`), and the range against the
brief (`launch.py commits`: an image the range needs that the brief does not name, or an nginx,
compose, migration or seed change the brief did not name).

## The window (exit 6)

On dev nothing heavy may overlap 04:00Z-12:00Z (the hung nightly dump holds a table lock on every
dmac table) or 01:45Z-02:45Z (the nightly graph sync). The script adds up the brief's steps
(app 15 min, cc-agent 5, sidecar 3, proxy 3, collectstatic 1, labs 2, CI 13, Nessie 1.2 per case)
and refuses a start whose run would touch either. It prints the next allowed start. The runner
checks the clock again on the box, and `ssh --purpose start` checks it a third time.

## Warnings (exit 0, printed)

No live marker; a cases file with a `_measure` block (re-pin it first); an nginx change without
the recreate allowed; CI off.
