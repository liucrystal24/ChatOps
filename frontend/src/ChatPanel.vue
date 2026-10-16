<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, errorMessage, request, streamRequest } from './api/client'
import { chatApi, type Citation, type Conversation, type Message, type Source } from './api/chat'
import { parseSse } from './api/sse'
import { renderMarkdown } from './markdown'

const props = defineProps<{ kbId: string }>()
const conversations = ref<Conversation[]>([]), selected = ref<Conversation | null>(null)
const messages = ref<Message[]>([]), candidates = ref<Source[]>([])
const conversationCursor = ref<string | null>(null), messageCursor = ref<string | null>(null)
const question = ref(''), error = ref(''), notice = ref(''), busy = ref(false), loading = ref(false)
const source = ref<{ filename: string; text: string; page_no: number | null } | null>(null)
let controller: AbortController | null = null, alive = true, epoch = 0
const labels = { completed: '已保存', generating: '生成中 · 草稿', failed: '失败 · 草稿', cancelled: '已停止 · 草稿', interrupted: '中断 · 草稿' }

function clearPrivate(cause: unknown) {
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
    messages.value = []; candidates.value = []; source.value = null; selected.value = null; conversations.value = []
  }
}
async function perform(action: () => Promise<void>) {
  if (loading.value || busy.value) return
  loading.value = true; error.value = ''; source.value = null
  try { await action() } catch (cause) { clearPrivate(cause); error.value = errorMessage(cause) }
  finally { if (alive) loading.value = false }
}
async function loadConversations(append = false) {
  const result = await chatApi.conversations(props.kbId, append ? conversationCursor.value ?? '' : '')
  if (!alive) return
  conversations.value = append ? [...conversations.value, ...result.items] : result.items
  conversationCursor.value = result.next_cursor
}
async function open(item: Conversation) {
  const result = await chatApi.messages(item.id)
  if (!alive) return
  selected.value = item; messages.value = result.items.sort((a, b) => a.sequence_no - b.sequence_no)
  messageCursor.value = result.next_cursor; candidates.value = []; source.value = null; notice.value = ''
}
async function older() {
  if (!selected.value || !messageCursor.value) return
  const result = await chatApi.messages(selected.value.id, messageCursor.value)
  if (!alive) return
  messages.value = [...result.items, ...messages.value].sort((a, b) => a.sequence_no - b.sequence_no)
  messageCursor.value = result.next_cursor
}
async function create() {
  const item = await chatApi.create(props.kbId, Array.from(question.value.trim()).slice(0, 120).join('') || '新对话')
  await loadConversations(); await open(item)
}
async function showSource(chunkId: string) {
  source.value = null
  try {
    const result = await request<{ filename: string; text: string; page_no: number | null }>(`/chunks/${chunkId}`)
    if (alive) source.value = result
  } catch (cause) { clearPrivate(cause); error.value = errorMessage(cause) }
}
async function verify(messageId: string, round: number) {
  if (!messageId || !alive || round !== epoch) return
  const stored = await chatApi.message(messageId)
  if (!alive || round !== epoch) return
  const index = messages.value.findIndex(m => m.id === messageId)
  if (index >= 0) messages.value[index] = stored
  else messages.value.push(stored)
  notice.value = stored.status === 'completed' ? '已核验：答案已保存' : `已核验：${labels[stored.status]}${stored.error_code ? `（${stored.error_code}）` : ''}`
}
async function send() {
  if (busy.value || loading.value || !question.value.trim()) return
  busy.value = true; error.value = ''; notice.value = ''; candidates.value = []; source.value = null
  const round = ++epoch, text = question.value.trim()
  let messageId = '', assistant: Message | null = null
  controller = new AbortController()
  try {
    if (!selected.value) await create()
    if (!alive) return
    const conversation = selected.value!
    let terminal = false
    for await (const event of parseSse(streamRequest(`/conversations/${conversation.id}/messages:stream`, {
      client_request_id: crypto.randomUUID(), question: text, retrieval: { strategy: 'dense', top_k: 5 },
    }, controller.signal))) {
      if (!alive || round !== epoch) return
      const data = event.data
      if (event.event === 'start') {
        if (messageId || typeof data.message_id !== 'string' || typeof data.user_message_id !== 'string') throw new Error('无效的开始事件')
        messageId = data.message_id
        const next = Math.max(0, ...messages.value.map(m => m.sequence_no)) + 1
        messages.value.push({ id: data.user_message_id, conversation_id: conversation.id, role: 'user', status: 'completed', content: text, sequence_no: next, citations: [], answer_type: null, error_code: null })
        assistant = { id: messageId, conversation_id: conversation.id, sequence_no: next + 1, role: 'assistant', status: 'generating', content: '', citations: [], answer_type: null, error_code: null }
        messages.value.push(assistant); assistant = messages.value[messages.value.length - 1]!
        question.value = ''
      } else if (event.event === 'error') {
        if (typeof data.code !== 'string' || typeof data.message !== 'string') throw new Error('无效的错误事件')
        if (typeof data.message_id === 'string') messageId = data.message_id
        error.value = `${data.message}（${data.code}）`; await verify(messageId, round); terminal = true; candidates.value = []
      } else if (!assistant) throw new Error('流缺少开始事件')
      else if (event.event === 'sources') {
        if (!Array.isArray(data.candidates)) throw new Error('无效的来源事件')
        candidates.value = data.candidates as Source[]
      } else if (event.event === 'delta') {
        if (typeof data.text !== 'string') throw new Error('无效的文本事件')
        assistant.content += data.text
      } else if (event.event === 'done') {
        if (data.message_id !== messageId || data.status !== 'completed') throw new Error('无效的完成事件')
        // 重新读取已提交事实；来源可用性以此刻服务端鉴权结果为准。
        await verify(messageId, round); terminal = true; candidates.value = []
      }
    }
    if (!terminal) throw new Error('连接已中断')
  } catch (cause) {
    if (!alive || round !== epoch) return
    clearPrivate(cause); candidates.value = []
    error.value = controller.signal.aborted ? '已停止接收，正在核验保存状态' : cause instanceof Error ? cause.message : '问答连接中断'
    if (cause instanceof ApiError && typeof cause.details.message_id === 'string') messageId = cause.details.message_id
    try { await verify(messageId, round) } catch (failure) { clearPrivate(failure); notice.value = '暂时无法核验保存状态，请恢复连接后刷新历史；不会自动重复提问。' }
  } finally {
    if (alive && round === epoch) { busy.value = false; controller = null }
  }
}
function stop() { controller?.abort() }
onMounted(() => perform(() => loadConversations()))
onBeforeUnmount(() => { alive = false; epoch++; controller?.abort() })
</script>

