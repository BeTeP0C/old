import hashlib

from app.core.config import Settings
from app.models.ai_chunk import AI_EMBEDDING_DIMENSION


class EmbeddingClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def embed_text(self, text: str) -> list[float]:
        if not self.settings.embeddings_enabled:
            raise NotImplementedError("embeddings are disabled")
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values = [byte / 255 for byte in digest]
        repeats = (AI_EMBEDDING_DIMENSION // len(values)) + 1
        return (values * repeats)[:AI_EMBEDDING_DIMENSION]
