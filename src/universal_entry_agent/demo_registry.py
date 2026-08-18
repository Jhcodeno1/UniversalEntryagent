from __future__ import annotations

import csv
import json
import os
import hashlib
import stat
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import AgentConfig


DEFAULT_AGENTS: list[dict[str, Any]] = [
    {
        "agent_id": "fund_report_agent",
        "name": "基金产品亮度报告",
        "source": "LOCAL",
        "business_type": "财富管理类",
        "description": "查询并打开基金产品亮度报告入口。",
        "execution_target": "DIRECT",
        "target_type": "DIRECT",
        "action_type": "JUMP",
        "channel": "业务工作台",
        "jump_target": "/fund/product/brightness-report",
        "enabled": True,
        "show_in_chat": True,
        "keywords": ["基金", "产品亮度", "亮度报告", "基金报告"],
        "examples": ["帮我打开基金产品亮度报告", "查询某基金的亮度报告"],
    },
    {
        "agent_id": "knowledge_agent",
        "name": "知识库业务",
        "source": "LOCAL",
        "business_type": "综合服务类",
        "description": "面向业务制度、流程材料和知识检索的问答服务。",
        "execution_target": "AGENT",
        "target_type": "AGENT",
        "enabled": True,
        "show_in_chat": True,
        "keywords": ["知识库", "制度", "流程", "材料"],
        "examples": ["帮我查一下业务流程", "查询开户材料要求"],
    },
    {
        "agent_id": "research_summary_agent",
        "name": "投研摘要",
        "source": "LOCAL",
        "business_type": "资产管理类",
        "description": "生成产品、市场和投研材料的摘要。",
        "execution_target": "LLM",
        "target_type": "LLM",
        "enabled": True,
        "show_in_chat": True,
        "keywords": ["投研", "摘要", "总结", "研报"],
        "examples": ["帮我总结这份投研材料", "生成产品摘要"],
    },
]

DEFAULT_FALLBACKS: list[dict[str, Any]] = [
    {
        "id": "low_confidence",
        "name": "低置信度兜底",
        "enabled": True,
        "trigger": "LOW_CONFIDENCE",
        "threshold": 0.65,
        "action": "LLM_GENERAL_REPLY",
        "reply": "我暂时没有匹配到明确的业务入口，已先按通用问题为你处理。",
        "record_sample": True,
        "priority": 10,
    },
    {
        "id": "second_stage_failed",
        "name": "二次识别失败兜底",
        "enabled": True,
        "trigger": "SECOND_STAGE_FAILED",
        "threshold": 0.0,
        "action": "FIXED_REPLY",
        "reply": "当前问题暂未匹配到可用业务服务，我已记录该问题用于后续优化。",
        "record_sample": True,
        "priority": 20,
    },
    {
        "id": "agent_call_failed",
        "name": "Agent 调用失败兜底",
        "enabled": True,
        "trigger": "AGENT_CALL_FAILED",
        "threshold": 0.0,
        "action": "FIXED_REPLY",
        "reply": "目标智能体服务暂时不可用，请稍后再试。",
        "record_sample": True,
        "priority": 30,
    },
]

DEFAULT_INTENT_STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "confidence_gate",
        "name": "低置信度拦截",
        "description": "模型判断不够确定时，不强行命中业务意图，避免把用户带到错误智能体或错误入口。",
        "enabled": True,
        "category": "JUDGEMENT",
        "impact": "模型精判置信度阈值",
        "threshold": 0.55,
        "priority": 100,
    },
    {
        "id": "rewrite_retry",
        "name": "改写后再次识别",
        "description": "首轮未识别准时，结合业务语境改写用户问题，再重新召回和精判一次。",
        "enabled": True,
        "category": "REWRITE",
        "impact": "二次干预识别开关",
        "priority": 90,
    },
    {
        "id": "rule_recall_guard",
        "name": "召回候选保护",
        "description": "控制一级规则召回数量和最低分，让可能相关的候选进入模型精判，同时过滤明显噪声。",
        "enabled": True,
        "category": "RECALL",
        "impact": "一级规则召回数量与最低分",
        "top_k": 10,
        "min_score": 8,
        "priority": 80,
    },
    {
        "id": "context_completion",
        "name": "上下文省略补全",
        "description": "用户说“这个”“继续办理”等省略表达时，参考最近对话补全业务对象再识别。",
        "enabled": True,
        "category": "CONTEXT",
        "impact": "会话上下文补全",
        "memory_turns": 5,
        "priority": 70,
    },
    {
        "id": "failure_sample_capture",
        "name": "兜底样本沉淀",
        "description": "记录未命中、低置信度、二次识别失败的问题，方便后续补充关键词和示例问法。",
        "enabled": True,
        "category": "LEARNING",
        "impact": "兜底样本池",
        "sample_limit": 500,
        "priority": 60,
    },
]

DEFAULT_ENTRY_CHANNELS: list[dict[str, Any]] = [
    {
        "channel": "JZYY",
        "label": "JZYY 集中运营入口",
        "enabled": True,
        "open_mode": "URL_TEMPLATE",
        "url_template": "",
        "api_url": "",
        "method": "POST",
        "headers": {},
        "request_template": {
            "channel": "{channel}",
            "target": "{jump_target}",
            "intent_id": "{intent_id}",
            "intent_name": "{intent_name}",
        },
        "description": "集中运营系统入口。URL 模板可使用 {jump_target}、{intent_id}、{intent_name}。",
    },
    {
        "channel": "VTM",
        "label": "VTM 入口",
        "enabled": True,
        "open_mode": "URL_TEMPLATE",
        "url_template": "",
        "api_url": "",
        "method": "POST",
        "headers": {},
        "request_template": {
            "channel": "{channel}",
            "target": "{jump_target}",
            "intent_id": "{intent_id}",
            "intent_name": "{intent_name}",
        },
        "description": "VTM 系统入口。URL 模板可使用 {jump_target}、{intent_id}、{intent_name}。",
    },
]


