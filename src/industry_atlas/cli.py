"""M1a 入口：一条命令跑完 配置 -> 取数 -> 落缓存。

2026-10-23 起接了两个源：dummy（练习用假源）+ wb_industry_chn（世界银行真实公开源）。

运行方式（在仓库根目录）：
    $env:PYTHONPATH = 'src'
    .\\.venv\\Scripts\\python.exe -m industry_atlas.cli
"""

import time
from pathlib import Path

from industry_atlas.fetch import load_config, fetch_by_kind, save_cache


def main():
    cfg = load_config()
    for src in cfg["sources"]:
        cache = Path("data/cache") / f"{src['source_id']}.jsonl"

        # ★ 先看缓存：命中就不发请求（"重跑不重复抓"靠这一句）
        if cache.exists():
            print("cache hit", src["source_id"], "->", cache)
            continue

        # ★ 合规限速：请求之前先等够 min_interval_sec（配置层已校验 >= 3）
        time.sleep(src["min_interval_sec"])

        rows = fetch_by_kind(src)
        path, _hit = save_cache(rows, src["source_id"])
        print("fetched", len(rows), "rows ->", path, "(kind =", src["kind"] + ")")


if __name__ == "__main__":   # 只有"直接运行这个文件"时才执行 main()
    main()
