"""M1c · CSV / 手工导入通道

为什么必须有这条通道：
    学校已购库（CSMAR / Wind / 同花顺）只允许【人工查询或导出 CSV】，
    不写程序批量拉它们的服务。所以「人工导出 CSV → 本通道入库」
    是那批数据唯一合规的入口 —— 这条通道不通，11 个库就一个也进不来。

三条规矩（= M1c 的验收）：
    ① 校验不过的行【不入库】，但要报清楚：第几行 / 哪个字段 / 为什么
    ② 同一份 CSV 重跑 → 不重复入库（document 按 sha256 唯一 + fact 六列自然键）
    ③ 每个数字都必须写 source_locator（哪页 / 哪张表 / 第几行）—— 写不出来就别录

★ 一条踩出来的规矩（2026-11-01 首次导入就中招）：
    期间 / 指标 / 实体这三张维度表有【自然键】唯一（period.label 全局唯一、metric.code 唯一），
    如果先按自己的哈希 id 插，撞上别的源已经建好的同 label 行，ON CONFLICT 会**静默跳过**，
    接着 fact 就撞外键 —— 报错在 fact，病根却在 period。
    → 所以：**先按自然键查已有 id，查不到才用哈希兜底**；插完再验一次"维度行真的在库里吗"。

用法（在仓库根目录）：
    .\\.venv\\Scripts\\python.exe -m industry_atlas.csv_import samples/demo_import.csv `
        --channel csv_demo --name "人工导出 CSV" --license "本人人工导出，仅本地研究"
"""

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from sqlalchemy import select

from industry_atlas.load import (
    TABLES,
    fact_id_for,
    insert_ignore,
    make_engine,
    reflect_tables,
    sha256_of,
    to_utc_text,
)

# ── 字段与字典（字典值必须和 sql/schema.sql 的 CHECK 一致）────────
REQUIRED = [
    "entity_name", "entity_kind",
    "metric_code", "metric_name", "metric_unit", "metric_definition", "metric_comparability",
    "period_label", "period_kind", "period_end_date",
    "scope_type", "value_num", "unit", "source_locator",
]

ENTITY_KINDS = {"company", "segment", "industry", "product", "region"}
PERIOD_KINDS = {"FY", "H1", "Q1", "Q2", "Q3", "Q4", "POINT", "RANGE"}
SCOPE_TYPES = {"whole", "segment", "product", "region", "industry", "consolidated", "parent_company"}
CURRENCIES = {"CNY", "USD", "HKD"}
SCALES = {"1", "1000", "10000", "100000000"}
VERIFY_STATUSES = {"unverified", "cross_checked", "source_verified", "deprecated"}


# ────────────────────────────────────────────────────────────
#  一、读 + 校验
# ────────────────────────────────────────────────────────────

def read_csv(path):
    """读 CSV（utf-8-sig：能吃掉 Excel 导出时带的 BOM）→ (表头, 行列表)。"""
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames or [], list(reader)


def check_header(fields):
    """表头缺列 = 整份文件直接拒（不是某一行的问题，修文件再说）。"""
    missing = [c for c in REQUIRED if c not in fields]
    if missing:
        return f"表头缺少这些列：{', '.join(missing)}"
    return None


def validate_row(row, line_no):
    """校验一行；返回错误列表（空 = 能入库）。

    ★ 这里只做"能明确判定为错"的检查 —— 校验器不该替人猜业务含义。
    """
    problems = []
    for col in REQUIRED:
        if not str(row.get(col) or "").strip():
            problems.append(f"第 {line_no} 行 · {col} 为空")
    if problems:
        return problems            # 缺字段就不再往下查，免得报一串连带错误

    if row["entity_kind"].strip() not in ENTITY_KINDS:
        problems.append(f"第 {line_no} 行 · entity_kind 只能是 {sorted(ENTITY_KINDS)}")
    if row["period_kind"].strip() not in PERIOD_KINDS:
        problems.append(f"第 {line_no} 行 · period_kind 只能是 {sorted(PERIOD_KINDS)}")
    if row["scope_type"].strip() not in SCOPE_TYPES:
        problems.append(f"第 {line_no} 行 · scope_type 只能是 {sorted(SCOPE_TYPES)}")

    try:
        float(row["value_num"])
    except (TypeError, ValueError):
        problems.append(f"第 {line_no} 行 · value_num 不是数字：{row['value_num']!r}")

    scale = str(row.get("value_scale") or "1").strip()
    if scale not in SCALES:
        problems.append(f"第 {line_no} 行 · value_scale 只能是 {sorted(SCALES)}（倍数，不是文字）")

    currency = str(row.get("currency") or "").strip().upper()
    if currency and currency not in CURRENCIES:
        problems.append(f"第 {line_no} 行 · currency 只能是 {sorted(CURRENCIES)} 或留空")

    status = str(row.get("verify_status") or "unverified").strip()
    if status not in VERIFY_STATUSES:
        problems.append(f"第 {line_no} 行 · verify_status 只能是 {sorted(VERIFY_STATUSES)}")

    return problems


