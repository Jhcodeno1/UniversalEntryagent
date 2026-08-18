<template>
  <main class="chat-page">
    <header class="page-top chat-top">
      <div>
        <h1>意图路由 Agent</h1>
        <p>用户自然语言输入后，由主 Agent 判断是否调用业务意图路由与专业 Agent。</p>
      </div>
      <div class="top-actions">
        <label class="mini-switch">
          <input v-model="debug" type="checkbox" />
          路由详情
        </label>
        <button class="icon-action" title="历史对话" @click="historyOpen = !historyOpen">
          <span class="history-icon"></span>
        </button>
      </div>
    </header>

    <section class="chat-stage">
      <div ref="messageBox" class="message-box">
        <div v-if="messages.length === 0" class="welcome">
          <h2>有什么我能帮你的吗？</h2>
          <div class="prompt-row">
            <button v-for="prompt in prompts" :key="prompt" @click="fill(prompt)">
              {{ prompt }}
            </button>
          </div>
        </div>

        <article
          v-for="message in messages"
          :key="message.id"
          :class="['message', message.role, { pending: message.pending }]"
        >
          <div class="bubble">
            <p>{{ message.content }}</p>
            <a
              v-if="message.route?.entry_url"
              class="entry-open-link"
              :href="message.route.entry_url"
              target="_blank"
              rel="noreferrer"
            >
              打开业务入口
            </a>
            <div v-if="message.pending" class="thinking-dots">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <details v-if="debug && message.role === 'assistant' && (message.route || message.fallback)">
              <summary>路由详情</summary>
              <dl v-if="message.route">
                <dt>识别意图</dt>
                <dd>{{ message.route.intent_name || '未命中' }}</dd>
                <dt>置信度</dt>
                <dd>{{ percent(message.route.confidence) }}</dd>
                <dt>执行目标</dt>
                <dd>{{ message.route.execution_target || '-' }}</dd>
                <dt v-if="message.route.entry_url">入口地址</dt>
                <dd v-if="message.route.entry_url">{{ message.route.entry_url }}</dd>
                <dt v-if="message.route.entry_api_url">入口接口</dt>
                <dd v-if="message.route.entry_api_url">{{ message.route.entry_method || 'POST' }} {{ message.route.entry_api_url }}</dd>
                <dt>二次干预</dt>
                <dd>{{ message.route.rewrite_used ? '是' : '否' }}</dd>
                <dt>原因</dt>
                <dd>{{ message.route.reason || '-' }}</dd>
              </dl>
              <dl v-if="message.fallback">
                <dt>兜底策略</dt>
                <dd>{{ message.fallback.name }}</dd>
                <dt>兜底动作</dt>
                <dd>{{ message.fallback.action }}</dd>
              </dl>
            </details>
          </div>
        </article>
      </div>

      <form class="composer" @submit.prevent="send">
        <textarea
          v-model="draft"
          placeholder="发消息或按住空格说话..."
          @keydown.enter.exact.prevent="send"
        />

        <div v-if="attachments.length || uploading" class="attachment-row">
          <span v-if="uploading" class="attachment-chip uploading">上传中...</span>
          <span v-for="file in attachments" :key="file.id" class="attachment-chip">
            {{ file.name }}
            <small>{{ formatSize(file.size) }}</small>
            <button type="button" title="移除附件" @click="removeAttachment(file.id)">×</button>
          </span>
        </div>

        <div class="tool-row">
          <input
            ref="fileInput"
            class="hidden-file-input"
            type="file"
            multiple
            @change="handleFiles"
          />
          <div class="tool-scroll">
            <button type="button" class="tool-button plus-button" title="上传文件" @click="pickFiles">
              +
            </button>
            <button type="button" class="tool-button" title="新对话" @click="startNewChat">
              新对话
            </button>
            <button
              v-for="agent in visibleAgents"
              :key="agent.agent_id"
              type="button"
              class="tool-button"
              @click="fill(agent.examples?.[0] || agent.name)"
            >
              <span class="tool-icon cube-icon"></span>
              {{ agent.name }}
            </button>
            <button type="button" class="tool-button" @click="$emit('open-admin')">
              <span class="tool-icon grid-icon"></span>
              更多
            </button>
          </div>
          <button class="send-button" type="submit" :disabled="sending || uploading || !canSend">
            {{ sending ? '发送中' : '发送' }}
          </button>
        </div>
      </form>
    </section>

    <aside :class="['history-drawer', { open: historyOpen }]">
      <div class="drawer-head">
        <strong>历史对话</strong>
        <button class="icon-action small" @click="historyOpen = false">×</button>
      </div>
      <div v-if="conversations.length === 0" class="history-empty">暂无历史对话</div>
      <div
        v-for="item in conversations"
        :key="item.id"
        :class="['history-item', { active: item.id === sessionId }]"
      >
        <button class="history-open" type="button" @click="loadConversation(item.id)">
          <span class="chat-dot"></span>
          <span>{{ item.title }}</span>
        </button>
        <button class="history-delete" type="button" title="删除对话" @click.stop="deleteConversation(item.id)">
          ×
        </button>
      </div>
    </aside>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import type { AgentItem, ChatMessage, UploadedAttachment } from '../types'

