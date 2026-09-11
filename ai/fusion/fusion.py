"""KnowledgeFuser — 融合意图+RAG+参考资料 → 课件生成指令"""

import json
import os
import re

from openai import OpenAI
from backend.schemas import IntentResult, RAGDocument


PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


class KnowledgeFuser:
    """知识融合器 — 综合意图、RAG 检索结果和参考资料生成课件指令集"""

    def __init__(self, api_key: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        prompt_path = os.path.join(PROMPT_DIR, "fusion.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.prompt_fusion = f.read()

    def fuse(self, intent: IntentResult, rag_docs: list[RAGDocument],
             references: list[dict] | None = None) -> dict:
        """融合多源信息，返回 GenerationInstruction 字典"""
        intent_json = json.dumps(intent.model_dump(), ensure_ascii=False)

        rag_parts = []
        for i, doc in enumerate(rag_docs):
            rag_parts.append(f"[来源{i+1}: {doc.source}]\n{doc.content}")
        rag_context = "\n\n---\n\n".join(rag_parts) if rag_parts else "（无知识库检索结果）"

        ref_texts = "（无上传参考资料）"
        if references:
            ref_parts = []
            for ref in references:
                file_name = ref.get("file_name", "未知文件")
                content = ref.get("extracted_text", "")
                ref_parts.append(f"--- {file_name} ---\n{content}")
            ref_texts = "\n\n".join(ref_parts)

        prompt = (self.prompt_fusion
                  .replace("{intent_json}", intent_json)
                  .replace("{rag_context}", rag_context)
                  .replace("{reference_texts}", ref_texts))

        try:
            resp = self.client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                timeout=30,
            )
            return _extract_json(resp.choices[0].message.content)
        except Exception as e:
            print(f"[fusion] LLM 调用失败: {e}")
            return {"error": str(e)}
