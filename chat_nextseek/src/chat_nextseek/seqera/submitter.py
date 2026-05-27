"""Tower-direct submitter.

Parses chat_nextseek's emitted launch.yml + per-cohort params.yml and submits
each entry to Tower's REST API. No external binaries (no `tw`, no `seqerakit`)
— stdlib `urllib` only (plus `requests` for the dataset uploader, which is
called from the emitter, not here).

The same launch.yml file remains usable by `seqerakit launch.yml` for users
who prefer that tooling; this module just provides an alternative path that
chat_nextseek can drive itself when SEQERA_AUTO_LAUNCH=true.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    import yaml as _yaml
    _HAVE_YAML = True
except Exception:  # pragma: no cover
    _yaml = None
    _HAVE_YAML = False

from .tower_client import TowerAPIError, TowerClient


def _read_yaml(path: Path) -> Any:
    if not _HAVE_YAML:
        raise RuntimeError(
            "PyYAML is required to parse launch.yml for direct REST submission. "
            "Install it via `uv pip install pyyaml`."
        )
    return _yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def submit_launch(
    launch_yml_path: str | Path,
    *,
    tower_env: Mapping[str, str | None],
    cwd: str | Path | None = None,
) -> list[str]:
    """Read a chat_nextseek launch.yml and submit each entry to Tower's REST API.

    Returns a list of Tower run URLs (one per launch entry that succeeded).
    """
    launch_path = Path(launch_yml_path).resolve()
    if not launch_path.exists():
        print(f"[SEQERA][SUBMIT] launch.yml not found: {launch_path}")
        return []

    token = (tower_env or {}).get("access_token")
    if not token:
        print("[SEQERA][SUBMIT] TOWER_ACCESS_TOKEN missing — skipping submission")
        return []

    try:
        doc = _read_yaml(launch_path)
    except Exception as e:
        print(f"[SEQERA][SUBMIT] failed to parse {launch_path}: {e!r}")
        return []

    entries = (doc or {}).get("launch") if isinstance(doc, dict) else None
    if not entries:
        print(f"[SEQERA][SUBMIT] no launch entries in {launch_path}")
        return []

    parent_dir = launch_path.parent
    api_endpoint = (tower_env or {}).get("api_endpoint")
    client = TowerClient(token=token, endpoint=api_endpoint)

    run_urls: list[str] = []
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            print(f"[SEQERA][SUBMIT] entry {idx} skipped (not a dict)")
            continue
        try:
            url = _submit_one(client, entry, parent_dir)
            if url:
                run_urls.append(url)
        except TowerAPIError as e:
            print(f"[SEQERA][SUBMIT] entry {entry.get('name', idx)!r} failed: {e}")
        except Exception as e:  # pragma: no cover
            print(f"[SEQERA][SUBMIT] entry {entry.get('name', idx)!r} failed: {e!r}")
    return run_urls


def _submit_one(client: TowerClient, entry: dict[str, Any], parent_dir: Path) -> str | None:
    name = (entry.get("name") or "unnamed-run").strip() or "unnamed-run"
    workspace_qualified = entry.get("workspace")
    ce_name = entry.get("compute-env")
    pipeline = entry.get("pipeline")
    revision = entry.get("revision")
    profile = entry.get("profile")
    work_dir = entry.get("work-dir")
    params_file_ref = entry.get("params-file") or "./params.yml"

    missing = [k for k, v in (
        ("workspace", workspace_qualified),
        ("compute-env", ce_name),
        ("pipeline", pipeline),
    ) if not v]
    if missing:
        print(f"[SEQERA][SUBMIT] entry {name!r} missing required fields: {missing}")
        return None

    # Resolve workspace + compute env
    ws = client.resolve_workspace(str(workspace_qualified))
    workspace_id = ws["workspaceId"]
    org_name = ws.get("orgName")
    workspace_name = ws.get("workspaceName")
    ce = client.resolve_compute_env(workspace_id, str(ce_name))
    compute_env_id = ce["id"]

    # Read params.yml verbatim — Tower accepts paramsText as the YAML body
    # that Nextflow will use as `-params-file`.
    params_path = (parent_dir / params_file_ref).resolve()
    if not params_path.exists():
        print(f"[SEQERA][SUBMIT] params file not found: {params_path}")
        return None
    params_text = params_path.read_text(encoding="utf-8")

    payload: dict[str, Any] = {
        "computeEnvId": compute_env_id,
        "pipeline": pipeline,
        "workDir": work_dir,
        "paramsText": params_text,
        "runName": name,
    }
    if revision:
        payload["revision"] = revision
    if profile:
        payload["configProfiles"] = (
            [profile] if isinstance(profile, str) else list(profile)
        )

    print(
        f"[SEQERA][SUBMIT] launching {name!r}: "
        f"pipeline={pipeline} revision={revision or '(default)'} "
        f"ce={ce_name!r} workspace={workspace_qualified!r}"
    )
    resp = client.launch_workflow(workspace_id, payload)
    workflow_id = resp.get("workflowId")
    if not workflow_id:
        print(f"[SEQERA][SUBMIT] launch returned no workflowId: {resp}")
        return None

    # Build the human-facing run URL
    cloud_base = client.cloud_ui_base
    if org_name and workspace_name:
        run_url = (
            f"{cloud_base}/orgs/{org_name}/workspaces/"
            f"{workspace_name}/watch/{workflow_id}"
        )
    else:
        run_url = f"{cloud_base}/watch/{workflow_id}"
    print(f"[SEQERA][SUBMIT] launched {name!r}: workflowId={workflow_id} url={run_url}")
    return run_url
