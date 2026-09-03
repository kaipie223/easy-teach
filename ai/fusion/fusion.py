"""KnowledgeFuser — 融合意图+RAG+参考资料 → 课件生成指令"""

import json
import os
import re
import sys
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, "backend"))

from openai import OpenAI
from schemas import IntentResult, RAGDocument


PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def _extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _coerce_logic_flow(value) -> list[str]:
    """把 LLM 返回的 logic_flow 统一成 list[str]，避免下游按字符遍历。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        for sep in (" → ", "→", " -> ", "->", "，", ",", "、"):
            if sep in text:
                parts = [p.strip() for p in text.split(sep) if p.strip()]
                if len(parts) > 1:
                    return parts
        return [text]
    return []


class KnowledgeFuser:
    """知识融合器 — 综合意图、RAG 检索结果和参考资料生成课件指令集"""

    def __init__(self, api_key: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        prompt_path = os.path.join(PROMPT_DIR, "fusion.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.prompt_fusion = f.read()

    def fuse(self, intent: IntentResult | dict, rag_docs: list[RAGDocument],
             references: list[dict] | None = None) -> dict:
        """融合多源信息，返回 GenerationInstruction 字典"""
        if isinstance(intent, IntentResult):
            intent_dict = intent.model_dump()
        else:
            intent_dict = intent or {}
        intent_json = json.dumps(intent_dict, ensure_ascii=False)

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
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                response_format={"type": "json_object"},
                timeout=30,
            )
            result = _extract_json(resp.choices[0].message.content)
            if isinstance(result, dict) and "logic_flow" in result:
                result["logic_flow"] = _coerce_logic_flow(result["logic_flow"])
            return result
        except Exception as e:
            print(f"[fusion] LLM 调用失败: {e}")
            return {"error": str(e)}
