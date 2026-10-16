export type CheckStatus = 'ok' | 'unavailable' | 'pending'
export interface ReadyReport {
  status: 'ok' | 'unavailable'
  checks: Record<'mysql' | 'qdrant' | 'migrations', { status: CheckStatus }>
  capabilities: { rag_configured: boolean; rag_available: boolean }
}
export interface Snapshot {
  api: 'ok' | 'unavailable'
  report: ReadyReport | null
}

function isReport(value: unknown): value is ReadyReport {
  if (!value || typeof value !== 'object') return false
  const report = value as Partial<ReadyReport>
  return (
    ['ok', 'unavailable'].includes(report.status ?? '') &&
    ['mysql', 'qdrant', 'migrations'].every((key) => {
      const status = report.checks?.[key as keyof ReadyReport['checks']]?.status
      return status !== undefined && ['ok', 'unavailable', 'pending'].includes(status)
    }) &&
    typeof report.capabilities?.rag_configured === 'boolean' &&
    typeof report.capabilities?.rag_available === 'boolean'
  )
}

export async function fetchHealth(signal: AbortSignal): Promise<Snapshot> {
  const [live, ready] = await Promise.allSettled([
    fetch('/api/health/live', { signal, cache: 'no-store' }).then(async (response) => {
      const data: unknown = await response.json()
      return response.ok && (data as { status?: string } | null)?.status === 'ok'
    }),
    fetch('/api/health/ready', { signal, cache: 'no-store' }).then(async (response) => {
      // 503 的依赖报告仍有意义；代理错误或不符契约的响应不能展示为健康。
      if (response.status !== 200 && response.status !== 503) return null
      const data: unknown = await response.json()
      if (!isReport(data)) return null
      if ((response.status === 200) !== (data.status === 'ok')) return null
      return data
    }),
  ])
  return {
    api: live.status === 'fulfilled' && live.value ? 'ok' : 'unavailable',
    report: ready.status === 'fulfilled' ? ready.value : null,
  }
}
