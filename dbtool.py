# @DATE：2026/8/1
# @TIME：
# @AUTHOR：YiLang CHEN
import sqlite3                  # sqlite3：连接 SQLite 数据库
import json                     # json：序列化消息给 OpenAI 接口
import queue                    # queue：线程间传递流式片段
import threading                # threading：后台线程执行模型调用
from datetime import datetime   # datetime：生成当前日期时间
from langchain.agents import create_agent   # LangChain：组装智能体
from langchain.agents.middleware import AgentMiddleware   # LangChain：中间件钩子（wrap_model_call）
from langchain.agents.middleware import ModelRequest, ModelResponse   # LangChain：模型请求/响应对象
from langchain.tools import tool            # LangChain：把函数包装成工具
from langchain_openai import ChatOpenAI     # LangChain：OpenAI 兼容接口的模型客户端
from langchain_core.messages import AIMessage, HumanMessage   # LangChain：消息类型
from langgraph.checkpoint.sqlite import SqliteSaver   # LangGraph：会话记忆持久化到 SQLite 文件
import sqlite3 as _sqlite3   # sqlite3：创建记忆存储的连接


class _GenerationStopped(Exception):
    """内部异常：用户点了"停止生成"，用于中断 agent 的工具/模型调用循环"""
    pass   # 不做任何事，仅作标志；捕获时不当作报错，正常结束


