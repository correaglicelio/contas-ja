@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\montar_site.py
cd public
start "" http://localhost:8000
python -m http.server 8000
