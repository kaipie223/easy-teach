<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>工作台</h1>
        <p>集中查看教学任务进展、待处理资料和最近导出成果。</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" @click="go('/requirements')">
          <el-icon><Plus /></el-icon>
          新建教学任务
        </el-button>
      </div>
    </header>

    <section class="stat-grid">
      <article v-for="stat in dashboardStats" :key="stat.key" class="metric-card">
        <span class="metric-icon">
          <el-icon><component :is="stat.icon" /></el-icon>
        </span>
        <div>
          <span>{{ stat.label }}</span>
          <strong>{{ stat.value }}</strong>
        </div>
      </article>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>最近教学任务</h2>
          <p>按更新时间展示当前课程生成进展。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>任务名称</th>
              <th>课程主题</th>
              <th>授课对象</th>
              <th>状态</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in teachingTasks" :key="task.id">
              <td>{{ task.name }}</td>
              <td>{{ task.subject }}</td>
              <td>{{ task.audience }}</td>
              <td>
                <span :class="['status-pill', statusClass(task.status)]">{{ task.status }}</span>
              </td>
              <td>{{ task.updatedAt }}</td>
              <td class="link-actions">
                <el-button link type="primary" @click="go('/blueprint')">查看</el-button>
                <el-button link type="primary" @click="go('/requirements')">编辑</el-button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>最近导出</h2>
          <p>PPT、教案和互动包的最近生成记录。</p>
        </div>
      </div>
      <div class="export-grid">
        <article v-for="item in recentExports" :key="item.id" class="file-card">
          <strong>{{ item.name }}</strong>
          <p class="muted">{{ item.exportedAt }} · {{ item.size }}</p>
          <div class="header-actions">
            <el-button type="primary" size="small" @click="go('/exports')">下载</el-button>
            <el-button size="small" @click="go('/editor')">预览</el-button>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { dashboardStats, recentExports, teachingTasks } from '../mocks'

const router = useRouter()

function go(path) {
  router.push(path)
}

function statusClass(status) {
  return {
    进行中: 'running',
    已完成: 'done',
    草稿: 'draft',
  }[status] || 'pending'
}
</script>
