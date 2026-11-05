# CI（GitHub Actions）

工作流 `.github/workflows/ci.yml`：push main → `pip install -e ".[dev]"` → `ruff check .` → `pytest -q`。

## 红 → 绿

| 提交 | 结果 | 说明 |
|---|---|---|
| 1b70930 | ❌ failure | 故意删 fact 的 UNIQUE 六列自然键 → 去重测试变红 |
| 9f1fbc3 | ✅ success | revert，恢复绿 |
| 939dc11 | ✅ success | 最新（含 Alembic） |

- 红：https://github.com/Togawa-Sakiii/industry-atlas/actions/runs/41479651897
- 绿：https://github.com/Togawa-Sakiii/industry-atlas/actions/runs/41479890730
- 最新绿：https://github.com/Togawa-Sakiii/industry-atlas/actions/runs/41667344500

截图：`actions-green.png`