class _StreamMiddleware(AgentMiddleware):
    """拦截模型调用：改用原生 OpenAI 流式接口，实时把推理过程和正文放进队列"""

    def __init__(self, queue_obj, llm, stop_event):
        self.queue = queue_obj   # 流式片段队列
        self.llm = llm           # ChatOpenAI 实例（含 base_url / api_key / model）
        self.stop_event = stop_event   # threading.Event：用户点"停止"时置位，流式循环据此中断

    def wrap_model_call(self, request: ModelRequest, handler):
        """模型调用钩子：绕过默认 invoke，改用原生流式 + 工具调用循环"""
        from openai import OpenAI   # 原生 SDK：直接读 reasoning_content 等原始字段

        if self.stop_event.is_set():   # 用户已点停止：不再发起新的模型调用，直接中断 agent 循环
            raise _GenerationStopped()   # 抛出中断标志，run_agent 捕获后正常结束

        messages = request.messages.copy()   # 复制消息列表，避免污染原状态
        if request.system_message:           # 有系统提示词则放最前
            messages = [request.system_message, *messages]

        def to_dict(m):
            """把 LangChain 消息转成 OpenAI 接口需要的字典格式"""
            content = m.content   # 消息文本内容
            if isinstance(content, list):   # 多模态块列表则只取文本
                content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            # LangChain 角色名和 OpenAI 角色名不完全一样，需要映射
            role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
            d = {"role": role_map.get(m.type, m.type), "content": content}   # 基本角色和内容
            if m.type == "tool":   # 工具消息需要带调用 ID
                d["tool_call_id"] = m.tool_call_id   # 对应工具调用 ID
            if m.type == "ai" and getattr(m, "tool_calls", None):   # 助手消息带工具调用记录
                d["tool_calls"] = [   # 转 OpenAI 格式的工具调用列表
                    {
                        "id": tc["id"],                    # 调用 ID
                        "type": "function",                # 类型固定
                        "function": {                      # 函数信息
                            "name": tc["name"],            # 工具名
                            "arguments": json.dumps(tc["args"]),   # 参数序列化
                        },
                    }
                    for tc in m.tool_calls                  # 遍历历史工具调用
                ]
            return d

        def normalize(msgs):
            """把传入消息统一成可序列化的字典列表"""
            out = []
            for m in msgs:
                if isinstance(m, dict):   # 已是字典则直接转
                    out.append({"role": m["role"], "content": m.get("content", "")})
                else:   # 是 LangChain 消息对象则转换
                    out.append(to_dict(m))
            return out

        raw_messages = normalize(messages)   # 全部消息转字典

        # 把 LangChain 工具转成 OpenAI 接口识别的格式（langchain 1.3 没有 to_openai_tool 方法）
        tool_schemas = []   # 工具 schema 列表
        for t in (request.tools or []):   # 遍历工具
            args_schema = t.args_schema.model_json_schema()   # 取参数 JSON schema（含描述）
            args_schema.pop("title", None)   # 去掉多余的 title 字段，兼容接口校验
            tool_schemas.append({   # 组装 function 格式
                "type": "function",   # 类型固定
                "function": {         # 函数定义
                    "name": t.name,             # 工具名
                    "description": t.description,   # 工具描述
                    "parameters": args_schema,  # 参数 schema
                },
            })

        client = OpenAI(   # 建原生客户端（与 ChatOpenAI 相同地址/密钥）
            base_url=self.llm.openai_api_base,   # 接口地址（属性名是 openai_api_base）
            api_key=self.llm.openai_api_key,     # API 密钥
        )
        model_name = self.llm.model_name     # 模型名

        # --- 流式请求：推理过程和正文实时入队，同时攒完整响应 ---
        stream = client.chat.completions.create(
            model=model_name,            # 模型名
            messages=raw_messages,       # 对话上下文
            tools=tool_schemas or None,  # 工具清单（没有则为 None）
            stream=True,                 # 开启流式输出
        )
        content_parts = []              # 正文片段列表（最后拼成完整回答）
        tool_calls_acc = {}             # 工具调用暂存：index -> {id, name, args}
        for chunk in stream:            # 遍历每个流式分块
            if self.stop_event.is_set():   # 用户点了停止：立即中断流式接收，不再继续攒内容
                break                    # 跳出循环，用已收到的片段收尾
            if not chunk.choices:       # 无 choices 则跳过
                continue
            choice = chunk.choices[0]   # 第一个候选
            delta = choice.delta        # 本分块的增量
            reasoning = getattr(delta, "reasoning_content", None)   # 推理过程文本
            if reasoning:               # 有推理片段
                self.queue.put(("reasoning", reasoning))   # 实时推送给界面
            if delta.content:           # 有正文片段
                content_parts.append(delta.content)        # 攒入正文列表
                self.queue.put(("content", delta.content)) # 实时推送给界面
            if delta.tool_calls:        # 有工具调用片段（流式分片下发）
                for tc in delta.tool_calls:                # 遍历每个调用
                    slot = tool_calls_acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})   # 按 index 取暂存位
                    if tc.id:                              # 补全调用 ID
                        slot["id"] = tc.id                 # 记录 ID
                    if tc.function:                        # 补全函数信息
                        if tc.function.name:               # 工具名分片
                            slot["name"] = tc.function.name   # 记录工具名
                        if tc.function.arguments:          # 参数分片
                            slot["args"] += tc.function.arguments   # 拼接参数 JSON

        content = "".join(content_parts)   # 完整正文

        if self.stop_event.is_set():   # 流式中途被停止：放弃这一段（可能不完整），直接中断 agent 循环
            raise _GenerationStopped()   # 抛出中断标志，run_agent 捕获后正常结束

        tool_calls = []   # 默认无工具调用（pydantic 要求列表，不能为 None）
        if tool_calls_acc:  # 模型要求调用工具
            tool_calls = [   # 按 index 排序组装
                {
                    "id": slot["id"],                        # 调用 ID
                    "name": slot["name"],                    # 工具名
                    "args": json.loads(slot["args"] or "{}"),   # 参数反序列化
                    "type": "tool_call",                     # 类型
                }
                for _, slot in sorted(tool_calls_acc.items())   # 按 index 升序
            ]

        result_msg = AIMessage(content=content, tool_calls=tool_calls)   # 组装回复消息
        return ModelResponse(result=[result_msg])   # 返回给 agent 继续流转（有工具调用则自动进工具节点）


