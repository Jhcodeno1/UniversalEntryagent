<template>
  <main class="admin-page">
    <header class="page-top admin-top">
      <div>
        <h1>能力库</h1>
        <p>统一管理专业 Agent 和 MCP 工具接入；业务入口和模型回复由后台配置保留，不暴露给业务用户维护。</p>
      </div>
      <div class="admin-actions">
        <label class="search-box">
          <span></span>
          <input v-model="keyword" placeholder="关键词搜索" />
        </label>
        <button class="primary" @click="openCreate">+ 添加能力</button>
      </div>
    </header>

    <section v-if="tab === 'agents'" class="source-tabs">
      <button :class="{ active: sourceTab === '' }" @click="sourceTab = ''">全部来源</button>
      <button :class="{ active: sourceTab === 'LOCAL' }" @click="sourceTab = 'LOCAL'">本地</button>
      <button :class="{ active: sourceTab === 'THIRD_PARTY' }" @click="sourceTab = 'THIRD_PARTY'">第三方</button>
    </section>

    <section class="business-tabs">
      <button :class="{ active: businessType === '' }" @click="businessType = ''">所有业务类型</button>
      <button v-for="item in businessTypes" :key="item" :class="{ active: businessType === item }" @click="businessType = item">
        {{ item }}
      </button>
      <button :class="{ active: tab === 'intents' }" class="fallback-tab" @click="tab = tab === 'intents' ? 'agents' : 'intents'">
        意图池
      </button>
      <button :class="{ active: tab === 'fallback' }" @click="tab = tab === 'fallback' ? 'agents' : 'fallback'">
        识别诊断
      </button>
    </section>

    <section v-if="tab === 'agents'" class="target-tabs">
      <span>接入类型</span>
      <button :class="{ active: targetType === '' }" @click="targetType = ''">全部接入</button>
      <button v-for="item in targetTypes" :key="item.value" :class="{ active: targetType === item.value }" @click="targetType = item.value">
        {{ item.label }}
      </button>
    </section>

    <section v-if="tab === 'agents'" class="agent-grid">
      <article v-for="agent in filteredAgents" :key="agent.agent_id" class="agent-card">
        <span :class="['corner-tag', agent.source === 'THIRD_PARTY' ? 'third' : 'local']">
          {{ agent.source === 'THIRD_PARTY' ? '三方' : '本地' }}
        </span>
        <div class="card-head">
          <span class="agent-icon">
            <span class="cube-face"></span>
          </span>
          <div>
            <h2>{{ agent.name }}</h2>
            <p>{{ agent.agent_id }}</p>
          </div>
          <span :class="['status-pill', agent.enabled ? 'on' : 'off']">
            {{ agent.enabled ? '已上线' : '未上线' }}
          </span>
        </div>

        <p class="agent-desc">{{ agent.description || '暂无智能体说明' }}</p>

        <div class="target-line">
          <span :class="['target-pill', targetClass(agent.execution_target)]">
            {{ targetLabel(agent.execution_target) }}
          </span>
          <span v-if="agent.execution_target === 'MCP'">{{ agent.mcp_server || '-' }} / {{ agent.mcp_tool || '-' }}</span>
          <span v-else-if="agent.execution_target === 'DIRECT'">{{ agent.jump_target || '未配置入口' }}</span>
          <span v-else-if="agent.execution_target === 'AGENT'">{{ agent.source === 'THIRD_PARTY' ? '第三方 Agent' : '本地 Agent' }}</span>
          <span v-else>由主模型直接回复</span>
        </div>

        <div class="card-foot">
          <span>更新时间：{{ shortDate(agent.updated_at) }}</span>
          <button class="more-button" title="更多操作">⋮</button>
        </div>

        <div class="card-actions">
          <button @click="edit(agent)">编辑</button>
          <button @click="test(agent)">测试</button>
          <button @click="toggle(agent)">{{ agent.enabled ? '下线' : '上线' }}</button>
        </div>
      </article>
    </section>

    <section v-else-if="tab === 'intents'" class="intent-pool-layout">
      <aside class="intent-entry-panel">
        <div class="sample-head">
          <div>
            <h2>入口分组</h2>
            <p>JZYY / VTM 作为两类办理入口；具体意图命中后连接到对应入口。</p>
          </div>
          <button class="refresh-button" @click="$emit('intent-pool-change')">刷新</button>
        </div>
        <button :class="['entry-card', intentChannel === '' ? 'active' : '']" @click="intentChannel = ''">
          <strong>全部入口</strong>
          <span>{{ intentPool.length }} 个意图</span>
        </button>
        <button
          v-for="channel in intentChannels"
          :key="channel.channel"
          :class="['entry-card', intentChannel === channel.channel ? 'active' : '']"
          @click="intentChannel = channel.channel"
        >
          <strong>{{ channel.label || channel.channel }}</strong>
          <span>{{ channel.channel }} · {{ channel.count }} 个意图</span>
        </button>

        <div class="entry-config-panel">
          <div class="entry-config-head">
            <strong>入口调用配置</strong>
            <button @click="saveEntryConfigs">保存</button>
          </div>
          <article v-for="entry in localEntryConfigs" :key="entry.channel" class="entry-config-card">
            <label class="switch-line">
              <input v-model="entry.enabled" type="checkbox" />
              <span>{{ entry.label || entry.channel }}</span>
            </label>
            <label>
              打开方式
              <select v-model="entry.open_mode">
                <option value="URL_TEMPLATE">URL 模板</option>
                <option value="API">接口调用</option>
              </select>
            </label>
            <label v-if="entry.open_mode !== 'API'">
              URL 模板
              <input v-model="entry.url_template" placeholder="例如 https://host/open?bizCode={jump_target}" />
            </label>
            <template v-else>
              <label>
                接口 URL
                <input v-model="entry.api_url" placeholder="例如 https://host/api/open-entry" />
              </label>
              <label>
                Method
                <select v-model="entry.method">
                  <option value="POST">POST</option>
                  <option value="GET">GET</option>
                </select>
              </label>
              <label>
                Header JSON
                <textarea v-model="entry.headers_text" placeholder='{"Authorization":"Bearer xxx"}' />
              </label>
              <label>
                请求模板 JSON
                <textarea v-model="entry.request_template_text" placeholder='{"target":"{jump_target}"}' />
              </label>
            </template>
          </article>
        </div>
      </aside>

      <section class="intent-table-panel">
        <div class="fallback-title">
          <div>
            <h2>可维护意图池</h2>
            <p>直接维护 CSV 意图清单，保存后会同步刷新业务意图路由。</p>
          </div>
          <button class="primary" @click="openIntentCreate">+ 新增意图</button>
        </div>

        <div class="intent-table">
          <div class="intent-table-head">
            <span>意图码</span>
            <span>意图名称</span>
            <span>入口</span>
            <span>关键词</span>
            <span>目标配置</span>
            <span>操作</span>
          </div>
          <div v-if="filteredIntentPool.length === 0" class="empty-samples">
            暂无匹配意图。可以切换入口或新增意图。
          </div>
          <article v-for="item in filteredIntentPool" :key="item.id" class="intent-row">
            <span class="mono">{{ item.SKILL_CODE }}</span>
            <strong>{{ item.NAME }}</strong>
            <em>{{ item.entry_label || item.CHANNEL }}</em>
            <span>{{ item.KEYWORDS || '-' }}</span>
            <span class="mono">{{ intentTargetDetail(item) }}</span>
            <div>
              <button @click="editIntent(item)">编辑</button>
              <button class="danger" @click="deleteIntent(item)">删除</button>
            </div>
          </article>
        </div>
      </section>
    </section>

    <section v-else class="fallback-layout">
      <div class="fallback-left">
        <div class="fallback-title">
          <div>
            <h2>识别保障策略</h2>
            <p>围绕业务路由召回、精判、改写和样本沉淀做保障，优先减少误判，再持续补齐盲区。</p>
          </div>
          <button class="primary" @click="saveStrategies">保存并刷新路由</button>
        </div>

        <article v-for="strategy in localStrategies" :key="strategy.id" class="fallback-strategy-card">
          <div class="strategy-card-head">
            <label class="switch-line">
              <input v-model="strategy.enabled" type="checkbox" />
              <span>{{ strategy.name }}</span>
            </label>
            <span class="stage-pill">{{ stageText(strategy.category) }}</span>
          </div>
          <p>{{ strategy.description }}</p>

          <div class="strategy-controls">
            <label v-if="strategy.id === 'confidence_gate'">
              置信度阈值
              <input v-model.number="strategy.threshold" type="number" min="0" max="1" step="0.01" />
            </label>

            <label v-if="strategy.id === 'rule_recall_guard'">
              召回候选数
              <input v-model.number="strategy.top_k" type="number" min="1" max="30" step="1" />
            </label>

            <label v-if="strategy.id === 'rule_recall_guard'">
              最低召回分
              <input v-model.number="strategy.min_score" type="number" min="0" max="100" step="1" />
            </label>

            <label v-if="strategy.id === 'context_completion'">
              上下文轮数
              <input v-model.number="strategy.memory_turns" type="number" min="1" max="20" step="1" />
            </label>

            <label v-if="strategy.id === 'failure_sample_capture'">
              样本保留数
              <input v-model.number="strategy.sample_limit" type="number" min="50" max="5000" step="50" />
            </label>

            <div v-if="strategy.id === 'rewrite_retry'" class="strategy-note">
              首轮未命中或低置信度时，系统会改写用户问题并重新召回，再进行第二次精判。
            </div>
          </div>
        </article>
      </div>

      <aside class="fallback-right">
        <div class="sample-head">
          <div>
            <h2>识别诊断中心</h2>
            <p>沉淀未命中、入口未触发、低置信命中、改写命中和执行失败样本，用于人工复核和回放优化。</p>
          </div>
          <button class="refresh-button" @click="$emit('samples-change')">刷新</button>
        </div>

        <div class="sample-stats">
          <div>
            <strong>{{ fallbackSamples.length }}</strong>
            <span>诊断样本</span>
          </div>
          <div>
            <strong>{{ pendingCount }}</strong>
            <span>待处理</span>
          </div>
          <div>
            <strong>{{ highRiskCount }}</strong>
            <span>高风险</span>
          </div>
        </div>

        <div v-if="fallbackSamples.length === 0" class="empty-samples">
          暂无诊断样本。发生未命中、入口未触发路由、低置信命中或执行失败后，会自动沉淀到这里。
        </div>

        <article v-for="sample in fallbackSamples" :key="sample.id || `${sample.time}-${sample.session_id}-${sample.user_query}`" class="sample-card">
          <div class="sample-card-top">
            <span>{{ shortDate(sample.time) }}</span>
            <b :class="['diagnosis-pill', diagnosisClass(sample)]">{{ diagnosisLabel(sample) }}</b>
            <em :class="['sample-status', sampleStatusClass(sample.status)]">{{ sampleStatusText(sample.status) }}</em>
          </div>
          <p>{{ sample.user_query }}</p>
          <div class="diagnosis-detail">
            <span>{{ sample.diagnosis_reason || '业务识别链路产生了需要复核的样本。' }}</span>
            <strong>{{ sample.suggested_action || '人工判断后再更新意图、关键词、示例或能力配置。' }}</strong>
          </div>
          <div v-if="sample.route?.rewrite_query" class="rewrite-detail">
            <span>原始问题：{{ sample.user_query }}</span>
            <span>改写问题：{{ sample.route.rewrite_query }}</span>
            <span v-if="sample.route.expanded_keywords?.length">扩展关键词：{{ sample.route.expanded_keywords.join('，') }}</span>
          </div>
          <dl>
            <dt>识别意图</dt>
            <dd>{{ sample.route?.intent_name || '未命中' }}</dd>
            <dt>置信度</dt>
            <dd>{{ percent(sample.route?.confidence) }}</dd>
            <dt>执行目标</dt>
            <dd>{{ targetLabel(sample.execution_target || sample.route?.execution_target) }}</dd>
            <dt>二次改写</dt>
            <dd>{{ sample.route?.rewrite_used ? '是' : '否' }}</dd>
          </dl>
          <div v-if="sample.status_note" class="sample-note">{{ sample.status_note }}</div>
          <div class="sample-actions">
            <button v-if="canPromoteRewrite(sample)" @click="promoteRewriteSample(sample)">沉淀到意图池</button>
            <button v-if="sample.status !== 'PROCESSED'" @click="markSample(sample, 'PROCESSED')">标记已处理</button>
            <button v-if="sample.status !== 'IGNORED'" @click="markSample(sample, 'IGNORED')">标记忽略</button>
            <button v-if="sample.status && sample.status !== 'PENDING'" @click="markSample(sample, 'PENDING')">重新待处理</button>
            <button class="danger" @click="deleteSample(sample)">删除</button>
          </div>
        </article>
      </aside>
    </section>

    <dialog ref="dialogRef" class="agent-dialog">
      <form method="dialog" class="dialog-body" @submit.prevent="saveAgent">
        <div class="dialog-head">
          <h2>{{ editing ? '编辑能力' : '添加能力' }}</h2>
          <button type="button" class="icon-action small" @click="closeDialog">×</button>
        </div>

        <div v-if="form.execution_target === 'AGENT'" class="type-switch">
          <button type="button" :class="{ active: form.source === 'LOCAL' }" @click="form.source = 'LOCAL'">
            <span class="screen-icon"></span>
            本地能力
          </button>
          <button type="button" :class="{ active: form.source === 'THIRD_PARTY' }" @click="form.source = 'THIRD_PARTY'">
            <span class="link-icon"></span>
            第三方 Agent
          </button>
        </div>

        <div v-if="form.execution_target === 'AGENT' && form.source === 'LOCAL'" class="config-upload">
          <input
            ref="agentConfigInput"
            class="hidden-file-input"
            type="file"
            accept=".json,.yaml,.yml"
            @change="handleAgentConfigFile"
          />
          <div>
            <strong>本地 Agent 配置文件</strong>
            <p>支持 JSON/YAML，上传后会保存到项目运行目录，并自动回填执行地址、名称、描述、关键词等字段。</p>
            <span v-if="form.config_file_name">已导入：{{ form.config_file_name }}</span>
          </div>
          <button type="button" @click="pickAgentConfig">
            {{ uploadingConfig ? '导入中...' : '导入配置文件' }}
          </button>
        </div>

        <div class="form-grid">
          <label>
            能力名称
            <input v-model="form.name" required maxlength="40" placeholder="请输入名称" />
          </label>
          <label>
            agent_id
            <input v-model="form.agent_id" required :disabled="editing" placeholder="唯一标识" />
          </label>
          <label>
            业务类型
            <select v-model="form.business_type">
              <option v-for="item in businessTypes" :key="item">{{ item }}</option>
            </select>
          </label>
          <label>
            接入类型
            <select v-model="form.execution_target">
              <option value="AGENT">专业 Agent：识别后调用外部或本地 Agent</option>
              <option value="MCP">MCP 工具：识别后调用指定 MCP Server/Tool</option>
            </select>
          </label>
          <label class="span-2">
            功能介绍
            <textarea v-model="form.description" maxlength="500" placeholder="请输入能力用途、可处理业务和边界" />
          </label>
          <label class="span-2">
            示例问法，一行一条
            <textarea v-model="examplesText" placeholder="例如：帮我打开基金产品亮度报告" />
          </label>
          <label class="span-2">
            关键词，用逗号分隔
            <input v-model="keywordsText" placeholder="基金，产品亮度，报告" />
          </label>
          <label v-if="form.execution_target === 'DIRECT'">
            办理渠道
            <input v-model="form.channel" />
          </label>
          <label v-if="form.execution_target === 'DIRECT'">
            业务入口
            <input v-model="form.jump_target" />
          </label>
          <label v-if="form.execution_target === 'AGENT' && form.source === 'THIRD_PARTY'" class="span-2">
            执行入口
            <input
              v-model="form.api_url"
              placeholder="每个 Agent 单独填写执行地址；OpenAI 兼容接口可填 base_url"
            />
          </label>
          <label v-if="form.execution_target === 'AGENT' && form.source === 'THIRD_PARTY' && form.adapter_type === 'OPENAI_COMPATIBLE'" class="span-2">
            Chat Completions URL（可选，优先使用）
            <input v-model="form.chat_completions_url" placeholder="例如：http://127.0.0.1:8000/v1/chat/completions" />
          </label>
          <label v-if="form.execution_target === 'AGENT' && form.source === 'THIRD_PARTY' && form.adapter_type === 'OPENAI_COMPATIBLE'">
            模型名称
            <input v-model="form.model" placeholder="不填则默认使用 agent_id" />
          </label>
          <label v-if="form.execution_target === 'AGENT' && form.source === 'THIRD_PARTY'">
            接入协议
            <select v-model="form.adapter_type">
              <option value="HTTP">普通 HTTP 适配器</option>
              <option value="OPENAI_COMPATIBLE">OpenAI 兼容接口</option>
            </select>
          </label>
          <label v-if="form.execution_target === 'AGENT'">
            超时时间（秒）
            <input v-model.number="form.timeout_seconds" type="number" min="5" max="600" />
          </label>
          <label v-if="form.execution_target === 'AGENT' && form.source === 'THIRD_PARTY'" class="span-2">
            Header JSON
            <textarea v-model="headersText" placeholder='{"Authorization":"Bearer xxx"}' />
          </label>
          <label v-if="form.execution_target === 'AGENT' && form.source === 'THIRD_PARTY'" class="span-2">
            请求体模板 JSON
            <textarea v-model="requestTemplateText" placeholder='{"query":"{message}","session_id":"{session_id}"}' />
          </label>
          <label v-if="form.execution_target === 'MCP'">
            MCP Server
            <input v-model="form.mcp_server" placeholder="server 名称" />
          </label>
          <label v-if="form.execution_target === 'MCP'">
            连接协议
            <select v-model="form.mcp_transport">
              <option value="streamable_http">Streamable HTTP</option>
              <option value="sse">SSE</option>
            </select>
          </label>
          <label v-if="form.execution_target === 'MCP'" class="span-2">
            MCP Server URL
            <input v-model="form.mcp_url" placeholder="例如：http://127.0.0.1:9000/mcp" />
          </label>
          <label v-if="form.execution_target === 'MCP'">
            MCP Tool
            <input v-model="form.mcp_tool" placeholder="tool 名称" />
          </label>
          <label v-if="form.execution_target === 'MCP'" class="span-2">
            MCP 参数 JSON
            <textarea v-model="form.mcp_params" placeholder='{"key":"value"}' />
          </label>
          <label v-if="form.execution_target === 'MCP'" class="span-2">
            MCP Header JSON
            <textarea v-model="mcpHeadersText" placeholder='{"Authorization":"Bearer xxx"}' />
          </label>
        </div>

        <div class="dialog-actions">
          <button type="button" @click="closeDialog">取消</button>
          <button class="primary" type="submit">确定</button>
        </div>
      </form>
    </dialog>

    <dialog ref="intentDialogRef" class="agent-dialog">
      <form class="dialog-body compact" @submit.prevent="saveIntent">
        <div class="dialog-head">
          <h2>{{ editingIntent ? '编辑意图' : '新增意图' }}</h2>
          <button type="button" class="icon-action small" @click="closeIntentDialog">×</button>
        </div>
        <div class="form-grid">
          <label>
            意图码 SKILL_CODE
            <input v-model="intentForm.SKILL_CODE" required placeholder="例如 bjs_auth_open" />
          </label>
          <label>
            连接方式
            <select v-model="intentForm.TARGET_TYPE">
              <option value="DIRECT">业务入口</option>
              <option value="AGENT">专业 Agent</option>
              <option value="MCP">MCP 工具</option>
            </select>
          </label>
          <label class="span-2">
            意图名称
            <input v-model="intentForm.NAME" required placeholder="例如 北交所及新三板联合权限开通" />
          </label>
          <label class="span-2">
            关键词
            <input v-model="intentForm.KEYWORDS" placeholder="用逗号分隔，例如 北交所，新三板，权限开通" />
          </label>
          <label v-if="intentForm.TARGET_TYPE === 'DIRECT'">
            入口 CHANNEL
            <select v-model="intentForm.CHANNEL">
              <option value="JZYY">JZYY 集中运营入口</option>
              <option value="VTM">VTM 入口</option>
            </select>
          </label>
          <label v-if="intentForm.TARGET_TYPE === 'AGENT'">
            目标专业 Agent
            <select v-model="intentForm.AGENT_ID">
              <option value="">请选择 Agent</option>
              <option v-for="agent in agentTargets" :key="agent.agent_id" :value="agent.agent_id">
                {{ agent.name }} / {{ agent.agent_id }}
              </option>
            </select>
          </label>
          <label v-if="intentForm.TARGET_TYPE === 'DIRECT'">
            直达动作
            <select v-model="intentForm.ACTION_TYPE">
              <option value="JUMP">打开业务入口</option>
            </select>
          </label>
          <label v-if="intentForm.TARGET_TYPE === 'DIRECT'">
            跳转目标
            <input v-model="intentForm.JUMP_TARGET" placeholder="入口编号或 URL" />
          </label>
          <label v-if="intentForm.TARGET_TYPE === 'MCP'">
            MCP Server
            <input v-model="intentForm.MCP_SERVER" placeholder="server 名称" />
          </label>
          <label v-if="intentForm.TARGET_TYPE === 'MCP'">
            MCP Tool
            <input v-model="intentForm.MCP_TOOL" placeholder="tool 名称" />
          </label>
          <label v-if="intentForm.TARGET_TYPE === 'MCP'" class="span-2">
            MCP 参数
            <textarea v-model="intentForm.MCP_PARAMS" placeholder='{"key":"value"}' />
          </label>
          <label class="span-2">
            扩展参数
            <textarea v-model="intentForm.EXTRA_PARAMS" placeholder="保留给业务系统的扩展配置" />
          </label>
        </div>
        <div class="dialog-actions">
          <span v-if="intentSaveMessage" class="dialog-status">{{ intentSaveMessage }}</span>
          <button type="button" @click="closeIntentDialog">取消</button>
          <button class="primary" type="submit" :disabled="intentSaving">
            {{ intentSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </form>
    </dialog>
  </main>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { AgentItem, EntryChannel, FallbackSample, IntentPoolChannel, IntentPoolItem, IntentStrategy } from '../types'

const props = defineProps<{
  agents: AgentItem[]
  intentPool: IntentPoolItem[]
  intentChannels: IntentPoolChannel[]
  entryConfigs: EntryChannel[]
  intentStrategies: IntentStrategy[]
  fallbackSamples: FallbackSample[]
}>()
const emit = defineEmits<{
  'back-chat': []
  'agents-change': []
  'intent-pool-change': []
  'entry-configs-change': []
  'strategies-change': []
  'samples-change': []
}>()

const tab = ref<'agents' | 'intents' | 'fallback'>('agents')
const sourceTab = ref('')
const keyword = ref('')
const businessType = ref('')
const targetType = ref('')
const dialogRef = ref<HTMLDialogElement | null>(null)
const intentDialogRef = ref<HTMLDialogElement | null>(null)
const editing = ref(false)
const editingIntent = ref(false)
const editingIntentId = ref('')
const intentSaving = ref(false)
const intentSaveMessage = ref('')
const intentChannel = ref('')
const examplesText = ref('')
const keywordsText = ref('')
const headersText = ref('')
const requestTemplateText = ref('')
const mcpHeadersText = ref('')
const uploadingConfig = ref(false)
const agentConfigInput = ref<HTMLInputElement | null>(null)
const localStrategies = ref<IntentStrategy[]>([])
const localEntryConfigs = ref<EntryChannel[]>([])
const businessTypes = ['业务运营类', '财富管理类', '综合服务类', '资产管理类']
const targetTypes = [
  { value: 'AGENT', label: '专业 Agent' },
  { value: 'MCP', label: 'MCP 工具' }
]
const manageableTargets = new Set(['AGENT', 'MCP'])

const intentForm = reactive<IntentPoolItem>({
  id: '',
  row_index: -1,
  SKILL_CODE: '',
  NAME: '',
  TARGET_TYPE: 'DIRECT',
  AGENT_ID: '',
  CHANNEL: 'JZYY',
  KEYWORDS: '',
  keywords_list: [],
  ACTION_TYPE: 'JUMP',
  JUMP_TARGET: '',
  MCP_SERVER: '',
  MCP_TOOL: '',
  MCP_PARAMS: '',
  EXTRA_PARAMS: '',
  CREATED_AT: '',
  UPDATED_AT: '',
  entry_label: ''
})

const form = reactive<AgentItem>({
  agent_id: '',
  name: '',
  source: 'LOCAL',
  business_type: '综合服务类',
  description: '',
  execution_target: 'AGENT',
  target_type: '',
  action_type: '',
  channel: '',
  jump_target: '',
  api_url: '',
  chat_completions_url: '',
  model: '',
  adapter_type: 'HTTP',
  method: 'POST',
  timeout_seconds: 120,
  config_file_name: '',
  config_file_path: '',
  mcp_server: '',
  mcp_tool: '',
  mcp_params: '',
  mcp_transport: 'streamable_http',
  mcp_url: '',
  mcp_headers: {},
  headers: {},
  request_params: [],
  request_template: {},
  keywords: [],
  examples: [],
  enabled: true,
  show_in_chat: true,
  priority: 0
})

watch(
  () => props.intentStrategies,
  (value) => {
    localStrategies.value = value.map((item) => ({ ...item }))
  },
  { immediate: true }
)

watch(
  () => props.entryConfigs,
  (value) => {
    localEntryConfigs.value = value.map((item) => ({
      ...item,
      headers_text: JSON.stringify(item.headers || {}, null, 2),
      request_template_text: JSON.stringify(item.request_template || {}, null, 2)
    }))
  },
  { immediate: true }
)

watch(
  () => form.execution_target,
  (value) => {
    if (value !== 'AGENT') {
      form.source = 'LOCAL'
      form.adapter_type = ''
    }
  }
)

const filteredAgents = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return props.agents.filter((agent) => {
    const executionTarget = String(agent.execution_target || '').toUpperCase()
    if (!manageableTargets.has(executionTarget)) return false
    const matchedText =
      !text ||
      agent.name.toLowerCase().includes(text) ||
      agent.agent_id.toLowerCase().includes(text)
    const matchedType = !businessType.value || agent.business_type === businessType.value
    const matchedSource = !sourceTab.value || agent.source === sourceTab.value
    const matchedTarget = !targetType.value || executionTarget === targetType.value
    return matchedText && matchedType && matchedSource && matchedTarget
  })
})

