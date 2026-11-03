"""Alembic 环境：SQLite + 纯 SQL 迁移。

★ target_metadata = None：本项目没有 ORM / Table 定义（表结构以 sql/schema.sql 为准，
  运行期靠反射），所以不能用 --autogenerate；迁移用 op.execute() 写 SQL。
"""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

ROOT = Path(__file__).resolve().parents[1]
db_path = Path(os.environ.get("ATLAS_DB", ROOT / "data" / "industry.db")).resolve()
db_url = f"sqlite:///{db_path.as_posix()}"
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = None


def run_migrations_offline() -> None:
    """alembic upgrade head --sql：只生成 SQL，不连库。"""
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(db_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()