# ────────────────────────────────────────────────────────────
#  二、维度行的 id 解析（★ 自然键优先，哈希兜底）
# ────────────────────────────────────────────────────────────

def short_id(prefix, text):
    """从文字算一个稳定短 id（同一家公司 / 同一个期间永远同一个 id）。"""
    return prefix + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def pk_of(table):
    return next(iter(table.primary_key.columns))


def find_existing_id(conn, table, *conditions):
    """按自然键查已有主键；没有就返回 None。"""
    stmt = select(pk_of(table)).where(*conditions).limit(1)
    row = conn.execute(stmt).first()
    return row[0] if row else None


def row_exists(conn, table, row):
    """这一行（按主键）真的在库里吗？—— 用来抓"ON CONFLICT 静默跳过"这类事故。"""
    pk = pk_of(table)
    stmt = select(pk).where(pk == row[str(pk.name)]).limit(1)
    return conn.execute(stmt).first() is not None


def resolve_ids(conn, tables, row):
    """给一行 CSV 定 3 个维度 id：库里已有同名/同码就复用，没有才算新的。"""
    name = row["entity_name"].strip()
    kind = row["entity_kind"].strip()
    code = row["metric_code"].strip()
    label = row["period_label"].strip()

    entity_id = find_existing_id(
        conn, tables["entity"],
        tables["entity"].c.name == name, tables["entity"].c.kind == kind,
    ) or short_id("e_", name)

    metric_id = find_existing_id(
        conn, tables["metric"], tables["metric"].c.code == code,
    ) or short_id("m_", code)

    period_id = find_existing_id(
        conn, tables["period"], tables["period"].c.label == label,
    ) or short_id("p_", label)

    return {"entity_id": entity_id, "metric_id": metric_id, "period_id": period_id}


# ────────────────────────────────────────────────────────────
#  三、行 → 表的行
# ────────────────────────────────────────────────────────────

def build_row(row, channel_id, document_id, fetched_at, ids=None):
    """一条 CSV 行 → 4 张表各自的那一行（channel / document 是整份文件一份）。"""
    ids = ids or {}
    entity_id = ids.get("entity_id") or short_id("e_", row["entity_name"].strip())
    metric_id = ids.get("metric_id") or short_id("m_", row["metric_code"].strip())
    period_id = ids.get("period_id") or short_id("p_", row["period_label"].strip())

    scope_label = str(row.get("scope_label") or "").strip()
    basis = str(row.get("basis") or "reported_by_source").strip()
    currency = str(row.get("currency") or "").strip().upper() or None
    scale = int(str(row.get("value_scale") or "1").strip())

    entity = {
        "entity_id": entity_id,
        "name": row["entity_name"].strip(),
        "kind": row["entity_kind"].strip(),
        "listed_status": "unknown",
        "region": str(row.get("entity_region") or "").strip() or None,
        "note": "CSV 导入",
    }

    metric = {
        "metric_id": metric_id,
        "code": row["metric_code"].strip(),
        "name_cn": row["metric_name"].strip(),
        "unit": row["metric_unit"].strip(),
        "definition": row["metric_definition"].strip(),
        "comparability_note": row["metric_comparability"].strip(),
        "kind": "raw",
        "source_hint": f"CSV 导入（{channel_id}）",
    }

    period = {
        "period_id": period_id,
        "label": row["period_label"].strip(),
        "kind": row["period_kind"].strip(),
        "end_date": row["period_end_date"].strip(),
        "start_date": str(row.get("period_start_date") or "").strip() or None,
        "is_cumulative": 1 if row["period_kind"].strip() in {"FY", "H1", "Q1", "Q2", "Q3", "Q4"} else 0,
        "note": "CSV 导入",
    }

    fact = {
        "fact_id": fact_id_for(document_id, metric_id, period_id,
                               row["scope_type"].strip(), scope_label, basis),
        "document_id": document_id,
        "source_locator": row["source_locator"].strip(),
        "metric_id": metric_id,
        "entity_id": entity_id,
        "period_id": period_id,
        "period_basis": "reported",
        "is_restated": 0,
        "scope_type": row["scope_type"].strip(),
        "scope_label": scope_label,
        "scope_entity_id": None,
        "basis": basis,
        "value_num": float(row["value_num"]),
        "value_text": None,
        "unit": row["unit"].strip(),
        "value_scale": scale,
        "currency": currency,
        "extractor": "manual",              # ★ 人工导出的 CSV = 人抽的
        "extractor_version": None,
        "confidence": None,
        "verify_status": str(row.get("verify_status") or "unverified").strip(),
        "fetched_at": fetched_at,
        "note": str(row.get("note") or "").strip() or None,
    }

    return {"entity": entity, "metric": metric, "period": period, "fact": fact}


