<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>资料中心</h1>
        <p>上传参考资料，查看解析状态，并将证据片段绑定到教学生成流程。</p>
      </div>
      <div class="header-actions">
        <el-button @click="go('/blueprint')">批量绑定用途</el-button>
      </div>
    </header>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>上传参考资料</h2>
          <p>支持 PDF / Word / PPT / 图片 / 视频。</p>
        </div>
      </div>
      <div class="upload-zone">
        <el-icon :size="28"><UploadFilled /></el-icon>
        <span>拖拽资料到此处，或选择文件</span>
        <el-button type="primary">选择文件</el-button>
      </div>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>资料列表与用途绑定</h2>
          <p>展示解析进度、资料用途和作用范围。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>文件名</th>
              <th>类型</th>
              <th>大小</th>
              <th>解析状态</th>
              <th>资料用途</th>
              <th>作用范围</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="file in materials" :key="file.id">
              <td>
                <span class="material-name">
                  <el-icon><Document /></el-icon>
                  {{ file.name }}
                </span>
              </td>
              <td>{{ file.type }}</td>
              <td>{{ file.size }}</td>
              <td>
                <div class="progress-track">
                  <div
                    :class="['progress-fill', file.status === '解析中' ? 'loading' : '']"
                    :style="{ width: `${file.progress}%` }"
                  />
                </div>
                <span :class="['status-pill', materialStatusClass(file.status)]">{{ file.status }}</span>
              </td>
              <td>
                <el-select :model-value="file.usage" size="small">
                  <el-option label="内容依据" value="内容依据" />
                  <el-option label="知识结构参考" value="知识结构参考" />
                  <el-option label="案例来源" value="案例来源" />
                  <el-option label="互动素材" value="互动素材" />
                </el-select>
              </td>
              <td>{{ file.scope }}</td>
              <td class="link-actions">
                <el-button link type="primary" @click="go('/editor')">预览</el-button>
                <el-button link type="primary">解绑</el-button>
                <el-button link type="primary">删除</el-button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="section-card">
      <div class="section-header">
        <div>
          <h2>证据片段</h2>
          <p>生成内容可引用的来源片段。</p>
        </div>
      </div>
      <div class="evidence-grid">
        <article v-for="evidence in evidenceSnips" :key="evidence.id" class="evidence-card">
          <strong>{{ evidence.source }}</strong>
          <p>{{ evidence.content }}</p>
          <div class="header-actions">
            <el-button type="primary" size="small" @click="go('/blueprint')">引用</el-button>
            <el-button size="small" link @click="go('/materials')">查看来源</el-button>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { evidenceSnips, materials } from '../mocks'

const router = useRouter()

function go(path) {
  router.push(path)
}

function materialStatusClass(status) {
  return {
    已解析: 'done',
    解析中: 'running',
    失败: 'failed',
  }[status] || 'pending'
}
</script>
