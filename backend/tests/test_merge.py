"""
ProfileService.merge_fact 核心逻辑单元测试

测试四种 Fact Merge 场景：
- Case 1: 全新字段（高/低置信度）
- Case 2: 值相同 → confidence boost
- Case 3: 高置信度 + direct source → supersede
- Case 4: 冲突但不确定 → pending queue
- 边界: inferred source 不能直接 supersede
"""
import asyncio
import os
import tempfile

import aiosqlite
import pytest


# ─── 测试夹具 ────────────────────────────────────────────────────────────────


class AsyncConnFactory:
    """
    异步连接工厂，实现 __aenter__/__aexit__ 异步上下文管理器协议。

    ProfileService._db() 使用:
        async with self._db_factory as conn:
            yield conn

    每次进入上下文时：
    1. 创建新 aiosqlite.connect()（连接到同一数据库文件）
    2. await conn 启动连接
    3. 设置 row_factory
    4. 返回连接给调用者

    退出上下文时关闭连接。
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def __aenter__(self):
        conn = aiosqlite.connect(self._db_path)
        await conn  # 启动连接（aiosqlite 在此初始化底层线程和 sqlite3 连接）
        conn.row_factory = aiosqlite.Row
        self._conn = conn
        return conn

    async def __aexit__(self, *args):
        await self._conn.close()


@pytest.fixture
async def db_factory():
    """返回 AsyncConnFactory 实例，每个测试结束后清理临时文件"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # 初始化建表（直接执行 schema，不走 init_db，因为它用固定路径）
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                field_value TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source TEXT DEFAULT 'inferred' CHECK(source IN ('direct', 'inferred')),
                source_turn_id INTEGER,
                status TEXT DEFAULT 'confirmed' CHECK(status IN ('confirmed', 'pending', 'superseded')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS profile_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                category TEXT NOT NULL,
                value TEXT NOT NULL,
                weight REAL DEFAULT 0.5,
                mention_count INTEGER DEFAULT 1,
                last_mentioned_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, category, value)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_confirmations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
        """)
        await db.commit()

    factory = AsyncConnFactory(path)

    yield factory

    try:
        os.unlink(path)
    except OSError:
        pass


# ─── 测试用例 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_new_fact_high_confidence(db_factory):
    """
    Case 1 (高置信度): 全新字段 + confidence=0.9
    → 应该直接 status='confirmed'，无需确认
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)
    await service.merge_fact(
        user_id="user1",
        fact={"field": "name", "value": "张三", "confidence": 0.9, "source": "direct"},
        source_turn_id=1,
    )

    # 验证数据库状态
    async with db_factory as conn:
        rows = await conn.execute_fetchall(
            "SELECT status, field_value, confidence FROM profile_facts WHERE user_id='user1'"
        )

    assert len(rows) == 1
    assert rows[0]["status"] == "confirmed"
    assert rows[0]["field_value"] == "张三"
    assert rows[0]["confidence"] == 0.9


@pytest.mark.asyncio
async def test_merge_new_fact_low_confidence(db_factory):
    """
    Case 1 (低置信度): 全新字段 + confidence=0.4
    → status='pending'，且应在 pending_confirmations 中创建一条记录
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)
    await service.merge_fact(
        user_id="user1",
        fact={"field": "occupation", "value": "工程师", "confidence": 0.4, "source": "inferred"},
        source_turn_id=2,
    )

    async with db_factory as conn:
        fact_rows = await conn.execute_fetchall(
            "SELECT status, field_value FROM profile_facts WHERE user_id='user1'"
        )
        confirm_rows = await conn.execute_fetchall(
            "SELECT field_name, new_value, status FROM pending_confirmations WHERE user_id='user1'"
        )

    assert len(fact_rows) == 1
    assert fact_rows[0]["status"] == "pending"

    assert len(confirm_rows) == 1
    assert confirm_rows[0]["field_name"] == "occupation"
    assert confirm_rows[0]["new_value"] == "工程师"
    assert confirm_rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_merge_same_value_boost(db_factory):
    """
    Case 2: 已有 confirmed fact，新值相同（归一化后）
    → 旧记录的 confidence 应该 +0.1（上限 1.0）
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)

    await service.merge_fact(
        user_id="user1",
        fact={"field": "city", "value": "北京", "confidence": 0.7, "source": "direct"},
        source_turn_id=1,
    )

    await service.merge_fact(
        user_id="user1",
        fact={"field": "city", "value": "北京", "confidence": 0.6, "source": "inferred"},
        source_turn_id=2,
    )

    async with db_factory as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, status, confidence FROM profile_facts WHERE user_id='user1' AND field_name='city'"
        )

    # 应该是同一记录（id 相同），confidence 从 0.7 → 0.8
    assert len(rows) == 1
    assert rows[0]["status"] == "confirmed"
    assert rows[0]["confidence"] == pytest.approx(0.8, abs=1e-6)


