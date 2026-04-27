#file name: app/schemas.py

from pydantic import BaseModel

class PostCreate(BaseModel):
    title: str
    content: str
class PostResponce(BaseModel):
    title: str
    content: str
