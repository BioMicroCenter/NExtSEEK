# Nessie questions: cases, cost, running, grading

The point of the Nessie step is to show the change working in real replies. The harness pass
rate is a hint. The verdict comes from reading every reply and the task rows behind it.

## 1. Cost, time, and the announcement

- Every `manage.py nessie --tier full` turn is paid. `--tier route` is cheaper, not free.
- Container-CC turns cost about $0.25 to $0.50 each and take 60 to 150 s (300 s when Bedrock
  returns 503s). NExtSEEK (NS) turns take 30 to 60 s and show `$0.00` because they are not
  priced, not because they are free: report them as "unpriced".
- Estimate: put the turns likely to go to Container-CC in the brief form, per cases file
  (`cc_turns_estimate`). The brief script multiplies by $0.50, adds $0.60 for the CI Nessie lane,
  and refuses an estimate over `paid.budget_usd` (exit 5). Since 2026-09-23 follow-up turns and
  open-ended project summaries route to Container-CC, and later turns in a chat that went to CC tend
  to stay there; the follow-up split (`NESSIE_FOLLOWUP_ROUTING`) keeps some on NS. Count with the
  upper bound anyway.
- Time: about 1.2 minutes per case (41 cases took 25 min; 67 cases 70 min); the script adds it to the
  window check.
- Before starting, one line: "Starting the paid Nessie run on <box>: <N> cases / <T> turns, about <C>
  CC turns, estimate $<X> (budget $<B>), about <M> min." The numbers are in `brief.json`.

## 2. The cases file

The brief gives either a repo file (for example
`NessieAI/tests/nessie_tests/probes/probe-2026-09-23-followups-to-cc.json`) or inline questions.

**A repo file** is already in the image at `/app/<same path>` for the deployed sha. Take it
from that sha into your scratchpad anyway, so the run uses exactly what you read, and re-pin it
if it has a `_measure` block (section 3):
`git -C <workstation_repo> show <expected_sha>:NessieAI/tests/nessie_tests/probes/<file> > $S/launch-$TAG-cases-1.json`.

**Inline questions**: write this shape. Each variant is one fresh chat; its turns run in order
in that chat (use a `seed` turn, then `main`, for a follow-up).

```json
{
  "_name": "launch-<TAG>: <one line on the change>",
  "_why": "<the brief's change summary>",
  "include_ids": [],
  "families": {
    "launch": {
      "description": "Questions from the launch brief",
      "variants": [
        {
          "id": "launch.q1_samples_in_srp",
          "family": "launch",
          "name": "How many samples are in the SRP project?",
          "_why": "<what the brief says it should show>",
          "tags": ["nessie", "probe", "full"],
          "requires_env": [],
          "turns": [
            {"label": "main", "query": "How many samples are in the SRP project?",
             "pass_criteria": [
               {"field": "last_reply", "op": "nonempty", "value": null}
             ]}
          ]
        }
      ]
    }
  }
}
```

- `include_ids` may list existing corpus case ids (read from the corpus baked into the image).
- Criteria ops seen in the probes: `nonempty`, `eq`, `matches_re`. A number that must appear:
  `(?<![\w.,/-])892(?![\w]|[.,]\d|[-/]\d)`. A phrase that must NOT appear:
  `(?is)\A(?!.*(could not apply|Reason from parser))`. Fields include `last_reply`, `route`
  (`nextseek_query`, `container_cc`, ...), `route_source`, `cc_trace_text`.
