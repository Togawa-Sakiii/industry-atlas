# industry-atlas · 产业图谱

> 一句话：把公开披露的产业数据收进一个可溯源的数据库，做成能对外提供查询的服务，
> 并在最上层用有纪律的方式记录研究判断。

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
├─ config/sources.json       # 配置驱动：抓哪个源 / 间隔几秒 / 要哪些字段 / 授权说明
├─ data/                     # 数据库与缓存（★ 已进 .gitignore，不入库）
├─ scripts/engine_probe.py   # SQLAlchemy 连通性验证
├─ sql/schema.sql            # schema v2：12 表 + 3 触发器 + 2 视图
├─ src/industry_atlas/       # fetch.py（读配置·取数·落缓存）· cli.py（入口）
└─ tests/test_schema.py      # pytest：结构 / 外键 / append-only / 去重 / 空串 / 锁
```

## 数据来源与合规

- 只取公开数据（开放 API、公开披露文件）；不抓登录内容、不绕反爬、每域 ≥3 秒/次、守 robots.txt
- 本地缓存；重跑导入不产生重复记录
- ★ 不使用任何非公开经营数据；不做选股 / 荐股 / 预测涨跌 / 不写目标价
- 当前已接入：**World Bank Open Data**（CC BY 4.0，对外成果须署名 World Bank）+ 一个本地假源（仅用于跑通管道）

## 怎么跑起来

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 1) 建库（12 张表 + 3 触发器 + 2 视图）
.\.venv\Scripts\python.exe -c "import sqlite3,pathlib; c=sqlite3.connect('data/industry.db'); c.executescript(pathlib.Path('sql/schema.sql').read_text(encoding='utf-8')); c.commit(); print('schema OK')"

# 2) 跑测试（6 项，全绿）
.\.venv\Scripts\python.exe -m pytest -q

# 3) 抓数据（先假源、再世界银行真实源；每个源间隔 3 秒）
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m industry_atlas.cli
```

## 状态

- **M0**（W4）✅ 仓库初始化 + schema v2 设计 + 建库跑通
- **M1a**（W5）✅ 配置驱动 + 假源 + World Bank 公开 API + 落盘缓存 + pytest 6 项全绿
- **下一步 M1b**：清洗 + SQLAlchemy 建模 + 入库（重跑不产生重复记录）
