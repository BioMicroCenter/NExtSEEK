from django.db import models

# Pydantic models for API request/response validation (JSON:API + SOPs)
from typing import Any, Dict, List, Optional, Literal, Union, Callable

from pydantic import BaseModel, Field, ConfigDict, model_validator

# Import SOP DB helper to resolve NExtSEEK UID -> SEEK id when provided in payloads
from seek.dbtable_sops import DBtable_sops
from seek.dbtable_projects import DBtable_projects


# -----------------------------
# Generic JSON:API scaffolding
# -----------------------------

class JsonApiVersion(BaseModel):
    version: str


class IndexLinks(BaseModel):
    self: str
    first: Optional[str] = None
    prev: Optional[str] = None
    next: Optional[str] = None
    last: Optional[str] = None


class CollectionLinks(BaseModel):
    self: str
    items: str


class Links(BaseModel):
    self: str


class BaseMeta(BaseModel):
    base_url: str
    api_version: str


class Meta(BaseModel):
    base_url: Optional[str] = None
    api_version: Optional[str] = None
    created: Optional[Any] = None


class JsonApiErrorSource(BaseModel):
    pointer: Optional[str] = None
    parameter: Optional[str] = None


class JsonApiError(BaseModel):
    id: Optional[str] = None
    status: Optional[str] = None
    code: Optional[str] = None
    title: Optional[str] = None
    detail: Optional[str] = None
    source: Optional[JsonApiErrorSource] = None


class JsonApiErrorResponse(BaseModel):
    errors: List[JsonApiError]


class ItemReference(BaseModel):
    id: str
    type: str


class SingleReference(BaseModel):
    data: ItemReference


class MultipleReferences(BaseModel):
    data: List[ItemReference] = Field(default_factory=list)


# -----------------------------
# SOP: create/patch request models
# -----------------------------

class ContentBlobSlot(BaseModel):
    # Minimal content blob slot compatible with SEEK examples
    original_filename: Optional[str] = None
    content_type: Optional[str] = None
    url: Optional[str] = None


class PolicyPermission(BaseModel):
    resource: ItemReference
    access: str


class Policy(BaseModel):
    access: Optional[str] = None
    permissions: Optional[List[PolicyPermission]] = None


class SopPostAttributes(BaseModel):
    title: str
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    license: Optional[str] = None
    policy: Optional[Policy] = None
    content_blobs: List[ContentBlobSlot]
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None


class SopPostRelationships(BaseModel):
    projects: MultipleReferences
    creators: Optional[MultipleReferences] = None
    assays: Optional[MultipleReferences] = None
    workflows: Optional[MultipleReferences] = None


class SopPostData(BaseModel):
    type: Literal['sops']
    attributes: SopPostAttributes
    relationships: SopPostRelationships


class SopCreateRequest(BaseModel):
    data: SopPostData


