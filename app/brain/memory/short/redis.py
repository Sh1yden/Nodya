from uuid import UUID

from redis.asyncio import Redis


class RedisClient(Redis):
    def __init__(self):
        super().__init__()

    def get_state(user_id: UUID) -> str | None:
        pass

    def set_state(user_id: UUID, status: str, ttl: int | None = None) -> None:
        pass
