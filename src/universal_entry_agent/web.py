from __future__ import annotations

import contextlib
import io
import asyncio
import base64
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import UniversalEntryAgent
from .config import AgentConfig
from .demo_registry import DemoRegistry


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(default="web")


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    trace_id: str = ""
    elapsed_ms: float = 0.0


class DemoChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field(default="demo")
    user_id: str = Field(default="demo_user")
    debug: bool = Field(default=True)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class DemoChatResponse(BaseModel):
    message_id: str
    answer: str
    type: str = "message"
    session_id: str
    route: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None
    fallback: dict[str, Any] | None = None
    trace_id: str = ""
    latency_ms: float = 0.0


class AgentPayload(BaseModel):
    agent_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    source: str = "LOCAL"
    business_type: str = "综合服务类"
    description: str = ""
    execution_target: str = "AGENT"
    target_type: str = ""
    action_type: str = ""
    channel: str = ""
    jump_target: str = ""
    api_url: str = ""
    chat_completions_url: str = ""
    model: str = ""
    adapter_type: str = ""
    method: str = "POST"
    timeout_seconds: int = 120
    config_file_name: str = ""
    config_file_path: str = ""
    mcp_server: str = ""
    mcp_tool: str = ""
    mcp_params: str = ""
    mcp_transport: str = "streamable_http"
    mcp_url: str = ""
    mcp_headers: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    request_params: list[dict[str, Any]] = Field(default_factory=list)
    request_template: dict[str, Any] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    enabled: bool = True
    show_in_chat: bool = True
    priority: int = 0


class TogglePayload(BaseModel):
    enabled: bool


class FallbackPayload(BaseModel):
    policies: list[dict[str, Any]]


class IntentStrategiesPayload(BaseModel):
    strategies: list[dict[str, Any]]


class IntentPoolItemPayload(BaseModel):
    SKILL_CODE: str = Field(..., min_length=1)
    NAME: str = Field(..., min_length=1)
    TARGET_TYPE: str = "DIRECT"
    AGENT_ID: str = ""
    CHANNEL: str = "JZYY"
    KEYWORDS: str = ""
    ACTION_TYPE: str = "JUMP"
    JUMP_TARGET: str = ""
    MCP_SERVER: str = ""
    MCP_TOOL: str = ""
    MCP_PARAMS: str = ""
    EXTRA_PARAMS: str = ""
    CREATED_AT: str = ""
    UPDATED_AT: str = ""


class EntryChannelsPayload(BaseModel):
    channels: list[dict[str, Any]]


class FallbackSampleMarkPayload(BaseModel):
    status: str = Field(..., min_length=1)
    note: str = ""


class DemoUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1)
    content_type: str = ""
    data_base64: str = Field(..., min_length=1)
    session_id: str = "demo"


class AgentConfigUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1)
    data_base64: str = Field(..., min_length=1)


@dataclass
class WebAgentState:
    agent: UniversalEntryAgent | None
    lock: threading.Lock
    registry: DemoRegistry
    warming: bool = False
    warmup_error: str = ""


def _ask_quietly(
    agent: UniversalEntryAgent,
    message: str,
    session_id: str,
    queue_wait_ms: float,
) -> str:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO(),
    ):
        return agent.ask(
            message,
            session_id=session_id,
            queue_wait_ms=queue_wait_ms,
        )


def _safe_upload_name(filename: str) -> str:
    name = Path(filename).name.strip() or "upload.bin"
    return re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name)[:120]


def _load_structured_config(raw: bytes, filename: str) -> dict[str, Any]:
    text = raw.decode("utf-8-sig", errors="replace")
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError:
            return _parse_simple_yaml(text)
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip("'\"")
        if value:
            data[key.strip()] = value
    return data


def _first_config_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        current: Any = data
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if current not in (None, "", []):
            return current
    return ""


def _extract_agent_config_fields(data: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "agent_id": _first_config_value(data, "agent_id", "id", "agent.id", "name"),
        "name": _first_config_value(data, "display_name", "name", "agent.name", "agent_id"),
        "description": _first_config_value(data, "description", "agent.description"),
        "api_url": _first_config_value(data, "api_url", "base_url", "baseUrl", "server.url"),
        "chat_completions_url": _first_config_value(
            data,
            "chat_completions_url",
            "chatCompletionsUrl",
            "chat.completions_url",
        ),
        "model": _first_config_value(data, "model", "model_name", "agent.model"),
        "adapter_type": _first_config_value(data, "adapter_type", "adapter", "protocol"),
        "method": _first_config_value(data, "method", "http.method"),
        "timeout_seconds": _first_config_value(data, "timeout_seconds", "timeout", "timeoutSeconds"),
        "business_type": _first_config_value(data, "business_type", "businessType", "category"),
        "headers": _first_config_value(data, "headers", "http.headers"),
        "request_template": _first_config_value(data, "request_template", "requestTemplate", "request.body"),
        "keywords": _first_config_value(data, "keywords", "triggers"),
        "examples": _first_config_value(data, "examples", "sample_questions"),
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [])}


