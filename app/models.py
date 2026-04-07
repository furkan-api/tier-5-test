from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    use_graph: bool = Field(default=True)


class DocumentResult(BaseModel):
    doc_id: str
    filename: str = ""
    score: float
    court: str = ""
    daire: str = ""
    decision_date: str = ""
    esas_no: str = ""
    karar_no: str = ""
    graph_score: float = 0.0
    is_graph_expansion: bool = False
    pagerank_score: float = 0.0


class SearchResponse(BaseModel):
    query: str
    results: list[DocumentResult]
    total: int
