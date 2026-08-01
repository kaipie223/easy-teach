<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <h1>需求共创</h1>
        <p>通过多轮对话确认课程主题、授课对象、教学目标和输出类型。</p>
      </div>
    </header>

    <section class="two-column">
      <article class="section-card chat-panel">
        <div class="section-header">
          <div>
            <h2>AI 共创助手</h2>
            <p>引导教师补齐生成教学成果所需的关键信息。</p>
          </div>
        </div>

        <div class="chat-list">
          <div
            v-for="message in chatMessages"
            :key="message.id"
            :class="['message-row', message.role === 'assistant' ? 'assistant' : 'teacher']"
          >
            <div class="message-meta">
              <strong>{{ message.sender }}</strong>
              <span>{{ message.time }}</span>
            </div>
            <div class="message-bubble">{{ message.content }}</div>
          </div>
        </div>

        <div class="chat-input-bar">
          <el-input placeholder="输入教学想法或回答 AI 的问题" />
          <el-button>
            <el-icon><Microphone /></el-icon>
          </el-button>
          <el-button type="primary" @click="go('/blueprint')">
            <el-icon><Promotion /></el-icon>
          </el-button>
        </div>
      </article>

      <article class="section-card requirement-panel">
        <div class="section-header">
          <div>
            <h2>教学需求确认单</h2>
            <p>右侧表单用于承接 AI 推断结果和教师确认内容。</p>
          </div>
        </div>

        <div class="field-list">
          <div class="field-row">
            <label>课程主题</label>
            <el-input :model-value="requirementForm.topic" />
            <el-button disabled>教师输入</el-button>
          </div>
          <div class="field-row">
            <label>授课对象</label>
            <el-input :model-value="requirementForm.audience" />
            <el-button disabled>教师输入</el-button>
          </div>
          <div class="field-row">
            <label>课时</label>
            <el-input :model-value="requirementForm.duration" />
            <el-button disabled>教师输入</el-button>
          </div>
          <div class="field-row">
            <label>教学目标</label>
            <el-input :model-value="requirementForm.objectives" type="textarea" :rows="3" />
            <el-button disabled>AI推断</el-button>
          </div>
          <div class="field-row">
            <label>核心知识点</label>
            <el-input :model-value="requirementForm.coreKnowledge" type="textarea" :rows="2" />
            <el-button disabled>AI推断</el-button>
          </div>
          <div class="field-row">
            <label>重点难点</label>
            <el-input :model-value="requirementForm.focusAndDifficulties" type="textarea" :rows="2" />
            <el-button disabled>AI推断</el-button>
          </div>
          <div class="field-row">
            <label>输出类型</label>
            <el-select :model-value="requirementForm.outputType">
              <el-option
                v-for="option in outputTypeOptions"
                :key="option"
                :label="option"
                :value="option"
              />
            </el-select>
            <el-button disabled>AI推断</el-button>
          </div>
        </div>

        <el-button type="primary" class="confirm-button" @click="go('/blueprint')">确认并生成蓝图</el-button>
      </article>
    </section>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { chatMessages, outputTypeOptions, requirementForm } from '../mocks'

const router = useRouter()

function go(path) {
  router.push(path)
}
</script>

<style scoped>
.confirm-button {
  width: 100%;
  margin-top: 20px;
}
</style>
