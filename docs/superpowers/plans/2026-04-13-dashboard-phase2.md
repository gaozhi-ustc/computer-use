# Dashboard Phase 2: Vue 3 Frontend Shell

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Vue 3 SPA shell with login page, role-aware sidebar navigation, router guards, and placeholder pages — ready for Phase 3-5 to fill in real content.

**Architecture:** Vite + Vue 3 + TypeScript + Naive UI + Pinia + Vue Router + Axios. Development proxy `/api` → FastAPI `:8000`. Production build served as static files from FastAPI via `StaticFiles` mount.

**Tech Stack:** Vue 3.5+, Naive UI, Pinia, Vue Router 4, Axios, Vite 6, TypeScript

**Depends on:** Phase 1 (auth API at `/api/auth/login`, `/api/auth/me`, `/api/auth/refresh`)

---

### Task 1: Scaffold Vue project + install dependencies

**Files:**
- Create: `dashboard/` directory via `npm create vite`
- Modify: `dashboard/package.json` (add deps)

- [ ] **Step 1: Create Vite project**

```bash
cd C:\Users\gaozhi\Desktop\computer-use
npm create vite@latest dashboard -- --template vue-ts
```

- [ ] **Step 2: Install dependencies**

```bash
cd dashboard
npm install
npm install naive-ui @vicons/ionicons5 vue-router@4 pinia axios
npm install -D @types/node
```

- [ ] **Step 3: Verify dev server starts**

```bash
npm run dev
# Should show: Local: http://localhost:5173/
```
Stop dev server after verification.

- [ ] **Step 4: Commit**

```bash
cd .. && git add dashboard/ && git commit -m "scaffold: Vue 3 + Naive UI frontend project"
```

---

### Task 2: Vite config + Axios API client

**Files:**
- Modify: `dashboard/vite.config.ts`
- Create: `dashboard/src/api/client.ts`

- [ ] **Step 1: Configure Vite proxy**

`dashboard/vite.config.ts`:
```typescript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': resolve(__dirname, 'src') }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      }
    }
  }
})
```

- [ ] **Step 2: Create Axios client with JWT interceptor**

`dashboard/src/api/client.ts`:
```typescript
import axios from 'axios'

const client = axios.create({ baseURL: '' })

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export default client
```

- [ ] **Step 3: Create auth API module**

`dashboard/src/api/auth.ts`:
```typescript
import client from './client'

export interface LoginRequest { username: string; password: string }
export interface TokenResponse { access_token: string; refresh_token: string; token_type: string }
export interface UserInfo {
  id: number; username: string; display_name: string; avatar_url: string
  role: 'admin' | 'manager' | 'employee'
  employee_id: string | null; department: string; department_id: string
  is_dept_manager: boolean
}

export const authApi = {
  login: (data: LoginRequest) => client.post<TokenResponse>('/api/auth/login', data),
  refresh: (refresh_token: string) => client.post<TokenResponse>('/api/auth/refresh', { refresh_token }),
  me: () => client.get<UserInfo>('/api/auth/me'),
}
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/ && git commit -m "feat: Vite proxy config + Axios JWT client"
```

---

### Task 3: Pinia auth store

**Files:**
- Create: `dashboard/src/stores/auth.ts`
- Modify: `dashboard/src/main.ts` (install Pinia)

- [ ] **Step 1: Create auth store**

`dashboard/src/stores/auth.ts`:
```typescript
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type UserInfo } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserInfo | null>(null)
  const accessToken = ref(localStorage.getItem('access_token') || '')
  const refreshToken = ref(localStorage.getItem('refresh_token') || '')

  const isLoggedIn = computed(() => !!accessToken.value)
  const role = computed(() => user.value?.role || '')
  const isAdmin = computed(() => role.value === 'admin')
  const isManager = computed(() => role.value === 'manager')

  async function login(username: string, password: string) {
    const { data } = await authApi.login({ username, password })
    accessToken.value = data.access_token
    refreshToken.value = data.refresh_token
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    await fetchUser()
  }

  async function fetchUser() {
    try {
      const { data } = await authApi.me()
      user.value = data
    } catch {
      logout()
    }
  }

  function logout() {
    user.value = null
    accessToken.value = ''
    refreshToken.value = ''
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }

  return { user, accessToken, refreshToken, isLoggedIn, role, isAdmin, isManager, login, fetchUser, logout }
})
```

- [ ] **Step 2: Install Pinia in main.ts**

`dashboard/src/main.ts`:
```typescript
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/ && git commit -m "feat: Pinia auth store with login/logout/fetchUser"
```

