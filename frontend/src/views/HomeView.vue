<template>
  <div class="home">
    <el-row :gutter="20">
      <el-col :span="12">
        <el-card>
          <template #header>💬 开始教学对话</template>
          <p>通过多轮对话描述您的教学需求，AI 将引导您明确教学目标。</p>
          <el-button type="primary" @click="$router.push('/chat')">进入对话</el-button>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header>📂 上传参考资料</template>
          <p>支持 PDF、Word、图片、视频 — AI 自动提取关键内容。</p>
          <el-button type="success" @click="$router.push('/chat')">去上传</el-button>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
// 首页 — 待潘卓然实现
</script>

<style scoped>
.home { padding: 20px; }
</style>
