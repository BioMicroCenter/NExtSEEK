# Agent instructions: NExtSEEK

1. Read [`CLAUDE.md`](CLAUDE.md) first. It is the map of this repo: folders, skills, sub-docs, gotchas.
2. Before editing inside a folder, read that folder's `CLAUDE.md`. Only Claude Code loads it automatically.
3. Deploying or operating a stack: follow [`DEPLOYMENT.md`](DEPLOYMENT.md) exactly.
4. Pinned workflows: issues per [`docs/ISSUE-CONVENTIONS.md`](docs/ISSUE-CONVENTIONS.md), validated with `scripts/validate_issue.py`, filed only after a person approves; Container-CC ops via `/add-cc-op` ([`.claude/skills/add-cc-op/SKILL.md`](.claude/skills/add-cc-op/SKILL.md)); ViewSets via `nextseek-create-endpoint` ([`.claude/skills/nextseek-create-endpoint/SKILL.md`](.claude/skills/nextseek-create-endpoint/SKILL.md)), checked with `scripts/validate_viewset_conventions.py`.
5. Hardening an instance before exposure: [`NExtSTEPS.md`](NExtSTEPS.md).
