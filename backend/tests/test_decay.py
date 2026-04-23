"""
ProfileService 偏好衰减 + get_profile_snapshot 过滤逻辑单元测试

测试：
- 新偏好初始 weight=0.5
- 同一偏好再次提到 → weight 增加（boost）
- 长时间不提 → weight 指数衰减（λ=0.05，约14天半衰期）
- 衰减后再提到 → weight = decayed + 0.3
- get_profile_snapshot 过滤：confidence < 0.5 / weight < 0.3 的记录不返回
"""
import math
import os
import tempfile
from datetime import datetime, timedelta

import aiosqlite
import pytest


# ─── 测试夹具 ────────────────────────────────────────────────────────────────


class AsyncConnFactory:
    """
    异步连接工厂，实现 __aenter__/__aexit__ 异步上下文管理器协议。

    每次进入上下文时创建新连接、启动、设置 row_factory。
    退出时关闭连接。
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def __aenter__(self):
        conn = aiosqlite.connect(self._db_path)
        await conn
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


# ─── 辅助函数 ────────────────────────────────────────────────────────────────


async def manually_insert_preference(conn, user_id, category, value, weight,
                                    mention_count, days_ago=0):
    """
    直接在数据库中插入一条偏好记录（用于构造历史数据）。
    days_ago: 距离今天多少天
    """
    last_mentioned = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    created = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    await conn.execute(
        """
        INSERT INTO profile_preferences
        (user_id, category, value, weight, mention_count, last_mentioned_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, category, value, weight, mention_count, last_mentioned, created),
    )
    await conn.commit()


async def manually_insert_fact(conn, user_id, field_name, field_value, confidence,
                               source, status, days_ago=0):
    """直接在数据库中插入一条 fact 记录（用于构造历史数据）"""
    created = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    updated = created
    await conn.execute(
        """
        INSERT INTO profile_facts
        (user_id, field_name, field_value, confidence, source, source_turn_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (user_id, field_name, field_value, confidence, source, status, created, updated),
    )
    await conn.commit()


# ─── 测试用例 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preference_initial_weight(db_factory):
    """
    新偏好初始 weight=0.5，mention_count=1
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)
    await service.update_preference(
        user_id="user1",
        pref={"category": "food", "value": "火锅"},
    )

    async with db_factory as conn:
        rows = await conn.execute_fetchall(
            "SELECT weight, mention_count FROM profile_preferences WHERE user_id='user1'"
        )

    assert len(rows) == 1
    assert rows[0]["weight"] == 0.5
    assert rows[0]["mention_count"] == 1


@pytest.mark.asyncio
async def test_preference_boost(db_factory):
    """
    同一偏好再次提到 → mention_count +1，weight = min(decayed + 0.3, 1.0)
    新权重应为 0.5 + 0.3 = 0.8（刚提及过，decay 可忽略）
    """
    from app.services.profile_service import ProfileService

    service = ProfileService(db_factory)

    await service.update_preference(user_id="user1", pref={"category": "movie", "value": "科幻片"})

    # 模拟刚过了几秒（几乎没有 decay）
    async with db_factory as conn:
        now = datetime.utcnow().isoformat()
        await conn.execute(
            "UPDATE profile_preferences SET last_mentioned_at = ? WHERE user_id='user1'",
            (now,),
        )
        await conn.commit()

    await service.update_preference(user_id="user1", pref={"category": "movie", "value": "科幻片"})

    async with db_factory as conn:
        rows = await conn.execute_fetchall(
            "SELECT weight, mention_count FROM profile_preferences WHERE user_id='user1'"
        )

    assert len(rows) == 1
    assert rows[0]["mention_count"] == 2
    # decay 几乎为0: 0.5 * exp(-0.05 * ~0) ≈ 0.5, + 0.3 = 0.8
    assert rows[0]["weight"] == pytest.approx(0.8, abs=1e-6)


