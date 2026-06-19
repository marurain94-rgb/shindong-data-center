@echo off
chcp 65001 >nul
title 데이터 센터 클라우드
cd /d "%~dp0"
echo 데이터 센터 클라우드를 시작합니다...
start "" http://127.0.0.1:8765
python server.py
pause
