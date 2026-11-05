# Alembic 迁移

- 建库入口：`alembic upgrade head`；`sql/schema.sql` 降级为设计快照
- 模式：SQL 迁移（`target_metadata = None`）——表结构是 SQL-first，没有 ORM/Table 定义
- 迁移链：`0001`（基线：12 表 + 3 触发器 + 2 视图）→ `0002`（`entity.website`）

## 验收

空库 `upgrade head`：13 张表（12 + `alembic_version`）、2 视图、3 触发器。
加列 / 回滚输出见同目录 `alembic-*.txt`。

## 踩过的坑

1. **迁移文件跑过之后改内容不会重跑** —— 版本表记的是 revision id，不是文件内容。
   症状：`alembic current` 显示 0002，但列没加上。
   修复：`alembic stamp 0001` → `alembic upgrade head`。
2. `PRAGMA foreign_keys = ON` 在迁移事务里是空操作。
3. 一条 `op.execute()` 只能跑一条语句；baseline 用 `sqlite3.complete_statement` 切。