@pytest.mark.asyncio
async def test_preference_decay_over_time(db_factory):
    """
    模拟 30 天前的偏好（λ=0.05）
    期望衰减: 0.5 * exp(-0.05 * 30) ≈ 0.5 * 0.223 = 0.112
    远低于 0.3 阈值，snapshot 中不应返回
    """
    from app.services.profile_service import ProfileService

    async with db_factory as conn:
        await manually_insert_preference(
            conn, user_id="user1", category="travel", value="日本",
            weight=0.5, mention_count=1, days_ago=30,
        )

    service = ProfileService(db_factory)
    snapshot = await service.get_profile_snapshot("user1")

    # 30天衰减后 weight ≈ 0.5 * exp(-1.5) ≈ 0.112 < 0.3，应被过滤
    travel_prefs = [p for p in snapshot["preferences"] if p["category"] == "travel"]
    assert len(travel_prefs) == 0, "30天前的偏好应被 snapshot 过滤掉"


@pytest.mark.asyncio
async def test_preference_boost_after_decay(db_factory):
    """
    30天前的偏好再次被提到
    weight = (0.5 * exp(-1.5)) + 0.3 ≈ 0.112 + 0.3 = 0.412
    """
    from app.services.profile_service import ProfileService

    async with db_factory as conn:
        await manually_insert_preference(
            conn, user_id="user1", category="sport", value="跑步",
            weight=0.5, mention_count=1, days_ago=30,
        )

    service = ProfileService(db_factory)
    await service.update_preference(user_id="user1", pref={"category": "sport", "value": "跑步"})

    async with db_factory as conn:
        rows = await conn.execute_fetchall(
            "SELECT weight, mention_count FROM profile_preferences WHERE user_id='user1'"
        )

    assert len(rows) == 1
    expected_decay = 0.5 * math.exp(-0.05 * 30)
    expected_weight = min(expected_decay + 0.3, 1.0)
    assert abs(rows[0]["weight"] - expected_weight) < 0.01
    assert rows[0]["mention_count"] == 2


@pytest.mark.asyncio
async def test_profile_snapshot_filters(db_factory):
    """
    get_profile_snapshot 的过滤规则：
    - facts: status='confirmed' 且 confidence >= 0.5
    - preferences: 衰减后 weight >= 0.3（按 weight 降序）

    构造数据：
    - fact_A: confirmed, confidence=0.6 → 应返回
    - fact_B: confirmed, confidence=0.3 → 应过滤（< 0.5）
    - fact_C: pending, confidence=0.9 → 应过滤（pending 不返回）
    - pref_X: weight=0.6 → 应返回
    - pref_Y: weight=0.1 → 应过滤（< 0.3）
    """
    from app.services.profile_service import ProfileService

    async with db_factory as conn:
        # fact_A: confirmed, confidence=0.6 → 通过
        await manually_insert_fact(conn, "user1", "name", "Alice", 0.6, "direct", "confirmed")
        # fact_B: confirmed, confidence=0.3 → 过滤
        await manually_insert_fact(conn, "user1", "age", "25", 0.3, "inferred", "confirmed")
        # fact_C: pending, confidence=0.9 → 过滤
        await manually_insert_fact(conn, "user1", "city", "深圳", 0.9, "inferred", "pending")

        # pref_X: weight=0.6 → 通过
        await manually_insert_preference(conn, "user1", "food", "粤菜", 0.6, 1, days_ago=0)
        # pref_Y: weight=0.1 → 过滤
        await manually_insert_preference(conn, "user1", "drink", "可乐", 0.1, 1, days_ago=60)

    service = ProfileService(db_factory)
    snapshot = await service.get_profile_snapshot("user1")

    # 检查 facts
    fact_fields = {f["field"] for f in snapshot["facts"]}
    assert "name" in fact_fields, "confidence=0.6 的 confirmed fact 应返回"
    assert "age" not in fact_fields, "confidence=0.3 的 fact 应被过滤"
    assert "city" not in fact_fields, "pending fact 不应返回"

    # 检查 preferences
    pref_categories = {p["category"] for p in snapshot["preferences"]}
    assert "food" in pref_categories, "weight=0.6 的 preference 应返回"
    assert "drink" not in pref_categories, "weight=0.1 的 preference 应被过滤"

    # 检查排序
    weights = [p["weight"] for p in snapshot["preferences"]]
    assert weights == sorted(weights, reverse=True), "preferences 应按 weight 降序排列"
