"""Shared Tower REST client used by submitter and dataset uploader.

Stdlib `urllib` is fine for plain JSON requests (workspace/compute-env
lookup, workflow launch). For multipart file upload we use `requests`,
which is already a project dependency.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


_DEFAULT_API_ENDPOINT = "https://api.cloud.seqera.io"
_DEFAULT_CLOUD_UI = "https://cloud.seqera.io"


class TowerAPIError(RuntimeError):
    """Raised when Tower REST returns a non-2xx response."""


class TowerClient:
    def __init__(self, token: str, endpoint: str | None = None) -> None:
        self.token = token
        self.endpoint = (
            endpoint
            or os.getenv("TOWER_API_ENDPOINT")
            or _DEFAULT_API_ENDPOINT
        ).rstrip("/")
        self._workspace_cache: dict[str, dict[str, Any]] = {}
        self._compute_env_cache: dict[tuple[str, str], dict[str, Any]] = {}

    # ── low-level request helpers ─────────────────────────────────────
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> Any:
        url = f"{self.endpoint}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url,
            method=method,
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            raise TowerAPIError(
                f"Tower API {method} {path} → HTTP {e.code}: {body_text[:500]}"
            )
        except urllib.error.URLError as e:
            raise TowerAPIError(f"Tower API {method} {path} → network error: {e}")

    # ── name → id resolution ──────────────────────────────────────────
    def resolve_workspace(self, qualified_or_id: str) -> dict[str, Any]:
        """Resolve `org/workspace` (or numeric ID) to a workspace dict."""
        if qualified_or_id in self._workspace_cache:
            return self._workspace_cache[qualified_or_id]
        user_info = self.request("GET", "/user-info")
        user_id = (user_info.get("user") or {}).get("id")
        if not user_id:
            raise TowerAPIError("Could not determine user id from /user-info")
        ws_resp = self.request("GET", f"/user/{user_id}/workspaces")
        target = qualified_or_id.strip()
        for ws in ws_resp.get("orgsAndWorkspaces") or []:
            if not ws.get("workspaceId"):
                continue
            qualified = f"{ws.get('orgName')}/{ws.get('workspaceName')}"
            if qualified == target or str(ws.get("workspaceId")) == target:
                self._workspace_cache[qualified_or_id] = ws
                return ws
        raise TowerAPIError(f"Workspace not found: {qualified_or_id}")

    def resolve_compute_env(self, workspace_id: str | int, name_or_id: str) -> dict[str, Any]:
        key = (str(workspace_id), name_or_id)
        if key in self._compute_env_cache:
            return self._compute_env_cache[key]
        resp = self.request(
            "GET", "/compute-envs", params={"workspaceId": str(workspace_id)}
        )
        target = (name_or_id or "").strip()
        for ce in resp.get("computeEnvs") or []:
            if ce.get("name") == target or ce.get("id") == target:
                self._compute_env_cache[key] = ce
                return ce
        raise TowerAPIError(
            f"Compute env not found in workspace {workspace_id}: {name_or_id}"
        )

    # ── workflow launch ───────────────────────────────────────────────
    def launch_workflow(self, workspace_id: str | int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST",
            "/workflow/launch",
            params={"workspaceId": str(workspace_id)},
            body={"launch": payload},
        )

    @property
    def cloud_ui_base(self) -> str:
        # cloud.seqera.io for cloud Tower; on-prem uses different host.
        # Derive from API endpoint if it looks like a custom deployment.
        api_host = urllib.parse.urlparse(self.endpoint).netloc
        if api_host == "api.cloud.seqera.io":
            return _DEFAULT_CLOUD_UI
        # Best-effort guess: strip "api." prefix
        if api_host.startswith("api."):
            return f"https://{api_host[len('api.'):]}"
        return f"https://{api_host}"
