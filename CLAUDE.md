# 用户要求

1. 回答前，请说一句：好的，Mr.Chen
2. 写的代码，每一句后面要有这句的解释，精简但易懂

# 地震勘探生产看板 (S90)

Streamlit + SQLite 的生产进度看板。主文件 `app.py`（绘图/导入 + LLM 助手 tab），辅助 `Tool.py`（Plotly 绘图，用 `scattergl` WebGL）、`dbtool.py`（LangChain 助手工具）。数据库 `production.db`。

## LLM 助手记忆（2026-08-01 新增）

- **机制**：LangGraph `SqliteSaver` checkpointer（langchain 1.3 原生记忆，旧 `langchain.memory` 已移除）。`dbtool.py` 的 `Productiontool.__init__` 创建 `agent_memory.db` 连接，`create_agent(checkpointer=...)` 挂载。记忆库连接用 `check_same_thread=False`（后台流式线程共用）。
- **会话键**：`ask_stream(query, thread_id="default")`，`agent.invoke(..., config={"configurable": {"thread_id": ...}})`。同一 thread_id 多轮对话共享上下文，不同 thread_id 互不相干。
- **会话管理**：`production.db` 的 `agent_session` 表（id, name, thread_id）存会话清单，app.py Tab2 有"记忆会话"下拉 + 新建/删除按钮。删除会话 = `agent.delete_thread(thread_id)` 删记忆 + 删表记录。
- **历史恢复**：切换/连接会话时用 `agent.get_state({"configurable": {"thread_id": ...}})` 读消息，过滤 `type in (human, ai)` 且 `content` 非空显示到聊天区。
- **坏炮分析工具**：`dbtool.py` 除进度统计外，另有 4 个坏炮工具——`get_rejected_reasons`（按原因分组计数占比）、`get_rejected_detail`（明细，可按 reason/line 过滤）、`get_rejected_by_swath`（按束聚合，找系统性集中）、`get_rejected_report`（综合报告：原因+束+测线集中度）。系统提示词引导模型按"先主因分布→再明细→再束线定位→三段式结论（主因/疑似系统原因/建议核查）"做因果推断。工具会提示"未关联到作业日的坏炮数"，不要误读成没有坏炮。
- 依赖：`langgraph-checkpoint-sqlite`（uv 已装）。

## 数据库 schema（2026-08-01 重构）

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
| `rejected_shot` | `id, project_id, work_day_id? , design_point_id? , line, point, shot_prompt, x, y, shot_time, reject_reason, src_file` | `work_day_id`/`design_point_id` 可空 |

要点：
- 设计点按 `(line, point)` 去重；`batch_src` 为逗号连接字符串，仅记"该点来自哪次导入"（如 `3,5,7`），不是独立表。
- `swath` 列：导入时从**文件名**提取（`re.search(r'(sw\d+)', 文件名)`），如 `sw123_sps_for_recorder.sps` → `sw123`、`sw123_0731.sps` → `sw123`。设计 SPS 和 daily SPS 都会提取，daily 导入时优先按 `(line, point, swath)` 匹配设计点，匹配不到再退回纯 `(line, point)`。
- 生产炮按 `line + point` 匹配设计点，匹配不到就跳过该行（`app.py` 会提示匹配了几炮）。导入顺序必须是先设计 SPS、再 daily SPS。
- `rejected_shot`（坏炮表）：独立表存 VB 导出的坏炮统计 CSV（如 `0205rejected_summary.csv`），**不塞进 shot_attempt**——因为坏炮常不在 daily SPS 里（如 0205 坏炮而库里作业日从 0206 起）。`work_day_id`/`design_point_id` 可空（宽松关联：按 `(line, point)` 匹配设计点，匹配不到也保留原始 line/point/time，不丢记录）。`shot_prompt` 存第几次激发（同一物理点重炮区分）。`shot_time` 已去掉 CSV 前导 `'`。按日期从**文件名**提取（`0205rejected_summary.csv` → 2月5日 + 侧边栏年份）。
- 删 `project` → 级联删全部；删 `work_day` → 级联删当天 shot_attempt。
- `mmp_records` 为遗留表（4 行），仍在使用但未重构。
- 旧表 `design_sps_db` / `daily_sps_db` / `daily_obs_db` 已废弃但保留在库中未删，可回滚。

