"""M1b · 入库入口（Core 版 · 不写 ORM 声明式类）

一句话：把 data/cache/*.jsonl 里已经抓到的原始行，反射数据库里现有的表，
清洗成「外圈 5 张表 + 1 条 fact」，重复运行不会重复入库。

三条口径（M1b 的验收就靠它们）：
    ① 反射 —— 表结构以 sql/schema.sql 为唯一真相源，Python 这边一行类都不写
    ② 七问 —— 每条 fact 的"谁说的 / 哪份文件 / 什么指标 / 说的是谁 / 什么期间 / 值 / 谁抽的"
              都能查到（v_fact_trace 一查全出）
    ③ 幂等 —— 六列自然键 UNIQUE + ON CONFLICT DO NOTHING → 第二次跑新增 0 条

两个源、两种形状（这正是"清洗映射"要干的活）：
    wb_industry_chn : 国家 / 指标 / 年份 / 值      → 已有年份与单位，直接映射
    dummy           : 企业 / 指标 / 值（无期间、无单位）→ 期间记为「未标期间」，单位记为「测试单位」

运行（在仓库根目录）：
    .\\.venv\\Scripts\\python.exe -m industry_atlas.load
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, event, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from industry_atlas.fetch import load_config

DB_PATH = Path("data/industry.db")
CACHE_DIR = Path("data/cache")
EXTRACTOR_VERSION = "0.1.0"

# 入库涉及的 6 张表（顺序 = 外键依赖顺序，插入必须按这个次序）
TABLES = ["channel", "entity", "metric", "period", "document", "fact"]


# ────────────────────────────────────────────────────────────
#  一、连接与反射
# ────────────────────────────────────────────────────────────

def make_engine(db_path=DB_PATH):
    """建 engine，并给每条新连接装上 PRAGMA foreign_keys = ON。

    ★ 为什么必须装：SQLite 默认【不】检查外键 —— 不装的话，
      schema.sql 里写的 REFERENCES 全是装饰品（W4 踩过：v0.1 的外键从未生效）。
      schema.sql 开头那句 PRAGMA 只对"执行它的那一次连接"生效，对现在这条连接无效。
    """
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.close()

    return engine


def reflect_tables(engine, names=TABLES):
    """反射：把库里已有的表读成 Table 对象，返回 {表名: Table}。

    Table(name, meta, autoload_with=engine) = "照库里真实的表长什么样，生成一个对象"；
    列名、类型、外键、主键、UNIQUE 全从库里读，Python 这边一个字都不用声明。
    """
    meta = MetaData()
    return {name: Table(name, meta, autoload_with=engine) for name in names}


def show_table(table):
    """打印一张表的形状（列数 + 主键），让人看得见"反射回来的到底是什么"。"""
    pk = ", ".join(c.name for c in table.primary_key) or "（无）"
    print(f"    {table.name:9s} {len(table.columns):2d} 列 · 主键 {pk}")


# ────────────────────────────────────────────────────────────
#  二、读缓存 + 小工具
# ────────────────────────────────────────────────────────────

def read_cache_rows(source_id):
    """读 data/cache/<source_id>.jsonl → (文件路径, 行列表)。"""
    path = CACHE_DIR / f"{source_id}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return path, [json.loads(x) for x in lines if x.strip()]


def sha256_of(path):
    """文件的指纹。

    document 表靠 (channel_id, file_sha256) 唯一 → 同一份文件重跑时撞车，
    这是"重跑不重复"的第一道闸（第二道是 fact 的六列自然键）。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def to_utc_text(ts):
    """本地时间戳 → 库里的 UTC 文本（★ 本库所有时间列一律存 UTC，展示时再转本地）。"""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fact_id_for(document_id, metric_id, period_id, scope_type, scope_label, basis):
    """由「六列自然键」推一个稳定主键。

    ★ 为什么不写死 f0001 / f0002：
      顺序会随数据增删而漂（今天 f0003 是 A，明天可能变成 B），
      而主键撞车时 ON CONFLICT 会静默跳过 —— 那是"数据莫名丢了"的经典来源。
      从自然键算哈希：同一条数字永远同一个 id，新增数据也不会挤掉老行。
    """
    key = "|".join([document_id, metric_id, period_id, scope_type, scope_label, basis])
    return "f_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


