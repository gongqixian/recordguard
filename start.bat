@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   RecordGuard - 记录校验 Agent
echo   启动后浏览器打开 http://127.0.0.1:8000
echo   关闭本窗口即停止服务
echo.
if exist ".env" (
  echo   [OK] 检测到 .env，模型层已启用
) else (
  echo   [!] 没找到 .env，语义层会降级为本地规则
  echo       要启用模型：把 .env.example 复制成 .env，填入 RG_API_KEY
  echo.
)
python app.py %*
pause
