@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "WORKSPACE=%PROJECT_ROOT%\qwenpaw_workspace"
set "QWENPAW=D:\hundsun\software\QwenPaw\qwenpaw.cmd"

if not exist "%QWENPAW%" (
  echo QwenPaw command not found: %QWENPAW%
  exit /b 1
)

"%QWENPAW%" agents create ^
  --agent-id universal_entry_agent ^
  --name "意图路由 Agent" ^
  --description "分析用户意图，拆解任务，调用合适的子智能体、工具或 MCP，并汇总结果统一回复。" ^
  --workspace-dir "%WORKSPACE%" ^
  --language zh

