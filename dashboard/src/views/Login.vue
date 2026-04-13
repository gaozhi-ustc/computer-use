<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NTabs, NTabPane, NForm, NFormItem, NInput, NButton, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()

const username = ref('')
const password = ref('')
const loading = ref(false)

async function handleLogin() {
  if (!username.value || !password.value) {
    message.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    message.success('登录成功')
    router.push('/')
  } catch (err: unknown) {
    const errorMessage = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '登录失败，请检查用户名和密码'
    message.error(errorMessage)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div style="display: flex; align-items: center; justify-content: center; min-height: 100vh; background: #f5f7f9;">
    <div style="width: 400px;">
      <div style="text-align: center; margin-bottom: 24px;">
        <h1 style="font-size: 24px; font-weight: 600; color: #333; margin: 0 0 8px;">Workflow Recorder</h1>
        <p style="color: #999; margin: 0;">工作流录制与分析平台</p>
      </div>
      <n-card>
        <n-tabs default-value="password" size="large" justify-content="space-evenly">
          <n-tab-pane name="password" tab="密码登录">
            <n-form @submit.prevent="handleLogin">
              <n-form-item label="用户名">
                <n-input v-model:value="username" placeholder="请输入用户名" />
              </n-form-item>
              <n-form-item label="密码">
                <n-input
                  v-model:value="password"
                  type="password"
                  show-password-on="click"
                  placeholder="请输入密码"
                  @keyup.enter="handleLogin"
                />
              </n-form-item>
              <n-button
                type="primary"
                block
                :loading="loading"
                @click="handleLogin"
              >
                登录
              </n-button>
            </n-form>
          </n-tab-pane>
          <n-tab-pane name="dingtalk" tab="钉钉扫码">
            <div style="text-align: center; padding: 40px 0; color: #999;">
              钉钉登录将在后续版本开放
            </div>
          </n-tab-pane>
        </n-tabs>
      </n-card>
    </div>
  </div>
</template>
