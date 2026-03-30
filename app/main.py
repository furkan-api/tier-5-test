import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.search import router as search_router
from app.core.graphdb import close_neo4j, connect_neo4j
from app.core.vectordb import connect_milvus

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    connect_milvus()
    try:
        connect_neo4j()
    except Exception as e:
        log.warning("Neo4j not available at startup: %s — graph retrieval will fall back to dense", e)
    yield
    close_neo4j()


app = FastAPI(
    title="Legal RAG",
    description="Turkish legal jurisprudence retrieval API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(search_router)
