<template>
  <main class="auth-page">
    <!-- 与"新建教案"页同款的 Stripe 式动态极光，且跟随鼠标；
         下层的静态 mesh 保留作为 WebGL 不可用时的兜底 -->
    <div class="auth-backdrop surface-mesh" aria-hidden="true" />
    <AuroraBackdrop class="auth-aurora" />

    <section class="auth-shell" aria-labelledby="auth-title">
      <header class="auth-brand">
        <img class="brand-lockup" :src="logoLockup" alt="easy-teach" />
      </header>

      <div class="auth-card">
        <p class="eyebrow">{{ isRegister ? '新账号' : '欢迎回来' }}</p>
        <h1 id="auth-title" class="auth-title">
          {{ isRegister ? '创建教师账号' : '登录工作台' }}
        </h1>
        <p class="auth-subtitle">
          {{
            isRegister
              ? '注册后即可保存课程项目与共创记录。'
              : '继续打磨你的下一节课。'
          }}
        </p>

        <el-form
          ref="formRef"
          class="auth-form"
          :model="form"
          :rules="rules"
          label-position="top"
          @submit.prevent="submit"
        >
          <el-form-item v-if="isRegister" label="显示名称" prop="display_name">
            <el-input
              v-model="form.display_name"
              size="large"
              placeholder="例如：张老师"
              autocomplete="name"
            />
          </el-form-item>

          <el-form-item label="邮箱" prop="email">
            <el-input
              v-model="form.email"
              size="large"
              type="email"
              placeholder="teacher@example.com"
              autocomplete="email"
            />
          </el-form-item>

          <el-form-item label="密码" prop="password">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              show-password
              :placeholder="isRegister ? '至少 8 个字符' : '请输入密码'"
              :autocomplete="isRegister ? 'new-password' : 'current-password'"
              @keydown.enter="submit"
            />
          </el-form-item>

          <el-alert
            v-if="errorMessage"
            class="auth-error"
            :title="errorMessage"
            type="error"
            show-icon
            :closable="false"
          />

          <el-button
            class="auth-submit"
            type="primary"
            size="large"
            native-type="submit"
            :loading="auth.loading"
          >
            {{ isRegister ? '注册并进入' : '登录' }}
          </el-button>
        </el-form>

        <button class="auth-switch" type="button" @click="toggleMode">
          {{ isRegister ? '已有账号？返回登录' : '还没有账号？注册一个' }}
        </button>
      </div>

      <p class="auth-footnote">
        多模态 AI 教学智能体 · 从需求共创到可直接上课的教案
      </p>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import logoLockup from '@/assets/brand/logo-lockup.png'
import AuroraBackdrop from '@/components/common/AuroraBackdrop.vue'

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
  position: relative;
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: var(--space-10) var(--space-6);
  overflow: hidden;
}

/* 静态 mesh 留在最底层兜底（WebGL 不可用时它还在）；动效层铺满视口，
   卡片由 .auth-shell（position: relative）自然浮在上面 */
.auth-backdrop {
  position: absolute;
  inset: -20%;
  opacity: 0.7;
  filter: saturate(1.05);
}

.auth-aurora {
  position: fixed;
  inset: 0;
  pointer-events: none;
}

.auth-shell {
  position: relative;
  width: min(100%, 420px);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.auth-brand {
  display: flex;
  align-items: center;
  justify-content: center;
}

.brand-lockup {
  width: 156px;
  height: auto;
}

.auth-card {
  padding: var(--space-10) var(--space-8);
  border: 1px solid var(--border-hairline);
  border-radius: var(--radius-xl);
  background: var(--bg-surface);
  box-shadow: var(--shadow-overlay);
}

.auth-title {
  margin-top: var(--space-3);
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
  color: var(--text-primary);
}

.auth-subtitle {
  margin-top: var(--space-2);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.auth-form {
  margin-top: var(--space-8);
}

.auth-error {
  margin-bottom: var(--space-4);
}

.auth-submit {
  width: 100%;
  margin-top: var(--space-2);
}

.auth-switch {
  display: block;
  width: 100%;
  margin-top: var(--space-5);
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-brand);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-standard);
}

.auth-switch:hover { color: var(--brand-800); }

.auth-footnote {
  text-align: center;
  color: var(--text-tertiary);
  font-size: var(--text-xs);
}

@media (max-width: 480px) {
  .auth-page { padding: var(--space-6) var(--space-4); }

  .auth-card {
    padding: var(--space-8) var(--space-5);
  }

  .auth-title { font-size: var(--text-xl); }
}
</style>
