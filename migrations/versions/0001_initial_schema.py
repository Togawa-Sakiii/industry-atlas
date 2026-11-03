"""0001 · 初始 schema v2（12 表 + 3 触发器 + 2 视图）

★ 冻结快照：同目录 0001_initial_schema.sql 是 2026-11-03 从 sql/schema.sql 拷来的。
  之后不要再改它；改表 = 新增 0002、0003…，并同步更新 sql/schema.sql（人读快照）。
"""

import sqlite3
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).with_suffix(".sql")


def _has_sql(chunk: str) -> bool:
    return any(
        line.strip() and not line.lstrip().startswith("--")
        for line in chunk.splitlines()
    )


def _statements() -> list[str]:
    """按 SQLite 的语句边界切分（触发器 BEGIN…END 里的分号不算结束）。"""
    out: list[str] = []
    buf: list[str] = []
    for line in SQL_FILE.read_text(encoding="utf-8").splitlines(keepends=True):
        buf.append(line)
        chunk = "".join(buf)
        if sqlite3.complete_statement(chunk):
            if _has_sql(chunk):
                out.append(chunk.strip())
            buf = []
    tail = "".join(buf)
    if _has_sql(tail):
        out.append(tail.strip())
    return out


def upgrade() -> None:
    for stmt in _statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_due_thesis")
    op.execute("DROP VIEW IF EXISTS v_fact_trace")
    for table in (
        "thesis_scope",
        "thesis_fact",
        "thesis",
        "chain_edge",
        "revision",
        "fact",
        "document",
        "entity_alias",
        "entity",
        "metric",
        "period",
        "channel",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")