const props = defineProps<{ agents: AgentItem[] }>()
defineEmits<{
  'open-admin': []
  'refresh-agents': []
}>()

interface Conversation {
  id: string
  title: string
  updated_at: number
  messages: ChatMessage[]
}

const STORAGE_KEY = 'universal-entry-demo-conversations'

const debug = ref(true)
const draft = ref('')
const sending = ref(false)
const uploading = ref(false)
const historyOpen = ref(false)
const sessionId = ref(`demo-${Date.now()}`)
const messageBox = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const messages = ref<ChatMessage[]>([])
const attachments = ref<UploadedAttachment[]>([])
const conversations = ref<Conversation[]>([])
const prompts = [
  '帮我打开基金产品亮度报告',
  '查询开户材料要求',
  '帮我总结一下这份投研材料',
  '只做意图识别：客户想开通港股通权限',
  '你有哪些能力？',
  '目标智能体不可用时怎么兜底？'
]

const visibleAgents = computed(() => props.agents.slice(0, 3))
const canSend = computed(() => Boolean(draft.value.trim() || attachments.value.length))

onMounted(() => {
  loadConversationStore()
  restoreLatestConversation()
})

function loadConversationStore() {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    conversations.value = Array.isArray(data) ? data : []
  } catch {
    conversations.value = []
  }
}

function saveConversationStore() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.value.slice(0, 30)))
}

function restoreLatestConversation() {
  if (messages.value.length || conversations.value.length === 0) return
  const latest = [...conversations.value].sort((left, right) => right.updated_at - left.updated_at)[0]
  if (!latest) return
  sessionId.value = latest.id
  messages.value = latest.messages.map((message) => ({ ...message, pending: false }))
  nextTick(() => messageBox.value?.scrollTo({ top: messageBox.value.scrollHeight }))
}

function upsertConversation(titleSeed = '') {
  if (!messages.value.length) return
  const title =
    titleSeed.trim().slice(0, 28) ||
    messages.value.find((item) => item.role === 'user')?.content.slice(0, 28) ||
    '新对话'
  const current: Conversation = {
    id: sessionId.value,
    title,
    updated_at: Date.now(),
    messages: messages.value.map((item) => ({ ...item, pending: false }))
  }
  const next = conversations.value.filter((item) => item.id !== sessionId.value)
  conversations.value = [current, ...next].slice(0, 30)
  saveConversationStore()
}

function loadConversation(id: string) {
  const item = conversations.value.find((conversation) => conversation.id === id)
  if (!item) return
  sessionId.value = item.id
  messages.value = item.messages.map((message) => ({ ...message, pending: false }))
  attachments.value = []
  historyOpen.value = false
  nextTick(() => messageBox.value?.scrollTo({ top: messageBox.value.scrollHeight }))
}

function deleteConversation(id: string) {
  const item = conversations.value.find((conversation) => conversation.id === id)
  if (!item) return
  if (!window.confirm(`确定删除“${item.title}”这条历史对话吗？`)) return
  conversations.value = conversations.value.filter((conversation) => conversation.id !== id)
  saveConversationStore()
  if (sessionId.value === id) {
    messages.value = []
    attachments.value = []
    sessionId.value = `demo-${Date.now()}`
  }
}