const agentTargets = computed(() =>
  props.agents.filter((agent) => String(agent.execution_target || '').toUpperCase() === 'AGENT')
)

const filteredIntentPool = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  return props.intentPool.filter((item) => {
    const matchedChannel = !intentChannel.value || intentGroupKey(item) === intentChannel.value
    const matchedText =
      !text ||
      item.SKILL_CODE.toLowerCase().includes(text) ||
      item.NAME.toLowerCase().includes(text) ||
      item.KEYWORDS.toLowerCase().includes(text) ||
      item.JUMP_TARGET.toLowerCase().includes(text) ||
      item.AGENT_ID.toLowerCase().includes(text) ||
      item.MCP_SERVER.toLowerCase().includes(text) ||
      item.MCP_TOOL.toLowerCase().includes(text)
    return matchedChannel && matchedText
  })
})

const pendingCount = computed(() =>
  props.fallbackSamples.filter((item) => String(item.status || 'PENDING').toUpperCase() === 'PENDING').length
)

const highRiskCount = computed(() =>
  props.fallbackSamples.filter((item) => String(item.risk_level || '').toUpperCase() === 'HIGH').length
)

function shortDate(value?: string) {
  return value ? value.replace('T', ' ').slice(0, 16) : '-'
}

function percent(value?: number) {
  if (value === undefined || value === null) return '-'
  return `${Math.round(value * 100)}%`
}

