"""M1a 骨架 · 函数版（2026-10-22）

为什么是"函数版"而不是"适配器类"：
    项目方案里 M1a 原本要写抽象基类 Adapter + 每源一个子类，
    但那需要 Python 块 6/7（类、继承、多态），本人当时只讲到块 2。
    → 按红线"卡住就降级、不加时间"先写成普通函数跑通；
    → 等学过块 6/7，再重构成适配器（那次重构本身就是最好的练习）。

本文件只管三件事：读配置 / 拿到原始数据 / 落盘缓存。
★ 不碰数据库（入库是 M1b 的活）。
"""

import json
from pathlib import Path


def load_config(path="config/sources.json"):
    """读配置，并守住合规红线：抓取间隔必须 >= 3 秒。

    数据库里 channel.min_interval_sec 有一个 CHECK (>= 3)；
    这里在配置层再拦一次 —— 同一条红线，两个地方都守。
    """
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for src in cfg["sources"]:
        if src["min_interval_sec"] < 3:
            raise ValueError(
                f"{src['source_id']}：min_interval_sec 必须 >= 3（合规红线，见项目方案 §七）"
            )
        note = str(src.get("license_note", "")).strip()
        if not note:
            raise ValueError(
                f"{src['source_id']}：license_note 不能为空（合规：必须说清这个源授权我用）"
            )
    return cfg


def fetch_dummy(src):
    """冒充"抓网络"：返回 3 条写死的记录。

    字段名先用我们自己的名字，不去对齐 fact 表的列 ——
    "对齐"是 M1b 清洗那一步的事。M1a 只负责拿到原始数据。
    """
    return [
        {"company": "示例企业 A", "metric": "indicator_x", "value": 1.0},
        {"company": "示例企业 A", "metric": "indicator_y", "value": 2.0},
        {"company": "示例企业 A", "metric": "indicator_z", "value": 3.0},
    ]


def save_cache(rows, source_id, cache_dir="data/cache"):
    """落盘成 jsonl；文件已存在就直接返回，算 cache hit（不重复抓）。

    返回 (文件路径, 是否命中缓存)。
    """
    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{source_id}.jsonl"
    if f.exists():
        return f, True
    with f.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return f, False

# ────────────────────────────────────────────────────────────
# 真实源（M1a · 2026-10-23）：世界银行公开 API
# ────────────────────────────────────────────────────────────
# 合规四条（写进代码，不靠自觉）：
#   ① 只抓公开 API，不抓任何需要登录的内容
#   ② UA 表明身份，冒充浏览器只会让你被封
#   ③ 间隔由 cli 层按 min_interval_sec 控制（>=3 秒）—— 见 load_config 的校验
#   ④ 数据许可：World Bank Open Data 要求署名（attribution），对外成果里必须写明来源
UA = "industry-atlas/0.1 (student learning project; public open data only)"


def fetch_wb_json(src):
    """抓世界银行公开 API（JSON），返回 list[dict]。

    为什么这个源"最死"：一条 URL 一个请求、返回标准 JSON、无需鉴权、
    结构多年不变 —— 适合第一次接真网络。
    """
    import httpx

    with httpx.Client(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as client:
        resp = client.get(src["url"])
        resp.raise_for_status()          # 4xx / 5xx 直接抛，不吞
        payload = resp.json()

    _meta, rows = payload[0], payload[1]
    out = []
    for item in rows:
        out.append({
            "country": item["country"]["value"],
            "indicator": item["indicator"]["value"],
            "year": item["date"],
            "value": item["value"],
        })
    return out


def fetch_by_kind(src):
    """按配置里的 kind 选抓取函数（★ 这一步以后要被"适配器类"取代，见文件头注释）"""
    table = {"dummy": fetch_dummy, "wb_json": fetch_wb_json}
    fn = table.get(src["kind"])
    if fn is None:
        raise ValueError(f"未知的 kind：{src['kind']}（可选：{list(table)}）")
    return fn(src)