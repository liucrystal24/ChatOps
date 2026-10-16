import { request, type Page } from './client'
export interface Conversation { id: string; kb_id: string; title: string; active_message_id: string | null }
export interface Source {
  source_label: string; chunk_id: string; document_id: string; index_version_id: string
  filename: string; page_no: number | null; section_title: string | null; excerpt: string; retrieval_score: number
}
export interface Citation {
  source_label: string; chunk_id: string; document_id: string; available: boolean
  filename: string | null; page_no: number | null; section_title: string | null
}
export interface Message {
  id: string; conversation_id: string; sequence_no: number; role: 'user' | 'assistant'
  status: 'completed' | 'generating' | 'failed' | 'cancelled' | 'interrupted'
  content: string; answer_type: 'answer' | 'abstention' | null; error_code: string | null; citations: Citation[]
}
export const chatApi = {
  conversations: (kb: string, cursor = '') => request<Page<Conversation>>(`/conversations?kb_id=${kb}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`),
  create: (kb: string, title: string) => request<Conversation>('/conversations', { method: 'POST', body: JSON.stringify({ kb_id: kb, title }) }),
  messages: (id: string, cursor = '') => request<Page<Message>>(`/conversations/${id}/messages?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`),
  message: (id: string) => request<Message>(`/messages/${id}`),
}
