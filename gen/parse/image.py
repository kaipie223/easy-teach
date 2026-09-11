import os


def parse_image(file_path: str) -> dict:
    """Legacy adapter that fails explicitly while vision support is disabled."""
    if not os.path.exists(file_path):
        return {
            "status": "error",
            "code": "IMAGE_FILE_NOT_FOUND",
            "message": f"找不到图片文件: {file_path}",
            "data": "",
        }
    return {
        "status": "error",
        "code": "VISION_MODEL_NOT_CONFIGURED",
        "message": "视觉识别模型尚未配置，未执行图片识别",
        "data": "",
    }


def parse_image_from_bytes(_content: bytes) -> dict:
    return {
        "status": "error",
        "code": "VISION_MODEL_NOT_CONFIGURED",
        "message": "视觉识别模型尚未配置，未执行图片识别",
        "data": "",
    }
