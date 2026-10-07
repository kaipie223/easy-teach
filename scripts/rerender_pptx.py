"""临时：用数据库里的真实蓝图重渲染 PPTX（不调用模型），供前后对比。"""

import os
import sys

# 从 scripts/ 直接运行时也要能 import backend：把仓库根加入模块搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pptx import Presentation

from backend.config import settings
from backend.db.database import SessionLocal
from backend.models.courseware import CoursewarePlan
from backend.services.courseware import to_info
from backend.services.generator import generate_pptx

keyword = sys.argv[1] if len(sys.argv) > 1 else "浮力"
db = SessionLocal()
try:
    plans = (
        db.query(CoursewarePlan)
        .filter(CoursewarePlan.title.like(f"%{keyword}%"))
        .order_by(CoursewarePlan.created_at.desc())
        .all()
    )
    if not plans:
        print("找不到包含该关键词的蓝图")
        raise SystemExit(1)
    plan = plans[0]
    # to_info 返回的是"记录 + content"两层结构；渲染器要的是 content 这一层
    payload = to_info(plan).content.model_dump(mode="json")
    print(f"蓝图: {plan.title[:40]} | {plan.plan_id}")
    path = generate_pptx(payload, output_name=f"quality_after_{keyword}.pptx")
    presentation = Presentation(path)
    print(f"重渲染 -> {path}")
    print(f"页数: {len(presentation.slides)}")
finally:
    db.close()
