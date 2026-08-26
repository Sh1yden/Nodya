"""Integration tests for ``app.brain.memory.vector.qdrant``."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock, patch

from app.brain.memory.vector.qdrant import VectorMemory, VectorPoint


class TestVectorMemory:
    """Test VectorMemory with mocked AsyncQdrantClient."""

    @patch("app.brain.memory.vector.qdrant.AsyncQdrantClient")
    async def test_upsert_empty_points_noop(self, mock_cls: Mock) -> None:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client

        vm = VectorMemory("localhost", 6333, "test")
        await vm.upsert_points(uuid.uuid4(), [])
        mock_client.upsert.assert_not_awaited()

    @patch("app.brain.memory.vector.qdrant.AsyncQdrantClient")
    async def test_upsert_creates_points(self, mock_cls: Mock) -> None:
        mock_client = AsyncMock()
        mock_client.collection_exists = AsyncMock(return_value=True)
        mock_cls.return_value = mock_client

        vm = VectorMemory("localhost", 6333, "test")
        uid = uuid.uuid4()
        points = [
            VectorPoint(
                fact_id=1,
                vector=[0.1, 0.2, 0.3],
                text="test fact",
            )
        ]

        await vm.upsert_points(uid, points)
        mock_client.upsert.assert_awaited_once()

    @patch("app.brain.memory.vector.qdrant.AsyncQdrantClient")
    async def test_search_returns_texts(self, mock_cls: Mock) -> None:
        mock_client = AsyncMock()
        mock_client.collection_exists = AsyncMock(return_value=True)
        result = Mock()
        result.points = [Mock(payload={"text": "found fact"})]
        mock_client.query_points = AsyncMock(return_value=result)
        mock_cls.return_value = mock_client

        vm = VectorMemory("localhost", 6333, "test")
        vm._ensured = True

        texts = await vm.search(uuid.uuid4(), [0.1, 0.2], limit=5)
        assert texts == ["found fact"]

    @patch("app.brain.memory.vector.qdrant.AsyncQdrantClient")
    async def test_search_empty_collection(self, mock_cls: Mock) -> None:
        mock_client = AsyncMock()
        mock_client.collection_exists = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        vm = VectorMemory("localhost", 6333, "test")
        texts = await vm.search(uuid.uuid4(), [0.1], limit=5)
        assert texts == []

    @patch("app.brain.memory.vector.qdrant.AsyncQdrantClient")
    async def test_reassign_user(self, mock_cls: Mock) -> None:
        mock_client = AsyncMock()
        op_result = Mock()
        op_result.updated = 5
        result = Mock()
        result.operation_result = op_result
        mock_client.set_payload = AsyncMock(return_value=result)
        mock_cls.return_value = mock_client

        vm = VectorMemory("localhost", 6333, "test")
        count = await vm.reassign_user(uuid.uuid4(), uuid.uuid4())
        assert count == 5

    @patch("app.brain.memory.vector.qdrant.AsyncQdrantClient")
    async def test_ensure_collection_creates_once(
        self, mock_cls: Mock
    ) -> None:
        mock_client = AsyncMock()
        mock_client.collection_exists = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        vm = VectorMemory("localhost", 6333, "test")
        await vm._ensure_collection(3)
        mock_client.create_collection.assert_awaited_once()
        assert vm._ensured is True
