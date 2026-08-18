from __future__ import annotations

import csv
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AgentConfig
from .diagnostics import elapsed_ms, get_current_trace


LOGGER = logging.getLogger(__name__)

GENERIC_ACTION_TERMS = {
    "开通",
    "开户",
    "申请",
    "办理",
    "设置",
    "修改",
    "变更",
    "调整",
    "重置",
    "找回",
    "取消",
    "关闭",
    "终止",
    "注销",
    "销户",
    "撤销",
    "申报",
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [
                    str(item).strip()
                    for item in data
                    if str(item).strip()
                ]
        except json.JSONDecodeError:
            pass
    parts = re.split(r"[\n,，;；|、]+", text)
    result = []
    for part in parts:
        cleaned = part.strip()
        cleaned = re.sub(r"^[-*]\s*", "", cleaned)
        cleaned = cleaned.strip().strip('"').strip("'").strip()
        if cleaned:
            result.append(cleaned)
    return result


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value.lower().strip())


def _expanded_query_norms(query_norm: str) -> list[str]:
    """Lightweight business synonym expansion for rule recall only."""
    replacements = {
        "调整": "设置",
        "调一下": "设置",
        "变更": "设置",
        "修改": "设置",
        "维护": "设置",
        "标识": "设置",
        "标签": "设置",
        "评估": "测评",
        "测评": "风险测评",
        "重做": "重新做",
        "再做": "重新做",
    }
    variants: list[str] = []
    for source, target in replacements.items():
        if source in query_norm:
            variants.append(query_norm.replace(source, target))
    return list(dict.fromkeys(item for item in variants if item != query_norm))


def _truthy_enabled(value: Any) -> bool:
    if value in (None, ""):
        return True
    text = str(value).strip().lower()
    return text not in {"0", "false", "no", "n", "disabled", "停用", "禁用", "否"}


def _first(row: dict[str, Any], aliases: list[str], default: str = "") -> str:
    normalized = {str(k).strip().lower(): v for k, v in row.items()}
    for name in aliases:
        value = normalized.get(name.strip().lower())
        if value not in (None, ""):
            return str(value).strip()
    return default


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if match:
        try:
            data = json.loads(match.group(1))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


@dataclass(frozen=True)
class IntentDefinition:
    intent_id: str
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    agent_id: str = ""
    priority: int = 0
    enabled: bool = True
    source: str = "config"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def route_key(self) -> str:
        key = self.metadata.get("_route_key")
        if key:
            return str(key)
        name_key = _norm(self.name)
        return f"name:{name_key}" if name_key else f"id:{self.intent_id}"

    def compact(self) -> dict[str, Any]:
        data = {
            "route_key": self.route_key,
            "intent_id": self.intent_id,
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords[:20],
            "examples": self.examples[:5],
            "agent_id": self.agent_id,
            "priority": self.priority,
        }
        for key in (
            "TARGET_TYPE",
            "AGENT_ID",
            "ACTION_TYPE",
            "JUMP_TARGET",
            "CHANNEL",
            "MCP_PARAMS",
            "MCP_TOOL",
            "MCP_SERVER",
            "EXTRA_PARAMS",
        ):
            value = self.metadata.get(key)
            if value not in (None, ""):
                data[key.lower()] = value
        return data


@dataclass(frozen=True)
class RecalledIntent:
    intent: IntentDefinition
    score: float
    matched_terms: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)

    def compact(self) -> dict[str, Any]:
        data = self.intent.compact()
        data.update(
            {
                "score": round(self.score, 3),
                "matched_terms": self.matched_terms[:20],
                "matched_rules": self.matched_rules[:20],
            },
        )
        return data

    def compact_for_judgement(self) -> dict[str, Any]:
        """Return the minimal fields needed by the LLM adjudicator.

        Execution metadata such as CHANNEL/JUMP_TARGET/MCP_PARAMS is resolved
        from the selected intent after adjudication, so it does not need to be
        included in the prompt.
        """
        return {
            "route_key": self.intent.route_key,
            "intent_id": self.intent.intent_id,
            "name": self.intent.name,
            "score": round(self.score, 3),
            "matched_terms": self.matched_terms[:12],
            "keywords": self.intent.keywords[:8],
        }


