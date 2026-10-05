# The entity hook the entrypoint installs into ~/.claude/settings.json at every start ($cmd is the hook
# script). The image build applies the same program to build expected-settings.json, the copy the start
# checks against, so the two can never drift.
.hooks //= {} |
.hooks.UserPromptSubmit = [{"matcher":"*","hooks":[{"type":"command","command":$cmd,"timeout":35}]}]
