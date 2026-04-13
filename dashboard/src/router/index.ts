import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'Login', component: () => import('@/views/Login.vue'), meta: { guest: true } },
    { path: '/', name: 'Dashboard', component: () => import('@/views/Dashboard.vue'), meta: { roles: ['admin', 'manager', 'employee'] } },
    { path: '/recording', name: 'Recording', component: () => import('@/views/Recording.vue'), meta: { roles: ['admin', 'manager', 'employee'] } },
    { path: '/sops', name: 'SopList', component: () => import('@/views/SopList.vue'), meta: { roles: ['admin', 'manager', 'employee'] } },
    { path: '/sops/:id', name: 'SopEditor', component: () => import('@/views/SopEditor.vue'), meta: { roles: ['admin', 'manager'] } },
    { path: '/analytics', name: 'Analytics', component: () => import('@/views/Analytics.vue'), meta: { roles: ['admin', 'manager', 'employee'] } },
    { path: '/audit', name: 'Audit', component: () => import('@/views/Audit.vue'), meta: { roles: ['admin', 'manager'] } },
    { path: '/users', name: 'UserManagement', component: () => import('@/views/UserManagement.vue'), meta: { roles: ['admin'] } },
    { path: '/settings', name: 'Settings', component: () => import('@/views/Settings.vue'), meta: { roles: ['admin'] } },
  ],
})

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore()

  // Guest pages (login) are always accessible
  if (to.meta.guest) {
    if (auth.isLoggedIn) return next('/')
    return next()
  }

  // Not logged in -> redirect to login
  if (!auth.isLoggedIn) return next('/login')

  // Fetch user info if not loaded yet
  if (!auth.user) {
    await auth.fetchUser()
    if (!auth.user) return next('/login')
  }

  // Role check
  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.user && !roles.includes(auth.user.role)) {
    return next('/')
  }

  next()
})

export default router
