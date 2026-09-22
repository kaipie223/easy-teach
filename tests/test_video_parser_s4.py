"""阶段 4 回归：跨分片合并与对齐的正确性。

这些路径过去没有任何测试覆盖（video_parser 全仓只有 backend 调用入口被覆盖），
所以这里按步骤文档的完成判据逐条锁住行为。
"""

from video_parser.parser import _merge_video_understanding_results
from video_parser.schemas import (
    CandidateEvidenceInterval,
    VideoChapterCandidate,
    VideoUnderstandingResult,
)
from video_parser.video_understanding import BailianVideoConfig


def _chunk(chapter_id: str, title: str, start: float, end: float, interval_id: str):
    """一个分片的一次模型返回；编号总是从 chapter_1 / interval_1 开始。"""
    return VideoUnderstandingResult(
        status="completed",
        chapters=[
            VideoChapterCandidate(
                chapter_id=chapter_id,
                title=title,
                start_seconds=start,
                end_seconds=end,
                confidence=0.8,
                candidate_intervals=[
                    CandidateEvidenceInterval(
                        interval_id=interval_id,
                        start_seconds=start,
                        end_seconds=end,
                        confidence=0.8,
                    )
                ],
            )
        ],
    )


def test_merged_chunks_keep_unique_candidate_ids():
    """合并多个分片时，ID 必须带分片命名空间，不能原样拼接。

    每个分片是独立的模型请求，都从 chapter_1 / interval_1 开始编号；直接拼接会
    重名，下游按 candidate_id 建字典时"后写覆盖先写"，章节会被挂到另一个分片的
    时间与证据上。
    """
    merged = _merge_video_understanding_results(
        [
            _chunk("chapter_1", "第一段", 30.0, 90.0, "interval_1"),
            _chunk("chapter_1", "第二段", 1830.0, 1890.0, "interval_1"),
        ],
        2400.0,
        BailianVideoConfig(api_key="probe"),
        {},
        0,
    )
    chapter_ids = [chapter.chapter_id for chapter in merged.chapters]
    interval_ids = [
        interval.interval_id
        for chapter in merged.chapters
        for interval in chapter.candidate_intervals
    ]
    assert len(set(chapter_ids)) == len(chapter_ids)
    assert len(set(interval_ids)) == len(interval_ids)
    # 章节本身的时间范围不能被合并改写
    assert [chapter.start_seconds for chapter in merged.chapters] == [30.0, 1830.0]
