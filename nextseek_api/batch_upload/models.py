"""All Pydantic models and dataclasses for the batch upload pipeline."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Literal, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
_SOP_URL_RE = re.compile(r"/sops/(\d+)")


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
    File_PrimartyData: Optional[str] = None  # intentional typo from SEEK
    Parent: Optional[str] = None
    Protocol: Optional[str] = None
    Scientist: Optional[str] = None

    model_config = ConfigDict(extra="allow")


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
        if isinstance(v, dict):
            return _minify_json_string(v)
        if isinstance(v, str):
            return _minify_json_string(v)
        if v is None:
            return "{}"
        return str(v)

    @model_validator(mode="after")
    def validate_optional_consistency(self):
        # 1) UID consistency if json_metadata contains UID
        try:
            meta = _json_loads(self.json_metadata) if self.json_metadata else {}
        except Exception:
            meta = {}
        if isinstance(meta, dict) and self.UID is not None:
            meta_uid = meta.get("UID") or meta.get("uid")
            if meta_uid is not None and str(meta_uid).strip() and str(meta_uid).strip() != str(self.UID).strip():
                raise ValueError("UID column does not match json_metadata.UID")

            # 2) SOP consistency if sop_id provided and Protocol encodes SOP
            if self.sop_id is not None:
                protocol_val = meta.get("Protocol") or meta.get("protocol") or ""
                m = _SOP_URL_RE.search(str(protocol_val))
                if m:
                    parsed = int(m.group(1))
                    if int(self.sop_id) != parsed:
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
    skipped_children_missing_parents: int = 0
    derived_from_rels_created: int = 0
    of_type_rels_created: int = 0
    in_study_rels_created: int = 0
    in_study_warnings: int = 0
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
