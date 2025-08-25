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