# ────────────────────────────────────────────────────────────
#  三、映射层：原始行 → 6 张表的行
# ────────────────────────────────────────────────────────────

def build_wb_rows(source, cache_path, raw, idx):
    """世界银行的一行（国家 / 指标 / 年份 / 值）→ 6 张表各一行。"""
    fetched_at = to_utc_text(cache_path.stat().st_mtime)   # 缓存落盘时间 ≈ 抓取时间
    year = int(raw["year"])

    channel = {
        "channel_id": source["source_id"],
        "name": "World Bank Open Data",
        "kind": "api",
        "base_url": "https://api.worldbank.org",
        "license_note": source["license_note"],
        "robots_checked_at": None,          # ★ 没真去查过 → 留空，不写假时间
        "min_interval_sec": source["min_interval_sec"],
        "enabled": 1,
        "note": "公开 API；对外成果须署名 World Bank",
    }

    entity = {
        "entity_id": "e_chn",
        "name": raw["country"],              # "China"，照源站原文，不改写
        "kind": "region",
        "listed_status": "unknown",
        "region": "CN",
        "note": "国家/地区实体；★ 行业整体数据也要挂一个'说的是谁'（D8 Q4）",
    }

    metric = {
        "metric_id": "manufacturing_va_pct_gdp",
        "code": "manufacturing_va_pct_gdp",
        "name_cn": "制造业增加值占 GDP 比重",
        "unit": "%",
        "definition": "制造业增加值 ÷ 该国 GDP × 100（世界银行 NV.IND.MANF.ZS 口径）",
        "comparability_note": "跨国可比；★ 最新年份常为估算值，与各国统计局口径可能有小数点级差异",
        "kind": "raw",
        "source_hint": "World Bank WDI · NV.IND.MANF.ZS",
    }

    period = {
        "period_id": f"{year}FY",
        "label": f"{year} 年度",
        "kind": "FY",
        "end_date": f"{year}-12-31",
        "fiscal_year": year,
        "is_cumulative": 1,
        "note": "世界银行按自然年统计",
    }

    document = {
        "document_id": f"doc_{source['source_id']}",
        "channel_id": channel["channel_id"],
        "title": "世界银行 API 导出：制造业增加值占 GDP 比重（CHN）",
        "doc_type": "statistics",
        "source_url": source["url"],
        "publish_date": None,
        "fetched_at": fetched_at,
        "file_sha256": sha256_of(cache_path),
        "file_path": cache_path.as_posix(),
        "byte_size": cache_path.stat().st_size,
        "note": "由 industry_atlas.cli 落盘的本地缓存；缓存命中时不再发请求",
    }

    basis = "reported_by_source"
    fact = {
        "fact_id": fact_id_for(document["document_id"], metric["metric_id"],
                               period["period_id"], "whole", "", basis),
        "document_id": document["document_id"],
        "source_locator": f"API JSON rows[{idx}] · country={raw['country']} · date={raw['year']}",
        "metric_id": metric["metric_id"],
        "entity_id": entity["entity_id"],
        "period_id": period["period_id"],
        "period_basis": "reported",
        "is_restated": 0,
        "scope_type": "whole",              # 国家级整体值，不分行业切片
        "scope_label": "",                  # ★ 空串 = 不细分（不是漏填，见 D10）
        "scope_entity_id": None,
        "basis": basis,
        "value_num": float(raw["value"]),
        "value_text": None,
        "unit": "%",
        "value_scale": 1,                   # 百分比不需要放大 1 万倍
        "currency": None,                   # ★ 百分比没有币种 → NULL，不硬塞 CNY
        "extractor": "csv",                 # 结构化文件导入这一档（建库字典里没有 'jsonl'）
        "extractor_version": EXTRACTOR_VERSION,
        "confidence": 1.0,
        "verify_status": "source_verified",
        "fetched_at": fetched_at,
        "note": "世界银行公开 API，真实公开数据",
    }

    return {"channel": channel, "entity": entity, "metric": metric,
            "period": period, "document": document, "fact": fact}


