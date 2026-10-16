export interface StreamEvent { event: string; id: string; data: Record<string, unknown> }

// 网络块不是事件；同时处理 UTF-8 半字、CRLF 半行和多行 data。
export async function* parseSse(chunks: AsyncIterable<Uint8Array>): AsyncGenerator<StreamEvent> {
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let buffer = '', name = '', id = '', lines: string[] = [], lastId = 0, terminal = false
  function line(value: string): StreamEvent | null {
    if (!value) {
      if (!lines.length) { name = ''; id = ''; return null }
      if (terminal) throw new Error('终止事件之后仍有数据')
      const number = Number(id)
      if (!/^\d+$/.test(id) || number !== lastId + 1) throw new Error('流事件序号无效')
      lastId = number
      const data: unknown = JSON.parse(lines.join('\n'))
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('流事件格式无效')
      if (!['start', 'sources', 'delta', 'done', 'error'].includes(name)) throw new Error('未知流事件')
      const result = { event: name, id, data: data as Record<string, unknown> }
      terminal = name === 'done' || name === 'error'
      name = ''; id = ''; lines = []
      return result
    }
    if (value.startsWith(':')) return null
    const colon = value.indexOf(':')
    const key = colon < 0 ? value : value.slice(0, colon)
    let content = colon < 0 ? '' : value.slice(colon + 1)
    if (content.startsWith(' ')) content = content.slice(1)
    if (key === 'event') name = content
    if (key === 'id') id = content
    if (key === 'data') lines.push(content)
    return null
  }
  for await (const chunk of chunks) {
    buffer += decoder.decode(chunk, { stream: true })
    if (buffer.length + lines.reduce((n, l) => n + l.length, 0) > 1_000_000) throw new Error('流事件过长')
    while (true) {
      const match = /\r\n|\r|\n/.exec(buffer)
      if (!match || (match[0] === '\r' && match.index === buffer.length - 1)) break
      const value = buffer.slice(0, match.index)
      buffer = buffer.slice(match.index + match[0].length)
      const event = line(value)
      if (event) yield event
    }
  }
  buffer += decoder.decode()
  while (true) {
    const match = /\r\n|\r|\n/.exec(buffer)
    if (!match) break
    const value = buffer.slice(0, match.index)
    buffer = buffer.slice(match.index + match[0].length)
    const event = line(value)
    if (event) yield event
  }
  if (buffer.trim() || lines.length || !terminal) throw new Error('连接已中断，正在核验消息状态')
}
