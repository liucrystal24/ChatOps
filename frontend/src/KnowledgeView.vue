<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElAlert, ElButton, ElForm, ElFormItem, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import { currentUser, request, errorMessage, type KnowledgeBase, type Member, type Page, type Role } from './api/client'

const items = ref<KnowledgeBase[]>([])
const nextCursor = ref<string | null>(null)
const selected = ref<KnowledgeBase | null>(null)
const members = ref<Member[]>([])
const memberCursor = ref<string | null>(null)
const working = ref(false)
const error = ref('')
const notice = ref('')
const name = ref('')
const description = ref('')
const editName = ref('')
const editDescription = ref('')
const targetId = ref('')
const targetRole = ref<Role>('viewer')
const roles: Role[] = ['owner', 'editor', 'viewer']
const labels: Record<Role, string> = { owner: '所有者', editor: '编辑者', viewer: '查看者' }

async function perform(action: () => Promise<void>) {
  if (working.value) return
  working.value = true; error.value = ''; notice.value = ''
  try { await action() } catch (cause) { error.value = errorMessage(cause) }
  finally { working.value = false }
}

async function loadList(append = false) {
  const suffix = append && nextCursor.value ? `?cursor=${encodeURIComponent(nextCursor.value)}` : ''
  const page = await request<Page<KnowledgeBase>>(`/knowledge-bases${suffix}`)
  items.value = append ? [...items.value, ...page.items] : page.items
  nextCursor.value = page.next_cursor
}

async function loadMembers(append = false) {
  if (!selected.value || selected.value.my_role !== 'owner') return
  const suffix = append && memberCursor.value ? `?cursor=${encodeURIComponent(memberCursor.value)}` : ''
  const page = await request<Page<Member>>(`/knowledge-bases/${selected.value.id}/members${suffix}`)
  members.value = append ? [...members.value, ...page.items] : page.items
  memberCursor.value = page.next_cursor
}

async function loadSelected(id: string) {
  selected.value = null; members.value = []; memberCursor.value = null
  const kb = await request<KnowledgeBase>(`/knowledge-bases/${id}`)
  selected.value = kb; editName.value = kb.name; editDescription.value = kb.description
  await loadMembers()
}

function open(id: string) { return perform(() => loadSelected(id)) }
function refresh() { return perform(async () => {
  const id = selected.value?.id
  selected.value = null; members.value = []
  await loadList()
  if (id) await loadSelected(id)
}) }

function create() { return perform(async () => {
  const kb = await request<KnowledgeBase>('/knowledge-bases', { method: 'POST', body: JSON.stringify({ name: name.value, description: description.value }) })
  name.value = ''; description.value = ''
  await loadList(); await loadSelected(kb.id); notice.value = '知识库已创建'
}) }

function save() { return perform(async () => {
  const kb = selected.value!
  await request(`/knowledge-bases/${kb.id}`, { method: 'PATCH', body: JSON.stringify({ name: editName.value, description: editDescription.value }) })
  await loadList(); await loadSelected(kb.id); notice.value = '修改已保存'
}) }

function remove() { return perform(async () => {
  await request(`/knowledge-bases/${selected.value!.id}`, { method: 'DELETE' })
  selected.value = null; members.value = []; await loadList(); notice.value = '空知识库已删除'
}) }

function putMember(userId: string, role: Role) { return perform(async () => {
  const id = selected.value!.id
  await request(`/knowledge-bases/${id}/members/${encodeURIComponent(userId)}`, { method: 'PUT', body: JSON.stringify({ role }) })
  targetId.value = ''; await loadList(); await loadSelected(id); notice.value = '成员权限已更新'
}) }

function removeMember(userId: string) { return perform(async () => {
  const id = selected.value!.id
  await request(`/knowledge-bases/${id}/members/${userId}`, { method: 'DELETE' })
  if (userId === currentUser.value?.id) { selected.value = null; members.value = [] }
  else await loadSelected(id)
  await loadList(); notice.value = '成员已移除'
}) }

onMounted(() => perform(() => loadList()))
</script>

