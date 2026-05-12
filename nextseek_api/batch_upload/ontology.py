"""Bulk ontology validation using Pydantic Literal + TypeAdapter."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional, Tuple, Type, Union

import polars as pl
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, create_model

from .models import InstructionRow, OntologySpec, OntologyValidationResult, OntologyViolation

log = logging.getLogger(__name__)


def load_ontology_from_sheet(
    instructions: List[InstructionRow],
    df_ont: pl.DataFrame,
) -> List[OntologySpec]:
    """Build OntologySpec list from INSTRUCTIONS rows and ONTOLOGY sheet.

    Mirrors legacy loadControlledVocabulary: filter instructions where
    field_type is 'Controlled Ontology', then for each vocab column in
    the Ontology DataFrame collect non-null terms.
    """
    specs: List[OntologySpec] = []
    # instruction.ontology is the column name in ONTOLOGY sheet (vocab name)
    # instruction.attribute_name is the sample attribute we're validating
    controlled: List[Tuple[str, str]] = []  # (attribute_name, vocab_name)
    for ins in instructions:
        if not ins.field_type or str(ins.field_type).strip().lower() != "controlled ontology":
            continue
        vocab_name = (ins.ontology or "").strip()
        if not vocab_name:
            continue
        controlled.append((ins.attribute_name, vocab_name))

    if not controlled:
        return specs

    # Normalize ONTOLOGY df column names for lookup (strip, preserve case for column access)
    ont_columns = {c.strip(): c for c in df_ont.columns}
    vocab_terms: Dict[str, List[str]] = {}  # vocab_name -> list of allowed terms

    for attr_name, vocab_name in controlled:
        col_actual = ont_columns.get(vocab_name)
        if col_actual is None:
            for k, v in ont_columns.items():
                if k.lower() == vocab_name.lower():
                    col_actual = v
                    break
        if col_actual is None or col_actual not in df_ont.columns:
            log.warning("Ontology column %r not found in ONTOLOGY sheet", vocab_name)
            continue

        if vocab_name not in vocab_terms:
            terms_series = df_ont[col_actual].drop_nulls()
            vocab_terms[vocab_name] = [
                str(x).strip() for x in terms_series.to_list() if str(x).strip()
            ]

        terms = vocab_terms[vocab_name]
        specs.append(
            OntologySpec(
                attribute_name=attr_name,
                vocab_name=vocab_name,
                allowed_terms=terms,
            )
        )

    # Dedupe by attribute_name (first wins)
    by_attr: Dict[str, OntologySpec] = {}
    for s in specs:
        if s.attribute_name not in by_attr:
            by_attr[s.attribute_name] = s
    return list(by_attr.values())


def _build_ontology_model(
    specs: List[OntologySpec],
) -> Tuple[Type[BaseModel], Dict[str, Dict[str, str]]]:
    """Build dynamic Pydantic model with Optional[Literal[...]] per controlled attribute."""
    field_defs: Dict[str, Any] = {}
    correction_maps: Dict[str, Dict[str, str]] = {}

    for spec in specs:
        canonical_terms = tuple(t.strip() for t in spec.allowed_terms if t.strip())
        if not canonical_terms:
            continue
        if len(canonical_terms) == 1:
            lit_type = Literal[canonical_terms[0]]
        else:
            lit_type = Literal[canonical_terms[0]]
            for t in canonical_terms[1:]:
                lit_type = Union[lit_type, Literal[t]]  # type: ignore
        field_defs[spec.attribute_name] = (Optional[lit_type], None)
        correction_maps[spec.attribute_name] = {
            t.strip().upper(): t.strip() for t in spec.allowed_terms if t.strip()
        }

    if not field_defs:
        # No controlled fields - model that accepts anything
        DynModel = create_model(
            "OntologyCheck",
            __config__=ConfigDict(extra="ignore"),
        )
        return DynModel, {}

    DynModel = create_model(
        "OntologyCheck",
        __config__=ConfigDict(extra="ignore"),
        **field_defs,
    )
    return DynModel, correction_maps


def _extract_ontology_violations(
    e: ValidationError,
    specs: List[OntologySpec],
    sample_dicts: Optional[List[Dict[str, Any]]] = None,
) -> List[OntologyViolation]:
    """Parse ValidationError from bulk validation into OntologyViolation list."""
    attr_terms: Dict[str, List[str]] = {s.attribute_name: s.allowed_terms for s in specs}
    vocab_by_attr: Dict[str, str] = {s.attribute_name: s.vocab_name for s in specs}
    violations: List[OntologyViolation] = []
    sample_dicts = sample_dicts or []

    for err in e.errors():
        loc = err.get("loc", ())
        if not loc or not isinstance(loc[0], int):
            continue
        row_index = loc[0]
        attr = loc[1] if len(loc) > 1 else "?"
        if isinstance(attr, str) and attr in attr_terms:
            value = "?"
            if row_index < len(sample_dicts):
                value = sample_dicts[row_index].get(attr, "?")
            if not isinstance(value, str):
                value = str(value) if value is not None else "?"
            ctx = err.get("ctx", {})
            if value == "?" and ctx:
                value = str(ctx.get("input", ctx.get("value", value)))
            violations.append(
                OntologyViolation(
                    row_index=row_index,
                    attribute=attr,
                    value=value,
                    vocab_name=vocab_by_attr.get(attr, ""),
                    allowed_terms=attr_terms.get(attr, []),
                )
            )
    return violations


def validate_ontology_bulk(
    sample_dicts: List[Dict[str, Any]],
    specs: List[OntologySpec],
) -> OntologyValidationResult:
    """Validate all sample rows against ontology specs in one bulk pass."""
    if not specs:
        return OntologyValidationResult(is_valid=True)

    DynModel, corrections = _build_ontology_model(specs)
    controlled_attrs = set(corrections.keys())

    # Pre-normalize: auto-correct casing in-place; treat empty string as missing (Optional)
    for row in sample_dicts:
        for attr in controlled_attrs:
            val = row.get(attr)
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                row[attr] = None  # so Optional accepts it
                continue
            if isinstance(val, str):
                canonical = corrections[attr].get(val.strip().upper())
                if canonical:
                    row[attr] = canonical

    adapter: TypeAdapter[List[BaseModel]] = TypeAdapter(List[DynModel])
    try:
        adapter.validate_python(sample_dicts)
        return OntologyValidationResult(is_valid=True)
    except ValidationError as e:
        violations = _extract_ontology_violations(e, specs, sample_dicts)
        return OntologyValidationResult(is_valid=False, violations=violations)
