# 意图路由 Agent 快速安装与启动

这是一个独立的“意图路由 Agent”。它统一接收用户请求，先加载 Session 并由 ReAct 判断是否需要证券业务意图识别；业务请求再调用两级意图路由，随后按结果直接回答、调用工具/MCP，或分发给专业 Agent，最后统一返回。

当前项目适合用于验证：

- 统一对话入口
- 意图识别与路由
- 多 Agent 协作
- 工具 / MCP 调用
- Session 记忆
- Web 对话入口

## 一、推荐交付方案

推荐把 Python 环境放在项目目录下，也就是创建：

```text
万能入口agent\.venv\
```

当前启动脚本已经按以下顺序查找 Python：

1. 环境变量 `PYTHON_EXE`
2. 当前项目目录下的 `.venv\Scripts\python.exe`
3. 原开发机路径 `D:\hundsun\software\QwenPaw\python.exe`
4. 系统 PATH 里的 `python`

也就是说，最推荐的方式是：在项目目录创建 `.venv`，然后直接运行 `scripts\run.bat` 或 `scripts\web.bat`。

## 二、安装步骤

进入项目目录：

```powershell
cd "D:\xxx\xxx\万能入口agent"
```

创建本项目自己的 Python 环境：

```powershell
py -3.10 -m venv .venv
```

激活环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

如果目标机器不能访问外网，需要提前在可联网机器下载依赖 wheel 包，再离线安装。依赖列表在：

```text
pyproject.toml
```

## 三、如果不想创建 .venv

也可以继续使用已有 Python，只需要设置 `PYTHON_EXE`：

```powershell
$env:PYTHON_EXE="D:\hundsun\software\QwenPaw\python.exe"
```

或者在 `scripts\run.bat`、`scripts\web.bat`、`scripts\run.ps1`、`scripts\web.ps1` 中修改 fallback 路径：

```text
D:\hundsun\software\QwenPaw\python.exe
```

但不推荐长期依赖这个写死路径，因为换机器后通常不存在。

## 四、启动方式

命令行连续对话：

```powershell
.\scripts\run.bat chat --session default
```

单次提问：

```powershell
.\scripts\run.bat ask --session default "帮我开个机构户"
```

Web 对话入口：

```powershell
.\scripts\web.bat
```

浏览器打开：

```text
http://127.0.0.1:8765
```

指定 host 和端口：

```powershell
.\scripts\web.bat 127.0.0.1 8877
```

PowerShell 脚本也可以使用：

```powershell
.\scripts\run.ps1 chat --session default
.\scripts\web.ps1 -HostName 127.0.0.1 -Port 8765
```

## 五、必须检查的外部依赖

项目源码已经 vendored 到本项目：

```text
src\qwenpaw
src\universal_entry_agent
```

但以下内容仍依赖项目外部环境，交付给别人前必须确认。

### 1. 大模型服务

配置位置：

```text
config.json -> llm
```

当前配置：

```json
{
  "base_url": "xxx",
  "api_key_env": "LLM_API_KEY",
  "model": "GLM-5-FP8"
}
```

目标机器必须能访问这个 `base_url`。如果模型服务地址不同，修改 `config.json` 的：

```text
llm.base_url
llm.model
```

如果模型服务需要 API Key，启动前设置：

```powershell
$env:LLM_API_KEY="你的 API Key"
```

如果服务不校验 Key，也可以给一个占位值：

```powershell
$env:LLM_API_KEY="empty"
```

### 2. Weather 专业 Agent

配置位置：

```text
config.json -> agent_registry.agents -> Weather
```

当前配置依赖本机另一个项目和服务：

```json
{
  "workspace_dir": "D:\\Helloagent\\nanobot",
  "base_url": "xx"
}
```

如果别人不需要天气 Agent，可以从 `agent_registry.agents` 中删除或禁用 Weather 这一项。

如果需要天气 Agent，目标机器必须：

- 拥有 `D:\Helloagent\nanobot` 或改成新的实际路径
- 先启动该 nanobot 服务
- 确认 `http://127.0.0.1:11001/v1` 可访问

