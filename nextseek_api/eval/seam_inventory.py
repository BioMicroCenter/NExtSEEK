"""AST/source-derived paid-run provider seam inventory (V4-8)."""
from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "SeamSite",
    "discover_seams",
    "build_inventory",
    "find_unvisited_paid_run_gated",
    "write_inventory",
]

_REPO = Path(__file__).resolve().parents[2]
_EVAL_ROOT = _REPO / "nextseek_api" / "eval"
_ROUTER_PATH = _REPO / "nextseek_api" / "cc_assistant" / "router.py"

_ONLINE_CHAT_METHODS = frozenset(
    {"_classify_query", "_route_query", "classify_query", "route_query"}
)


@dataclass(frozen=True)
class SeamSite:
    name: str
    path: str
    line: int
    classification: str
    wired: bool
    cite: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["cite"]:
            del payload["cite"]
        return payload


def _rel(path: Path) -> str:
    return str(path.relative_to(_REPO))


def _scan_eval_file(path: Path) -> list[SeamSite]:
    rel = _rel(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sites: list[SeamSite] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, ast.Name) and node.func.id == "guarded_provider_call":
            sites.append(
                SeamSite(
                    name=f"guarded_provider_call:{rel}:{node.lineno}",
                    path=rel,
                    line=node.lineno,
                    classification="paid_run_gated",
                    wired=True,
                )
            )
            continue

        if isinstance(node.func, ast.Attribute) and node.func.attr == "invoke":
            wired = rel.endswith("fake_provider.py") or rel.endswith("judging_engine.py")
            sites.append(
                SeamSite(
                    name=f"transport_invoke:{rel}:{node.lineno}",
                    path=rel,
                    line=node.lineno,
                    classification="paid_run_gated",
                    wired=wired,
                )
            )

        if isinstance(node.func, ast.Attribute) and node.func.attr == "execute_attempt":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in {
                "engine",
                "self",
            }:
                sites.append(
                    SeamSite(
                        name=f"judging_engine_execute_attempt:{rel}:{node.lineno}",
                        path=rel,
                        line=node.lineno,
                        classification="paid_run_gated",
                        wired=True,
                    )
                )

    return sites


def _scan_router(path: Path) -> list[SeamSite]:
    if not path.is_file():
        return []
    rel = _rel(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    sites: list[SeamSite] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name not in _ONLINE_CHAT_METHODS:
                continue
            sites.append(
                SeamSite(
                    name=f"router_{node.name}:{rel}:{node.lineno}",
                    path=rel,
                    line=node.lineno,
                    classification="online_chat_out_of_v48_scope",
                    wired=False,
                    cite="V4-6 call table; V11 at-time approval for live chat provider",
                )
            )
    return sites


def discover_seams(*, repo_root: Path | None = None) -> list[SeamSite]:
    root = repo_root or _REPO
    eval_root = root / "nextseek_api" / "eval"
    router_path = root / "nextseek_api" / "cc_assistant" / "router.py"
    sites: list[SeamSite] = []
    for path in sorted(eval_root.rglob("*.py")):
        if path.name.startswith("test_") or path.parts[-2] == "tests":
            continue
        sites.extend(_scan_eval_file(path))
    sites.extend(_scan_router(router_path))
    # Stable dedupe by name
    seen: set[str] = set()
    unique: list[SeamSite] = []
    for site in sites:
        if site.name in seen:
            continue
        seen.add(site.name)
        unique.append(site)
    return unique


def build_inventory(*, repo_root: Path | None = None) -> dict[str, Any]:
    seams = discover_seams(repo_root=repo_root)
    return {
        "schema": "plan018-v4-8-provider-seam-inventory/v1",
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "complete",
        "derivation": "ast_source_scan",
        "classification_rules": {
            "paid_run_gated": "Eval/judge paid-run paths that must call guarded_provider_call before transport",
            "online_chat_out_of_v48_scope": "Live chat BAML router/classifier — V4-6/V11; not forced through eval run manifests",
        },
        "seams": [site.to_dict() for site in seams],
        "v4_4_debt_claim_void": "plan018-v4-4-debt-closeout.json V4-8 reservation PASS is non-authoritative scaffolding only",
    }


def find_unvisited_paid_run_gated(inventory: dict[str, Any]) -> list[str]:
    unwired = [
        seam["name"]
        for seam in inventory.get("seams", [])
        if seam.get("classification") == "paid_run_gated" and not seam.get("wired")
    ]
    return sorted(unwired)


def write_inventory(path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    payload = build_inventory(repo_root=repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def load_or_build_inventory(path: Path) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return build_inventory()


def iter_paid_run_gated_sites(seams: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in seams if s.get("classification") == "paid_run_gated"]
