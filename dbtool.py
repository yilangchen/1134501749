# @DATE：2026/2/21
# @TIME：
# @AUTHOR：YiLang CHEN
import sqlite3   # sqlite3：连接 SQLite 数据库
from datetime import datetime   # datetime：生成当前日期等时间信息
from langchain.agents import create_agent   # LangChain：创建 AI 智能体
from langchain.tools import tool            # LangChain：把函数包装成工具
# from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI     # 用 OpenAI 接口的大模型
# from langchain_google_genai import ChatGoogleGenerativeAI

class Productiontool:
    def __init__(self, db_path: str, api_key: str, model_name:str, BASE_URL:str):
        """初始化代理"""
        self.db_path = db_path   # 数据库文件路径

        self.llm = ChatOpenAI(model=model_name,   # 指定模型名
                              base_url=BASE_URL,  # 指定接口地址（兼容代理/中转）
                              api_key=api_key)    # API 密钥


        # self.llm = ChatOllama(model=model_name,
        #          base_url=api_key,
        #                       temperature=0,
        #                       num_ctx=4096)
        # self.llm = ChatGoogleGenerativeAI(
        #     model=model_name,
        #     google_api_key=api_key,
        #     temperature=0  # 生产数据分析建议设为 0，保证稳定性
        # )
        self.tools = self._setup_tools()   # 准备可供 AI 调用的工具列表
        self.current_date = datetime.now().strftime("%Y-%m-%d")   # 记录当前日期字符串
        self.agent = self._create_myagent()   # 用工具和提示词创建智能体

    def _get_db_connection(self):
        return sqlite3.connect(self.db_path)   # 建立数据库连接

    def validate_date(date_str):
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")   # 校验日期格式

    def _setup_tools(self):

        @tool
        def get_total_shots(start_date: str, end_date: str) -> str:
            """统计指定日期范围内的总炮数。参数格式：'YYYY-MM-DD'"""
            print(f"--- 正在查询数据库：{start_date} 到 {end_date} ---")
            with self._get_db_connection() as conn:   # 打开连接，用后自动关闭
                cursor = conn.cursor()
                cursor.execute("""
                               SELECT COUNT(*)
                               FROM shot_attempt sa
                               JOIN work_day wd ON wd.id = sa.work_day_id
                               WHERE wd.work_date BETWEEN ? AND ?
                               """, (start_date, end_date))   # 统计时间段内所有生产炮数
                result = cursor.fetchone()[0] or 0   # 取第一个计数值
                return f"{start_date} 到 {end_date} 总炮数：{result}"

        @tool
        def get_daily_shots(start_date: str, end_date: str) -> str:
            """获取指定日期范围内的每日炮数"""
            print(f"--- 正在查询数据库：{start_date} 到 {end_date} ---")
            with self._get_db_connection() as conn:   # 打开连接
                cursor = conn.cursor()
                cursor.execute("""
                               SELECT wd.work_date, COUNT(*)
                               FROM shot_attempt sa
                               JOIN work_day wd ON wd.id = sa.work_day_id
                               WHERE wd.work_date BETWEEN ? AND ?
                               GROUP BY wd.work_date
                               ORDER BY wd.work_date
                               """, (start_date, end_date))   # 按日期分组统计每天炮数

                rows = cursor.fetchall()   # 取全部结果

                if not rows:
                    return "该时间范围内没有数据"   # 无数据显示提示

                result_text = "\n".join([f"{row[0]}：{row[1]}" for row in rows])   # 每行日期：炮数
                return result_text

        return [get_total_shots, get_daily_shots]   # 返回工具列表

    def _create_myagent(self):
        # 获取今天、今年、本月的具体数值
        now = datetime.now()   # 当前时刻
        today = now.strftime("%Y-%m-%d")   # 今天日期
        this_month = now.strftime("%Y-%m")   # 本月月份

        prompt = f"""你是一个拥有实时日期感知能力的生产数据助手。
            现在的精确日期是：{today}。
            现在是 {now.year} 年第 {now.isocalendar()[1]} 周。

            ### 日期转换逻辑：
            - **本月**：从 {this_month}-01 到 {today}。
            - **上月**：计算上个月的第一天到最后一天。
            - **本周**：一周从周一开始,本周就是从本周一到今天。
            - **今年**：从 {now.year}-01-01 到 {today}。

            当用户提到相对时间（如"上周"、"昨天"、"本月"）时，你必须先在脑中将其转换为具体的 YYYY-MM-DD 格式。

            ### 你的职责
            1. 能够回答关于生产数据的查询。
            2. 对于与数据库无关的闲聊（如询问姓名、天气等），请作为助手礼貌回答，不要提及数据库逻辑。

            ### Your Responsibilities
            1. You can answer questions related to production data queries.
            2. For casual conversations unrelated to the database (such as asking your name, greetings, weather, etc.), respond politely as a normal assistant. Do NOT mention any database logic in such cases.

            ### 工具使用规范
            - get_total_shots：获取指定日期范围的生产炮数。
            - get_empty_shots：获取用户位置。
            - calculate_mmp：计算生产mmp。
            - record_mmp_value:记录每月的mmp值。
            - get_daily_shots 当用户提到"趋势"、"每天"、"每日"、"波动"、"按天"时调用 。

            ### Tool Usage Rules
            - get_total_shots: Retrieve the total number of production shots within a specified date range.
            - get_empty_shots: Retrieve the number of empty shots.
            - calculate_mmp: Calculate the production MMP.

            ### 业务逻辑（仅在涉及相关关键词时触发）
            - 只有当用户明确提到"每日"或"炮数"时，才调用 get_total_shots 统计 count(*)。
            - 严禁在用户未提及生产数据时主动讨论统计逻辑。

            ### Business Logic (Trigger Only When Relevant)
            - Only call get_total_shots when the user explicitly mentions keywords such as "daily" or "shot count" (or their Chinese equivalents like "每日" or "炮数").
            - Strictly DO NOT trigger any production statistics logic unless the user clearly refers to production-related data.
        """
        agent = create_agent(   # 组装智能体
            model=self.llm,     # 使用配置的大模型
            tools=self.tools,   # 挂载可调用工具
            system_prompt=prompt   # 注入系统提示词
        )
        return agent

    def ask(self, query: str):
        """外部调用接口"""
        # return self.agent.invoke({"messages": [{"role": "user", "content": query}]},)
        result = self.agent.invoke({"messages": [{"role": "user", "content": query}]})   # 调用智能体回答用户问题

        # 提取最后一条 AIMessage 的 content
        ai_messages = [m for m in result['messages'] if m.__class__.__name__ == 'AIMessage']   # 筛出 AI 回复
        if ai_messages:
            return ai_messages[-1].content   # 返回最后一条 AI 回复内容
        else:
            return ""   # 没有回复则返回空串