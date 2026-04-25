"""
清理 profile_facts 表中的重复 confirmed 记录。

逻辑：
1. 对每个 user_id，按 normalized field_name 分组
2. 每组只保留最新（updated_at 最大）的一条 confirmed 记录
3. 将同组的其他 confirmed 标记为 superseded
4. 对已被标记 superseded 的记录，判断是否真的和保留的记录重复，
   如果值也相同则直接删除，如果值不同则保留 superseded 状态（作为历史）
"""

import sqlite3
import re
from datetime import datetime

DB_PATH = "data/memory.db"

# 与 profile_service.py 保持一致的 FIELD_ALIASES
FIELD_ALIASES = {
    "姓名": "name", "名字": "name", "名称": "name",
    "职业": "occupation", "工作": "occupation", "职位": "occupation", "身份": "occupation",
    "城市": "city", "居住地": "city", "所在城市": "city", "地点": "city",
    "兴趣": "interests", "爱好": "interests", "兴趣爱好": "interests",
    "年龄": "age", "岁数": "age",
    "教育": "education", "学历": "education", "学校": "education", "教育背景": "education",
    "公司": "company", "当前项目": "current_project",
    "游戏": "game", "昵称": "nickname", "称呼": "nickname",
    "游戏角色": "game_character", "游戏ID": "game_id",
    "游戏兴趣": "game",
    "偏好角色": "game_character",
    "游戏本命角色": "game_character",
    "项目": "current_project",
}


def normalize(field_name: str) -> str:
    return FIELD_ALIASES.get(field_name, field_name)


def normalize_value(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.strip().lower())


def cleanup_user(conn: sqlite3.Connection, user_id: str) -> dict:
    cur = conn.cursor()
    stats = {"kept": 0, "superseded": 0, "deleted": 0}

    # 获取该用户所有 confirmed facts，按 updated_at 倒序
    rows = cur.execute(
        """
        SELECT id, field_name, field_value, confidence, source, updated_at
        FROM profile_facts
        WHERE user_id = ? AND status = 'confirmed'
        ORDER BY updated_at DESC
        """,
        (user_id,),
    ).fetchall()

    # 按 normalized field_name 分组，每组只保留第一条（最新的）
    to_keep = {}  # normalized_field -> row
    to_supersede = []  # [row, ...]

    for row in rows:
        fid, fname, fvalue, conf, source, updated = row
        norm = normalize(fname)
        if norm not in to_keep:
            to_keep[norm] = row
        else:
            to_supersede.append(row)

    # 将需要 superseded 的标记
    for row in to_supersede:
        fid = row[0]
        now = datetime.utcnow().isoformat()
        cur.execute(
            "UPDATE profile_facts SET status = 'superseded', updated_at = ? WHERE id = ?",
            (now, fid),
        )
        stats["superseded"] += 1

    stats["kept"] = len(to_keep)

    # 对 superseded 的记录：如果值和保留的那条 normalized 后相同，直接删除
    # 先收集保留的值
    kept_rows = list(to_keep.values())
    for kept in kept_rows:
        kept_norm_field = normalize(kept[1])
        kept_norm_val = normalize_value(kept[2])

        # 找出所有 superseded 的同名字段（normalized），值也相同的 → 删除
        superseded_to_delete = cur.execute(
            """
            SELECT id FROM profile_facts
            WHERE user_id = ? AND status = 'superseded'
            """,
            (user_id,),
        ).fetchall()

        for sid_row in superseded_to_delete:
            sid = sid_row[0]
            row = cur.execute(
                "SELECT field_name, field_value FROM profile_facts WHERE id = ?",
                (sid,),
            ).fetchone()
            if row:
                s_fname, s_fvalue = row
                if normalize(s_fname) == kept_norm_field and normalize_value(s_fvalue) == kept_norm_val:
                    cur.execute("DELETE FROM profile_facts WHERE id = ?", (sid,))
                    stats["deleted"] += 1

    conn.commit()
    return stats


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    users = cur.execute("SELECT user_id, username FROM users").fetchall()
    for uid, uname in users:
        print(f"\n=== User: {uname or '(empty)'} ({uid}) ===")
        stats = cleanup_user(conn, uid)
        print(f"  kept: {stats['kept']}, superseded: {stats['superseded']}, deleted duplicates: {stats['deleted']}")

        # 展示清理后的 confirmed facts
        remaining = cur.execute(
            """
            SELECT field_name, field_value, confidence, source
            FROM profile_facts
            WHERE user_id = ? AND status = 'confirmed'
            ORDER BY field_name
            """,
            (uid,),
        ).fetchall()
        for r in remaining:
            print(f"  - {r[0]}: {r[1]}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