def build_dummy_rows(source, cache_path, raw, idx):
    """本地假源的一行（企业 / 指标 / 值）→ 6 张表各一行。

    ★ 这个源的形状和世界银行完全不同：没有年份、没有单位、没有真正的"文件"。
      映射时要做的判断 = 缺什么就显式写成"未标"，而不是瞎编一个看起来像真的值：
        · 期间 → 「未标期间」（不硬编 2024）
        · 单位 → 「测试单位」（不假装是元）
        · 可信度 → verify_status='unverified' + confidence=0（它就是假数据，别装成真数据）
    """
    fetched_at = to_utc_text(cache_path.stat().st_mtime)

    channel = {
        "channel_id": source["source_id"],
        "name": "本地练习假源",
        "kind": "manual",
        "base_url": None,
        "license_note": source["license_note"],
        "robots_checked_at": None,
        "min_interval_sec": source["min_interval_sec"],
        "enabled": 1,
        "note": "写死的假数据，只用来跑通管道（★ 指标已去隐私化，见 D12）",
    }

    entity = {
        "entity_id": "e_demo_a",
        "name": raw["company"],              # 示例企业 A
        "kind": "company",
        "listed_status": "unknown",
        "region": None,
        "note": "演示用假实体，非真实企业",
    }

    metric = {
        "metric_id": raw["metric"],          # test_metric_a / b / c
        "code": raw["metric"],
        "name_cn": "演示指标 " + raw["metric"].replace("test_metric_", "").upper(),
        "unit": "测试单位",
        "definition": "演示用假指标：只用来跑通管道，不代表任何真实经济含义",
        "comparability_note": "无真实含义，不可与任何指标比较",
        "kind": "raw",
        "source_hint": "本地假源 dummy",
    }

    period = {
        "period_id": "unspecified",
        "label": "未标期间",
        "kind": "RANGE",
        "is_cumulative": 0,
        "note": "假源没有期间字段 —— 不硬编年份",
    }

    document = {
        "document_id": f"doc_{source['source_id']}",
        "channel_id": channel["channel_id"],
        "title": "本地假源缓存（练习用，无真实来源）",
        "doc_type": "csv_export",
        "source_url": None,
        "publish_date": None,
        "fetched_at": fetched_at,
        "file_sha256": sha256_of(cache_path),
        "file_path": cache_path.as_posix(),
        "byte_size": cache_path.stat().st_size,
        "note": "无真实来源，仅用于跑通管道",
    }

    basis = "demo_fixture"
    fact = {
        "fact_id": fact_id_for(document["document_id"], metric["metric_id"],
                               period["period_id"], "whole", "", basis),
        "document_id": document["document_id"],
        "source_locator": f"{cache_path.name} 第 {idx + 1} 行",
        "metric_id": metric["metric_id"],
        "entity_id": entity["entity_id"],
        "period_id": period["period_id"],
        "period_basis": "reported",
        "is_restated": 0,
        "scope_type": "whole",
        "scope_label": "",
        "scope_entity_id": None,
        "basis": basis,
        "value_num": float(raw["value"]),
        "value_text": None,
        "unit": "测试单位",
        "value_scale": 1,
        "currency": None,
        "extractor": "csv",
        "extractor_version": EXTRACTOR_VERSION,
        "confidence": 0.0,                  # ★ 假数据，可信度就是 0
        "verify_status": "unverified",
        "fetched_at": fetched_at,
        "note": "演示用假数据，不入任何结论",
    }

    return {"channel": channel, "entity": entity, "metric": metric,
            "period": period, "document": document, "fact": fact}


BUILDERS = {"wb_json": build_wb_rows, "dummy": build_dummy_rows}


def gather_rows(source, cache_path, raws):
    """一份缓存里的所有行 → 6 张表的行（同一份文件共用的 channel/document 只留一份）。"""
    builder = BUILDERS.get(source["kind"])
    if builder is None:
        raise ValueError(f"还没有为 kind={source['kind']} 写映射函数")

    out = {name: [] for name in TABLES}
    for idx, raw in enumerate(raws):
        one = builder(source, cache_path, raw, idx)
        for name, row in one.items():
            if row not in out[name]:         # 简单去重：内容相同就只留一份
                out[name].append(row)
    return out


