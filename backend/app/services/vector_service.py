import logging
import struct
from typing import AsyncIterator

import openai
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


def _serialize_f32(vector: list[float]) -> bytes:
    """Serialize a list of floats to bytes for sqlite-vec."""
    return struct.pack(f"{len(vector)}f", *vector)


def _deserialize_f32(data: bytes) -> list[float]:
    """Deserialize bytes back to float list."""
    count = len(data) // 4
    return list(struct.unpack(f"{count}f", data))


class VectorService:
    def __init__(self, db_factory=None):
        self._db_factory = db_factory
        self.embedding_dim = 1536
        self._vec_available = False

    async def _get_db(self):
        """Get a database connection, applying to the factory if provided."""
        if self._db_factory is not None:
            async with self._db_factory() as db:
                yield db
        else:
            from app.database import get_db
            async with get_db() as db:
                yield db

    async def init_vec_table(self) -> None:
        """
        初始化向量表。在 app 启动时调用。

        sqlite-vec 虚拟表：
        CREATE VIRTUAL TABLE IF NOT EXISTS turn_embeddings USING vec0(
            turn_id INTEGER PRIMARY KEY,
            embedding FLOAT[1536]
        )
        """
        try:
            import sqlite_vec

            async with self._get_db() as db:
                await db.enable_load_extension(True)
                sqlite_vec.load(db)
                await db.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS turn_embeddings USING vec0(
                        turn_id INTEGER PRIMARY KEY,
                        embedding FLOAT[1536]
                    )
                    """
                )
                await db.commit()
            self._vec_available = True
            logger.info("Vector table initialized successfully")
        except Exception as e:
            logger.warning(f"sqlite-vec init failed, vector search disabled: {e}")
            self._vec_available = False

    async def index_turn(self, turn_id: int, text: str) -> None:
        """
        为一轮对话生成 embedding 并存入向量表。

        步骤：
        1. 调用 OpenAI embedding API: text-embedding-3-small
        2. 将 embedding 插入 turn_embeddings 表
        """
        if not self._vec_available:
            return

        try:
            embedding = await self._get_embedding(text)
            async with self._get_db() as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO turn_embeddings (turn_id, embedding)
                    VALUES (?, ?)
                    """,
                    (turn_id, _serialize_f32(embedding)),
                )
                await db.commit()
        except Exception as e:
            logger.error(f"vector indexing failed for turn {turn_id}: {e}")

    async def search_similar(
        self, query_text: str, user_id: str, top_k: int = 3
    ) -> list[dict]:
        """
        语义检索：找到与 query_text 最相关的历史对话。

        返回 [{"turn_id": int, "user_message": str, "assistant_message": str,
              "summary": str, "distance": float, "created_at": str}]
        """
        if not self._vec_available:
            return []

        try:
            query_embedding = await self._get_embedding(query_text)
            serialized = _serialize_f32(query_embedding)

            async with self._get_db() as db:
                # sqlite-vec 1.x 语法: MATCH + vector
                rows = await db.execute_fetchall(
                    """
                    SELECT te.turn_id, te.distance,
                           ct.user_message, ct.assistant_message,
                           ct.turn_summary, ct.created_at
                    FROM turn_embeddings te
                    JOIN conversation_turns ct ON te.turn_id = ct.turn_id
                    WHERE te.embedding MATCH ? AND ct.user_id = ?
                    ORDER BY te.distance
                    LIMIT ?
                    """,
                    (serialized, user_id, top_k),
                )

            return [
                {
                    "turn_id": row["turn_id"],
                    "user_message": row["user_message"],
                    "assistant_message": row["assistant_message"],
                    "summary": row["turn_summary"] or "",
                    "distance": row["distance"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        except Exception as e:
            logger.error(f"vector search failed: {e}")
            return []

    async def _get_embedding(self, text: str) -> list[float]:
        """调用 OpenAI embedding API 获取向量。"""
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding
