from __future__ import annotations

from openai import OpenAI

from app.core.config import get_settings

DIMENSIONS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072, "BAAI/bge-m3": 1024}


def get_embedding_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        api_key=settings.openai_api_key or "local",
        base_url=settings.embedding_base_url,
    )


def embed_texts(client: OpenAI, texts: list[str], model: str | None = None) -> list[list[float]]:
    """Embed a batch of texts. Returns list of float vectors."""
    model = model or get_settings().embedding_model
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]