- Keep criteria minimal. You grade by reading, and a wrong criterion costs a triage cycle
  (four of ten reds on 2026-09-22 were the probe's own criteria).
- Validate it with the harness's own loader before paying (free, offline, from origin/dev):

```bash
rm -rf $S/nt && mkdir -p $S/nt && git -C <workstation_repo> archive origin/dev \
  NessieAI/__init__.py NessieAI/paths.py NessieAI/tests | tar -x -C $S/nt
(cd $S/nt && uv run --no-project --with pydantic --with requests --with beautifulsoup4 --with orjson python -c "
from NessieAI.tests.nessie_tests.corpus import load_case_file
inc, v = load_case_file('$S/launch-$TAG-cases-1.json')
print('include_ids', inc); print([(x.id, x.family, len(x.turns)) for x in v])")
```

  When origin/dev carries the case-file checker (branch feat/skills-json-forms), run it instead:
  it runs the same loader and adds the checks that used to be done by eye (unique ids, families
  that exist in the corpus, `include_ids` that resolve, every `_measure` key a case whose number
  pattern matches its `locals`, the writing families on prod, and with `--diverse` no repeated
  question):

```bash
(cd $S/nt && uv run --no-project --with pydantic --with requests --with beautifulsoup4 --with orjson \
  python -m NessieAI.tests.nessie_tests.case_file_check $S/launch-$TAG-cases-1.json --instance dev)
```

  Copying a block out of `corpus.json` brings retired cases and `known_fail` tags with it, and
  they run and bill. Name corpus cases in `include_ids` instead.
- On prod: no case from `entity_write`, `pipeline_launch`, `pipeline_output_reingest`,
  `batch_upload_preparation`, or any `write.*` id.

## 3. Re-pin numbers to the box's own graph

Every number in a probe was measured on some other graph. Dev (TCGA, no real projects) and
prod (real projects, no TCGA) differ from each other and from the workstation. If the file has
a `_measure` block, re-pin it. The pin script is standalone Python, so everything but the one
read-only query runs in your scratchpad and the box's checkout stays clean:

```bash
G="git -C <workstation_repo> show origin/dev:NessieAI/tests/nessie_tests"
$G/scripts/pin_probe_truths.py > $S/pin_probe_truths.py
$G/probes/<probe>.json > $S/probe.json
python3 $S/pin_probe_truths.py --emit-cypher $S/probe.json > $S/launch-$TAG-pin.cypher
```

On the box, one read-only script through the door. Write `$S/pin.sh` with the Cypher inlined (so
no file copy is needed), then run it with `launch.py ssh --purpose read`:

```bash
{ echo 'cd <repo>'
  echo "docker compose exec -T neo4j sh -c 'cypher-shell --access-mode read -u neo4j -p \"\${NEO4J_AUTH#neo4j/}\" --format plain' <<'CYPHER'"
  cat $S/launch-$TAG-pin.cypher; echo CYPHER; } > $S/pin.sh
uv run $K/launch.py ssh --brief $D/brief.json --purpose read --script $S/pin.sh --out $S/pin.out
```

Build `$S/measured.json` from it. When the harness at origin/dev has `pin_probe_truths.py
--parse-output` (branch feat/skills-json-forms), let it do this: it reads the labelled output,
writes `{case_id: number}` or the numbers in order, and lists every case that measured 0 (not a
truth: drop that case) or returned a different count of numbers than its `_measure` block holds.
Otherwise build it by hand, as the probe's `_instance_warning` says. Then:

```bash
python3 $S/pin_probe_truths.py --pin $S/probe.json --from $S/measured.json --out $S/launch-$TAG-cases-1.json
```

`$NEO4J_PASSWORD` is unset on the host shell: always read the password inside the neo4j
container, as above. A case with no `_measure` entry keeps its old numbers: check those by hand.

## 4. Running

The rendered runner's Nessie block does this, as `CI_WRITE_USER` from `~/.config/nextseek/ci.env`,
once per cases file in the brief:

```bash
docker compose exec -T -e CI_WRITE_PASS nextseek uv run manage.py nessie --tier full \
  --cases /app/runs/<file> --user "$CI_WRITE_USER" --password-env CI_WRITE_PASS \
  --out /app/runs/<run> </dev/null > <log> 2>&1
```

Optional flags, only from the brief form (`nessie.force_route`, `nessie.pace_s`): `--force-route
ns|cc` (forces every turn; the route criteria are stripped), `--pace <seconds>` between turns. The paid forced-arm modes (`--arms`,
`--force-parser-mode`, `--prompt-variant`) are for experiments, not launches.

The exit code is 1 when any non-`known_fail` case failed. `/app/runs` is lost on the next
recreate, so the runner copies each run dir out to `~/launch-<TAG>/` and `~/backups/`.

## 5. Pulling the evidence

1. The launch folder (`launch.py ssh --purpose pull`): logs, `checks.log`, each run's
   `manifest.json` and `report.html`. `launch.py judge` puts each case's harness line (status,
   route, turn seconds, cost from the running total) into `facts.json` and one row per case into
   `report-form.json`.
