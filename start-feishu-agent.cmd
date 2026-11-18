@echo off
setlocal
cd /d "%~dp0" || exit /b 1
chcp 65001 >nul

rem 先识别已运行的服务，避免中断当前录屏或打开其他项目。
powershell.exe -NoLogo -NoProfile -NonInteractive -Command ^
  "$tcp = New-Object Net.Sockets.TcpClient;" ^
  "try { $tcp.Connect('127.0.0.1', 18970) } catch { if ($_.Exception.GetBaseException().SocketErrorCode -eq 'ConnectionRefused') { exit 1 }; exit 2 } finally { $tcp.Dispose() };" ^
  "try { $s = Invoke-RestMethod -Uri 'http://127.0.0.1:18970/api/state' -MaximumRedirection 0 -TimeoutSec 25 -ErrorAction Stop;" ^
  "if ($s.mode -eq 'feishu' -and $s.capabilities.agent.enabled -eq $true -and $s.capabilities.agent.provider -eq 'Codex CLI' -and $s.download_url -eq '/api/sample.pdf') { exit 0 } } catch {}; exit 2"
if errorlevel 2 goto occupied
if errorlevel 1 goto launch
start "" "http://127.0.0.1:18970/"
exit /b 0

:launch
if not exist "data\feishu-target.json" (
    echo 尚未绑定飞书表。请先按 README 的说明配置目标表。
    pause
    exit /b 1
)
rem 本次启动固定使用已绑定的表。模型名称可由运行者预先指定。
set "FEISHU_APP_TOKEN="
set "FEISHU_TABLE_ID="
set "FEISHU_TABLE_NAME="
set "PAPER_AGENT_PROVIDER=codex"
if not defined PAPER_MODEL_NAME set "PAPER_MODEL_NAME=gpt-6.2-sol"
if not defined LARK_CLI (
    where lark-cli >nul 2>&1
    if errorlevel 1 if exist "%APPDATA%\npm\lark-cli.cmd" set "LARK_CLI=%APPDATA%\npm\lark-cli.cmd"
)
call start.cmd --mode feishu-cli --port 18970
exit /b %errorlevel%

:occupied
echo 端口 18970 已占用，或当前服务未能返回飞书和 Codex 状态。请查看正在运行的服务窗口。
pause
exit /b 1
