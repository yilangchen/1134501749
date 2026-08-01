# 用户要求

1. 回答前，请说一句：好的，Mr.Chen
2. 写的代码，每一句后面要有这句的解释，精简但易懂

# 地震勘探生产看板 (S90)

Streamlit + SQLite 的生产进度看板。主文件 `app.py`（绘图/导入 + LLM 助手 tab），辅助 `Tool.py`（Plotly 绘图，用 `scattergl` WebGL）、`dbtool.py`（LangChain 助手工具）。数据库 `production.db`。

## 数据库 schema（2026-07-31 重构）

4 张表，外键级联。所有 SQLite 连接需先 `PRAGMA foreign_keys = ON`。

```
project (工区)
 ├── design_point (设计点)   -- 上传的设计 SPS：line/point/x/y/batch_src
 └── work_day (作业日)       -- 某工区某一天，一行
      └── shot_attempt (生产记录) -- 每天每炮，必须指向设计点
```

| 表 | 关键字段 | 约束 |
|---|---|---|
| `project` | `id, name` | `name` UNIQUE |
| `work_day` | `id, project_id, work_date` | `(project_id, work_date)` UNIQUE |
| `design_point` | `id, project_id, line, point, x, y, batch_src, swath` | `(project_id, line, point)` UNIQUE |
| `shot_attempt` | `id, work_day_id, design_point_id, elevation, gps_time, swath` | 两个外键均 NOT NULL |

要点：
- 设计点按 `(line, point)` 去重；`batch_src` 为逗号连接字符串，仅记"该点来自哪次导入"（如 `3,5,7`），不是独立表。
- `swath` 列：导入时从**文件名**提取（`re.search(r'(sw\d+)', 文件名)`），如 `sw123_sps_for_recorder.sps` → `sw123`、`sw123_0205.sps` → `sw123`。设计 SPS 和 daily SPS 都会提取，daily 导入时优先按 `(line, point, swath)` 匹配设计点，匹配不到再退回纯 `(line, point)`。
- 生产炮按 `line + point` 匹配设计点，匹配不到就跳过该行（`app.py` 会提示匹配了几炮）。导入顺序必须是先设计 SPS、再 daily SPS。
- 删 `project` → 级联删全部；删 `work_day` → 级联删当天 shot_attempt。
- `mmp_records` 为遗留表（4 行），仍在使用但未重构。
- 旧表 `design_sps_db` / `daily_sps_db` / `daily_obs_db` 已废弃但保留在库中未删，可回滚。

## 数据导入（侧边栏）

1. 选择/新建**工区** → 2. 上传设计 SPS → 3. 选作业日期 → 4. 上传 daily SPS（多选）→ 5. 💾 确认入库。
- 设计 SPS 和 daily SPS 都支持多文件；`swath` 从每个文件名提取。
- 设计 SPS：幂等 upsert（`ON CONFLICT (project_id,line,point) DO UPDATE`），重复导入更新坐标、追加 `batch_src`、`swath` 为空时不覆盖已有值。
- daily SPS：先删该工区该日期的旧炮再重建（**同天重新导入 = 覆盖**）。
- 老"生产 obs (SPS)"上传路径不再入库（原 `daily_obs_db` 已废弃）。

## 绘图性能

- `Tool.py` 用 `go.Scattergl`（WebGL），几十万点流畅。
- `app.py` 有"⚡ 快速模式"复选框：勾选后每层最多画 2 万点（均匀降采样），超限时禁用 hover。275k 设计点 + 409k 生产点全量在普通模式下也能跑。

## 环境与运行

- Python 依赖装在  uv 环境/Users/pinganxilemac/uv_env/.venv/bin/python，本机系统 Python 没有 streamlit/pandas。
- 运行：在 PyCharm 里以解释器跑 `streamlit run app.py`。
