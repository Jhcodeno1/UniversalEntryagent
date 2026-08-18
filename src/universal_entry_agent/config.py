from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}$")


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value)
        if match:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default or "")
    return value


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    config_file: Path


class AgentConfig:
    def __init__(self, data: dict[str, Any], paths: ProjectPaths) -> None:
        self.data = data
        self.paths = paths

    @property
    def agent_id(self) -> str:
        return self.data.get("agent", {}).get("id", "universal_entry_agent")

    @property
    def agent_name(self) -> str:
        return self.data.get("agent", {}).get("name", self.agent_id)

    @property
    def llm(self) -> dict[str, Any]:
        return self.data.get("llm", {})

    @property
    def qwenpaw(self) -> dict[str, Any]:
        return self.data.get("qwenpaw", {})

    @property
    def agent_registry(self) -> dict[str, Any]:
        return self.data.get("agent_registry", {})

    @property
    def mcp(self) -> dict[str, Any]:
        return self.data.get("mcp", {})

    @property
    def runtime(self) -> dict[str, Any]:
        return self.data.get("runtime", {})

    @property
    def intent_router(self) -> dict[str, Any]:
        return self.data.get("intent_router", {})

    @property
    def diagnostics(self) -> dict[str, Any]:
        return self.data.get("diagnostics", {})


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "config.json").exists() and (candidate / "src").is_dir():
            return candidate
    return current


def load_config(config_path: str | Path | None = None) -> AgentConfig:
    explicit = Path(config_path).expanduser() if config_path else None
    env_config = os.environ.get("UNIVERSAL_AGENT_CONFIG")
    if explicit is not None:
        path = explicit
    elif env_config:
        path = Path(env_config).expanduser()
    else:
        root = find_project_root()
        path = root / "config.json"
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    expanded = _expand_env(data)
    return AgentConfig(expanded, ProjectPaths(root=path.parent, config_file=path))
