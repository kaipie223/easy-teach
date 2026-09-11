<template>
  <main class="auth-page">
    <section class="auth-panel" aria-labelledby="auth-title">
      <div class="auth-mark">ET</div>
      <p class="eyebrow">EASY-TEACH</p>
      <h1 id="auth-title">{{ isRegister ? '创建教师账号' : '登录工作台' }}</h1>
      <p class="auth-intro">{{ isRegister ? '注册后即可保存课程项目和对话记录。' : '继续管理你的教学项目。' }}</p>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
        <el-form-item v-if="isRegister" label="显示名称" prop="display_name">
          <el-input v-model="form.display_name" placeholder="例如：张老师" autocomplete="name" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" type="email" placeholder="teacher@example.com" autocomplete="email" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            :placeholder="isRegister ? '至少 8 个字符' : '请输入密码'"
            :autocomplete="isRegister ? 'new-password' : 'current-password'"
            @keydown.enter="submit"
          />
        </el-form-item>

        <el-alert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />
        <el-button class="submit-button" type="primary" native-type="submit" :loading="auth.loading">
          {{ isRegister ? '注册并进入' : '登录' }}
        </el-button>
      </el-form>

      <button class="mode-button" type="button" @click="toggleMode">
        {{ isRegister ? '已有账号？返回登录' : '还没有账号？注册一个' }}
      </button>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const formRef = ref(null)
const isRegister = ref(false)
const errorMessage = ref('')
const form = reactive({
  display_name: '',
  email: '',
  password: '',
})

const rules = {
  display_name: [{ required: true, message: '请输入显示名称', trigger: 'blur' }],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: ['blur', 'change'] },
  ],
  password: [{ required: true, min: 8, message: '密码至少 8 个字符', trigger: 'blur' }],
}

function toggleMode() {
  isRegister.value = !isRegister.value
  errorMessage.value = ''
  formRef.value?.clearValidate()
}

async function submit() {
  if (!formRef.value || auth.loading) return
  errorMessage.value = ''
  try {
    await formRef.value.validate()
    if (isRegister.value) {
      await auth.register({
        email: form.email,
        password: form.password,
        display_name: form.display_name,
      })
    } else {
      await auth.login({ email: form.email, password: form.password })
    }
    const roleHome = auth.user?.role === 'admin' ? '/admin' : '/'
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : roleHome
    await router.replace(redirect.startsWith('/') ? redirect : roleHome)
  } catch (error) {
    if (error?.response) {
      errorMessage.value = error.response.data?.error?.message || '请求失败，请稍后重试'
    } else if (typeof error === 'string') {
      errorMessage.value = error
    }
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: #f4f7fb;
}

.auth-panel {
  width: min(100%, 420px);
  padding: 34px;
  border: 1px solid #dfe6ef;
  border-radius: 10px;
  background: #ffffff;
  box-shadow: 0 16px 36px rgba(15, 23, 42, 0.08);
}

.auth-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: #1463ff;
  color: #ffffff;
  font-weight: 800;
  letter-spacing: 0;
}

.eyebrow {
  margin: 20px 0 8px;
  color: #1463ff;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1px;
}

h1 {
  margin: 0;
  color: #0f172a;
  font-size: 26px;
  line-height: 1.25;
}

.auth-intro {
  margin: 10px 0 26px;
  color: #64748b;
  font-size: 14px;
}

.submit-button {
  width: 100%;
  margin-top: 8px;
}

.mode-button {
  width: 100%;
  margin-top: 18px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #1463ff;
  font-size: 14px;
  cursor: pointer;
}

.mode-button:hover {
  color: #0b45b4;
}

@media (max-width: 480px) {
  .auth-panel {
    padding: 26px 20px;
  }
}
</style>
