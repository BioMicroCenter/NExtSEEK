# Setting up the Slack note

The deploy skill can post two short notes to a Slack channel when it rebuilds a box ("Rebuilding prod
(NExtSEEK) to `<sha>`. Expect a short downtime." then "Prod is back up on `<sha>`."), and one line on a
rollback. It posts through the **Claude in Chrome** extension, as you, in the Slack you are already signed in to.
It needs no Slack app, bot, webhook or admin rights. The steps the session follows are in
[SKILL.md "The Slack note"](../SKILL.md); this page is how to set a machine up.

It is optional and safe to leave off. With any piece missing the deploy prints one line,
`Slack note skipped: <reason>; post by hand: <the message>`, and carries on. The note never stops, delays or
fails a deploy.

## One-time setup (about 5 minutes)

1. **Claude in Chrome.** Install the Claude in Chrome extension in the Chrome you use and connect it to Claude Code
   (`/chrome` in Claude Code shows whether it is connected). The session must be able to open a tab in that Chrome.
2. **Slack on the web, signed in, in that same Chrome.** Open `https://app.slack.com`, sign in to the workspace,
   then open the channel link once and check that you see a message box and not a sign-in page. A signed-out
   Slack makes the note skip.
3. **The channel link.** In Slack: right-click the channel, "Copy link". It looks like
   `https://app.slack.com/client/<workspace id>/<channel id>`.
4. **The box config** `~/.config/nextseek/boxes.json` (the file `references/boxes.md` describes, outside git). Add next to
   `instances`:

   ```json
   "slack_note": {
     "enabled": true,
     "channel_url": "https://app.slack.com/client/<workspace id>/<channel id>",
     "instances": ["prod", "dev"]
   }
   ```

   `instances` lists where the note posts (default `["prod"]`; `"local"` never posts). `enabled` is false
   unless you set it true. The shape is in [`boxes.example.json`](../boxes.example.json). Never commit the file.
5. **Check it, free, touching no box and no Slack:**

   ```bash
   uv run .claude/skills/deploy/scripts/launch.py slack-note --instance prod --event start --sha <8 hex chars>
   ```

   `SLACK_POST channel=... message=...` means the config is right. `SLACK_SKIP: ...` names what is missing.

## The gates (what a session checks before it posts)

| Gate | Fails when | Result |
|---|---|---|
| Config | no `slack_note`, `enabled` not true, the instance not in `instances`, no `https` `channel_url` | skip, message printed |
| Claude in Chrome | the tools are not connected | skip, message printed |
| Slack signed in | the channel page shows a sign-in page and no message box | skip, message printed |
| Message box | the text read back from the box is not exactly the message | clear the box, skip, message printed |
| Send | any doubt after sending | no retry (it could double-post); read the channel once, then report |

Your deploy go covers the posts. Run the step in a NEW Chrome tab and close it after. Posting is a write to a
shared channel, so a rehearsal that posts the real wording (with no real rebuild) should be announced to the team first.

## Troubleshooting

- **Always skipping with "no slack_note in the box config".** The script reads `NEXTSEEK_BOXES` if set, else
  `~/.config/nextseek/boxes.json`: check you edited the file it names.
- **Message box not found.** Slack redirects to a sign-in or workspace chooser: sign in in that Chrome, then rerun.
- **Wrong channel.** The link's last path part is the channel id; copy it from the channel you mean.