<template>
  <section class="chat-panel" aria-label="知识库问答">
    <div class="chat-heading"><div><h2>知识库问答</h2><p>回答来自当前知识库，来源会在生成后确认。</p></div><button :disabled="busy || loading" @click="perform(create)">新对话</button></div>
    <div class="chat-toolbar"><label>对话记录 <select :value="selected?.id ?? ''" :disabled="busy || loading" @change="perform(() => open(conversations.find(c => c.id === ($event.target as HTMLSelectElement).value)!))"><option value="" disabled>选择对话</option><option v-for="item in conversations" :key="item.id" :value="item.id">{{ item.title }}</option></select></label><button :disabled="busy || loading" @click="perform(async () => { await loadConversations(); if (selected) await open(selected) })">刷新历史</button><button v-if="conversationCursor" :disabled="busy || loading" @click="perform(() => loadConversations(true))">更多对话</button></div>
    <p v-if="error" class="chat-error" role="alert">{{ error }}</p><p v-if="notice" class="chat-notice" role="status">{{ notice }}</p>
    <button v-if="messageCursor" :disabled="busy || loading" @click="perform(older)">加载更早消息</button>
    <div class="chat-messages" aria-live="polite" aria-relevant="additions text">
      <p v-if="!messages.length" class="chat-empty">选择已建立索引的知识库资料，开始提问。可以继续追问；缺少依据时会明确说明。</p>
      <article v-for="item in messages" :key="item.id" :class="['chat-message', item.role]">
        <div class="message-meta"><strong>{{ item.role === 'user' ? '你' : '知识库助手' }}</strong><span>{{ labels[item.status] }}</span><span v-if="item.answer_type === 'abstention'">证据不足</span></div>
        <p v-if="item.role === 'user'" class="user-text">{{ item.content }}</p><div v-else class="markdown-body" v-html="renderMarkdown(item.content)" />
        <p v-if="item.error_code" class="chat-error">{{ item.error_code }}</p>
        <div v-if="item.citations.length" class="citation-list" aria-label="最终引用"><button v-for="citation in item.citations" :key="citation.source_label" :disabled="!citation.available" @click="showSource(citation.chunk_id)">[{{ citation.source_label }}] {{ citation.available ? citation.filename : '来源已失效' }}<span v-if="citation.page_no"> · 第 {{ citation.page_no }} 页</span></button></div>
      </article>
    </div>
    <details v-if="candidates.length" class="candidate-sources" open><summary>候选来源 · 尚未确认引用</summary><div v-for="item in candidates" :key="item.source_label"><button @click="showSource(item.chunk_id)">[{{ item.source_label }}] {{ item.filename }}<span v-if="item.page_no"> · 第 {{ item.page_no }} 页</span></button><p>{{ item.excerpt.slice(0, 180) }}</p></div></details>
    <aside v-if="source" class="chat-source"><button @click="source = null">关闭来源</button><h3>{{ source.filename }}<span v-if="source.page_no"> · 第 {{ source.page_no }} 页</span></h3><pre>{{ source.text }}</pre></aside>
    <form class="chat-composer" @submit.prevent="send"><label for="chat-question">你的问题</label><textarea id="chat-question" v-model="question" :disabled="busy || loading" maxlength="4000" rows="3" placeholder="这份资料的主要要求是什么？" /><div><small>最多 4,000 字 · 生成中的内容为草稿</small><button v-if="busy" type="button" @click="stop">停止生成</button><button v-else type="submit" :disabled="loading || !question.trim()">发送问题</button></div></form>
  </section>
