# industry-atlas · 产业图谱

> 一句话：把公开披露的产业数据收进一个可溯源的数据库，做成能对外提供查询的服务，
> 并在最上层用有纪律的方式记录研究判断。

> ⚠️ **声明**：本项目是**个人练手项目**，开发过程中使用了 **DeepSeek 等 AI 助手**辅助；
> 它**不构成任何投资建议**（不做选股 / 荐股 / 预测涨跌 / 目标价），数据全部取自公开渠道，示例数据为虚构。
> 完整条款见 [DISCLAIMER.md](DISCLAIMER.md)。

## 它是什么 / 不是什么

- **是**：一个 Python 后端练手项目 —— 数据管道 + HTTP 服务 + 一套有纪律的研究记录工具。
- **不是**：不是选股 / 荐股工具，不预测涨跌，不写目标价；不做商业产品，没有真实用户。
- 验收标准不是"预测准不准"，而是 **可运行 / 可溯源 / 可重复 / 可讲清**。

## 三层架构

- **L1 产业数据库**：输入 = 公开数据源（开放 API / 公开披露文件 / 人工导出的 CSV）→ 输出 = 结构化表（12 张：维度层 + 事实层 + 图与判断层）
- **L2 产业分析系统**：输入 = 数据库 → 输出 = HTTP 接口（FastAPI），指标计算、查询与分页
- **L3 产业研究与决策分析**：输入 = 数据库 + 人工判断 → 输出 = 论点卡与报告（每个数字都能点回来源）

## 目录结构

```
industry-atlas/
├─ config/sources.json        # 配置驱动：抓哪个源 / 间隔几秒 / 要哪些字段 / 授权说明
├─ data/                      # 数据库与缓存（★ 已进 .gitignore，不入库）
├─ samples/demo_import.csv    # 手工导入通道的样例文件（可在库外照着填）
├─ scripts/engine_probe.py    # SQLAlchemy 连通性验证
├─ sql/schema.sql             # schema v2：12 表 + 3 触发器 + 2 视图
├─ src/industry_atlas/
│   ├─ fetch.py               # 读配置 · 取数（假源 / World Bank 公开 API）· 落盘缓存
│   ├─ cli.py                 # 入口 1：抓数据 → data/cache/*.jsonl
│   ├─ load.py                # 入口 2：缓存 → 清洗映射 → 入库（反射 + 幂等）★ M1b
│   └─ csv_import.py          # 入口 3：人工导出 CSV → 校验 → 入库 ★ M1c
├─ tests/                     # pytest：结构 / 外键 / append-only / 去重 / 空串 / 锁 / 导入通道
└─ DISCLAIMER.md              # 声明与免责：AI 辅助 / 非投资建议 / 数据来源 / 责任限制
```

## 数据来源与合规

- 只取公开数据（开放 API、公开披露文件）；不抓登录内容、不绕反爬、每域 ≥3 秒/次、守 robots.txt
- 本地缓存；重跑导入不产生重复记录（document 按 sha256 唯一 + fact 六列自然键）
- ★ 不使用任何非公开经营数据；不做选股 / 荐股 / 预测涨跌 / 不写目标价
- 当前已接入：
  - **World Bank Open Data**（CC BY 4.0，对外成果须署名 World Bank）—— 公开 API，自动抓
  - **人工导出 CSV**（学校已购库只允许人工查询或导出 → 用 `csv_import` 入库）—— 手工通道
  - 一个本地假源（仅用于跑通管道，指标是中性名 `test_metric_*`，不是任何真实数据）

## 怎么跑起来

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1) 建库（12 张表 + 3 触发器 + 2 视图）
.\.venv\Scripts\python.exe -c "import sqlite3,pathlib; c=sqlite3.connect('data/industry.db'); c.executescript(pathlib.Path('sql/schema.sql').read_text(encoding='utf-8')); c.commit(); print('schema OK')"

# 2) 跑测试
.\.venv\Scripts\python.exe -m pytest -q

# 3) 抓数据（先假源、再世界银行真实源；每个源间隔 3 秒，已缓存则直接命中）
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m industry_atlas.cli

# 4) 入库（缓存 → 维度表 + fact）★ 跑两遍，第二遍应全部 +0
.\.venv\Scripts\python.exe -m industry_atlas.load

# 5) 手工导入 CSV（学校已购库导出的文件走这条）
.\.venv\Scripts\python.exe -m industry_atlas.csv_import samples/demo_import.csv `
    --channel csv_demo --name "人工导出 CSV" --license "本人人工导出，仅本地研究"
```

## 状态

- **M0**（W4）✅ 仓库初始化 + schema v2 设计 + 建库跑通
- **M1a**（W5）✅ 配置驱动 + 假源 + World Bank 公开 API + 落盘缓存 + pytest 全绿
- **M1b**（W6）✅ Core 反射 6 表 + 两源清洗映射（两种形状）+ 入库幂等（连跑 3 次：+6 / +0 / +0）
- **M1c**（W7）✅ CSV 手工导入通道 + 行级校验（坏行不入库但要报第几行）+ 可重复导入
- **下一步 M2a**（W8）：FastAPI 骨架 + 3 个查询接口，本机 8000 端口能打开