def _attachment_context(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""
    lines = ["", "", "【用户上传附件】"]
    for index, item in enumerate(attachments, start=1):
        name = str(item.get("name") or item.get("filename") or "未命名文件")
        content_type = str(item.get("content_type") or item.get("type") or "未知类型")
        size = item.get("size")
        path = str(item.get("path") or "")
        parts = [f"{index}. 文件名：{name}", f"类型：{content_type}"]
        if isinstance(size, (int, float)):
            parts.append(f"大小：{int(size)} 字节")
        if path:
            parts.append(f"本地路径：{path}")
        lines.append("；".join(parts))
    lines.append("请在回答或调用业务智能体时结合这些附件信息。")
    return "\n".join(lines)


def _close_quietly(agent: UniversalEntryAgent, lock: threading.Lock) -> None:
    with lock:
        agent.close()


def _ensure_agent(state: WebAgentState, config: AgentConfig) -> UniversalEntryAgent:
    if state.agent is None:
        state.agent = UniversalEntryAgent(config)
    return state.agent


def _warmup_agent(state: WebAgentState, config: AgentConfig) -> None:
    try:
        with state.lock:
            _ensure_agent(state, config)
            state.warmup_error = ""
    except Exception as exc:  # noqa: BLE001 - expose concise status to UI.
        state.warmup_error = f"{exc.__class__.__name__}: {exc}"
    finally:
        state.warming = False


def _route_payload(agent: UniversalEntryAgent) -> dict[str, Any] | None:
    decision = agent.last_route_decision
    if decision is None:
        return None
    payload = agent._compact_route_result(decision)
    payload["fallback_used"] = decision.status == "fallback"
    payload["rewrite_used"] = bool(decision.intervention)
    if decision.intervention is not None:
        payload["rewrite_query"] = decision.intervention.rewrite_query
        payload["expanded_keywords"] = decision.intervention.expanded_keywords
        payload["suspected_domain"] = decision.intervention.suspected_domain
    payload["first_candidates"] = [
        item.compact()
        for item in decision.first_candidates[:5]
    ]
    payload["retry_candidates"] = [
        item.compact()
        for item in decision.retry_candidates[:5]
    ]
    return payload


def _enrich_direct_entry_route(
    registry: DemoRegistry,
    route: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not route:
        return route
    if str(route.get("execution_target") or "").upper() != "DIRECT":
        return route
    route.update(registry.resolve_entry_route(route))
    return route


def _render_direct_entry_answer(route: dict[str, Any], fallback_reply: str) -> str:
    if str(route.get("execution_target") or "").upper() != "DIRECT":
        return fallback_reply
    intent_name = str(route.get("intent_name") or "已识别业务")
    channel = str(route.get("entry_channel_label") or route.get("channel") or "未配置")
    jump_target = str(route.get("jump_target") or "未配置")
    confidence = _float_or_none(route.get("confidence"))
    lines = [
        f"已识别到您的业务意图：**{intent_name}**",
        "",
        f"- 办理渠道：`{channel}`",
        f"- 业务入口目标：`{jump_target}`",
    ]
    if route.get("entry_url"):
        lines.append(f"- 打开地址：{route['entry_url']}")
    elif route.get("entry_api_url"):
        lines.append(f"- 调用接口：`{route.get('entry_method') or 'POST'} {route['entry_api_url']}`")
    else:
        message = route.get("entry_message") or "入口渠道尚未配置 URL 模板或接口地址"
        lines.append(f"- 入口状态：{message}")
    if confidence is not None:
        lines.append(f"- 识别置信度：{confidence:.0%}")
    return "\n".join(lines)


def _render_pre_route_answer(route: dict[str, Any]) -> str:
    intent_name = str(route.get("intent_name") or "已识别业务")
    execution_target = str(route.get("execution_target") or "LLM")
    confidence = _float_or_none(route.get("confidence"))
    lines = [
        f"已识别到您的业务意图：**{intent_name}**",
        "",
        f"- 执行目标：`{execution_target}`",
    ]
    if route.get("agent_id"):
        lines.append(f"- 目标 Agent：`{route['agent_id']}`")
    if route.get("mcp_server") or route.get("mcp_tool"):
        lines.append(
            f"- MCP 工具：`{route.get('mcp_server') or '-'} / {route.get('mcp_tool') or '-'}`"
        )
    if confidence is not None:
        lines.append(f"- 识别置信度：{confidence:.0%}")
    return "\n".join(lines)


def _fallback_for_response(
    registry: DemoRegistry,
    agent: UniversalEntryAgent,
    reply: str,
) -> dict[str, Any] | None:
    decision = agent.last_route_decision
    if decision is not None and decision.status == "fallback":
        trigger = (
            "SECOND_STAGE_FAILED"
            if decision.intervention is not None
            else "LOW_CONFIDENCE"
        )
        return {
            "id": "intent_route_fallback",
            "name": "意图识别未命中样本",
            "enabled": True,
            "trigger": trigger,
            "threshold": decision.confidence,
            "action": "记录样本，不覆盖回复",
            "reply": "",
            "record_sample": True,
            "priority": 0,
        }
    if "Agent 执行失败" in reply or "MODEL_EXECUTION_FAILED" in reply:
        return registry.choose_fallback(trigger="AGENT_CALL_FAILED")
    return None


def _diagnosis_for_response(
    message: str,
    route: dict[str, Any] | None,
    reply: str,
) -> tuple[str, str] | None:
    if route is None:
        if _looks_like_business_request(message):
            return ("ROUTER_NOT_CALLED", "NO_ROUTE_BUSINESS_LIKE")
        if _looks_like_fact_boundary(reply):
            return ("FACT_BOUNDARY", "FACT_BOUNDARY")
        return None
    confidence = _float_or_none(route.get("confidence"))
    if confidence is not None and confidence < 0.65 and not route.get("fallback_used"):
        return ("LOW_CONFIDENCE_SELECTED", "LOW_CONFIDENCE_SELECTED")
    if route.get("rewrite_used") and not route.get("fallback_used"):
        return ("REWRITE_RECOVERED", "REWRITE_RECOVERED")
    return None


def _looks_like_business_request(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    business_terms = (
        "开户",
        "开通",
        "办理",
        "权限",
        "账户",
        "账号",
        "销户",
        "注销",
        "转签",
        "注册",
        "交易",
        "股票",
        "证券",
        "基金",
        "一码通",
        "科创板",
        "创业板",
        "京股通",
        "机构户",
        "机构开户",
    )
    return any(term in text for term in business_terms)


def _should_pre_route_business_request(message: str, attachments: list[dict[str, Any]]) -> bool:
    if attachments:
        return False
    text = message.strip()
    if not text:
        return False

    lowered = text.lower()
    non_business_markers = (
        "你是谁",
        "有哪些能力",
        "能做什么",
        "帮我总结",
        "解释一下",
        "是什么",
        "什么意思",
        "天气",
        "写一段",
    )
    if any(marker in lowered for marker in non_business_markers):
        return False

    action_terms = (
        "开户",
        "开通",
        "办理",
        "申请",
        "取消",
        "关闭",
        "注销",
        "销户",
        "修改",
        "变更",
        "设置",
        "转挂",
        "转托管",
        "双录",
        "测评",
        "权限不足",
        "没有资格",
        "没权限",
        "买不了",
        "提示",
        "入口",
        "下单",
    )
    object_terms = (
        "股票",
        "证券",
        "账户",
        "一码通",
        "中登",
        "中国结算",
        "科创",
        "创业板",
        "港股通",
        "北交所",
        "新三板",
        "st",
        "风险警示",
        "可转债",
        "期权",
        "两融",
        "融资融券",
        "退市",
        "适当性",
        "专业投资者",
        "沪伦通",
        "存托凭证",
        "基金",
    )
    stock_prefix_patterns = (
        r"\b688\d*",
        r"\b300\d*",
        r"\b8\d{2}\d*",
        r"688\s*开头",
        r"300\s*开头",
    )
    has_action = any(term in lowered for term in action_terms)
    has_object = any(term in lowered for term in object_terms) or any(
        re.search(pattern, lowered) for pattern in stock_prefix_patterns
    )
    return has_action and has_object


def _looks_like_fact_boundary(reply: str) -> bool:
    text = reply.strip()
    if not text:
        return False
    markers = (
        "无法确认",
        "不能确认",
        "没有足够依据",
        "缺少依据",
        "暂时无法核实",
    )
    return any(marker in text for marker in markers)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _agent_for_route(
    registry: DemoRegistry,
    route: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not route:
        return None
    agent_id = str(route.get("agent_id") or "")
    if not agent_id:
        intent_name = str(route.get("intent_name") or "")
        for item in registry.list_agents():
            if item.get("name") == intent_name:
                return {"agent_id": item.get("agent_id"), "name": item.get("name")}
        return None
    for item in registry.list_agents():
        if item.get("agent_id") == agent_id:
            return {"agent_id": item.get("agent_id"), "name": item.get("name")}
    return {"agent_id": agent_id, "name": agent_id}


def _reload_router_quietly(agent: UniversalEntryAgent, lock: threading.Lock) -> None:
    with lock:
        agent.reload_intent_router()


def _extract_openai_user_text(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        parts.append(str(block.get("text") or ""))
                return "\n".join(part for part in parts if part)
    return str(payload.get("prompt") or payload.get("input") or "")


def _extract_http_agent_text(data: Any) -> str:
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return str(data or "").strip()

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content.strip()
            text = first.get("text")
            if text:
                return str(text).strip()

    for key in ("answer", "reply", "content", "message", "result", "output"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _extract_http_agent_text(value)
            if nested:
                return nested
    return json_dump_compact(data)


def json_dump_compact(data: Any) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _openai_chat_response(agent_id: str, text: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{agent_id}-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": agent_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            },
        ],
    }


def _render_http_template(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        result = value
        for key, replacement in variables.items():
            if isinstance(replacement, (str, int, float, bool)):
                result = result.replace("{" + key + "}", str(replacement))
        return result
    if isinstance(value, list):
        return [_render_http_template(item, variables) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _render_http_template(item, variables)
            for key, item in value.items()
        }
    return value


def _call_http_agent_adapter(
    registry: DemoRegistry,
    agent_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    agent = next(
        (item for item in registry.list_agents() if item.get("agent_id") == agent_id),
        None,
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")
    if not agent.get("enabled", True):
        raise HTTPException(status_code=400, detail="agent disabled")
    api_url = str(agent.get("api_url") or "").strip()
    if not api_url:
        raise HTTPException(status_code=400, detail="agent api_url is required")

    user_text = _extract_openai_user_text(payload)
    request_payload = {
        "message": user_text,
        "query": user_text,
        "text": user_text,
        "agent_id": agent_id,
        "messages": payload.get("messages") or [],
        "session_id": payload.get("session_id") or "",
    }
    variables = {
        **request_payload,
        "input": user_text,
    }
    template = agent.get("request_template")
    if isinstance(template, dict) and template:
        request_payload = _render_http_template(template, variables)
    headers = dict(agent.get("headers") or {})
    headers.setdefault("Content-Type", "application/json")
    timeout = float(agent.get("timeout_seconds") or 120)
    method = str(agent.get("method") or "POST").upper()

    try:
        with httpx.Client(timeout=timeout) as client:
            if method == "GET":
                response = client.get(
                    api_url,
                    params={
                        "message": user_text,
                        "query": user_text,
                        "text": user_text,
                        "agent_id": agent_id,
                    },
                    headers=headers,
                )
            else:
                response = client.request(
                    method,
                    api_url,
                    json=request_payload,
                    headers=headers,
                )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            data: Any
            if "json" in content_type.lower():
                data = response.json()
            else:
                data = response.text
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"HTTP agent call failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    return _openai_chat_response(agent_id, _extract_http_agent_text(data))


def create_app(config: AgentConfig) -> FastAPI:
    registry = DemoRegistry(config)
    registry.sync_config_agent_registry(registry.list_agents())
    registry.sync_intent_router_settings(registry.list_intent_strategies())
    state = WebAgentState(
        agent=None,
        lock=threading.Lock(),
        registry=registry,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if state.agent is not None:
                await asyncio.to_thread(_close_quietly, state.agent, state.lock)

    app = FastAPI(
        title="意图路由 Agent Web",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/", response_class=HTMLResponse, response_model=None)
    def index() -> Response:
        frontend_dist = config.paths.root / "frontend" / "dist"
        if frontend_dist.exists():
            return RedirectResponse(url="/demo")
        return WEB_HTML

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/demo/status")
    def demo_status() -> dict[str, Any]:
        return {
            "status": "ok",
            "agent_loaded": state.agent is not None,
            "agent_warming": state.warming,
            "warmup_error": state.warmup_error,
        }

    @app.post("/api/demo/warmup")
    def demo_warmup() -> dict[str, Any]:
        if state.agent is not None:
            return {"started": False, "agent_loaded": True}
        if state.warming:
            return {"started": False, "agent_warming": True}
        state.warming = True
        state.warmup_error = ""
        thread = threading.Thread(
            target=_warmup_agent,
            args=(state, config),
            daemon=True,
        )
        thread.start()
        return {"started": True, "agent_warming": True}

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        message = request.message.strip()
        session_id = request.session_id.strip() or "web"
        queued_at = time.perf_counter()
        with state.lock:
            agent = _ensure_agent(state, config)
            queue_wait_ms = (time.perf_counter() - queued_at) * 1000
            reply = _ask_quietly(
                agent,
                message,
                session_id,
                queue_wait_ms,
            )
        summary = agent.last_trace_summary
        return ChatResponse(
            reply=reply,
            session_id=session_id,
            trace_id=str(summary.get("trace_id") or ""),
            elapsed_ms=float(summary.get("elapsed_ms") or 0.0),
        )

    @app.post("/api/demo/chat", response_model=DemoChatResponse)
    def demo_chat(request: DemoChatRequest) -> DemoChatResponse:
        message = request.message.strip()
        session_id = request.session_id.strip() or "demo"
        effective_message = f"{message}{_attachment_context(request.attachments)}"
        queued_at = time.perf_counter()
        pre_route = _should_pre_route_business_request(message, request.attachments)
        with state.lock:
            agent = _ensure_agent(state, config)
            queue_wait_ms = (time.perf_counter() - queued_at) * 1000
            if pre_route:
                try:
                    agent.route_business_intent_first(
                        effective_message,
                        session_id,
                        queue_wait_ms=queue_wait_ms,
                    )
                    reply = ""
                    route = _route_payload(agent)
                    if route is not None:
                        route["pre_react_routed"] = True
                    route = _enrich_direct_entry_route(state.registry, route)
                    fallback = _fallback_for_response(
                        state.registry,
                        agent,
                        reply,
                    )
                except Exception as exc:  # noqa: BLE001
                    reply = f"业务意图路由失败：{exc.__class__.__name__}。{exc}"
                    route = None
                    fallback = state.registry.choose_fallback(trigger="AGENT_CALL_FAILED")
            else:
                reply = _ask_quietly(
                    agent,
                    effective_message,
                    session_id,
                    queue_wait_ms,
                )
                route = _enrich_direct_entry_route(state.registry, _route_payload(agent))
                fallback = _fallback_for_response(
                    state.registry,
                    agent,
                    reply,
                )
            summary = dict(agent.last_trace_summary)
        answer = str(fallback.get("reply") or reply) if fallback else reply
        if pre_route and fallback is not None and not answer:
            answer = "当前问题暂未匹配到明确的业务入口，我已记录该问题用于后续优化。"
        if fallback is None and route is not None:
            answer = _render_direct_entry_answer(route, answer)
            if pre_route and not answer:
                answer = _render_pre_route_answer(route)
        if fallback is not None:
            state.registry.record_fallback_sample(
                user_query=effective_message,
                session_id=session_id,
                route=route,
                fallback=fallback,
                answer=answer,
            )
        else:
            diagnosis = _diagnosis_for_response(effective_message, route, reply)
            if diagnosis is not None:
                diagnosis_type, trigger = diagnosis
                state.registry.record_diagnostic_sample(
                    user_query=effective_message,
                    session_id=session_id,
                    route=route,
                    diagnosis_type=diagnosis_type,
                    trigger=trigger,
                    answer=answer,
                )
        response_type = str(route.get("execution_target") or "message").lower() if route else "message"
        return DemoChatResponse(
            message_id=str(summary.get("trace_id") or f"msg-{int(time.time() * 1000)}"),
            answer=answer,
            type=response_type,
            session_id=session_id,
            route=route if request.debug else None,
            agent=_agent_for_route(state.registry, route),
            fallback=fallback,
            trace_id=str(summary.get("trace_id") or ""),
            latency_ms=float(summary.get("elapsed_ms") or 0.0),
        )

    @app.post("/api/demo/uploads")
    def upload_demo_file(payload: DemoUploadRequest) -> dict[str, Any]:
        try:
            raw = base64.b64decode(payload.data_base64, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid base64 file data") from exc
        max_size = 20 * 1024 * 1024
        if len(raw) > max_size:
            raise HTTPException(status_code=413, detail="file too large, max 20MB")

        session_id = re.sub(r"[^0-9A-Za-z._-]+", "_", payload.session_id or "demo")[:80]
        filename = _safe_upload_name(payload.filename)
        stored_name = f"{int(time.time() * 1000)}_{filename}"
        upload_dir = config.paths.root / "qwenpaw_runtime" / "demo" / "uploads" / session_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / stored_name
        target.write_bytes(raw)
        file_payload = {
            "id": stored_name,
            "name": filename,
            "size": len(raw),
            "content_type": payload.content_type or "application/octet-stream",
            "path": str(target),
        }
        return {"file": file_payload}

    @app.post("/api/demo/agent-configs")
    def upload_agent_config(payload: AgentConfigUploadRequest) -> dict[str, Any]:
        try:
            raw = base64.b64decode(payload.data_base64, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid base64 config data") from exc
        max_size = 50 * 1024 * 1024
        if len(raw) > max_size:
            raise HTTPException(status_code=413, detail="config file too large, max 50MB")

        filename = _safe_upload_name(payload.filename)
        stored_name = f"{int(time.time() * 1000)}_{filename}"
        config_dir = config.paths.root / "qwenpaw_runtime" / "demo" / "agent_configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        target = config_dir / stored_name
        target.write_bytes(raw)

        parsed: dict[str, Any] = {}
        try:
            parsed = _load_structured_config(raw, filename)
        except Exception:
            parsed = {}
        fields = _extract_agent_config_fields(parsed)
        fields["config_file_name"] = filename
        fields["config_file_path"] = str(target)
        return {
            "file": {
                "name": filename,
                "size": len(raw),
                "path": str(target),
            },
            "fields": fields,
        }

    @app.get("/api/demo/agents")
    def list_demo_agents() -> dict[str, Any]:
        return {"agents": state.registry.list_agents()}

    @app.post("/api/demo/agents")
    def create_demo_agent(payload: AgentPayload) -> dict[str, Any]:
        try:
            agent = state.registry.save_agent(payload.model_dump())
            if state.agent is not None:
                _reload_router_quietly(state.agent, state.lock)
            return {"agent": agent}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/demo/agents/{agent_id}")
    def update_demo_agent(agent_id: str, payload: AgentPayload) -> dict[str, Any]:
        data = payload.model_dump()
        data["agent_id"] = agent_id
        try:
            agent = state.registry.save_agent(data)
            if state.agent is not None:
                _reload_router_quietly(state.agent, state.lock)
            return {"agent": agent}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/demo/agents/{agent_id}/toggle")
    def toggle_demo_agent(agent_id: str, payload: TogglePayload) -> dict[str, Any]:
        try:
            agent = state.registry.toggle_agent(agent_id, payload.enabled)
            if state.agent is not None:
                _reload_router_quietly(state.agent, state.lock)
            return {"agent": agent}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="agent not found") from exc

    @app.post("/api/demo/agents/{agent_id}/test")
    def test_demo_agent(agent_id: str) -> dict[str, Any]:
        for item in state.registry.list_agents():
            if item.get("agent_id") == agent_id:
                if not item.get("enabled", True):
                    return {"ok": False, "message": "智能体未启用"}
                target = str(item.get("execution_target") or item.get("target_type") or "").upper()
                source = str(item.get("source") or "").upper()
                if target == "AGENT" and source == "THIRD_PARTY" and not (
                    item.get("api_url") or item.get("chat_completions_url")
                ):
                    return {
                        "ok": False,
                        "message": "Agent 缺少执行地址，请填写 API URL 或导入包含执行地址的配置文件",
                    }
                if target == "MCP":
                    missing = [
                        label
                        for label, value in {
                            "MCP Server": item.get("mcp_server"),
                            "MCP Tool": item.get("mcp_tool"),
                            "MCP Server URL": item.get("mcp_url"),
                        }.items()
                        if not value
                    ]
                    if missing:
                        return {
                            "ok": False,
                            "message": f"MCP 配置缺少：{'、'.join(missing)}",
                        }
                return {"ok": True, "message": "配置校验通过"}
        raise HTTPException(status_code=404, detail="agent not found")

    @app.get("/api/demo/fallbacks")
    def list_demo_fallbacks() -> dict[str, Any]:
        return {"policies": state.registry.list_fallbacks()}

    @app.put("/api/demo/fallbacks")
    def save_demo_fallbacks(payload: FallbackPayload) -> dict[str, Any]:
        return {"policies": state.registry.save_fallbacks(payload.policies)}

    @app.get("/api/demo/intent-strategies")
    def list_demo_intent_strategies() -> dict[str, Any]:
        return {"strategies": state.registry.list_intent_strategies()}

    @app.put("/api/demo/intent-strategies")
    def save_demo_intent_strategies(
        payload: IntentStrategiesPayload,
    ) -> dict[str, Any]:
        strategies = state.registry.save_intent_strategies(payload.strategies)
        if state.agent is not None:
            _reload_router_quietly(state.agent, state.lock)
        return {"strategies": strategies}

    @app.get("/api/demo/entry-channels")
    def list_demo_entry_channels() -> dict[str, Any]:
        return {"channels": state.registry.list_entry_channels()}

    @app.put("/api/demo/entry-channels")
    def save_demo_entry_channels(payload: EntryChannelsPayload) -> dict[str, Any]:
        try:
            return {"channels": state.registry.save_entry_channels(payload.channels)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/demo/intent-pool")
    def list_demo_intent_pool() -> dict[str, Any]:
        try:
            return state.registry.list_intent_pool()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/demo/intent-pool")
    def create_demo_intent_pool_item(payload: IntentPoolItemPayload) -> dict[str, Any]:
        try:
            item = state.registry.save_intent_pool_item(payload.model_dump())
            if state.agent is not None:
                _reload_router_quietly(state.agent, state.lock)
            return {"intent": item}
        except PermissionError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/demo/intent-pool/{row_id}")
    def update_demo_intent_pool_item(
        row_id: str,
        payload: IntentPoolItemPayload,
    ) -> dict[str, Any]:
        try:
            item = state.registry.save_intent_pool_item(
                payload.model_dump(),
                row_id=row_id,
            )
            if state.agent is not None:
                _reload_router_quietly(state.agent, state.lock)
            return {"intent": item}
        except PermissionError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="intent not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/demo/intent-pool/{row_id}")
    def delete_demo_intent_pool_item(row_id: str) -> dict[str, Any]:
        try:
            state.registry.delete_intent_pool_item(row_id)
            if state.agent is not None:
                _reload_router_quietly(state.agent, state.lock)
            return {"ok": True}
        except PermissionError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="intent not found") from exc

    @app.post("/api/demo/rewrite-samples/{sample_id}/promote")
    def promote_demo_rewrite_sample(sample_id: str) -> dict[str, Any]:
        try:
            result = state.registry.promote_rewrite_sample(sample_id)
            if state.agent is not None:
                _reload_router_quietly(state.agent, state.lock)
            return result
        except PermissionError as exc:
            raise HTTPException(status_code=423, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="rewrite sample not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/demo/fallback-samples")
    def list_demo_fallback_samples(limit: int = 50) -> dict[str, Any]:
        return {"samples": state.registry.list_fallback_samples(limit=limit)}

    @app.put("/api/demo/fallback-samples/{sample_id}/mark")
    def mark_demo_fallback_sample(
        sample_id: str,
        payload: FallbackSampleMarkPayload,
    ) -> dict[str, Any]:
        try:
            sample = state.registry.mark_fallback_sample(
                sample_id,
                status=payload.status,
                note=payload.note,
            )
            return {"sample": sample}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="fallback sample not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/demo/fallback-samples/{sample_id}")
    def delete_demo_fallback_sample(sample_id: str) -> dict[str, Any]:
        try:
            state.registry.delete_fallback_sample(sample_id)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="fallback sample not found") from exc

    @app.post("/api/demo/http-agents/{agent_id}/v1/chat/completions")
    def http_agent_chat_completions(
        agent_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return _call_http_agent_adapter(state.registry, agent_id, payload)

    frontend_dist = config.paths.root / "frontend" / "dist"
    if frontend_dist.exists():
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount(
                "/demo/assets",
                StaticFiles(directory=str(assets_dir)),
                name="demo-assets",
            )

        @app.get("/demo", response_class=FileResponse)
        def demo_index() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")

        @app.get("/demo/{path:path}", response_class=FileResponse)
        def demo_spa(path: str) -> FileResponse:
            target = (frontend_dist / path).resolve()
            try:
                target.relative_to(frontend_dist.resolve())
            except ValueError:
                target = frontend_dist / "index.html"
            if target.is_file():
                return FileResponse(target)
            return FileResponse(frontend_dist / "index.html")

    return app


WEB_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>意图路由 Agent</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d8dee9;
      --text: #172033;
      --muted: #65728a;
      --brand: #2563eb;
      --brand-dark: #1e40af;
      --user: #e8f0ff;
      --agent: #ffffff;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-rows: 56px 1fr;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      white-space: nowrap;
    }
    .mark {
      width: 28px;
      height: 28px;
      display: grid;
      place-items: center;
      border-radius: 6px;
      background: var(--brand);
      color: #fff;
      font-size: 15px;
    }
    .session {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      min-width: 0;
    }
    .session input {
      width: 220px;
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      color: var(--text);
      outline: none;
      background: #fff;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 0;
      min-height: 0;
    }
    .chat {
      display: grid;
      grid-template-rows: 1fr auto;
      min-width: 0;
      min-height: calc(100vh - 56px);
      border-right: 1px solid var(--line);
    }
    .messages {
      overflow: auto;
      padding: 24px;
    }
    .empty {
      height: 100%;
      display: grid;
      place-items: center;
      color: var(--muted);
      text-align: center;
      line-height: 1.8;
    }
    .msg {
      display: flex;
      margin: 0 0 16px;
    }
    .msg.user { justify-content: flex-end; }
    .bubble {
      max-width: min(760px, 78%);
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--agent);
      line-height: 1.7;
      font-size: 14px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      box-shadow: 0 1px 2px rgba(18, 28, 45, 0.04);
    }
    .user .bubble {
      background: var(--user);
      border-color: #c7d7ff;
    }
    .meta {
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .composer {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      padding: 16px 24px 20px;
      background: var(--panel);
      border-top: 1px solid var(--line);
    }
    textarea {
      width: 100%;
      min-height: 48px;
      max-height: 160px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      outline: none;
      color: var(--text);
      line-height: 1.5;
      font-family: inherit;
      font-size: 14px;
    }
    textarea:focus, .session input:focus {
      border-color: var(--brand);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
    }
    button {
      width: 92px;
      height: 48px;
      border: 0;
      border-radius: 8px;
      color: #fff;
      background: var(--brand);
      font-weight: 700;
      cursor: pointer;
    }
    button:hover { background: var(--brand-dark); }
    button:disabled {
      cursor: not-allowed;
      background: #9aa8bd;
    }
    aside {
      background: #fbfcff;
      padding: 20px;
      overflow: auto;
    }
    aside h2 {
      margin: 0 0 12px;
      font-size: 15px;
    }
    .tips {
      display: grid;
      gap: 8px;
    }
    .tip {
      width: 100%;
      min-height: 38px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      text-align: left;
      cursor: pointer;
      line-height: 1.45;
      font-size: 13px;
    }
    .tip:hover {
      border-color: var(--brand);
      color: var(--brand-dark);
    }
    .status {
      margin-top: 16px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.6;
    }
    .error {
      color: var(--danger);
    }
    @media (max-width: 900px) {
      header {
        padding: 0 14px;
      }
      main {
        grid-template-columns: 1fr;
      }
      aside {
        display: none;
      }
      .messages {
        padding: 16px;
      }
      .composer {
        padding: 12px 14px 14px;
      }
      .session input {
        width: 140px;
      }
      .bubble {
        max-width: 90%;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="brand"><span class="mark">路</span><span>意图路由 Agent</span></div>
      <label class="session">Session <input id="session" value="web" /></label>
    </header>
    <main>
      <section class="chat">
        <div id="messages" class="messages">
          <div class="empty">输入一句话开始测试。<br />可以问能力、查天气、或发起机构开户类任务。</div>
        </div>
        <form id="form" class="composer">
          <textarea id="input" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>
          <button id="send" type="submit">发送</button>
        </form>
      </section>
      <aside>
        <h2>快捷测试</h2>
        <div class="tips">
          <div class="tip">你有哪些工具和技能可用？</div>
          <div class="tip">帮我查一下杭州今天的天气</div>
          <div class="tip">帮我开个机构户</div>
          <div class="tip">只做意图识别：客户想开通港股通权限</div>
        </div>
        <div id="status" class="status">服务状态：待检测</div>
      </aside>
    </main>
  </div>
  <script>
    const messages = document.querySelector("#messages");
    const input = document.querySelector("#input");
    const form = document.querySelector("#form");
    const send = document.querySelector("#send");
    const session = document.querySelector("#session");
    const status = document.querySelector("#status");

    function clearEmpty() {
      const empty = messages.querySelector(".empty");
      if (empty) empty.remove();
    }

    function addMessage(role, text) {
      clearEmpty();
      const row = document.createElement("div");
      row.className = `msg ${role}`;
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = role === "user" ? "你" : "意图路由 Agent";
      const content = document.createElement("div");
      content.textContent = text;
      bubble.append(meta, content);
      row.appendChild(bubble);
      messages.appendChild(row);
      messages.scrollTop = messages.scrollHeight;
      return content;
    }

    async function submitMessage(text) {
      const message = text.trim();
      if (!message) return;
      input.value = "";
      addMessage("user", message);
      const agentContent = addMessage("agent", "思考中...");
      send.disabled = true;
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            message,
            session_id: session.value.trim() || "web"
          })
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        agentContent.textContent = data.reply || "(空回复)";
      } catch (error) {
        agentContent.classList.add("error");
        agentContent.textContent = `请求失败：${error.message}`;
      } finally {
        send.disabled = false;
        input.focus();
      }
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      submitMessage(input.value);
    });

    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    document.querySelectorAll(".tip").forEach(tip => {
      tip.addEventListener("click", () => {
        input.value = tip.textContent;
        input.focus();
      });
    });

    fetch("/api/health")
      .then(response => response.json())
      .then(data => {
        status.textContent = `服务状态：${data.status}`;
      })
      .catch(error => {
        status.classList.add("error");
        status.textContent = `服务状态：异常 ${error.message}`;
      });
  </script>
</body>
</html>"""
