"""回归：同一项目的蓝图生成只能有一条流水线（后来的请求加入它）。

实测过：生成期间页面一刷新就又是一次 404 → 自动重新发起完整生成，于是同一项目
同时跑好几条流水线，互相抢模型额度，谁也跑不完，教师看到的就是"运行不了了"。
"""

import threading
import time

from backend.services.plan_jobs import (
    PlanJob,
    acquire_plan_job,
    release_plan_job,
)


def test_second_request_joins_instead_of_starting_another():
    job, is_owner = acquire_plan_job("p_concurrent")
    assert is_owner is True

    same_job, is_owner_again = acquire_plan_job("p_concurrent")
    assert is_owner_again is False
    assert same_job is job

    release_plan_job("p_concurrent", job)
    fresh, is_owner_after = acquire_plan_job("p_concurrent")
    assert is_owner_after is True
    assert fresh is not job
    release_plan_job("p_concurrent", fresh)


def test_late_subscriber_gets_current_progress_then_result():
    """中途进来的订阅者要立刻看到当前阶段，而不是等下一次进度。"""
    job, _ = acquire_plan_job("p_late")
    job.emit("progress", {"stage": "fill_slides", "percent": 62})

    channel = job.subscribe()
    first = channel.get_nowait()
    assert first[0] == "progress"
    assert first[1]["stage"] == "fill_slides"

    job.emit("result", {"plan_id": "plan_x"})
    while True:
        item = channel.get_nowait()
        if item is None:
            break
        if item[0] == "result":
            assert item[1]["plan_id"] == "plan_x"
            break
    job.close()
    release_plan_job("p_late", job)


def test_wait_for_result_returns_payload_for_non_streaming_clients():
    job, _ = acquire_plan_job("p_sync")
    result_holder = {}

    def waiter():
        result_holder["payload"] = job.wait_for_result()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.05)  # 确保等待者先订阅上
    job.emit("progress", {"stage": "generate"})
    job.emit("result", {"plan_id": "plan_sync"})
    thread.join(timeout=2)

    assert result_holder.get("payload") == {"plan_id": "plan_sync"}
    job.close()
    release_plan_job("p_sync", job)


def test_failure_is_propagated_to_subscribers():
    """失败要抛同一个异常给订阅方，而不是各自编文案。"""
    job, _ = acquire_plan_job("p_fail")
    error = RuntimeError("boom")
    job.fail(error)
    assert job.wait_for_result() is None
    assert job.error is error
    job.close()
    release_plan_job("p_fail", job)


def test_progress_detail_overrides_label_without_breaking_monotonic_percent():
    from backend.services.progress import PLAN_BRANCHES, PLAN_STAGES, frame

    payload = frame(
        PLAN_STAGES,
        "fill_slides",
        branches=PLAN_BRANCHES,
        detail="正在写每页要点与讲稿（4/12 页）",
    )
    assert payload["stage_label"] == "正在写每页要点与讲稿（4/12 页）"
    # 百分比仍取该阶段的里程碑值
    plain = frame(PLAN_STAGES, "fill_slides", branches=PLAN_BRANCHES)
    assert payload["percent"] == plain["percent"]
