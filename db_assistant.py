# @DATE：2026/2/21
# @TIME：
# @AUTHOR：YiLang CHEN
from langchain_community.llms import Ollama
from langchain_community.utilities import SQLDatabase
from langchain_experimental.sql import SQLDatabaseChain
import requests
from langchain_core.language_models.llms import LLM
from typing import Optional, List

class SeismicDBAssistant:

    def __init__(self, model_name="deepseek-r1:8b"):
        # self.llm = Ollama(model=model_name, temperature=0)
        self.llm = MlvocaLLM()
        self.db = SQLDatabase.from_uri("sqlite:///production.db")

        self.db_chain = SQLDatabaseChain.from_llm(
            self.llm,
            self.db,
            verbose=True,
            use_query_checker=True,
            return_intermediate_steps=True
        )

    def query(self, prompt: str) -> dict:
        """执行数据库智能查询"""

        custom_prompt = (
            "你是一个地震勘探数据专家，请根据数据库查询结果回答用户问题。\n"
            "注意：\n"
            "1. 如果用户提到具体日期（如2月20日），SQL 中使用格式 'YYYY-MM-DD'。\n"
            "2. '炮数' 对应 COUNT(*)。\n\n"
            f"Question: {prompt}"
        )

        response = self.db_chain.invoke(custom_prompt)

        raw_answer = response.get("result", "")
        steps = response.get("intermediate_steps", [])

        return {
            "raw_answer": raw_answer,
            "steps": steps
        }

class MlvocaLLM(LLM):
    model: str = "deepseek-r1:1.5b"

    @property
    def _llm_type(self) -> str:
        return "mlvoca"

    def _call(self, prompt: str, stop: Optional[List[str]] = None) -> str:
        url = "https://mlvoca.com/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt
        }

        response = requests.post(url, json=payload)
        data = response.json()

        # 根据接口返回结构调整
        return data.get("response", "")