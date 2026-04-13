<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NTag, NButton, NSpace } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const pageTitle = computed(() => {
  const nameMap: Record<string, string> = {
    Dashboard: '概览',
    Recording: '录制回放',
    SopList: 'SOP 管理',
    SopEditor: 'SOP 编辑器',
    Analytics: '效率分析',
    Audit: '审计查询',
    UserManagement: '用户管理',
    Settings: '系统设置',
  }
  return nameMap[route.name as string] || (route.name as string) || ''
})

const roleLabel = computed(() => {
  const map: Record<string, string> = {
    admin: '管理员',
    manager: '主管',
    employee: '员工',
  }
  return map[auth.user?.role || ''] || auth.user?.role || ''
})

const roleType = computed<'success' | 'warning' | 'info'>(() => {
  const map: Record<string, 'success' | 'warning' | 'info'> = {
    admin: 'success',
    manager: 'warning',
    employee: 'info',
  }
  return map[auth.user?.role || ''] || 'info'
})

async function handleLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<template>
  <div style="display: flex; align-items: center; justify-content: space-between; padding: 0 24px; height: 56px; border-bottom: 1px solid #efeff5;">
    <h2 style="margin: 0; font-size: 18px; font-weight: 600;">{{ pageTitle }}</h2>
    <n-space align="center" :size="12">
      <span v-if="auth.user">{{ auth.user.display_name }}</span>
      <n-tag v-if="auth.user" :type="roleType" size="small">{{ roleLabel }}</n-tag>
      <n-button text type="error" @click="handleLogout">退出</n-button>
    </n-space>
  </div>
</template>
