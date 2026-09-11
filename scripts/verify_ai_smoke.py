"""Run one bounded real-model blueprint smoke test without printing content or secrets."""

from __future__ import annotations

import json

from backend.services.courseware_ai import generate_courseware_spec
from backend.services.quality import inspect_courseware


def main() -> None:
    brief = {
        "teaching_goal": "解释网络分层思想，并能根据时序图判断 TCP 三次握手报文的作用",
        "target_audience": "高一学生",
        "duration_minutes": 45,
        "knowledge_points": [
            {
                "order": 1,
                "title": "网络分层",
                "difficulty": "basic",
                "key_points": ["分层思想", "协议职责"],
                "estimated_minutes": 8,
            },
            {
                "order": 2,
                "title": "TCP连接建立",
                "difficulty": "intermediate",
                "key_points": ["SYN", "SYN-ACK", "ACK"],
                "estimated_minutes": 15,
            },
            {
                "order": 3,
                "title": "序列号与确认号",
                "difficulty": "advanced",
                "key_points": ["序列号", "确认号"],
                "estimated_minutes": 12,
            },
            {
                "order": 4,
                "title": "常见错误诊断",
                "difficulty": "intermediate",
                "key_points": ["时序判断", "故障定位"],
                "estimated_minutes": 10,
            },
        ],
        "logic_flow": ["情境导入", "分层建模", "时序推演", "故障判断", "总结评价"],
        "teaching_focus": "三次报文的因果关系",
        "teaching_difficulties": "序列号与确认号",
        "output_types": ["pptx", "docx", "pdf", "html"],
        "interaction_ideas": "时序排序与选择题，提交后展示答案解析",
        "style_preference": "图示讲解",
        "homework_type": "根据新时序图判断连接建立问题。",
    }
    result = generate_courseware_spec(brief, [])
    quality = inspect_courseware(result.spec)
    print(
        json.dumps(
            {
                "model": result.model_name,
                "prompt_version": result.prompt_version,
                "slides": len(result.spec.slides),
                "lesson_sections": len(result.spec.lesson_sections),
                "interactions": len(result.spec.interactions),
                "duration_minutes": sum(
                    section.duration_minutes for section in result.spec.lesson_sections
                ),
                "quality_status": quality["status"],
                "usage": result.usage,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