function sampleStatusText(value?: string) {
  const map: Record<string, string> = {
    PENDING: '待处理',
    PROCESSED: '已处理',
    IGNORED: '已忽略'
  }
  return map[String(value || 'PENDING').toUpperCase()] || '待处理'
}

function sampleStatusClass(value?: string) {
  return String(value || 'PENDING').toLowerCase()
}

function diagnosisLabel(sample: FallbackSample) {
  if (sample.diagnosis_label) return sample.diagnosis_label
  const trigger = String(sample.fallback?.trigger || sample.diagnosis_type || '').toUpperCase()
  const map: Record<string, string> = {
    SECOND_STAGE_FAILED: '路由未命中',
    LOW_CONFIDENCE: '低置信拦截',
    AGENT_CALL_FAILED: '执行失败',
    ROUTER_NOT_CALLED: '入口未触发路由',
    LOW_CONFIDENCE_SELECTED: '低置信命中',
    REWRITE_RECOVERED: '二次改写命中',
    FACT_BOUNDARY: '事实依据不足'
  }
  return map[trigger] || '识别诊断'
}

function diagnosisClass(sample: FallbackSample) {
  return String(sample.diagnosis_type || sample.fallback?.trigger || 'route_fallback').toLowerCase()
}

function canPromoteRewrite(sample: FallbackSample) {
  return Boolean(
    sample.id &&
    sample.route?.rewrite_query &&
    sample.route?.intent_id &&
    sample.status !== 'PROCESSED'
  )
}

