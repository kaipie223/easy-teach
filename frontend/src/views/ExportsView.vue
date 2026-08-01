<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>导出与版本</h1>
        <p>查看当前版本、导出质量检查、导出格式和历史版本。</p>
      </div>
      <div class="header-actions">
        <el-button type="primary" @click="go('/exports')">导出成果</el-button>
      </div>
    </header>

    <section class="exports-layout">
      <article class="section-card">
        <div class="section-header">
          <h2>当前版本 {{ exportVersion.version }}</h2>
        </div>
        <div class="version-meta">
          <span class="muted">创建者</span>
          <strong>{{ exportVersion.author }}</strong>
          <span class="muted">更新时间</span>
          <strong>{{ exportVersion.updatedAt }}</strong>
          <span class="muted">内容概览</span>
          <strong>{{ exportVersion.summary }}</strong>
          <span class="muted">状态</span>
          <strong>
            <span class="status-pill done">{{ exportVersion.status }}</span>
          </strong>
        </div>
      </article>

      <article class="section-card">
        <div class="section-header">
          <h2>导出前质量检查</h2>
        </div>
        <div class="field-list">
          <div v-for="check in exportQualityChecks" :key="check.key" class="check-item">
            <span class="status-pill pass">
              <el-icon><CircleCheck /></el-icon>
            </span>
            <strong>{{ check.label }}</strong>
          </div>
        </div>
      </article>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>导出格式</h2>
          <p>当前版本可导出的成果包。</p>
        </div>
      </div>
      <div class="export-grid">
        <article v-for="format in exportFormats" :key="format.key" class="format-card">
          <strong>{{ format.label }}</strong>
          <p>
            <span class="status-pill done">{{ format.status }}</span>
          </p>
          <el-button @click="go('/exports')">下载</el-button>
        </article>
      </div>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>版本历史</h2>
          <p>支持查看、恢复和重新导出历史版本。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>版本号</th>
              <th>更新时间</th>
              <th>修改说明</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="version in versionHistory" :key="version.version">
              <td>
                {{ version.version }}
                <span v-if="version.current" class="status-pill active">当前</span>
              </td>
              <td>{{ version.updatedAt }}</td>
              <td>{{ version.note }}</td>
              <td class="link-actions">
                <el-button link type="primary" @click="go('/editor')">查看</el-button>
                <el-button link type="primary" @click="go('/editor')">恢复</el-button>
                <el-button link type="primary" @click="go('/exports')">重新导出</el-button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { exportFormats, exportQualityChecks, exportVersion, versionHistory } from '../mocks'

const router = useRouter()

function go(path) {
  router.push(path)
}
</script>