function fill(text: string) {
  draft.value = text
}

function startNewChat() {
  upsertConversation()
  messages.value = []
  attachments.value = []
  sessionId.value = `demo-${Date.now()}`
}

function percent(value?: number) {
  if (value === undefined || value === null) return '-'
  return `${Math.round(value * 100)}%`
}

function formatSize(size: number) {
  if (size < 1024) return `${size}B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`
  return `${(size / 1024 / 1024).toFixed(1)}MB`
}

function pickFiles() {
  fileInput.value?.click()
}

function removeAttachment(id: string) {
  attachments.value = attachments.value.filter((item) => item.id !== id)
}

async function fileToBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer())
  const chunkSize = 0x8000
  let binary = ''
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize))
  }
  return btoa(binary)
}

async function uploadFile(file: File) {
  if (file.size > 20 * 1024 * 1024) {
    window.alert(`${file.name} 超过 20MB，暂不支持上传`)
    return
  }
  const response = await fetch('/api/demo/uploads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename: file.name,
      content_type: file.type || 'application/octet-stream',
      data_base64: await fileToBase64(file),
      session_id: sessionId.value
    })
  })
  if (!response.ok) throw new Error(`上传失败 HTTP ${response.status}`)
  const data = await response.json()
  attachments.value.push(data.file)
}

async function handleFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (!files.length) return
  uploading.value = true
  try {
    for (const file of files) {
      await uploadFile(file)
    }
  } catch (error) {
    window.alert(error instanceof Error ? error.message : String(error))
  } finally {
    uploading.value = false
    input.value = ''
  }
}

function userMessageText(text: string, files: UploadedAttachment[]) {
  if (!files.length) return text
  const fileText = files.map((file) => `- ${file.name}（${formatSize(file.size)}）`).join('\n')
  return `${text || '请处理这些附件'}\n\n附件：\n${fileText}`
}

async function send() {
  const text = draft.value.trim()
  const files = attachments.value.map((item) => ({ ...item }))
  if ((!text && !files.length) || sending.value || uploading.value) return
  draft.value = ''
  attachments.value = []
  const userContent = userMessageText(text, files)
  messages.value.push({
    id: crypto.randomUUID(),
    role: 'user',
    content: userContent
  })
  const pendingId = crypto.randomUUID()
  messages.value.push({
    id: pendingId,
    role: 'assistant',
    content: pendingText(text, files),
    pending: true
  })
  upsertConversation(text || userContent)
  sending.value = true
  await nextTick()
  messageBox.value?.scrollTo({ top: messageBox.value.scrollHeight, behavior: 'smooth' })
  try {
    const response = await fetch('/api/demo/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text || '请处理我上传的附件',
        session_id: sessionId.value,
        debug: true,
        attachments: files
      })
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    replacePending(pendingId, {
      id: data.message_id || crypto.randomUUID(),
      role: 'assistant',
      content: data.answer || '',
      route: data.route,
      fallback: data.fallback,
      latency_ms: data.latency_ms
    })
  } catch (error) {
    replacePending(pendingId, {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: `请求失败：${error instanceof Error ? error.message : String(error)}`
    })
  } finally {
    sending.value = false
    upsertConversation(text || userContent)
    await nextTick()
    messageBox.value?.scrollTo({ top: messageBox.value.scrollHeight, behavior: 'smooth' })
  }
}

function replacePending(id: string, nextMessage: ChatMessage) {
  const index = messages.value.findIndex((message) => message.id === id)
  if (index >= 0) {
    messages.value.splice(index, 1, nextMessage)
  } else {
    messages.value.push(nextMessage)
  }
}

function pendingText(text: string, files: UploadedAttachment[]) {
  if (files.length) return '正在读取附件，并结合你的问题处理...'
  if (looksLikeBusinessRequest(text)) return '正在判断业务意图，并匹配合适的智能体...'
  return '正在思考回复...'
}

function looksLikeBusinessRequest(text: string) {
  const businessWords = [
    '办理',
    '开通',
    '开户',
    '查询',
    '报告',
    '基金',
    '产品',
    '投研',
    '权限',
    '材料',
    '流程',
    'Agent',
    '智能体'
  ]
  return businessWords.some((word) => text.includes(word))
}
</script>
