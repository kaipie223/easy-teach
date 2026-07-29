"""验证 DeepSeek API 可调用"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

if not API_KEY:
    raise RuntimeError("请在 .env 中设置 DEEPSEEK_API_KEY")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "请用一句话解释什么是面向对象编程"}],
    max_tokens=200,
)

print("=== DeepSeek API 测试 ===")
print(f"模型: {response.model}")
print(f"回复: {response.choices[0].message.content}")
print("=== 测试通过 ===")
