<script setup lang="ts">
import { ref } from 'vue'
import { ElAlert, ElButton, ElForm, ElFormItem, ElInput } from 'element-plus'
import { login, errorMessage, sessionNotice } from './api/client'

const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')
async function submit() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await login(username.value, password.value) }
  catch (cause) { error.value = errorMessage(cause) }
  finally { password.value = ''; busy.value = false }
}
</script>

<template>
  <main class="login-layout">
    <section class="login-intro"><div class="eyebrow">YOUR KNOWLEDGE, CONNECTED</div><h1>让团队知识<br />有序、安全地共享。</h1><p>从一个知识库开始，为团队成员分配合适的访问权限。</p><div class="login-note">当前已开放知识库与成员管理，文档索引及问答将在后续阶段接入。</div></section>
    <section class="login-card"><h2>登录工作空间</h2><p>使用管理员为你创建的账号</p>
      <ElAlert v-if="error || sessionNotice" :title="error || sessionNotice" type="warning" :closable="false" />
      <ElForm label-position="top" @submit.prevent="submit">
        <ElFormItem label="用户名"><ElInput v-model="username" name="username" autocomplete="username" maxlength="64" aria-label="用户名" required /></ElFormItem>
        <!-- 原生 maxlength 按 UTF-16 计数；512 避免截断 256 个非 BMP 字符，API 校验实际字符数。 -->
        <ElFormItem label="密码"><ElInput v-model="password" name="password" type="password" show-password autocomplete="current-password" maxlength="512" aria-label="密码" required /></ElFormItem>
        <ElButton type="primary" native-type="submit" :loading="busy" class="login-submit">登录</ElButton>
      </ElForm>
      <p class="login-help">账号由管理员创建。刷新页面后需要重新登录。</p>
    </section>
  </main>
</template>
