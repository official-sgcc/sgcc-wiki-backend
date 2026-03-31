from pydantic import BaseModel
from datetime import datetime
from permissions import Permissions

class WikiDoc(BaseModel):
    title: str
    content: str
    tags: list[str]
    updated_at: datetime
    permissions: Permissions
    

