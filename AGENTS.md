# Agent instructions: NExtSEEK

## Non-negotiables

1. This repository is public. Never write credentials, tokens, email addresses, personal home paths or
   host names into a file, an issue or a commit message.
2. Never commit secrets (`DEPLOYMENT.md` §8) or local data: `filestore/`, `logs/`, `outputs/`, seed dumps,
   env files.
3. Stage files by name, never `git add -A`: other sessions may share the working tree.
4. Deploy or operate a stack only by following [`DEPLOYMENT.md`](DEPLOYMENT.md), exactly and in order.
5. Deferred work becomes a drafted issue, never a silent TODO: draft it per
   [`docs/ISSUE-CONVENTIONS.md`](docs/ISSUE-CONVENTIONS.md), check it with `scripts/validate_issue.py`, and file
   it only after a person approves.

## Then read

- [`CLAUDE.md`](CLAUDE.md), the map of this repo: folders, skills, sub-docs, gotchas. It is written for any
  agent, not only Claude Code.
- [`ARCHITECTURE.md`](ARCHITECTURE.md), how the parts fit and where the Neo4j graph code lives.
- Before editing inside a folder, that folder's `CLAUDE.md`. Only Claude Code loads it automatically.

## Pinned workflows

- Container-CC ops: `/add-cc-op` ([`.claude/skills/add-cc-op/SKILL.md`](.claude/skills/add-cc-op/SKILL.md)).
- ViewSets: `nextseek-create-endpoint`
  ([`.claude/skills/nextseek-create-endpoint/SKILL.md`](.claude/skills/nextseek-create-endpoint/SKILL.md)),
  checked with `scripts/validate_viewset_conventions.py`.
- All committed skills: [`.claude/skills/README.md`](.claude/skills/README.md).
- Hardening an instance before exposure: [`NExtSTEPS.md`](NExtSTEPS.md).
