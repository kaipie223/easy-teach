"""幻灯片主题下发：应用内预览与导出的 PPTX 必须画的是同一页。

导出的 PPTX 由主题层（services/slide_theme.py）渲染；应用内的幻灯片预览
（SlidePreview.vue）用 CSS 变量画。两边各存一份色值**必然**漂移 —— 实测就是这样：
后端换成了学术蓝，前端那份变量还停在旧的亮蓝，同一页在预览里和导出里不是一个颜色。

所以主题只存在于后端这一处，前端启动时把这个接口给的值写成 CSS 变量。

无需鉴权：它只是一组颜色与字号，登录页也用得上，且不含任何账号信息。
"""

from fastapi import APIRouter

from backend.services.slide_theme import DEFAULT_THEME, css_variables

router = APIRouter()


@router.get("/slide-theme")
def get_slide_theme() -> dict[str, object]:
    """当前幻灯片主题：名字 + 前端要注入的 CSS 变量。"""
    return {
        "theme": DEFAULT_THEME.name,
        "variables": css_variables(),
    }
