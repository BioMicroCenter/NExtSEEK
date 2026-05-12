# backend/app/models/schemas.py

from pydantic import BaseModel
from typing import Optional, Any

class NHPInfo(BaseModel):
    UID: Optional[Any] = None
        # Allow arbitrary additional fields
    class Config:
        extra = "allow"
        
    def __init__(self, **data):
        super().__init__(**data)

class TimelineEvent(BaseModel):
    nhp_id: Optional[Any] = None
    LINK: Optional[Any] = None
    START_DATE: Optional[Any] = None
    STOP_DATE: Optional[Any] = None
    TYPE: Optional[Any] = None
    PATIENT_ID: Optional[Any] = None
    EVENT_TYPE: Optional[Any] = None
    UID: Optional[Any] = None
    # Allow arbitrary additional fields
    class Config:
        extra = "allow"
        
    def __init__(self, **data):
        super().__init__(**data)


class PAVInfo(BaseModel):
    pass