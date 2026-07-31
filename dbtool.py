# @DATE：2026/2/21
# @TIME：
# @AUTHOR：YiLang CHEN
import sqlite3
from datetime import datetime
from langchain.agents import create_agent
from langchain.tools import tool
# from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
# from langchain_google_genai import ChatGoogleGenerativeAI

class Productiontool:
    def __init__(self, db_path: str, api_key: str, model_name:str, BASE_URL:str):
        """初始化代理"""
        self.db_path = db_path

        self.llm = ChatOpenAI(model=model_name,
                              base_url=BASE_URL,
                              api_key=api_key)


        # self.llm = ChatOllama(model=model_name,
        #          base_url=api_key,
        #                       temperature=0,
        #                       num_ctx=4096)
        # self.llm = ChatGoogleGenerativeAI(
        #     model=model_name,
        #     google_api_key=api_key,
        #     temperature=0  # 生产数据分析建议设为 0，保证稳定性
        # )
        self.tools = self._setup_tools()
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.agent = self._create_myagent()

    def _get_db_connection(self):
        return sqlite3.connect(self.db_path)

    def validate_date(date_str):
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")

    def _setup_tools(self):

        @tool
        def get_total_shots(start_date: str, end_date: str) -> str:
            """统计指定日期范围内的总炮数。参数格式：'YYYY-MM-DD'"""
            print(f"--- 正在查询数据库：{start_date} 到 {end_date} ---")
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                               SELECT COUNT(*)
                               FROM shot_attempt sa
                               JOIN work_day wd ON wd.id = sa.work_day_id
                               WHERE wd.work_date BETWEEN ? AND ?
                               """, (start_date, end_date))
                result = cursor.fetchone()[0] or 0
                return f"{start_date} 到 {end_date} 总炮数：{result}"

        @tool
        def get_daily_shots(start_date: str, end_date: str) -> str:
            """获取指定日期范围内的每日炮数"""
            print(f"--- 正在查询数据库：{start_date} 到 {end_date} ---")
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                               SELECT wd.work_date, COUNT(*)
                               FROM shot_attempt sa
                               JOIN work_day wd ON wd.id = sa.work_day_id
                               WHERE wd.work_date BETWEEN ? AND ?
                               GROUP BY wd.work_date
                               ORDER BY wd.work_date
                               """, (start_date, end_date))

                rows = cursor.fetchall()

                if not rows:
                    return "该时间范围内没有数据"

                result_text = "\n".join([f"{row[0]}：{row[1]}" for row in rows])
                return result_text

        return [get_total_shots, get_daily_shots]

    def _create_myagent(self):
        # 获取今天、今年、本月的具体数值
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        this_month = now.strftime("%Y-%m")

        prompt = f"""你是一个拥有实时日期感知能力的生产数据助手。
            现在的精确日期是：{today}。
            现在是 {now.year} 年第 {now.isocalendar()[1]} 周。

            ### 日期转换逻辑：
            - **本月**：从 {this_month}-01 到 {today}。
            - **上月**：计算上个月的第一天到最后一天。
            - **本周**：一周从周一开始,本周就是从本周一到今天。
            - **今年**：从 {now.year}-01-01 到 {today}。
        
            当用户提到相对时间（如“上周”、“昨天”、“本月”）时，你必须先在脑中将其转换为具体的 YYYY-MM-DD 格式。
       
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
            - get_daily_shots 当用户提到“趋势”、“每天”、“每日”、“波动”、“按天”时调用 。
            
            ### Tool Usage Rules
            - get_total_shots: Retrieve the total number of production shots within a specified date range.
            - get_empty_shots: Retrieve the number of empty shots.
            - calculate_mmp: Calculate the production MMP.
    
            ### 业务逻辑（仅在涉及相关关键词时触发）
            - 只有当用户明确提到“每日”或“炮数”时，才调用 get_total_shots 统计 count(*)。
            - 严禁在用户未提及生产数据时主动讨论统计逻辑。
    
            ### Business Logic (Trigger Only When Relevant)
            - Only call get_total_shots when the user explicitly mentions keywords such as "daily" or "shot count" (or their Chinese equivalents like “每日” or “炮数”).
            - Strictly DO NOT trigger any production statistics logic unless the user clearly refers to production-related data.
        """
        agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=prompt
        )
        return agent

    def ask(self, query: str):
        """外部调用接口"""
        # return self.agent.invoke({"messages": [{"role": "user", "content": query}]},)
        result = self.agent.invoke({"messages": [{"role": "user", "content": query}]})

        # 提取最后一条 AIMessage 的 content
        ai_messages = [m for m in result['messages'] if m.__class__.__name__ == 'AIMessage']
        if ai_messages:
            return ai_messages[-1].content
        else:
            return ""