@dataclass
class DemoRegistry:
    config: AgentConfig

    @property
    def root(self) -> Path:
        path = self.config.paths.root / "qwenpaw_runtime" / "demo"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def agents_file(self) -> Path:
        return self.root / "agents.json"

    @property
    def fallbacks_file(self) -> Path:
        return self.root / "fallbacks.json"

    @property
    def intent_strategies_file(self) -> Path:
        return self.root / "intent_strategies.json"

    @property
    def entry_channels_file(self) -> Path:
        return self.root / "entry_channels.json"

    @property
    def fallback_samples_file(self) -> Path:
        return self.root / "fallback_samples.jsonl"

    @property
    def intent_catalog_file(self) -> Path:
        catalog = self.config.intent_router.get("catalog", {})
        for item in catalog.get("csv_files", []):
            csv_config = {"path": item} if isinstance(item, str) else item
            if not isinstance(csv_config, dict):
                continue
            path_text = str(csv_config.get("path") or "").strip()
            if not path_text:
                continue
            path = Path(path_text)
            if not path.is_absolute():
                path = self.config.paths.root / path
            return path
        return self.config.paths.root / "test" / "SKILL_CHANNEL_CONFIG_202606101012(1).csv"

    def list_agents(self) -> list[dict[str, Any]]:
        if not self.agents_file.exists():
            self.save_agents(self._seed_agents())
        agents = []
        changed = False
        for item in self._read_list(self.agents_file):
            try:
                normalized = self._normalize_agent(item)
            except ValueError:
                changed = True
                continue
            agents.append(normalized)
            if normalized != item:
                changed = True
        if changed:
            self.save_agents(agents)
        return agents

    def save_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        agents = self.list_agents()
        normalized = self._normalize_agent(agent)
        replaced = False
        for index, item in enumerate(agents):
            if item.get("agent_id") == normalized["agent_id"]:
                agents[index] = {**item, **normalized}
                replaced = True
                break
        if not replaced:
            agents.append(normalized)
        self.save_agents(agents)
        self.sync_config_agent_registry(agents)
        return normalized

    def toggle_agent(self, agent_id: str, enabled: bool) -> dict[str, Any]:
        agents = self.list_agents()
        for item in agents:
            if item.get("agent_id") == agent_id:
                item["enabled"] = enabled
                item["updated_at"] = self._now()
                self.save_agents(agents)
                self.sync_config_agent_registry(agents)
                return item
        raise KeyError(agent_id)

    def save_agents(self, agents: list[dict[str, Any]]) -> None:
        self._write_json(self.agents_file, agents)

    def list_fallbacks(self) -> list[dict[str, Any]]:
        if not self.fallbacks_file.exists():
            self.save_fallbacks(deepcopy(DEFAULT_FALLBACKS))
        return self._read_list(self.fallbacks_file)

    def save_fallbacks(self, policies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for index, item in enumerate(policies):
            data = dict(item)
            data["id"] = str(data.get("id") or f"fallback_{index + 1}")
            data["name"] = str(data.get("name") or data["id"])
            data["enabled"] = bool(data.get("enabled", True))
            data["trigger"] = str(data.get("trigger") or "LOW_CONFIDENCE")
            data["threshold"] = float(data.get("threshold") or 0.0)
            data["action"] = str(data.get("action") or "FIXED_REPLY")
            data["reply"] = str(data.get("reply") or "")
            data["record_sample"] = bool(data.get("record_sample", True))
            data["priority"] = int(data.get("priority") or 0)
            data["updated_at"] = self._now()
            normalized.append(data)
        self._write_json(self.fallbacks_file, normalized)
        return normalized

    def list_intent_strategies(self) -> list[dict[str, Any]]:
        if not self.intent_strategies_file.exists():
            self.save_intent_strategies(deepcopy(DEFAULT_INTENT_STRATEGIES))
        strategies = []
        changed = False
        existing = {item.get("id"): item for item in self._read_list(self.intent_strategies_file)}
        for default in DEFAULT_INTENT_STRATEGIES:
            saved = existing.get(default["id"], {})
            merged = {
                **saved,
                "id": default["id"],
                "name": default["name"],
                "description": default["description"],
                "category": default["category"],
                "impact": default["impact"],
                "priority": default["priority"],
            }
            for key in ("threshold", "top_k", "min_score", "memory_turns", "sample_limit"):
                if key not in merged and key in default:
                    merged[key] = default[key]
            if "enabled" not in merged:
                merged["enabled"] = default["enabled"]
            normalized = self._normalize_intent_strategy(merged)
            strategies.append(normalized)
            if existing.get(default["id"]) != normalized:
                changed = True
        for item in existing.values():
            if item.get("id") not in {default["id"] for default in DEFAULT_INTENT_STRATEGIES}:
                strategies.append(self._normalize_intent_strategy(item))
        if changed:
            self.save_intent_strategies(strategies)
        return strategies

    def save_intent_strategies(
        self,
        strategies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = [
            self._normalize_intent_strategy(item)
            for item in strategies
            if isinstance(item, dict)
        ]
        normalized.sort(key=lambda item: int(item.get("priority") or 0), reverse=True)
        self._write_json(self.intent_strategies_file, normalized)
        self.sync_intent_router_settings(normalized)
        return normalized

    def list_entry_channels(self) -> list[dict[str, Any]]:
        if not self.entry_channels_file.exists():
            self.save_entry_channels(deepcopy(DEFAULT_ENTRY_CHANNELS))
        saved = {
            str(item.get("channel") or "").strip(): item
            for item in self._read_list(self.entry_channels_file)
            if isinstance(item, dict)
        }
        merged: dict[str, dict[str, Any]] = {}
        for default in DEFAULT_ENTRY_CHANNELS:
            channel = str(default.get("channel") or "").strip()
            merged[channel] = self._normalize_entry_channel(
                {**default, **saved.get(channel, {})}
            )
        for channel, item in saved.items():
            if channel not in merged:
                merged[channel] = self._normalize_entry_channel(item)
        return [merged[key] for key in sorted(merged)]

    def save_entry_channels(self, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = [
            self._normalize_entry_channel(item)
            for item in channels
            if isinstance(item, dict) and str(item.get("channel") or "").strip()
        ]
        self._write_json(self.entry_channels_file, normalized)
        return normalized

    def resolve_entry_route(self, route: dict[str, Any]) -> dict[str, Any]:
        channel = str(route.get("channel") or "").strip()
        jump_target = str(route.get("jump_target") or "").strip()
        entry = next(
            (item for item in self.list_entry_channels() if item.get("channel") == channel),
            None,
        )
        if entry is None:
            return {"entry_configured": False, "entry_message": "未找到该入口渠道配置"}
        if not entry.get("enabled", True):
            return {
                "entry_configured": False,
                "entry_channel_label": entry.get("label") or channel,
                "entry_message": "该入口渠道已停用",
            }
        variables = {
            "channel": channel,
            "jump_target": jump_target,
            "jump_target_encoded": quote(jump_target, safe=""),
            "intent_id": str(route.get("intent_id") or ""),
            "intent_name": str(route.get("intent_name") or ""),
        }
        open_mode = str(entry.get("open_mode") or "URL_TEMPLATE").strip().upper()
        resolved: dict[str, Any] = {
            "entry_configured": True,
            "entry_channel_label": entry.get("label") or channel,
            "entry_open_mode": open_mode,
            "entry_description": entry.get("description") or "",
        }
        if open_mode == "API":
            resolved.update(
                {
                    "entry_api_url": self._render_template(str(entry.get("api_url") or ""), variables),
                    "entry_method": str(entry.get("method") or "POST").upper(),
                    "entry_request_payload": self._render_template_value(
                        entry.get("request_template") or {},
                        variables,
                    ),
                }
            )
        else:
            resolved["entry_url"] = self._render_template(
                str(entry.get("url_template") or ""),
                variables,
            )
        if not resolved.get("entry_url") and not resolved.get("entry_api_url"):
            resolved["entry_configured"] = False
            resolved["entry_message"] = "入口渠道尚未配置 URL 模板或接口地址"
        return resolved

    def sync_intent_router_settings(
        self,
        strategies: list[dict[str, Any]] | None = None,
    ) -> None:
        strategies = strategies if strategies is not None else self.list_intent_strategies()
        by_id = {str(item.get("id")): item for item in strategies}
        data = self.config.data
        router = data.setdefault("intent_router", {})
        rule = router.setdefault("rule", {})
        llm = router.setdefault("llm", {})

        confidence_gate = by_id.get("confidence_gate", {})
        if confidence_gate.get("enabled", True):
            llm["confidence_threshold"] = float(confidence_gate.get("threshold") or 0.55)

        rewrite_retry = by_id.get("rewrite_retry", {})
        llm["intervention_enabled"] = bool(rewrite_retry.get("enabled", True))

        recall_guard = by_id.get("rule_recall_guard", {})
        if recall_guard.get("enabled", True):
            router["top_k"] = int(recall_guard.get("top_k") or router.get("top_k") or 10)
            rule["min_score"] = float(recall_guard.get("min_score") or rule.get("min_score") or 0)

        data["demo_intent_strategies"] = {
            item["id"]: {
                key: value
                for key, value in item.items()
                if key
                in {
                    "enabled",
                    "threshold",
                    "top_k",
                    "min_score",
                    "sample_limit",
                    "memory_turns",
                }
            }
            for item in strategies
        }
        self._write_json(self.config.paths.config_file, data)

    def list_intent_pool(self) -> dict[str, Any]:
        rows, fieldnames, _ = self._read_intent_csv()
        intents = [
            self._normalize_intent_row(row, index)
            for index, row in enumerate(rows)
        ]
        channels: dict[str, int] = {}
        for item in intents:
            channel = str(item.get("CHANNEL") or "UNASSIGNED")
            channels[channel] = channels.get(channel, 0) + 1
        return {
            "source": str(self.intent_catalog_file),
            "columns": fieldnames,
            "channels": [
                {"channel": channel, "count": count, "label": self._entry_label(channel)}
                for channel, count in sorted(channels.items())
            ],
            "intents": intents,
        }

    def save_intent_pool_item(
        self,
        item: dict[str, Any],
        *,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        rows, fieldnames, encoding = self._read_intent_csv()
        row = self._to_intent_csv_row(item, fieldnames)
        if not row.get("SKILL_CODE"):
            raise ValueError("SKILL_CODE is required")
        if not row.get("NAME"):
            raise ValueError("NAME is required")
        now = self._now()
        row["UPDATED_AT"] = now
        if row_id is None:
            row["CREATED_AT"] = row.get("CREATED_AT") or now
            rows.append(row)
            index = len(rows) - 1
        else:
            index = self._intent_row_index(row_id, rows)
            original = rows[index]
            row["CREATED_AT"] = row.get("CREATED_AT") or original.get("CREATED_AT") or now
            rows[index] = {**original, **row}
        self._write_intent_csv(rows, fieldnames, encoding)
        return self._normalize_intent_row(rows[index], index)

    def delete_intent_pool_item(self, row_id: str) -> None:
        rows, fieldnames, encoding = self._read_intent_csv()
        index = self._intent_row_index(row_id, rows)
        del rows[index]
        self._write_intent_csv(rows, fieldnames, encoding)

    def promote_rewrite_sample(self, sample_id: str) -> dict[str, Any]:
        records = self._read_fallback_sample_records()
        sample: dict[str, Any] | None = None
        for item in records:
            if item.get("id") == sample_id:
                sample = item
                break
        if sample is None:
            raise KeyError(sample_id)

        route = sample.get("route") if isinstance(sample.get("route"), dict) else {}
        rewrite_query = str(route.get("rewrite_query") or "").strip()
        user_query = str(sample.get("user_query") or "").strip()
        if not rewrite_query:
            raise ValueError("sample has no rewrite_query")

        rows, fieldnames, encoding = self._read_intent_csv()
        target_index = self._find_intent_row_index_for_route(rows, route)
        if target_index is None:
            raise ValueError("matched intent row not found in CSV")

        row = rows[target_index]
        existing = self._as_list(row.get("KEYWORDS"))
        additions = [
            user_query,
            rewrite_query,
            *self._as_list(route.get("expanded_keywords")),
        ]
        merged = list(dict.fromkeys([*existing, *[item for item in additions if item]]))
        row["KEYWORDS"] = "，".join(merged)
        row["UPDATED_AT"] = self._now()
        rows[target_index] = row
        self._write_intent_csv(rows, fieldnames, encoding)

        sample["status"] = "PROCESSED"
        sample["status_note"] = (
            f"已沉淀改写表达：{rewrite_query}；目标意图：{route.get('intent_name') or row.get('NAME')}"
        )
        sample["updated_at"] = self._now()
        self._write_fallback_sample_records(records)

        return {
            "sample": sample,
            "intent": self._normalize_intent_row(row, target_index),
            "added_keywords": [item for item in additions if item],
        }

    @staticmethod
    def _find_intent_row_index_for_route(
        rows: list[dict[str, str]],
        route: dict[str, Any],
    ) -> int | None:
        intent_id = str(route.get("intent_id") or "").strip()
        intent_name = str(route.get("intent_name") or "").strip()
        channel = str(route.get("channel") or "").strip()
        for index, row in enumerate(rows):
            if (
                intent_id
                and str(row.get("SKILL_CODE") or "").strip() == intent_id
                and (not channel or str(row.get("CHANNEL") or "").strip() == channel)
            ):
                return index
        for index, row in enumerate(rows):
            if (
                intent_name
                and str(row.get("NAME") or "").strip() == intent_name
                and (not channel or str(row.get("CHANNEL") or "").strip() == channel)
            ):
                return index
        for index, row in enumerate(rows):
            if intent_id and str(row.get("SKILL_CODE") or "").strip() == intent_id:
                return index
        for index, row in enumerate(rows):
            if intent_name and str(row.get("NAME") or "").strip() == intent_name:
                return index
        return None

    def _read_intent_csv(self) -> tuple[list[dict[str, str]], list[str], str]:
        path = self.intent_catalog_file
        if not path.exists():
            raise FileNotFoundError(f"Intent catalog CSV not found: {path}")
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with path.open("r", encoding=encoding, newline="") as handle:
                    reader = csv.DictReader(handle)
                    fieldnames = self._intent_fieldnames(list(reader.fieldnames or []))
                    rows = [
                        {
                            str(key): str(value or "")
                            for key, value in row.items()
                            if key is not None
                        }
                        for row in reader
                    ]
                return rows, fieldnames, encoding
            except UnicodeDecodeError as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise last_error
        return [], self._intent_fieldnames([]), "utf-8-sig"

    def _write_intent_csv(
        self,
        rows: list[dict[str, str]],
        fieldnames: list[str],
        encoding: str,
    ) -> None:
        path = self.intent_catalog_file
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                path.chmod(path.stat().st_mode | stat.S_IWRITE)
            except OSError:
                pass
            backup_dir = self.root / "intent_catalog_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_name = f"{path.stem}_{datetime.now().strftime('%Y%m%d%H%M%S')}{path.suffix}"
            (backup_dir / backup_name).write_bytes(path.read_bytes())
        try:
            with path.open("w", encoding=encoding, newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: row.get(key, "") for key in fieldnames})
        except PermissionError as exc:
            raise PermissionError(
                f"意图池 CSV 文件无法写入：{path}。请关闭 Excel/WPS 等占用该文件的程序，"
                "或检查文件权限后重试。"
            ) from exc

    @staticmethod
    def _intent_fieldnames(fieldnames: list[str]) -> list[str]:
        defaults = [
            "SKILL_CODE",
            "TARGET_TYPE",
            "CHANNEL",
            "AGENT_ID",
            "KEYWORDS",
            "ACTION_TYPE",
            "JUMP_TARGET",
            "MCP_SERVER",
            "MCP_TOOL",
            "MCP_PARAMS",
            "EXTRA_PARAMS",
            "CREATED_AT",
            "UPDATED_AT",
            "NAME",
        ]
        result = list(fieldnames)
        for key in defaults:
            if key not in result:
                result.append(key)
        return result

    @staticmethod
    def _intent_row_index(row_id: str, rows: list[dict[str, str]]) -> int:
        try:
            index = int(str(row_id).replace("row-", ""))
        except ValueError as exc:
            raise KeyError(row_id) from exc
        if index < 0 or index >= len(rows):
            raise KeyError(row_id)
        return index

    def _normalize_intent_row(self, row: dict[str, str], index: int) -> dict[str, Any]:
        channel = str(row.get("CHANNEL") or "").strip()
        target_type = str(row.get("TARGET_TYPE") or "").strip().upper()
        agent_id = str(row.get("AGENT_ID") or row.get("agent_id") or "").strip()
        if not target_type:
            has_mcp = bool(
                str(row.get("MCP_SERVER") or "").strip()
                or str(row.get("MCP_TOOL") or "").strip()
                or str(row.get("MCP_PARAMS") or "").strip()
                or str(row.get("ACTION_TYPE") or "").strip().upper() == "MCP"
                or channel.upper() == "MCP"
            )
            target_type = "MCP" if has_mcp else "AGENT" if agent_id else "DIRECT"
        if target_type == "AGENT":
            channel = "AGENT"
        elif target_type == "MCP":
            channel = "MCP"
        return {
            "id": f"row-{index}",
            "row_index": index,
            "SKILL_CODE": str(row.get("SKILL_CODE") or "").strip(),
            "NAME": str(row.get("NAME") or "").strip(),
            "TARGET_TYPE": target_type,
            "AGENT_ID": agent_id,
            "CHANNEL": channel,
            "KEYWORDS": str(row.get("KEYWORDS") or "").strip(),
            "keywords_list": self._as_list(row.get("KEYWORDS")),
            "ACTION_TYPE": str(row.get("ACTION_TYPE") or "JUMP").strip() or "JUMP",
            "JUMP_TARGET": str(row.get("JUMP_TARGET") or "").strip(),
            "MCP_SERVER": str(row.get("MCP_SERVER") or "").strip(),
            "MCP_TOOL": str(row.get("MCP_TOOL") or "").strip(),
            "MCP_PARAMS": str(row.get("MCP_PARAMS") or "").strip(),
            "EXTRA_PARAMS": str(row.get("EXTRA_PARAMS") or "").strip(),
            "CREATED_AT": str(row.get("CREATED_AT") or "").strip(),
            "UPDATED_AT": str(row.get("UPDATED_AT") or "").strip(),
            "entry_label": self._entry_label(channel, target_type, agent_id),
        }

    def _to_intent_csv_row(
        self,
        item: dict[str, Any],
        fieldnames: list[str],
    ) -> dict[str, str]:
        row = {key: str(item.get(key) or "").strip() for key in fieldnames}
        keywords = item.get("KEYWORDS")
        if isinstance(keywords, list):
            row["KEYWORDS"] = "，".join(
                str(value).strip()
                for value in keywords
                if str(value).strip()
            )
        row["SKILL_CODE"] = str(item.get("SKILL_CODE") or item.get("skill_code") or row.get("SKILL_CODE") or "").strip()
        row["NAME"] = str(item.get("NAME") or item.get("name") or row.get("NAME") or "").strip()
        row["TARGET_TYPE"] = str(item.get("TARGET_TYPE") or item.get("target_type") or row.get("TARGET_TYPE") or "").strip().upper()
        row["AGENT_ID"] = str(item.get("AGENT_ID") or item.get("agent_id") or row.get("AGENT_ID") or "").strip()
        row["CHANNEL"] = str(item.get("CHANNEL") or item.get("channel") or row.get("CHANNEL") or "JZYY").strip()
        row["ACTION_TYPE"] = str(item.get("ACTION_TYPE") or item.get("action_type") or row.get("ACTION_TYPE") or "JUMP").strip()
        row["JUMP_TARGET"] = str(item.get("JUMP_TARGET") or item.get("jump_target") or row.get("JUMP_TARGET") or "").strip()
        row["MCP_SERVER"] = str(item.get("MCP_SERVER") or item.get("mcp_server") or row.get("MCP_SERVER") or "").strip()
        row["MCP_TOOL"] = str(item.get("MCP_TOOL") or item.get("mcp_tool") or row.get("MCP_TOOL") or "").strip()
        row["MCP_PARAMS"] = str(item.get("MCP_PARAMS") or item.get("mcp_params") or row.get("MCP_PARAMS") or "").strip()
        row["EXTRA_PARAMS"] = str(item.get("EXTRA_PARAMS") or item.get("extra_params") or row.get("EXTRA_PARAMS") or "").strip()
        if not row["TARGET_TYPE"]:
            has_mcp = bool(
                row["MCP_SERVER"]
                or row["MCP_TOOL"]
                or row["MCP_PARAMS"]
                or row["ACTION_TYPE"].upper() == "MCP"
                or row["CHANNEL"].upper() == "MCP"
            )
            row["TARGET_TYPE"] = "MCP" if has_mcp else "AGENT" if row["AGENT_ID"] else "DIRECT"
        if row["TARGET_TYPE"] == "AGENT":
            row["CHANNEL"] = "AGENT"
            row["ACTION_TYPE"] = ""
            row["JUMP_TARGET"] = ""
            row["MCP_SERVER"] = ""
            row["MCP_TOOL"] = ""
            row["MCP_PARAMS"] = ""
        elif row["TARGET_TYPE"] == "MCP":
            row["ACTION_TYPE"] = "MCP"
            row["JUMP_TARGET"] = ""
            row["AGENT_ID"] = ""
            row["CHANNEL"] = "MCP"
        else:
            row["TARGET_TYPE"] = "DIRECT"
            if row["CHANNEL"] not in {"JZYY", "VTM"}:
                row["CHANNEL"] = "JZYY"
            row["ACTION_TYPE"] = "JUMP"
            row["AGENT_ID"] = ""
            row["MCP_SERVER"] = ""
            row["MCP_TOOL"] = ""
            row["MCP_PARAMS"] = ""
        return row

    @staticmethod
    def _entry_label(channel: str, target_type: str = "", agent_id: str = "") -> str:
        if target_type.upper() == "AGENT" or agent_id:
            return f"专业 Agent：{agent_id or '未配置'}"
        if target_type.upper() == "MCP":
            return "MCP 工具"
        labels = {
            "JZYY": "集中运营入口",
            "VTM": "VTM 入口",
            "AGENT": "专业 Agent",
            "MCP": "MCP 工具",
            "UNASSIGNED": "未配置入口",
        }
        return labels.get(channel, channel or "未配置入口")

    def should_record_failure_samples(self) -> bool:
        for item in self.list_intent_strategies():
            if item.get("id") == "failure_sample_capture":
                return bool(item.get("enabled", True))
        return True

    def choose_fallback(
        self,
        *,
        trigger: str,
        confidence: float = 0.0,
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in self.list_fallbacks()
            if item.get("enabled", True) and item.get("trigger") == trigger
        ]
        if trigger == "LOW_CONFIDENCE":
            candidates = [
                item
                for item in candidates
                if confidence < float(item.get("threshold") or 0.0)
            ]
        candidates.sort(key=lambda item: int(item.get("priority") or 0), reverse=True)
        return candidates[0] if candidates else None

    def record_fallback_sample(
        self,
        *,
        user_query: str,
        session_id: str,
        route: dict[str, Any] | None,
        fallback: dict[str, Any],
        answer: str,
    ) -> None:
        if not self.should_record_failure_samples():
            return
        if not bool(fallback.get("record_sample", True)):
            return
        record = {
            "time": self._now(),
            "session_id": session_id,
            "user_query": user_query,
            "route": route,
            "fallback": fallback,
            "answer": answer,
            "status": "PENDING",
        }
        record.update(self._diagnose_sample(record))
        record["id"] = self._fallback_sample_id(record)
        self.fallback_samples_file.parent.mkdir(parents=True, exist_ok=True)
        with self.fallback_samples_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def record_diagnostic_sample(
        self,
        *,
        user_query: str,
        session_id: str,
        route: dict[str, Any] | None,
        diagnosis_type: str,
        answer: str,
        trigger: str = "",
    ) -> None:
        if not self.should_record_failure_samples():
            return
        record = {
            "time": self._now(),
            "session_id": session_id,
            "user_query": user_query,
            "route": route,
            "fallback": None,
            "answer": answer,
            "status": "PENDING",
            "diagnosis_type": diagnosis_type,
            "diagnosis_trigger": trigger,
        }
        record.update(self._diagnose_sample(record))
        record["id"] = self._fallback_sample_id(record)
        self.fallback_samples_file.parent.mkdir(parents=True, exist_ok=True)
        with self.fallback_samples_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def list_fallback_samples(self, limit: int = 50) -> list[dict[str, Any]]:
        records = self._read_fallback_sample_records()
        records = records[-max(1, limit):]
        records.reverse()
        return records

    def mark_fallback_sample(
        self,
        sample_id: str,
        *,
        status: str,
        note: str = "",
    ) -> dict[str, Any]:
        normalized_status = status.strip().upper()
        if normalized_status not in {"PENDING", "PROCESSED", "IGNORED"}:
            raise ValueError("sample status must be PENDING, PROCESSED or IGNORED")
        records = self._read_fallback_sample_records()
        for item in records:
            if item.get("id") == sample_id:
                item["status"] = normalized_status
                item["status_note"] = note.strip()
                item["updated_at"] = self._now()
                self._write_fallback_sample_records(records)
                return item
        raise KeyError(sample_id)

    def delete_fallback_sample(self, sample_id: str) -> None:
        records = self._read_fallback_sample_records()
        kept = [item for item in records if item.get("id") != sample_id]
        if len(kept) == len(records):
            raise KeyError(sample_id)
        self._write_fallback_sample_records(kept)

    def _read_fallback_sample_records(self) -> list[dict[str, Any]]:
        if not self.fallback_samples_file.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self.fallback_samples_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(self._normalize_fallback_sample(data))
        return records

    def _write_fallback_sample_records(self, records: list[dict[str, Any]]) -> None:
        self.fallback_samples_file.parent.mkdir(parents=True, exist_ok=True)
        with self.fallback_samples_file.open("w", encoding="utf-8") as handle:
            for item in records:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def _normalize_fallback_sample(self, record: dict[str, Any]) -> dict[str, Any]:
        data = dict(record)
        data.setdefault("time", self._now())
        data.setdefault("session_id", "")
        data.setdefault("user_query", "")
        data.setdefault("route", None)
        data.setdefault("fallback", None)
        data.setdefault("answer", "")
        data["status"] = str(data.get("status") or "PENDING").upper()
        if data["status"] not in {"PENDING", "PROCESSED", "IGNORED"}:
            data["status"] = "PENDING"
        data.update(self._diagnose_sample(data))
        data["id"] = str(data.get("id") or self._fallback_sample_id(data))
        return data

    def _diagnose_sample(self, record: dict[str, Any]) -> dict[str, Any]:
        route = record.get("route") if isinstance(record.get("route"), dict) else {}
        fallback = record.get("fallback") if isinstance(record.get("fallback"), dict) else {}
        trigger = str(
            record.get("diagnosis_trigger")
            or fallback.get("trigger")
            or route.get("status")
            or "",
        ).upper()
        diagnosis_type = str(record.get("diagnosis_type") or "").upper()
        confidence = self._to_float(route.get("confidence"))
        answer = str(record.get("answer") or "")

        if not diagnosis_type:
            if "MODEL_EXECUTION_FAILED" in answer or "Agent 执行失败" in answer:
                diagnosis_type = "EXECUTION_FAILED"
            elif trigger == "AGENT_CALL_FAILED":
                diagnosis_type = "EXECUTION_FAILED"
            elif trigger == "SECOND_STAGE_FAILED":
                diagnosis_type = "ROUTE_FALLBACK"
            elif trigger == "LOW_CONFIDENCE":
                diagnosis_type = "LOW_CONFIDENCE_BLOCKED"
            elif trigger == "NO_ROUTE_BUSINESS_LIKE":
                diagnosis_type = "ROUTER_NOT_CALLED"
            elif trigger == "LOW_CONFIDENCE_SELECTED":
                diagnosis_type = "LOW_CONFIDENCE_SELECTED"
            elif trigger == "REWRITE_RECOVERED":
                diagnosis_type = "REWRITE_RECOVERED"
            elif route and route.get("fallback_used"):
                diagnosis_type = "ROUTE_FALLBACK"
            elif route and confidence < 0.65:
                diagnosis_type = "LOW_CONFIDENCE_SELECTED"
            elif route and route.get("rewrite_used"):
                diagnosis_type = "REWRITE_RECOVERED"
            else:
                diagnosis_type = "ROUTE_FALLBACK"

        if diagnosis_type == "EXECUTION_FAILED":
            label = "执行失败"
            reason = "意图可能已命中，但后续 Agent、MCP 或入口调用失败。"
            action = "优先检查 execution_target、Agent/MCP 配置、URL、Header 和超时时间。"
            risk = "HIGH"
        elif diagnosis_type == "ROUTER_NOT_CALLED":
            label = "入口未触发路由"
            reason = "请求看起来像业务问题，但主入口没有调用业务意图路由。"
            action = "补充入口 Agent 的路由触发边界，或把该表达加入业务类触发样本。"
            risk = "HIGH"
        elif diagnosis_type == "LOW_CONFIDENCE_SELECTED":
            label = "低置信命中"
            reason = "模型选择了意图，但置信度偏低，存在误命中风险。"
            action = "复核 TopK 候选和意图描述，必要时补充反例或更清晰的示例问法。"
            risk = "MEDIUM"
        elif diagnosis_type == "LOW_CONFIDENCE_BLOCKED":
            label = "低置信拦截"
            reason = "召回或精判结果不足以确认唯一意图，已进入兜底。"
            action = "人工标注到已有意图或新增意图，确认后再补关键词和示例。"
            risk = "MEDIUM"
        elif diagnosis_type == "REWRITE_RECOVERED":
            label = "二次改写命中"
            reason = "首次识别不稳，依赖改写后才完成命中。"
            action = "把原始表达补入目标意图示例，降低后续对改写的依赖。"
            risk = "LOW"
        elif diagnosis_type == "FACT_BOUNDARY":
            label = "事实依据不足"
            reason = "问题涉及具体产品、平台、流程或规则，但当前没有可靠依据。"
            action = "补充知识源、MCP 查询能力或专业 Agent，不建议直接写固定话术。"
            risk = "MEDIUM"
        else:
            label = "路由未命中"
            reason = "业务路由未能确认唯一意图。"
            action = "判断是补已有意图、创建新意图，还是标记为非业务/歧义问题。"
            risk = "MEDIUM"

        return {
            "diagnosis_type": diagnosis_type,
            "diagnosis_label": str(record.get("diagnosis_label") or label),
            "diagnosis_reason": str(record.get("diagnosis_reason") or reason),
            "suggested_action": str(record.get("suggested_action") or action),
            "risk_level": str(record.get("risk_level") or risk),
            "execution_target": str(
                record.get("execution_target")
                or route.get("execution_target")
                or "",
            ),
        }

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _fallback_sample_id(record: dict[str, Any]) -> str:
        raw = "|".join(
            [
                str(record.get("time") or ""),
                str(record.get("session_id") or ""),
                str(record.get("user_query") or ""),
            ],
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def sync_config_agent_registry(self, agents: list[dict[str, Any]] | None = None) -> None:
        agents = agents if agents is not None else self.list_agents()
        config_agents = [self._to_config_agent(item) for item in agents]
        data = self.config.data
        data.setdefault("agent_registry", {})
        existing = data["agent_registry"].setdefault("agents", [])
        existing_by_id = {
            str(item.get("agent_id") or item.get("id") or ""): item
            for item in existing
            if isinstance(item, dict)
        }
        for item in config_agents:
            existing_by_id[item["agent_id"]] = item
        data["agent_registry"]["agents"] = list(existing_by_id.values())
        self._sync_mcp_clients(data, agents)
        self._write_json(self.config.paths.config_file, data)

    def _sync_mcp_clients(self, data: dict[str, Any], agents: list[dict[str, Any]]) -> None:
        mcp = data.setdefault("mcp", {})
        clients = mcp.setdefault("clients", {})
        for item in agents:
            target = str(item.get("execution_target") or item.get("target_type") or "").upper()
            if target != "MCP":
                continue
            server = str(item.get("mcp_server") or item.get("agent_id") or "").strip()
            url = str(item.get("mcp_url") or "").strip()
            if not server or not url:
                continue
            transport = str(item.get("mcp_transport") or "streamable_http").strip() or "streamable_http"
            if transport == "http":
                transport = "streamable_http"
            headers = item.get("mcp_headers") if isinstance(item.get("mcp_headers"), dict) else {}
            clients[server] = {
                "name": server,
                "description": item.get("description", ""),
                "enabled": bool(item.get("enabled", True)),
                "transport": transport,
                "url": url,
                "headers": headers,
            }

    def _seed_agents(self) -> list[dict[str, Any]]:
        seeded = []
        seen = set()
        for item in self.config.agent_registry.get("agents", []):
            if isinstance(item, dict):
                normalized = self._normalize_agent(self._from_config_agent(item))
                seeded.append(normalized)
                seen.add(normalized["agent_id"])
        for item in DEFAULT_AGENTS:
            if item["agent_id"] not in seen:
                seeded.append(self._normalize_agent(item))
        return seeded

    def _from_config_agent(self, item: dict[str, Any]) -> dict[str, Any]:
        metadata = item.get("demo_metadata") if isinstance(item.get("demo_metadata"), dict) else {}
        merged = {**metadata, **item}
        merged["agent_id"] = str(item.get("agent_id") or item.get("id") or "")
        merged.setdefault("source", "THIRD_PARTY" if item.get("base_url") else "LOCAL")
        merged.setdefault("execution_target", item.get("TARGET_TYPE") or "AGENT")
        merged.setdefault("target_type", item.get("TARGET_TYPE") or merged["execution_target"])
        merged.setdefault("keywords", item.get("triggers") or [])
        for source_key, target_key in (
            ("base_url", "api_url"),
            ("chat_completions_url", "chat_completions_url"),
            ("model", "model"),
        ):
            value = item.get(source_key)
            if value not in (None, "", []):
                merged[target_key] = value
        protocol = str(item.get("protocol") or item.get("type") or "")
        if protocol in {"openai_compatible", "openai_chat_completions"}:
            merged["adapter_type"] = "OPENAI_COMPATIBLE"
        elif merged.get("source") == "THIRD_PARTY":
            merged.setdefault("adapter_type", "HTTP")
        return merged

    def _to_config_agent(self, item: dict[str, Any]) -> dict[str, Any]:
        target = str(item.get("target_type") or item.get("execution_target") or "").upper()
        source = str(item.get("source") or "").upper()
        adapter_type = str(item.get("adapter_type") or "").upper()
        data = {
            "agent_id": item["agent_id"],
            "name": item["name"],
            "description": item.get("description", ""),
            "enabled": bool(item.get("enabled", True)),
            "triggers": item.get("keywords") or [],
            "examples": item.get("examples") or [],
            "priority": int(item.get("priority") or 0),
            "TARGET_TYPE": target,
            "ACTION_TYPE": item.get("action_type") or ("JUMP" if target == "DIRECT" else ""),
            "CHANNEL": item.get("channel") or "",
            "JUMP_TARGET": item.get("jump_target") or "",
            "demo_metadata": item,
        }
        if target == "AGENT" and (
            source == "THIRD_PARTY"
            or item.get("api_url")
            or item.get("base_url")
            or item.get("chat_completions_url")
        ):
            data["type"] = "openai_compatible"
            data["protocol"] = "openai_chat_completions"
            data["model"] = item.get("model") or item.get("agent_id")
            data["timeout_seconds"] = int(item.get("timeout_seconds") or 120)
            if isinstance(item.get("headers"), dict):
                data["headers"] = item.get("headers")
            if item.get("chat_completions_url"):
                data["chat_completions_url"] = item.get("chat_completions_url")
            elif adapter_type == "OPENAI_COMPATIBLE":
                data["base_url"] = item.get("api_url") or item.get("base_url") or ""
            else:
                data["chat_completions_url"] = self._http_adapter_chat_url(
                    item["agent_id"],
                )
        if target == "MCP":
            data["MCP_SERVER"] = item.get("mcp_server") or ""
            data["MCP_TOOL"] = item.get("mcp_tool") or ""
            data["MCP_PARAMS"] = item.get("mcp_params") or ""
        return {key: value for key, value in data.items() if value not in (None, "")}

    def _normalize_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        agent_id = str(agent.get("agent_id") or agent.get("id") or "").strip()
        if not agent_id:
            raise ValueError("agent_id is required")
        name = str(agent.get("name") or agent_id).strip()
        target = str(
            agent.get("execution_target") or agent.get("target_type") or "AGENT",
        ).upper()
        source = str(agent.get("source") or "LOCAL").upper()
        keywords = self._as_list(agent.get("keywords") or agent.get("triggers"))
        examples = self._as_list(agent.get("examples"))
        return {
            "agent_id": agent_id,
            "name": name,
            "source": source,
            "business_type": str(agent.get("business_type") or "综合服务类"),
            "description": str(agent.get("description") or ""),
            "execution_target": target,
            "target_type": str(agent.get("target_type") or target).upper(),
            "action_type": str(agent.get("action_type") or ""),
            "channel": str(agent.get("channel") or ""),
            "jump_target": str(agent.get("jump_target") or ""),
            "api_url": str(agent.get("api_url") or agent.get("base_url") or ""),
            "chat_completions_url": str(agent.get("chat_completions_url") or ""),
            "model": str(agent.get("model") or ""),
            "adapter_type": self._adapter_type(agent, target),
            "method": str(agent.get("method") or "POST").upper(),
            "timeout_seconds": int(agent.get("timeout_seconds") or 120),
            "config_file_name": str(agent.get("config_file_name") or ""),
            "config_file_path": str(agent.get("config_file_path") or ""),
            "headers": agent.get("headers") if isinstance(agent.get("headers"), dict) else {},
            "request_params": agent.get("request_params")
            if isinstance(agent.get("request_params"), list)
            else [],
            "request_template": agent.get("request_template")
            if isinstance(agent.get("request_template"), dict)
            else {},
            "mcp_server": str(agent.get("mcp_server") or agent.get("MCP_SERVER") or ""),
            "mcp_tool": str(agent.get("mcp_tool") or agent.get("MCP_TOOL") or ""),
            "mcp_params": str(agent.get("mcp_params") or agent.get("MCP_PARAMS") or ""),
            "mcp_transport": str(agent.get("mcp_transport") or "streamable_http"),
            "mcp_url": str(agent.get("mcp_url") or ""),
            "mcp_headers": agent.get("mcp_headers") if isinstance(agent.get("mcp_headers"), dict) else {},
            "keywords": keywords,
            "examples": examples,
            "enabled": bool(agent.get("enabled", True)),
            "show_in_chat": bool(agent.get("show_in_chat", True)),
            "priority": int(agent.get("priority") or 0),
            "created_at": str(agent.get("created_at") or now),
            "updated_at": now,
        }

    def _normalize_intent_strategy(self, item: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        strategy_id = str(item.get("id") or "").strip()
        if not strategy_id:
            raise ValueError("strategy id is required")
        data = {
            "id": strategy_id,
            "name": str(item.get("name") or strategy_id),
            "description": str(item.get("description") or ""),
            "enabled": bool(item.get("enabled", True)),
            "category": str(item.get("category") or "JUDGEMENT").upper(),
            "impact": str(item.get("impact") or ""),
            "priority": int(item.get("priority") or 0),
            "updated_at": now,
        }
        for key in ("threshold", "min_score"):
            if item.get(key) not in (None, ""):
                data[key] = float(item.get(key))
        for key in ("top_k", "sample_limit", "memory_turns"):
            if item.get(key) not in (None, ""):
                data[key] = int(item.get(key))
        return data

    def _normalize_entry_channel(self, item: dict[str, Any]) -> dict[str, Any]:
        channel = str(item.get("channel") or "").strip()
        if not channel:
            raise ValueError("channel is required")
        open_mode = str(item.get("open_mode") or "URL_TEMPLATE").strip().upper()
        if open_mode not in {"URL_TEMPLATE", "API"}:
            open_mode = "URL_TEMPLATE"
        headers = item.get("headers")
        request_template = item.get("request_template")
        return {
            "channel": channel,
            "label": str(item.get("label") or channel).strip(),
            "enabled": bool(item.get("enabled", True)),
            "open_mode": open_mode,
            "url_template": str(item.get("url_template") or "").strip(),
            "api_url": str(item.get("api_url") or "").strip(),
            "method": str(item.get("method") or "POST").strip().upper() or "POST",
            "headers": headers if isinstance(headers, dict) else {},
            "request_template": request_template if isinstance(request_template, dict) else {},
            "description": str(item.get("description") or "").strip(),
            "updated_at": self._now(),
        }

    @staticmethod
    def _render_template(text: str, variables: dict[str, str]) -> str:
        result = text
        for key, value in variables.items():
            result = result.replace("{" + key + "}", value)
        return result

    def _render_template_value(self, value: Any, variables: dict[str, str]) -> Any:
        if isinstance(value, str):
            return self._render_template(value, variables)
        if isinstance(value, list):
            return [self._render_template_value(item, variables) for item in value]
        if isinstance(value, dict):
            return {
                str(key): self._render_template_value(item, variables)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]

    @staticmethod
    def _adapter_type(agent: dict[str, Any], target: str) -> str:
        explicit = str(agent.get("adapter_type") or "").strip().upper()
        if explicit:
            return explicit
        source = str(agent.get("source") or "").strip().upper()
        protocol = str(agent.get("protocol") or agent.get("type") or "").strip()
        if protocol in {"openai_compatible", "openai_chat_completions"}:
            return "OPENAI_COMPATIBLE"
        if target == "AGENT" and agent.get("chat_completions_url"):
            return "OPENAI_COMPATIBLE"
        if target == "AGENT" and (
            source == "THIRD_PARTY" or agent.get("api_url") or agent.get("base_url")
        ):
            return "HTTP"
        return ""

    @staticmethod
    def _http_adapter_chat_url(agent_id: str) -> str:
        base_url = os.environ.get(
            "UNIVERSAL_AGENT_WEB_BASE_URL",
            "http://127.0.0.1:8765",
        ).rstrip("/")
        return (
            f"{base_url}/api/demo/http-agents/"
            f"{quote(agent_id, safe='')}/v1/chat/completions"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _read_list(path: Path) -> list[dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
