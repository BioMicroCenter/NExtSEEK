# output-skill

Turn a `nessie_tests` run into a reviewable HTML report where every failure carries
its expected vs observed values and a verdict.

Hand this directory to an agent and point it at **`SKILL.md`**.

```
output-skill/
├── SKILL.md                    START HERE. Workflow + the gotchas that cause wrong calls.
├── REFERENCE.md                Dev box access, SQL patterns, the criterion field-alias
│                               table, run-root layout, routing, known state.
├── scripts/
│   ├── fetch_run.py            Read-only pull of manifest + per-turn evidence off the dev box.
│   └── build_report.py         Join run data + your triage.json -> report.html
├── templates/
│   └── report.html.tpl         The page. Fully data-driven; no run specifics baked in.
└── examples/
    ├── triage.json             A complete worked triage (the 2026-07-24 run).
    └── run-2026-07-24/         That run's manifest.json + turns.json.
```

Regenerate the worked example end to end, no dev box needed:

```bash
python scripts/build_report.py \
    --run examples/run-2026-07-24 \
    --repo /path/to/dev-v3-merge \
    --triage examples/triage.json \
    --out /tmp/report.html
```

Expected: `cases 44  verdicts {'pass': 29, 'masked': 2, 'drift': 4, 'real': 5, 'policy': 3, 'notrun': 1}`

The one thing to internalise before triaging: **the manifest records criterion
names, never observed values.** Recover the values from `assistant_query_task`
rather than re-running cases. See SKILL.md.

No credentials live in this directory. The MySQL password is read from the
container environment at query time.
