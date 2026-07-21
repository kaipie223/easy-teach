<template>
  <el-container class="app-container">
    <el-header class="app-header">
      <h1>📚 easy-teach — 多模态AI教学智能体</h1>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
// App 根组件
</script>

<style>
body { margin: 0; font-family: 'Microsoft YaHei', sans-serif; }
.app-header { background: #409eff; color: white; display: flex; align-items: center; }
.app-header h1 { font-size: 20px; }
.app-container { min-height: 100vh; }
</style>
