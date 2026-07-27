<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>成果编辑</h1>
        <p>预览生成成果，查看来源引用，并根据 AI 建议进行局部修改。</p>
      </div>
      <div class="header-actions">
        <el-button @click="go('/')">保存草稿</el-button>
        <el-button type="primary" @click="go('/exports')">发布版本</el-button>
      </div>
    </header>

    <div class="tabs-row">
      <span class="tab-item active">PPT预览</span>
      <span class="tab-item">教案预览</span>
      <span class="tab-item">互动内容</span>
    </div>

    <section class="editor-grid">
      <aside class="section-card">
        <div class="section-header">
          <h2>页面列表</h2>
          <el-button size="small">新增页面</el-button>
        </div>
        <div class="slide-rail">
          <article
            v-for="slide in slidePages.slice(0, 6)"
            :key="slide.id"
            :class="['slide-thumb', slide.selected ? 'active' : '']"
          >
            <strong>{{ slide.page }}</strong>
            <div class="thumb-preview" />
          </article>
        </div>
      </aside>

      <main class="section-card">
        <div class="section-header">
          <div>
            <h2>当前页面预览</h2>
            <p>第 {{ currentSlide.page }} 页 / 共 {{ currentSlide.total }} 页</p>
          </div>
        </div>

        <div class="slide-canvas">
          <div>
            <h2>{{ currentSlide.title }}</h2>
            <ul>
              <li v-for="bullet in currentSlide.bullets" :key="bullet">{{ bullet }}</li>
            </ul>
          </div>
          <div class="media-placeholder">
            <el-icon><VideoPlay /></el-icon>
          </div>
        </div>

        <div class="canvas-toolbar">
          <el-button text>
            <el-icon><ArrowLeft /></el-icon>
          </el-button>
          <span>{{ currentSlide.page }} / {{ currentSlide.total }}</span>
          <el-button text>
            <el-icon><ArrowRight /></el-icon>
          </el-button>
          <span class="muted">|</span>
          <el-button text>-</el-button>
          <span>{{ currentSlide.zoom }}%</span>
          <el-button text>+</el-button>
          <el-button text>
            <el-icon><FullScreen /></el-icon>
          </el-button>
        </div>

        <div class="source-block">
          <div class="section-header">
            <h2>来源与引用</h2>
          </div>
          <div class="source-row">
            <span v-for="source in sourceReferences" :key="source" class="status-pill pending">
              {{ source }}
            </span>
          </div>
        </div>
      </main>

      <aside class="section-card">
        <div class="section-header">
          <div>
            <h2>AI 修改助手</h2>
            <p>当前选中：第{{ currentSlide.page }}页</p>
          </div>
        </div>
        <p class="muted">优化建议</p>
        <ul class="suggestion-list">
          <li v-for="suggestion in editorSuggestions" :key="suggestion">{{ suggestion }}</li>
        </ul>
        <el-input type="textarea" :rows="5" placeholder="输入修改意见..." />
        <el-button class="assistant-button" type="primary" @click="go('/editor')">应用修改</el-button>
      </aside>
    </section>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { currentSlide, editorSuggestions, slidePages, sourceReferences } from '../mocks'

const router = useRouter()

function go(path) {
  router.push(path)
}
</script>

<style scoped>
.source-block {
  margin-top: 14px;
  padding-top: 18px;
  border-top: 1px solid #e4e9f2;
}

.assistant-button {
  width: 100%;
  margin-top: 14px;
}
</style>