function targetLabel(value?: string) {
  const map: Record<string, string> = {
    DIRECT: '业务入口',
    AGENT: '专业 Agent',
    MCP: 'MCP 工具',
    LLM: '模型回复'
  }
  return map[String(value || '').toUpperCase()] || '未配置'
}

function targetClass(value?: string) {
  return String(value || '').toLowerCase()
}

function intentGroupKey(item: IntentPoolItem) {
  const target = String(item.TARGET_TYPE || '').toUpperCase()
  if (target === 'AGENT') return 'AGENT'
  if (target === 'MCP') return 'MCP'
  return item.CHANNEL || 'UNASSIGNED'
}

function intentTargetDetail(item: IntentPoolItem) {
  const target = String(item.TARGET_TYPE || '').toUpperCase()
  if (target === 'AGENT') return item.AGENT_ID || '-'
  if (target === 'MCP') return [item.MCP_SERVER, item.MCP_TOOL].filter(Boolean).join(' / ') || '-'
  return item.JUMP_TARGET || '-'
}

function stageText(category: string) {
  const map: Record<string, string> = {
    RECALL: '规则召回',
    JUDGEMENT: '模型精判',
    REWRITE: '二次识别',
    LEARNING: '样本沉淀',
    CONTEXT: '上下文补全'
  }
  return map[category] || category
}