class SopPatchAttributes(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    license: Optional[str] = None
    policy: Optional[Policy] = None
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None


class SopPatchRelationships(BaseModel):
    creators: Optional[MultipleReferences] = None
    projects: Optional[MultipleReferences] = None
    assays: Optional[MultipleReferences] = None
    workflows: Optional[MultipleReferences] = None


def _resolve_sop_uid_to_id(uid: str) -> Optional[str]:
    """Resolve a NExtSEEK SOP UID (stored in title) to a SEEK numeric id as string.
    Returns None if not uniquely resolvable.
    """
    try:
        dbsop = DBtable_sops("DEFAULT")
        constraint = {"title": uid}
        records = dbsop.queryRecordsByConstraint(constraint)
        if isinstance(records, list) and len(records) == 1:
            rid = records[0].get('id')
            return str(rid) if rid is not None else None
        return None
    except Exception:
        return None


class SopPatchData(BaseModel):
    type: Literal['sops']
    # SEEK expects 'id', but we allow 'uid' as an alternative input for convenience.
    id: Optional[str] = None
    uid: Optional[str] = None
    attributes: Optional[SopPatchAttributes] = None
    relationships: Optional[SopPatchRelationships] = None


class SopUpdateRequest(BaseModel):
    data: SopPatchData

    model_config = ConfigDict(extra='allow')

    def to_seek_payload(self) -> Dict[str, Any]:
        """
        Convert this update request into a SEEK-compliant JSON:API payload.
        - If 'data.id' is provided (or 'data.uid' is numeric), use it as-is.
        - Else if 'data.uid' is provided and is non-numeric, resolve to SEEK id via SOP resolver.
        - Replace 'uid' with 'id' in the outgoing payload; all other fields remain unchanged.
        """
        # Extract candidate id/uid
        data_obj = self.data
        candidate_id: Optional[str] = data_obj.id
        candidate_uid: Optional[str] = data_obj.uid

        resolved_id: Optional[str] = None

        if candidate_id:
            resolved_id = str(candidate_id)
        elif candidate_uid:
            # If numeric, treat as SEEK id
            if str(candidate_uid).isdigit():
                resolved_id = str(candidate_uid)
            else:
                resolved_id = _resolve_sop_uid_to_id(str(candidate_uid))

        # Build outgoing payload
        payload: Dict[str, Any] = {
            "data": {
                "type": self.data.type,
            }
        }

        if resolved_id is not None:
            payload["data"]["id"] = resolved_id

        if self.data.attributes is not None:
            payload["data"]["attributes"] = self.data.attributes.model_dump(exclude_none=True)

        if self.data.relationships is not None:
            payload["data"]["relationships"] = self.data.relationships.model_dump(exclude_none=True)

        return payload


# -----------------------------
# SOP: response models
# -----------------------------

class SopRelationships(BaseModel):
    creators: MultipleReferences
    submitter: MultipleReferences
    people: MultipleReferences
    projects: MultipleReferences
    investigations: MultipleReferences
    studies: MultipleReferences
    assays: MultipleReferences
    publications: MultipleReferences
    workflows: MultipleReferences


class SopResponseData(BaseModel):
    id: str
    type: Literal['sops']
    attributes: Dict[str, Any]
    relationships: SopRelationships
    links: Links
    meta: Meta


class SopSingleResponse(BaseModel):
    data: SopResponseData
    jsonapi: Optional[JsonApiVersion] = None


class SopIndexAttributes(BaseModel):
    title: str


class SopIndexItem(BaseModel):
    id: str
    type: Literal['sops']
    attributes: SopIndexAttributes
    links: Links


class SopListResponse(BaseModel):
    data: List[SopIndexItem]
    jsonapi: JsonApiVersion
    links: IndexLinks
    meta: BaseMeta


# -----------------------------
# DataFiles: constants
# -----------------------------

DATAFILE_TYPE = "data_files"


# -----------------------------
# DataFiles: request models
# -----------------------------

class RemoteContentBlob(BaseModel):
    url: str
    original_filename: Optional[str] = None
    content_type: Optional[str] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ContentBlobPlaceholder(BaseModel):
    original_filename: str
    content_type: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


ContentBlobSlotUnion = Union[RemoteContentBlob, ContentBlobPlaceholder]


class DataFilePostAttributes(BaseModel):
    title: str
    content_blobs: List[ContentBlobSlotUnion]
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    data_type_annotations: Optional[List[str]] = None
    data_format_annotations: Optional[List[str]] = None
    license: Optional[str] = None
    policy: Optional[Dict[str, Any]] = None
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFilePostRelationships(BaseModel):
    projects: MultipleReferences
    creators: Optional[MultipleReferences] = None
    assays: Optional[MultipleReferences] = None
    publications: Optional[MultipleReferences] = None
    events: Optional[MultipleReferences] = None
    workflows: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFilePostData(BaseModel):
    type: Literal['data_files']
    attributes: DataFilePostAttributes
    relationships: DataFilePostRelationships

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFileCreateRequest(BaseModel):
    data: DataFilePostData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        # Guarantee JSON:API type constant
        payload['data']['type'] = DATAFILE_TYPE
        return payload


class DataFilePatchAttributes(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    data_type_annotations: Optional[List[str]] = None
    data_format_annotations: Optional[List[str]] = None
    license: Optional[str] = None
    policy: Optional[Dict[str, Any]] = None
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFilePatchRelationships(BaseModel):
    projects: Optional[MultipleReferences] = None
    creators: Optional[MultipleReferences] = None
    assays: Optional[MultipleReferences] = None
    publications: Optional[MultipleReferences] = None
    events: Optional[MultipleReferences] = None
    workflows: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFilePatchData(BaseModel):
    id: str
    type: Literal['data_files']
    attributes: Optional[DataFilePatchAttributes] = None
    relationships: Optional[DataFilePatchRelationships] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFileUpdateRequest(BaseModel):
    data: DataFilePatchData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": {"id": self.data.id, "type": self.data.type}}
        if self.data.attributes is not None:
            payload['data']['attributes'] = self.data.attributes.model_dump(exclude_none=True)
        if self.data.relationships is not None:
            payload['data']['relationships'] = self.data.relationships.model_dump(exclude_none=True)
        # Guarantee JSON:API type constant
        payload['data']['type'] = DATAFILE_TYPE
        return payload


# -----------------------------
# DataFiles: response models
# -----------------------------

class DataFileIndexAttributes(BaseModel):
    title: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFileIndexItem(BaseModel):
    id: str
    type: Literal['data_files']
    attributes: DataFileIndexAttributes
    links: Links

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFileListResponse(BaseModel):
    data: List[DataFileIndexItem]
    jsonapi: JsonApiVersion
    links: IndexLinks
    meta: BaseMeta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFileResponseData(BaseModel):
    id: str
    type: Literal['data_files']
    attributes: Dict[str, Any]
    relationships: Dict[str, Any]
    links: Links
    meta: Meta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DataFileSingleResponse(BaseModel):
    data: DataFileResponseData
    jsonapi: Optional[JsonApiVersion] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Projects: constants
# -----------------------------

PROJECTS_TYPE = "projects"


# -----------------------------
# Projects: list/index models
# -----------------------------

class ProjectIndexAttributes(BaseModel):
    title: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectIndexItem(BaseModel):
    id: str
    type: Literal['projects']
    attributes: ProjectIndexAttributes
    links: Links

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectListResponse(BaseModel):
    data: List[ProjectIndexItem]
    jsonapi: JsonApiVersion
    links: IndexLinks
    meta: BaseMeta

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Projects: detail models
# -----------------------------

class ProjectAttributes(BaseModel):
    avatar: Optional[Any] = None
    title: str
    description: Optional[str] = None
    web_page: Optional[str] = None
    wiki_page: Optional[str] = None
    default_policy: Optional[Dict[str, Any]] = None
    default_license: Optional[str] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None
    topic_annotations: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectRelationships(BaseModel):
    people: MultipleReferences
    projects: MultipleReferences
    institutions: MultipleReferences
    investigations: MultipleReferences
    studies: MultipleReferences
    assays: MultipleReferences
    data_files: MultipleReferences
    documents: MultipleReferences
    models: MultipleReferences
    sops: MultipleReferences
    publications: MultipleReferences
    presentations: MultipleReferences
    events: MultipleReferences
    workflows: MultipleReferences
    collections: MultipleReferences

    model_config = ConfigDict(extra='allow', validate_default=True)


class ProjectResponseData(BaseModel):
    id: str
    type: Literal['projects']
    attributes: Dict[str, Any]
    relationships: Dict[str, Any]
    links: Links
    meta: Meta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectSingleResponse(BaseModel):
    data: ProjectResponseData
    jsonapi: Optional[JsonApiVersion] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Projects: request models
# -----------------------------

class ProjectPostAttributes(BaseModel):
    title: str
    avatar: Optional[Any] = None
    description: Optional[str] = None
    web_page: Optional[str] = None
    wiki_page: Optional[str] = None
    default_policy: Optional[Dict[str, Any]] = None
    default_license: Optional[str] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None
    topic_annotations: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectPostRelationships(BaseModel):
    programmes: Optional[MultipleReferences] = None
    organisms: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectPostData(BaseModel):
    type: Literal['projects']
    attributes: ProjectPostAttributes
    relationships: Optional[ProjectPostRelationships] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectCreateRequest(BaseModel):
    data: ProjectPostData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        # Guarantee JSON:API type constant
        payload['data']['type'] = PROJECTS_TYPE
        return payload


class ProjectPatchAttributes(BaseModel):
    title: Optional[str] = None
    avatar: Optional[Any] = None
    description: Optional[str] = None
    web_page: Optional[str] = None
    wiki_page: Optional[str] = None
    default_policy: Optional[Dict[str, Any]] = None
    default_license: Optional[str] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None
    topic_annotations: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectPatchRelationships(BaseModel):
    programmes: Optional[MultipleReferences] = None
    organisms: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectPatchData(BaseModel):
    id: Optional[str] = None
    type: Literal['projects']
    attributes: Optional[ProjectPatchAttributes] = None
    relationships: Optional[ProjectPatchRelationships] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class ProjectUpdateRequest(BaseModel):
    data: ProjectPatchData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": {"type": PROJECTS_TYPE}}
        if self.data.id is not None:
            payload['data']['id'] = str(self.data.id)
        if self.data.attributes is not None:
            payload['data']['attributes'] = self.data.attributes.model_dump(exclude_none=True)
        if self.data.relationships is not None:
            payload['data']['relationships'] = self.data.relationships.model_dump(exclude_none=True)
        return payload


# -----------------------------
# People: constants
# -----------------------------

PEOPLE_TYPE = "people"


# -----------------------------
# People: list/index models
# -----------------------------

class PersonIndexAttributes(BaseModel):
    title: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class PersonIndexItem(BaseModel):
    id: str
    type: Literal['people']
    attributes: PersonIndexAttributes
    links: Links

    model_config = ConfigDict(extra='forbid', validate_default=True)


class PersonListResponse(BaseModel):
    data: List[PersonIndexItem]
    jsonapi: JsonApiVersion
    links: IndexLinks
    meta: BaseMeta

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# People: detail models
# -----------------------------

class PersonResponseData(BaseModel):
    id: str
    type: Literal['people']
    attributes: Dict[str, Any]
    relationships: Dict[str, Any]
    links: Links
    meta: Meta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class PersonSingleResponse(BaseModel):
    data: PersonResponseData
    jsonapi: Optional[JsonApiVersion] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# People: request models
# -----------------------------

class PersonPostAttributes(BaseModel):
    first_name: str
    last_name: str
    email: str
    description: Optional[str] = None
    web_page: Optional[str] = None
    orcid: Optional[str] = None
    expertise: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    phone: Optional[str] = None
    skype_name: Optional[str] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class PersonPostData(BaseModel):
    type: Literal['people']
    attributes: PersonPostAttributes

    model_config = ConfigDict(extra='forbid', validate_default=True)


class PersonCreateRequest(BaseModel):
    data: PersonPostData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        payload['data']['type'] = PEOPLE_TYPE
        return payload


class PersonPatchAttributes(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    description: Optional[str] = None
    web_page: Optional[str] = None
    orcid: Optional[str] = None
    expertise: Optional[List[str]] = None
    tools: Optional[List[str]] = None
    phone: Optional[str] = None
    skype_name: Optional[str] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class PersonPatchData(BaseModel):
    id: Optional[str] = None
    type: Literal['people']
    attributes: Optional[PersonPatchAttributes] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class PersonUpdateRequest(BaseModel):
    data: PersonPatchData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": {"type": PEOPLE_TYPE}}
        if self.data.id is not None:
            payload['data']['id'] = str(self.data.id)
        if self.data.attributes is not None:
            payload['data']['attributes'] = self.data.attributes.model_dump(exclude_none=True)
        return payload


# -----------------------------
# Investigations: constants
# -----------------------------

INVESTIGATIONS_TYPE = "investigations"


# -----------------------------
# Investigations: list/index models
# -----------------------------

class InvestigationIndexAttributes(BaseModel):
    title: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationIndexItem(BaseModel):
    id: str
    type: Literal['investigations']
    attributes: InvestigationIndexAttributes
    links: Links

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationListResponse(BaseModel):
    data: List[InvestigationIndexItem]
    jsonapi: JsonApiVersion
    links: IndexLinks
    meta: BaseMeta

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Investigations: detail models
# -----------------------------

class InvestigationResponseData(BaseModel):
    id: str
    type: Literal['investigations']
    attributes: Dict[str, Any]
    relationships: Dict[str, Any]
    links: Links
    meta: Meta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationSingleResponse(BaseModel):
    data: InvestigationResponseData
    jsonapi: Optional[JsonApiVersion] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Investigations: request models
# -----------------------------

class InvestigationPostAttributes(BaseModel):
    title: str
    description: Optional[str] = None
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationPostRelationships(BaseModel):
    projects: MultipleReferences
    creators: Optional[MultipleReferences] = None
    publications: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationPostData(BaseModel):
    type: Literal['investigations']
    attributes: InvestigationPostAttributes
    relationships: InvestigationPostRelationships

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationCreateRequest(BaseModel):
    data: InvestigationPostData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        payload['data']['type'] = INVESTIGATIONS_TYPE
        return payload


class InvestigationPatchAttributes(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationPatchRelationships(BaseModel):
    projects: Optional[MultipleReferences] = None
    creators: Optional[MultipleReferences] = None
    publications: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationPatchData(BaseModel):
    id: str
    type: Literal['investigations']
    attributes: Optional[InvestigationPatchAttributes] = None
    relationships: Optional[InvestigationPatchRelationships] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class InvestigationUpdateRequest(BaseModel):
    data: InvestigationPatchData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": {"id": self.data.id, "type": self.data.type}}
        if self.data.attributes is not None:
            payload['data']['attributes'] = self.data.attributes.model_dump(exclude_none=True)
        if self.data.relationships is not None:
            payload['data']['relationships'] = self.data.relationships.model_dump(exclude_none=True)
        payload['data']['type'] = INVESTIGATIONS_TYPE
        return payload


# -----------------------------
# Assays: constants
# -----------------------------

ASSAYS_TYPE = "assays"


# -----------------------------
# Assays: list/index models
# -----------------------------

class AssayIndexAttributes(BaseModel):
    title: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayIndexItem(BaseModel):
    id: str
    type: Literal['assays']
    attributes: AssayIndexAttributes
    links: Links

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayListResponse(BaseModel):
    data: List[AssayIndexItem]
    jsonapi: JsonApiVersion
    links: IndexLinks
    meta: BaseMeta

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Assays: request models
# -----------------------------

class AssayClass(BaseModel):
    # Restrict to EXP (experimental) or MOD (modelling)
    key: Literal['EXP', 'MOD']

    model_config = ConfigDict(extra='forbid', validate_default=True)


class OntologyRef(BaseModel):
    uri: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayPostAttributes(BaseModel):
    title: str
    description: Optional[str] = None
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    assay_class: AssayClass
    assay_type: OntologyRef
    technology_type: Optional[OntologyRef] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayPostRelationships(BaseModel):
    study: SingleReference
    creators: Optional[MultipleReferences] = None
    publications: Optional[MultipleReferences] = None
    data_files: Optional[MultipleReferences] = None
    samples: Optional[MultipleReferences] = None
    documents: Optional[MultipleReferences] = None
    models: Optional[MultipleReferences] = None
    sops: Optional[MultipleReferences] = None
    organisms: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayPostData(BaseModel):
    type: Literal['assays']
    attributes: AssayPostAttributes
    relationships: AssayPostRelationships

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayCreateRequest(BaseModel):
    data: AssayPostData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        payload['data']['type'] = ASSAYS_TYPE
        return payload


class AssayPatchAttributes(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    other_creators: Optional[str] = None
    creators: Optional[List[Dict[str, Any]]] = None
    assay_class: Optional[AssayClass] = None
    assay_type: Optional[OntologyRef] = None
    technology_type: Optional[OntologyRef] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    extended_attributes: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayPatchRelationships(BaseModel):
    study: Optional[SingleReference] = None
    creators: Optional[MultipleReferences] = None
    publications: Optional[MultipleReferences] = None
    data_files: Optional[MultipleReferences] = None
    samples: Optional[MultipleReferences] = None
    documents: Optional[MultipleReferences] = None
    models: Optional[MultipleReferences] = None
    sops: Optional[MultipleReferences] = None
    organisms: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayPatchData(BaseModel):
    id: str
    type: Literal['assays']
    attributes: Optional[AssayPatchAttributes] = None
    relationships: Optional[AssayPatchRelationships] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayUpdateRequest(BaseModel):
    data: AssayPatchData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": {"id": self.data.id, "type": self.data.type}}
        if self.data.attributes is not None:
            payload['data']['attributes'] = self.data.attributes.model_dump(exclude_none=True)
        if self.data.relationships is not None:
            payload['data']['relationships'] = self.data.relationships.model_dump(exclude_none=True)
        payload['data']['type'] = ASSAYS_TYPE
        return payload


# -----------------------------
# Assays: response models
# -----------------------------

class AssayRelationships(BaseModel):
    creators: MultipleReferences
    submitter: MultipleReferences
    organisms: MultipleReferences
    people: MultipleReferences
    projects: MultipleReferences
    investigation: SingleReference
    study: SingleReference
    data_files: MultipleReferences
    samples: MultipleReferences
    documents: MultipleReferences
    models: MultipleReferences
    sops: MultipleReferences
    publications: MultipleReferences
    placeholders: MultipleReferences
    human_diseases: MultipleReferences

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssayResponseData(BaseModel):
    id: str
    type: Literal['assays']
    attributes: Dict[str, Any]
    relationships: AssayRelationships
    links: Links
    meta: Meta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AssaySingleResponse(BaseModel):
    data: AssayResponseData
    jsonapi: Optional[JsonApiVersion] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# SampleTypes: constants
# -----------------------------

SAMPLE_TYPES_TYPE = "sample_types"


# -----------------------------
# SampleTypes: enums & helpers
# -----------------------------

from enum import Enum


class SampleAttributeBaseType(str, Enum):
    Integer = "Integer"
    Float = "Float"
    String = "String"
    DateTime = "DateTime"
    Date = "Date"
    Text = "Text"
    Boolean = "Boolean"
    SeekStrain = "SeekStrain"
    SeekSample = "SeekSample"
    CV = "CV"
    CVList = "CVList"
    SeekDataFile = "SeekDataFile"
    SeekSop = "SeekSop"
    SeekSampleMulti = "SeekSampleMulti"


class SampleAttributeTypeIdRef(BaseModel):
    id: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleAttributeTypeTitleRef(BaseModel):
    title: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


SampleAttributeTypeRef = Union[SampleAttributeTypeIdRef, SampleAttributeTypeTitleRef]


# -----------------------------
# SampleTypes: request models
# -----------------------------

class SampleTypeSampleAttributePost(BaseModel):
    title: str
    sample_attribute_type: SampleAttributeTypeRef
    required: bool = False
    description: Optional[str] = None
    pid: Optional[str] = None
    pos: Optional[int] = None
    unit: Optional[str] = None
    is_title: Optional[bool] = None
    sample_controlled_vocab_id: Optional[str] = None
    linked_sample_type_id: Optional[str] = None
    linked_extended_metadata_type_id: Optional[str] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeSampleAttributePatch(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = None
    sample_attribute_type: Optional[SampleAttributeTypeRef] = None
    required: Optional[bool] = None
    description: Optional[str] = None
    pid: Optional[str] = None
    pos: Optional[int] = None
    unit: Optional[str] = None
    is_title: Optional[bool] = None
    sample_controlled_vocab_id: Optional[str] = None
    linked_sample_type_id: Optional[str] = None
    linked_extended_metadata_type_id: Optional[str] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypePostAttributes(BaseModel):
    title: str
    description: Optional[str] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    sample_attributes: List[SampleTypeSampleAttributePost]
    tags: Optional[List[str]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypePostRelationships(BaseModel):
    projects: MultipleReferences
    assays: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypePostData(BaseModel):
    type: Literal['sample_types']
    attributes: SampleTypePostAttributes
    relationships: SampleTypePostRelationships

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeCreateRequest(BaseModel):
    data: SampleTypePostData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        payload['data']['type'] = SAMPLE_TYPES_TYPE
        return payload


class SampleTypePatchAttributes(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    tags: Optional[List[str]] = None
    sample_attributes: Optional[List[SampleTypeSampleAttributePatch]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypePatchRelationships(BaseModel):
    projects: Optional[MultipleReferences] = None
    assays: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypePatchData(BaseModel):
    id: str
    type: Literal['sample_types']
    attributes: Optional[SampleTypePatchAttributes] = None
    relationships: Optional[SampleTypePatchRelationships] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeUpdateRequest(BaseModel):
    data: SampleTypePatchData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": {"id": self.data.id, "type": self.data.type}}
        if self.data.attributes is not None:
            payload['data']['attributes'] = self.data.attributes.model_dump(exclude_none=True)
        if self.data.relationships is not None:
            payload['data']['relationships'] = self.data.relationships.model_dump(exclude_none=True)
        payload['data']['type'] = SAMPLE_TYPES_TYPE
        return payload


# -----------------------------
# SampleTypes: response models
# -----------------------------

class SampleTypeIndexAttributes(BaseModel):
    title: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeIndexItem(BaseModel):
    id: str
    type: Literal['sample_types']
    attributes: SampleTypeIndexAttributes
    links: Links

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeListResponse(BaseModel):
    data: List[SampleTypeIndexItem]
    jsonapi: JsonApiVersion
    links: IndexLinks
    meta: BaseMeta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleAttributeTypeDetail(BaseModel):
    id: str
    title: str
    base_type: SampleAttributeBaseType
    regexp: Optional[str] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeSampleAttributeResponse(BaseModel):
    id: Optional[str] = None
    title: str
    sample_attribute_type: SampleAttributeTypeDetail
    required: Optional[bool] = None
    pos: Optional[int] = None
    unit: Optional[str] = None
    is_title: Optional[bool] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeRelationships(BaseModel):
    submitter: MultipleReferences
    projects: MultipleReferences
    assays: MultipleReferences
    samples: MultipleReferences

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeResponseData(BaseModel):
    id: str
    type: Literal['sample_types']
    attributes: Dict[str, Any]
    relationships: SampleTypeRelationships
    links: Links
    meta: Meta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTypeSingleResponse(BaseModel):
    data: SampleTypeResponseData
    jsonapi: Optional[JsonApiVersion] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Samples: constants
# -----------------------------

SAMPLES_TYPE = "samples"


# -----------------------------
# Samples: request models
# -----------------------------


class SamplePostAttributes(BaseModel):
    title: str
    tags: Optional[List[str]] = None
    other_creators: Optional[str] = None
    attribute_map: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    creators: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SamplePostRelationships(BaseModel):
    sample_type: SingleReference
    creators: Optional[MultipleReferences] = None
    projects: Optional[MultipleReferences] = None
    people: Optional[MultipleReferences] = None
    data_files: Optional[MultipleReferences] = None
    assays: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SamplePostData(BaseModel):
    type: Literal['samples']
    attributes: SamplePostAttributes
    relationships: SamplePostRelationships

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleCreateRequest(BaseModel):
    data: SamplePostData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self, db_resolver=None) -> Dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        # Guarantee JSON:API type constant
        payload['data']['type'] = SAMPLES_TYPE

        # Optionally normalize relationship ids via provided resolver
        if db_resolver:
            try:
                rels = payload.get('data', {}).get('relationships', {})
                for key, rel in list(rels.items()):
                    if isinstance(rel, dict) and 'data' in rel:
                        if isinstance(rel['data'], dict):
                            ref = rel['data']
                            rid = ref.get('id')
                            rtype = ref.get('type')
                            if rid is not None and isinstance(rid, str) and not rid.isdigit():
                                mapped = db_resolver(rtype, rid)
                                if mapped:
                                    ref['id'] = str(mapped)
                        elif isinstance(rel['data'], list):
                            new_list = []
                            for ref in rel['data']:
                                rid = ref.get('id')
                                rtype = ref.get('type')
                                if rid is not None and isinstance(rid, str) and not rid.isdigit():
                                    mapped = db_resolver(rtype, rid)
                                    if mapped:
                                        ref['id'] = str(mapped)
                                new_list.append(ref)
                            rel['data'] = new_list
            except Exception:
                # Best-effort normalization; ignore resolver errors
                pass
        return payload


class SamplePatchAttributes(BaseModel):
    title: Optional[str] = None
    tags: Optional[List[str]] = None
    other_creators: Optional[str] = None
    attribute_map: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None
    discussion_links: Optional[List[Dict[str, Any]]] = None
    creators: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SamplePatchRelationships(BaseModel):
    sample_type: Optional[SingleReference] = None
    creators: Optional[MultipleReferences] = None
    projects: Optional[MultipleReferences] = None
    people: Optional[MultipleReferences] = None
    data_files: Optional[MultipleReferences] = None
    assays: Optional[MultipleReferences] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SamplePatchData(BaseModel):
    id: Optional[str] = None
    type: Literal['samples']
    attributes: Optional[SamplePatchAttributes] = None
    relationships: Optional[SamplePatchRelationships] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleUpdateRequest(BaseModel):
    data: SamplePatchData

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_seek_payload(self, db_resolver=None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"data": {"type": SAMPLES_TYPE}}
        if self.data.id is not None:
            payload['data']['id'] = str(self.data.id)
        if self.data.attributes is not None:
            payload['data']['attributes'] = self.data.attributes.model_dump(exclude_none=True)
        if self.data.relationships is not None:
            payload['data']['relationships'] = self.data.relationships.model_dump(exclude_none=True)

        # Optionally normalize relationship ids
        if db_resolver and 'relationships' in payload['data']:
            try:
                rels = payload['data']['relationships']
                for key, rel in list(rels.items()):
                    if isinstance(rel, dict) and 'data' in rel:
                        if isinstance(rel['data'], dict):
                            ref = rel['data']
                            rid = ref.get('id')
                            rtype = ref.get('type')
                            if rid is not None and isinstance(rid, str) and not rid.isdigit():
                                mapped = db_resolver(rtype, rid)
                                if mapped:
                                    ref['id'] = str(mapped)
                        elif isinstance(rel['data'], list):
                            new_list = []
                            for ref in rel['data']:
                                rid = ref.get('id')
                                rtype = ref.get('type')
                                if rid is not None and isinstance(rid, str) and not rid.isdigit():
                                    mapped = db_resolver(rtype, rid)
                                    if mapped:
                                        ref['id'] = str(mapped)
                                new_list.append(ref)
                            rel['data'] = new_list
            except Exception:
                pass
        return payload


# -----------------------------
# Samples: response models
# -----------------------------


class SampleRelationships(BaseModel):
    sample_type: SingleReference
    creators: MultipleReferences
    projects: MultipleReferences
    people: MultipleReferences
    assays: MultipleReferences
    data_files: MultipleReferences

    model_config = ConfigDict(extra='allow', validate_default=True)


class SampleResponseData(BaseModel):
    id: str
    type: Literal['samples']
    attributes: Dict[str, Any]
    relationships: SampleRelationships
    links: Links
    meta: Meta

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleSingleResponse(BaseModel):
    data: SampleResponseData
    jsonapi: Optional[JsonApiVersion] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Samples: advanced search models
# -----------------------------


class AdvancedSearchMatchType(str, Enum):
    EXACT = "EXACT"
    PARTIAL = "PARTIAL"

class FilterLogic(str, Enum):
    AND = "AND"
    OR = "OR"


class SampleAdvancedSearchRequest(BaseModel):
    sampletype: Optional[Union[str, List[str]]] = None
    attribute: Optional[Union[str, List[str]]] = None
    filter_searchText: Union[str, List[str]] = Field(..., description='Search text or list of search texts')
    filter_matchType: AdvancedSearchMatchType = AdvancedSearchMatchType.PARTIAL
    attribute_logic: Optional[FilterLogic] = None
    searchText_logic: Optional[FilterLogic] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def to_db_filters(self, sampletype_resolver: Optional[Callable[[str], Optional[str]]] = None) -> Dict[str, Any]:
        """
        Normalize request into DB helper filters.
        - Accept string or list for 'sampletype' and 'attribute'.
        - For sample types: strip whitespace, keep case; resolve titles when possible; drop unresolvable; dedupe.
        - Upstream sampletype_id: singleton id or 0. We also return 'sampletype_ids' for post-filter OR.
        - Attributes return as normalized lowercase list in 'attribute_list'.
        """
        # sampletype → list[str]
        sampletype_items: List[str] = []
        if self.sampletype is not None:
            if isinstance(self.sampletype, list):
                for it in self.sampletype:
                    if it is None:
                        continue
                    s = str(it).strip()
                    if s:
                        sampletype_items.append(s)
            else:
                s = str(self.sampletype).strip()
                if s:
                    sampletype_items.append(s)

        # Resolve sample types to ids (drop unresolvable), dedupe preserving order
        sampletype_ids: List[int] = []
        seen_ids: set = set()
        for item in sampletype_items:
            if item.isdigit():
                val = int(item)
            else:
                val = None
                if sampletype_resolver is not None:
                    resolved: Optional[str] = sampletype_resolver(item)
                    if resolved is not None and str(resolved).isdigit():
                        val = int(str(resolved))
            if val is not None and val not in seen_ids:
                seen_ids.add(val)
                sampletype_ids.append(val)

        if len(sampletype_ids) == 1:
            sampletype_id_upstream = sampletype_ids[0]
        else:
            sampletype_id_upstream = 0

        # attribute → list[str] (normalized lower for view matching)
        attribute_list: List[str] = []
        if self.attribute is not None:
            if isinstance(self.attribute, list):
                for it in self.attribute:
                    if it is None:
                        continue
                    s = str(it).strip().lower()
                    if s:
                        attribute_list.append(s)
            else:
                s = str(self.attribute).strip().lower()
                if s:
                    attribute_list.append(s)

        # Determine attribute logic default
        if self.attribute_logic is not None:
            attribute_logic_val = self.attribute_logic.value
        else:
            attribute_logic_val = "OR" if len(attribute_list) > 1 else None
        
        #  determine search text logic default
        if self.searchText_logic is not None:
            searchText_logic_val = self.searchText_logic.value
        else:
            searchText_logic_val = "OR" if isinstance(self.filter_searchText, list) and len(self.filter_searchText) > 1 else None

        return {
            "filter_searchText": self.filter_searchText,
            "filter_matchType": self.filter_matchType.value,
            "sampletype_id": sampletype_id_upstream,
            "sampletype_ids": sampletype_ids,
            "attribute": 'none',
            "attribute_list": attribute_list,
            "attribute_logic": attribute_logic_val,
            "searchText_logic": searchText_logic_val,
        }


class SampleAdvancedSearchRow(BaseModel):
    id: Union[str, int] = Field(..., description='Numeric SEEK ID of the sample')
    uuid: Optional[str] = Field(None, description='UUID of the sample')
    sample_type_id: Union[str, int] = Field(..., description='Numeric SEEK ID of the sample type')
    sample_type: Optional[str] = Field(None, description='Title of the sample type')
    json_metadata: Dict[str, Any] = Field(default_factory=dict, description='JSON metadata of the sample')
    attributeValue: str = Field(..., description='Value of the attribute')

    model_config = ConfigDict(extra='allow', validate_default=True)


class SampleAdvancedSearchResult(BaseModel):
    total: int
    rows: List[SampleAdvancedSearchRow]
    footer: Optional[List[Any]] = None
    sampleTypes: Optional[List[str]] = None
    noSampleTypes: Optional[int] = None
    msg: Optional[str] = None
    status: Optional[int] = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Additional request/response models
# -----------------------------

# Imports already present at file scope: List, Optional, BaseModel, Field

class SamplesByChildTypesRequest(BaseModel):
    child_sample_types: Optional[Union[List[int], List[str]]] = Field(
        None, description="Child sample type titles to match (logical AND)"
    )
    parent_sample_type_filters: Optional[Union[List[int], List[str]]] = Field(
        None, description="Parent sample type titles or SEEK IDs to filter by (logical OR)"
    )


class SampleUIDItem(BaseModel):
    id: str = Field(..., description="SEEK numeric id (stringified)")
    uuid: str = Field(..., description="Sample UID")
    sample_type: Union[int, str] = Field(..., description="Sample type title or SEEK ID")
    sample_type_description: Optional[str] = Field(None, description="Sample type description")


class SamplesByChildTypesResponse(BaseModel):
    total: int = Field(..., description="Total number of matching samples")
    samples: List[SampleUIDItem] = Field(..., description="List of parent sample IDs and UIDs")
    msg: Optional[str] = Field(None, description="Status message")


class ChildSampleTypeItem(BaseModel):
    id: str = Field(..., description="Sample type SEEK numeric id (stringified)")
    title: str = Field(..., description="Sample type title (e.g., PAV, D.SEQ)")
    description: Optional[str] = Field(None, description="Sample type description")


class ChildSampleTypesResponse(BaseModel):
    total: int = Field(..., description="Total number of unique child sample types")
    child_types: List[ChildSampleTypeItem] = Field(..., description="List of child sample type details")
    msg: Optional[str] = Field(None, description="Status message")


class SampleTreeNode(BaseModel):
    id: str = Field(..., description="SEEK numeric id (stringified)")
    uuid: str = Field(..., description="Sample UID")
    sample_type: str = Field(..., description="Sample type title (e.g., PAT, TIS, D.IMG)")
    color: str = Field(..., description="Hex color for the sample type")
    parentIds: List[str] = Field(default_factory=list, description="List of parent SEEK ids (stringified)")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SampleTreeRel(BaseModel):
    child_id: Optional[str] = Field(None, description="Child sample SEEK id (numeric string)")
    parent_id: Optional[str] = Field(None, description="Parent sample SEEK id (numeric string)")
    internal_assay_id: Optional[str] = Field(None, description="Internal assay id (numeric string, if available)")
    internal_assay_title: Optional[str] = Field(None, description="Internal assay title (if available)")
    protocol_title: Optional[str] = Field(None, description="Protocol title (if available)")
    protocol_id: Optional[str] = Field(None, description="Protocol id (numeric string, if available)")

    # Neo4j relationship properties may evolve; allow extras while strongly typing known keys.
    model_config = ConfigDict(extra='allow', validate_default=True)


class SampleTreeResponse(BaseModel):
    total_nodes: int = Field(..., description="Total number of unique nodes in the tree")
    total_rels: int = Field(..., description="Total number of relationships in the tree")
    nodes: List[SampleTreeNode] = Field(..., description="List of nodes for visualization")
    rels: List[SampleTreeRel] = Field(..., description="List of relationship property dicts")

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Admin samples: request/response models
# -----------------------------

class AdminSampleRetrieveRequest(BaseModel):
    identifiers: List[str] = Field(
        ...,
        description="List of Sample UIDs (e.g., 'NHP-220630FLY-1-PUB') and/or SEEK IDs (numeric strings). "
                    "Numeric strings are treated as SEEK IDs; non-numeric strings are treated as sample UIDs."
    )
    output_format: Literal["json", "excel"] = Field("json", description="Output format")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AdminSampleGroup(BaseModel):
    sample_type: str = Field(..., description="Sample type identifier (e.g., 'NHP', 'TIS')")
    n_samples: int = Field(..., description="Number of samples returned for this sample type")
    samples: List[Dict[str, Any]] = Field(..., description="List of sample metadata dicts")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class AdminSampleRetrieveResponse(BaseModel):
    total_samples: int = Field(
        ...,
        description="Total samples returned across all sample types; includes results for both parents and child (derived) samples."
    )
    total_sample_types: int = Field(..., description="Number of distinct sample types")
    total_children: int = Field(..., description="Total child samples (derived) included")
    failed_uids: int = Field(0, description="Count of identifiers not found")
    data: List[AdminSampleGroup] = Field(..., description="Samples grouped by type")

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# Schema RAG: request/response models
# -----------------------------

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nextseek_api.schema_rag.models import MinimalAPIEndpoint, FullAPIEndpoint


class IngestRequest(BaseModel):
    """Request to ingest an OpenAPI schema for RAG retrieval."""
    schema_url: str = Field(..., description="URL of the OpenAPI schema to ingest")
    ttl_minutes: Optional[int] = Field(None, description="Session TTL in minutes (uses default if not provided)")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class IngestResponse(BaseModel):
    """Successful response from schema ingestion."""
    session_id: str = Field(..., description="Unique session identifier for retrieval")
    schema_url: str = Field(..., description="URL of the ingested schema")
    ttl_minutes: int = Field(..., description="Session TTL in minutes")
    expires_at: datetime = Field(..., description="When the session will expire")
    num_endpoints: int = Field(..., description="Number of endpoints indexed")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class IngestError(BaseModel):
    """Error details for failed ingestion."""
    error_code: str = Field(..., description="Symbolic error code from schema_rag/errors.py")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class IngestResult(BaseModel):
    """Discriminated union result for ingestion operations."""
    success: bool = Field(..., description="True if ingestion succeeded")
    response: Optional[IngestResponse] = Field(None, description="Populated when success=True")
    error: Optional[IngestError] = Field(None, description="Populated when success=False")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class RetrieveDebugInfo(BaseModel):
    """Debug metadata for retrieval operations."""
    used_fallback_full: bool = Field(..., description="Whether full mode fallback was used")
    used_enriched_minimal_examples: bool = Field(..., description="Whether enriched minimal examples were used")
    similarity_threshold_effective: float = Field(..., description="Effective similarity threshold used")
    num_candidates_initial: int = Field(..., description="Number of initial candidates before filtering")
    num_candidates_final: int = Field(..., description="Number of final candidates returned")
    scores: Optional[List[float]] = Field(None, description="Final similarity scores for returned endpoints")
    error_code: Optional[str] = Field(None, description="Error code if partial failure")
    error_details: Optional[dict] = Field(None, description="Error details if partial failure")

    model_config = ConfigDict(extra='forbid', validate_default=True)


ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class RetrieveRequest(BaseModel):
    """Request to retrieve relevant API endpoints from an ingested schema."""
    session_id: Optional[str] = Field(None, description="Session ID from prior ingestion")
    schema_url: Optional[str] = Field(None, description="Schema URL to look up existing session")
    query: str = Field(..., description="Natural language query describing the desired operation")
    resolved_terms: Optional[Dict[str, Any]] = Field(None, description="Pre-resolved entity terms")
    mode: Literal["minimal", "full"] = Field("minimal", description="Response detail level")
    top_k: Union[int, Literal["ALL"]] = Field(5, description="Maximum number of endpoints to return. Use 'ALL' to return all matching endpoints up to the ingestion ceiling.")
    min_score: Optional[float] = Field(None, description="Minimum similarity score threshold")
    include_debug: bool = Field(True, description="Whether to include debug info in response")
    filter_by: Optional[Literal["METHOD", "TAG"]] = Field(None, description="Filter type: 'METHOD' to filter by HTTP method, 'TAG' to filter by endpoint tag")
    filter_terms: Optional[List[str]] = Field(None, description="Values to filter by. For METHOD: HTTP methods (GET, POST, etc.). For TAG: tag names.")

    model_config = ConfigDict(extra='forbid', validate_default=True)

    @model_validator(mode="after")
    def _validate_filters_and_top_k(self) -> "RetrieveRequest":
        # filter_by and filter_terms must be provided together
        if self.filter_by is not None and not self.filter_terms:
            raise ValueError("filter_terms is required when filter_by is set")
        if self.filter_terms is not None and self.filter_by is None:
            raise ValueError("filter_by is required when filter_terms is set")

        # Validate METHOD filter_terms against allowed methods
        if self.filter_by == "METHOD" and self.filter_terms:
            normalized = []
            for term in self.filter_terms:
                upper = term.upper()
                if upper not in ALLOWED_METHODS:
                    raise ValueError(
                        f"Invalid HTTP method '{term}'. Allowed: {sorted(ALLOWED_METHODS)}"
                    )
                normalized.append(upper)
            self.filter_terms = normalized

        # Validate integer top_k >= 1
        if isinstance(self.top_k, int) and self.top_k < 1:
            raise ValueError("top_k must be >= 1")

        return self


class RetrieveResponse(BaseModel):
    """Response containing retrieved API endpoints."""
    session_id: str = Field(..., description="Session ID used for retrieval")
    mode: Literal["minimal", "full"] = Field(..., description="Response detail level")
    endpoints_minimal: Optional[List["MinimalAPIEndpoint"]] = Field(
        None, description="Minimal endpoint info (when mode='minimal')"
    )
    endpoints_full: Optional[List["FullAPIEndpoint"]] = Field(
        None, description="Full endpoint info (when mode='full')"
    )
    message: Optional[str] = Field(None, description="Optional message (e.g., warnings)")
    debug: Optional[RetrieveDebugInfo] = Field(None, description="Debug info (when include_debug=True)")

    model_config = ConfigDict(extra='forbid', validate_default=True)


# Rebuild models with forward references when schema_rag package is available
def _rebuild_schema_rag_models():
    """Call this after schema_rag.models is imported to resolve forward references."""
    try:
        from nextseek_api.schema_rag.models import MinimalAPIEndpoint, FullAPIEndpoint
        RetrieveResponse.model_rebuild()
    except ImportError:
        pass  # schema_rag package not yet available


# -----------------------------
# EntityTree: request/response models
# -----------------------------


class NodeAttribute(BaseModel):
    """Node metadata from dmac.sample_types_context."""

    node: str = Field(..., description="Sample type abbreviation (e.g., 'NHP', 'TIS')")
    id: int = Field(..., description="Sample type ID (sample_types_context.sampletype_id)")
    description: Optional[str] = Field(None, description="Human-readable description")
    clade: Optional[str] = Field(None, description="Clade classification")
    metadata_fields: str = Field(
        ...,
        description="Semicolon-separated sample attribute titles for this sample type (delimiter '; ')",
    )

    model_config = ConfigDict(extra='forbid', validate_default=True)


class NodeAttributeResponse(BaseModel):
    """Response model for node attributes endpoint."""

    total: int = Field(..., description="Total number of node attributes (unpaginated)")
    nodes: List[NodeAttribute] = Field(..., description="List of node attributes (may be paginated)")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class Edge(BaseModel):
    """A single directed edge between sample types."""

    source: str = Field(..., description="Source sample type")
    target: str = Field(..., description="Target sample type")
    annotation: str = Field(..., description="Edge label (internal assay title)")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class EdgeResponse(BaseModel):
    """Response model for edges endpoint."""

    total: int = Field(..., description="Total number of edges (unpaginated)")
    edges: List[Edge] = Field(..., description="List of edges (may be paginated)")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class EdgeAttribute(BaseModel):
    """Edge metadata with provenance information."""

    source: str = Field(..., description="Source sample type")
    target: str = Field(..., description="Target sample type")
    annotation: str = Field(..., description="Internal assay title")
    internal_assay_id: Optional[str] = Field(None, description="ID from dmac.internal_assays table (string)")
    study_titles: Optional[str] = Field(None, description="Semicolon-separated study titles")
    description: Optional[str] = Field(None, description="Assay description from dmac.assay_context.Description")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class EdgeAttributeResponse(BaseModel):
    """Response model for edge attributes endpoint."""

    total: int = Field(..., description="Total number of edge attributes (unpaginated)")
    edges: List[EdgeAttribute] = Field(..., description="List of edge attributes (may be paginated)")

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# EntityTree Lineage: request/response models
# -----------------------------


class LineageRequest(BaseModel):
    """Request body for POST /entity_tree/lineage endpoint."""

    sample_ids: List[str] = Field(
        ...,
        description="List of sample identifiers: UIDs (e.g., 'NHP-220630FLY-1-PUB') or numeric SEEK IDs",
    )
    include_attributes: bool = Field(
        default=False,
        description="When true, include edge attributes (study_titles, description)",
    )
    timeout: Optional[float] = Field(
        default=90.0,
        ge=1.0,
        le=300.0,
        description="Query timeout in seconds (default 90, max 300)",
    )

    model_config = ConfigDict(extra="forbid", validate_default=True)


class LineageEdge(BaseModel):
    """An edge within a sample lineage tree."""

    child_id: str = Field(..., description="Child sample SEEK id (numeric string)")
    parent_id: str = Field(..., description="Parent sample SEEK id (numeric string)")
    internal_assay_id: Optional[str] = Field(None, description="Internal assay id")
    internal_assay_title: Optional[str] = Field(None, description="Internal assay title")
    protocol_title: Optional[str] = Field(None, description="Protocol title")
    protocol_id: Optional[str] = Field(None, description="Protocol id")
    study_titles: Optional[str] = Field(
        None, description="Semicolon-separated study titles (when include_attributes=true)"
    )
    description: Optional[str] = Field(
        None, description="Assay description (when include_attributes=true)"
    )

    model_config = ConfigDict(extra="forbid", validate_default=True)


class LineageTree(BaseModel):
    """Lineage tree for a single input sample."""

    input_id: str = Field(..., description="Original input identifier (for correlation)")
    resolved_id: Optional[str] = Field(
        None, description="Resolved SEEK numeric id (null if not found)"
    )
    total_nodes: int = Field(..., description="Total node count (unpaginated)")
    total_rels: int = Field(..., description="Total relationship count")
    nodes: List[SampleTreeNode] = Field(
        default_factory=list, description="List of nodes (may be paginated)"
    )
    rels: List[LineageEdge] = Field(default_factory=list, description="List of relationships")
    warning: Optional[str] = Field(
        None, description="Warning message (e.g., sample not found)"
    )

    model_config = ConfigDict(extra="forbid", validate_default=True)


class LineageResponse(BaseModel):
    """Response model for POST /entity_tree/lineage endpoint."""

    total_trees: int = Field(..., description="Number of trees returned")
    trees: List[LineageTree] = Field(
        ..., description="Array of lineage trees, one per input sample"
    )

    model_config = ConfigDict(extra="forbid", validate_default=True)


# -----------------------------
# Content Blob: SEEK path param models
# -----------------------------


class SeekContentBlobPathParams(BaseModel):
    """Maps directly to SEEK's ``/{asset_types}/{id}/content_blobs/{blob_id}`` path segments."""

    asset_types: str = Field(..., description="SEEK asset type path segment, e.g. 'sops' or 'data_files'")
    id: int = Field(..., description="SEEK numeric asset id (path param {id})")
    blob_id: int = Field(..., description="SEEK numeric content-blob id (path param {blob_id})")

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SeekContentBlobReadRequest(BaseModel):
    """Internal request model for ``GET /{asset_types}/{id}/content_blobs/{blob_id}`` (readContentBlob).

    Supports optional format conversion via the ``Accept`` header.
    """

    path: SeekContentBlobPathParams
    output_format: Optional[Literal["json", "csv"]] = Field(
        None,
        description=(
            "Desired conversion format. "
            "None → Accept: */* (original representation); "
            "'json' → Accept: application/json; "
            "'csv' → Accept: text/csv"
        ),
    )

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def accept_header(self) -> str:
        """Return the ``Accept`` header value for the upstream SEEK request."""
        if self.output_format == "json":
            return "application/json"
        if self.output_format == "csv":
            return "text/csv"
        return "*/*"


class SeekContentBlobDownloadRequest(BaseModel):
    """Internal request model for ``GET /{asset_types}/{id}/content_blobs/{blob_id}/download``
    (downloadAssetContent).  Binary download — no conversion fields.
    """

    path: SeekContentBlobPathParams

    model_config = ConfigDict(extra='forbid', validate_default=True)


# -----------------------------
# SOP Download: public request model
# -----------------------------


class SopDownloadRequest(BaseModel):
    """Public request body for ``POST /sops/download``.

    The client provides a SOP identifier (NExtSEEK UID or numeric SEEK ID).
    The endpoint resolves the identifier, fetches the latest SOP version from
    SEEK, discovers the content blob, and streams the download.
    """

    uid_or_id: str = Field(
        ...,
        description=(
            "SOP identifier: a numeric SEEK SOP id (string) or a NExtSEEK SOP UID. "
            "Numeric strings are treated as SEEK IDs; non-numeric strings are resolved via the SOP title."
        ),
    )
    output_format: Optional[Literal["original", "csv", "json", "binary"]] = Field(
        None,
        description=(
            "Desired output format. Maps to SEEK endpoints as follows: "
            "'binary' or None/'original' → GET /{asset_types}/{id}/content_blobs/{blob_id}/download (binary stream); "
            "'csv' → GET /{asset_types}/{id}/content_blobs/{blob_id} with Accept: text/csv; "
            "'json' → GET /{asset_types}/{id}/content_blobs/{blob_id} with Accept: application/json."
        ),
    )
    
    asset_types: Optional[str] = Field(
        None,
        description="Override for SEEK path param {asset_types}. Default internally is 'sops'.",
    )
    seek_id: Optional[int] = Field(
        None,
        description="Override for SEEK path param {id}. Default comes from resolving uid_or_id.",
    )
    blob_id: Optional[int] = Field(
        None,
        description="Override for SEEK path param {blob_id}. Default comes from SOP metadata content_blobs.",
    )

    model_config = ConfigDict(extra='forbid', validate_default=True)


class SopBatchDownloadManifestEntry(BaseModel):
    """Single entry (success) in the batch download manifest."""
    filename: str
    seek_id: str
    uid_or_id: str
    model_config = ConfigDict(extra='forbid')


class SopBatchDownloadManifestFailure(BaseModel):
    """Single failure entry in the batch download manifest."""
    uid_or_id: str
    error: str
    model_config = ConfigDict(extra='forbid')


class SopBatchDownloadManifest(BaseModel):
    """manifest.json written inside the zip."""
    generated_at: str
    total_requested: int
    total_success: int
    total_failed: int
    successes: List[SopBatchDownloadManifestEntry] = Field(default_factory=list)
    failures: List[SopBatchDownloadManifestFailure] = Field(default_factory=list)
    model_config = ConfigDict(extra='forbid')
