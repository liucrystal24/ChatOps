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
  constructor(public code: string, message: string, public status = 0, public requestId = '') {
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

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const epoch = generation
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body) headers.set('Content-Type', 'application/json')
  try {
    const response = await fetch(`/api/v1${path}`, {
      ...init, headers, cache: 'no-store',
      signal: AbortSignal.any([sessionController.signal, AbortSignal.timeout(15000)]),
    })
    if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      if (epoch !== generation) throw new ApiError('REQUEST_CANCELLED', '请求已取消')
      if (response.status === 401) logout('会话已失效，请重新登录')
      throw new ApiError(data?.error?.code ?? 'HTTP_ERROR', data?.error?.message ?? '服务暂时无法处理请求', response.status, data?.error?.request_id ?? '')
    }
    if (response.status === 204) return undefined as T
    const data: T = await response.json()
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