---

### Task 4: Vue Router with role-based guards

**Files:**
- Create: `dashboard/src/router/index.ts`

- [ ] **Step 1: Define routes + guards**

```typescript
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

router.beforeEach(async (to, from, next) => {
  const auth = useAuthStore()
  if (to.meta.guest) { return next() }
  if (!auth.isLoggedIn) { return next('/login') }
  if (!auth.user) { await auth.fetchUser() }
  const roles = to.meta.roles as string[] | undefined
  if (roles && auth.user && !roles.includes(auth.user.role)) { return next('/') }
  next()
})

export default router
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/ && git commit -m "feat: Vue Router with role-based navigation guards"
```

---

### Task 5: Layout components (Sidebar + Header)

**Files:**
- Create: `dashboard/src/components/layout/Sidebar.vue`
- Create: `dashboard/src/components/layout/Header.vue`
- Modify: `dashboard/src/App.vue`

- [ ] **Step 1: Create Sidebar with role-aware menu**

Sidebar.vue uses `n-menu` from Naive UI with items filtered by current user role. Menu items: Dashboard, Recording, SOP Management, Analytics, Audit (manager+admin), User Management (admin), Settings (admin).

- [ ] **Step 2: Create Header with user info + logout**

Header.vue shows user display_name, avatar, role badge, and logout button.

- [ ] **Step 3: Update App.vue with layout**

App.vue wraps `<router-view>` in an `n-layout` with Sidebar on the left and Header on top. Login page renders without the layout (detected via route meta).

- [ ] **Step 4: Commit**

```bash
git add dashboard/ && git commit -m "feat: sidebar + header layout with role-aware navigation"
```

---

### Task 6: Login page

**Files:**
- Create: `dashboard/src/views/Login.vue`

- [ ] **Step 1: Build login page**

Dual-tab card: "密码登录" tab with username/password form calling auth store `login()`, "钉钉扫码" tab with placeholder text ("钉钉登录将在后续版本开放"). On successful login, redirect to `/`.

- [ ] **Step 2: Commit**

```bash
git add dashboard/ && git commit -m "feat: login page with password form + DingTalk placeholder"
```

---

### Task 7: Placeholder view pages

**Files:**
- Create: `dashboard/src/views/Dashboard.vue`
- Create: `dashboard/src/views/Recording.vue`
- Create: `dashboard/src/views/SopList.vue`
- Create: `dashboard/src/views/SopEditor.vue`
- Create: `dashboard/src/views/Analytics.vue`
- Create: `dashboard/src/views/Audit.vue`
- Create: `dashboard/src/views/UserManagement.vue`
- Create: `dashboard/src/views/Settings.vue`

Each page shows its name as a heading + "Coming in Phase N" subtitle. Just enough to verify routing works.

- [ ] **Step 1: Create all 8 placeholder pages**
- [ ] **Step 2: Verify full navigation works (dev server)**
- [ ] **Step 3: Commit**

```bash
git add dashboard/ && git commit -m "feat: placeholder pages for all dashboard views"
```

---

### Task 8: FastAPI static file serving for production

**Files:**
- Modify: `server/app.py`

- [ ] **Step 1: Add conditional StaticFiles mount**

At the bottom of `server/app.py`, add static file serving that only activates when the `dashboard/dist/` directory exists (i.e. after `npm run build`):

```python
from pathlib import Path
_dashboard_dist = Path(__file__).resolve().parent.parent / "dashboard" / "dist"
if _dashboard_dist.is_dir():
    from starlette.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_dashboard_dist), html=True), name="dashboard")
```

- [ ] **Step 2: Build frontend and verify**

```bash
cd dashboard && npm run build
cd .. && uvicorn server.app:app --port 8000
# Open http://localhost:8000 — should show the Vue app
```

- [ ] **Step 3: Commit**

```bash
git add server/app.py && git commit -m "feat: serve Vue dashboard from FastAPI in production"
```

---

## Phase 2 Completion Criteria

1. `npm run dev` in `dashboard/` starts Vite dev server on :5173
2. Vite proxies `/api/*` to FastAPI :8000
3. Login page with password form → calls `/api/auth/login` → stores JWT → redirects to `/`
4. Sidebar shows role-appropriate menu items
5. Navigation guards block unauthorized routes (e.g. employee can't access /users)
6. All 8 pages render (as placeholders)
7. `npm run build` + `uvicorn server.app:app` serves the SPA from FastAPI
8. Existing backend tests still pass (159+)
