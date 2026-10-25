"""勾 6 起步：照着官方 tutorial 的 engine 一节做一次（2026-10-24）

文档：https://docs.sqlalchemy.org/en/20/tutorial/engine.html
"""
from pathlib import Path

from sqlalchemy import create_engine, text

DB = Path(__file__).resolve().parents[1] / "data" / "industry.db"

# ★ create_engine 是懒连接：这里不会真的打开数据库，写错了也不会当场报错
if not DB.exists():
    raise SystemExit(f"找不到数据库：{DB}（先跑 M1a 建库那条命令）")

engine = create_engine(f"sqlite:///{DB.as_posix()}", echo=False)   # echo=True 可以看它发的 SQL

with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )).fetchall()

print(len(rows), "张表")
print([r[0] for r in rows])