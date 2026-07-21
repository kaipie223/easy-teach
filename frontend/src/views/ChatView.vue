<template>
  <div class="chat-view">
    <el-row :gutter="20">
      <el-col :span="16">
        <ChatPanel />
      </el-col>
      <el-col :span="8">
        <UploadPanel />
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import ChatPanel from '../components/ChatPanel.vue'
import UploadPanel from '../components/UploadPanel.vue'
</script>

<style scoped>
.chat-view { padding: 20px; height: calc(100vh - 80px); }
</style>
