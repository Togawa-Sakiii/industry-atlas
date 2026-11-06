"""API 的数据库连接（只读）。

★ 库路径与 Alembic 用同一个约定：ATLAS_DB 可覆盖，默认 data/industry.db。
★ 反射：表结构以 sql/schema.sql 为唯一真相源，这里不重复写表定义。
"""

import os
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine

DB_PATH = Path(os.environ.get("ATLAS_DB", "data/industry.db")).resolve()
engine = create_engine(f"sqlite:///{DB_PATH.as_posix()}")

_meta = MetaData()
TABLES = {
    name: Table(name, _meta, autoload_with=engine)
    for name in ("entity", "metric", "period", "document", "fact", "channel")
}

ROOT = Path(__file__).resolve().parents[3]          # src/industry_atlas/api/db.py → 仓库根
DB_PATH = Path(os.environ.get("ATLAS_DB", ROOT / "data" / "industry.db")).resolve()