# ────────────────────────────────────────────────────────────
#  四、导入
# ────────────────────────────────────────────────────────────

def import_csv(csv_path, db_path, channel_id, channel_name, license_note,
               doc_title=None):
    """把一份 CSV 导进库；返回一份结果字典（给 CLI 打印，也给测试断言）。"""
    csv_path = Path(csv_path)
    fields, raws = read_csv(csv_path)

    result = {"file": csv_path.name, "rows": len(raws), "errors": [],
              "inserted": {name: 0 for name in TABLES}, "fact_added": 0}

    header_problem = check_header(fields)
    if header_problem:
        result["errors"].append(header_problem)
        return result

    fetched_at = to_utc_text(csv_path.stat().st_mtime)
    engine = make_engine(db_path)
    tables = reflect_tables(engine)

    document_id = f"doc_{channel_id}_{sha256_of(csv_path)[:12]}"

    channel_row = {
        "channel_id": channel_id,
        "name": channel_name,
        "kind": "manual",                   # 人工导出 → 手工通道
        "base_url": None,
        "license_note": license_note,
        "robots_checked_at": None,
        "min_interval_sec": 3,              # 合规红线：>=3 秒（这里没有自动抓取，仍按红线填）
        "enabled": 1,
        "note": "人工导出 CSV 通道（M1c）",
    }

    document_row = {
        "document_id": document_id,
        "channel_id": channel_id,
        "title": doc_title or f"人工导出 CSV：{csv_path.name}",
        "doc_type": "csv_export",
        "source_url": None,
        "publish_date": None,
        "fetched_at": fetched_at,
        "file_sha256": sha256_of(csv_path),
        "file_path": csv_path.as_posix(),
        "byte_size": csv_path.stat().st_size,
        "note": "同一份文件重复导入 → 撞 (channel_id, file_sha256) 唯一键，不会重复建 document",
    }

    with engine.begin() as conn:
        result["inserted"]["channel"] += insert_ignore(conn, tables["channel"], [channel_row])
        result["inserted"]["document"] += insert_ignore(conn, tables["document"], [document_row])

        for line_no, row in enumerate(raws, start=2):      # 2 = CSV 第 1 行是表头
            problems = validate_row(row, line_no)
            if problems:
                result["errors"].extend(problems)
                continue                       # ★ 坏行不入库，但整份文件继续导

            ids = resolve_ids(conn, tables, row)
            one = build_row(row, channel_id, document_id, fetched_at, ids)

            for name in ("entity", "metric", "period"):
                result["inserted"][name] += insert_ignore(conn, tables[name], [one[name]])

            # ★ 保险：维度行真在库里吗？（同名/同码撞车时 ON CONFLICT 会静默跳过）
            missing = [n for n in ("entity", "metric", "period")
                       if not row_exists(conn, tables[n], one[n])]
            if missing:
                result["errors"].append(
                    f"第 {line_no} 行 · 维度行没落库（{', '.join(missing)}）"
                    "——大概率撞了别的源已有的同名/同码记录，请核对后再导")
                continue

            added = insert_ignore(conn, tables["fact"], [one["fact"]])
            result["inserted"]["fact"] += added
            result["fact_added"] += added

    return result


# ────────────────────────────────────────────────────────────
#  五、CLI
# ────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description="把一份人工导出的 CSV 导进产业图谱库")
    parser.add_argument("csv", help="CSV 路径，例如 samples/demo_import.csv")
    parser.add_argument("--db", default="data/industry.db", help="数据库路径")
    parser.add_argument("--channel", default="csv_manual", help="渠道 id（同一批数据用一个）")
    parser.add_argument("--name", default="人工导出 CSV", help="渠道人话名")
    parser.add_argument("--license", default="本人人工导出，仅本地研究使用", help="授权说明（必填，不可空）")
    parser.add_argument("--title", default=None, help="这份文件的名字（缺省用文件名）")
    args = parser.parse_args(argv)

    if not Path(args.db).exists():
        print(f"× 没找到 {args.db}；先按 README「怎么跑起来」第 1 步建库", file=sys.stderr)
        return 2

    res = import_csv(args.csv, args.db, args.channel, args.name, args.license, args.title)

    print(f"文件：{res['file']} · 共 {res['rows']} 行")
    for name in TABLES:
        print(f"  {name:9s} +{res['inserted'][name]}")
    print(f"其中 fact 新增 {res['fact_added']} 条")

    if res["errors"]:
        print(f"\n★ 有 {len(res['errors'])} 处没通过校验（这些行没有入库）：")
        for e in res["errors"]:
            print(f"  - {e}")
        return 1

    print("\n★ 全部通过校验。重跑同一份文件：各行应为 +0（幂等）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
