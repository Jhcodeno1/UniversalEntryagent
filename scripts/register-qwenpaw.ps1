$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Workspace = Join-Path $ProjectRoot "qwenpaw_workspace"
$QwenPaw = "D:\hundsun\software\QwenPaw\qwenpaw.cmd"

if (-not (Test-Path $QwenPaw)) {
  throw "QwenPaw command not found: $QwenPaw"
}

& $QwenPaw agents create `
  --agent-id universal_entry_agent `
  --name "意图路由 Agent" `
  --description "分析用户意图，拆解任务，调用合适的子智能体、工具或 MCP，并汇总结果统一回复。" `
  --workspace-dir $Workspace `
  --language zh

Write-Host "Registered agent: universal_entry_agent"