@pytest.mark.asyncio
async def test_merge_supersede(db_factory):
    """
    Case 3: 已有 confirmed，新值不同 + confidence 高（> old + 0.2）+ source='direct'
    → 旧记录 status='superseded'，新记录 status='confirmed'
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)

    await service.merge_fact(
        user_id="user1",
        fact={"field": "name", "value": "李四", "confidence": 0.7, "source": "direct"},
        source_turn_id=1,
    )

    await service.merge_fact(
        user_id="user1",
        fact={"field": "name", "value": "王五", "confidence": 0.95, "source": "direct"},
        source_turn_id=2,
    )

    async with db_factory as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, field_value, status, confidence FROM profile_facts WHERE user_id='user1' ORDER BY id"
        )

    assert len(rows) == 2
    old_row, new_row = rows

    assert old_row["status"] == "superseded"
    assert old_row["field_value"] == "李四"

    assert new_row["status"] == "confirmed"
    assert new_row["field_value"] == "王五"
    assert new_row["confidence"] == 0.95


@pytest.mark.asyncio
async def test_merge_conflict_queued(db_factory):
    """
    Case 4: 已有 confirmed，新值不同且 confidence 不够高（不足以 supersede）
    → 新记录 status='pending'，创建 pending_confirmation
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)

    await service.merge_fact(
        user_id="user1",
        fact={"field": "education", "value": "本科", "confidence": 0.8, "source": "direct"},
        source_turn_id=1,
    )

    await service.merge_fact(
        user_id="user1",
        fact={"field": "education", "value": "硕士", "confidence": 0.9, "source": "inferred"},
        source_turn_id=2,
    )

    async with db_factory as conn:
        fact_rows = await conn.execute_fetchall(
            "SELECT id, field_value, status FROM profile_facts WHERE user_id='user1' ORDER BY id"
        )
        confirm_rows = await conn.execute_fetchall(
            "SELECT old_value, new_value, status FROM pending_confirmations WHERE user_id='user1'"
        )

    assert len(fact_rows) == 2
    assert fact_rows[0]["status"] == "confirmed"  # 旧值保持 confirmed
    assert fact_rows[1]["status"] == "pending"    # 新值 pending

    assert len(confirm_rows) == 1
    assert confirm_rows[0]["old_value"] == "本科"
    assert confirm_rows[0]["new_value"] == "硕士"


@pytest.mark.asyncio
async def test_merge_inferred_never_supersedes(db_factory):
    """
    Case 3 边界: source='inferred' 即使 confidence 很高也不应直接 supersede
    → 旧值保持 confirmed，新值进入 pending
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)

    # 先插入一条 confirmed fact（confidence=0.9 >= 0.7，source=direct）
    await service.merge_fact(
        user_id="user1",
        fact={"field": "age", "value": "30", "confidence": 0.9, "source": "direct"},
        source_turn_id=1,
    )

    # 新值 confidence=0.99，但 source=inferred
    # Case 3 要求 source=='direct'，所以不满足 → 走 Case 4
    await service.merge_fact(
        user_id="user1",
        fact={"field": "age", "value": "28", "confidence": 0.99, "source": "inferred"},
        source_turn_id=2,
    )

    async with db_factory as conn:
        rows = await conn.execute_fetchall(
            "SELECT id, field_value, status, source FROM profile_facts WHERE user_id='user1' ORDER BY id"
        )
        confirm_rows = await conn.execute_fetchall(
            "SELECT status FROM pending_confirmations WHERE user_id='user1'"
        )

    assert len(rows) == 2
    assert rows[0]["status"] == "confirmed"   # 旧值仍是 confirmed
    assert rows[1]["status"] == "pending"     # 新值进入 pending
    assert rows[1]["source"] == "inferred"

    assert len(confirm_rows) == 1             # 创建了待确认