class Productiontool:
    """S90 生产数据助手：LangChain 智能体 + 数据库工具 + 推理过程可见的流式输出 + 会话记忆"""

    def __init__(self, db_path: str, api_key: str, model_name: str, base_url: str,
                 memory_db: str = "agent_memory.db"):
        """初始化：模型客户端、工具、智能体（带持久化会话记忆）"""
        self.db_path = db_path   # 生产数据库文件路径
        self.llm = ChatOpenAI(model=model_name,   # 指定模型名
                              base_url=base_url,  # 接口地址，如 https://xxx/v1
                              api_key=api_key,    # API 密钥
                              temperature=0)      # 生产分析场景设为 0，回答稳定
        # 会话记忆：SQLite 文件持久化，同 thread_id 的多轮对话共享上下文
        self._memory_conn = _sqlite3.connect(memory_db, check_same_thread=False)   # 记忆库连接（后台线程也要用，关闭线程检查）
        self.checkpointer = SqliteSaver(self._memory_conn)   # 记忆检查点：按 thread_id 存取对话历史
        self.tools = self._setup_tools()   # 准备工具列表
        self._stream_queue = queue.Queue()   # 流式片段队列（线程间传递）
        self._stop_event = threading.Event()   # 停止标志：用户点"停止生成"时置位，流式/agent循环据此中断
        self.agent = create_agent(         # 用 LangChain 组装智能体
            model=self.llm,                # 底层模型
            tools=self.tools,              # 可用工具
            system_prompt=self._make_prompt(),   # 系统提示词
            middleware=[_StreamMiddleware(self._stream_queue, self.llm, self._stop_event)],   # 挂载流式中间件（带停止标志）
            checkpointer=self.checkpointer,   # 挂载记忆检查点：invoke 时按 thread_id 自动读写对话历史
        )

    def request_stop(self):
        """请求停止当前正在进行的生成：置位停止标志，流式循环会在下个分块处中断"""
        self._stop_event.set()   # 置位标志，让后台线程尽快停下

    def reset_stop(self):
        """新一轮提问前清空停止标志，允许再次正常生成"""
        self._stop_event.clear()   # 清除标志，恢复生成能力

    def get_threads(self):
        """列出所有记忆会话的 thread_id（供界面做会话切换）"""
        return list(self.checkpointer.list(None))   # 返回所有检查点元数据

    def delete_thread(self, thread_id: str):
        """删除某会话的全部记忆（thread_id 对应的检查点链）"""
        self.checkpointer.delete_thread(thread_id)   # 删除该会话所有检查点

    def _get_conn(self):
        """建立数据库连接，开启外键约束"""
        conn = sqlite3.connect(self.db_path)   # 按数据库路径连接
        conn.execute("PRAGMA foreign_keys = ON")   # 开启外键，保证级联删除
        return conn

    def _rejected_range_hint(self, project_name: str) -> str:
        """查询某工区坏炮的全库日期范围，用于'当前日期范围查不到时'给出兜底提示。
        返回形如'该工区坏炮实际分布在 2026-02-05 至 2026-02-05，共 141 条'。
        自行开连接，不依赖调用方传入的连接（因为各工具的空分支可能在 with 块外）。"""
        with self._get_conn() as conn:
            row = conn.execute(
            "SELECT MIN(wd.work_date), MAX(wd.work_date), COUNT(*) "
            "FROM rejected_shot r "
            "JOIN work_day wd ON wd.id = r.work_day_id "
            "JOIN project p ON p.id = r.project_id "
            "WHERE p.name = ?",
            (project_name,)
            ).fetchone()   # 该工区已关联作业日的坏炮最早/最晚日期 + 总数
            unlinked = conn.execute(
                "SELECT COUNT(*) FROM rejected_shot r "
                "JOIN project p ON p.id = r.project_id "
                "WHERE p.name = ? AND r.work_day_id IS NULL",
                (project_name,)
            ).fetchone()[0]   # 未关联到作业日的坏炮数（日期错位时的存量）
            if row[2] == 0 and unlinked == 0:
                return f"{project_name} 工区没有任何坏炮记录"   # 该工区彻底没坏炮
            hint = f"注意：{project_name} 工区实际有坏炮"   # 兜底提示开头
            if row[2] > 0:
                hint += f"在 {row[0]} 至 {row[1]}（共 {row[2]} 条）"   # 已关联的日期范围
            if unlinked > 0:
                hint += f"；另有 {unlinked} 条坏炮未关联到作业日（日期可能更早）"   # 未关联提示
            hint += "。请用这些日期范围重新调用工具，不要直接说'没有坏炮'。"   # 引导模型回查
            return hint

    def _setup_tools(self):
        """定义全部工具，返回 LangChain 工具列表"""

        @tool
        def list_projects() -> str:
            """列出所有工区及其设计点数"""
            with self._get_conn() as conn:     # 连接自动关闭
                rows = conn.execute(
                    "SELECT p.name, COUNT(dp.id) FROM project p "
                    "LEFT JOIN design_point dp ON dp.project_id = p.id "
                    "GROUP BY p.id ORDER BY p.name"
                ).fetchall()   # 工区名 + 设计点数
            if not rows:
                return "暂无工区数据"   # 空库提示
            return "\n".join(f"{r[0]}：{r[1]} 设计点" for r in rows)   # 逐行列出

        @tool
        def get_design_count(project_name: str) -> str:
            """获取某工区的设计点总数，参数 project_name 为工区名"""
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM design_point dp "
                    "JOIN project p ON p.id = dp.project_id WHERE p.name = ?",
                    (project_name,)
                ).fetchone()   # 按工区名计数
            return f"{project_name} 设计点总数：{row[0]}"

        @tool
        def get_total_shots(project_name: str, start_date: str, end_date: str) -> str:
            """获取某工区指定日期范围内的总生产炮数，日期格式 YYYY-MM-DD"""
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM shot_attempt sa "
                    "JOIN work_day wd ON wd.id = sa.work_day_id "
                    "JOIN project p ON p.id = wd.project_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?",
                    (project_name, start_date, end_date)
                ).fetchone()   # 时间段内总炮数
            return f"{project_name} {start_date} 至 {end_date} 总炮数：{row[0]}"

        @tool
        def get_daily_shots(project_name: str, start_date: str, end_date: str) -> str:
            """按天统计某工区指定日期范围内的生产炮数，日期格式 YYYY-MM-DD"""
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT wd.work_date, COUNT(*) FROM shot_attempt sa "
                    "JOIN work_day wd ON wd.id = sa.work_day_id "
                    "JOIN project p ON p.id = wd.project_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ? "
                    "GROUP BY wd.work_date ORDER BY wd.work_date",
                    (project_name, start_date, end_date)
                ).fetchall()   # 按天分组统计
            if not rows:
                return "该时间范围内没有数据"   # 无数据提示
            return "\n".join(f"{r[0]}：{r[1]} 炮" for r in rows)   # 每天一行

        @tool
        def get_completion_stats(project_name: str, start_date: str, end_date: str) -> str:
            """获取某工区指定日期范围内的完成进度：设计总数、已完成炮数、百分比"""
            with self._get_conn() as conn:
                design = conn.execute(
                    "SELECT COUNT(*) FROM design_point dp "
                    "JOIN project p ON p.id = dp.project_id WHERE p.name = ?",
                    (project_name,)
                ).fetchone()[0]   # 设计点总数
                shots = conn.execute(
                    "SELECT COUNT(*) FROM shot_attempt sa "
                    "JOIN work_day wd ON wd.id = sa.work_day_id "
                    "JOIN project p ON p.id = wd.project_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?",
                    (project_name, start_date, end_date)
                ).fetchone()[0]   # 已完成炮数
            pct = f"{shots / design * 100:.1f}%" if design > 0 else "N/A"   # 百分比，防除零
            return f"{project_name} [{start_date} 至 {end_date}] 设计 {design} 炮，已完成 {shots} 炮，进度 {pct}"

        @tool
        def get_work_days(project_name: str) -> str:
            """列出某工区所有有生产记录的作业日期及当日炮数，模型不确定日期范围时先查这个"""
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT wd.work_date, COUNT(sa.id) FROM work_day wd "
                    "JOIN project p ON p.id = wd.project_id "
                    "LEFT JOIN shot_attempt sa ON sa.work_day_id = wd.id "
                    "WHERE p.name = ? GROUP BY wd.id ORDER BY wd.work_date",
                    (project_name,)
                ).fetchall()   # 日期 + 当日炮数
            if not rows:
                return "该工区暂无生产记录"   # 无数据提示
            return "作业日期：" + ", ".join(f"{r[0]}({r[1]}炮)" for r in rows)   # 逗号连接列出

        @tool
        def get_rejected_reasons(project_name: str, start_date: str, end_date: str) -> str:
            """按坏炮原因分组统计某工区某日期范围内的坏炮数及占比，用于快速定位主要坏炮原因。日期格式 YYYY-MM-DD"""
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT r.reject_reason, COUNT(*) AS cnt "
                    "FROM rejected_shot r "
                    "JOIN work_day wd ON wd.id = r.work_day_id "
                    "JOIN project p ON p.id = r.project_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ? "
                    "GROUP BY r.reject_reason ORDER BY cnt DESC",
                    (project_name, start_date, end_date)
                ).fetchall()   # 按原因分组计数，降序
                unlinked = conn.execute(
                    "SELECT COUNT(*) FROM rejected_shot r "
                    "JOIN project p ON p.id = r.project_id "
                    "WHERE p.name = ? AND r.work_day_id IS NULL",
                    (project_name,)
                ).fetchone()[0]   # 匹配不到作业日的坏炮数（日期错位时的存量）
            total = sum(r[1] for r in rows)   # 时间段内坏炮总数
            if total == 0:
                return self._rejected_range_hint(project_name)   # 当前范围无结果：提示该工区实际坏炮分布，引导回查
            lines = [f"{project_name} {start_date} 至 {end_date} 共 {total} 条坏炮："]
            for reason, cnt in rows:   # 逐原因列出条数和占比
                lines.append(f"- {reason}：{cnt} 条（{cnt / total * 100:.1f}%）")
            if unlinked:
                lines.append(f"- （另有 {unlinked} 条坏炮未关联到作业日，未计入上面统计）")   # 提示未关联存量
            return "\n".join(lines)

        @tool
        def get_rejected_detail(project_name: str, start_date: str, end_date: str,
                                reason: str = None, line: str = None) -> str:
            """列出某工区某日期范围内的坏炮明细，可指定 reason（坏炮原因）和 line（测线）过滤。用于核查某类原因具体在哪几炮"""
            sql = ("SELECT r.line, r.point, r.shot_prompt, r.shot_time, r.reject_reason, wd.work_date "
                   "FROM rejected_shot r "
                   "JOIN work_day wd ON wd.id = r.work_day_id "
                   "JOIN project p ON p.id = r.project_id "
                   "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ?")
            params = [project_name, start_date, end_date]
            if reason:   # 按原因过滤
                sql += " AND r.reject_reason = ?"
                params.append(reason)
            if line:   # 按测线过滤
                sql += " AND r.line = ?"
                params.append(line)
            sql += " ORDER BY wd.work_date, r.line, r.point, r.shot_prompt"
            with self._get_conn() as conn:
                rows = conn.execute(sql, params).fetchall()   # 明细行
            if not rows:
                return self._rejected_range_hint(project_name)   # 明细为空：提示该工区实际坏炮分布，引导回查
            head = f"{project_name} {start_date} 至 {end_date}" + (f" 原因={reason}" if reason else "") + (f" 线={line}" if line else "") + f"，共 {len(rows)} 条："
            return head + "\n" + "\n".join(
                f"{r[5]} 线{r[0]} 点{r[1]} 第{r[2]}次 {r[3]}：{r[4]}" for r in rows)   # 逐条列出

        @tool
        def get_rejected_by_swath(project_name: str, start_date: str, end_date: str) -> str:
            """按束号（swath）聚合某工区某日期范围内的坏炮数，用于发现坏炮是否系统性集中在某个束。日期格式 YYYY-MM-DD"""
            with self._get_conn() as conn:
                # 坏炮本身无 swath 列，按 (line,point) 关联设计点的 swath，找不到的归入"未知"
                rows = conn.execute(
                    "SELECT COALESCE(dp.swath, '未知') AS swath, COUNT(*) AS cnt "
                    "FROM rejected_shot r "
                    "JOIN work_day wd ON wd.id = r.work_day_id "
                    "JOIN project p ON p.id = r.project_id "
                    "LEFT JOIN design_point dp ON dp.id = r.design_point_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ? "
                    "GROUP BY COALESCE(dp.swath, '未知') ORDER BY cnt DESC",
                    (project_name, start_date, end_date)
                ).fetchall()   # 按 swath 聚合计数
            if not rows:
                return self._rejected_range_hint(project_name)   # 当前范围无结果：提示该工区实际坏炮分布，引导回查
            total = sum(r[1] for r in rows)   # 总坏炮数
            lines = [f"{project_name} {start_date} 至 {end_date} 坏炮按束分布（共 {total} 条）："]
            for swath, cnt in rows:   # 逐束列出
                lines.append(f"- 束 {swath}：{cnt} 条（{cnt / total * 100:.1f}%）")
            return "\n".join(lines)

        @tool
        def get_rejected_report(project_name: str, start_date: str, end_date: str) -> str:
            """生成某工区某日期范围内坏炮的综合分析报告：原因分布、按束分布、按测线集中度。一次性返回给模型做因果推断"""
            with self._get_conn() as conn:
                # 原因分布
                by_reason = conn.execute(
                    "SELECT r.reject_reason, COUNT(*) FROM rejected_shot r "
                    "JOIN work_day wd ON wd.id = r.work_day_id "
                    "JOIN project p ON p.id = r.project_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ? "
                    "GROUP BY r.reject_reason ORDER BY 2 DESC",
                    (project_name, start_date, end_date)
                ).fetchall()
                # 按束分布
                by_swath = conn.execute(
                    "SELECT COALESCE(dp.swath, '未知'), COUNT(*) FROM rejected_shot r "
                    "JOIN work_day wd ON wd.id = r.work_day_id "
                    "JOIN project p ON p.id = r.project_id "
                    "LEFT JOIN design_point dp ON dp.id = r.design_point_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ? "
                    "GROUP BY COALESCE(dp.swath, '未知') ORDER BY 2 DESC",
                    (project_name, start_date, end_date)
                ).fetchall()
                # 按测线集中度：坏炮数最多的 5 条测线
                by_line = conn.execute(
                    "SELECT r.line, COUNT(*) FROM rejected_shot r "
                    "JOIN work_day wd ON wd.id = r.work_day_id "
                    "JOIN project p ON p.id = r.project_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ? "
                    "GROUP BY r.line ORDER BY 2 DESC LIMIT 5",
                    (project_name, start_date, end_date)
                ).fetchall()
            total = sum(r[1] for r in by_reason)   # 总坏炮数
            if total == 0:
                return self._rejected_range_hint(project_name)   # 当前范围无结果：提示该工区实际坏炮分布，引导回查
            # 组装三段式报告：总览 / 原因分布 / 束与线分布
            out = [f"{project_name} {start_date} 至 {end_date} 坏炮综合报告（共 {total} 条）"]
            out.append("\n【原因分布】")
            out += [f"- {r[0]}：{r[1]}（{r[1] / total * 100:.1f}%）" for r in by_reason]
            out.append("\n【按束分布】")
            out += [f"- 束 {r[0]}：{r[1]}" for r in by_swath]
            out.append("\n【坏炮最集中的测线】")
            out += [f"- 线 {r[0]}：{r[1]} 条" for r in by_line]
            return "\n".join(out)

        return [list_projects, get_design_count, get_total_shots,
                get_daily_shots, get_completion_stats, get_work_days,
                get_rejected_reasons, get_rejected_detail, get_rejected_by_swath,
                get_rejected_report]

    def _make_prompt(self):
        """生成系统提示词，含当前日期和工具使用规则"""
        now = datetime.now()   # 当前时刻
        today = now.strftime("%Y-%m-%d")   # 今天
        this_month = now.strftime("%Y-%m")   # 本月
        return f"""你是 S90 地震勘探生产数据助手。现在日期：{today}，第 {now.isocalendar()[1]} 周。

### 日期推理
- 本月：{this_month}-01 至 {today}
- 今年：{now.year}-01-01 至 {today}
- 用户提到"本月""上周""最近7天"等相对时间，必须换算为 YYYY-MM-DD 再调用工具
- 不确定工区有哪些作业日期时，先调 get_work_days 确认

### 坏炮分析（自动总结原因）
- 用户问"坏炮主要原因""为什么坏炮""坏炮分析"时，用工具数据回答，不编造数字
- 推荐流程：先 get_rejected_reasons 看主因分布 → 命中主因后用 get_rejected_detail 看具体炮 → 需要定位束/测线时用 get_rejected_by_swath 或 get_rejected_report 看是否系统性集中
- 综合结论按三段式组织：①主要问题（占比最高的坏炮原因） ②疑似系统原因（某束 swath / 某测线坏炮是否异常集中） ③建议核查项（哪些具体炮/时间需要人工复核）
- 若工具提示"有 N 条坏炮未关联到作业日"，说明这些坏炮日期可能早于已入库的生产记录，应在回答中单独说明，不要把"未关联"误读成"没有坏炮"

### 规则
- 涉及数据库的查询必须用工具，工具返回的数据直接引用，不要编造数字
- 与生产数据无关的闲聊正常回答即可
- 回答用简体中文
"""

    def ask_stream(self, query: str, thread_id: str = "default"):
        """流式问答生成器：先流式输出推理过程，再逐 token 输出回答（带会话记忆）"""
        self.reset_stop()   # 新轮提问先清空停止标志，保证能正常生成
        # 清空队列，确保本轮的片段干净
        while not self._stream_queue.empty():
            self._stream_queue.get_nowait()

        # 后台线程执行 agent（工具调用 + 模型调用都在里面）
        def run_agent():
            try:
                # config 里的 thread_id 决定记忆会话：同一 thread_id 多轮对话共享上下文
                self.agent.invoke(
                    {"messages": [HumanMessage(content=query)]},
                    config={"configurable": {"thread_id": thread_id}},   # 指定会话 ID，按此读写记忆
                )   # 执行智能体
            except _GenerationStopped:   # 用户点了"停止"：不算报错，正常结束本轮
                pass                     # 不 put error，静默收尾
            except Exception as e:
                self._stream_queue.put(("error", str(e)))   # 异常也放进队列
            finally:
                self._stream_queue.put(("done", None))   # 结束标记

        threading.Thread(target=run_agent, daemon=True).start()   # 启动后台线程

        # 主线程从队列读片段，逐个交给界面显示
        while True:
            kind, payload = self._stream_queue.get()   # 阻塞等待新片段
            if kind == "done":   # 结束标记：退出循环
                break
            if kind == "error":   # 错误信息：抛给调用方
                raise RuntimeError(payload)
            yield (kind, payload)   # ("reasoning", 思考片段) 或 ("content", 正文片段)
