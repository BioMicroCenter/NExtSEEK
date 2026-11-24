"""Build the on-box script that posts each new SEEK sample type as the box's CI write account.

    python3 post_types.py DRY.json REPO_ON_BOX > post_types.sh    # REPO_ON_BOX: boxes.json "repo"; run it with bash -s

The box's own ~/.config/nextseek/ci.env holds CI_WRITE_USER / CI_WRITE_PASS; the password goes to curl through a
config on a file descriptor (never argv, never printed). Each answer is printed as status + title + new id only.
A non-2xx prints SEEK's answer (it holds no secret) and stops the script: a 5xx or timeout may still have created the
type, so look it up before posting again. A 422 "Can only associate projects that you are an active member of" means
the account is not a member of the target project: pick a project it belongs to.
"""
import base64
import json
import sys

dry = json.load(open(sys.argv[1]))
repo = sys.argv[2]
bodies = dry["seek_type_payloads"]
assert bodies and all(b["data"]["relationships"]["projects"]["data"][0]["id"] != "<project id>" for b in bodies), \
    "every body needs the target project id (run the dry run with the project in the input)"
lines = ["set -euo pipefail",
         f"cd {json.dumps(repo)}",
         "PORT=$(python3 -c 'import json;print(json.load(open(\"startup/.instance.json\")).get(\"ports\",{}).get(\"nextseek\",8000))' 2>/dev/null || echo 8000)",
         "ENVF=\"$HOME/.config/nextseek/ci.env\"",
         "val(){ grep \"^$1=\" \"$ENVF\" | head -1 | cut -d= -f2- | sed -e 's/^[\"'\\'']//' -e 's/[\"'\\'']$//'; }",
         "U=$(val CI_WRITE_USER); P=$(val CI_WRITE_PASS)",
         "[ -n \"$U\" ] && [ -n \"$P\" ] || { echo 'no CI_WRITE_USER/CI_WRITE_PASS in ci.env'; exit 3; }",
         "esc(){ local s=${1//\\\\/\\\\\\\\}; printf '%s' \"${s//\\\"/\\\\\\\"}\"; }",
         "post(){ code=$(curl -sS -m 300 -o /tmp/defsync_resp.json -w '%{http_code}' "
         "--config <(printf 'user = \"%s:%s\"\\n' \"$(esc \"$U\")\" \"$(esc \"$P\")\") "
         "-H 'Content-Type: application/json' -X POST \"http://127.0.0.1:$PORT/nextseek_api/sample_types/\" --data @-); "
         "id=$(python3 -c 'import json;print((json.load(open(\"/tmp/defsync_resp.json\")).get(\"data\") or {}).get(\"id\",\"\"))' 2>/dev/null); "
         "echo \"$code $1 ${id:-no id}\"; "
         "case $code in 2*) rm -f /tmp/defsync_resp.json;; *) echo \"body: $(head -c 800 /tmp/defsync_resp.json)\"; "
         "rm -f /tmp/defsync_resp.json; echo STOP; exit 1;; esac; }"]
for b in bodies:
    t = b["data"]["attributes"]["title"]
    lines.append(f"echo {base64.b64encode(json.dumps(b).encode()).decode()} | base64 -d | post {json.dumps(t)}")
lines.append("echo ALL_POSTED")
print("\n".join(lines))
