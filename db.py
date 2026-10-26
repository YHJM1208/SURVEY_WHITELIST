"""数据访问层：每群一个独立 SQLite 文件，物理隔离替代 group_id 列隔离。

- 库文件路径：``<db_dir>/<group_id>.db``
- 表结构极简：``group_members(qq_number PK)`` + ``survey_responses(qq_number UNIQUE)``
- 只接收 / 返回纯 Python 值（``str``、``int``、``list[str]``）
- 不 import 任何 NoneBot 事件类型，可直接拷贝给 Web 后端复用
- 异常向上抛，不吞、不转用户话术
"""

import logging
import sqlite3
from pathlib import Path

from .config import config

try:  # 允许在没有 NoneBot 的环境（如 Web 后端）中直接复用本模块
    from nonebot.log import logger
except ImportError:  # pragma: no cover - 仅在脱离 NoneBot 复用时走到
    logger = logging.getLogger(__name__)

__all__ = ["ensure_db", "init_db_dir", "upsert_group_members", "query_unfilled"]


def _db_file(group_id: str) -> Path:
    """把群号映射为数据库文件路径。

    群号必须全部是数字才允许拼进文件名，否则直接拒绝，
    防止 ``group_id="../../etc/passwd"`` 这类路径穿越。
    """
    gid = str(group_id).strip()
    if not gid.isdigit():
        raise ValueError(f"invalid group_id: {group_id!r}")
    return Path(config.db_dir) / f"{gid}.db"


def _connect(group_id: str) -> sqlite3.Connection:
    """打开某群的库文件并设置 PRAGMA。

    PRAGMA 顺序有讲究：busy_timeout 必须先于 WAL。切换 WAL 需要短暂的
    排他锁，若此时库被别的连接锁住，没设 busy_timeout 会立刻抛
    "database is locked"。
    """
    db_file = _db_file(group_id)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file, timeout=config.busy_timeout_ms / 1000)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(config.busy_timeout_ms)}")

        if config.wal_enabled:
            conn.execute("PRAGMA journal_mode=WAL")

        if config.foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        # PRAGMA 失败时也要关掉连接，避免句柄泄漏
        conn.close()
        raise

    return conn


def ensure_db(group_id: str) -> None:
    """幂等建表。每个群的文件各自独立，首次访问时自动创建。"""
    conn = _connect(group_id)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                qq_number TEXT PRIMARY KEY,
                synced_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS survey_responses (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                qq_number    TEXT NOT NULL UNIQUE,
                survey_id    TEXT,
                answers      TEXT NOT NULL,
                submitted_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def init_db_dir() -> None:
    """启动时确保数据库目录存在（各群的 .db 文件在首次使用时创建）。"""
    Path(config.db_dir).mkdir(parents=True, exist_ok=True)
    logger.info(f"[survey_whitelist] 数据库目录就绪：{config.db_dir}")


def upsert_group_members(group_id: str, members: list[dict]) -> int:
    """把群成员写入“本群”的白名单，返回本次真正新增的人数。

    用单条 ``INSERT ... ON CONFLICT DO NOTHING`` 而不是“先查再插”：
    并发安全，且 ``rowcount`` 能准确反映是否真的插入。
    """
    ensure_db(group_id)
    conn = _connect(group_id)
    inserted = 0
    try:
        for member in members:
            qq = str(member["user_id"])
            cur = conn.execute(
                "INSERT INTO group_members (qq_number) VALUES (?) "
                "ON CONFLICT (qq_number) DO NOTHING",
                (qq,),
            )
            inserted += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return inserted


def query_unfilled(group_id: str) -> list[str]:
    """返回本群白名单中尚未提交答卷的 QQ 号（按 QQ 号排序）。"""
    ensure_db(group_id)
    conn = _connect(group_id)
    try:
        cur = conn.execute("""
            SELECT m.qq_number
            FROM group_members m
            LEFT JOIN survey_responses r ON m.qq_number = r.qq_number
            WHERE r.qq_number IS NULL
            ORDER BY m.qq_number
        """)
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
