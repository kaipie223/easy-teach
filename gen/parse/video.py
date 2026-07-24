import os
import cv2
from moviepy.editor import VideoFileClip  # 负责剥离声音的工具

# 导入你的两个左膀右臂
from gen.parse.image import parse_image_from_bytes
from gen.parse.audio import parse_audio


def parse_video_summary(file_path: str, num_frames: int = 3) -> dict:
    """
    【M2模块 - 视频双轨解析总调度引擎】
    功能：同时剥离视频画面和声音，分别交给视觉和语音大模型，最后汇总输出。
    """
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"找不到视频文件: {file_path}", "data": ""}

    try:
        audio_text = "【未检测到语音】"
        frame_descriptions = []

        # ==================== 第一轨：提取声音 ====================
        try:
            # 加载视频
            clip = VideoFileClip(file_path)
            # 如果视频里真的有声音
            if clip.audio is not None:
                temp_audio_path = "temp_audio.wav"
                # 把声音单独抽出来，存成一个临时的音频文件 (logger=None 是为了不让控制台打印多余的进度条)
                clip.audio.write_audiofile(temp_audio_path, logger=None)

                # 把音频文件交给你的 audio.py 去听
                audio_result = parse_audio(temp_audio_path)
                if audio_result["status"] == "success":
                    audio_text = audio_result["data"]

                # 【强迫症清理】听完之后，立刻把临时录音文件删掉，保持系统干净！
                if os.path.exists(temp_audio_path):
                    os.remove(temp_audio_path)

            clip.close()
        except Exception as e:
            audio_text = f"【声音提取失败: {str(e)}】"

        # ==================== 第二轨：提取画面 ====================
        video = cv2.VideoCapture(file_path)
        total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))

        if total_frames > 0:
            step = max(1, total_frames // num_frames)
            frame_indices = [i * step for i in range(num_frames)]
            if frame_indices[-1] >= total_frames:
                frame_indices[-1] = total_frames - 1

            for idx in frame_indices:
                video.set(cv2.CAP_PROP_POS_FRAMES, idx)
                success, frame = video.read()

                if success:
                    # 内存里转码图片，直接抛给 image.py
                    _, buffer = cv2.imencode('.jpg', frame)
                    image_bytes = buffer.tobytes()

                    ai_result = parse_image_from_bytes(image_bytes)
                    if ai_result["status"] == "success":
                        frame_descriptions.append(f"- 画面 {idx}：{ai_result['data']}")
                    else:
                        frame_descriptions.append(f"- 画面 {idx}：[视觉识别失败]")
        video.release()

        # ==================== 终极汇总 ====================
        final_summary = (
                f"【视频智能双轨解析报告】\n\n"
                f"🗣️ [教师语音内容]:\n{audio_text}\n\n"
                f"📺 [视频画面摘要] (共提取 {len(frame_descriptions)} 张关键帧):\n"
                + "\n".join(frame_descriptions)
        )

        return {
            "status": "success",
            "message": "音视频双轨解析完成",
            "data": final_summary
        }

    except Exception as e:
        return {"status": "error", "message": f"视频调度发生异常: {str(e)}", "data": ""}