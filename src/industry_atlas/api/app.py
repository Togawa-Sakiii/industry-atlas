"""M2a · FastAPI 骨架：/health + 3 个查询接口。

运行（仓库根目录）：
    $env:PYTHONPATH = "src"
    .\.venv\Scripts\python.exe -m uvicorn industry_atlas.api.app:app --port 8000
打开 http://127.0.0.1:8000/docs
"""

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select, text

from industry_atlas.api.db import TABLES, engine

app = FastAPI(title="industry-atlas API", version="0.1.0")


def get_conn():
    """每个请求一条连接，用完自动关。"""
    with engine.connect() as conn:
        yield conn


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/entities")
def list_entities(
    q: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn=Depends(get_conn),
):
    t = TABLES["entity"]
    stmt = select(t.c.entity_id, t.c.name, t.c.kind, t.c.region, t.c.listed_status)
    if q:
        stmt = stmt.where(t.c.name.like(f"%{q}%"))
    total = conn.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = [dict(r) for r in conn.execute(stmt.order_by(t.c.name).limit(limit).offset(offset)).mappings()]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@app.get("/facts")
def list_facts(
    entity_id: str | None = None,
    metric_id: str | None = None,
    period_id: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn=Depends(get_conn),
):
    fact, entity = TABLES["fact"], TABLES["entity"]
    metric, period = TABLES["metric"], TABLES["period"]

    stmt = (
        select(
            fact.c.fact_id,
            fact.c.entity_id,
            entity.c.name.label("entity_name"),
            fact.c.metric_id,
            metric.c.name_cn.label("metric_name"),
            fact.c.period_id,
            period.c.label.label("period_label"),
            fact.c.value_num,
            fact.c.value_text,
            fact.c.unit,
            fact.c.value_scale,
            fact.c.currency,
            fact.c.source_locator,
            fact.c.verify_status,
        )
        .join(entity, entity.c.entity_id == fact.c.entity_id)
        .join(metric, metric.c.metric_id == fact.c.metric_id)
        .join(period, period.c.period_id == fact.c.period_id)
    )
    if entity_id:
        stmt = stmt.where(fact.c.entity_id == entity_id)
    if metric_id:
        stmt = stmt.where(fact.c.metric_id == metric_id)
    if period_id:
        stmt = stmt.where(fact.c.period_id == period_id)

    total = conn.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = [dict(r) for r in conn.execute(stmt.limit(limit).offset(offset)).mappings()]
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@app.get("/facts/{fact_id}")
def get_fact(fact_id: str, conn=Depends(get_conn)):
    row = conn.execute(
        text("SELECT * FROM v_fact_trace WHERE fact_id = :fid"), {"fid": fact_id}
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"fact 不存在：{fact_id}")
    return dict(row)