# The local box config (`boxes.json`)

Every value that names a real machine, account or path lives in one JSON file outside the repo,
so it works from every worktree and never reaches git. The repo is public: do not paste these
values into a committed file, a commit message or an issue.

- Default path: `~/.config/nextseek/boxes.json`. Override with the `NEXTSEEK_BOXES` environment variable.
- Template: [`../boxes.example.json`](../boxes.example.json) (placeholders only). Copy it, fill it in, `chmod 600`.
- The scripts read it at run time. A missing file, invalid JSON or a missing key stops with exit 2
  and a message that names the file and the key.

## Top level

| Key | Meaning |
|---|---|
| `reports_dir` | Where launch folders `<instance>-launch-<TAG>/` are written. `~` is expanded. |
| `workstation_repo` | The checkout used for `git fetch`, the commit review and the Nessie case files. |
| `instances` | One object per instance: `local`, `dev`, `prod`. Only the instance a command uses is required. |

## Per instance

| Key | Needed for | Meaning |
|---|---|---|
| `box` | all | The name used in messages and the report. |
| `repo` | all | The NExtSEEK checkout on that machine (for `local`, the workstation checkout). |
| `ssh_host` | dev, prod | The ssh host alias (from your ssh config) the scripts connect to. |
| `transport` | dev, prod | `sudo` (log in as a login user, then `sudo -n -u <run_as> bash -l`) or `direct` (the login already is the stack owner). |
| `run_as` | dev, prod | The account that owns the stack and the repo on the box. |
| `home` | dev, prod | That account's home directory; the launch evidence and backups live there. |
| `in_dir` | dev, prod | Where copied files land first: a world-writable dir such as `/tmp` for `sudo`, the home for `direct`. |
| `url` | optional | Base URL of the instance, for your own notes and the report. |
| `test_logins` | optional | Names (never passwords) of the smoke and write accounts CI uses. The passwords live in `ci.env` on the box, never here. |

## Not here

Rules that name no machine stay in `.claude/skills/deploy/scripts/rules.py`: disk and memory floors, known reds, the
nightly windows, image needs per path, budgets.
