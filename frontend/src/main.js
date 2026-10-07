import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
// 引入顺序不可改：Element Plus 默认样式必须在最前，否则我们的覆写会被它盖回去
// （这正是过去"双蓝冲突"的成因）。
import 'element-plus/dist/index.css'
import './styles/tokens.css'
import './styles/element-overrides.css'
import './styles/base.css'
import './styles/motion.css'
import './styles/workspace.css'

import { fetchSlideTheme } from './api'
import App from './App.vue'
import router from './router'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)
app.use(ElementPlus)

/*
 * 这里刻意不做图标的全局注册。
 *
 * 以前是 `for (const [key, component] of Object.entries(ElementPlusIconsVue))`，
 * 把 293 个图标全注册进主包；而审计（node scripts/audit-icons.mjs）显示项目实际只用到
 * 34 个，且每一个都在自己的文件里按需 import —— 也就是说那 293 个图标一个都没被真正
 * 依赖，纯粹是首屏体积。
 *
 * 新图标一律在用到它的文件里 import：
 *   import { Search } from '@element-plus/icons-vue'
 * 如果哪天确实需要全局注册（例如图标名来自后端字符串），只注册用到的那几个，
 * 不要再整包注册。
 */

/*
 * 幻灯片主题由后端下发，启动时写成 CSS 变量。
 *
 * 为什么不在 tokens.css 里手写：应用内的幻灯片预览（SlidePreview.vue）用
 * var(--slide-*) 画，导出的 PPTX 由后端主题层（services/slide_theme.py）渲染 ——
 * 两份色值各写一遍必然漂移，实测就是这样：后端换成了学术蓝，前端那份还停在旧亮蓝，
 * 同一页在预览里和导出里不是一个颜色。注入之后主题只存在于后端一处。
 *
 * 不 await：预览是用户后来才打开的，不值得为它拖慢启动。取不到就沿用 tokens.css
 * 的兜底值（那份值与主题一致，有契约测试守着）。
 */
async function applySlideTheme() {
  try {
    const response = await fetchSlideTheme()
    const variables = response.data?.variables || {}
    for (const [name, value] of Object.entries(variables)) {
      if (name && value) document.documentElement.style.setProperty(name, value)
    }
  } catch (error) {
    console.warn('幻灯片主题下发失败，沿用内置兜底值：', error?.message || error)
  }
}

applySlideTheme()

window.addEventListener('easy-teach-auth-expired', () => {
  const authStore = pinia._s.get('auth')
  authStore?.logout()
  if (router.currentRoute.value.name !== 'login') {
    router.replace({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

app.mount('#app')