function resetForm() {
  Object.assign(form, {
    agent_id: '',
    name: '',
    source: 'LOCAL',
    business_type: '综合服务类',
    description: '',
    execution_target: 'AGENT',
    target_type: '',
    action_type: '',
    channel: '',
    jump_target: '',
    api_url: '',
    chat_completions_url: '',
    model: '',
    adapter_type: 'HTTP',
    method: 'POST',
    timeout_seconds: 120,
    config_file_name: '',
    config_file_path: '',
    mcp_server: '',
    mcp_tool: '',
    mcp_params: '',
    mcp_transport: 'streamable_http',
    mcp_url: '',
    mcp_headers: {},
    headers: {},
    request_params: [],
    request_template: {},
    keywords: [],
    examples: [],
    enabled: true,
    show_in_chat: true,
    priority: 0
  })
  examplesText.value = ''
  keywordsText.value = ''
  headersText.value = ''
  requestTemplateText.value = ''
  mcpHeadersText.value = ''
}

function openCreate() {
  editing.value = false
  resetForm()
  dialogRef.value?.showModal()
}

function edit(agent: AgentItem) {
  editing.value = true
  Object.assign(form, { ...agent })
  examplesText.value = (agent.examples || []).join('\n')
  keywordsText.value = (agent.keywords || []).join('，')
  headersText.value = JSON.stringify(agent.headers || {}, null, 2)
  requestTemplateText.value = JSON.stringify(agent.request_template || {}, null, 2)
  mcpHeadersText.value = JSON.stringify(agent.mcp_headers || {}, null, 2)
  dialogRef.value?.showModal()
}

