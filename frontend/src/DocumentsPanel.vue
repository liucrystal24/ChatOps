<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElAlert, ElButton, ElInput, ElTag } from 'element-plus'
import { ApiError, errorMessage, request, type Page, type Role } from './api/client'
import { active, type Chunk, type Document, type Job } from './api/documents'

const props = defineProps<{ kbId: string; role: Role }>()
const canWrite = computed(() => props.role !== 'viewer')
const documents = ref<Document[]>([])
const jobs = ref<Job[]>([])
const documentCursor = ref<string | null>(null)
const jobCursor = ref<string | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const tags = ref('')
const selected = ref<Document | null>(null)
const editFilename = ref('')
const editTags = ref('')
const chunks = ref<Chunk[]>([])
const chunkCursor = ref<string | null>(null)
const inaccessible = ref(false)
const confirmDelete = ref('')
const statuses: Record<Job['status'], string> = { queued: '等待处理', running: '处理中', retry_wait: '等待自动重试', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const stages: Record<string, string> = { queued: '排队', parse: '解析', chunk: '切分', embed: '向量化', upsert: '写入向量', publish: '发布', cleanup: '清理' }
let alive = true
let timer: ReturnType<typeof setTimeout> | null = null
const pending = new Set<string>()
const documentNames = new Map<string, string>()

function track(job: Job | null) { if (job && active(job)) pending.add(job.id); else if (job) pending.delete(job.id) }
function clearSources() { selected.value = null; chunks.value = []; chunkCursor.value = null; confirmDelete.value = '' }
function handle(cause: unknown) {
  error.value = errorMessage(cause)
  if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
    inaccessible.value = true; documents.value = []; jobs.value = []; pending.clear(); clearSources()
  }
}
async function perform(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try { await action() } catch (cause) { if (alive) handle(cause) }
  finally { if (alive) { busy.value = false; schedule() } }
}
async function loadDocuments(append = false) {
  const suffix = append && documentCursor.value ? `?cursor=${encodeURIComponent(documentCursor.value)}` : ''
  const page = await request<Page<Document>>(`/knowledge-bases/${props.kbId}/documents${suffix}`)
  if (!alive) return
  documents.value = append ? [...documents.value, ...page.items] : page.items
  documentCursor.value = page.next_cursor
  page.items.forEach(d => { documentNames.set(d.id, d.filename); track(d.latest_job) })
  if (selected.value) {
    const fresh = page.items.find(d => d.id === selected.value!.id)
    if (fresh && fresh.active_index_version_id !== selected.value.active_index_version_id) clearSources()
  }
}
async function loadJobs(append = false) {
  const suffix = append && jobCursor.value ? `?cursor=${encodeURIComponent(jobCursor.value)}` : ''
  const page = await request<Page<Job>>(`/knowledge-bases/${props.kbId}/jobs${suffix}`)
  if (!alive) return
  jobs.value = append ? [...jobs.value, ...page.items] : page.items
  jobCursor.value = page.next_cursor; page.items.forEach(track)
}
async function reload() { inaccessible.value = false; await loadDocuments(); await loadJobs() }
function schedule() {
  if (timer) clearTimeout(timer)
  timer = null
  if (alive && pending.size && !inaccessible.value) timer = setTimeout(poll, 2000)
}
async function poll() {
  timer = null
  if (!alive || busy.value) { schedule(); return }
  busy.value = true
  try {
    for (const id of [...pending]) { const job = await request<Job>(`/jobs/${id}`); if (!alive) return; track(job) }
    await loadDocuments(); await loadJobs()
  } catch (cause) { if (alive) handle(cause) }
  finally { if (alive) { busy.value = false; schedule() } }
}
function chooseFile(event: Event) { file.value = (event.target as HTMLInputElement).files?.[0] ?? null }
function tagList(value: string) { return [...new Set(value.split(/[,，]/).map(t => t.trim()).filter(Boolean))] }
function upload() { return perform(async () => {
  if (!file.value) return
  if (file.value.size > 10 * 1024 * 1024) throw new ApiError('DOCUMENT_TOO_LARGE', '单文件不能超过 10 MiB')
  const body = new FormData(); body.append('file', file.value); body.append('tags', JSON.stringify(tagList(tags.value)))
  const result = await request<{ document: Document; job: Job }>(`/knowledge-bases/${props.kbId}/documents`, { method: 'POST', body })
  if (!alive) return
  track(result.job); file.value = null; tags.value = ''; if (fileInput.value) fileInput.value.value = ''
  await reload(); notice.value = '文件已上传，索引任务正在排队；完成后才可检索。'
}) }
async function loadChunks(append = false) {
  if (!selected.value) return
  const suffix = append && chunkCursor.value ? `?cursor=${encodeURIComponent(chunkCursor.value)}` : ''
  const page = await request<Page<Chunk>>(`/documents/${selected.value.id}/chunks${suffix}`)
  if (!alive) return
  chunks.value = append ? [...chunks.value, ...page.items] : page.items; chunkCursor.value = page.next_cursor
}
function open(document: Document) { return perform(async () => {
  clearSources(); selected.value = document; editFilename.value = document.filename; editTags.value = document.tags.join('，')
  await loadChunks()
}) }
function save() { return perform(async () => {
  const document = selected.value!
  const changes: { filename?: string; tags?: string[] } = {}
  if (editFilename.value !== document.filename) changes.filename = editFilename.value
  const nextTags = tagList(editTags.value)
  if (JSON.stringify(nextTags) !== JSON.stringify(document.tags)) changes.tags = nextTags
  if (!Object.keys(changes).length) return
  const result = await request<Document | { document: Document; job: Job }>(`/documents/${document.id}`, { method: 'PATCH', body: JSON.stringify(changes) })
  if (!alive) return
  if ('job' in result) track(result.job)
  clearSources(); await reload(); notice.value = '修改已保存；标签变更会重新索引。'
}) }
function reindex(document: Document) { return perform(async () => {
  const result = await request<{ job: Job }>(`/documents/${document.id}/reindex`, { method: 'POST' })
  if (!alive) return
  track(result.job); await reload(); notice.value = '已提交重建任务，原有效版本继续可检索。'
}) }
function remove(document: Document) { return perform(async () => {
  const result = await request<{ cleanup_job_id: string }>(`/documents/${document.id}`, { method: 'DELETE' })
  if (!alive) return
  pending.add(result.cleanup_job_id); clearSources(); await reload(); notice.value = '文档已删除，立即停止检索；文件与向量正在后台清理。'
}) }
function retry(job: Job) { return perform(async () => {
  const result = await request<{ job: Job }>(`/jobs/${job.id}/retry`, { method: 'POST' })
  if (!alive) return
  track(result.job); await reload(); notice.value = '已创建新的重试任务。'
}) }
function download(document: Document) { return perform(async () => {
  const blob = await request<Blob>(`/documents/${document.id}/file`, {}, 'blob')
  if (!alive) return
  const url = URL.createObjectURL(blob); const a = window.document.createElement('a')
  a.href = url; a.download = document.filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}) }
