import client from './client'

export interface QueueStats {
  pending: number
  running: number
  failed: number
  done: number
}

export const framesApi = {
  /** URL for the <img> tag to fetch the raw PNG. Axios interceptor adds JWT. */
  imageUrl: (frameId: number): string => `/api/frames/${frameId}/image`,

  /** Admin: reset a failed frame back to pending. */
  retry: (frameId: number) =>
    client.post<{ ok: boolean }>(`/api/frames/${frameId}/retry`),

  /** Admin: snapshot of pending/running/failed/done counts. */
  queueStatus: () => client.get<QueueStats>('/api/frames/queue'),
}
