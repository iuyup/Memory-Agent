import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path

DATABASE_PATH = Path("./data/memory.db")


async def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        # 1. users
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # 2. profile_facts
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
            CREATE INDEX IF NOT EXISTS idx_profile_facts_user_field_status
            ON profile_facts(user_id, field_name, status)
        """)

        # 3. profile_preferences
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
            CREATE INDEX IF NOT EXISTS idx_profile_prefs_user_weight
            ON profile_preferences(user_id, weight DESC)
        """)

        # 4. pending_confirmations
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_confirmations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
        """)

        # 5. sessions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            )
        """)

        # 6. conversation_turns
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                assistant_message TEXT NOT NULL,
                turn_summary TEXT,
                tags TEXT,
                has_open_question INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_turns_user_created
            ON conversation_turns(user_id, created_at DESC)
        """)

        # 7. conversation_summaries
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                covers_turn_start INTEGER NOT NULL,
                covers_turn_end INTEGER NOT NULL,
                summary_text TEXT NOT NULL,
                level INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)

        # 8. proactive_log
        await db.execute("""
            CREATE TABLE IF NOT EXISTS proactive_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                hook_type TEXT NOT NULL,
                topic TEXT NOT NULL,
                triggered_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_proactive_log_user_hook_time
            ON proactive_log(user_id, hook_type, triggered_at DESC)
        """)

        # 9. procedural_rules
        await db.execute("""
            CREATE TABLE IF NOT EXISTS procedural_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                rule_text TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                source_turn_ids TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        await db.commit()


@asynccontextmanager
async def get_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        yield db