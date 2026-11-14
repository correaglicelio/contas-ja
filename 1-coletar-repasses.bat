@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Coletando repasses federais (Portal da Transparencia) - Mato Grosso...
python scripts\coletar_repasses.py --uf MT
pause
