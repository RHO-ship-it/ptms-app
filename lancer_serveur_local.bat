@echo off
title PTMS Server
cd /d "%~dp0"
echo ===================================================
echo     Demarrage du serveur PTMS (Production Local)
echo ===================================================
python wsgi.py
pause
