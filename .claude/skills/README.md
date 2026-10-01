# Project skills

Four skills are committed here. Each is a folder with a `SKILL.md` that Claude Code loads on its
own when the task matches. Everything else under `.claude/` is local and ignored by git.

| Skill | Use it when | What it does | What it calls | Set up locally |
|---|---|---|---|---|
| [`add-cc-op`](add-cc-op/SKILL.md) | Adding or wiring a Container-CC operation (a `nextseek-*` shim or plugin op). Also `/add-cc-op`. | Walks the durable way: the shim, the runner dispatch, the `OpSpec` row with its safety and gate fields, the plugin tree, the `ops.json` export, the generated surfaces, then the audits, the no-write checks and the focused tests. | `NessieAI/cc/` sources (`ops.py` is the source of truth), the surface generators and the Task 12 gate | Nothing |
| [`deploy`](deploy/SKILL.md) | Installing, redeploying, rolling back or verifying a box, or launching a box to a commit that is already on `origin/dev` ("rebuild it at dev"). | Routes to `DEPLOYMENT.md`, holds the hard deployment gates and the `./startup.sh` verb per change, and carries the launch flow: preflight, commit review, rebuild, CI, Nessie questions, graded report. | `DEPLOYMENT.md`, `./startup.sh`, `deploy/scripts/launch.py` and `rules.py`, `deploy/references/` | Create the box config `~/.config/nextseek/boxes.json` from [`deploy/boxes.example.json`](deploy/boxes.example.json) (path override: `NEXTSEEK_BOXES`). Fields: [`deploy/references/boxes.md`](deploy/references/boxes.md). Never commit it. |
| [`nextseek-issues`](nextseek-issues/SKILL.md) | A bug fix is deferred or out of scope, a plan finishes with residuals, or someone asks to file an issue. | Drafts an issue that follows the conventions and asks a person to approve before anything is filed. | `docs/ISSUE-CONVENTIONS.md`, `scripts/validate_issue.py`, `scripts/seed_issue_labels.sh` | The GitHub CLI, logged in, for filing |
| [`nextseek-create-endpoint`](nextseek-create-endpoint/SKILL.md) | Adding or changing an API endpoint, that is a `nextseek_api` ViewSet: router registration, SEEK proxy or native endpoint, project scoping, OpenAPI schema and examples. | A step-by-step guide with patterns and worked examples, ending with the convention validator. | `scripts/validate_viewset_conventions.py`, `nextseek_api/tests/test_viewset_conventions.py` | Nothing |

The Nessie review skills (`nessie-run-review`, `nessie-bayes-report`) live beside their tests under
`NessieAI/tests/nessie_tests/`; the root [`CLAUDE.md`](../../CLAUDE.md) lists them.
