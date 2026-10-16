import { describe, expect, it, vi, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import App from '../src/HealthStatus.vue'
import { fetchHealth } from '../src/api/health'

const report = {
  status: 'ok',
  checks: { mysql: { status: 'ok' }, qdrant: { status: 'ok' }, migrations: { status: 'ok' } },
  capabilities: { rag_configured: false, rag_available: false },
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

function mockFetch(ready: unknown, status = 200) {
  vi.stubGlobal('fetch', vi.fn((path: string) => Promise.resolve(
    new Response(JSON.stringify(path.endsWith('/live') ? { status: 'ok' } : ready), { status: path.endsWith('/live') ? 200 : status }),
  )))
}

describe('真实健康状态展示', () => {
  it('读取 503 报告，区分 API 存活和依赖不可用', async () => {
    mockFetch({ ...report, status: 'unavailable', checks: { ...report.checks, mysql: { status: 'unavailable' } } }, 503)
    const state = await fetchHealth(new AbortController().signal)
    expect(state.api).toBe('ok')
    expect(state.report?.checks.mysql.status).toBe('unavailable')
  })

  it('网络失败和无效响应不能沿用健康状态', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network failure')))
    expect(await fetchHealth(new AbortController().signal)).toEqual({ api: 'unavailable', report: null })
    mockFetch({ status: 'ok' })
    expect((await fetchHealth(new AbortController().signal)).report).toBeNull()
    mockFetch(report, 503)
    expect((await fetchHealth(new AbortController().signal)).report).toBeNull()
  })

  it('自动检查从正常切换为故障，并在卸载后停止请求', async () => {
    vi.useFakeTimers()
    mockFetch(report)
    const wrapper = mount(App)
    await flushPromises()
    expect(wrapper.text()).toContain('基础服务已就绪')
    mockFetch({ ...report, status: 'unavailable', checks: { ...report.checks, qdrant: { status: 'unavailable' } } }, 503)
    await vi.advanceTimersByTimeAsync(10000)
    await flushPromises()
    expect(wrapper.text()).toContain('基础服务尚未就绪')
    expect(wrapper.text()).toContain('问答配置未就绪')
    const fetchMock = vi.mocked(fetch)
    const count = fetchMock.mock.calls.length
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(20000)
    expect(fetchMock.mock.calls).toHaveLength(count)
  })
})
