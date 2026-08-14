"""All Pydantic models and dataclasses for the batch upload pipeline."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .helpers import parse_protocol_value

try:
    import orjson

    def _json_loads(s):
        return orjson.loads(s)

    def _json_dumps_min(obj):
        return orjson.dumps(obj).decode("utf-8")

except ImportError:
    _json_loads = json.loads

    def _json_dumps_min(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ── helpers ────────────────────────────────────────────────────────────────


def _minify_json_string(json_value: Union[str, dict]) -> str:
    """Parse and re-serialize JSON with minimal whitespace."""
    if isinstance(json_value, dict):
        return _json_dumps_min(json_value)
    if isinstance(json_value, str):
        # Strip BOM
        s = json_value.lstrip("\ufeff").strip()
        if not s:
            return "{}"
        parsed = _json_loads(s)
        return _json_dumps_min(parsed)
    return str(json_value)


_ASSAY_ID_RE = re.compile(r"\d+")


# ── 1. SampleTypeDescription ──────────────────────────────────────────────


class SampleTypeDescription(BaseModel):
    st_id: int
    sampletype: str
    st_description: str = ""
    st_attributes: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    def save_to_json(self, path: str) -> None:
        import pathlib
        pathlib.Path(path).write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def from_pd_df(cls, df) -> List["SampleTypeDescription"]:
        """Build from a pandas DataFrame with columns st_id, sampletype, st_description, st_attributes."""
        results = []
        for _, row in df.iterrows():
            attrs = row.get("st_attributes", [])
            if isinstance(attrs, str):
                attrs = [a.strip() for a in attrs.split(",") if a.strip()]
            results.append(cls(
                st_id=int(row["st_id"]),
                sampletype=str(row["sampletype"]),
                st_description=str(row.get("st_description", "")),
                st_attributes=attrs,
            ))
        return results

    @classmethod
    def load_from_json(cls, path: str) -> "SampleTypeDescription":
        import pathlib
        return cls.model_validate_json(pathlib.Path(path).read_text(encoding="utf-8"))


# ── 2. SampleMetadata ─────────────────────────────────────────────────────


class SampleMetadata(BaseModel):
    UID: Optional[str] = None
    Name: Optional[Union[str, int]] = None
    Link_PrimaryData: Optional[str] = None
    Link_SecondaryData: Optional[str] = None
    File_PrimaryData: Optional[str] = None
    File_PrimaryData_Forward: Optional[str] = None
    File_PrimaryData_Reverse: Optional[str] = None
    File_PrimartyData: Optional[str] = None  # intentional typo from SEEK
    File_PrimartyData_Forward: Optional[str] = None
    File_PrimartyData_Reverse: Optional[str] = None
    Parent: Optional[str] = None
    Protocol: Optional[str] = None
    Scientist: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    def model_dump(self, *args, **kwargs):
        from .identity import canonicalize_file_primary_data

        data = super().model_dump(*args, **kwargs)
        return canonicalize_file_primary_data(data)


# ── 3. InputRowModel ──────────────────────────────────────────────────────


class InputRowModel(BaseModel):
    UID: Optional[str] = None
    SampleType: str
    json_metadata: str
    assay_ids: List[int] = Field(default_factory=list)
    project_id: Optional[int] = None
    study_title: Optional[str] = None
    study_id: Optional[int] = None
    sop_id: Optional[int] = None
    assay_titles: Optional[List[str]] = None
    original_row_index: Optional[int] = None

    model_config = ConfigDict(extra="allow")

    @field_validator("UID", mode="before")
    @classmethod
    def _normalize_uid(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("assay_titles", mode="before")
    @classmethod
    def coerce_assay_titles(cls, v):
        """Coerce assay_titles from common Excel encodings into a list[str].

        Accepts:
        - list[str]
        - JSON array string (e.g. '["A","B"]')
        - bracketed single-item string from Excel (e.g. '[ADCD Analysis - Data Linked]')
        - delimited strings ('A; B' or 'A, B')
        """
        if v is None:
            return None
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            return out or None
        s = str(v).strip()
        if not s or s.lower() == "nan":
            return None

        # JSON array
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = _json_loads(s)
                if isinstance(parsed, list):
                    out = [str(x).strip() for x in parsed if str(x).strip()]
                    return out or None
            except Exception:
                # Excel sometimes produces unquoted bracketed strings: [Title]
                inner = s[1:-1].strip()
                if inner:
                    return [inner]
                return None

        # Delimited strings
        delim = ";" if ";" in s else ("," if "," in s else None)
        if delim:
            out = [part.strip() for part in s.split(delim) if part.strip()]
            return out or None
        return [s]

    @field_validator("assay_ids", mode="before")
    @classmethod
    def coerce_assay_ids(cls, v):
        if isinstance(v, list):
            return [int(x) for x in v if x is not None and str(x).strip()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, (str, float)):
            s = str(v).strip()
            if not s or s.lower() == "nan":
                return []
            return [int(m) for m in _ASSAY_ID_RE.findall(s)]
        if v is None:
            return []
        return [int(x) for x in v]

    @field_validator("json_metadata", mode="before")
    @classmethod
    def normalize_json_metadata(cls, v):
        from .identity import canonicalize_identity_metadata

        if isinstance(v, dict):
            return _minify_json_string(canonicalize_identity_metadata(v))
        if isinstance(v, str):
            s = v.lstrip("\ufeff").strip()
            if not s:
                return "{}"
            try:
                parsed = _json_loads(s)
                if isinstance(parsed, dict):
                    return _minify_json_string(canonicalize_identity_metadata(parsed))
                return _minify_json_string(s)
            except Exception:
                return v
        if v is None:
            return "{}"
        return str(v)

    @field_validator("json_metadata", mode="after")
    @classmethod
    def _validate_metadata_shape(cls, v):
        try:
            parsed = _json_loads(v) if v else {}
        except Exception as exc:
            raise ValueError("json_metadata is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("json_metadata must be a JSON object, not a JSON array/scalar")
        return v

    @model_validator(mode="after")
    def validate_optional_consistency(self):
        # Parse json_metadata once for all downstream checks
        try:
            meta = _json_loads(self.json_metadata) if self.json_metadata else {}
        except Exception:
            meta = {}
        if isinstance(meta, dict) and self.UID is None:
            meta_uid = meta.get("UID") or meta.get("uid")
            if meta_uid and str(meta_uid).strip():
                raise ValueError(
                    f"json_metadata.UID is {str(meta_uid).strip()!r} but row UID "
                    "column is empty; provide the UID explicitly"
                )
        if isinstance(meta, dict) and self.UID is not None:
            meta_uid = meta.get("UID") or meta.get("uid")
            if meta_uid is not None and str(meta_uid).strip() and str(meta_uid).strip() != str(self.UID).strip():
                raise ValueError("UID column does not match json_metadata.UID")

            # 2) SOP consistency if sop_id provided and Protocol encodes SOP
            #
            # This is VALIDATION, not resolution: it stays purely syntactic (no
            # DB), so it can only compare two integers. It shares
            # parse_protocol_value only to agree with the ingest path on what
            # "the Protocol encodes a SOP id" means. Two consequences, both
            # intended:
            #   * a title-shaped Protocol carries no id, so there is nothing to
            #     disagree with — ingest resolves it by title later;
            #   * an id in a FOREIGN SOP URL (fairdomhub.org/sops/795, 1,855
            #     live values) is not in our sops table, so comparing it to a
            #     local sop_id would reject perfectly good rows.
            if self.sop_id is not None:
                protocol_val = meta.get("Protocol") or meta.get("protocol") or ""
                parsed = parse_protocol_value(protocol_val).sop_id
                if parsed is not None and int(self.sop_id) != parsed:
                    raise ValueError("sop_id does not match SOP id in json_metadata.Protocol")

        # 3) assay_titles length consistency if provided
        if self.assay_titles is not None:
            if self.assay_ids and len(self.assay_titles) != len(self.assay_ids):
                # allow singleton only when assay_ids is singleton
                if not (len(self.assay_titles) == 1 and len(self.assay_ids) == 1):
                    raise ValueError("assay_titles length must match assay_ids length")

        return self


# ── 4. InsertableSample ───────────────────────────────────────────────────


class InsertableSample(BaseModel):
    uuid: str
    title: str
    sample_type_id: int
    json_metadata: str
    assay_ids: List[int] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @field_validator("assay_ids", mode="before")
    @classmethod
    def coerce_assay_ids(cls, v):
        if isinstance(v, list):
            return [int(x) for x in v if x is not None and str(x).strip()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, (str, float)):
            s = str(v).strip()
            if not s or s.lower() == "nan":
                return []
            return [int(m) for m in _ASSAY_ID_RE.findall(s)]
        if v is None:
            return []
        return [int(x) for x in v]

    @field_validator("json_metadata", mode="before")
    @classmethod
    def normalize_json_metadata(cls, v):
        if isinstance(v, dict):
            return _minify_json_string(v)
        if isinstance(v, str):
            return _minify_json_string(v)
        if v is None:
            return "{}"
        return str(v)


# ── 5. RowOutcome ─────────────────────────────────────────────────────────


class RowOutcome(BaseModel):
    status: Literal["success", "skipped", "failed"]
    reason: Optional[str] = None
    sample_id: Optional[int] = None
    assays_linked_count: Optional[int] = None
    uid_generated: bool = False
    topo_level: Optional[int] = None
    parent_changed: bool = False

    model_config = ConfigDict(extra="forbid")


# ── 6. PermissionCreate ───────────────────────────────────────────────────


class PermissionCreate(BaseModel):
    contributor_type: str = "Project"
    contributor_id: int
    policy_id: int
    access_type: Literal[1, 2, 3, 4] = 4

    model_config = ConfigDict(extra="forbid")

    @field_validator("contributor_id")
    @classmethod
    def positive_contributor(cls, v):
        if v <= 0:
            raise ValueError("contributor_id must be positive")
        return v

    @field_validator("policy_id")
    @classmethod
    def positive_policy(cls, v):
        if v <= 0:
            raise ValueError("policy_id must be positive")
        return v

    @field_validator("contributor_type")
    @classmethod
    def uppercase_contributor_type(cls, v):
        return v.strip().title()


# ── 7. Neo4jRuntimeConfig ─────────────────────────────────────────────────


class Neo4jRuntimeConfig(BaseModel):
    NEO4J_DB: str = ""
    URI: str = ""
    NEO4J_USER: str = ""
    PASSWORD: str = ""
    NEO4J_UPLOAD_ENABLED: bool = False
    NEO4J_NODE_CHUNK: int = 10_000
    NEO4J_REL_CHUNK: int = 20_000
    NEO4J_DEBUG: bool = False
    DRIVER_AVAILABLE: bool = False
    MISSING_KEYS: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


# ── 8. Metrics ─────────────────────────────────────────────────────────────


class Metrics(BaseModel):
    nodes_input: int = 0
    nodes_created: int = 0
    nodes_matched: int = 0
    rels_input: int = 0
    rels_created: int = 0
    rels_matched: int = 0
    eligible_children: int = 0
    # Children whose metadata names a parent UID that matches no row in
    # `samples`, so no edge could even be BUILT for it. Complements
    # derived_from_rels_dropped below, which is the other half of the same
    # loss: the row was built and the graph had no node to hang it on.
    skipped_children_missing_parents: int = 0
    derived_from_rels_created: int = 0
    # Same reasoning as in_study_rels_dropped: rows whose Sample node did not
    # exist are dropped by the MERGE's double MATCH with no error. The
    # attempted half is already `rels_input` above, so only the shortfall is
    # new; a caller can now check created + dropped == rels_input without
    # reading the log.
    derived_from_rels_dropped: int = 0
    # Parent-changed children whose stored json_metadata could not be read, so
    # the stale-edge delete declined to run for them at all. The third loss
    # class in this family and counted for the same reason as the other two:
    # the delete quietly not happening is indistinguishable from a clean run,
    # and it leaves a removed parent's edge in the graph.
    parent_changed_children_skipped_unreadable: int = 0
    of_type_rels_created: int = 0
    in_study_rels_created: int = 0
    # #44: IN_STUDY rows whose Sample or Study node did not exist are dropped by the
    # MERGE's MATCH with no error. `attempted - created` is the only evidence, so it
    # is recorded rather than inferred, and the drop count also lands in
    # in_study_warnings so an existing reader of that field sees it.
    in_study_rels_attempted: int = 0
    in_study_rels_dropped: int = 0
    in_study_warnings: int = 0
    # Children whose json_metadata Protocol named no SOP, so their DERIVED_FROM
    # edge carries no protocol. Same reasoning as in_study_rels_dropped above:
    # a silent null is indistinguishable from "this sample has no protocol",
    # so it is counted rather than inferred.
    protocols_unresolved: int = 0
    # Protocol values pointing at a SOP hosted elsewhere. Counted apart from
    # protocols_unresolved because these are fine: the edge correctly has no
    # LOCAL protocol, and folding them together would bury the real failures.
    protocols_external_links: int = 0
    sample_type_nodes_created: int = 0
    study_nodes_created: int = 0
    investigation_nodes_created: int = 0
    in_investigation_rels_created: int = 0
    elapsed_ms_total: float = 0.0
    per_chunk_timings: List[dict] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


# ── 9. NodeRow ─────────────────────────────────────────────────────────────


class NodeRow(BaseModel):
    sample_id: int
    sample_uuid: str
    sample_type: str
    properties: Dict[str, Any] = Field(default_factory=dict)
    parent_titles: List[str] = Field(default_factory=list)
    parent_title_hashes: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    @field_validator("sample_id")
    @classmethod
    def positive_id(cls, v):
        if v <= 0:
            raise ValueError("sample_id must be positive")
        return v

    @field_validator("sample_uuid")
    @classmethod
    def non_empty_uuid(cls, v):
        if not v or not v.strip():
            raise ValueError("sample_uuid must be non-empty")
        return v.strip()


# ── 10. RelRow ─────────────────────────────────────────────────────────────


class RelRow(BaseModel):
    child_id: int
    child_uuid: str
    parent_uuid: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("child_id")
    @classmethod
    def positive_child_id(cls, v):
        if v <= 0:
            raise ValueError("child_id must be positive")
        return v

    @field_validator("child_uuid", "parent_uuid")
    @classmethod
    def non_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("uuid must be non-empty")
        return v.strip()


# ── 11. DerivedFromRelRow ──────────────────────────────────────────────────


class DerivedFromRelRow(BaseModel):
    child_id: int
    child_uuid: str
    parent_id: int
    parent_uuid: str
    protocol_id: Optional[int] = None
    protocol_title: Optional[str] = None
    assay_id: Optional[int] = None
    internal_assay_id: Optional[int] = None
    internal_assay_title: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("child_id", "parent_id")
    @classmethod
    def positive_ids(cls, v):
        if v <= 0:
            raise ValueError("id must be positive")
        return v

    @field_validator("child_uuid", "parent_uuid")
    @classmethod
    def non_empty_uuids(cls, v):
        if not v or not v.strip():
            raise ValueError("uuid must be non-empty")
        return v.strip()


# ── 12. OfTypeRelRow ──────────────────────────────────────────────────────


class OfTypeRelRow(BaseModel):
    sample_id: int
    sample_uuid: str
    sample_type_id: int

    model_config = ConfigDict(extra="forbid")

    @field_validator("sample_id", "sample_type_id")
    @classmethod
    def positive_ids(cls, v):
        if v <= 0:
            raise ValueError("id must be positive")
        return v


# ── 12b. InStudyRelRow ───────────────────────────────────────────────────


class InStudyRelRow(BaseModel):
    sample_uuid: str
    study_id: int
    model_config = ConfigDict(extra="forbid")

    @field_validator("study_id")
    @classmethod
    def positive_study_id(cls, v):
        if v <= 0:
            raise ValueError("study_id must be positive")
        return v

    @field_validator("sample_uuid")
    @classmethod
    def non_empty_uuid(cls, v):
        if not v or not v.strip():
            raise ValueError("sample_uuid must be non-empty")
        return v.strip()


# ── 12c. StudyNodeRow ────────────────────────────────────────────────────


class StudyNodeRow(BaseModel):
    id: int
    title: str
    description: str = ""
    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def non_empty_title(cls, v):
        if not v or not v.strip():
            raise ValueError("title must be non-empty")
        return v.strip()


# ── 12d. InvestigationNodeRow ────────────────────────────────────────────


class InvestigationNodeRow(BaseModel):
    id: int
    title: str
    description: str = ""
    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def non_empty_title(cls, v):
        if not v or not v.strip():
            raise ValueError("title must be non-empty")
        return v.strip()


# ── 12e. InInvestigationRelRow ───────────────────────────────────────────


class InInvestigationRelRow(BaseModel):
    study_id: int
    investigation_id: int
    model_config = ConfigDict(extra="forbid")

    @field_validator("study_id", "investigation_id")
    @classmethod
    def positive_ids(cls, v):
        if v <= 0:
            raise ValueError("id must be positive")
        return v


# ── 13. SampleTypeNodeRow ─────────────────────────────────────────────────


class SampleTypeNodeRow(BaseModel):
    id: Optional[int] = None
    title: str

    model_config = ConfigDict(extra="forbid")

    @field_validator("title")
    @classmethod
    def non_empty_title(cls, v):
        if not v or not v.strip():
            raise ValueError("title must be non-empty")
        return v.strip()


# ── 14. PayloadStats ──────────────────────────────────────────────────────


class PayloadStats(BaseModel):
    eligible_children: int = 0
    skipped_children_missing_parents: int = 0
    created_count: int = 0

    model_config = ConfigDict(extra="forbid")


# ── 15. ConstraintStatus ──────────────────────────────────────────────────


class ConstraintStatus(BaseModel):
    sample_id: str = "skipped"
    sample_uuid: str = "skipped"
    sample_type_id: str = "skipped"
    sample_type_title: str = "skipped"

    model_config = ConfigDict(extra="forbid")


# ── 16. StreamResult ──────────────────────────────────────────────────────


@dataclass
class StreamResult:
    row_index: int
    data: Optional[InputRowModel]
    errors: List[str] = field(default_factory=list)


# ── 17. BatchResult ───────────────────────────────────────────────────────


@dataclass
class BatchResult:
    inserted_count: int = 0
    linked_project_count: int = 0
    linked_assays_count: int = 0
    outcomes: Dict[str, RowOutcome] = field(default_factory=dict)
    attempted_uids: Set[str] = field(default_factory=set)
    stopped_early: bool = False
    permissions_inserted_count: int = 0
    updated_count: int = 0


# ── DirectionComputation ──────────────────────────────────────────────────


@dataclass(frozen=True)
class DirectionComputation:
    direction_by_pair: Dict[Tuple[str, int], int]  # (uid, assay_id) -> 1|2
    parents_of: Dict[str, Set[str]]  # child_uid -> set(parent_uids)
    assays_by_uid: Dict[str, Set[int]]  # uid -> set(assay_ids)
    child_uids_by_assay: Dict[int, Set[str]]  # assay_id -> set(child_uids)
    conflicts_by_assay: Dict[int, Set[str]]  # assay_id -> set(uids with conflict)


# ── 18. InstructionRow ────────────────────────────────────────────────────


class InstructionRow(BaseModel):
    """One row from the INSTRUCTIONS sheet of a traditional 4-sheet upload."""
    field: str = Field(..., description="Human-readable column name in SAMPLES sheet")
    database_field: str = Field(..., description="SampleType::AttributeName format")
    field_type: Optional[str] = Field(None, description="Text, Number, Date, or Controlled Ontology")
    ontology: Optional[str] = Field(None, description="Ontology column name in ONTOLOGY sheet")
    model_config = ConfigDict(extra="forbid")

    @field_validator("database_field")
    @classmethod
    def must_contain_delimiter(cls, v):
        if "::" not in v:
            raise ValueError("database_field must be in 'SampleType::AttributeName' format")
        return v

    @property
    def sample_type(self) -> str:
        return self.database_field.split("::")[0]

    @property
    def attribute_name(self) -> str:
        return self.database_field.split("::")[1]


# ── 19. AssaySheetRow ─────────────────────────────────────────────────────


class AssaySheetRow(BaseModel):
    """One row from the ASSAY sheet. 'Assay' column contains the integer assay_id."""
    sample_type: str = Field(..., alias="SampleType")
    assay_type: Optional[str] = Field(None, alias="AssayType")
    assay_id: int = Field(..., alias="Assay")
    direction: int = Field(..., alias="Direction")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("direction")
    @classmethod
    def valid_direction(cls, v):
        if v not in (1, 2):
            raise ValueError("direction must be 1 or 2")
        return v


# ── 20. OntologySpec ──────────────────────────────────────────────────────


class OntologySpec(BaseModel):
    """Maps a sample attribute to its allowed controlled vocabulary terms."""
    attribute_name: str
    vocab_name: str
    allowed_terms: List[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


# ── 21. OntologyViolation ────────────────────────────────────────────────


class OntologyViolation(BaseModel):
    """One ontology validation failure."""
    row_index: int
    attribute: str
    value: str
    vocab_name: str
    allowed_terms: List[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


# ── 22. OntologyValidationResult ─────────────────────────────────────────


class OntologyValidationResult(BaseModel):
    """Aggregated result of ontology validation across all rows."""
    is_valid: bool = True
    violations: List[OntologyViolation] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


# ── 23. ConvertedBatch ───────────────────────────────────────────────────


class ConvertedBatch(BaseModel):
    """Output of converting one or more files into InputRowModel lists."""
    rows: List[InputRowModel] = Field(default_factory=list)
    source_files: List[str] = Field(default_factory=list)
    ontology_result: Optional[OntologyValidationResult] = None
    warnings: List[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


# ── 24. BatchUploadError / BatchUploadTotals / BatchUploadResult ──────────


class BatchUploadError(BaseModel):
    """One entry in the orchestrator's terminal ``errors[]`` list.

    Produced by ErrorCollector during pipeline execution and projected to this
    public shape before being returned as part of BatchUploadResult /
    ValidationResult. ``row`` and ``uid`` locate the offending row when the
    error is attributable to one; both are None for whole-file or stage-level
    errors. The internal RowError additionally carries ``severity``, which is
    not surfaced here — so an entry in this list does NOT by itself mean the
    sheet failed. ``ValidationResult.valid`` is computed from the severities
    before this projection drops them (see ``validation._has_blocking_error``);
    WARNING/INFO entries are advisory and are reported on valid sheets too.
    """
    type: str = Field(..., description="ErrorType enum value, e.g. 'VALIDATION_ATTRIBUTE_NAME'.")
    message: str = Field(..., description="Human-readable error message.")
    row: Optional[int] = Field(
        None,
        description=(
            "0-based row index of the offending row within the SAMPLES data, "
            "when the error is attributable to a specific row. None for "
            "whole-file / stage-level errors."
        ),
    )
    uid: Optional[str] = Field(
        None,
        description=(
            "UID of the offending row, when it has one (update rows, or rows "
            "with a pre-assigned UID). New samples have no UID at validation "
            "time, so this is None for them — use `row` to locate them."
        ),
    )


class BatchUploadErrorGroup(BaseModel):
    """A set of /validate/ errors collapsed by identical ``(type, message)``.

    Issue C of the 2026-05-22 /validate/ UI handoff: a sheet where many rows
    fail the same check produced one flat ``BatchUploadError`` per row —
    visually identical lines differing only in ``row``. Grouping by
    ``(type, message)`` gives the UI one block per distinct failure while
    ``count`` / ``rows`` / ``uids`` preserve which rows were affected.

    This is a presentation aid layered on top of the flat ``errors`` list; it
    is NOT a cap — ``errors`` still carries every error (see Issue B).
    """
    type: str = Field(..., description="ErrorType enum value shared by every error in the group.")
    message: str = Field(..., description="Human-readable message shared by every error in the group.")
    count: int = Field(..., description="Number of errors in the group.")
    rows: List[int] = Field(
        default_factory=list,
        description="Sorted, de-duplicated 0-based row indices of the affected rows. Row-less (whole-file) errors contribute to `count` but not here.",
    )
    uids: List[str] = Field(
        default_factory=list,
        description="Sorted, de-duplicated UIDs of affected rows that had a pre-assigned one.",
    )


class BatchUploadTotals(BaseModel):
    """The ``totals`` sub-dict in the orchestrator's terminal result.

    Tracks per-stage row counts plus pipeline outcome flags. Populated by
    the Stage 7 REPORT step in orchestrator.py:run_batch_upload_multi.
    Distinct from the in-flight Metrics dataclass used during INSERT.
    """
    processed: int
    success: int
    skipped: int
    failed: int
    elapsed_s: float = 0.0
    throughput_rps: float = 0.0
    permissions_inserted: int = 0
    uids_generated: int = 0
    updated: int = 0
    error: Optional[str] = Field(None, description="One-line error summary when the pipeline aborted before completion.")
    cancelled: bool = Field(False, description="True if the job was cancelled via the user-stop callback.")


class BatchUploadResult(BaseModel):
    """Typed wrapper for the orchestrator's terminal result dict.

    This is the SOURCE OF TRUTH for the shape returned by
    ``run_batch_upload_multi``, ``_error_result``, and ``_cancelled_result``.
    Both endpoints expose it: ``/start/``'s status endpoint nests it under
    ``result``, and ``/validate/`` extends it via ValidationResult.

    NOTE — DO NOT CONFUSE WITH ``BatchResult`` (this module, above). That
    dataclass is INSERT-stage internal state (counts of inserts, linked
    projects, outcomes by UID). BatchUploadResult is the END-OF-PIPELINE
    public shape.

    The orchestrator currently returns this as a plain dict for Celery
    serialization compatibility. Conversion happens at endpoint boundaries
    via ``BatchUploadResult.model_validate(orch_dict)``. A contract test in
    ``test_orchestrator_result_contract.py`` asserts the dict-to-model
    round-trip stays valid as the pipeline evolves.
    """
    job_id: Optional[str] = Field(None, description="Celery task UUID. None for sync /validate/ responses.")
    summary_path: Optional[str] = Field(None, description="On-disk path to the summary CSV. None for /validate/ (no CSV written).")
    totals: BatchUploadTotals
    errors: List[BatchUploadError] = Field(default_factory=list)
    warnings: dict = Field(default_factory=dict, description="Free-form warnings keyed by category (e.g. 'convert_warnings').")


class ValidationResult(BatchUploadResult):
    """Response shape for POST /api/batch-upload/validate/.

    Extends BatchUploadResult with four validation-specific fields:
    valid, summary, checks_run, checks_skipped. Inherits all pipeline-result
    fields (totals, errors, warnings) so consumers see one flat object — no
    nested ``result`` wrapper.

    Production by ``run_validation_multi``: orchestrator dict is converted via
    ``BatchUploadResult.model_validate``, the four extra fields are computed
    from the validation flow, and the combined model is returned to the
    /validate/ view.

    See ALSO the existing dataclass ``BatchResult`` (this module) — different
    concept; it is INSERT-stage internal state, not a pipeline result.
    """
    valid: bool = Field(
        ...,
        description=(
            "True iff no collected entry carried a blocking severity "
            "(ERROR/CRITICAL; WARNING and INFO do not block) AND `totals.error` "
            "is None AND `totals.failed` is 0. A valid sheet may still list "
            "advisory entries in `errors` — e.g. a parent UID not yet uploaded."
        ),
    )
    summary: str = Field(..., description="One-line human summary, e.g. '2 distinct errors (5 total)', 'No blocking errors (3 non-blocking issues)' or 'No issues'.")
    checks_run: List[str] = Field(..., description="Validation stages that executed for this request.")
    checks_skipped: List[str] = Field(default_factory=list, description="Stages NOT requested via the `checks` parameter.")
    error_groups: List[BatchUploadErrorGroup] = Field(
        default_factory=list,
        description="`errors` collapsed by identical (type, message); each group lists every affected row/uid. A presentation aid — `errors` remains the complete, uncapped list. Empty when there are no errors.",
    )
