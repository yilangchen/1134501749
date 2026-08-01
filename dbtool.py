# @DATE：2026/2/21
# @TIME：
# @AUTHOR：YiLang CHEN
import sqlite3   # sqlite3：连接 SQLite 数据库
from datetime import datetime, date   # datetime：生成当前日期等时间信息
from langchain.agents import create_agent   # LangChain：创建 AI 智能体
from langchain.tools import tool            # LangChain：把函数包装成工具
from langchain_openai import ChatOpenAI     # 用 OpenAI 接口的大模型


class Productiontool:
    def __init__(self, db_path: str, api_key: str, model_name: str, base_url: str):
        """初始化代理"""
        self.db_path = db_path   # 数据库文件路径
        self.llm = ChatOpenAI(model=model_name,   # 指定模型名
                              base_url=base_url,  # 指定接口地址（兼容代理/中转）
                              api_key=api_key,    # API 密钥
                              temperature=0)      # 生产分析场景设为 0，保持稳定
        self.tools = self._setup_tools()   # 准备工具列表
        self.current_date = datetime.now().strftime("%Y-%m-%d")   # 当前日期
        self.agent = self._create_myagent()   # 创建智能体

    def _get_conn(self):
        """建立数据库连接"""
        conn = sqlite3.connect(self.db_path)   # 按数据库路径连接
        conn.execute("PRAGMA foreign_keys = ON")  # 开启外键约束
        return conn

    def _setup_tools(self):
        """准备 agent 可调用的工具"""

        @tool
        def list_projects() -> str:
            """列出所有工区及其设计点数"""
            with self._get_conn() as conn:     # 自动关闭连接
                rows = conn.execute(
                    "SELECT p.name, COUNT(dp.id) FROM project p "
                    "LEFT JOIN design_point dp ON dp.project_id = p.id "
                    "GROUP BY p.id ORDER BY p.name"
                ).fetchall()   # 工区名 + 设计点数
            if not rows:
                return "暂无工区数据"   # 空库
            return "\n".join(f"{r[0]}：{r[1]} 设计点" for r in rows)   # 逐行列出

        @tool
        def get_design_count(project_name: str) -> str:
            """获取某个工区的设计点总数，参数 project_name 为工区名"""
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM design_point dp "
                    "JOIN project p ON p.id = dp.project_id WHERE p.name = ?",
                    (project_name,)
                ).fetchone()   # 计数查询
            return f"{project_name} 设计点总数：{row[0]}"

        @tool
        def get_total_shots(project_name: str, start_date: str, end_date: str) -> str:
            """获取某工区指定日期范围内的总生产炮数。日期格式 YYYY-MM-DD"""
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
            """获取某工区指定日期范围内的每日炮数。日期格式 YYYY-MM-DD"""
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT wd.work_date, COUNT(*) FROM shot_attempt sa "
                    "JOIN work_day wd ON wd.id = sa.work_day_id "
                    "JOIN project p ON p.id = wd.project_id "
                    "WHERE p.name = ? AND wd.work_date BETWEEN ? AND ? "
                    "GROUP BY wd.work_date ORDER BY wd.work_date",
                    (project_name, start_date, end_date)
                ).fetchall()   # 按日分组统计
            if not rows:
                return "该时间范围内没有数据"   # 无数据
            return "\n".join(f"{r[0]}：{r[1]} 炮" for r in rows)  # 逐日显示

        @tool
        def get_completion_stats(project_name: str, start_date: str, end_date: str) -> str:
            """获取某工区指定日期范围内的完成统计：设计总数、已完成炮数、完成百分比"""
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
            pct = f"{shots / design * 100:.1f}%" if design > 0 else "N/A"  # 完成百分比
            return f"{project_name} [{start_date} 至 {end_date}] 设计 {design} 炮，已完成 {shots} 炮，进度 {pct}"

        return [list_projects, get_design_count, get_total_shots, get_daily_shots, get_completion_stats]

    def _make_myagent(self):
        now = datetime.now()   # 当前时刻
        today = now.strftime("%Y-%m-%d")   # 今天
        this_month = now.strftime("%Y-%m")  # 本月

        prompt = f"""你是 S90 地震勘探生产数据助手。现在日期：{today}，第 {now.isocalendar()[1]} 周。

### 日期推理
- 本月：{this_month}-01 至 {today}
- 今年：{now.year}-01-01 至 {today}
- 用户提到"本月""上周"等相对时间，必须换算为 YYYY-MM-DD 再调用工具

### 可用工具
- list_projects: 列出所有工区
- get_design_count: 查某工区设计点总数
- get_total_shots: 查时间段总炮数
- get_daily_shots: 按天查炮数（用户提到"每天""趋势""波动"时用这个）
- get_completion_stats: 查完成进度

### 规则
- 涉及数据库的查询优先用工具，工具能拿到的数据不要凭空编造
- 与生产数据无关的闲聊正常回答即可
"""
        return create_agent(model=self.llm, tools=self.tools, system_prompt=prompt)  # 组装 agent

    def ask_stream(self, query: str):
        """流式问答生成器，逐 token 输出文本"""
        from langchain_core.messages import HumanMessage   # HumanMessage：封装用户输入
        result = self.agent.stream(
            {"messages": [HumanMessage(content=query)]},
            stream_mode="messages"    # 按消息级别流式输出
        )
        for chunk in result:
            content = chunk.get("content") or ""   # 提取当前片段的文本
            if content:
                yield content   # 逐 token 交给调用方显示