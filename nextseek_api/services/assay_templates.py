"""An assay's components: what a template download for it would contain.

Pure data: no Django request objects, no HTTP, no openpyxl. `seek.views
.assay_setup` consumes what this returns, so the view reaches into no table.
Same division `context_catalog` and `template_catalog` already draw.

Two sets of sample type codes meet here, and they are not the same set.
`context_catalog` validates an assay's components against the curated
`sample_types_context` rows; `template_catalog.downloadable_codes` is SEEK's own
sample types minus the retired and the unusable-as-a-sheet-name.
`template_catalog` states the consequence directly: anything that OFFERS a
download has to gate on the latter. So every component is checked against
`load_catalog` and annotated rather than dropped -- a curator naming a component
no sheet exists for is a fact the user needs to see, not one to hide.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from nextseek_api.services.context_catalog import load_assay
from nextseek_api.services.template_catalog import load_catalog

logger = logging.getLogger(__name__)

NO_SHEET = "No template sheet exists for this sample type."


@dataclass
class ComponentCode:
    code: str
    name: str = ""
    downloadable: bool = False
    reason: str = ""
    selected: bool = False


@dataclass
class ComponentGroup:
    """One alternation group. The upload needs exactly one of its members."""
    alternatives: list[ComponentCode] = field(default_factory=list)


@dataclass
class AssayComponents:
    slug: str
    name: str
    is_split: bool = False
    required_parents: list[ComponentGroup] = field(default_factory=list)
    optional_parents: list[ComponentGroup] = field(default_factory=list)
    produces: list[ComponentGroup] = field(default_factory=list)


def _catalog_by_code() -> dict:
    """Code to catalog entry. Empty on failure: the offer costs, not the page."""
    try:
        return {entry.code: entry for entry in load_catalog()}
    except Exception:
        logger.exception("sample type catalog unavailable; nothing is downloadable")
        return {}


def _build_group(codes, catalog, preselect) -> ComponentGroup:
    """One alternation group, annotated.

    `preselect` ticks the first DOWNLOADABLE alternative rather than simply the
    first: defaulting to one with no sheet would hand the user a workbook
    quietly missing it. A group with nothing downloadable selects nothing.
    """
    group = ComponentGroup()
    chosen = False
    for code in codes:
        entry = catalog.get(code)
        component = ComponentCode(
            code=code,
            name=getattr(entry, "name", "") or "",
            downloadable=entry is not None,
            reason="" if entry is not None else NO_SHEET,
        )
        if preselect and not chosen and component.downloadable:
            component.selected = True
            chosen = True
        group.alternatives.append(component)
    return group


def _extend(groups, raw_groups, catalog, preselect) -> None:
    """Append each raw group, skipping one whose codes are already present.

    Deduplicated by the tuple of codes so that a split slug's two rows do not
    offer the same pick-one group twice.
    """
    seen = {tuple(c.code for c in g.alternatives) for g in groups}
    for codes in raw_groups:
        key = tuple(codes)
        if not codes or key in seen:
            continue
        seen.add(key)
        groups.append(_build_group(codes, catalog, preselect))


def resolve_components(slug: str) -> AssayComponents | None:
    """Every component of the assay at `slug`, or None when there is no such assay.

    A slug carrying two unreconciled `assay_context` rows yields the UNION of
    both rows' components. `context_catalog.AssayEntry` records why those rows
    are not merged for display; a download is different, because it has to
    resolve to one set of sheets, and offering only one row's components would
    hide half the assay.
    """
    entry = load_assay(slug)
    if entry is None:
        return None

    catalog = _catalog_by_code()
    components = AssayComponents(
        slug=entry.slug, name=entry.name, is_split=len(entry.rows) > 1,
    )
    for row in entry.rows:
        _extend(components.required_parents, row.required_parents, catalog, True)
        _extend(components.optional_parents, row.optional_parents, catalog, False)
        _extend(components.produces, row.children, catalog, True)
    return components
