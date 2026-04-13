<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { NConfigProvider, NLayout, NLayoutSider, NLayoutContent, zhCN, dateZhCN } from 'naive-ui'
import Sidebar from '@/components/layout/Sidebar.vue'
import HeaderBar from '@/components/layout/Header.vue'

const route = useRoute()
const isGuestPage = computed(() => !!route.meta.guest)
</script>

<template>
  <n-config-provider :locale="zhCN" :date-locale="dateZhCN">
    <template v-if="isGuestPage">
      <router-view />
    </template>
    <template v-else>
      <n-layout has-sider style="height: 100vh">
        <n-layout-sider bordered :width="220" content-style="padding: 0;">
          <Sidebar />
        </n-layout-sider>
        <n-layout>
          <HeaderBar />
          <n-layout-content content-style="padding: 24px;">
            <router-view />
          </n-layout-content>
        </n-layout>
      </n-layout>
    </template>
  </n-config-provider>
</template>
