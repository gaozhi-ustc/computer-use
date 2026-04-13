<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NMenu, type MenuOption } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import {
  HomeOutline,
  VideocamOutline,
  DocumentTextOutline,
  BarChartOutline,
  SearchOutline,
  PeopleOutline,
  SettingsOutline,
} from '@vicons/ionicons5'
import { NIcon } from 'naive-ui'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

function renderIcon(icon: typeof HomeOutline) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

interface MenuItem {
  label: string
  key: string
  icon: typeof HomeOutline
  roles: string[]
}

const allMenuItems: MenuItem[] = [
  { label: '概览', key: '/', icon: HomeOutline, roles: ['admin', 'manager', 'employee'] },
  { label: '录制回放', key: '/recording', icon: VideocamOutline, roles: ['admin', 'manager', 'employee'] },
  { label: 'SOP 管理', key: '/sops', icon: DocumentTextOutline, roles: ['admin', 'manager', 'employee'] },
  { label: '效率分析', key: '/analytics', icon: BarChartOutline, roles: ['admin', 'manager', 'employee'] },
  { label: '审计查询', key: '/audit', icon: SearchOutline, roles: ['manager', 'admin'] },
  { label: '用户管理', key: '/users', icon: PeopleOutline, roles: ['admin'] },
  { label: '系统设置', key: '/settings', icon: SettingsOutline, roles: ['admin'] },
]

const menuOptions = computed<MenuOption[]>(() => {
  const userRole = auth.user?.role || ''
  return allMenuItems
    .filter((item) => item.roles.includes(userRole))
    .map((item) => ({
      label: item.label,
      key: item.key,
      icon: renderIcon(item.icon),
    }))
})

const activeKey = ref(route.path)

watch(
  () => route.path,
  (path) => {
    // Match /sops/:id to /sops menu item
    activeKey.value = path.startsWith('/sops/') ? '/sops' : path
  }
)

function handleMenuUpdate(key: string) {
  router.push(key)
}
</script>

<template>
  <div style="padding: 20px 0;">
    <div style="padding: 0 20px 16px; font-size: 18px; font-weight: 600; color: #333;">
      Workflow Recorder
    </div>
    <n-menu
      :value="activeKey"
      :options="menuOptions"
      @update:value="handleMenuUpdate"
    />
  </div>
</template>
