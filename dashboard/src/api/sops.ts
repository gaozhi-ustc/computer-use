import client from './client'

export interface SopInfo {
  id: number
  title: string
  description: string
  status: 'draft' | 'in_review' | 'published' | 'regenerating'
  created_by: string
  assigned_reviewer: string | null
  source_session_id: string | null
  source_employee_id: string | null
  tags: string[]
  created_at: string
  updated_at: string
  published_at: string | null
  step_count?: number
}

export interface StepInfo {
  id: number
  sop_id: number
  step_order: number
  title: string
  description: string
  application: string
  action_type: string
  action_detail: Record<string, unknown>
  screenshot_ref: string
  source_frame_ids: number[]
  confidence: number
  created_at: string
  updated_at: string
  human_description?: string
  machine_actions?: Array<{
    type: string
    x?: number
    y?: number
    target?: string
    text?: string
    key?: string
  }>
  revision?: number
}

export interface SopDetail extends SopInfo {
  steps: StepInfo[]
}

export interface SopFeedbackResponse {
  feedback_id: number
  new_revision: number
  status: string
}

export interface SopStatusResponse {
  status: string
  revision: number
}

export interface SopRevision {
  id: number
  sop_id: number
  revision: number
  steps_snapshot_json: string
  feedback_id: number | null
  created_at: string
}

export interface SopListResponse {
  total: number
  count: number
  sops: SopInfo[]
}

export const sopsApi = {
  list: (params?: { status?: string; limit?: number; offset?: number }) =>
    client.get<SopListResponse>('/api/sops/', { params }),

  detail: (id: number) =>
    client.get<SopDetail>(`/api/sops/${id}`),

  create: (data: { title: string; description?: string; source_session_id?: string; source_employee_id?: string }) =>
    client.post<SopInfo>('/api/sops/', data),

  update: (id: number, data: { title?: string; description?: string; tags?: string[] }) =>
    client.put<SopInfo>(`/api/sops/${id}`, data),

  delete: (id: number) =>
    client.delete(`/api/sops/${id}`),

  updateStatus: (id: number, data: { status: string }) =>
    client.put<SopInfo>(`/api/sops/${id}/status`, data),

  generate: (id: number) =>
    client.post<{ steps_created: number }>(`/api/sops/${id}/generate`),

  exportMd: (id: number) =>
    client.get<string>(`/api/sops/${id}/export/md`, { responseType: 'text' }),

  exportJson: (id: number) =>
    client.get(`/api/sops/${id}/export/json`),

  // Steps
  addStep: (sopId: number, data: { title: string; description?: string; application?: string; action_type?: string; step_order?: number }) =>
    client.post<StepInfo>(`/api/sops/${sopId}/steps/`, data),

  updateStep: (sopId: number, stepId: number, data: Partial<StepInfo>) =>
    client.put<StepInfo>(`/api/sops/${sopId}/steps/${stepId}`, data),

  deleteStep: (sopId: number, stepId: number) =>
    client.delete(`/api/sops/${sopId}/steps/${stepId}`),

  reorderSteps: (sopId: number, stepIds: number[]) =>
    client.put(`/api/sops/${sopId}/steps/reorder`, { step_ids: stepIds }),

  getStatus: (sopId: number) =>
    client.get<SopStatusResponse>(`/api/sops/${sopId}/status`),

  submitFeedback: (sopId: number, body: { feedback_text: string; scope: string }) =>
    client.post<SopFeedbackResponse>(`/api/sops/${sopId}/feedback`, body),

  listRevisions: (sopId: number) =>
    client.get<SopRevision[]>(`/api/sops/${sopId}/revisions`),

  getRevision: (sopId: number, rev: number) =>
    client.get<SopRevision>(`/api/sops/${sopId}/revisions/${rev}`),

  restoreRevision: (sopId: number, rev: number) =>
    client.post<{ ok: boolean; revision: number }>(`/api/sops/${sopId}/revisions/${rev}/restore`),
}
