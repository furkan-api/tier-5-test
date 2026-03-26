from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)


class DocumentResult(BaseModel):
    doc_id: str
    score: float
    court: str = ""
    daire: str = ""
    decision_date: str = ""
    esas_no: str = ""
    karar_no: str = ""


class SearchResponse(BaseModel):
    query: str
    results: list[DocumentResult]
    total: int
