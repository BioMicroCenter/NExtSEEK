"""One-shot Tower API discovery: list workspaces, compute envs, and the
work-dir / outdir bucket configured on each compute env.

Reads TOWER_ACCESS_TOKEN (and optional TOWER_API_ENDPOINT) from the env or
the project .env file. Prints a copy/paste-ready snippet for chat_nextseek's
SEQERA_* variables at the bottom.

Usage:
    uv run python scripts/seqera_discover.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _load_dotenv() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def _api_get(endpoint: str, path: str, token: str, params: dict[str, str] | None = None) -> Any:
    url = f"{endpoint.rstrip('/')}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {e.code} on {path}: {body[:300]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Network error on {path}: {e}")


def main() -> int:
    _load_dotenv()
    token = os.getenv("TOWER_ACCESS_TOKEN")
    if not token:
        print("ERROR: TOWER_ACCESS_TOKEN not set (.env or shell env).", file=sys.stderr)
        return 1
    endpoint = os.getenv("TOWER_API_ENDPOINT", "https://api.cloud.seqera.io")

    print(f"Tower endpoint: {endpoint}")
    print("Fetching user + workspaces ...\n")

    user = _api_get(endpoint, "/user-info", token)
    user_obj = user.get("user") or {}
    user_id = user_obj.get("id")
    print(f"User: {user_obj.get('userName')} (id={user_id}, email={user_obj.get('email')})")

    # Workspaces visible to this token
    ws = _api_get(endpoint, f"/user/{user_id}/workspaces", token) if user_id else {}
    workspaces = ws.get("orgsAndWorkspaces") or []
    if not workspaces:
        print("No workspaces visible to this token.")
        return 0

    print(f"\n{'=' * 78}\nWORKSPACES")
    print(f"{'=' * 78}")
    rows: list[dict[str, Any]] = []
    for w in workspaces:
        if not w.get("workspaceId"):
            continue  # personal/null entries
        rows.append(w)
        print(
            f"  workspaceId={w.get('workspaceId'):<20}  "
            f"orgName={w.get('orgName'):<25}  "
            f"workspaceName={w.get('workspaceName')}"
        )

    suggestions: list[tuple[str, str, str, str]] = []  # (workspace_qualified, ce_name, work_dir, ce_id)

    for w in rows:
        ws_id = w["workspaceId"]
        ws_qualified = f"{w.get('orgName')}/{w.get('workspaceName')}"
        print(f"\n--- Compute envs in {ws_qualified} (workspaceId={ws_id}) ---")
        try:
            ces = _api_get(endpoint, "/compute-envs", token, {"workspaceId": str(ws_id)})
        except SystemExit as e:
            print(f"  (skipped: {e})")
            continue
        ce_list = ces.get("computeEnvs") or []
        if not ce_list:
            print("  (none)")
            continue
        for ce in ce_list:
            ce_id = ce.get("id")
            name = ce.get("name")
            status = ce.get("status")
            platform = ce.get("platform")
            print(f"  - name={name!r:<35} platform={platform:<15} status={status:<12} id={ce_id}")
            # Detail fetch for work-dir
            try:
                detail = _api_get(
                    endpoint, f"/compute-envs/{ce_id}", token, {"workspaceId": str(ws_id)}
                )
                cfg = (detail.get("computeEnv") or {}).get("config") or {}
                work_dir = cfg.get("workDir") or cfg.get("workdir") or ""
                if work_dir:
                    print(f"      workDir: {work_dir}")
                    suggestions.append((ws_qualified, name, work_dir, str(ce_id)))
                else:
                    print("      workDir: (not configured on this CE)")
            except SystemExit as e:
                print(f"      (detail fetch skipped: {e})")

    print(f"\n{'=' * 78}\nSUGGESTED .env SNIPPETS")
    print(f"{'=' * 78}")
    if not suggestions:
        print("No compute env with a workDir was found. Configure one in Tower UI first.")
        return 0

    for ws_q, ce_name, work_dir, _ in suggestions:
        # work-bucket = the bucket prefix; we suggest the parent of /work or use as-is
        bucket_prefix = work_dir
        # If workDir ends in /work, suggest its parent so chat_nextseek can put both
        # work/<run> and results/<run> beneath it.
        bucket_prefix = bucket_prefix.rstrip("/")
        if bucket_prefix.endswith("/work"):
            bucket_prefix = bucket_prefix[: -len("/work")]
        print(
            f"\n# {ws_q} / {ce_name}\n"
            f"TOWER_WORKSPACE_ID={ws_q}\n"
            f"SEQERA_COMPUTE_ENV={ce_name}\n"
            f"SEQERA_WORK_BUCKET={bucket_prefix}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
