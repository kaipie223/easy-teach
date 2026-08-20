import { expect, test } from '@playwright/test'

test('teacher can register, create a project, and enter course chat', async ({ page }) => {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`
  const email = `playwright-${suffix}@example.com`
  let projectId = null
  let token = null

  try {
    await page.goto('/login')
    await page.getByRole('button', { name: '还没有账号？注册一个' }).click()
    await page.getByPlaceholder('例如：张老师').fill('Playwright 教师')
    await page.getByPlaceholder('teacher@example.com').fill(email)
    await page.getByPlaceholder('至少 8 个字符').fill('password123')
    await page.getByRole('button', { name: '注册并进入' }).click()

    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
    token = await page.evaluate(() => localStorage.getItem('easy_teach_access_token'))
    await page.getByRole('button', { name: '新建项目' }).click()
    await expect(page).toHaveURL(/\/home$/)

    const projectResponse = page.waitForResponse(
      (response) => response.url().includes('/api/v1/projects') && response.request().method() === 'POST',
    )
    await page.getByPlaceholder(/输入课程名称/).fill('Playwright 课程')
    await page.getByRole('button', { name: '开始创建' }).click()
    projectId = (await (await projectResponse).json()).project_id

    await expect(page).toHaveURL(/\/chat\/s_[a-f0-9]+$/)
    await expect(page.getByRole('main').getByText('课程对话', { exact: true })).toBeVisible()
    await page.screenshot({ path: 'test-results/main-flow.png', fullPage: true })
  } finally {
    if (projectId && token) {
      await page.request.delete(`${process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'}/api/v1/projects/${projectId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    }
  }
})
