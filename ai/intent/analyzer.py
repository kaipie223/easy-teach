"""IntentAnalyzer — 教学意图理解引擎"""

import json
import os
import re

from openai import OpenAI
from backend.schemas import IntentResult, KnowledgePoint
from ai.intent.state import IntentStateMachine, State


PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
MODEL_NAME = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _parse_knowledge_points(raw_kps: list[dict]) -> list[KnowledgePoint]:
    return [
        KnowledgePoint(
            order=k.get("order", i + 1),
            title=k.get("title", ""),
            difficulty=k.get("difficulty", "basic"),
            key_points=k.get("key_points", []),
            examples=k.get("examples", []),
            estimated_minutes=k.get("estimated_minutes", 5),
        )
        for i, k in enumerate(raw_kps)
    ]


def _load_prompt(name: str) -> str:
    path = os.path.join(PROMPT_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_json(text: str) -> dict:
    """从 LLM 返回文本中提取 JSON，处理 markdown 代码块包裹"""
    text = text.strip()
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


class IntentAnalyzer:
    """教学意图分析器 — 从多轮对话中提取结构化教学意图"""

    def __init__(self, api_key: str, base_url: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.prompt_extract = _load_prompt("intent_extract.txt")
        self.prompt_follow_up = _load_prompt("follow_up.txt")
        self.prompt_confirm = _load_prompt("confirm.txt")
        self.sessions: dict[str, dict] = {}

    def _init_session(self, session_id: str):
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "messages": [],
                "state_machine": IntentStateMachine(),
                "intent_raw": {},
                "locked_intent": None,
            }

    def _format_history(self, messages: list[dict]) -> str:
        lines = []
        for m in messages:
            role = "教师" if m.get("role") == "user" else "AI助教"
            lines.append(f"{role}: {m.get('content', '')}")
        return "\n".join(lines)

    def analyze(self, session_id: str, messages: list[dict],
                uploaded_files: list[str] | None = None) -> IntentResult:
        self._init_session(session_id)
        session = self.sessions[session_id]

        session["messages"].extend(messages)
        history_text = self._format_history(session["messages"])

        try:
            raw = self._call_llm_analyze(history_text)
        except Exception as e:
            return IntentResult(
                teaching_goal="",
                missing_info=[f"LLM调用失败: {str(e)}"],
            )

        session["intent_raw"] = raw
        is_complete = raw.get("is_complete", False)
        state = session["state_machine"].transition(is_complete)

        if state == State.PROBING:
            follow_up = self._gen_follow_up(raw)
            return IntentResult(
                teaching_goal=raw.get("teaching_goal", ""),
                target_audience=raw.get("target_audience", ""),
                duration_minutes=raw.get("duration_minutes", 45),
                knowledge_points=_parse_knowledge_points(raw.get("knowledge_points", [])),
                logic_flow=raw.get("logic_flow", []),
                style_preference=raw.get("style_preference", ""),
                is_complete=False,
                missing_info=raw.get("missing_info", []),
                follow_up_question=follow_up.get("question_text", ""),
            )

        if state == State.CONFIRMING:
            confirm = self._gen_confirm(raw)
            return IntentResult(
                teaching_goal=raw.get("teaching_goal", ""),
                target_audience=raw.get("target_audience", ""),
                duration_minutes=raw.get("duration_minutes", 45),
                knowledge_points=_parse_knowledge_points(raw.get("knowledge_points", [])),
                logic_flow=raw.get("logic_flow", []),
                style_preference=raw.get("style_preference", ""),
                is_complete=True,
                missing_info=[],
                confirm_summary=confirm.get("summary", ""),
            )

        # LOCKED state — 返回锁定的意图
        locked = session.get("locked_intent", raw)
        return IntentResult(
            teaching_goal=locked.get("teaching_goal", ""),
            target_audience=locked.get("target_audience", ""),
            duration_minutes=locked.get("duration_minutes", 45),
            knowledge_points=_parse_knowledge_points(locked.get("knowledge_points", [])),
            logic_flow=locked.get("logic_flow", []),
            style_preference=locked.get("style_preference", ""),
            is_complete=True,
            missing_info=[],
        )

    def lock_intent(self, session_id: str) -> IntentResult:
        session = self.sessions.get(session_id)
        if not session:
            return IntentResult(missing_info=["会话不存在"])

        session["state_machine"].transition(is_complete=True, locked=True)
        session["locked_intent"] = session["intent_raw"]

        raw = session["locked_intent"]
        return IntentResult(
            teaching_goal=raw.get("teaching_goal", ""),
            target_audience=raw.get("target_audience", ""),
            duration_minutes=raw.get("duration_minutes", 45),
            knowledge_points=_parse_knowledge_points(raw.get("knowledge_points", [])),
            logic_flow=raw.get("logic_flow", []),
            style_preference=raw.get("style_preference", ""),
            is_complete=True,
            missing_info=[],
        )

    def update_intent(self, session_id: str, modification_text: str) -> IntentResult:
        """根据教师修改意见更新意图"""
        session = self.sessions.get(session_id)
        if not session:
            return IntentResult(missing_info=["会话不存在"])

        session["messages"].append({"role": "user", "content": f"请修改：{modification_text}"})
        return self.analyze(session_id, [])

    def _call_llm_analyze(self, history_text: str) -> dict:
        prompt = self.prompt_extract.replace("{messages}", history_text)
        resp = self.client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            timeout=30,
        )
        return _extract_json(resp.choices[0].message.content)

    def _gen_follow_up(self, intent_dict: dict) -> dict:
        missing = intent_dict.get("missing_info", [])
        intent_summary = json.dumps({
            "course_name": intent_dict.get("course_name"),
            "teaching_goal": intent_dict.get("teaching_goal"),
            "target_audience": intent_dict.get("target_audience"),
            "subject": intent_dict.get("subject"),
        }, ensure_ascii=False)

        prompt = (self.prompt_follow_up
                  .replace("{current_intent}", intent_summary)
                  .replace("{missing_info}", json.dumps(missing, ensure_ascii=False)))

        resp = self.client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            timeout=30,
        )
        return _extract_json(resp.choices[0].message.content)

    def _gen_confirm(self, intent_dict: dict) -> dict:
        intent_json = json.dumps(intent_dict, ensure_ascii=False)
        prompt = self.prompt_confirm.replace("{intent_json}", intent_json)

        resp = self.client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            timeout=30,
        )
        return _extract_json(resp.choices[0].message.content)

    def get_raw_intent(self, session_id: str) -> dict:
        """获取原始 LLM 返回的完整意图 JSON（供 fusion 模块使用）"""
        session = self.sessions.get(session_id)
        if not session:
            return {}
        return session.get("locked_intent") or session.get("intent_raw", {})

    def get_state(self, session_id: str) -> str:
        session = self.sessions.get(session_id)
        if not session:
            return "unknown"
        return session["state_machine"].state.value
