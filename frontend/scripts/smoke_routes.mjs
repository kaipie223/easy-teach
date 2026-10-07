/** 全路由运行时冒烟：逐页访问，收集控制台与页面异常。 */

import { chromium } from 'playwright'

const token = process.env.UI_SHOT_TOKEN || ''
const projectId = process.env.UI_SHOT_PROJECT || ''
// 默认测 dev；用 SMOKE_BASE 指向 preview（42173/4173）就能测生产构建
const BASE = process.env.SMOKE_BASE || 'http://localhost:5173'
const routes = [
  ['/', '工作台'],
  ['/home', '新建教案'],
  ['/chat', '对话'],
  ['/blueprint', '教学蓝图'],
  ['/editor', '成果编辑'],
  ['/materials', '资料中心'],
  ['/knowledge', '知识库'],
  ['/exports', '导出与版本'],
]

const browser = await chromium.launch({ channel: 'msedge' })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await context.addInitScript(
  ([t, p]) => {
    localStorage.setItem('easy_teach_access_token', t)
    localStorage.setItem('easy_teach_user', JSON.stringify({ role: 'teacher', display_name: '自查' }))
    if (p) localStorage.setItem('active_project_id', p)
    sessionStorage.setItem('easy_teach_intro_seen', '1')
  },
  [token, projectId],
)
const page = await context.newPage()
const problems = []
page.on('pageerror', (error) => problems.push(`[pageerror] ${String(error).slice(0, 160)}`))
page.on('console', (message) => {
  if (message.type() === 'error') {
    const text = message.text()
    if (!text.includes('favicon') && !text.includes('Failed to load resource')) {
      problems.push(`[console] ${text.slice(0, 160)}`)
    }
  }
})

for (const [path, name] of routes) {
  const before = problems.length
  await page.goto(`${BASE}${path}`, { waitUntil: 'load' })
  await page.waitForTimeout(1600)
  const heading = await page.locator('h1').first().textContent().catch(() => '')
  const empty = await page.locator('.el-empty').count()
  const skeletons = await page.locator('.el-skeleton').count()
  console.log(
    `${path.padEnd(12)} ${String(name).padEnd(6)} | h1: ${(heading || '—').trim().slice(0, 16).padEnd(16)} | ` +
      `空态 ${empty} | 骨架 ${skeletons} | 新增错误 ${problems.length - before}`,
  )
}

console.log('\n问题明细:')
if (!problems.length) console.log('  无')
else [...new Set(problems)].forEach((item) => console.log(`  ${item}`))
await browser.close()