@dataclass(frozen=True)
class LLMJudgement:
    decision: str
    route_key: str = ""
    intent_id: str = ""
    confidence: float = 0.0
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMIntervention:
    rewrite_query: str = ""
    expanded_keywords: list[str] = field(default_factory=list)
    suspected_domain: str = ""
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def expanded_text(self, original_query: str) -> str:
        parts = [original_query, self.rewrite_query, self.suspected_domain]
        parts.extend(self.expanded_keywords)
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class RouteDecision:
    status: str
    selected_intent: IntentDefinition | None = None
    confidence: float = 0.0
    reason: str = ""
    first_candidates: list[RecalledIntent] = field(default_factory=list)
    retry_candidates: list[RecalledIntent] = field(default_factory=list)
    intervention: LLMIntervention | None = None
    trace_id: str = ""

    @property
    def routed(self) -> bool:
        return self.status == "selected" and self.selected_intent is not None

    def to_log_record(self, user_query: str, session_id: str | None) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "time": datetime.now().isoformat(timespec="seconds"),
            "session_id": session_id or "default",
            "user_query": user_query,
            "status": self.status,
            "selected_intent": (
                self.selected_intent.compact() if self.selected_intent else None
            ),
            "confidence": self.confidence,
            "reason": self.reason,
            "first_candidates": [item.compact() for item in self.first_candidates],
            "retry_candidates": [item.compact() for item in self.retry_candidates],
            "intervention": (
                asdict(self.intervention) if self.intervention is not None else None
            ),
        }