function closeDialog() {
  dialogRef.value?.close()
}

function resetIntentForm() {
  Object.assign(intentForm, {
    id: '',
    row_index: -1,
    SKILL_CODE: '',
    NAME: '',
    TARGET_TYPE: 'DIRECT',
    AGENT_ID: '',
    CHANNEL: intentChannel.value || 'JZYY',
    KEYWORDS: '',
    keywords_list: [],
    ACTION_TYPE: 'JUMP',
    JUMP_TARGET: '',
    MCP_SERVER: '',
    MCP_TOOL: '',
    MCP_PARAMS: '',
    EXTRA_PARAMS: '',
    CREATED_AT: '',
    UPDATED_AT: '',
    entry_label: ''
  })
}

function openIntentCreate() {
  editingIntent.value = false
  editingIntentId.value = ''
  intentSaveMessage.value = ''
  resetIntentForm()
  intentDialogRef.value?.showModal()
}

function editIntent(item: IntentPoolItem) {
  editingIntent.value = true
  editingIntentId.value = item.id
  intentSaveMessage.value = ''
  Object.assign(intentForm, { ...item })
  intentDialogRef.value?.showModal()
}

function closeIntentDialog() {
  intentSaveMessage.value = ''
  intentDialogRef.value?.close()
}

function pickAgentConfig() {
  if (uploadingConfig.value) return
  agentConfigInput.value?.click()
}

function fileToBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve(result.includes(',') ? result.split(',')[1] : result)
    }
    reader.onerror = () => reject(reader.error || new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
}

function normalizeAgentId(value: unknown) {
  return String(value || '')
    .trim()
    .replace(/[^0-9A-Za-z_-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64)
}

function listText(value: unknown, separator = '，') {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean).join(separator)
  return String(value || '').trim()
}

function applyImportedAgentFields(fields: Record<string, unknown>) {
  const importedId = normalizeAgentId(fields.agent_id)
  if (importedId && !editing.value) form.agent_id = importedId
  if (fields.name) form.name = String(fields.name)
  if (fields.description) form.description = String(fields.description)
  if (fields.business_type) form.business_type = String(fields.business_type)
  if (fields.api_url) form.api_url = String(fields.api_url)
  if (fields.chat_completions_url) form.chat_completions_url = String(fields.chat_completions_url)
  if (fields.model) form.model = String(fields.model)
  if (fields.method) form.method = String(fields.method).toUpperCase()
  if (fields.timeout_seconds) form.timeout_seconds = Number(fields.timeout_seconds) || form.timeout_seconds
  if (fields.config_file_name) form.config_file_name = String(fields.config_file_name)
  if (fields.config_file_path) form.config_file_path = String(fields.config_file_path)

  const adapter = String(fields.adapter_type || '').toUpperCase()
  if (adapter.includes('OPENAI')) {
    form.adapter_type = 'OPENAI_COMPATIBLE'
  } else if (adapter || form.api_url || form.chat_completions_url) {
    form.adapter_type = form.chat_completions_url ? 'OPENAI_COMPATIBLE' : 'HTTP'
  }

  if (fields.headers && typeof fields.headers === 'object') {
    headersText.value = JSON.stringify(fields.headers, null, 2)
  }
  if (fields.request_template && typeof fields.request_template === 'object') {
    requestTemplateText.value = JSON.stringify(fields.request_template, null, 2)
  }
  if (fields.keywords) keywordsText.value = listText(fields.keywords)
  if (fields.examples) examplesText.value = listText(fields.examples, '\n')
}

async function handleAgentConfigFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  uploadingConfig.value = true
  try {
    const response = await fetch('/api/demo/agent-configs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: file.name,
        data_base64: await fileToBase64(file)
      })
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const data = await response.json()
    applyImportedAgentFields(data.fields || {})
  } catch (error) {
    window.alert(`配置文件导入失败：${error instanceof Error ? error.message : '未知错误'}`)
  } finally {
    uploadingConfig.value = false
    input.value = ''
  }
}

async function saveAgent() {
  let headers: Record<string, unknown> = {}
  let request_template: Record<string, unknown> = {}
  let mcp_headers: Record<string, unknown> = {}
  try {
    headers = headersText.value.trim() ? JSON.parse(headersText.value) : {}
    request_template = requestTemplateText.value.trim()
      ? JSON.parse(requestTemplateText.value)
      : {}
    mcp_headers = mcpHeadersText.value.trim() ? JSON.parse(mcpHeadersText.value) : {}
  } catch {
    window.alert('Header JSON、请求体模板 JSON 或 MCP Header JSON 格式不正确')
    return
  }
  const isLocalAgent = form.execution_target === 'AGENT' && form.source === 'LOCAL'
  const payload = {
    ...form,
    api_url: isLocalAgent ? '' : form.api_url,
    chat_completions_url: isLocalAgent ? '' : form.chat_completions_url,
    adapter_type:
      isLocalAgent
        ? ''
        : form.execution_target === 'AGENT'
        ? form.adapter_type ||
          (form.chat_completions_url ? 'OPENAI_COMPATIBLE' : 'HTTP')
        : form.adapter_type,
    headers: isLocalAgent ? {} : headers,
    request_template: isLocalAgent ? {} : request_template,
    mcp_server:
      form.execution_target === 'MCP'
        ? form.mcp_server || form.agent_id
        : form.mcp_server,
    target_type: form.target_type || form.execution_target,
    action_type: form.execution_target === 'DIRECT' ? 'JUMP' : form.action_type,
    mcp_headers,
    examples: examplesText.value.split('\n').map((item) => item.trim()).filter(Boolean),
    keywords: keywordsText.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
  }
  const response = await fetch(
    editing.value ? `/api/demo/agents/${encodeURIComponent(form.agent_id)}` : '/api/demo/agents',
    {
      method: editing.value ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }
  )
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  closeDialog()
  emit('agents-change')
}

