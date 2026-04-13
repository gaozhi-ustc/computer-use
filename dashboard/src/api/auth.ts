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
