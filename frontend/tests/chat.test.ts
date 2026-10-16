import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { parseSse } from '../src/api/sse'
import { renderMarkdown } from '../src/markdown'
import ChatPanel from '../src/ChatPanel.vue'
import { login, logout } from '../src/api/client'

const wire = (name: string, id: number, data: unknown) => `event: ${name}\r\nid: ${id}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`
async function* bytes(value: string, size = 1) { const encoded = new TextEncoder().encode(value); for (let i=0;i<encoded.length;i+=size) yield encoded.slice(i,i+size) }
const collect = async (text: string, size=1) => { const result=[]; for await (const item of parseSse(bytes(text,size))) result.push(item); return result }
afterEach(() => { logout(); vi.unstubAllGlobals() })

describe('流式问答', () => {
  it('逐字节 UTF-8、CRLF、转义、多事件与心跳解析正确', async () => {
    const text = ': ping\r\n\r\n'+wire('start',1,{message_id:'1'})+wire('delta',2,{text:'中文\n"引号"'})+wire('done',3,{status:'completed'})
    const events = await collect(text)
    expect(events.map(e=>e.event)).toEqual(['start','delta','done'])
    expect(events[1]!.data.text).toBe('中文\n"引号"')
    expect(await collect(text,100000)).toEqual(events)
    expect(await collect(text.replaceAll('\r\n','\r'))).toEqual(events)
  })
  it('断流、序号重复与完成后数据不能当成功', async () => {
    await expect(collect(wire('start',1,{}))).rejects.toThrow('中断')
    await expect(collect(wire('start',1,{})+wire('done',1,{}))).rejects.toThrow('序号')
    await expect(collect(wire('done',1,{})+wire('delta',2,{text:'late'}))).rejects.toThrow('终止')
  })
  it('Markdown 转义 HTML、过滤危险协议且不加载外部图片', () => {
    const html = renderMarkdown('<script>alert(1)</script>\n[危险](javascript:alert(1)) ![图](https://evil.invalid/private)\n[合法](https://example.com)\n**粗体**')
    expect(html).not.toContain('<script>'); expect(html).not.toContain('href="javascript:')
    expect(html).not.toContain('<img'); expect(html).toContain('<strong>粗体</strong>')
    expect(html).toContain('rel="noopener noreferrer"')
  })

  async function setup(complete = true, earlyError = false, stall = false) {
    const conversation={id:'conv-1',kb_id:'kb-1',title:'培训',active_message_id:null}
    const stored={id:'answer-1',conversation_id:'conv-1',sequence_no:2,role:'assistant',status:earlyError?'failed':complete?'completed':'cancelled',content:'培训周二举行 [S1]',answer_type:complete?'answer':null,error_code:null,citations:complete&&!earlyError?[{source_label:'S1',chunk_id:'chunk-1',available:true,filename:'培训.txt',page_no:null,section_title:null}]:[]}
    const json=(data: unknown,status=200)=>new Response(JSON.stringify(data),{status})
    const fetchMock=vi.fn(async (path: string, init: RequestInit) => {
      if (path.endsWith('/auth/login')) return json({access_token:'test-memory-token'})
      if (path.endsWith('/auth/me')) return json({id:'user-1',username:'alice',is_admin:false})
      if (path.includes('/messages:stream')) {
        const text=earlyError?wire('error',1,{code:'UPSTREAM_TIMEOUT',message:'模型响应超时',message_id:'answer-1'}):wire('start',1,{message_id:'answer-1',user_message_id:'user-msg-1'})+wire('sources',2,{candidates:[{source_label:'S1',filename:'培训.txt',chunk_id:'chunk-1',excerpt:'原始证据'}]})+wire('delta',3,{text:'培训周二举行 [S1]'})+(complete?wire('done',4,{message_id:'answer-1',status:'completed'}):'')
        return new Response(new ReadableStream({ start(controller) { for (const b of new TextEncoder().encode(text)) controller.enqueue(new Uint8Array([b])); if (stall) init.signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted','AbortError')), {once:true}); else controller.close() } }),{headers:{'Content-Type':'text/event-stream'}})
      }
      if (path.endsWith('/messages/answer-1')) return json(stored)
      if (path.includes('/conversations?')) return json({items:[conversation],next_cursor:null})
      if (path.endsWith('/conversations') && init.method==='POST') return json(conversation,201)
      if (path.includes('/messages?')) return json({items:[],next_cursor:null})
      if (path.endsWith('/chunks/chunk-1')) return json({filename:'培训.txt',text:'周二培训的原始证据',page_no:null})
      return json({})
    })
    vi.stubGlobal('fetch',fetchMock); await login('alice','test-private-password')
    const wrapper=mount(ChatPanel,{props:{kbId:'kb-1'}}); await flushPromises()
    return {wrapper,fetchMock}
  }
  it('完成后核验已保存消息和最终引用，来源走鉴权接口', async () => {
    const {wrapper,fetchMock}=await setup()
    await wrapper.get('textarea').setValue('什么时候培训？'); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('已保存')
    expect(wrapper.find('[aria-label="最终引用"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('尚未确认引用')
    const call=fetchMock.mock.calls.find(([p])=>p.includes('messages:stream'))!
    expect(new Headers(call[1].headers).get('Authorization')).toBe('Bearer test-memory-token')
    await wrapper.findAll('button').find(b=>b.text().includes('[S1]'))!.trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('周二培训的原始证据')
    wrapper.unmount()
  })
  it('EOF 核验取消草稿且不重放 POST', async () => {
    const {wrapper,fetchMock}=await setup(false)
    await wrapper.get('textarea').setValue('问题'); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('已停止 · 草稿')
    expect(fetchMock.mock.calls.filter(([p])=>p.includes('messages:stream'))).toHaveLength(1)
    expect(wrapper.find('[aria-label="最终引用"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('开始事件前的 SSE error 仍显示原因并核验消息', async () => {
    const {wrapper}=await setup(false,true)
    await wrapper.get('textarea').setValue('问题'); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('模型响应超时'); expect(wrapper.text()).toContain('失败 · 草稿')
    wrapper.unmount()
  })
  it('卸载中止流请求，不重放 POST 或写回旧视图', async () => {
    const {wrapper,fetchMock}=await setup(false,false,true)
    await wrapper.get('textarea').setValue('问题'); await wrapper.get('form').trigger('submit'); await flushPromises()
    const call=fetchMock.mock.calls.find(([p])=>p.includes('messages:stream'))!
    expect(call[1].signal?.aborted).toBe(false)
    wrapper.unmount(); await flushPromises()
    expect(call[1].signal?.aborted).toBe(true)
    expect(fetchMock.mock.calls.filter(([p])=>p.includes('messages:stream'))).toHaveLength(1)
  })

})
