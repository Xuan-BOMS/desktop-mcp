@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
python "C:\Users\Xuan\.codex\mcp\desktop-mcp\desktop_mcp.py" %*
