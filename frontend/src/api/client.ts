import { shallowRef } from 'vue'

export interface User { id: string; username: string; is_admin: boolean }
export type Role = 'owner' | 'editor' | 'viewer'
export interface KnowledgeBase {
  id: string; name: string; description: string; my_role: Role
  embedding_profile_id: string | null; created_at: string; updated_at: string
}
export interface Member {
  user_id: string; username: string; role: Role; is_active: boolean
  created_at: string; updated_at: string
}
export interface Page<T> { items: T[]; next_cursor: string | null }

export const currentUser = shallowRef<User | null>(null)
export const sessionNotice = shallowRef('')
let token = ''
let generation = 0
let sessionController = new AbortController()

export class ApiError extends Error {
  constructor(public code: string, message: string, public status = 0, public requestId = '', public details: Record<string, unknown> = {}) {
    super(message)
  }
}

export function logout(notice = '') {
  generation += 1
  sessionController.abort()
  sessionController = new AbortController()
  token = ''
  currentUser.value = null
  sessionNotice.value = notice
}

export function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作失败，请稍后重试'
}

export async function request<T>(path: string, init: RequestInit = {}, format: 'json' | 'blob' = 'json'): Promise<T> {
  const epoch = generation
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  try {
    const response = await fetch(`/api/v1${path}`, {
      ...init, headers, cache: 'no-store',
      signal: AbortSignal.any([sessionController.signal, AbortSignal.timeout(15000), ...(init.signal ? [init.signal] : [])]),
    })
    if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
      if (response.status === 401) logout('会话已失效，请重新登录')
      throw new ApiError(data?.error?.code ?? 'HTTP_ERROR', data?.error?.message ?? '服务暂时无法处理请求', response.status, data?.error?.request_id ?? '')
    }
    if (response.status === 204) return undefined as T
    const data = (format === 'blob' ? await response.blob() : await response.json()) as T
    if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
    return data
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
    throw new ApiError('NETWORK_ERROR', '无法连接服务或请求已超时，请重试')
  }
}

export async function login(username: string, password: string): Promise<void> {
  logout()
  const epoch = generation
  const result = await request<{ access_token: string }>('/auth/login', {
    method: 'POST', body: JSON.stringify({ username, password }),
  })
  if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
  token = result.access_token
  try {
    const user = await request<User>('/auth/me')
    if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
    currentUser.value = user
  } catch (error) {
    if (epoch === generation) logout()
    throw error
  }
}

export async function* streamRequest(path: string, body: unknown, signal: AbortSignal): AsyncGenerator<Uint8Array> {
  const epoch = generation
  const combined = AbortSignal.any([signal, sessionController.signal, AbortSignal.timeout(130000)])
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    const response = await fetch(`/api/v1${path}`, {
      method: 'POST', body: JSON.stringify(body), cache: 'no-store', signal: combined,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    })
    if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
      if (response.status === 401) logout('会话已失效，请重新登录')
      throw new ApiError(data?.error?.code ?? 'HTTP_ERROR', data?.error?.message ?? '问答请求失败', response.status, data?.error?.request_id ?? '', data?.error?.details ?? {})
    }
    if (!response.headers.get('content-type')?.startsWith('text/event-stream') || !response.body) throw new ApiError('INVALID_STREAM', '服务未返回问答流')
    reader = response.body.getReader()
    while (true) {
      const { value, done } = await reader.read()
      if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
      if (done) break
      yield value
    }
  } finally {
    await reader?.cancel().catch(() => undefined)
    reader?.releaseLock()
  }
}