class IntentCatalog:
    ID_ALIASES = [
        "intent_id",
        "intentid",
        "id",
        "意图id",
        "意图ID",
        "意图编号",
        "意图编码",
        "skill_id",
        "SKILL_ID",
        "skill_code",
        "SKILL_CODE",
        "技能ID",
        "技能编码",
        "技能代码",
    ]
    NAME_ALIASES = [
        "intent_name",
        "intentname",
        "name",
        "意图名称",
        "名称",
        "skill_name",
        "SKILL_NAME",
        "技能名称",
        "场景名称",
    ]
    DESC_ALIASES = [
        "description",
        "desc",
        "意图描述",
        "描述",
        "skill_desc",
        "SKILL_DESC",
        "技能描述",
        "人设提示词",
        "prompt",
    ]
    KEYWORD_ALIASES = [
        "keywords",
        "keyword",
        "trigger",
        "triggers",
        "trigger_words",
        "关键词",
        "触发词",
        "命中词",
        "样例问法",
        "用户问法",
    ]
    PATTERN_ALIASES = ["patterns", "pattern", "regex", "正则", "正则表达式"]
    EXAMPLE_ALIASES = [
        "examples",
        "example",
        "utterances",
        "utterance",
        "示例",
        "语料",
        "样本",
    ]
    AGENT_ALIASES = [
        "agent_id",
        "agent",
        "target_agent",
        "route_target",
        "智能体ID",
        "智能体",
        "路由目标",
        "渠道",
        "channel",
    ]

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.router_config = config.intent_router

    def load(self) -> list[IntentDefinition]:
        catalog_config = self.router_config.get("catalog", {})
        intents: list[IntentDefinition] = []

        if catalog_config.get("include_agent_registry", True):
            intents.extend(self._from_agent_registry())

        for item in catalog_config.get("inline_intents", []):
            if isinstance(item, dict):
                intent = self._from_mapping(item, source="config.inline")
                if intent is not None:
                    intents.append(intent)

        for item in catalog_config.get("csv_files", []):
            csv_config = {"path": item} if isinstance(item, str) else item
            if isinstance(csv_config, dict):
                intents.extend(self._from_csv(csv_config))

        return self._dedupe([intent for intent in intents if intent.enabled])

    def _from_agent_registry(self) -> list[IntentDefinition]:
        intents = []
        for item in self.config.agent_registry.get("agents", []):
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or item.get("id") or "").strip()
            if not agent_id:
                continue
            intents.append(
                IntentDefinition(
                    intent_id=f"agent:{agent_id}",
                    name=str(item.get("name") or agent_id).strip(),
                    description=str(item.get("description") or "").strip(),
                    keywords=_as_list(item.get("triggers")),
                    patterns=_as_list(item.get("patterns")),
                    examples=_as_list(item.get("examples")),
                    agent_id=agent_id,
                    priority=int(item.get("priority") or 0),
                    enabled=bool(item.get("enabled", True)),
                    source="agent_registry",
                    metadata={**item, "agent_registry": item},
                ),
            )
        return intents

    def _from_csv(self, csv_config: dict[str, Any]) -> list[IntentDefinition]:
        path_text = str(csv_config.get("path") or "").strip()
        if not path_text:
            return []
        path = Path(path_text)
        if not path.is_absolute():
            path = self.config.paths.root / path
        if not path.exists():
            if csv_config.get("required", False):
                raise FileNotFoundError(f"Intent catalog CSV not found: {path}")
            LOGGER.warning("Intent catalog CSV not found, skipped: %s", path)
            return []

        encodings = [str(csv_config.get("encoding") or "auto")]
        if encodings == ["auto"]:
            encodings = ["utf-8-sig", "utf-8", "gb18030"]

        last_error: Exception | None = None
        for encoding in encodings:
            try:
                with path.open("r", encoding=encoding, newline="") as handle:
                    reader = csv.DictReader(handle)
                    return [
                        intent
                        for row in reader
                        if (intent := self._from_mapping(row, source=str(path)))
                    ]
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        return []

    def _from_mapping(
        self,
        row: dict[str, Any],
        *,
        source: str,
    ) -> IntentDefinition | None:
        intent_id = _first(row, self.ID_ALIASES)
        name = _first(row, self.NAME_ALIASES)
        if not intent_id and name:
            intent_id = f"{source}:{name}"
        if not intent_id:
            return None

        priority_text = _first(row, ["priority", "优先级"], "0")
        try:
            priority = int(float(priority_text))
        except ValueError:
            priority = 0

        return IntentDefinition(
            intent_id=intent_id,
            name=name or intent_id,
            description=_first(row, self.DESC_ALIASES),
            keywords=_as_list(_first(row, self.KEYWORD_ALIASES)),
            patterns=_as_list(_first(row, self.PATTERN_ALIASES)),
            examples=_as_list(_first(row, self.EXAMPLE_ALIASES)),
            agent_id=_first(row, self.AGENT_ALIASES),
            priority=priority,
            enabled=_truthy_enabled(_first(row, ["enabled", "status", "是否启用"])),
            source=source,
            metadata={str(k): v for k, v in row.items()},
        )

    @staticmethod
    def _dedupe(intents: list[IntentDefinition]) -> list[IntentDefinition]:
        result: dict[str, IntentDefinition] = {}
        for intent in intents:
            key = IntentCatalog._identity_key(intent)
            intent_with_key = IntentCatalog._with_route_key(intent, key)
            existing = result.get(key)
            if existing is None:
                result[key] = intent_with_key
            else:
                result[key] = IntentCatalog._merge_duplicate(
                    existing,
                    intent_with_key,
                )
        return list(result.values())

    @staticmethod
    def _identity_key(intent: IntentDefinition) -> str:
        name_key = _norm(intent.name)
        if name_key:
            return f"name:{name_key}"
        return f"id:{intent.intent_id}"

    @staticmethod
    def _with_route_key(
        intent: IntentDefinition,
        route_key: str,
    ) -> IntentDefinition:
        metadata = dict(intent.metadata)
        metadata["_route_key"] = route_key
        return IntentDefinition(
            intent_id=intent.intent_id,
            name=intent.name,
            description=intent.description,
            keywords=intent.keywords,
            patterns=intent.patterns,
            examples=intent.examples,
            agent_id=intent.agent_id,
            priority=intent.priority,
            enabled=intent.enabled,
            source=intent.source,
            metadata=metadata,
        )

    @staticmethod
    def _merge_duplicate(
        left: IntentDefinition,
        right: IntentDefinition,
    ) -> IntentDefinition:
        # The same SKILL_CODE may appear once with a precise business name and
        # again with a generic display name. Keep the more specific name while
        # merging all trigger material.
        left_name = _norm(left.name)
        right_name = _norm(right.name)
        name = left.name if len(left_name) >= len(right_name) else right.name
        description = left.description or right.description
        if right.description and right.description not in description:
            description = (description + "\n" + right.description).strip()

        metadata = dict(left.metadata)
        metadata.setdefault("_duplicates", [])
        duplicates = metadata["_duplicates"]
        if isinstance(duplicates, list):
            duplicates.append(right.metadata)

        return IntentDefinition(
            intent_id=left.intent_id,
            name=name,
            description=description,
            keywords=list(dict.fromkeys([*left.keywords, *right.keywords])),
            patterns=list(dict.fromkeys([*left.patterns, *right.patterns])),
            examples=list(dict.fromkeys([*left.examples, *right.examples])),
            agent_id=left.agent_id or right.agent_id,
            priority=max(left.priority, right.priority),
            enabled=left.enabled or right.enabled,
            source=left.source,
            metadata=metadata,
        )


