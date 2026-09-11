import { expect, test } from '@playwright/test'

test('teacher can register, create a project, and enter course chat', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 10000)}`
  const email = `playwright-${suffix}@example.com`
  let projectId = null
  let token = null
  const consoleErrors = []
  const bootstrappedSessions = new Set()
  let startRequests = 0
  const openingText = `我已经收到“Playwright 课程”这个主题。${'接下来会逐项确认教学需求。\n'.repeat(70)}`
  const openingBrief = {
    brief_id: 'brief-playwright-opening',
    project_id: null,
    status: 'draft',
    version: 1,
    teaching_goal: '围绕课程主题建立清晰的概念理解与实践能力',
    target_audience: '',
    duration_minutes: 45,
    knowledge_points: [
      { order: 1, title: '核心概念', difficulty: 'basic', key_points: [], estimated_minutes: 15 },
      { order: 2, title: '实践应用', difficulty: 'intermediate', key_points: [], estimated_minutes: 20 },
    ],
    logic_flow: ['问题导入', '概念讲解', '实践练习', '总结反馈'],
    teaching_focus: '核心概念与实际任务之间的联系',
    teaching_difficulties: '把抽象知识迁移到具体场景',
    output_types: ['pptx', 'docx', 'html'],
    interaction_ideas: '通过分组任务和即时反馈检查理解',
    missing_info: ['target_audience'],
    is_complete: false,
  }
  const confirmData = {
    fields: {
      topic: '线性回归',
      audience: '大三人工智能专业学生',
      duration: '60 分钟',
      core_knowledge: '最小二乘法、梯度下降、模型评估',
      logic_flow: '问题导入 → 数学推导 → 编程实践 → 总结',
      teaching_focus: '掌握线性回归的数学原理与训练流程',
      teaching_difficulties: '理解梯度下降的参数更新逻辑',
      output_types: 'pptx、docx、pdf、html',
      interaction_ideas: '手动计算一步梯度更新并与代码结果比较',
      style: '案例驱动',
    },
    note: '请确认教学信息。',
  }

  await page.route(/\/api\/v1\/sessions\/(s_[^/]+)$/, async (route) => {
    if (route.request().method() !== 'GET') return route.continue()
    const sessionId = route.request().url().match(/\/sessions\/(s_[^/]+)$/)?.[1]
    const response = await route.fetch()
    const payload = await response.json()
    if (sessionId && bootstrappedSessions.has(sessionId) && !payload.messages?.length) {
      payload.messages = [
        {
          id: `msg-opening-${sessionId}`,
          role: 'assistant',
          content: openingText,
          msg_type: 'text',
          event_data: null,
          created_at: new Date().toISOString(),
        },
        {
          id: `msg-question-${sessionId}`,
          role: 'assistant',
          content: '请先确认授课对象。',
          msg_type: 'question',
          event_data: {
            prompt: '请先确认授课对象。',
            missing_info: ['target_audience'],
            options: ['小学', '初中', '高中', '大学'],
            allow_free: true,
            brief: openingBrief,
          },
          created_at: new Date().toISOString(),
        },
        {
          id: `msg-confirm-${sessionId}`,
          role: 'assistant',
          content: '',
          msg_type: 'confirm',
          event_data: confirmData,
          created_at: new Date().toISOString(),
        },
      ]
    }
    await route.fulfill({ response, json: payload })
  })

  await page.route(/\/api\/v1\/sessions\/(s_[^/]+)\/start$/, async (route) => {
    startRequests += 1
    const sessionId = route.request().url().match(/\/sessions\/(s_[^/]+)\/start$/)?.[1]
    if (sessionId) bootstrappedSessions.add(sessionId)
    const textEvent = JSON.stringify({ event_type: 'text', content: openingText, data: null })
    const questionEvent = JSON.stringify({
      event_type: 'question',
      content: '请先确认授课对象。',
      data: {
        prompt: '请先确认授课对象。',
        missing_info: ['target_audience'],
        options: ['小学', '初中', '高中', '大学'],
        allow_free: true,
        brief: openingBrief,
      },
    })
    const confirmEvent = JSON.stringify({ event_type: 'confirm', content: '', data: confirmData })
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: `event: text\ndata: ${textEvent}\n\nevent: question\ndata: ${questionEvent}\n\nevent: confirm\ndata: ${confirmEvent}\n\n`,
    })
  })

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  try {
    await page.goto('/login')
    await page.getByRole('button', { name: '还没有账号？注册一个' }).click()
    await page.getByPlaceholder('例如：张老师').fill('Playwright 教师')
    await page.getByPlaceholder('teacher@example.com').fill(email)
    await page.getByPlaceholder('至少 8 个字符').fill('password123')
    await page.getByRole('button', { name: '注册并进入' }).click()

    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: '工作台' })).toBeVisible()
    await expect(page.getByRole('button', { name: '通知' })).toHaveCount(0)
    await expect(page.getByText('已自动保存', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('link', { name: '我的知识库' })).toBeVisible()
    await expect(page.getByRole('link', { name: '开始新课' })).toHaveCount(0)
    await page.goto('/knowledge')
    await expect(page).toHaveURL(/\/knowledge$/)
    await expect(page.getByRole('heading', { name: '我的知识库' })).toBeVisible()
    await page.goto('/admin')
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('link', { name: '管理员工作台' })).not.toBeVisible()
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
    await expect(page.getByText(/我已经收到“Playwright 课程”这个主题/)).toBeVisible()
    await expect(page.getByText('请先确认授课对象。', { exact: true })).toBeVisible()
    const confirmPanel = page.locator('.confirm-panel')
    await expect(confirmPanel.getByText('教学流程', { exact: true })).toBeVisible()
    await expect(confirmPanel.getByText('教学重点', { exact: true })).toBeVisible()
    await expect(confirmPanel.getByText('教学难点', { exact: true })).toBeVisible()
    await expect(confirmPanel.getByText('输出形式', { exact: true })).toBeVisible()
    await expect(confirmPanel.getByText('互动设计', { exact: true })).toBeVisible()
    await expect(confirmPanel.getByText('教学风格', { exact: true })).toBeVisible()
    await expect(confirmPanel.getByText('PPT 课件、Word 教案、PDF 打印版、互动 HTML', { exact: true })).toBeVisible()
    await expect(confirmPanel.getByText('logic_flow', { exact: true })).toHaveCount(0)
    const confirmRowsDoNotOverlap = await page.locator('.cp-field').evaluateAll(rows => rows.every(row => {
      const label = row.querySelector('.cp-label')?.getBoundingClientRect()
      const value = row.querySelector('.cp-value')?.getBoundingClientRect()
      return label && value && label.right <= value.left
    }))
    expect(confirmRowsDoNotOverlap).toBeTruthy()
    expect(startRequests).toBe(1)
    const chatUrl = page.url()

    const scrollMetrics = await page.evaluate(() => {
      const messages = document.querySelector('.chat-messages')
      const brief = document.querySelector('.brief-panel')
      const sidebar = document.querySelector('.sidebar-nav')
      const before = {
        messagesTop: messages.getBoundingClientRect().top,
        briefTop: brief.getBoundingClientRect().top,
      }
      messages.scrollTop = messages.scrollHeight
      brief.scrollTop = brief.scrollHeight
      return {
        bodyFitsViewport: document.scrollingElement.scrollHeight <= window.innerHeight,
        sidebarOverflow: getComputedStyle(sidebar).overflow,
        messagesOverflowY: getComputedStyle(messages).overflowY,
        briefOverflowY: getComputedStyle(brief).overflowY,
        messagesScrolled: messages.scrollTop > 0,
        messagesPositionStable: messages.getBoundingClientRect().top === before.messagesTop,
        briefPositionStable: brief.getBoundingClientRect().top === before.briefTop,
      }
    })
    expect(scrollMetrics).toEqual({
      bodyFitsViewport: true,
      sidebarOverflow: 'hidden',
      messagesOverflowY: 'auto',
      briefOverflowY: 'auto',
      messagesScrolled: true,
      messagesPositionStable: true,
      briefPositionStable: true,
    })

    await page.setViewportSize({ width: 1024, height: 768 })
    const compactScrollMetrics = await page.evaluate(() => {
      const messages = document.querySelector('.chat-messages')
      const brief = document.querySelector('.brief-panel')
      messages.scrollTop = messages.scrollHeight
      brief.scrollTop = brief.scrollHeight
      return {
        bodyFitsViewport: document.scrollingElement.scrollHeight <= window.innerHeight,
        messagesScrolled: messages.scrollTop > 0,
        briefScrolled: brief.scrollTop > 0,
      }
    })
    expect(compactScrollMetrics).toEqual({
      bodyFitsViewport: true,
      messagesScrolled: true,
      briefScrolled: true,
    })
    await page.setViewportSize({ width: 1440, height: 900 })

    await page.reload()
    await expect(page.getByText(/我已经收到“Playwright 课程”这个主题/)).toBeVisible()
    expect(startRequests).toBe(1)

    await page.route(new RegExp(`/api/v1/projects/${projectId}/materials$`), async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{
          material_id: 'm_e2e_material',
          original_name: '机器学习考点详解.pdf',
          file_type: 'pdf',
          size_bytes: 4096,
          status: 'ready',
          created_at: new Date().toISOString(),
        }]),
      })
    })
    await page.route(/\/api\/v1\/materials\/m_e2e_material\/bindings$/, async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    })
    await page.route(/\/api\/v1\/materials\/m_e2e_material\/evidence$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { evidence_id: 'e_e2e_1', text: '线性回归通过最小二乘法拟合连续目标。', locator_json: { page: 1 } },
          { evidence_id: 'e_e2e_2', text: '梯度下降根据损失函数梯度迭代更新参数。', locator_json: { page: 2 } },
        ]),
      })
    })

    await page.goto('/materials')
    await expect(page.getByRole('heading', { name: '资料中心' })).toBeVisible()
    await expect(page.locator('#materials-project')).toHaveCount(0)
    await expect(page.locator('.top-bar').getByText('Playwright 课程', { exact: true })).toBeVisible()
    await expect(page.getByText(/视觉与视频解析暂未启用/)).toBeVisible()
    await expect(page.locator('.evidence-list .evidence-card')).toHaveCount(2)
    const evidenceRows = await page.locator('.evidence-list .evidence-card').evaluateAll(cards => cards.map(card => {
      const rect = card.getBoundingClientRect()
      return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width) }
    }))
    expect(evidenceRows[1].y).toBeGreaterThan(evidenceRows[0].y)
    expect(evidenceRows[1].x).toBe(evidenceRows[0].x)
    expect(evidenceRows[1].width).toBe(evidenceRows[0].width)
    const acceptedTypes = await page.locator('input[type="file"]').getAttribute('accept')
    expect(acceptedTypes).not.toContain('.mp4')
    await page.goto('/requirements')
    await expect(page).toHaveURL(chatUrl)
    await expect(page.getByText('课程主题', { exact: true })).not.toBeVisible()

    const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'
    const authHeaders = { Authorization: `Bearer ${token}` }
    const briefResponse = await page.request.patch(`${baseURL}/api/v1/projects/${projectId}/brief`, {
      headers: authHeaders,
      data: {
        teaching_goal: '理解 TCP 三次握手并解释每次报文的作用',
        target_audience: '大一新生',
        duration_minutes: 45,
        knowledge_points: [
          { order: 1, title: '连接建立过程', difficulty: 'basic', key_points: ['SYN', 'SYN-ACK', 'ACK'], estimated_minutes: 20 },
          { order: 2, title: '确认机制', difficulty: 'intermediate', key_points: ['序列号', '确认号'], estimated_minutes: 15 },
        ],
        logic_flow: ['问题导入', '过程讲解', '例题练习', '总结'],
        teaching_focus: '三次报文交换的时序和作用',
        teaching_difficulties: '区分 SYN 与 ACK 的含义',
        output_types: ['pptx', 'docx', 'pdf', 'html'],
        interaction_ideas: '根据时序图补全报文',
      },
    })
    expect(briefResponse.ok()).toBeTruthy()
    const brief = await briefResponse.json()
    const confirmed = await page.request.post(`${baseURL}/api/v1/projects/${projectId}/brief/confirm`, {
      headers: authHeaders,
      data: { expected_version: brief.version },
    })
    expect(confirmed.ok()).toBeTruthy()
    const planResponse = await page.request.post(`${baseURL}/api/v1/projects/${projectId}/plan`, {
      headers: authHeaders,
      data: { generation_mode: 'template' },
    })
    expect(planResponse.ok()).toBeTruthy()

    await page.goto('/blueprint')
    await expect(page.getByRole('heading', { name: '教学蓝图' })).toBeVisible()
    await expect(page.locator('.top-bar').getByText('Playwright 课程', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: '四类成果规范' })).toBeVisible()
    await page.getByRole('button', { name: '编辑蓝图' }).click()
    await page.locator('.summary-title input').fill('TCP 连接建立发布验收')
    await page.getByRole('button', { name: '保存新版本' }).click()
    await expect(page.getByText('教师修订', { exact: true })).toBeVisible()
    await expect(page.getByText('TCP 连接建立发布验收', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: '生成并导出成果' }).click()
    await expect(page).toHaveURL(/\/exports\?/)
    await expect(page.getByRole('heading', { name: '导出与版本' })).toBeVisible()
    await expect(page.getByText('已完成', { exact: true })).toHaveCount(4, { timeout: 30_000 })

    const versionsResponse = await page.request.get(`${baseURL}/api/v1/projects/${projectId}/versions`, {
      headers: authHeaders,
    })
    const versions = await versionsResponse.json()
    expect(versions[0].snapshot.title).toBe('TCP 连接建立发布验收')
    const exportsResponse = await page.request.get(
      `${baseURL}/api/v1/projects/${projectId}/exports?artifact_version_id=${versions[0].artifact_version_id}`,
      { headers: authHeaders },
    )
    const exports = await exportsResponse.json()
    expect(exports).toHaveLength(4)
    const signatures = { pptx: 'PK', docx: 'PK', pdf: '%PDF-', html: '<!DOCTYPE html>' }
    for (const item of exports) {
      expect(item.status).toBe('completed')
      const download = await page.request.get(`${baseURL}/api/v1/exports/${item.export_id}/download`, {
        headers: authHeaders,
      })
      expect(download.ok()).toBeTruthy()
      const body = await download.body()
      expect(body.length).toBe(item.size_bytes)
      expect(body.toString('utf8', 0, signatures[item.format].length)).toBe(signatures[item.format])
    }

    await page.goto('/editor')
    await expect(page.getByRole('heading', { name: '成果编辑' })).toBeVisible()
    await expect(page.getByText('AI 局部重生成', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'AI 重生成并创建版本' })).toBeVisible()

    await page.goto('/')
    const projectRow = page.locator('tbody tr').filter({ hasText: 'Playwright 课程' })
    await projectRow.getByRole('button', { name: '归档' }).click()
    await page.getByRole('dialog').getByRole('button', { name: '归档' }).click()
    await expect(projectRow.getByText('已归档', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('active_project_id'))).toBeNull()
    await page.goto('/materials')
    await expect.poll(() => new URL(page.url()).pathname).toBe('/')

    expect(consoleErrors).toEqual([])
    await page.screenshot({ path: 'test-results/main-flow.png', fullPage: true })
  } finally {
    if (projectId && token) {
      await page.request.delete(`${process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'}/api/v1/projects/${projectId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
    }
  }
})

test('admin can review the server-backed user directory', async ({ page }) => {
  const email = process.env.E2E_ADMIN_EMAIL
  const password = process.env.E2E_ADMIN_PASSWORD
  test.skip(!email || !password, 'Set E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD to run the admin smoke test')

  await page.goto('/login')
  await page.getByPlaceholder('teacher@example.com').fill(email)
  await page.getByPlaceholder('请输入密码').fill(password)
  await page.getByRole('button', { name: '登录', exact: true }).click()

  await expect(page).toHaveURL(/\/admin$/)
  await expect(page.getByRole('link', { name: '管理员工作台' })).toBeVisible()
  await expect(page.getByRole('link', { name: '我的知识库' })).not.toBeVisible()
  await expect(page.getByRole('heading', { name: '管理员工作台' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '用户目录' })).toBeVisible()
  await expect(page.getByText(email, { exact: true })).toBeVisible()

  await page.getByLabel('搜索用户').fill(email)
  await expect(page.getByText(email, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '清除筛选' }).click()
  await page.getByRole('button', { name: '刷新用户' }).click()
  await expect(page.getByText(email, { exact: true })).toBeVisible()
  await page.goto('/knowledge')
  await expect(page).toHaveURL(/\/admin$/)
})
