import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import App from '../src/App.vue'
import { ApiError, currentUser, login, logout, request } from '../src/api/client'

const user = { id: '11111111-1111-4111-8111-111111111111', username: 'alice', is_admin: false }
const kb = { id: '22222222-2222-4222-8222-222222222222', name: '真实接口库', description: '团队资料', my_role: 'viewer', embedding_profile_id: null, created_at: '2026-10-15T00:00:00Z', updated_at: '2026-10-15T00:00:00Z' }
function response(data: unknown, status = 200) { return new Response(JSON.stringify(data), { status }) }

afterEach(() => { logout(); vi.unstubAllGlobals() })

function mockApi(role: 'viewer' | 'owner' = 'viewer') {
  const fetchMock = vi.fn((path: string, options: RequestInit) => {
    if (path.endsWith('/auth/login')) return Promise.resolve(response({ access_token: 'test-memory-token' }))
    expect(new Headers(options.headers).get('Authorization')).toBe('Bearer test-memory-token')
    if (path.endsWith('/auth/me')) return Promise.resolve(response(user))
    if (path.endsWith('/members')) return Promise.resolve(response({ items: [{ user_id: user.id, username: user.username, role: 'owner', is_active: true }], next_cursor: null }))
    if (path === '/api/v1/knowledge-bases') return Promise.resolve(response({ items: [{ ...kb, my_role: role }], next_cursor: null }))
    return Promise.resolve(response({ ...kb, my_role: role }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('会话与权限页面', () => {
  it('登录后读取真实列表，viewer 不显示成员管理；退出清空页面和存储', async () => {
    const fetchMock = mockApi()
    const wrapper = mount(App)
    await wrapper.get('input[name="username"]').setValue('alice')
    const password = '🔐'.repeat(200)
    expect(wrapper.get('input[name="password"]').attributes('maxlength')).toBe('512')
    await wrapper.get('input[name="password"]').setValue(password)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('我的知识库')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string).password).toBe(password)
    await wrapper.get('.library-item').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('真实接口库')
    expect(wrapper.text()).not.toContain('添加或更新成员')
    expect(fetchMock.mock.calls.some(([path]) => path.endsWith('/members'))).toBe(false)
    const exit = wrapper.findAll('button').find((item) => item.text() === '退出')!
    await exit.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('登录工作空间')
    expect(wrapper.text()).not.toContain('真实接口库')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    wrapper.unmount()
  })

  it('owner 看到成员操作，服务端最后 owner 拒绝后保留详情并展示安全错误', async () => {
    const fetchMock = mockApi('owner')
    await login('alice', 'private-password')
    const wrapper = mount(App)
    await flushPromises()
    await wrapper.get('.library-item').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('添加或更新成员')
    fetchMock.mockImplementationOnce(() => Promise.resolve(response({ error: { code: 'LAST_OWNER_REQUIRED', message: '至少保留一名有效 owner' } }, 409)))
    const remove = wrapper.findAll('button').find((item) => item.text() === '移除')!
    await remove.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('至少保留一名有效 owner')
    expect(wrapper.text()).toContain('真实接口库')
    wrapper.unmount()
  })

  it('401 清除会话，旧账号的迟到响应不能重新填充状态', async () => {
    mockApi()
    await login('alice', 'private-password')
    let resolveOld!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { resolveOld = resolve })))
    const old = request('/knowledge-bases')
    const rejected = expect(old).rejects.toMatchObject({ code: 'REQUEST_CANCELLED' })
    logout()
    resolveOld(response({ items: [kb], next_cursor: null }))
    await rejected
    expect(currentUser.value).toBeNull()
    mockApi()
    await login('alice', 'private-password')
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({ error: { code: 'USER_DISABLED', message: '账号已停用' } }, 401))))
    await expect(request('/auth/me')).rejects.toBeInstanceOf(ApiError)
    expect(currentUser.value).toBeNull()
  })
})
