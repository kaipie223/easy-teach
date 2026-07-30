<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>教学蓝图</h1>
        <p>确认课程范围、教学阶段、知识点顺序和生成检查状态。</p>
      </div>
      <div class="header-actions">
        <el-button @click="go('/requirements')">调整蓝图</el-button>
        <el-button type="primary" @click="go('/editor')">生成成果</el-button>
      </div>
    </header>

    <section class="section-card">
      <div class="blueprint-summary">
        <div class="summary-item">
          <span>课程主题</span>
          <strong>{{ blueprintSummary.topic }}</strong>
        </div>
        <div class="summary-item">
          <span>授课对象</span>
          <strong>{{ blueprintSummary.audience }}</strong>
        </div>
        <div class="summary-item">
          <span>课时</span>
          <strong>{{ blueprintSummary.duration }}</strong>
        </div>
        <div class="summary-item">
          <span>生成范围</span>
          <strong>{{ blueprintSummary.scope }}</strong>
        </div>
      </div>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>课程流程规划</h2>
          <p>每个阶段都绑定知识点、教学活动和证据来源。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>阶段</th>
              <th>时间分配</th>
              <th>知识点</th>
              <th>教学活动</th>
              <th>证据来源</th>
              <th>拟生成页面</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="stage in blueprintPlan" :key="stage.id">
              <td>{{ stage.stage }}</td>
              <td>{{ stage.duration }}</td>
              <td>{{ stage.knowledge }}</td>
              <td>{{ stage.activity }}</td>
              <td>
                <span class="status-pill pending">{{ stage.evidence }}</span>
              </td>
              <td>{{ stage.pages }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>生成检查</h2>
          <p>确保蓝图满足成果生成前置条件。</p>
        </div>
      </div>
      <div class="check-grid">
        <div v-for="check in generationChecks" :key="check.key" class="check-item">
          <span class="status-pill pass">
            <el-icon><CircleCheck /></el-icon>
          </span>
          <strong>{{ check.label }}</strong>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { blueprintPlan, blueprintSummary, generationChecks } from '../mocks'

const router = useRouter()

function go(path) {
  router.push(path)
}
</script>