2. The task rows, read-only, with the output skill's fetcher. It is standalone Python: take it
   from origin/dev, never edit the shared checkout.

```bash
git -C <workstation_repo> show origin/dev:.claude/skills/nessie-run-review/scripts/fetch_run.py > $S/fetch_run.py
git -C <workstation_repo> show origin/dev:NessieAI/tests/nessie_tests/turn_cost.py > $S/turn_cost.py   # beside it, or turn_cost stays empty
python3 $S/fetch_run.py --instance <dev|prod> --out $D/pull-<run> --raw \
  --manifest /app/runs/<run>/manifest.json --since "<UTC start>" --until "<UTC end>"
```

`--since/--until` are the app clock (UTC), from the status file's `NESSIE_START` and
`NESSIE_EXIT` lines. It writes `turns.json` (route, route source, parser mode, the Cypher or API
call, error, CC model and cost per turn; `cost` is the engine's total, `turn_cost` the router plus engine sum, filled only when `turn_cost.py` sits beside the fetcher: if the run prints "not priced", copy it and pull again) and, with `--raw`, `tasks/<id>.json`. A turn with
`src: "pipeline"` means the BAML router was bypassed: report it.

3. The replies and failed criteria straight from the manifest:

```bash
python3 - $D/launch-<TAG>/<run>/manifest.json <<'PY'
import json, sys
m = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "manifest.json"))
for e in m["entries"]:
    print("=====", e["id"], e["status"], e.get("route"))
    seen = set()
    for o in e["observations"]:
        if o["field"] == "last_reply" and o["turn"] not in seen:
            seen.add(o["turn"]); print(f"  [{o['turn']}]", str(o["observed"]).split("**Debug info**")[0][:600].replace("\n", " "))
        if not o["passed"] and not o.get("skipped"):
            print("    FAIL", o["turn"], o["field"], str(o["expected"])[:80], "| got", str(o["observed"])[:80])
PY
```

## 6. Grading

For every question the brief asked, read the whole reply, then fill its row in `report-form.json`
(`nessie.questions[]`: `question`, `expected`, `reply_key_line`, `verdict`, `evidence`). The report
script refuses a row without a verdict or evidence.

| Verdict | Use when |
|---|---|
| `pass` | the reply says what the brief said it should, and the number checks out on this box |
| `real` | a genuine product defect: wrong number, false caveat, leaked internals, crash, wrong route by the operator's rules |
| `drift` | the criterion or answer key is stale; the reply is right |
| `policy` | differs from the expectation by design; the operator must decide |
| `masked` | the harness passed it but the reply is wrong (a regex satisfied by the question's own words) |
| `notrun` | `status: error` with `outage: true`, a provider outage, or never executed |

Checks that decide most verdicts:

- **Content versus plumbing.** A red on `route`, `route_source`, `parser_plan.mode`, `api_plan`
  or `api_ok` with a correct reply is plumbing. Look at the reply first.
- **Numbers on this box.** Before calling a number wrong, measure it on the box's own graph with
  one read-only query (`cypher-shell --access-mode read`, as in section 3). Corpus keys are
  production numbers; dev differs.
- **Container-CC stalls near 300 s** are usually Bedrock 503s:
  `docker logs --since <start>Z --until <end>Z dmac-bedrock-proxy 2>&1 | grep -c ' 503 '`.
  Grade the question `notrun` for the change under test, and list the 503 count under
  Anomalies: Container-CC has no provider fallback yet, so users saw it too. The proxy's 404s
  and 403s are Claude Code's model discovery and harmless.
- **CC follow-ups on dev** failing with `VALIDATION: bad request` on `aggregate` on the sidecar road:
  the sidecar is stale (dev.md gotchas), not the product.
- **Files served** by a turn are in its task row's `artifacts` / `files`, not in the harness
  report, which hides the download buttons.
- **Exact text:** `fetch_run.py` garbles non-ASCII. Quote from the manifest, or say the quote is
  approximate.
- The six-verdict vocabulary and the deeper SQL patterns are the output skill's:
  `.claude/skills/nessie-run-review/SKILL.md` and `REFERENCE.md` (the nessie-run-review
  skill). Use it for an HTML review if the brief asks for one.
