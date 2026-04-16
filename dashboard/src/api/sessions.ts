import client from './client'

export interface SessionInfo {
  session_id: string
  employee_id: string
  first_frame_at: string
  last_frame_at: string
  frame_count: number
  applications: string[]
  status?: string
}

export interface SessionListResponse {
  total: number
  count: number
  sessions: SessionInfo[]
}

export type AnalysisStatus = 'pending' | 'running' | 'done' | 'failed'

export interface FrameInfo {
  id: number
  employee_id: string
  session_id: string
  frame_index: number
  recorded_at: string
  received_at: string
  application: string | null
  window_title: string | null
  user_action: string | null
  text_content: string | null
  confidence: number
  mouse_position: number[]
  ui_elements: Array<{ name: string; element_type: string; coordinates: number[] }>
  context_data: Record<string, unknown>
  // v0.4.0 offline-analysis fields
  image_path?: string
  analysis_status?: AnalysisStatus
  analysis_error?: string
  cursor_x?: number  // -1 if OS capture unavailable
  cursor_y?: number
  focus_rect?: number[] | null  // [x1, y1, x2, y2] in image pixels
  // v0.5.x group-analysis enrichment
  group_indices?: number[]  // which frame_groups contain this frame
  sop_steps?: Array<{
    sop_id: number
    step_order: number
    title: string
    application: string
  }>
  // v0.5.2 server-side pre-analysis filter
  skip_reason?: '' | 'near_duplicate' | 'low_signal' | string
}

export interface SessionDetail {
  session_id: string
  employee_id: string
  frame_count: number
  frames: FrameInfo[]
}

export const sessionsApi = {
  list: (params?: { employee_id?: string; date_from?: string; date_to?: string; limit?: number; offset?: number }) =>
    client.get<SessionListResponse>('/api/sessions/', { params }),
  detail: (sessionId: string) =>
    client.get<SessionDetail>(`/api/sessions/${sessionId}`),
  analyze: (sessionId: string) =>
    client.post<{ ok: boolean; session_id: string; status: string }>(`/api/sessions/${sessionId}/analyze`),
}
