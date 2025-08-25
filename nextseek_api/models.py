from django.db import models

# Pydantic models for API request/response validation (JSON:API + SOPs)
from typing import Any, Dict, List, Optional, Literal, Union

from pydantic import BaseModel, Field, ConfigDict

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