async function toggle(agent: AgentItem) {
  await fetch(`/api/demo/agents/${encodeURIComponent(agent.agent_id)}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: !agent.enabled })
  })
  emit('agents-change')
}

async function test(agent: AgentItem) {
  const response = await fetch(`/api/demo/agents/${encodeURIComponent(agent.agent_id)}/test`, {
    method: 'POST'
  })
  const data = await response.json()
  window.alert(data.message || '测试完成')
}

async function responseErrorMessage(response: Response) {
  try {
    const data = await response.json()
    if (data?.detail) return String(data.detail)
  } catch {
    // Fall back to plain text below.
  }
  try {
    const text = await response.text()
    if (text) return text
  } catch {
    // Ignore body parse failures.
  }
  return `HTTP ${response.status}`
}

async function saveIntent() {
  if (intentSaving.value) return
  const targetType = String(intentForm.TARGET_TYPE || 'DIRECT').toUpperCase()
  if (targetType === 'AGENT' && !intentForm.AGENT_ID.trim()) {
    window.alert('请选择目标专业 Agent')
    return
  }
  if (targetType === 'MCP' && (!intentForm.MCP_SERVER.trim() || !intentForm.MCP_TOOL.trim())) {
    window.alert('请填写 MCP Server 和 MCP Tool')
    return
  }
  const payload = {
    SKILL_CODE: intentForm.SKILL_CODE.trim(),
    NAME: intentForm.NAME.trim(),
    TARGET_TYPE: targetType,
    AGENT_ID: targetType === 'AGENT' ? intentForm.AGENT_ID.trim() : '',
    CHANNEL:
      targetType === 'DIRECT'
        ? intentForm.CHANNEL || 'JZYY'
        : targetType === 'AGENT'
        ? 'AGENT'
        : 'MCP',
    KEYWORDS: intentForm.KEYWORDS.trim(),
    ACTION_TYPE: targetType === 'MCP' ? 'MCP' : targetType === 'DIRECT' ? 'JUMP' : '',
    JUMP_TARGET: targetType === 'DIRECT' ? intentForm.JUMP_TARGET.trim() : '',
    MCP_SERVER: targetType === 'MCP' ? intentForm.MCP_SERVER.trim() : '',
    MCP_TOOL: targetType === 'MCP' ? intentForm.MCP_TOOL.trim() : '',
    MCP_PARAMS: targetType === 'MCP' ? intentForm.MCP_PARAMS.trim() : '',
    EXTRA_PARAMS: intentForm.EXTRA_PARAMS.trim(),
    CREATED_AT: intentForm.CREATED_AT,
    UPDATED_AT: intentForm.UPDATED_AT
  }
  intentSaving.value = true
  intentSaveMessage.value = '保存中...'
  try {
    const response = await fetch(
      editingIntent.value
        ? `/api/demo/intent-pool/${encodeURIComponent(editingIntentId.value)}`
        : '/api/demo/intent-pool',
      {
        method: editingIntent.value ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }
    )
    if (!response.ok) {
      throw new Error(await responseErrorMessage(response))
    }
    emit('intent-pool-change')
    closeIntentDialog()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    intentSaveMessage.value = message
    window.alert(`意图保存失败：${message}`)
  } finally {
    intentSaving.value = false
  }
}

async function deleteIntent(item: IntentPoolItem) {
  if (!window.confirm(`确定删除意图「${item.NAME}」吗？删除后会同步修改 CSV。`)) return
  const response = await fetch(`/api/demo/intent-pool/${encodeURIComponent(item.id)}`, {
    method: 'DELETE'
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  emit('intent-pool-change')
}

async function promoteRewriteSample(sample: FallbackSample) {
  if (!sample.id) return
  const response = await fetch(`/api/demo/rewrite-samples/${encodeURIComponent(sample.id)}/promote`, {
    method: 'POST'
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  emit('intent-pool-change')
  emit('samples-change')
}

async function markSample(sample: FallbackSample, status: 'PENDING' | 'PROCESSED' | 'IGNORED') {
  if (!sample.id) return
  const response = await fetch(`/api/demo/fallback-samples/${encodeURIComponent(sample.id)}/mark`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status })
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  emit('samples-change')
}

async function deleteSample(sample: FallbackSample) {
  if (!sample.id) return
  if (!window.confirm('确定删除这条兜底样本吗？删除后不可恢复。')) return
  const response = await fetch(`/api/demo/fallback-samples/${encodeURIComponent(sample.id)}`, {
    method: 'DELETE'
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  emit('samples-change')
}

async function saveStrategies() {
  const response = await fetch('/api/demo/intent-strategies', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategies: localStrategies.value })
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  emit('strategies-change')
}

async function saveEntryConfigs() {
  let channels: EntryChannel[]
  try {
    channels = localEntryConfigs.value.map((item) => ({
      ...item,
      headers: item.headers_text?.trim() ? JSON.parse(item.headers_text) : {},
      request_template: item.request_template_text?.trim()
        ? JSON.parse(item.request_template_text)
        : {},
      method: item.method || 'POST'
    }))
  } catch {
    window.alert('入口配置中的 Header JSON 或请求模板 JSON 格式不正确')
    return
  }
  const response = await fetch('/api/demo/entry-channels', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channels })
  })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  emit('entry-configs-change')
}
</script>
