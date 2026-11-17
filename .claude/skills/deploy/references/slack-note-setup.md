# Setting up the team note

The runner on the box emails three short notes to the team channel's email address: when it starts ("Rebuilding
Prod (NExtSEEK) to `<sha>`", with who, from which machine, what is rebuilt and the expected end in ET), when it ends
("Prod is back up on `<sha>`", with CI's result line), and when it stops or dies ("Prod rebuild to `<sha>`
stopped", with the reason and the sha the box now runs). It sends them with the box's own SMTP keys, the ones the
weekly Nessie digest uses, so the notes go out even when the laptop sleeps or no browser is open. What the session
does: [SKILL.md "The team note"](../SKILL.md).

It is optional and safe to leave off. With any piece missing the runner writes one status line, `NOTE skipped
<event>: ...`, and carries on. The note never stops, delays or fails a deploy.

## One-time setup

1. **The box's SMTP keys** in `docker/nextseek.env` on each box that should send (outside git, never copied off the
   box): `NEXTSEEK_SMTP_HOST`, `NEXTSEEK_SMTP_PORT` (587, STARTTLS), `NEXTSEEK_SMTP_USER`, `NEXTSEEK_SMTP_PASSWORD`,
   `NEXTSEEK_SMTP_FROM`, and the recipient `NESSIE_DIGEST_TO` (the team channel's email address). The note reads
   the file directly, so no restart is needed. A box without them skips the note.
2. **The box config** `~/.config/nextseek/boxes.json` on the machine that launches (`references/boxes.md`). Add next
   to `instances`:

   ```json
   "slack_note": {
     "enabled": true,
     "instances": ["prod", "dev"],
     "who": "<your first name>"
   }
   ```

   `instances` lists where the runner sends notes (default `["prod"]`; `"local"` never sends). `enabled` is false
   unless you set it true. `who` names you in the notes (default: your login name). `email_to` (optional) sends to
   another address than the box's `NESSIE_DIGEST_TO`. Never commit the file.
3. **Check it**, free, touching no box:

   ```bash
   uv run .claude/skills/deploy/scripts/launch.py slack-note --instance prod --event start --sha <8 hex chars>
   ```

   `NOTE_BY_BOX: ...` means the next runner rendered for that instance will send. `NOTE_OFF: ...` names what is off.

## Troubleshooting

- **`NOTE skipped`** in the status: the box's `docker/nextseek.env` lacks `NEXTSEEK_SMTP_HOST` or a recipient.
- **`NOTE failed`**: the reason is in `<home>/launch-<tag>/note.log` on the box (pulled with the launch evidence).
  It is never retried, so a note cannot go out twice.
- **Always `NOTE_OFF: no slack_note in the box config`**: the script reads `NEXTSEEK_BOXES` if set, else
  `~/.config/nextseek/boxes.json`: check you edited the file it names.
