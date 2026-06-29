# Cross-session memory (probe fixture)

USER_MEMORY_MARKER=1C_PROBE_ALPHA

This user-tier memory file is mounted read-only at `/home/user/.claude/CLAUDE.md`.
If this file REPLACES (rather than merges with) the baked project CLAUDE.md,
the agent would lose "Write-safety on NExtSEEK" guidance from the project tier.

## Probe-only session memory

- User prefers concise answers.
- Last session discussed cross-session memory design.
