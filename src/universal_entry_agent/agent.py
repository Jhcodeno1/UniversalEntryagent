from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import AgentConfig
from .diagnostics import (
    create_trace,
    elapsed_ms,
    get_current_trace,
    reset_current_trace,
    set_current_trace,
)
from .intent_router import IntentRouter, RouteDecision


class UniversalEntryAgent:
    """Universal entry agent backed by the vendored core runtime."""

    agent_id = "universal_entry_agent"
    user_id = "local_user"
    channel = "console"

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.project_root = config.paths.root
        self.runtime_root = self.project_root / "qwenpaw_runtime"
        self.secret_root = Path(f"{self.runtime_root}.secret")
        self.workspace_dir = self.runtime_root / "workspaces" / self.agent_id
        self.intent_router = IntentRouter(config)
        self.last_route_decision: RouteDecision | None = None
        self.last_trace_summary: dict[str, Any] = {}
        self.workspace = None
        self.runner = None
        self._loop = asyncio.new_event_loop()
        self._closed = False

        self._prepare_runtime_environment()
        self._prepare_runtime_files()
        self._configure_runtime_logging()

        from qwenpaw.app.workspace import Workspace

        self._assert_local_core_loaded()
        self.workspace = Workspace(
            agent_id=self.agent_id,
            workspace_dir=self.workspace_dir,
        )
        self._run_async(self._start_runtime_services())

    def _run_async(self, awaitable):
        asyncio.set_event_loop(self._loop)
        return self._loop.run_until_complete(awaitable)

    async def _start_runtime_services(self) -> None:
        """Start the same workspace lifecycle used by the core runtime."""
        if self.workspace is None:
            raise RuntimeError("Workspace has not been created")
        await self.workspace.start()
        self._configure_runtime_logging()
        self.runner = self.workspace.runner
        if self.runner is None:
            raise RuntimeError("Workspace did not create an AgentRunner")

    async def _close_runtime_services(self) -> None:
        if self.workspace is not None:
            await self.workspace.stop(final=True)
        self.intent_router.close()

    async def _cancel_pending_runtime_tasks(self) -> None:
        current = asyncio.current_task(loop=self._loop)
        tasks = [
            task
            for task in asyncio.all_tasks(self._loop)
            if task is not current and not task.done()
        ]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._run_async(
                asyncio.wait_for(self._close_runtime_services(), timeout=8.0),
            )
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            logging.getLogger(__name__).warning(
                "Timed out while closing runtime services; cancelling pending tasks",
            )
        finally:
            try:
                self._run_async(
                    asyncio.wait_for(
                        self._cancel_pending_runtime_tasks(),
                        timeout=2.0,
                    ),
                )
                self._run_async(self._loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001 - shutdown must be best effort.
                logging.getLogger(__name__).debug(
                    "Error during best-effort runtime task cleanup",
                    exc_info=True,
                )
            self._loop.close()

    def reload_intent_router(self) -> None:
        """Reload dynamic agent, tool, and intent configuration for management UI changes."""
        old_router = self.intent_router
        self._prepare_runtime_files()
        self.intent_router = IntentRouter(self.config)
        old_router.close()
        self.last_route_decision = None

    def _prepare_runtime_environment(self) -> None:
        # These must be set before importing vendored core modules because
        # qwenpaw.constant resolves WORKING_DIR at import time.
        os.environ["QWENPAW_WORKING_DIR"] = str(self.runtime_root)
        os.environ["QWENPAW_SECRET_DIR"] = str(self.secret_root)
        os.environ["QWENPAW_CONFIG_FILE"] = "config.json"
        os.environ["QWENPAW_ENABLED_CHANNELS"] = "console"
        os.environ["QWENPAW_EXTERNAL_AGENTS_FILE"] = str(
            self.runtime_root / "external_agents.json",
        )
        skill_pool_builtins = self.config.runtime.get("skill_pool_builtins")
        if isinstance(skill_pool_builtins, list):
            os.environ["QWENPAW_SKILL_POOL_BUILTINS"] = ",".join(
                str(item).strip()
                for item in skill_pool_builtins
                if str(item).strip()
            )

    def _configure_runtime_logging(self) -> None:
        level_name = os.environ.get("UNIVERSAL_AGENT_LOG_LEVEL", "critical")
        level = getattr(logging, level_name.upper(), logging.CRITICAL)
        os.environ["QWENPAW_LOG_LEVEL"] = logging.getLevelName(level).lower()

        for logger_name in (
            "as",
            "qwenpaw",
            "httpx",
            "httpcore",
            "openai",
            "agentscope",
            "agentscope_runtime",
        ):
            logging.getLogger(logger_name).setLevel(level)

        root = logging.getLogger()
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)

        agentscope_logger = logging.getLogger("as")
        agentscope_logger.handlers.clear()
        agentscope_logger.setLevel(level)
        agentscope_logger.propagate = False

        try:
            from loguru import logger as loguru_logger

            loguru_logger.remove()
            loguru_logger.disable("")
        except Exception:
            pass

    def _assert_local_core_loaded(self) -> None:
        import qwenpaw

        core_file = Path(qwenpaw.__file__ or "").resolve()
        expected_root = (self.project_root / "src" / "qwenpaw").resolve()
        try:
            core_file.relative_to(expected_root)
        except ValueError as exc:
            raise RuntimeError(
                "Core runtime must be loaded from this project, but got: "
                f"{core_file}",
            ) from exc

    def _llm_base_url(self) -> str:
        return str(self.config.llm.get("base_url") or "").rstrip("/")

    def _llm_model(self) -> str:
        return str(self.config.llm.get("model") or "")

    def _llm_api_key(self) -> str:
        env_name = str(self.config.llm.get("api_key_env") or "LLM_API_KEY")
        return os.environ.get(env_name, "")

    def _agent_api_host_port(self) -> tuple[str, int]:
        raw = str(
            self.config.qwenpaw.get("base_url")
            or "http://127.0.0.1:8088/api",
        )
        parsed = urlparse(raw)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port

    def _enabled_builtin_tools(self) -> dict[str, dict[str, Any]]:
        enabled = {
            "read_file",
            "write_file",
            "edit_file",
            "grep_search",
            "glob_search",
            "get_current_time",
            "set_user_timezone",
            "get_token_usage",
            "business_intent_router",
            "list_agents",
            "chat_with_agent",
            "submit_to_agent",
            "check_agent_task",
        }
        disabled = {
            "execute_shell_command",
            "browser_use",
            "desktop_screenshot",
            "view_image",
            "view_video",
            "send_file_to_user",
            "delegate_external_agent",
        }
        result = {}
        for name in sorted(enabled | disabled):
            result[name] = {
                "name": name,
                "enabled": name in enabled,
                "description": "",
            }
        return result

    @staticmethod
    def _is_capability_question(text: str) -> bool:
        normalized = text.strip().lower()
        if not normalized:
            return False
        keywords = (
            "有哪些工具",
            "有什么工具",
            "可用工具",
            "工具列表",
            "哪些技能",
            "有什么技能",
            "可用技能",
            "技能列表",
            "工具和技能",
            "能力列表",
            "有哪些能力",
            "你能做什么",
            "能调用什么",
            "tools",
            "skills",
        )
        return any(keyword in normalized for keyword in keywords)

    def _format_capability_answer(self) -> str:
        tool_descriptions = {
            "read_file": "读取工作区内文件内容。",
            "write_file": "创建或覆盖写入工作区文件。",
            "edit_file": "按原文片段编辑工作区文件。",
            "grep_search": "按关键字或正则搜索文件内容。",
            "glob_search": "按 glob 模式查找文件。",
            "get_current_time": "查询当前时间。",
            "set_user_timezone": "设置用户时区。",
            "get_token_usage": "查询模型 token 使用情况。",
            "business_intent_router": "识别证券业务意图并返回对应执行入口。",
            "list_agents": "列出已注册的专业 agent。",
            "chat_with_agent": "同步调用专业 agent 并等待回复。",
            "submit_to_agent": "异步提交任务给专业 agent。",
            "check_agent_task": "查询异步专业 agent 任务状态。",
            "execute_shell_command": "执行 shell 命令。",
            "browser_use": "控制浏览器。",
            "desktop_screenshot": "截取桌面截图。",
            "view_image": "把本地图像加入模型上下文。",
            "view_video": "把本地视频加入模型上下文。",
            "send_file_to_user": "发送文件给用户。",
            "delegate_external_agent": "委托外部 agent。",
        }
        builtin_tools = self._enabled_builtin_tools()
        enabled_tools = [
            f"- `{name}`：{tool_descriptions.get(name, '内置工具。')}"
            for name, item in sorted(builtin_tools.items())
            if item.get("enabled")
        ]
        disabled_tools = [
            f"`{name}`"
            for name, item in sorted(builtin_tools.items())
            if not item.get("enabled")
        ]

        skill_manifest = self.workspace_dir / "skill.json"
        skills: list[str] = []
        if skill_manifest.exists():
            try:
                payload = json.loads(skill_manifest.read_text(encoding="utf-8"))
                for name, item in sorted((payload.get("skills") or {}).items()):
                    if item.get("enabled", True):
                        desc = str(item.get("description") or "").strip()
                        skills.append(
                            f"- `{name}`" + (f"：{desc}" if desc else "")
                        )
            except Exception:
                skills = []

        agents = []
        for item in self.config.agent_registry.get("agents", []):
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or item.get("id") or "").strip()
            if not agent_id:
                continue
            name = str(item.get("name") or agent_id)
            description = str(item.get("description") or "").strip()
            agents.append(
                f"- `{agent_id}`：{name}"
                + (f"。{description}" if description else "")
            )

        mcp_clients = self.config.mcp.get("clients", {})
        mcps = [
            f"- `{name}`"
            for name, item in sorted(mcp_clients.items())
            if isinstance(item, dict) and item.get("enabled", True)
        ]

        parts = [
            "我当前可以使用这些能力：",
            "",
            "内置工具：",
            "\n".join(enabled_tools) if enabled_tools else "- 暂无启用的内置工具",
            "",
            "专业 agent：",
            "\n".join(agents) if agents else "- 暂无已注册专业 agent",
            "",
            "已安装技能：",
            "\n".join(skills) if skills else "- 当前意图路由 Agent workspace 暂未安装独立 skill",
            "",
            "MCP 工具：",
            "\n".join(mcps) if mcps else "- 当前未配置启用的 MCP client",
        ]
        if disabled_tools:
            parts.extend(
                [
                    "",
                    "默认关闭的高权限/重依赖工具：",
                    "- " + "、".join(disabled_tools),
                ],
            )
        return "\n".join(parts)

    def _registered_agents_text(self) -> str:
        rows = []
        for item in self.config.agent_registry.get("agents", []):
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or item.get("id") or "").strip()
            if not agent_id:
                continue
            name = str(item.get("name") or agent_id)
            description = str(item.get("description") or "")
            triggers = "、".join(str(v) for v in (item.get("triggers") or []))
            rows.append(
                f"- `{agent_id}`: {name}. {description}"
                + (f" 触发词：{triggers}" if triggers else ""),
            )
        return "\n".join(rows) or "- 暂无已注册专业 agent。"

    def _external_agents_payload(self) -> dict[str, Any]:
        agents: list[dict[str, Any]] = []
        for item in self.config.agent_registry.get("agents", []):
            if not isinstance(item, dict):
                continue
            protocol = str(item.get("protocol") or item.get("type") or "")
            if protocol not in {
                "openai_compatible",
                "openai_chat_completions",
            }:
                continue
            agent_id = str(item.get("agent_id") or item.get("id") or "").strip()
            if not agent_id:
                continue
            agents.append(
                {
                    "id": agent_id,
                    "name": item.get("name") or agent_id,
                    "description": item.get("description") or "",
                    "workspace_dir": item.get("workspace_dir") or "",
                    "protocol": "openai_chat_completions",
                    "base_url": item.get("base_url") or "",
                    "chat_completions_url": item.get("chat_completions_url") or "",
                    "model": item.get("model") or "",
                    "api_key_env": item.get("api_key_env") or "",
                    "headers": item.get("headers") if isinstance(item.get("headers"), dict) else {},
                    "timeout_seconds": item.get("timeout_seconds") or 120,
                    "system_prompt": item.get("system_prompt") or "",
                },
            )
        return {"agents": agents}

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _prepare_runtime_files(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "memory").mkdir(parents=True, exist_ok=True)
        memory_file = self.workspace_dir / "MEMORY.md"
        if not memory_file.exists():
            memory_file.write_text("# 长期记忆\n\n", encoding="utf-8")

        provider_dir = self.secret_root / "providers" / "custom"
        provider_dir.mkdir(parents=True, exist_ok=True)
        (self.secret_root / "providers").mkdir(parents=True, exist_ok=True)

        host, port = self._agent_api_host_port()
        root_config = {
            "last_api": {"host": host, "port": port},
            "mcp": self.config.mcp or {"clients": {}},
            "agents": {
                "active_agent": self.agent_id,
                "agent_order": [self.agent_id],
                "profiles": {
                    self.agent_id: {
                        "id": self.agent_id,
                        "workspace_dir": str(self.workspace_dir),
                        "enabled": True,
                    },
                },
            },
            "tools": {"builtin_tools": self._enabled_builtin_tools()},
            "security": {
                "tool_guard": {"enabled": False},
                "skill_scanner": {"mode": "warn"},
            },
            "user_timezone": "Asia/Shanghai",
        }
        self._write_json(self.runtime_root / "config.json", root_config)
        self._write_json(
            self.runtime_root / "external_agents.json",
            self._external_agents_payload(),
        )

        extra_body = dict(self.config.llm.get("extra_body") or {})
        extra_body.pop("stream", None)

        provider = {
            "id": "universal-entry-openai",
            "name": "Intent Router OpenAI-compatible",
            "base_url": self._llm_base_url(),
            "api_key": self._llm_api_key(),
            "chat_model": "OpenAIChatModel",
            "api_key_prefix": "",
            "is_custom": True,
            "require_api_key": False,
            "models": [{"id": self._llm_model(), "name": self._llm_model()}],
            "extra_models": [],
            "generate_kwargs": {"extra_body": extra_body},
        }
        self._write_json(
            provider_dir / "universal-entry-openai.json",
            provider,
        )
        self._write_json(
            self.secret_root / "providers" / "active_model.json",
            {
                "provider_id": "universal-entry-openai",
                "model": self._llm_model(),
            },
        )

        agent_config = {
            "id": self.agent_id,
            "name": self.config.agent_name,
            "description": self.config.data.get("agent", {}).get(
                "description",
                "",
            ),
            "workspace_dir": str(self.workspace_dir),
            "active_model": {
                "provider_id": "universal-entry-openai",
                "model": self._llm_model(),
            },
            "language": "zh",
            "system_prompt_files": ["AGENTS.md", "PROFILE.md"],
            "mcp": self.config.mcp or {"clients": {}},
            "tools": {"builtin_tools": self._enabled_builtin_tools()},
            "security": {
                "tool_guard": {"enabled": False},
                "skill_scanner": {"mode": "warn"},
            },
            "running": {
                "max_iters": int(self.config.runtime.get("max_steps") or 20),
                "auto_continue_on_text_only": False,
                "memory_manager_backend": "remelight",
                "memory_summary": {
                    "memory_summary_enabled": True,
                    "memory_prompt_enabled": True,
                    "force_memory_search": False,
                    "rebuild_memory_index_on_start": False,
                    "recursive_file_watcher": True,
                },
                "context_compact": {"context_compact_enabled": True},
            },
        }
        self._write_json(self.workspace_dir / "agent.json", agent_config)

        agents_md = (
            "# 意图路由 Agent\n\n"
            "你是统一对话入口，负责理解用户意图，自主选择直接回答、"
            "调用内置工具、调用 MCP 工具，或调用已注册的专业 agent。\n\n"
            "<!-- memory:start -->\n"
            "## 记忆\n\n"
            "每次会话都可能是新的。工作目录下的文件是你的记忆延续：\n\n"
            "- 每日笔记：`memory/YYYY-MM-DD.md`，用于记录当天发生的重要事件、决策和上下文。\n"
            "- 长期记忆：`MEMORY.md`，用于保留经过整理的长期事实、偏好、项目决策和可复用经验。\n"
            "- 重要：更新记忆前先读取原内容，再使用文件工具增量更新，避免覆盖有价值的信息。\n\n"
            "当用户说“记住这个”或对话中出现未来会用到的信息时，先写入每日笔记；"
            "适合长期保留的内容再沉淀到 `MEMORY.md`。回答关于过往工作、决策、偏好、"
            "上下文的问题前，优先使用 `memory_search` 检索记忆。\n"
            "<!-- memory:end -->\n\n"
            "## 多轮对话\n\n"
            "你会通过 session state 保留同一会话中的历史消息。"
            "当用户输入“查询”“继续”“然后呢”等短句时，必须结合上一轮用户和助手消息理解省略含义。\n\n"
            "## 事实边界\n\n"
            "- 可以按普通问题回答通用概念、通用方法和项目说明，但必须区分已确认事实、合理建议和无法确认的信息。\n"
            "- 涉及具体产品、App、平台、业务名称、注册流程、开户流程、权限规则、交易规则、收费、政策或公司归属时，"
            "只有当前会话、已注册业务意图、已注册 Agent/MCP、已读取文件或工具结果提供了依据，才能陈述为事实。\n"
            "- 如果没有依据，必须明确说“当前业务配置和上下文中没有找到该项，无法确认”，可以请用户提供准确名称、券商/系统名称或官方材料，"
            "也可以建议管理员把该业务补充到意图/Agent/MCP 配置中。\n"
            "- 不要用“根据我的知识”“一般来说”包装不确定的具体事实；不要把相似词、听起来像证券产品的名称，自动当成真实产品或真实流程。\n\n"
            "## 意图路由边界\n\n"
            "每轮先结合当前消息和 Session 历史判断用户真实意图，再决定是否调用工具。\n"
            "- 用户询问你的身份、能力、可用工具、可用技能、MCP、浏览器能力、token、session、配置、启动方式、项目实现时，"
            "这是意图路由 Agent 自身问题，直接回答或使用对应本地工具，不调用业务意图路由。\n"
            "- 闲聊、问候、致谢、普通知识问答等非证券业务请求，直接回答，不调用业务意图路由。\n"
            "- 当用户提出证券业务办理、业务入口、账户、权限、密码、资金、交易等业务需求，或结合历史可判断其在继续证券业务时，"
            "先调用 `business_intent_router`，不要自行猜测业务编码。对省略表达，可结合 Session 历史补全为独立、准确的业务问句后传入工具。\n"
            "- 每轮最多调用一次 `business_intent_router`。如果工具返回 fallback，可以按普通问题自行回答，不要重复调用，"
            "但必须遵守事实边界；涉及具体业务、产品、平台或流程且缺少依据时，只能说明无法确认，不能补编流程。\n"
            "- 工具返回 selected 后按 `execution_target` 执行：`DIRECT` 由系统直接完成回复；"
            "`AGENT` 调用 `chat_with_agent`；`MCP` 调用结果指定的 MCP 工具。\n"
            "- 只有当用户在继续专业业务流程、提交业务资料、确认业务步骤、询问专业业务细节，或明确要求调用某个专业 agent 时，"
            "才调用专业 agent。\n"
            "- 如果当前对话里存在专业 agent 返回的业务流程卡片，但用户提出了新的元问题或系统问题，优先回答新问题，"
            "不要被上一张业务卡片的待确认状态劫持。\n"
            "- 能自己完成的普通问答、项目说明、工具说明和配置说明，不要调用其他 agent。\n\n"
            "## 专业 Agent\n\n"
            "调用专业 agent 时使用 `chat_with_agent`，目标 agent id 必须"
            "放在 `to_agent` 参数中，不能把 `chat_with_agent` 当作 agent id。\n\n"
            f"{self._registered_agents_text()}\n\n"
            "如果用户提到机构开户、开机构户、开户资料、开户流程，优先"
            "调用 `to_agent=\"Simple\"`。\n"
            "如果用户提到天气、气温、降雨、风力或出行天气，优先"
            "调用 `to_agent=\"Weather\"`。\n\n"
            "## 工具调用\n\n"
            "如果你已经生成了工具调用，必须等待工具结果后再总结回复。"
            "不要只输出一句“我先看看”就结束当前轮。\n\n"
            "用户询问 token/API 用量、对话还剩多少空间、查看 token 使用情况时，"
            "调用 `get_token_usage`。如果上一轮你询问“是否查询 token 使用情况”，"
            "用户回答“查询”，也应调用 `get_token_usage`。\n"
        )
        (self.workspace_dir / "AGENTS.md").write_text(
            agents_md,
            encoding="utf-8",
        )
        profile = (
            "# PROFILE\n\n"
            "- 用户希望本项目直接复用核心 ReAct、session、memory、tool、MCP、"
            "skill 和多 agent 协作实现，不使用自写简化 planner。\n"
        )
        (self.workspace_dir / "PROFILE.md").write_text(
            profile,
            encoding="utf-8",
        )
        self._ensure_runtime_skills()

    def _ensure_runtime_skills(self) -> None:
        """Install routing skills into the standalone workspace."""
        skill_names = (
            "chat_with_agent",
            "make_plan",
        )
        source_root = (
            self.project_root
            / "src"
            / "qwenpaw"
            / "agents"
            / "skills"
        )
        target_root = self.workspace_dir / "skills"
        target_root.mkdir(parents=True, exist_ok=True)

        manifest_path = self.workspace_dir / "skill.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8"),
                )
            except json.JSONDecodeError:
                manifest = {}
        else:
            manifest = {}
        manifest.setdefault("schema_version", "workspace-skill-manifest.v1")
        manifest.setdefault("version", 0)
        manifest.setdefault("skills", {})

        for skill_name in skill_names:
            src = source_root / skill_name
            dst = target_root / skill_name
            if not (src / "SKILL.md").exists():
                continue
            if not dst.exists():
                shutil.copytree(src, dst)
            manifest["skills"][skill_name] = {
                **manifest["skills"].get(skill_name, {}),
                "enabled": True,
                "channels": ["all"],
                "source": "builtin",
            }

        self._write_json(manifest_path, manifest)

    @staticmethod
    def _has_tool_use(msg: Any) -> bool:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            return False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True
            if getattr(block, "type", None) == "tool_use":
                return True
        return False

    @staticmethod
    def _has_tool_result(msg: Any) -> bool:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            return False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return True
            if getattr(block, "type", None) == "tool_result":
                return True
        return False

    @staticmethod
    def _is_interrupted_msg(msg: Any) -> bool:
        metadata = getattr(msg, "metadata", None)
        return isinstance(metadata, dict) and bool(metadata.get("_is_interrupted"))

    @staticmethod
    def _text_from_msg(msg: Any) -> str:
        getter = getattr(msg, "get_text_content", None)
        if callable(getter):
            text = getter()
            if text:
                return str(text)
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                else:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(str(text))
            return "\n".join(part for part in parts if part)
        return str(msg)

    @staticmethod
    def _tool_blocks(msg: Any, block_type: str) -> list[dict[str, Any]]:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            return []
        result: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict):
                data = block
            else:
                dumper = getattr(block, "model_dump", None)
                if callable(dumper):
                    data = dumper()
                else:
                    data = {
                        "type": getattr(block, "type", None),
                        "id": getattr(block, "id", None),
                        "name": getattr(block, "name", None),
                        "input": getattr(block, "input", None),
                        "output": getattr(block, "output", None),
                    }
            if data.get("type") == block_type:
                result.append(data)
        return result

    async def _ask_async(self, user_request: str, session_id: str | None) -> str:
        from agentscope.message import Msg, TextBlock
        from agentscope.tool import ToolResponse
        from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest

        sid = session_id or "default"
        msg = Msg(
            name="user",
            role="user",
            content=[TextBlock(type="text", text=user_request)],
        )
        request = AgentRequest(
            input=[],
            session_id=sid,
            user_id=self.user_id,
        )
        setattr(request, "channel", self.channel)
        route_state: dict[str, Any] = {}

        async def business_intent_router(user_query: str):
            """Identify a securities business intent and its execution target.

            Call this only for securities business requests after considering
            the loaded Session context. Do not call it for greetings, casual
            conversation, system questions, or general knowledge questions.

            Args:
                user_query: A standalone securities business request. Resolve
                    omitted references from Session history when necessary.

            Returns:
                A compact JSON routing result with status, action type,
                channel, jump target, professional agent, and MCP parameters.
            """
            tool_started = time.perf_counter()
            try:
                decision = await asyncio.to_thread(
                    self.intent_router.route,
                    user_query,
                    sid,
                )
                self.last_route_decision = decision
                payload = self._compact_route_result(decision)
                if payload.get("execution_target") == "DIRECT":
                    route_state["direct_response"] = self._render_direct_route_response(
                        payload,
                    )
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "Business intent router failed: %s",
                    exc,
                )
                self.last_route_decision = None
                payload = {
                    "status": "error",
                    "selected": False,
                    "reason": f"{exc.__class__.__name__}: {exc}",
                    "instruction": (
                        "业务路由不可用，可以基于当前对话回答一般问题；"
                        "涉及具体业务、产品、平台或流程且缺少依据时，必须说明无法确认，不要编造。"
                    ),
                }
            current_trace = get_current_trace()
            if current_trace is not None:
                current_trace.event(
                    "intent.route_tool.completed",
                    duration_ms=elapsed_ms(tool_started),
                    route_status=payload.get("status", ""),
                    selected_intent=payload.get("intent_name", ""),
                    action_type=payload.get("action_type", ""),
                    execution_target=payload.get("execution_target", ""),
                )
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=json.dumps(payload, ensure_ascii=False),
                    ),
                ],
            )

        setattr(
            request,
            "request_context",
            {
                "business_intent_router_tool": business_intent_router,
                "business_intent_route_state": route_state,
            },
        )

        latest_text = ""
        latest_complete_text = ""
        first_complete_text_after_tool = ""
        saw_tool_interaction = False
        trace = get_current_trace()
        runner_started = time.perf_counter()
        previous_event_at = runner_started
        first_output_seen = False
        response_events = 0
        tool_events = 0
        pending_tools: dict[str, tuple[float, str]] = {}
        if trace is not None:
            trace.event(
                "react.runner.start",
                routed_request_chars=len(user_request),
            )
        async for response_msg, last in self.runner.query_handler(
            [msg],
            request=request,
        ):
            now = time.perf_counter()
            gap_ms = (now - previous_event_at) * 1000
            previous_event_at = now
            if trace is not None and not first_output_seen:
                first_output_seen = True
                trace.event(
                    "react.runner.first_output",
                    duration_ms=elapsed_ms(runner_started),
                )

            tool_uses = self._tool_blocks(response_msg, "tool_use")
            if tool_uses:
                saw_tool_interaction = True
                for block in tool_uses:
                    tool_events += 1
                    tool_id = str(block.get("id") or f"tool-{tool_events}")
                    tool_name = str(block.get("name") or "unknown")
                    tool_input = block.get("input")
                    input_keys = (
                        sorted(str(key) for key in tool_input)
                        if isinstance(tool_input, dict)
                        else []
                    )
                    target_agent = (
                        str(tool_input.get("to_agent") or "")
                        if isinstance(tool_input, dict)
                        else ""
                    )
                    tool_action = (
                        str(tool_input.get("action") or "")
                        if isinstance(tool_input, dict)
                        else ""
                    )
                    pending_tools[tool_id] = (now, tool_name)
                    if trace is not None:
                        trace.event(
                            "react.tool.requested",
                            duration_ms=gap_ms,
                            tool_name=tool_name,
                            tool_id=tool_id,
                            input_keys=input_keys,
                            target_agent=target_agent,
                            tool_action=tool_action,
                        )
                continue

            tool_results = self._tool_blocks(response_msg, "tool_result")
            if tool_results:
                saw_tool_interaction = True
                for block in tool_results:
                    tool_id = str(block.get("id") or "")
                    tool_name = str(block.get("name") or "unknown")
                    started, requested_name = pending_tools.pop(
                        tool_id,
                        (now, tool_name),
                    )
                    output = block.get("output")
                    if trace is not None:
                        trace.event(
                            "react.tool.completed",
                            duration_ms=(now - started) * 1000,
                            tool_name=requested_name or tool_name,
                            tool_id=tool_id,
                            output_chars=len(str(output or "")),
                        )
                continue
            if self._is_interrupted_msg(response_msg):
                if trace is not None:
                    trace.event(
                        "react.output.interrupted",
                        duration_ms=gap_ms,
                    )
                continue
            text = self._text_from_msg(response_msg)
            if text:
                response_events += 1
                latest_text = text
                if last:
                    latest_complete_text = text
                    if saw_tool_interaction and not first_complete_text_after_tool:
                        first_complete_text_after_tool = text
                    if trace is not None:
                        trace.event(
                            "react.answer.completed",
                            duration_ms=gap_ms,
                            response_chars=len(text),
                            response_event=response_events,
                        )
        result = first_complete_text_after_tool or latest_complete_text or latest_text
        if trace is not None:
            trace.event(
                "react.runner.completed",
                duration_ms=elapsed_ms(runner_started),
                response_events=response_events,
                tool_events=tool_events,
                unresolved_tools=len(pending_tools),
                reply_chars=len(result),
            )
        return result

    @staticmethod
    def _compact_route_result(decision: RouteDecision) -> dict[str, Any]:
        """Return only execution-relevant fields to the ReAct Agent."""
        intent = decision.selected_intent
        if not decision.routed or intent is None:
            return {
                "status": decision.status,
                "selected": False,
                "confidence": round(decision.confidence, 3),
                "reason": decision.reason,
                "instruction": (
                    "未选中业务意图，可以结合会话上下文按普通问题回答；"
                    "涉及具体业务、产品、平台或流程且缺少依据时，必须说明无法确认，不要编造。"
                ),
            }

        target_type = str(intent.metadata.get("TARGET_TYPE") or "").strip().upper()
        action_type = str(intent.metadata.get("ACTION_TYPE") or "").strip().upper()
        jump_target = str(intent.metadata.get("JUMP_TARGET") or "").strip()
        channel = str(intent.metadata.get("CHANNEL") or "").strip()
        mcp_params = intent.metadata.get("MCP_PARAMS") or ""
        mcp_tool = str(intent.metadata.get("MCP_TOOL") or "").strip()
        mcp_server = str(intent.metadata.get("MCP_SERVER") or "").strip()
        extra_params = str(intent.metadata.get("EXTRA_PARAMS") or "").strip()

        if target_type in {"MCP", "TOOL"}:
            execution_target = "MCP"
        elif target_type in {"AGENT", "PROFESSIONAL_AGENT"}:
            execution_target = "AGENT"
        elif target_type in {"DIRECT", "JUMP", "ENTRY"}:
            execution_target = "DIRECT"
        elif (
            action_type.startswith("MCP")
            or channel.upper() == "MCP"
            or bool(mcp_tool)
            or bool(mcp_server)
            or bool(mcp_params)
        ):
            execution_target = "MCP"
        elif action_type == "JUMP":
            execution_target = "DIRECT"
        elif intent.agent_id:
            execution_target = "AGENT"
        else:
            execution_target = "LLM"

        if execution_target == "DIRECT":
            instruction = "直接向用户说明业务名称、办理渠道和业务入口。"
        elif execution_target == "MCP":
            instruction = "调用路由结果指定的 MCP 工具。"
        elif execution_target == "AGENT":
            instruction = "调用指定的专业 Agent，等待结果后统一回复。"
        else:
            instruction = "根据已识别的业务意图直接回答。"

        return {
            "status": decision.status,
            "selected": True,
            "route_key": intent.route_key,
            "intent_id": intent.intent_id,
            "intent_name": intent.name,
            "description": intent.description,
            "confidence": round(decision.confidence, 3),
            "reason": decision.reason,
            "target_type": target_type,
            "execution_target": execution_target,
            "action_type": action_type,
            "channel": channel,
            "jump_target": jump_target,
            "agent_id": intent.agent_id,
            "mcp_params": mcp_params,
            "mcp_tool": mcp_tool,
            "mcp_server": mcp_server,
            "extra_params": extra_params,
            "instruction": instruction,
        }

    @staticmethod
    def _render_direct_route_response(payload: dict[str, Any]) -> str:
        """Render a JUMP result without another model call."""
        intent_name = str(payload.get("intent_name") or "已识别业务")
        channel = str(payload.get("channel") or "未配置")
        jump_target = str(payload.get("jump_target") or "未配置")
        confidence = float(payload.get("confidence") or 0.0)
        return (
            f"已识别到您的业务意图：**{intent_name}**\n\n"
            f"- 办理渠道：`{channel}`\n"
            f"- 业务入口：`{jump_target}`\n"
            f"- 识别置信度：{confidence:.0%}"
        )

    def ask(
        self,
        user_request: str,
        session_id: str | None = None,
        *,
        queue_wait_ms: float = 0.0,
    ) -> str:
        self._configure_runtime_logging()
        sid = session_id or "default"
        trace = create_trace(
            project_root=self.project_root,
            session_id=sid,
            query=user_request,
            settings=self.config.diagnostics,
        )
        token = set_current_trace(trace)
        reply = ""
        try:
            self.last_route_decision = None
            if queue_wait_ms > 0:
                trace.event(
                    "web.queue_wait",
                    duration_ms=queue_wait_ms,
                )
            trace.event(
                "intent.route.deferred_to_agent",
                route_status="deferred",
            )
            reply = self._run_async(self._ask_async(user_request, session_id))
            trace.finish(status="ok", reply_chars=len(reply))
            return reply
        except Exception as exc:  # noqa: BLE001 - keep CLI/user output clean.
            reply = self._format_runtime_error(exc)
            trace.event(
                "request.error",
                status="error",
                error=f"{exc.__class__.__name__}: {exc}",
            )
            trace.finish(
                status="error",
                reply_chars=len(reply),
                error=f"{exc.__class__.__name__}: {exc}",
            )
            return reply
        finally:
            self.last_trace_summary = trace.summary()
            reset_current_trace(token)

    def route_business_intent_first(
        self,
        user_request: str,
        session_id: str | None = None,
        *,
        queue_wait_ms: float = 0.0,
    ) -> RouteDecision:
        """Route an original business query without first invoking ReAct."""
        self._configure_runtime_logging()
        sid = session_id or "default"
        trace = create_trace(
            project_root=self.project_root,
            session_id=sid,
            query=user_request,
            settings=self.config.diagnostics,
        )
        token = set_current_trace(trace)
        try:
            self.last_route_decision = None
            if queue_wait_ms > 0:
                trace.event(
                    "web.queue_wait",
                    duration_ms=queue_wait_ms,
                )
            trace.event(
                "intent.route.pre_react",
                route_status="pre_react",
            )
            decision = self.intent_router.route(user_request, sid)
            self.last_route_decision = decision
            trace.finish(status="ok", reply_chars=0)
            return decision
        except Exception as exc:
            trace.event(
                "request.error",
                status="error",
                error=f"{exc.__class__.__name__}: {exc}",
            )
            trace.finish(
                status="error",
                reply_chars=0,
                error=f"{exc.__class__.__name__}: {exc}",
            )
            raise
        finally:
            self.last_trace_summary = trace.summary()
            reset_current_trace(token)

    def _format_runtime_error(self, exc: Exception) -> str:
        message = str(exc)
        if "MODEL_EXECUTION_FAILED" in message or "InternalServerError" in message:
            return (
                f"模型调用失败：{self._llm_model()} 暂时不可用或返回错误，"
                "请稍后重试。"
            )
        return f"Agent 执行失败：{exc.__class__.__name__}。{message}"
