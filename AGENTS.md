# Agent instructions — NExtSEEK

Project instructions for coding agents live in [`CLAUDE.md`](CLAUDE.md)
(stack overview, build/run, testing, conventions). Read it first.

For **deploying or operating** the stack — greenfield install, shipping a
change, rollback, verification — the authoritative runbook is
[`DEPLOYMENT.md`](DEPLOYMENT.md). Follow it exactly; it encodes the
deployment-hygiene gates (rollback tags before rebuilds, mysqldump before
migration deploys, scoped service recreation, post-deploy verification,
Container-CC isolation invariants).

Production hardening (credentials, TLS, exposure) is in
[`NExtSTEPS.md`](NExtSTEPS.md).

For **filing GitHub issues** — including any bug you find whose fix is
deferred, or residuals left when a plan/task completes — follow
[`docs/ISSUE-CONVENTIONS.md`](docs/ISSUE-CONVENTIONS.md): draft the
structured body, validate it with `scripts/validate_issue.py`, and ask the
user before filing (the repo is public). Do not let deferred defects
languish uncreated.

For **adding or changing `nextseek_api` ViewSets** — follow
[`.claude/skills/nextseek-viewset/SKILL.md`](.claude/skills/nextseek-viewset/SKILL.md):
pydantic request/response models, `endpoint_descriptions.py` constants,
Basic+Session auth, project-scoping rules, and drf-spectacular examples.
Validate with `scripts/validate_viewset_conventions.py` before calling the
work done.
