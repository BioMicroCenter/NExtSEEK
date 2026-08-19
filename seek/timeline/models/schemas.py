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

