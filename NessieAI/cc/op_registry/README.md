# `NessieAI/cc/op_registry/`

## What this is

The registry of every operation the Container-CC agent may call: one `OpSpec` row per op, and the
surfaces derived from those rows. `ops.py` is the source of truth; everything else here is
generated from it or checks it.

## Surface

| File | What it does |
|---|---|
| `models.py` | the `OpSpec` and `OpList` models (strict pydantic) |
| `ops.py` | `OPS`, the registration source of truth: one row per op with its safety and per-op gate fields |
| `export.py` | renders `OPS` to the committed `ops.json`; `--write` regenerates it, `--check` fails on drift |
| `ops.json` | the generated export; also baked into the plugin's `context/` |
| `derive.py`, `routes.py`, `ns_capabilities.py` | build the derived surfaces (route examples, the NS capability text) from the rows |
| `plugin_identity.py`, `install_oracle.py` | the plugin identity and the check that what is installed on disk matches the rows |
| `paired_evidence.py`, `route_example_evidence.json` | evidence that ties route examples to the harness and e2e catalog cases |

Never edit `ops.json` or the plugin's `plugin.json` by hand. The executable shims live in
`NessieAI/docker/cc-runtime/build_context/plugins/nextseek/bin/`.

## Running and testing

Add or change an op only through the `/add-cc-op` skill (`.claude/skills/add-cc-op/SKILL.md`). Its
export steps are:

```bash
python -m NessieAI.cc.op_registry.export --write --root <repo>
python -m NessieAI.cc.op_registry.export --check --root <repo>
```

The registry's tests are in `NessieAI/tests/cc/`; lanes are in `NessieAI/tests/README.md`.

## Depends on / depended on by

- Read by `NessieAI/build_tools/gen_op_surfaces/`, which regenerates the marked blocks in the container `CLAUDE.md` and the Dockerfile.
- Context: `NessieAI/cc/README.md` (the engine that spawns the agent) and `NessieAI/docker/README.md` (the image).
