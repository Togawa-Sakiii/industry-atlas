"""验证数据库结构（勾 7 起步 · 2026-10-24）

★ 判据不是"测试全绿"，而是"结构被改坏时测试会变红"。
   验证方法：把 schema.sql 里 fact 的 UNIQUE 六列自然键那行删掉 → 第 4 个测试必须变红。

为什么用内存库（:memory:）而不是文件：
    ① 快（不读写磁盘）② 每次都是全新库、互不污染 ③ 跑完自动消失、不留垃圾
"""

import sqlite3
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"
#  ★ parents[1]：从 tests/test_schema.py 往上两级 → 仓库根 → 再找 sql/schema.sql
#    这样无论从哪个目录运行 pytest 都找得到，不依赖"当前工作目录"


def make_db():
    """在内存里建一个装好 schema 的库"""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")   # ★ 每个新连接都要开，否则外键是装饰品
    return conn


def seed(conn):
    """插入最小一套前置数据，让后面的测试有东西可引用"""
    conn.execute("INSERT INTO channel VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
                 ("ch1", "akshare", "api", None, "公开开源库，MIT", "2026-10-24", 3, 1, None))
    conn.execute("INSERT INTO period VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                 ("2024FY", "2024 年度", "FY", "2024-01-01", "2024-12-31", 2024, 0, None))
    conn.execute("INSERT INTO metric VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
                 ("m1", "gross_margin", "毛利率", "%", "(收入-成本)/收入",
                  "同一指标不同口径不可比", "raw", None, None))
    conn.execute("INSERT INTO entity VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                 ("e1", "示例企业 A", "company", None, None, "unlisted", None, None))
    conn.execute("INSERT INTO document VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                 ("d1", "ch1", "某公司 2024 年报", "annual_report", None, "2025-04-20",
                  "2026-10-24", "a" * 64, "raw/d1.pdf", 1, None))


FACT = ("INSERT INTO fact(fact_id,document_id,source_locator,metric_id,entity_id,period_id,"
        "scope_type,scope_label,basis,value_num,value_text,unit,value_scale,currency,"
        "extractor,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

GOOD_FACT = ("f1", "d1", "p.55 表 12", "m1", "e1", "2024FY", "whole", "",
             "reported_by_source", 0.0, None, "%", 1, "CNY", "manual", "2026-10-24")


# ── 1 ────────────────────────────────────────────────────────
def test_十二张表和三个触发器都在():
    conn = make_db()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"channel", "period", "metric", "entity", "entity_alias", "document", "fact",
                "revision", "chain_edge", "thesis", "thesis_fact", "thesis_scope"}
    assert expected <= names, f"缺表：{expected - names}"

    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"fact_no_update", "fact_no_delete", "thesis_lock"} <= triggers, f"缺触发器：{triggers}"


# ── 2 ────────────────────────────────────────────────────────
def test_外键真的生效():
    conn = make_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO entity_alias(alias_id,entity_id,alias) "
                     "VALUES('a1','不存在的实体','别名')")


# ── 3 ────────────────────────────────────────────────────────
def test_fact_不许改也不许删():
    conn = make_db()
    seed(conn)
    conn.execute(FACT, GOOD_FACT)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE fact SET value_num = 99 WHERE fact_id = 'f1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM fact WHERE fact_id = 'f1'")


# ── 4 ────────────────────────────────────────────────────────
def test_同一条数字不能插两次():
    """六列自然键（document + metric + period + scope_type + scope_label + basis）去重"""
    conn = make_db()
    seed(conn)
    conn.execute(FACT, GOOD_FACT)
    dup = ("f2",) + GOOD_FACT[1:]          # 只改 fact_id，其余六列一模一样
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(FACT, dup)


# ── 5 ────────────────────────────────────────────────────────
def test_合规说明为空串被拒():
    """★ 这条来自本人 2026-10-22 自己发现的洞：NOT NULL 拦不住空串"""
    conn = make_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO channel(channel_id,name,kind,license_note) "
                     "VALUES('c9','x','api','')")


# ── 6 ────────────────────────────────────────────────────────
def test_论点卡的_claim_不能改_但结算能改():
    conn = make_db()
    conn.execute("INSERT INTO thesis(thesis_id,claim,counter_evidence,falsify_condition,due_at) "
                 "VALUES('t1','某环节会涨价','有下游压价证据','连续两季出厂价同比为负','2027-01-09')")
    # 写入即锁：claim 改不动
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE thesis SET claim = '改一下试试' WHERE thesis_id = 't1'")
    # 但结算相关列必须放行
    conn.execute("UPDATE thesis SET outcome='成立', outcome_verdict='right', status='settled' "
                 "WHERE thesis_id = 't1'")
    row = conn.execute("SELECT status, outcome_verdict FROM thesis WHERE thesis_id='t1'").fetchone()
    assert row == ("settled", "right")