"""Vector storage of long-term facts on top of Qdrant (VS role).

The collection is created lazily on the first write: the embedding
dimensionality is taken from the first real vector instead of being
hardcoded, so switching embedding models never breaks bootstrap.
Point id equals the HardFacts.fact_id — re-upserting a fact
overwrites its vector naturally.
"""

from dataclasses import dataclass
from uuid import UUID

from qdrant_client import AsyncQdrantClient, models

from app.core import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class VectorPoint:
    """One fact prepared for storage: id, vector, searchable text."""

    fact_id: int
    vector: list[float]
    text: str


class VectorMemory:
    """Semantic search over user facts with per-user isolation."""

    def __init__(
        self,
        host: str,
        port: int,
        collection: str,
    ) -> None:
        """Store connection parameters (no I/O here).

        Args:
            host: Qdrant host.
            port: Qdrant HTTP port.
            collection: Target collection name.
        """
        self._client = AsyncQdrantClient(url=f"http://{host}:{port}")
        self._collection = collection
        self._ensured = False

    async def close(self) -> None:
        """Release the underlying HTTP client."""
        await self._client.close()

    async def upsert_points(
        self, user_id: UUID, points: list[VectorPoint]
    ) -> None:
        """Store or overwrite fact vectors for a user.

        Args:
            user_id: Owner stored in payload for filtering.
            points: Prepared points; empty list is a no-op.
        """
        if not points:
            return
        await self._ensure_collection(len(points[0].vector))
        await self._client.upsert(
            collection_name=self._collection,
            points=[
                models.PointStruct(
                    id=point.fact_id,
                    vector=point.vector,
                    payload={
                        "user_id": str(user_id),
                        "fact_id": point.fact_id,
                        "text": point.text,
                    },
                )
                for point in points
            ],
        )
        logger.debug(f"Upserted {len(points)} vectors for {user_id}.")

    async def search(
        self,
        user_id: UUID,
        query_vector: list[float],
        limit: int = 5,
    ) -> list[str]:
        """Find the most similar fact texts of one user.

        Args:
            user_id: Payload filter — never cross user boundaries.
            query_vector: Embedding of the current question/batch.
            limit: Max hits to return.

        Returns:
            Fact texts ordered by similarity; empty list when the
            collection does not exist yet (nothing ever stored).
        """
        if not self._ensured:
            if not await self._client.collection_exists(self._collection):
                return []
            self._ensured = True
        result = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=limit,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=str(user_id)),
                    )
                ]
            ),
            with_payload=True,
        )
        return [
            str(point.payload.get("text", ""))
            for point in result.points
            if point.payload
        ]

    async def reassign_user(self, old_user_id: UUID, new_user_id: UUID) -> int:
        """Re-label stored points after an account merge.

        Used by Telegram pairing: the auto-registered duplicate's
        vectors become the property of the surviving account.

        Args:
            old_user_id: Identity being absorbed.
            new_user_id: Surviving identity.

        Returns:
            None. Completion is reported via logs; the qdrant-client
            response does not expose a reliable updated-count here.
        """
        if not self._ensured:
            if not await self._client.collection_exists(self._collection):
                return 0
            self._ensured = True
        result = await self._client.set_payload(
            collection_name=self._collection,
            payload={"user_id": str(new_user_id)},
            # This client version takes the selector in `points`;
            # a Filter object is a valid selector here.
            points=models.Filter(
                must=[
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=str(old_user_id)),
                    )
                ]
            ),
        )
        operation = getattr(result, "operation_result", None)
        updated = getattr(operation, "updated", 0) or 0
        logger.info(
            f"Vectors reassigned {old_user_id} -> {new_user_id}: {updated}."
        )
        return updated

    async def _ensure_collection(self, dimension: int) -> None:
        """Create the collection once, sized by the first vector.

        Args:
            dimension: Embedding size detected at runtime.
        """
        if self._ensured:
            return
        if not await self._client.collection_exists(self._collection):
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name="user_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            logger.info(
                f"Qdrant collection '{self._collection}' created "
                f"(dim={dimension})."
            )
        self._ensured = True