class RuleBasedMatcher:
    IMPLICIT_STOPWORDS = {
        "客户",
        "业务",
        "办理",
        "处理",
        "查询",
        "开通",
        "权限",
        "设置",
    }

    def __init__(self, intents: list[IntentDefinition]) -> None:
        self.intents = intents

    def recall(
        self,
        query: str,
        *,
        limit: int = 10,
        min_score: float = 1.0,
    ) -> list[RecalledIntent]:
        query_norm = _norm(query)
        if not query_norm:
            return []

        recalled = []
        for intent in self.intents:
            score, terms, rules = self._score_intent(query, query_norm, intent)
            if score >= min_score:
                recalled.append(
                    RecalledIntent(
                        intent=intent,
                        score=score,
                        matched_terms=terms,
                        matched_rules=rules,
                    ),
                )
        recalled.sort(
            key=lambda item: (
                item.score,
                item.intent.priority,
                len(item.matched_terms),
                item.intent.name,
            ),
            reverse=True,
        )
        return recalled[:limit]

    def _score_intent(
        self,
        query: str,
        query_norm: str,
        intent: IntentDefinition,
    ) -> tuple[float, list[str], list[str]]:
        score = float(intent.priority)
        terms: list[str] = []
        rules: list[str] = []
        query_norms = [query_norm, *_expanded_query_norms(query_norm)]

        name_norm = _norm(intent.name)
        if query_norm == name_norm:
            score += 100
            terms.append(intent.name)
            rules.append("name_exact")
        elif name_norm and any(name_norm in item for item in query_norms):
            score += 45 + min(len(name_norm), 20) * 0.5
            terms.append(intent.name)
            rules.append("name_contains")
        elif query_norm and query_norm in name_norm and len(query_norm) >= 2:
            score += 28
            terms.append(intent.name)
            rules.append("query_in_name")

        for keyword in intent.keywords:
            keyword_norm = _norm(keyword)
            if not keyword_norm:
                continue
            if query_norm == keyword_norm:
                score += 95
                terms.append(keyword)
                rules.append("keyword_exact")
            elif any(keyword_norm in item for item in query_norms):
                score += 35 + min(len(keyword_norm), 20) * 0.6
                terms.append(keyword)
                rules.append("keyword_contains")
            elif len(query_norm) >= 2 and query_norm in keyword_norm:
                score += 22
                terms.append(keyword)
                rules.append("query_in_keyword")

        for example in intent.examples:
            example_norm = _norm(example)
            if not example_norm:
                continue
            if query_norm == example_norm:
                score += 80
                terms.append(example)
                rules.append("example_exact")
            elif example_norm in query_norm or query_norm in example_norm:
                score += 25
                terms.append(example)
                rules.append("example_overlap")

        for pattern in intent.patterns:
            try:
                match = re.search(pattern, query)
            except re.error:
                LOGGER.warning("Invalid intent regex skipped: %s", pattern)
                continue
            if match:
                score += 85
                terms.append(pattern)
                rules.append("regex")

        for term in self._implicit_terms(intent):
            term_norm = _norm(term)
            if len(term_norm) < 2:
                continue
            if term_norm in self.IMPLICIT_STOPWORDS:
                continue
            if any(term_norm in item for item in query_norms):
                score += 8 + min(len(term_norm), 12) * 0.4
                terms.append(term)
                rules.append("implicit_term")

        return score, list(dict.fromkeys(terms)), list(dict.fromkeys(rules))

    @staticmethod
    def _implicit_terms(intent: IntentDefinition) -> list[str]:
        source = f"{intent.name} {intent.description}"
        terms = re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", source)
        grams: list[str] = []
        for term in terms:
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", term):
                grams.extend(term[index : index + 2] for index in range(len(term) - 1))
        return terms + grams


