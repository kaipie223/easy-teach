/**
 * 视觉自查脚本：登录一个临时账号，按三档视口给关键页面截图。
 *
 * 用系统自带的 Edge/Chrome 驱动（channel），不需要额外下载 Chromium。
 *   node scripts/ui-shot.mjs [页面1 页面2 ...]
 * 默认截：/login 与 /（工作台）。
 */

import { mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = resolve(HERE, '../.ui-shots')
const APP = 'http://localhost:5173'
const API = 'http://127.0.0.1:8000/api/v1'

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'phone', width: 375, height: 812 },
]

const pages = process.argv.slice(2)
const targets = pages.length ? pages : ['/login', '/']

// 支持用"已有账号"看真实数据：UI_SHOT_TOKEN + UI_SHOT_PROJECT
const ENV_TOKEN = process.env.UI_SHOT_TOKEN || ''
const ENV_PROJECT = process.env.UI_SHOT_PROJECT || ''

/** 对话页需要一个真实会话；`chat` 作为伪目标，脚本会按需创建。 */
async function createSession(auth, subject) {
  const response = await fetch(`${API}/sessions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${auth.token}`,
    },
    body: JSON.stringify({ subject, project_id: auth.projectId }),
  })
  if (!response.ok) throw new Error(`创建会话失败: ${response.status} ${await response.text()}`)
  const payload = await response.json()
  return payload.session_id
}

async function seedAccount() {
  if (ENV_TOKEN) {
    return { token: ENV_TOKEN, user: { role: 'teacher', display_name: '视觉自查' }, projectId: ENV_PROJECT }
  }

  const email = `ui-check-${Date.now()}@example.com`
  const response = await fetch(`${API}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: 'password123', display_name: '视觉自查' }),
  })
  if (!response.ok) throw new Error(`注册失败: ${response.status} ${await response.text()}`)
  const payload = await response.json()
  const headers = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${payload.access_token}`,
  }
  // 造几条示例数据，让列表页与状态样式都能被看到
  let firstProjectId = ''
  for (const title of ['显性遗传与遗传图解', '初中物理：浮力', 'Python 入门第一课']) {
    const created = await fetch(`${API}/projects`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ title, scenario: '常规课堂' }),
    })
    const project = await created.json()
    if (!firstProjectId) firstProjectId = project.project_id
  }
  return { token: payload.access_token, user: payload.user, projectId: firstProjectId }
}

async function launch() {
  for (const channel of ['msedge', 'chrome']) {
    try {
      return await chromium.launch({ channel })
    } catch (error) {
      console.warn(`channel ${channel} 不可用：${error.message.split('\n')[0]}`)
    }
  }
  return chromium.launch()
}

const auth = await seedAccount()
await mkdir(OUT_DIR, { recursive: true })

const browser = await launch()
try {
  for (const viewport of VIEWPORTS) {
    const size = { width: viewport.width, height: viewport.height }

    // 公开路由（登录页）必须用匿名上下文：带 token 会被路由守卫直接重定向走
    const anonymous = await browser.newContext({ viewport: size, deviceScaleFactor: 1, locale: 'zh-CN' })
    const authed = await browser.newContext({ viewport: size, deviceScaleFactor: 1, locale: 'zh-CN' })
    await authed.addInitScript(
      ([token, user, projectId]) => {
        localStorage.setItem('easy_teach_access_token', token)
        localStorage.setItem('easy_teach_user', JSON.stringify(user))
        // 项目内页面（资料/成果/导出）需要选中一个教案，否则会被守卫送回工作台
        if (projectId) localStorage.setItem('active_project_id', projectId)
        // 默认跳过品牌开场，避免每张截图都被它盖住
        sessionStorage.setItem('easy_teach_intro_seen', '1')
      },
      [auth.token, auth.user, auth.projectId],
    )

    // 匿名上下文：默认跳过开场，只有 intro 伪目标才让它播
    await anonymous.addInitScript(() => {
      sessionStorage.setItem('easy_teach_intro_seen', '1')
    })

    for (const target of targets) {
      const isIntro = target === 'intro'
      const isPublic = target === '/login'
      const isChat = target === 'chat'
      const path = isChat ? `/chat/${await createSession(auth, '课堂对话演示')}` : isPublic || isIntro ? '/login' : target

      const page = await (isPublic || isIntro ? anonymous : authed).newPage()
      if (isIntro) {
        // 清掉"已看过"标记，让开场动画真的播放
        await page.addInitScript(() => sessionStorage.removeItem('easy_teach_intro_seen'))
      }
      await page.goto(`${APP}${path}`, { waitUntil: 'load' })
      // 对话页首屏会触发 AI 开场；开场动画等它进入播放中段
      await page.waitForTimeout(isChat ? 9000 : isIntro ? 3200 : 800)
      // 等正文真的渲染出来再截：批量截图时出现过两种假象——"只截到顶栏"（还没绘制）
      // 和"截到骨架屏"（数据还没回来），所以这里要求骨架消失且正文就绪。
      await page
        .waitForFunction(
          (intro) => {
            if (intro) return Boolean(document.querySelector('.intro'))
            const main = document.querySelector('#main-content')
            if (!main || main.innerText.trim().length <= 10) return false
            return !document.querySelector('.el-skeleton')
          },
          isIntro,
          { timeout: 12000 },
        )
        .catch(() => {})
      await page.waitForTimeout(400)

      const label = isIntro
        ? 'intro'
        : isChat
          ? 'chat'
          : target === '/'
            ? 'dashboard'
            : target.replace(/\//g, '_').replace(/^_/, '')
      const file = resolve(OUT_DIR, `${label}-${viewport.name}.png`)
      await page.screenshot({ path: file, fullPage: false })
      console.log(`已截图 ${file}`)
      await page.close()
    }

    await anonymous.close()
    await authed.close()
  }
} finally {
  await browser.close()
}
