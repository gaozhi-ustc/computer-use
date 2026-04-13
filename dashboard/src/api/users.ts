import client from './client'
import type { UserInfo } from './auth'

export interface UserListResponse {
  total: number
  users: UserInfo[]
}

export interface UserUpdate {
  display_name?: string
  role?: string
  employee_id?: string
  department?: string
  department_id?: string
  is_active?: boolean
  password?: string
}

export const usersApi = {
  list: (params?: { role?: string; limit?: number; offset?: number }) =>
    client.get<UserListResponse>('/api/users/', { params }),

  update: (userId: number, data: UserUpdate) =>
    client.put<UserInfo>(`/api/users/${userId}`, data),
}
