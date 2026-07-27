import os
import base64
import requests


def parse_image(file_path: str) -> dict:
    """
    【M2模块 - 视觉大模型接口】
    功能：接收本地的 .png 文件路径，转换为 Base64 并请求 DeepSeek-VL。
    """
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"找不到图片文件: {file_path}", "data": ""}

    try:
        with open(file_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxx"  # 换成真实的 API Key

        # 如果没有 API Key，暂时返回一个模拟结果证明流程通了
        if API_KEY == "sk-xxxxxxxxxxxxxxxxxxxxx":
            return {"status": "success", "message": "跳过大模型",
                    "data": f"[图片 {os.path.basename(file_path)} 的 AI 识别描述]"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"
        }

        payload = {
            "model": "deepseek-vl",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "提取图片中的文字内容及描述。"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                    ]
                }
            ]
        }

        response = requests.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=payload)

        if response.status_code == 200:
            result_text = response.json()["choices"][0]["message"]["content"]
            return {"status": "success", "message": "识别成功", "data": result_text}
        else:
            return {"status": "error", "message": f"接口报错: {response.text}", "data": ""}

    except Exception as e:
        return {"status": "error", "message": f"图片解析异常: {str(e)}", "data": ""}