# ────────────────────────────────────────────────────────────
#  四、入库（幂等）
# ────────────────────────────────────────────────────────────

def insert_ignore(conn, table, rows):
    """插行；撞上任何唯一键就跳过，返回真正新增的行数。

    sqlite_insert(...).on_conflict_do_nothing() 编译出来就是
        INSERT INTO t (...) VALUES (...) ON CONFLICT DO NOTHING
    ★ M1b 的硬判据就靠这一句：第二次跑这里必须返回 0。
    """
    added = 0
    for row in rows:
        stmt = sqlite_insert(table).values(**row).on_conflict_do_nothing()
        added += conn.execute(stmt).rowcount
    return added


# ────────────────────────────────────────────────────────────
#  五、回查
# ────────────────────────────────────────────────────────────

def show_counts(engine, tables):
    with engine.connect() as conn:
        for name in TABLES:
            n = conn.execute(select(func.count()).select_from(tables[name])).scalar_one()
            print(f"    {name:9s} {n}")


def show_trace(engine, limit=2):
    """回查 v_fact_trace：一条 fact 的"七问"是不是都答得上（M1 验收口径）。"""
    view = Table("v_fact_trace", MetaData(), autoload_with=engine)
    with engine.connect() as conn:
        rows = conn.execute(select(view).order_by(view.c.fact_id).limit(limit)).mappings().all()

    for row in rows:
        currency = row["currency"] if row["currency"] is not None else "无"
        print(f"\n  ── fact_id = {row['fact_id']} ──")
        print(f"    ①谁说的     {row['channel_name']}")
        print(f"    ②哪份文件   {row['document_title']}")
        print(f"    ②哪一页     {row['source_locator']}")
        print(f"    ③什么指标   {row['metric_name']}（{row['metric_code']}）")
        print(f"    ④说的是谁   {row['entity_name']}／切片 {row['scope_type']}"
              f"「{row['scope_label'] or '不细分'}」")
        print(f"    ⑤什么期间   {row['period_label']}／{row['period_basis']}")
        print(f"    ⑥值         {row['value_num']} {row['unit']} × 标度 {row['value_scale']}"
              f"／币种 {currency}")
        print(f"    ⑦谁抽的     {row['extractor']} {row['extractor_version'] or ''}".rstrip())
        print(f"    ⑦多可信     {row['verify_status']}")


# ────────────────────────────────────────────────────────────
#  六、主流程
# ────────────────────────────────────────────────────────────

def main():
    print("=" * 66)
    print("M1b · 反射 → 清洗映射 → 入库（幂等）")
    print("=" * 66)

    cfg = load_config()                    # 复用 M1a 的配置校验（≥3 秒 / license_note 非空）

    if not DB_PATH.exists():
        raise FileNotFoundError(f"没找到 {DB_PATH}；先按 README「怎么跑起来」第 1 步建库")

    engine = make_engine(DB_PATH)

    print("\n[1] 反射：从库结构生成对象（★ 不抄列名）")
    tables = reflect_tables(engine)
    for name in TABLES:
        show_table(tables[name])

    print("\n[2] 映射：每份缓存 → 6 张表的行")
    per_source = {}
    for source in cfg["sources"]:
        cache_path, raws = read_cache_rows(source["source_id"])
        plan = gather_rows(source, cache_path, raws)
        per_source[source["source_id"]] = plan
        print(f"    {source['source_id']:16s} {len(raws)} 行 → fact {len(plan['fact'])} 条")

    print("\n[3] 入库（一个事务；INSERT … ON CONFLICT DO NOTHING）")
    with engine.begin() as conn:
        total = 0
        for source_id, plan in per_source.items():
            print(f"    ── {source_id}")
            for name in TABLES:
                added = insert_ignore(conn, tables[name], plan[name])
                total += added
                print(f"       {name:9s} +{added}")

    print(f"\n[4] 本次新增 {total} 行")
    print("\n[5] 库里现在各表多少行")
    show_counts(engine, tables)

    print("\n[6] 回查 v_fact_trace —— 七问：")
    show_trace(engine, limit=2)

    print("\n★ 验收动作：把同一条命令再跑一遍 —— 新增应为 0 行，各表总数不变。")


if __name__ == "__main__":
    main()
