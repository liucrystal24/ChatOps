import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import DocumentsPanel from '../src/DocumentsPanel.vue'
import { login, logout } from '../src/api/client'

const kbId = '22222222-2222-4222-8222-222222222222'
const job = { id: 'job-1', document_id: 'doc-1', kind: 'index', status: 'queued', stage: 'queued', attempt_count: 0, max_attempts: 3, progress_completed: 0, progress_total: null, error: null }
const document = { id: 'doc-1', kb_id: kbId, filename: '培训.txt', file_type: 'txt', size_bytes: 200, tags: [], availability: 'unavailable', active_index_version_id: null, latest_job: job }
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
afterEach(() => { logout(); vi.useRealTimers(); vi.unstubAllGlobals() })

async function setup(role: 'owner' | 'viewer' = 'owner', initialDocs: unknown[] = []) {
  let docs = initialDocs
  let task = { ...job }
  const fetchMock = vi.fn((path: string, options: RequestInit) => {
    if (path.endsWith('/auth/login')) return Promise.resolve(response({ access_token: 'test-token' }))
    if (path.endsWith('/auth/me')) return Promise.resolve(response({ id: 'user-1', username: 'alice', is_admin: false }))
    if (path.endsWith('/documents') && options.method === 'POST') {
      docs = [document]; return Promise.resolve(response({ document, job }, 202))
    }
    if (path.endsWith('/documents')) return Promise.resolve(response({ items: docs, next_cursor: null }))
    if (path.endsWith('/jobs')) return Promise.resolve(response({ items: docs.length ? [task] : [], next_cursor: null }))
    if (path.endsWith('/jobs/job-1')) return Promise.resolve(response(task))
    return Promise.resolve(response({ items: [], next_cursor: null }))
  })
  vi.stubGlobal('fetch', fetchMock)
  await login('alice', 'private-password')
  const wrapper = mount(DocumentsPanel, { props: { kbId, role } })
  await flushPromises()
  return { wrapper, fetchMock, complete: () => { task = { ...job, status: 'succeeded', stage: 'publish', attempt_count: 1 }; docs = [{ ...document, availability: 'searchable', active_index_version_id: 'version-1', latest_job: task }] } }
}

describe('文档与任务', () => {
  it('multipart 上传携带内存 token；轮询到终态停止，成功后显示真实可检索状态', async () => {
    vi.useFakeTimers()
    const { wrapper, fetchMock, complete } = await setup()
    const input = wrapper.get('input[type=file]')
    Object.defineProperty(input.element, 'files', { value: [new File(['真实正文'], '培训.txt', { type: 'text/plain' })] })
    await input.trigger('change'); await wrapper.get('form').trigger('submit'); await flushPromises()
    const uploadCall = fetchMock.mock.calls.find(([path, options]) => path.endsWith('/documents') && options.method === 'POST')!
    expect(uploadCall[1].body).toBeInstanceOf(FormData)
    expect(new Headers(uploadCall[1].headers).get('Content-Type')).toBeNull()
    expect(new Headers(uploadCall[1].headers).get('Authorization')).toBe('Bearer test-token')
    expect(wrapper.text()).toContain('尚不可检索')
    complete(); await vi.advanceTimersByTimeAsync(2000); await flushPromises()
    expect(wrapper.text()).toContain('可检索')
    const calls = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetchMock.mock.calls.length).toBe(calls)
    wrapper.unmount()
  })

  it('viewer 读取来源但没有上传、重建、修改、删除或重试操作', async () => {
    const { wrapper } = await setup('viewer', [{ ...document, latest_job: { ...job, status: 'failed', error: { code: 'DOCUMENT_TEXT_EMPTY', message: '扫描 PDF 暂不支持 OCR' } } }])
    expect(wrapper.find('input[type=file]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('重建索引')
    expect(wrapper.text()).not.toContain('删除文档')
    await wrapper.findAll('button').find(b => b.text() === '查看来源')!.trigger('click'); await flushPromises()
    expect(wrapper.find('[aria-label="来源正文"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="文档展示名称"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('失败显示服务端原因，重试冲突保留任务记录；卸载停止轮询', async () => {
    vi.useFakeTimers()
    const { wrapper, fetchMock } = await setup('owner', [document])
    const failed = { ...job, status: 'failed', error: { code: 'EMBEDDING_REQUEST_REJECTED', message: '请检查模型凭据' } }
    fetchMock.mockImplementationOnce(() => Promise.resolve(response({ items: [document], next_cursor: null })))
    fetchMock.mockImplementationOnce(() => Promise.resolve(response({ items: [failed], next_cursor: null })))
    await wrapper.findAll('button').find(b => b.text() === '刷新文档')!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('请检查模型凭据')
    fetchMock.mockImplementationOnce(() => Promise.resolve(response({ error: { code: 'JOB_NOT_RETRYABLE', message: '此任务已有重试后继' } }, 409)))
    await wrapper.findAll('button').find(b => b.text() === '重试任务')!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('此任务已有重试后继')
    expect(wrapper.text()).toContain('请检查模型凭据')
    wrapper.unmount(); const count = fetchMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(6000)
    expect(fetchMock.mock.calls.length).toBe(count)
  })
})
