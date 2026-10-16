<script setup lang="ts">
import { ref } from 'vue'
import { ElButton } from 'element-plus'
import { currentUser, logout } from './api/client'
import HealthStatus from './HealthStatus.vue'
import KnowledgeView from './KnowledgeView.vue'
import LoginView from './LoginView.vue'
const view = ref<'knowledge' | 'health'>('knowledge')
</script>

<template>
  <div class="workspace">
    <header class="topbar"><a class="brand" href="/" aria-label="知识库首页"><span class="brand-mark">知</span>知识库<span class="brand-sub">RAG WORKSPACE</span></a>
      <nav class="top-nav" aria-label="主导航"><ElButton :type="view === 'knowledge' ? 'primary' : undefined" text @click="view = 'knowledge'">知识库</ElButton><ElButton :type="view === 'health' ? 'primary' : undefined" text @click="view = 'health'">服务状态</ElButton></nav>
      <div v-if="currentUser" class="user-menu"><span>{{ currentUser.username }}</span><ElButton text @click="logout()">退出</ElButton></div>
    </header>
    <HealthStatus v-if="view === 'health'" />
    <KnowledgeView v-else-if="currentUser" :key="currentUser.id" />
    <LoginView v-else />
  </div>
</template>
