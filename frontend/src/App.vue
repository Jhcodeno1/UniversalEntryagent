<template>
  <div class="app-shell">
    <aside class="side-nav">
      <div class="platform-brand">
        <span class="brand-logo">
          <i></i>
        </span>
        <strong>Agentic应用管理平台</strong>
      </div>

      <nav class="main-menu">
        <button :class="{ active: view === 'chat' }" @click="view = 'chat'">
          <span class="menu-icon chat-icon"></span>
          智能体广场
        </button>
        <button :class="{ active: view === 'admin' }" @click="view = 'admin'">
          <span class="menu-icon cube-icon"></span>
          Agent管理
        </button>
      </nav>

      <div class="side-user">
        <span class="user-avatar">A</span>
        <span>admin</span>
      </div>
    </aside>

    <ChatView
      v-show="view === 'chat'"
      :agents="chatAgents"
      @open-admin="view = 'admin'"
      @refresh-agents="loadAgents"
    />
    <AdminView
      v-if="view === 'admin'"
      :agents="agents"
      :intent-pool="intentPool"
      :intent-channels="intentChannels"
      :entry-configs="entryConfigs"
      :intent-strategies="intentStrategies"
      :fallback-samples="fallbackSamples"
      @back-chat="view = 'chat'"
      @agents-change="loadAgents"
      @intent-pool-change="loadIntentPool"
      @entry-configs-change="loadEntryConfigs"
      @strategies-change="loadIntentStrategies"
      @samples-change="loadFallbackSamples"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ChatView from './components/ChatView.vue'
import AdminView from './components/AdminView.vue'
import type { AgentItem, EntryChannel, FallbackSample, IntentPoolChannel, IntentPoolItem, IntentStrategy } from './types'

const view = ref<'chat' | 'admin'>('chat')
const agents = ref<AgentItem[]>([])
const intentPool = ref<IntentPoolItem[]>([])
const intentChannels = ref<IntentPoolChannel[]>([])
const entryConfigs = ref<EntryChannel[]>([])
const intentStrategies = ref<IntentStrategy[]>([])
const fallbackSamples = ref<FallbackSample[]>([])

const chatAgents = computed(() => agents.value.filter((item) => item.enabled && item.show_in_chat))

async function loadAgents() {
  const response = await fetch('/api/demo/agents')
  const data = await response.json()
  agents.value = data.agents || []
}

async function loadIntentStrategies() {
  const response = await fetch('/api/demo/intent-strategies')
  const data = await response.json()
  intentStrategies.value = data.strategies || []
}

async function loadIntentPool() {
  const response = await fetch('/api/demo/intent-pool')
  const data = await response.json()
  intentPool.value = data.intents || []
  intentChannels.value = data.channels || []
}

async function loadEntryConfigs() {
  const response = await fetch('/api/demo/entry-channels')
  const data = await response.json()
  entryConfigs.value = data.channels || []
}

async function loadFallbackSamples() {
  const response = await fetch('/api/demo/fallback-samples?limit=50')
  const data = await response.json()
  fallbackSamples.value = data.samples || []
}

function warmupAgent() {
  fetch('/api/demo/warmup', { method: 'POST' }).catch(() => {
    // Warmup is opportunistic; chat still initializes the agent on demand.
  })
}

onMounted(async () => {
  await Promise.all([loadAgents(), loadIntentPool(), loadEntryConfigs(), loadIntentStrategies(), loadFallbackSamples()])
  warmupAgent()
})
</script>
