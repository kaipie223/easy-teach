"""验证意图分析链路 — 模拟完整追问-确认流程"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.intent.analyzer import IntentAnalyzer

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

if not API_KEY:
    raise RuntimeError("请在 .env 中设置 DEEPSEEK_API_KEY")

analyzer = IntentAnalyzer(api_key=API_KEY, base_url=BASE_URL)
sid = "test-session-01"

print("=== 意图分析链路测试 ===")

# 第1轮：模糊输入
print("\n--- 第1轮：模糊输入 ---")
msg = [{"role": "user", "content": "我想备一节Python列表的课"}]
result = analyzer.analyze(sid, msg)
print(f"状态: {analyzer.get_state(sid)}")
print(f"主题: {result.teaching_goal}")
print(f"is_complete: {result.is_complete}")
print(f"missing_info: {result.missing_info}")
print(f"follow_up: {result.follow_up_question}")

assert analyzer.get_state(sid) == "probing", "应处于probing状态"
assert len(result.missing_info) > 0, "missing_info应非空"
print("  ✓ 模糊输入正确触发追问")

# 第2轮：补充信息
print("\n--- 第2轮：补充信息 ---")
msg2 = [{"role": "user", "content": "给大学计算机专业大一学生上，90分钟，风格互动式"}]
result2 = analyzer.analyze(sid, msg2)
print(f"状态: {analyzer.get_state(sid)}")
print(f"missing_info: {result2.missing_info}")

# 第3轮：确认锁定
print("\n--- 第3轮：锁定意图 ---")
locked = analyzer.lock_intent(sid)
print(f"状态: {analyzer.get_state(sid)}")
print(f"锁定后 teaching_goal: {locked.teaching_goal}")
print(f"锁定后 is_complete: {locked.is_complete}")

assert analyzer.get_state(sid) == "locked", "应处于locked状态"
print("  ✓ 意图已锁定")

# 输出原始意图（用于后续 fusion）
raw = analyzer.get_raw_intent(sid)
print(f"\n完整意图 JSON: {json.dumps(raw, ensure_ascii=False, indent=2)[:500]}...")

print("\n=== 全部测试通过 ===")
