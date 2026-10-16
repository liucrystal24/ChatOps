<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElAlert, ElButton, ElTag } from 'element-plus'
import { fetchHealth, type Snapshot, type CheckStatus } from './api/health'

const snapshot = ref<Snapshot | null>(null)
const loading = ref(false)
const checkedAt = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
let disposed = false

const healthy = computed(() => snapshot.value?.api === 'ok' && snapshot.value.report?.status === 'ok')
const dependencies = computed(() => [
  { name: 'API', description: '应用进程', status: snapshot.value?.api },
  { name: 'MySQL', description: '业务数据存储', status: snapshot.value?.report?.checks.mysql.status },
  { name: 'Qdrant', description: '向量存储', status: snapshot.value?.report?.checks.qdrant.status },
  { name: '数据库迁移', description: '结构版本检查', status: snapshot.value?.report?.checks.migrations.status },
])

function statusLabel(status: CheckStatus | undefined) {
  if (!snapshot.value) return '检查中'
  return status === 'ok' ? '正常' : status === 'pending' ? '待迁移' : status === 'unavailable' ? '不可用' : '状态未知'
}

async function refresh() {
  if (loading.value || disposed) return
  clearTimeout(timer)
  loading.value = true
  controller = new AbortController()
  const timeout = setTimeout(() => controller?.abort(), 6000)
  try {
    const result = await fetchHealth(controller.signal)
    if (!disposed) {
      snapshot.value = result
      checkedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    }
  } finally {
    clearTimeout(timeout)
    loading.value = false
    if (!disposed) timer = setTimeout(refresh, 10000)
  }
}

onMounted(refresh)
onUnmounted(() => {
  disposed = true
  clearTimeout(timer)
  controller?.abort()
})
</script>

<template>
  <div class="workspace">

    <main>
      <div class="eyebrow">SYSTEM STATUS</div>
      <div class="heading-row">
        <div><h1>服务状态</h1><p class="intro">检查知识库的基础服务，为后续文档检索与问答做好准备。</p></div>
        <ElButton :loading="loading" @click="refresh">刷新状态</ElButton>
      </div>

      <section class="overview" aria-live="polite">
        <div class="status-symbol" :class="{ success: healthy, warning: snapshot && !healthy }">{{ healthy ? '✓' : snapshot ? '!' : '·' }}</div>
        <div>
          <h2>{{ !snapshot ? '正在检查服务' : healthy ? '基础服务已就绪' : '基础服务尚未就绪' }}</h2>
          <p>{{ !snapshot ? '正在读取 API 与依赖的实时状态。' : healthy ? 'API、数据存储与数据库迁移检查均已通过。' : '部分检查未通过或状态未知，请查看下方详情。' }}</p>
        </div>
        <span class="updated">{{ checkedAt ? `最近检查 ${checkedAt}` : '首次检查中' }}</span>
      </section>

      <section class="dependency-grid" aria-label="依赖健康状态">
        <article v-for="item in dependencies" :key="item.name" class="dependency-card">
          <div class="card-top"><span class="card-icon" aria-hidden="true">{{ item.name === 'API' ? '↗' : item.name === 'MySQL' ? '▤' : item.name === 'Qdrant' ? '◇' : '≡' }}</span><ElTag :type="item.status === 'ok' ? 'success' : 'info'" effect="light" round>{{ statusLabel(item.status) }}</ElTag></div>
          <h3>{{ item.name }}</h3><p>{{ item.description }}</p>
        </article>
      </section>

      <section class="capability-panel">
        <div><div class="eyebrow">NEXT CAPABILITY</div><h2>从文档到可追溯的回答</h2><p>已提供文档上传、索引与来源读取；流式问答将在下一阶段开放。</p></div>
        <ElTag type="info" effect="plain">问答尚未接入</ElTag>
      </section>
      <ElAlert title="基础健康与模型能力分别检查" description="文档索引需要配置真实 Embedding Key。本页不调用模型，基础服务就绪不代表模型远端可用。" type="info" :closable="false" show-icon />
      <footer>每 10 秒自动检查 · 健康检查不调用模型</footer>
    </main>
  </div>
</template>
