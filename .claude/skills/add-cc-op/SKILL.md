---
name: add-cc-op
description: >-
  Add a NExtSEEK Container-CC operation the durable way: executable shim,
  runner dispatch (_DISPATCH or _CMDS), OpSpec row with safety and per-op
  gate fields, optional hand-authored server enforcement, plugin tree,
  ops.json export, mechanical surface regeneration, then Audit A / no-write
  checks / focused tests / Task 12 gate. Use when adding, registering, or
  wiring a new nextseek-* bin, plugin op, or CC tool — not a one-place edit.
---

# Add a Container-CC operation

Canonical registration is pydantic `OpSpec` in
`nextseek_api/cc_assistant/op_registry/ops.py` plus a real shim and a real
runner table entry. `plugin.json` and `discover_ops` are **not** the
registration source of truth.

Follow every step. Do not skip export/regeneration and hope CI will invent
the surfaces.

## 1. Add an executable shim using the common runner contract

Copy an existing shim of the same transport:

- viewset / sidecar: `docker/cc-runtime/build_context/plugins/nextseek/bin/nextseek-query`
  (exec `_nextseek_runner.py --agent <runner_key> …`)
- local_subcommand: `docker/cc-runtime/build_context/plugins/nextseek/bin/nextseek-sampletype-attrs`
  (exec `_batch_upload_runner.py <runner_key> "$@"`)

Name it `nextseek-<op>`. Forward required argv flags in OpSpec order. Mark
the file executable.

## 2. Register the exact `_DISPATCH` or `_CMDS` runner_key

- `Backend.dispatch` → `_DISPATCH` in `bin/_nextseek_runner.py`, handler
  named `_dispatch_<runner_key_with_underscores>`
- `Backend.subcmd` → `_CMDS` in `bin/_batch_upload_runner.py`, handler
  named `_cmd_<runner_key_with_underscores>`

The table key must equal `OpSpec.runner_key` exactly.

## 3. Add the NExtSEEK OpSpec row

Append one `OpSpec` through the same `_dispatch` / `_subcmd` constructors
used in production (`ops.py`). Set `transport`, `gate_class`,
`per_op_gate_enabled`, `skill_name` / `skill_row`, and argv explicitly.
Do not invent a parallel catalog or a magic op count.

## 4. Hand-authored server enforcement (only if the transport requires it)

Viewset, sidecar contract/handler/gate, or write-confirm policy changes are
**not** mechanical. Edit `_ws_contract.py` `SIDECAR_OPS`,
`nextseek_api/assistant/granular.py` `_HANDLERS`, and `write_gate.py` only
when that transport needs them, and pause for separate review. A new
viewset op also updates Audit A's viewset membership expectation.

## 5. New plugin: add its manifest-bearing tree

A new plugin needs `.claude-plugin/plugin.json` (identity only — no op
inventory), `bin/` shims, and Dockerfile COPY/PATH membership via the
install oracle. Do not treat `plugin.json` as the op list.

## 6. Export `ops.json`

```
python -m nextseek_api.cc_assistant.op_registry.export --write --root <repo>
python -m nextseek_api.cc_assistant.op_registry.export --check --root <repo>
```

This writes canonical `op_registry/ops.json` and every installed plugin
baked `context/ops.json`.

## 7. Regenerate mechanical surfaces

```
python -m build_tools.gen_op_surfaces --write --root <repo>
python -m build_tools.gen_op_surfaces --check --root <repo>
```

Commands, SKILL matrices, container CLAUDE inventories, Dockerfile
COPY/PATH, Compose additional contexts, baked capabilities, and
`route_capabilities.json` are generated. Do not hand-edit marked blocks.

## 8. Run Audit A, no-write checks, focused tests, and the Task 12 gate

- Audit A: `pytest nextseek_api/cc_assistant/tests/test_op_registry_audit.py`
- No-write: `export --check` and `gen_op_surfaces --check` with the repo
  mounted read-only
- Focused tests for the new op
- Task 12 coverage / JUnit gate (do not lower 95%, no `--omit`, no extra
  ignores)

A newly added operation must **not** require a task-family route example.
Family evidence is Plan 018 routing data, not op registration.