class OpenAICompatibleRouterClient:
    def __init__(self, config: AgentConfig) -> None:
        router_llm = config.intent_router.get("llm", {})
        self.enabled = bool(router_llm.get("enabled", True))
        self.base_url = str(
            router_llm.get("base_url") or config.llm.get("base_url") or "",
        ).rstrip("/")
        self.model = str(router_llm.get("model") or config.llm.get("model") or "")
        self.timeout = float(
            router_llm.get(
                "timeout_seconds",
                config.llm.get("timeout_seconds") or 60,
            ),
        )
        env_name = str(
            router_llm.get("api_key_env")
            or config.llm.get("api_key_env")
            or "LLM_API_KEY",
        )
        self.api_key = str(router_llm.get("api_key") or os.environ.get(env_name, ""))
        self.extra_body = dict(config.llm.get("extra_body") or {})
        self.extra_body.update(dict(router_llm.get("extra_body") or {}))
        self._clients: dict[int, Any] = {}
        self._clients_lock = threading.Lock()

    def close(self) -> None:
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()

    def _client(self):
        import httpx

        thread_id = threading.get_ident()
        with self._clients_lock:
            client = self._clients.get(thread_id)
            if client is None or getattr(client, "is_closed", False):
                client = httpx.Client(timeout=self.timeout)
                self._clients[thread_id] = client
            return client

    def adjudicate(
        self,
        query: str,
        candidates: list[RecalledIntent],
        *,
        retry: bool = False,
    ) -> LLMJudgement:
        if not self.enabled or not self.base_url or not self.model or not candidates:
            return LLMJudgement(decision="none", reason="llm_disabled_or_no_candidates")
        data = self._chat_json(
            self._adjudicate_prompt(query, candidates, retry=retry),
            operation="intent.llm.retry_adjudicate" if retry else "intent.llm.adjudicate",
        )
        route_key = str(data.get("route_key") or data.get("candidate_key") or "").strip()
        intent_id = str(data.get("intent_id") or "").strip()
        decision = str(data.get("decision") or "").strip().lower()
        if decision not in {"select", "none"}:
            decision = "select" if intent_id else "none"
        try:
            confidence = float(data.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        return LLMJudgement(
            decision=decision,
            route_key=route_key,
            intent_id=intent_id,
            confidence=max(0.0, min(confidence, 1.0)),
            reason=str(data.get("reason") or ""),
            raw=data,
        )

    def intervene(
        self,
        query: str,
        candidates: list[RecalledIntent],
        reason: str,
    ) -> LLMIntervention:
        if not self.enabled or not self.base_url or not self.model:
            return LLMIntervention(reason="llm_disabled")
        data = self._chat_json(
            self._intervention_prompt(query, candidates, reason),
            operation="intent.llm.intervention",
        )
        return LLMIntervention(
            rewrite_query=str(data.get("rewrite_query") or "").strip(),
            expanded_keywords=_as_list(data.get("expanded_keywords")),
            suspected_domain=str(data.get("suspected_domain") or "").strip(),
            reason=str(data.get("reason") or ""),
            raw=data,
        )

    def _chat_json(self, prompt: str, *, operation: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严格的意图路由 JSON 组件，只返回 JSON 对象。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "stream": False,
        }
        if self.extra_body:
            payload.update(self.extra_body)
            payload["stream"] = False

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        trace = get_current_trace()
        started = time.perf_counter()
        if trace is not None:
            trace.event(
                f"{operation}.start",
                operation=operation,
                model=self.model,
                prompt_chars=len(prompt),
                timeout_seconds=self.timeout,
            )
        try:
            response = self._client().post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            result = _extract_json_object(str(content))
            if trace is not None:
                trace.event(
                    f"{operation}.completed",
                    duration_ms=elapsed_ms(started),
                    operation=operation,
                    http_status=response.status_code,
                    response_chars=len(str(content)),
                )
            return result
        except Exception as exc:
            if trace is not None:
                trace.event(
                    f"{operation}.failed",
                    status="error",
                    duration_ms=elapsed_ms(started),
                    operation=operation,
                    error=f"{exc.__class__.__name__}: {exc}",
                )
            raise

    @staticmethod
    def _adjudicate_prompt(
        query: str,
        candidates: list[RecalledIntent],
        *,
        retry: bool,
    ) -> str:
        candidates_json = json.dumps(
            [item.compact_for_judgement() for item in candidates],
            ensure_ascii=False,
            indent=2,
        )
        phase = "二次补召回" if retry else "首次召回"
        return (
            f"当前阶段：{phase}\n"
            "请判断用户请求是否命中候选意图之一。\n"
            "规则：\n"
            "1. 只能选择候选列表中存在的 route_key。\n"
            "2. intent_id/SKILL_CODE 可能重复，不能单独作为唯一判断依据；业务意图以 name 为准。\n"
            "3. 如果候选中没有正确意图，必须返回 decision=none。\n"
            "4. 优先选择同时覆盖用户明确对象和目标动作的更具体候选；泛化候选只在没有具体候选覆盖时选择。\n"
            "5. 用户请求中的原因、背景、状态词只能作为辅助信息，不能覆盖用户明确表达的目标动作。\n"
            "6. 如果多个候选都可行，优先选择 score 更高且 matched_terms 更完整的候选。\n"
            "7. 不要输出解释性正文，只返回 JSON。\n\n"
            f"用户请求：{query}\n\n"
            f"候选意图：\n{candidates_json}\n\n"
            "返回格式：\n"
            '{"decision":"select|none","route_key":"候选route_key或空字符串",'
            '"intent_id":"候选intent_id或空字符串",'
            '"confidence":0.0,"reason":"一句话原因"}'
        )

    @staticmethod
    def _intervention_prompt(
        query: str,
        candidates: list[RecalledIntent],
        reason: str,
    ) -> str:
        candidates_json = json.dumps(
            [item.compact_for_judgement() for item in candidates],
            ensure_ascii=False,
            indent=2,
        )
        safety_constraints = (
            "Safety constraints for rewrite:\n"
            "1. Only rewrite words already expressed by the user into real synonyms.\n"
            "2. You may use your language knowledge for synonym expansion, but do not guess a business object, product, account type, or scenario that the user did not express.\n"
            "3. If the query only contains an unknown object plus a generic action such as cancel/open/modify/set, keep rewrite_query empty, expanded_keywords empty, and suspected_domain empty.\n"
            "4. suspected_domain must be empty unless it is directly expressed by the user or is a true synonym of the user's wording.\n\n"
        )
        return (
            safety_constraints +
            "第三级大模型未能在规则召回候选中确认正确意图。"
            "现在不要直接选择意图，也不要编造 intent_id。"
            "你的任务只是帮助第一级规则召回层补救：生成改写 query 和关键词，"
            "让系统用这些词重新跑一次规则匹配。\n\n"
            f"原始用户请求：{query}\n"
            f"首次失败原因：{reason}\n"
            f"首次候选：\n{candidates_json}\n\n"
            "返回 JSON：\n"
            '{"rewrite_query":"更标准的用户请求表达",'
            '"expanded_keywords":["关键词1","关键词2"],'
            '"suspected_domain":"可能业务域",'
            '"reason":"为什么这样改写"}'
        )


class IntentRouter:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.router_config = config.intent_router
        self.enabled = bool(self.router_config.get("enabled", False))
        self.top_k = int(self.router_config.get("top_k") or 5)
        rule_config = self.router_config.get("rule", {})
        self.min_score = float(rule_config.get("min_score", 1.0))
        self.direct_threshold = float(rule_config.get("direct_threshold", 9999.0))
        self.llm_fallback_threshold = float(
            rule_config.get("fallback_when_llm_unavailable_threshold", 85.0),
        )
        llm_config = self.router_config.get("llm", {})
        self.confidence_threshold = float(llm_config.get("confidence_threshold", 0.55))
        self.intervention_enabled = bool(llm_config.get("intervention_enabled", True))
        self.log_enabled = bool(self.router_config.get("logging", {}).get("enabled", True))
        self.intents = IntentCatalog(config).load() if self.enabled else []
        self.matcher = RuleBasedMatcher(self.intents)
        self.llm = OpenAICompatibleRouterClient(config)

    def close(self) -> None:
        self.llm.close()

    def route(self, query: str, session_id: str | None = None) -> RouteDecision:
        performance_trace = get_current_trace()
        trace_id = (
            performance_trace.trace_id
            if performance_trace is not None
            else datetime.now().strftime("%Y%m%d%H%M%S%f")
        )
        route_started = time.perf_counter()
        if performance_trace is not None:
            performance_trace.event(
                "intent.route.start",
                intent_count=len(self.intents),
                top_k=self.top_k,
            )
        if not self.enabled or not self.intents:
            decision = RouteDecision(
                status="disabled",
                reason="intent_router_disabled_or_empty",
                trace_id=trace_id,
            )
            self._log(query, session_id, decision)
            return decision

        recall_started = time.perf_counter()
        first = self.matcher.recall(query, limit=self.top_k, min_score=self.min_score)
        if performance_trace is not None:
            performance_trace.event(
                "intent.rule_recall.first",
                duration_ms=elapsed_ms(recall_started),
                candidates=len(first),
                candidate_details=[
                    {
                        "name": item.intent.name,
                        "route_key": item.intent.route_key,
                        "score": round(item.score, 3),
                        "matched_terms": item.matched_terms,
                        "matched_rules": item.matched_rules,
                    }
                    for item in first
                ],
            )
        direct = self._direct_match(first, trace_id)
        if direct is not None:
            if performance_trace is not None:
                performance_trace.event(
                    "intent.route.direct_match",
                    duration_ms=elapsed_ms(route_started),
                    selected_intent=direct.selected_intent.name,
                    confidence=direct.confidence,
                )
            self._log(query, session_id, direct)
            return direct

        adjudicate_started = time.perf_counter()
        judgement = self._safe_adjudicate(query, first, retry=False)
        if performance_trace is not None:
            performance_trace.event(
                "intent.judgement.first",
                duration_ms=elapsed_ms(adjudicate_started),
                decision=judgement.decision,
                route_key=judgement.route_key,
                confidence=judgement.confidence,
                reason=judgement.reason,
            )
        selected = self._selected_from_judgement(judgement, first)
        if selected is not None:
            decision = RouteDecision(
                status="selected",
                selected_intent=selected,
                confidence=judgement.confidence,
                reason=judgement.reason,
                first_candidates=first,
                trace_id=trace_id,
            )
            if performance_trace is not None:
                performance_trace.event(
                    "intent.route.selected",
                    duration_ms=elapsed_ms(route_started),
                    selected_intent=selected.name,
                    confidence=judgement.confidence,
                    retry=False,
                )
            self._log(query, session_id, decision)
            return decision

        if self._can_rule_fallback(judgement, first):
            top = first[0]
            decision = RouteDecision(
                status="selected",
                selected_intent=top.intent,
                confidence=min(top.score / 100, 1.0),
                reason="LLM 不可用，使用高置信规则召回结果。",
                first_candidates=first,
                trace_id=trace_id,
            )
            self._log(query, session_id, decision)
            return decision

        intervention = None
        retry_candidates: list[RecalledIntent] = []
        retry_judgement = LLMJudgement(
            decision="none",
            reason=judgement.reason or "first_stage_no_match",
        )
        if self._should_skip_intervention(query, first):
            decision = RouteDecision(
                status="fallback",
                confidence=judgement.confidence,
                reason=(
                    judgement.reason
                    or "low_confidence_generic_action_only; skip_llm_intervention"
                ),
                first_candidates=first,
                trace_id=trace_id,
            )
            if performance_trace is not None:
                performance_trace.event(
                    "intent.intervention.skipped",
                    duration_ms=elapsed_ms(route_started),
                    reason="low_confidence_generic_action_only",
                )
                performance_trace.event(
                    "intent.route.fallback",
                    duration_ms=elapsed_ms(route_started),
                    confidence=decision.confidence,
                    reason=decision.reason,
                )
            self._log(query, session_id, decision)
            return decision

        if self.intervention_enabled:
            intervention_started = time.perf_counter()
            intervention = self._safe_intervene(
                query,
                first,
                judgement.reason or "candidate_not_matched",
            )
            if performance_trace is not None:
                performance_trace.event(
                    "intent.intervention.completed",
                    duration_ms=elapsed_ms(intervention_started),
                    rewrite_query=intervention.rewrite_query if intervention else "",
                    expanded_keywords=(
                        intervention.expanded_keywords if intervention else []
                    ),
                    suspected_domain=(
                        intervention.suspected_domain if intervention else ""
                    ),
                )
            expanded = intervention.expanded_text(query) if intervention else query
            retry_recall_started = time.perf_counter()
            retry_candidates = self.matcher.recall(
                expanded,
                limit=self.top_k,
                min_score=self.min_score,
            )
            if performance_trace is not None:
                performance_trace.event(
                    "intent.rule_recall.retry",
                    duration_ms=elapsed_ms(retry_recall_started),
                    candidates=len(retry_candidates),
                    candidate_details=[
                        {
                            "name": item.intent.name,
                            "route_key": item.intent.route_key,
                            "score": round(item.score, 3),
                            "matched_terms": item.matched_terms,
                        }
                        for item in retry_candidates
                    ],
                )
            retry_adjudicate_started = time.perf_counter()
            retry_judgement = self._safe_adjudicate(
                expanded,
                retry_candidates,
                retry=True,
            )
            if performance_trace is not None:
                performance_trace.event(
                    "intent.judgement.retry",
                    duration_ms=elapsed_ms(retry_adjudicate_started),
                    decision=retry_judgement.decision,
                    route_key=retry_judgement.route_key,
                    confidence=retry_judgement.confidence,
                    reason=retry_judgement.reason,
                )
            selected = self._selected_from_judgement(retry_judgement, retry_candidates)
            if selected is not None:
                decision = RouteDecision(
                    status="selected",
                    selected_intent=selected,
                    confidence=retry_judgement.confidence,
                    reason=retry_judgement.reason,
                    first_candidates=first,
                    retry_candidates=retry_candidates,
                    intervention=intervention,
                    trace_id=trace_id,
                )
                if performance_trace is not None:
                    performance_trace.event(
                        "intent.route.selected",
                        duration_ms=elapsed_ms(route_started),
                        selected_intent=selected.name,
                        confidence=retry_judgement.confidence,
                        retry=True,
                    )
                self._log(query, session_id, decision)
                return decision

        decision = RouteDecision(
            status="fallback",
            confidence=max(judgement.confidence, retry_judgement.confidence),
            reason=retry_judgement.reason or judgement.reason or "no_intent_selected",
            first_candidates=first,
            retry_candidates=retry_candidates,
            intervention=intervention,
            trace_id=trace_id,
        )
        if performance_trace is not None:
            performance_trace.event(
                "intent.route.fallback",
                duration_ms=elapsed_ms(route_started),
                confidence=decision.confidence,
                reason=decision.reason,
            )
        self._log(query, session_id, decision)
        return decision

    def _direct_match(
        self,
        candidates: list[RecalledIntent],
        trace_id: str,
    ) -> RouteDecision | None:
        if not candidates:
            return None
        top = candidates[0]
        if top.score < self.direct_threshold:
            return None
        return RouteDecision(
            status="selected",
            selected_intent=top.intent,
            confidence=min(top.score / 100, 1.0),
            reason=f"规则层达到直出阈值：{top.score:.1f}",
            first_candidates=candidates,
            trace_id=trace_id,
        )

    def _safe_adjudicate(
        self,
        query: str,
        candidates: list[RecalledIntent],
        *,
        retry: bool,
    ) -> LLMJudgement:
        try:
            return self.llm.adjudicate(query, candidates, retry=retry)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Intent router LLM adjudication failed: %s", exc)
            return LLMJudgement(decision="none", reason=f"llm_error:{exc.__class__.__name__}")

    def _safe_intervene(
        self,
        query: str,
        candidates: list[RecalledIntent],
        reason: str,
    ) -> LLMIntervention:
        try:
            return self.llm.intervene(query, candidates, reason)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Intent router LLM intervention failed: %s", exc)
            return LLMIntervention(reason=f"llm_error:{exc.__class__.__name__}")

    def _selected_from_judgement(
        self,
        judgement: LLMJudgement,
        candidates: list[RecalledIntent],
    ) -> IntentDefinition | None:
        if judgement.decision != "select":
            return None
        if judgement.confidence < self.confidence_threshold:
            return None
        if judgement.route_key:
            by_route_key = {
                item.intent.route_key: item
                for item in candidates
            }
            selected_item = by_route_key.get(judgement.route_key)
            if selected_item is not None:
                return selected_item.intent
        for item in candidates:
            if item.intent.intent_id == judgement.intent_id:
                return item.intent
        return None

    def _can_rule_fallback(
        self,
        judgement: LLMJudgement,
        candidates: list[RecalledIntent],
    ) -> bool:
        if not candidates:
            return False
        llm_unavailable = (
            judgement.reason.startswith("llm_disabled")
            or judgement.reason.startswith("llm_error")
        )
        return llm_unavailable and candidates[0].score >= self.llm_fallback_threshold

    def _should_skip_intervention(
        self,
        query: str,
        candidates: list[RecalledIntent],
    ) -> bool:
        """Whether to bypass LLM intervention.

        Keep this cheap.  The intervention LLM is responsible for real synonym
        expansion; we do not scan the full catalog here.
        """
        return False

    def _log(
        self,
        query: str,
        session_id: str | None,
        decision: RouteDecision,
    ) -> None:
        if not self.log_enabled:
            return
        started = time.perf_counter()
        log_dir = self.config.paths.root / "qwenpaw_runtime" / "intent_router"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{datetime.now().date().isoformat()}.jsonl"
        record = decision.to_log_record(query, session_id)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        performance_trace = get_current_trace()
        if performance_trace is not None:
            performance_trace.event(
                "intent.audit_log",
                duration_ms=elapsed_ms(started),
                log_file=str(log_file),
            )
