export interface Job {
  id: string; document_id: string; kind: 'index' | 'purge'
  status: 'queued' | 'running' | 'retry_wait' | 'succeeded' | 'failed' | 'cancelled'
  stage: string; attempt_count: number; max_attempts: number
  progress_completed: number; progress_total: number | null
  error: { code: string; message: string } | null
}
export interface Document {
  id: string; kb_id: string; filename: string; file_type: 'txt' | 'md' | 'pdf'
  size_bytes: number; tags: string[]; availability: 'searchable' | 'unavailable'
  active_index_version_id: string | null; latest_job: Job | null
}
export interface Chunk {
  id: string; document_id: string; index_version_id: string; filename: string
  text: string; page_no: number | null; section_title: string | null
  char_start: number; char_end: number
}
export const active = (job: Job) => ['queued', 'running', 'retry_wait'].includes(job.status)