### 3. 意图路由 CSV

配置位置：

```text
config.json -> intent_router.catalog.csv_files
```

当前必需文件：

```text
test\SKILL_CHANNEL_CONFIG_202606101012(1).csv
```

这个文件用于加载待路由的意图清单。交付时必须一起带上。如果文件移动了，需要修改：

```json
"path": "test\\SKILL_CHANNEL_CONFIG_202606101012(1).csv"
```

如果暂时不使用意图路由，可以关闭：

```json
"intent_router": {
  "enabled": false
}
```

### 4. MCP 配置

配置位置：

```text
config.json -> mcp.clients
```

当前为空：

```json
"mcp": {
  "clients": {}
}
```

如果后续添加 MCP，需要同时交付：

- MCP server 启动命令或服务地址
- MCP server 所需环境变量
- MCP server 依赖的本地文件或凭证

### 5. QwenPaw Runtime 目录

运行时目录：

```text
qwenpaw_runtime
qwenpaw_runtime.secret
```

首次启动会自动生成或更新这些目录，用于保存 workspace、session、tool 配置和模型 provider 配置。

交付建议：

- 可以不交付历史 `sessions`，让使用方首次启动自动生成
- 如果要保留已有会话记忆，可以一起带上 `qwenpaw_runtime\workspaces\universal_entry_agent\sessions`
- 不建议把真实密钥放进压缩包；如果 `qwenpaw_runtime.secret` 中有敏感信息，交付前先清理或改为让使用方通过环境变量配置

## 六、目录结构

```text
.
├── config.json                         # 主配置：模型、专业 Agent、MCP、意图路由
├── pyproject.toml                      # Python 依赖声明
├── scripts
│   ├── run.bat                         # ask/chat 启动脚本
│   ├── run.ps1
│   ├── web.bat                         # Web 启动脚本
│   └── web.ps1
├── src
│   ├── universal_entry_agent           # 意图路由 Agent 代码
│   └── qwenpaw                         # vendored 核心运行时
├── test
│   └── SKILL_CHANNEL_CONFIG_*.csv      # 意图路由清单
├── qwenpaw_runtime                     # 运行时目录
└── qwenpaw_runtime.secret              # 运行时密钥/模型 provider 配置
```

## 七、交付前检查清单

- `config.json` 中的 `llm.base_url` 在目标机器可访问
- 已设置 `LLM_API_KEY`，或模型服务允许空 Key/占位 Key
- 已创建 `.venv` 并执行 `python -m pip install -e .`
- `test\SKILL_CHANNEL_CONFIG_202606101012(1).csv` 已随项目一起交付
- 如果保留 Weather Agent，已启动 `http://127.0.0.1:11001/v1`
- 如果不保留 Weather Agent，已从 `config.json` 删除 Weather 配置
- 如有 MCP，目标机器已经安装并能启动对应 MCP server
- 不把真实密钥、个人 session、临时日志误打包给外部人员

## 八、常见问题

### 启动后提示找不到模块

确认是在项目根目录启动，或者使用项目自带脚本。脚本会自动设置：

```text
PYTHONPATH=当前项目\src
```

### 启动后连不上模型

检查：

```text
config.json -> llm.base_url
```

并确认目标机器能访问该地址。

### 天气查询失败

Weather Agent 是外部服务，不在本项目内。要么启动 nanobot 服务，要么从 `config.json` 中删除 Weather 配置。

### 意图路由启动失败

检查 CSV 文件是否存在：

```text
test\SKILL_CHANNEL_CONFIG_202606101012(1).csv
```

如果没有这个文件，要么补齐文件，要么关闭 `intent_router.enabled`。

## 九、性能诊断

每个问题都会在启动终端打印带 `trace_id` 的阶段耗时，并写入：

```text
qwenpaw_runtime\diagnostics\YYYY-MM-DD.jsonl
```

分析最近一次请求：

```powershell
python .\scripts\analyze_performance.py
```

列出最近 10 次请求：

```powershell
python .\scripts\analyze_performance.py --list 10
```

详细阶段说明见 `docs\性能诊断日志.md`。
