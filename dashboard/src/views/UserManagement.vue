<script setup lang="ts">
import { ref, h, onMounted } from 'vue'
import { NCard, NDataTable, NTag, NSelect, NH2, NText, NSpin, useMessage } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { usersApi } from '@/api/users'
import type { UserInfo } from '@/api/auth'

const message = useMessage()
const loading = ref(true)
const users = ref<UserInfo[]>([])
const total = ref(0)

const roleOptions = [
  { label: '管理员', value: 'admin' },
  { label: '经理', value: 'manager' },
  { label: '员工', value: 'employee' },
]

function roleTagType(role: string): 'success' | 'warning' | 'info' {
  if (role === 'admin') return 'success'
  if (role === 'manager') return 'warning'
  return 'info'
}

async function handleRoleChange(userId: number, newRole: string) {
  try {
    await usersApi.update(userId, { role: newRole })
    const user = users.value.find((u) => u.id === userId)
    if (user) user.role = newRole as UserInfo['role']
    message.success('角色已更新')
  } catch {
    message.error('更新失败')
  }
}

const columns: DataTableColumns<UserInfo> = [
  { title: 'ID', key: 'id', width: 60 },
  { title: '用户名', key: 'username', width: 120 },
  { title: '显示名称', key: 'display_name', width: 140 },
  { title: '员工 ID', key: 'employee_id', width: 100 },
  { title: '部门', key: 'department', width: 120 },
  {
    title: '角色',
    key: 'role',
    width: 160,
    render(row) {
      return h(
        NSelect,
        {
          value: row.role,
          options: roleOptions,
          size: 'small',
          style: 'width: 120px;',
          onUpdateValue: (val: string) => handleRoleChange(row.id, val),
        },
      )
    },
  },
  {
    title: '状态',
    key: 'is_dept_manager',
    width: 100,
    render(row) {
      return h(NTag, { size: 'small', type: roleTagType(row.role), bordered: false }, () => {
        const labels: Record<string, string> = { admin: '管理员', manager: '经理', employee: '员工' }
        return labels[row.role] || row.role
      })
    },
  },
]

async function loadUsers() {
  loading.value = true
  try {
    const { data } = await usersApi.list({ limit: 200 })
    users.value = data.users
    total.value = data.total
  } finally {
    loading.value = false
  }
}

onMounted(() => loadUsers())
</script>

<template>
  <div>
    <n-h2 style="margin: 0 0 16px;">
      <n-text>用户管理</n-text>
    </n-h2>

    <n-card>
      <n-spin :show="loading">
        <n-data-table
          :columns="columns"
          :data="users"
          :row-key="(row: UserInfo) => row.id"
          :bordered="false"
          size="small"
          :max-height="600"
        />
        <div style="margin-top: 8px; color: #999; font-size: 13px;">
          共 {{ total }} 位用户
        </div>
      </n-spin>
    </n-card>
  </div>
</template>
