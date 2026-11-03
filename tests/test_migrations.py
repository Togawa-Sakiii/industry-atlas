"""迁移冒烟：空库 upgrade head 必须建出 12 张表（CI 防回归）。"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
TWELVE = {
    "channel", "period", "metric", "entity", "entity_alias", "document",
    "fact", "revision", "chain_edge", "thesis", "thesis_fact", "thesis_scope",
}


def test_空库_upgrade_head_建出十二张表(tmp_path, monkeypatch):
    db = tmp_path / "migrate.db"
    monkeypatch.setenv("ATLAS_DB", str(db))

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TWELVE <= names, f"缺表：{TWELVE - names}"

    head = ScriptDirectory.from_config(cfg).get_current_head()
    got = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert got == head