import client from './client'

export interface DashboardSummary {
  today_frames: number
  today_sessions: number
  draft_sops: number
  published_sops: number
  total_employees: number
}

export interface AppUsage {
  application: string | null
  frame_count: number
}

export interface HeatmapCell {
  hour: number
  weekday: number
  count: number
}

export interface DailyStats {
  date: string
  frame_count: number
  app_count: number
  first_at: string
  last_at: string
}

export interface FrameStats {
  app_usage: AppUsage[]
  heatmap: HeatmapCell[]
  daily: DailyStats[]
}

export interface SearchFrame {
  id: number
  employee_id: string
  session_id: string
  frame_index: number
  recorded_at: string
  application: string | null
  window_title: string | null
  user_action: string | null
  text_content: string | null
  confidence: number
}

export interface SearchResult {
  total: number
  count: number
  frames: SearchFrame[]
}

export const statsApi = {
  dashboardSummary: () =>
    client.get<DashboardSummary>('/api/dashboard/summary'),

  recentSessions: () =>
    client.get('/api/dashboard/recent-sessions'),

  frameStats: (params?: {
    employee_id?: string
    date_from?: string
    date_to?: string
  }) => client.get<FrameStats>('/api/frames/stats', { params }),

  searchFrames: (params?: {
    keyword?: string
    employee_id?: string
    application?: string
    date_from?: string
    date_to?: string
    min_confidence?: number
    limit?: number
    offset?: number
  }) => client.get<SearchResult>('/api/frames/search', { params }),

  exportCsv: (params?: {
    employee_id?: string
    date_from?: string
    date_to?: string
  }) =>
    client.get('/api/frames/export', {
      params,
      responseType: 'blob',
    }),
}
