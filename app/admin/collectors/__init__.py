"""Collectors used by the admin panel."""

from app.admin.collectors.base import Result
from app.admin.collectors.broker import BrokerCollector, QueueInfo
from app.admin.collectors.docker import (
    ContainerInfo,
    ContainerLogLine,
    DockerCollector,
)
from app.admin.collectors.errors import ErrorRecord, ErrorsCollector
from app.admin.collectors.infra import (
    DependencyStatus,
    InfraCollector,
    PostgresStats,
    QdrantCollectionInfo,
    RedisStats,
)
from app.admin.collectors.logs import (
    LogEntry,
    LogFileInfo,
    LogsCollector,
    LogSlice,
)
from app.admin.collectors.runtime import RuntimeCollector, RuntimeStatus

__all__ = [
    "BrokerCollector",
    "ContainerInfo",
    "ContainerLogLine",
    "DependencyStatus",
    "DockerCollector",
    "ErrorRecord",
    "ErrorsCollector",
    "InfraCollector",
    "LogEntry",
    "LogFileInfo",
    "LogSlice",
    "LogsCollector",
    "PostgresStats",
    "QdrantCollectionInfo",
    "QueueInfo",
    "RedisStats",
    "Result",
    "RuntimeCollector",
    "RuntimeStatus",
]