<template>
  <main class="knowledge-layout">
    <div class="heading-row"><div><div class="eyebrow">KNOWLEDGE WORKSPACE</div><h1>我的知识库</h1><p class="intro">只有你作为成员加入的知识库会显示在这里。</p></div><ElButton :loading="working" @click="refresh">刷新列表</ElButton></div>
    <ElAlert v-if="error" :title="error" type="error" :closable="false" class="feedback" />
    <ElAlert v-if="notice" :title="notice" type="success" :closable="false" class="feedback" />
    <div class="knowledge-columns">
      <aside>
        <section class="panel"><h2>创建知识库</h2><ElForm label-position="top" @submit.prevent="create">
          <ElFormItem label="知识库名称"><ElInput v-model="name" aria-label="知识库名称" maxlength="120" required /></ElFormItem>
          <ElFormItem label="知识库描述"><ElInput v-model="description" aria-label="知识库描述" type="textarea" maxlength="2000" :rows="2" /></ElFormItem>
          <ElButton type="primary" native-type="submit" :disabled="working || !name.trim()">创建知识库</ElButton>
        </ElForm></section>
        <section class="library-list" aria-label="知识库列表">
          <p v-if="!items.length && !working" class="empty">你还没有知识库，创建一个或请 owner 将你加入。</p>
          <button v-for="kb in items" :key="kb.id" class="library-item" :class="{ active: selected?.id === kb.id }" :disabled="working" @click="open(kb.id)"><span>{{ kb.name }}</span><small>{{ labels[kb.my_role] }} · {{ kb.my_role }}</small></button>
          <ElButton v-if="nextCursor" :disabled="working" @click="perform(() => loadList(true))">加载更多知识库</ElButton>
        </section>
      </aside>

      <section v-if="selected" class="panel detail-panel" aria-label="知识库详情">
        <div class="detail-heading"><div><h2>{{ selected.name }}</h2><p class="muted">{{ selected.id }}</p></div><ElTag type="info">{{ labels[selected.my_role] }} · {{ selected.my_role }}</ElTag></div>
        <p class="description">{{ selected.description || '尚未填写描述' }}</p>
        <ElAlert title="文档索引与问答尚未接入" description="当前可以管理知识库和成员权限，无需模型密钥。" type="info" :closable="false" />
        <template v-if="selected.my_role === 'owner'">
          <section class="section-divider"><h3>知识库设置</h3><ElForm label-position="top" @submit.prevent="save">
            <ElFormItem label="修改名称"><ElInput v-model="editName" aria-label="修改名称" maxlength="120" required /></ElFormItem>
            <ElFormItem label="修改描述"><ElInput v-model="editDescription" aria-label="修改描述" type="textarea" maxlength="2000" /></ElFormItem>
            <div class="button-row"><ElButton type="primary" native-type="submit" :disabled="working || !editName.trim()">保存修改</ElButton><ElButton type="danger" plain :disabled="working" @click="remove">删除空知识库</ElButton></div>
          </ElForm></section>
          <section class="section-divider"><h3>成员管理</h3><p class="muted">owner 管理知识库和成员；editor 管理文档；viewer 只读。至少保留一名有效 owner。</p>
            <ElForm class="member-add" label-position="top" @submit.prevent="putMember(targetId, targetRole)">
              <ElFormItem label="用户 UUID"><ElInput v-model="targetId" aria-label="用户 UUID" placeholder="由管理员提供" required /></ElFormItem>
              <ElFormItem label="成员角色"><ElSelect v-model="targetRole" aria-label="成员角色"><ElOption v-for="role in roles" :key="role" :label="`${labels[role]} · ${role}`" :value="role" /></ElSelect></ElFormItem>
              <ElButton native-type="submit" :disabled="working || !targetId.trim()">添加或更新成员</ElButton>
            </ElForm>
            <ul class="member-list" aria-label="成员列表"><li v-for="member in members" :key="member.user_id"><div class="member-identity"><strong>{{ member.username }}</strong><ElTag v-if="!member.is_active" type="warning" size="small">已停用</ElTag><small>{{ member.user_id }}</small></div><ElSelect :model-value="member.role" :aria-label="`${member.username}的角色`" :disabled="working || !member.is_active" @change="(role: Role) => putMember(member.user_id, role)"><ElOption v-for="role in roles" :key="role" :label="labels[role]" :value="role" /></ElSelect><ElButton :disabled="working" type="danger" text @click="removeMember(member.user_id)">移除</ElButton></li></ul>
            <ElButton v-if="memberCursor" :disabled="working" @click="perform(() => loadMembers(true))">加载更多成员</ElButton>
          </section>
        </template>
        <p v-else class="muted section-divider">知识库设置和成员管理由 owner 负责。</p>
      </section>
      <section v-else class="panel welcome-panel"><div class="welcome-icon">▤</div><h2>{{ working ? '正在加载' : '选择一个知识库' }}</h2><p>查看知识库详情，或创建新的团队空间。</p></section>
    </div>
  </main>
</template>