</template>

<style scoped>
.chat-panel{margin-top:24px;background:#fff;border:1px solid #e1e7ee;border-radius:16px;padding:24px;min-width:0}.chat-heading,.chat-toolbar,.message-meta,.chat-composer>div{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.chat-heading{justify-content:space-between}.chat-heading h2{margin:0}.chat-heading p,.chat-composer small{color:#6a7c8d;font-size:13px}.chat-toolbar{padding:16px 0;border-bottom:1px solid #edf0f4}.chat-toolbar select{max-width:220px;padding:8px;border:1px solid #d7e0e8;border-radius:6px}.chat-panel button{font:inherit;font-size:13px;padding:7px 12px;border-radius:7px;border:1px solid #cddae6;color:#245778;background:#f6faff;cursor:pointer}.chat-panel button:disabled{opacity:.5;cursor:default}.chat-messages{padding:8px 0}.chat-empty{padding:24px 0;color:#708293;line-height:1.8}.chat-message{margin:16px 0;padding:18px;background:#f6f8fb;border-radius:10px;overflow-wrap:anywhere}.chat-message.user{background:#eef6ff}.message-meta{font-size:12px;color:#6b7d8f}.message-meta strong{color:#2b4052;font-size:14px}.user-text{white-space:pre-wrap}.markdown-body{font-size:14px;line-height:1.8;overflow-x:auto}.markdown-body :deep(pre){overflow:auto;background:#e9eef4;padding:12px;border-radius:6px}.markdown-body :deep(table){border-collapse:collapse}.markdown-body :deep(td),.markdown-body :deep(th){border:1px solid #d4dce5;padding:6px}.citation-list{display:flex;gap:8px;flex-wrap:wrap}.candidate-sources,.chat-source{padding:16px;border:1px solid #d8e3ed;border-radius:10px;margin-bottom:16px;font-size:13px}.candidate-sources p{color:#6b7b8e;white-space:pre-wrap}.chat-source pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.8}.chat-composer{border-top:1px solid #e2e9f1;padding-top:16px}.chat-composer label{display:block;font-size:13px;margin-bottom:8px}.chat-composer textarea{box-sizing:border-box;width:100%;padding:12px;border-radius:9px;border:1px solid #cbd9e5;font:inherit;resize:vertical}.chat-composer>div{justify-content:space-between;margin-top:10px}.chat-error{color:#a33535;font-size:13px}.chat-notice{color:#326e58;font-size:13px}@media(max-width:600px){.chat-panel{padding:14px}.chat-toolbar select{max-width:160px}.chat-message{padding:12px}}
</style>
