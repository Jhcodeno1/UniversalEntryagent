export interface AgentItem {
  agent_id: string
  name: string
  source: 'LOCAL' | 'THIRD_PARTY' | string
  business_type: string
  description: string
  execution_target: 'DIRECT' | 'AGENT' | 'MCP' | 'LLM' | string
  target_type: string
  action_type: string
  channel: string
  jump_target: string
  api_url: string
  chat_completions_url: string
  model: string
  adapter_type: string
  method: string
  timeout_seconds?: number
  config_file_name: string
  config_file_path: string
  mcp_server: string
  mcp_tool: string
  mcp_params: string
  mcp_transport: string
  mcp_url: string
  mcp_headers: Record<string, unknown>
  headers: Record<string, unknown>
  request_params: Array<Record<string, unknown>>
  request_template: Record<string, unknown>
  keywords: string[]
  examples: string[]
  enabled: boolean
  show_in_chat: boolean
  priority: number
  created_at?: string
  updated_at?: string
}

export interface FallbackPolicy {
  id: string
  name: string
  enabled: boolean
  trigger: string
  threshold: number
  action: string
  reply: string
  record_sample: boolean
  priority: number
  updated_at?: string
}

export interface IntentStrategy {
  id: string
  name: string
  description: string
  enabled: boolean
  category: 'RECALL' | 'JUDGEMENT' | 'REWRITE' | 'LEARNING' | 'CONTEXT' | string
  impact: string
  threshold?: number
  top_k?: number
  min_score?: number
  sample_limit?: number
  memory_turns?: number
  updated_at?: string
}

export interface IntentPoolChannel {
  channel: string
  label: string
  count: number
}

export interface EntryChannel {
  channel: string
  label: string
  enabled: boolean
  open_mode: 'URL_TEMPLATE' | 'API' | string
  url_template: string
  api_url: string
  method: string
  headers: Record<string, unknown>
  request_template: Record<string, unknown>
  description: string
  updated_at?: string
  headers_text?: string
  request_template_text?: string
}

export interface IntentPoolItem {
  id: string
  row_index: number
  SKILL_CODE: string
  NAME: string
  TARGET_TYPE: 'DIRECT' | 'AGENT' | 'MCP' | string
  AGENT_ID: string
  CHANNEL: string
  KEYWORDS: string
  keywords_list: string[]
  ACTION_TYPE: string
  JUMP_TARGET: string
  MCP_SERVER: string
  MCP_TOOL: string
  MCP_PARAMS: string
  EXTRA_PARAMS: string
  CREATED_AT: string
  UPDATED_AT: string
  entry_label: string
}

export interface FallbackSample {
  id: string
  time: string
  session_id: string
  user_query: string
  diagnosis_type?: string
  diagnosis_label?: string
  diagnosis_reason?: string
  suggested_action?: string
  risk_level?: 'LOW' | 'MEDIUM' | 'HIGH' | string
  execution_target?: string
  status?: 'PENDING' | 'PROCESSED' | 'IGNORED' | string
  status_note?: string
  updated_at?: string
  route?: ChatRoute | null
  fallback?: {
    name?: string
    trigger?: string
    action?: string
    threshold?: number
  } | null
  answer?: string
}

export interface ChatRoute {
  status?: string
  selected?: boolean
  route_key?: string
  intent_id?: string
  intent_name?: string
  confidence?: number
  reason?: string
  execution_target?: string
  entry_configured?: boolean
  entry_channel_label?: string
  entry_open_mode?: string
  entry_url?: string
  entry_api_url?: string
  entry_method?: string
  entry_message?: string
  fallback_used?: boolean
  rewrite_used?: boolean
  rewrite_query?: string
  expanded_keywords?: string[]
  suspected_domain?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  route?: ChatRoute | null
  fallback?: FallbackPolicy | null
  latency_ms?: number
  pending?: boolean
}

export interface UploadedAttachment {
  id: string
  name: string
  size: number
  content_type: string
  path: string
}
