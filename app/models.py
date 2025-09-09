from pydantic import BaseModel, HttpUrl
from typing import List, Optional

class Article(BaseModel):
    id: str
    title: str
    source: str
    summary: Optional[str] = ""
    url: HttpUrl
    thumbnailUrl: Optional[str] = None
    publishedUtc: str
    teams: List[str]
    leagues: List[str]

class NewsResponse(BaseModel):
    items: List[Article]
    page: int
    pageSize: int
    total: int
