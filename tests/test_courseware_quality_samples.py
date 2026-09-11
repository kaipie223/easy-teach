import pytest

from backend.models.brief import TeachingBrief
from backend.services.courseware import compile_plan_content
from backend.services.quality import inspect_courseware


COURSE_SAMPLES = [
    ("浮力及其应用", "初二学生", ["浮力概念", "阿基米德原理"], "影响浮力大小的因素", "排开液体体积的理解", "实验探究"),
    ("TCP 三次握手", "大一新生", ["SYN 报文", "确认机制"], "报文交换顺序", "序列号与确认号", "案例驱动"),
    ("勾股定理", "初二学生", ["直角三角形", "勾股关系"], "定理应用", "逆定理判断", "互动探究"),
    ("赤壁赋意象赏析", "高中二年级", ["主客问答", "水月意象"], "情感变化", "哲理意蕴", "严谨学术"),
    ("光合作用", "高中一年级", ["光反应", "暗反应"], "物质与能量变化", "反应条件辨析", "图示讲解"),
    ("分数的初步认识", "小学三年级", ["平均分", "分数表示"], "分数含义", "单位一理解", "生活情境"),
    ("工业革命", "初中二年级", ["蒸汽动力", "社会变迁"], "技术与社会关系", "因果链分析", "史料探究"),
    ("牛顿第二定律", "高中一年级", ["合力", "加速度"], "公式条件", "矢量方向", "实验探究"),
    ("数据库事务", "高职二年级", ["ACID", "隔离级别"], "一致性保障", "并发现象判断", "案例驱动"),
    ("英语一般过去时", "初中一年级", ["规则变化", "时间标志"], "句型运用", "不规则动词", "任务教学"),
    ("垃圾分类", "小学五年级", ["分类标准", "资源回收"], "正确投放", "易混垃圾辨析", "项目式学习"),
    ("概率初步", "高中二年级", ["随机事件", "古典概型"], "概率计算", "样本空间列举", "问题驱动"),
    ("细胞有丝分裂", "高中一年级", ["染色体变化", "细胞周期"], "各时期特征", "图像识别", "图示讲解"),
    ("供应链牛鞭效应", "本科三年级", ["需求波动", "信息共享"], "放大机制", "多层级因果关系", "案例驱动"),
    ("幼儿颜色认知", "幼儿园中班", ["三原色", "颜色配对"], "颜色辨认", "控制无关刺激", "低刺激互动"),
    ("Python 循环结构", "高中一年级", ["for 循环", "while 循环"], "循环控制", "边界条件", "代码实践"),
    ("化学平衡移动", "高中二年级", ["勒夏特列原理", "影响因素"], "条件变化判断", "速率与平衡混淆", "实验探究"),
    ("市场供求均衡", "本科一年级", ["需求曲线", "供给曲线"], "均衡价格", "曲线移动与点移动", "案例驱动"),
    ("网络信息安全", "初中一年级", ["密码安全", "钓鱼识别"], "风险判断", "社会工程识别", "情境模拟"),
    ("音乐节奏型", "小学四年级", ["四分音符", "八分音符"], "节奏组合", "稳定拍点", "听唱互动"),
]


@pytest.mark.parametrize(
    ("title", "audience", "points", "focus", "difficulties", "style"),
    COURSE_SAMPLES,
)
def test_template_blueprint_quality_across_twenty_courses(
    title,
    audience,
    points,
    focus,
    difficulties,
    style,
):
    brief = TeachingBrief(
        content_json={
            "teaching_goal": title,
            "target_audience": audience,
            "duration_minutes": 45,
            "knowledge_points": [
                {
                    "order": index,
                    "title": point,
                    "difficulty": "intermediate",
                    "key_points": [f"理解{point}", f"应用{point}"],
                    "estimated_minutes": 15,
                }
                for index, point in enumerate(points, start=1)
            ],
            "logic_flow": ["情境导入", "概念建构", "应用练习", "总结迁移"],
            "teaching_focus": focus,
            "teaching_difficulties": difficulties,
            "output_types": ["pptx", "docx", "pdf", "html"],
            "interaction_ideas": f"围绕{focus}完成分类、排序或判断",
            "style_preference": style,
            "homework_type": f"用新情境解释{title}中的关键关系。",
        }
    )

    spec = compile_plan_content(brief, [])
    report = inspect_courseware(spec)

    assert report["status"] != "failed", report
    assert spec.title == title
    assert sum(section.duration_minutes for section in spec.lesson_sections) == 45
    assert 6 <= len(spec.slides) <= 16
    assert spec.output_specs.pptx.narrative_arc
    assert spec.output_specs.docx.teacher_preparation
    assert spec.output_specs.docx.homework
    assert spec.output_specs.pdf.printable_summary
    assert spec.output_specs.pdf.assessment_checklist
    assert spec.output_specs.html.interaction_ids == [spec.interactions[0].interaction_id]
