import base64
import requests


def parse_image_from_bytes(image_bytes: bytes) -> dict:
    """
    【M2模块 - DeepSeek-VL 真实多模态图片解析引擎】
    功能：直接在内存中接收 PDF 传来的扫描版图片，将其发给 DeepSeek-VL 提取文字和排版。
    """
    try:
        # 1. 核心转换：把二进制图片流，转成 DeepSeek 认识的 Base64 编码
        base64_image = base64.b64encode(image_bytes).decode('utf-8')

        # ==========================================
        # 2. 配置 DeepSeek 接口信息
        # 【重要】你需要把下面这行的 "" 里面，换成你们团队申请的真实 DeepSeek API Key
        API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxx"
        # ==========================================

        # 如果你们还没申请 API Key，为了防止代码报错，可以先加个判断：
        if API_KEY == "sk-xxxxxxxxxxxxxxxxxxxxx":
            return {"status": "success", "message": "跳过大模型",
                    "data": "【系统提示】这里是一张扫描图片，请填入真实的 API Key 后解锁 DeepSeek 识别功能。"}

        # 3. 按照 DeepSeek 官方要求，组装网络请求
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}"
        }

        # 告诉 DeepSeek 它要做什么（Prompt 提示词）
        payload = {
            "model": "deepseek-vl",  # 指定使用的是视觉模型
            "messages": [
                {
                    "role": "user",
                    "content": [
                        # 核心指令：要求大模型把图片里的字抠出来
                        {"type": "text",
                         "text": "你是一个教学内容解析助手。请精准提取这张图片中的所有文字内容，如果有表格或者公式，请用 Markdown 格式清晰地描述出来。"},
                        # 把转码后的图片喂给大模型
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                    ]
                }
            ]
        }

        # 4. 发射！将数据通过网络发送给 DeepSeek 服务器
        # 注意：你需要确保你的终端安装了 requests 库 (如果没有，在终端运行 pip install requests)
        response = requests.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=payload)

        # 5. 接收并拆解 DeepSeek 返回的快递包裹
        if response.status_code == 200:
            # 解析成功，把深藏在层层数据里的最终文字提炼出来
            result_text = response.json()["choices"][0]["message"]["content"]
            return {"status": "success", "message": "DeepSeek-VL 识别成功", "data": result_text}
        else:
            # 如果出错了（比如 API Key 不对，或者欠费了），把报错信息原样打印出来方便排查
            return {"status": "error", "message": f"DeepSeek 接口报错: {response.text}", "data": ""}

    except Exception as e:
        return {"status": "error", "message": f"连接多模态大模型时发生异常: {str(e)}", "data": ""}