## 数据导入（侧边栏）

1. 选择/新建**工区** → 2. 上传设计 SPS → 3. 选**作业年份**（仅年份下拉，日期自动从文件名提取） → 4. 上传 daily SPS（多选）→ 5. 上传**坏炮统计 CSV**（可选，多选）→ 6. 💾 确认入库。
- 设计 SPS 和 daily SPS 都支持多文件；`swath` 从每个文件名提取。
- daily SPS 日期提取：用 `r'[-_](\d{2})(\d{2})\.\w+$'` 从文件名末尾匹配 `-MMDD.ext` 或 `_MMDD.ext`（如 `sw123-0731.sps` → 7月31日）。年份从下拉选的，日期完全自动识别，不提供手动日期输入框。
- **多天导入**：每个文件按自己的文件名日期独立入库（`file_date_map` 文件名→日期），一次可上传多个不同日期的文件，自动按日期分组，同一天的文件会被覆盖式合并。
- 设计 SPS：幂等 upsert（`ON CONFLICT (project_id,line,point) DO UPDATE`），重复导入更新坐标、追加 `batch_src`、`swath` 为空时不覆盖已有值。
- daily SPS：先删该工区该日期的旧炮再重建（**同天重新导入 = 覆盖**）。入库成功后侧边栏显示导入文件清单。
- 坏炮 CSV：解析后 `Time` 去前导 `'`、`shot prompt` 空转 1、`RejectReason` 去空格；日期从文件名提取（`0205rejected_summary.csv` → 2月5日 + 侧边栏年份），文件名无 mmdd 跳过。入库同 daily SPS 覆盖式（先删同工区同日期旧坏炮），每行按 `(line, point)` 宽松匹配设计点，匹配到填 `design_point_id`，否则 NULL 也入库。上传时选对**年份**，否则日期可能错位（如坏炮 0205 落进 2026-02-05 的 work_day，而生产记录从 0206 起，属预期）。
- 老"生产 obs (SPS)"上传路径不再入库（原 `daily_obs_db` 已废弃）。

## 绘图性能

- `Tool.py` 用 `go.Scattergl`（WebGL），几十万点流畅。
- `app.py` 有"⚡"快速模式"复选框：勾选后每层最多画 2 万点（均匀降采样），超限时禁用 hover。275k 设计点 + 409k 生产点全量在普通模式下也能跑。
- **swath 筛选**：用 `st.pills` 水平胶囊多选（不选 = 全部），在 5 列布局第 4 列。**设计点永远画全量**（与时间、swath 无关）；swath 只筛**生产点**（按 `sa.swath` 过滤，不是 `dp.swath`）。
- 两个绘图查询函数都带 `@st.cache_data`，入库成功后必须调用 `load_all_designs.clear()` / `load_daily_sps.clear()` 清缓存，否则新数据不显示。

## 环境与运行

- Python 依赖装在  uv 环境/Users/pinganxilemac/uv_env/.venv/bin/python，本机系统 Python 没有 streamlit/pandas。
- 运行：在 PyCharm 里以解释器跑 `streamlit run app.py`。

## 常见坑

- **SQLite 注释只能用 `--`**，不能写 `#`。executemany/executescript 里的多行 SQL 字符串尤其容易写出 `#`。
- 每个 daily 文件读取时都会加 `_src_file` 列（来源文件名），入库按该列分组找日期，改列结构时别删掉。
- **坏炮 CSV 的 `Time` 列带前导 `'`**（如 `'053410`），解析时用 `.str.lstrip("'")` 清洗，否则时间列带脏引号。
- **坏炮日期可能与已入库 work_day 错位**：坏炮 CSV 独立于 daily SPS（常早于生产入库日期），日期全靠文件名提取 + 侧边栏年份，选错年份会挂错日期。坏炮匹配不到设计点/作业日是宽松关联的预期行为，不是 bug。