onMounted(() => perform(reload))
onUnmounted(() => { alive = false; pending.clear(); if (timer) clearTimeout(timer) })
</script>

<template>
  <section class="section-divider document-panel" aria-label="知识库文档">
    <div class="heading-row"><h3>文档与索引</h3><ElButton :disabled="busy" @click="perform(reload)">刷新文档</ElButton></div>
    <p class="muted">支持 UTF-8 TXT、Markdown 和文字 PDF，单文件最多 10 MiB；扫描 PDF 暂不支持。问答将在后续阶段接入。</p>
    <ElAlert v-if="error" :title="error" type="error" :closable="false" class="feedback" />
    <ElAlert v-if="notice" :title="notice" type="success" :closable="false" class="feedback" />
    <form v-if="canWrite && !inaccessible" class="upload-form" @submit.prevent="upload">
      <label>选择文档<input ref="fileInput" type="file" accept=".txt,.md,.pdf" aria-label="选择文档" :disabled="busy" @change="chooseFile" /></label>
      <ElInput v-model="tags" aria-label="文档标签" placeholder="标签，以逗号分隔（最多 10 项）" maxlength="330" :disabled="busy" />
      <ElButton native-type="submit" type="primary" :disabled="busy || !file">上传并索引</ElButton>
    </form>
    <p v-if="!documents.length && !busy && !inaccessible" class="empty">尚未上传文档。</p>
    <ul class="document-list" aria-label="文档列表"><li v-for="document in documents" :key="document.id">
      <div><strong>{{ document.filename }}</strong><p class="muted">{{ (document.size_bytes / 1024).toFixed(1) }} KiB · {{ document.file_type }} <span v-if="document.tags.length">· {{ document.tags.join(' / ') }}</span></p>
        <ElTag :type="document.availability === 'searchable' ? 'success' : 'info'">{{ document.availability === 'searchable' ? '可检索' : '尚不可检索' }}</ElTag>
        <span v-if="document.latest_job" class="muted"> 最新索引：{{ statuses[document.latest_job.status] }}</span>
      </div>
      <div class="button-row"><ElButton :disabled="busy" @click="open(document)">查看来源</ElButton><ElButton :disabled="busy" @click="download(document)">下载原文件</ElButton>
        <ElButton v-if="canWrite" :disabled="busy || (document.latest_job !== null && active(document.latest_job))" @click="reindex(document)">重建索引</ElButton>
        <ElButton v-if="canWrite && confirmDelete !== document.id" type="danger" text :disabled="busy" @click="confirmDelete = document.id">删除文档</ElButton>
        <template v-if="canWrite && confirmDelete === document.id"><span>确认删除 {{ document.filename }}？</span><ElButton type="danger" :disabled="busy" @click="remove(document)">确认删除文档</ElButton><ElButton :disabled="busy" @click="confirmDelete = ''">取消</ElButton></template>
      </div>
    </li></ul>
    <ElButton v-if="documentCursor" :disabled="busy" @click="perform(() => loadDocuments(true))">加载更多文档</ElButton>
    <section v-if="selected" class="source-panel" aria-label="来源正文">
      <div class="heading-row"><h4>{{ selected.filename }}</h4><ElButton :disabled="busy" @click="clearSources">关闭来源</ElButton></div>
      <form v-if="canWrite" class="upload-form" @submit.prevent="save"><ElInput v-model="editFilename" aria-label="文档展示名称" maxlength="255" /><ElInput v-model="editTags" aria-label="修改文档标签" maxlength="330" /><ElButton native-type="submit" :disabled="busy">保存文档信息</ElButton></form>
      <p v-if="!chunks.length" class="muted">此文档还没有有效索引来源，请查看下方任务状态。</p>
      <article v-for="chunk in chunks" :key="chunk.id" class="source-chunk"><p class="muted">{{ chunk.page_no ? `PDF 第 ${chunk.page_no} 页` : '文本来源' }} · {{ chunk.section_title || '' }} 字符 {{ chunk.char_start }}–{{ chunk.char_end }}</p><pre>{{ chunk.text }}</pre></article>
      <ElButton v-if="chunkCursor" :disabled="busy" @click="perform(() => loadChunks(true))">加载更多来源</ElButton>
    </section>
    <h4 v-if="jobs.length">任务记录</h4>
    <ul class="job-list" aria-label="索引任务"><li v-for="job in jobs" :key="job.id"><div><strong>{{ job.kind === 'purge' ? '删除清理' : '文档索引' }} · {{ statuses[job.status] }}</strong><p class="muted">{{ documentNames.get(job.document_id) || '已删除或未加载的文档' }}</p><p class="muted">{{ stages[job.stage] || job.stage }} · 尝试 {{ job.attempt_count }}/{{ job.max_attempts }}<span v-if="job.status === 'running' && job.progress_total !== null"> · {{ job.progress_completed }}/{{ job.progress_total }}</span></p><p v-if="job.error" class="task-error">{{ job.error.message }}（{{ job.error.code }}）</p></div><ElButton v-if="canWrite && job.status === 'failed'" :disabled="busy" @click="retry(job)">重试任务</ElButton></li></ul>
    <ElButton v-if="jobCursor" :disabled="busy" @click="perform(() => loadJobs(true))">加载更多任务</ElButton>
  </section>
</template>
