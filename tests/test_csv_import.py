"""M1c 的验收测试（2026-11-01 · W7）

★ 判据不是"测试全绿"，而是"通道坏了时测试会变红"：
    · 把 import_csv 里的 insert_ignore 换成 insert → 第 1 个测试必须变红（重跑会重复）
    · 把 validate_row 的 source_locator 检查删掉 → 第 2 个测试必须变红

为什么用临时文件库：导入通道本来就要读一个真实的 CSV 文件 + 写一个真实的库，
用 tmp_path 每测一次都是干净环境，互不污染。
"""

import csv
import sqlite3
from pathlib import Path

from industry_atlas.csv_import import import_csv

SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "schema.sql"

REQUIRED = [
    "entity_name", "entity_kind",
    "metric_code", "metric_name", "metric_unit", "metric_definition", "metric_comparability",
    "period_label", "period_kind", "period_end_date",
    "scope_type", "value_num", "unit", "source_locator",
]
OPTIONAL = ["scope_label", "value_scale", "currency"]
FIELDS = REQUIRED + OPTIONAL


def make_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return db


def good_row(**over):
    row = {
        "entity_name": "示例企业 A", "entity_kind": "company",
        "metric_code": "demo_revenue", "metric_name": "营业收入", "metric_unit": "万元",
        "metric_definition": "演示用：企业年度营业收入", "metric_comparability": "演示数据，不可比",
        "period_label": "2024 年度", "period_kind": "FY", "period_end_date": "2024-12-31",
        "scope_type": "whole", "value_num": "1200", "unit": "万元",
        "source_locator": "演示 CSV 第 2 行",
        "scope_label": "", "value_scale": "10000", "currency": "CNY",
    }
    row.update(over)
    return row


def write_csv(tmp_path, rows, name="t.csv"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    return path


def count(db, table):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ── 1 ────────────────────────────────────────────────────────
def test_同一份CSV导入两次_第二次零新增(tmp_path):
    db = make_db(tmp_path)
    path = write_csv(tmp_path, [good_row()])

    first = import_csv(path, db, "csv_test", "测试通道", "本人人工导出")
    second = import_csv(path, db, "csv_test", "测试通道", "本人人工导出")

    assert first["fact_added"] == 1
    assert second["fact_added"] == 0, "重跑不该新增 fact —— 六列自然键 + ON CONFLICT 没生效？"
    assert count(db, "fact") == 1
    # 外圈也不能重复建
    assert count(db, "entity") == 1
    assert count(db, "metric") == 1
    assert count(db, "period") == 1
    assert count(db, "document") == 1, "同一份文件（同 sha256）不该建第二个 document"


# ── 2 ────────────────────────────────────────────────────────
def test_坏行不入库_但报得出是第几行哪个字段(tmp_path):
    db = make_db(tmp_path)
    path = write_csv(tmp_path, [
        good_row(),
        good_row(metric_code="demo_bad_1", source_locator=""),          # 缺来源
        good_row(metric_code="demo_bad_2", entity_kind="赚不了钱的"),    # 枚举非法
        good_row(metric_code="demo_bad_3", value_num="一千二"),          # 不是数字
    ])

    res = import_csv(path, db, "csv_test", "测试通道", "本人人工导出")

    assert res["fact_added"] == 1, "只有那 1 行好的能入库"
    assert len(res["errors"]) == 3
    joined = " ".join(res["errors"])
    assert "第 3 行" in joined and "source_locator" in joined
    assert "第 4 行" in joined and "entity_kind" in joined
    assert "第 5 行" in joined and "value_num" in joined
    assert count(db, "fact") == 1


# ── 3 ────────────────────────────────────────────────────────
def test_表头缺列_整份文件直接拒(tmp_path):
    db = make_db(tmp_path)
    path = tmp_path / "broken.csv"
    path.write_text("entity_name,value_num\n示例企业 A,1200\n", encoding="utf-8")

    res = import_csv(path, db, "csv_test", "测试通道", "本人人工导出")

    assert res["fact_added"] == 0
    assert res["errors"] and "表头缺少" in res["errors"][0]
    assert count(db, "fact") == 0
