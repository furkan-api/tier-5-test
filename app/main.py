from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.search import router as search_router
from app.core.vectordb import connect_milvus


@asynccontextmanager
async def lifespan(app: FastAPI):
    connect_milvus()
    yield


app = FastAPI(
    title="Legal RAG",
    description="Turkish legal jurisprudence retrieval API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(search_router)
