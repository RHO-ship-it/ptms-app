@echo off
title PTMS Serveur Externe (Ngrok Free)
cd /d "%~dp0"
echo ==========================================================
echo     PTMS - Demarrage avec Acces Externe Gratuit (Ngrok)
echo ==========================================================
echo.
echo Note: Si c'est votre premiere utilisation de Ngrok,
echo vous pouvez associer votre compte gratuit avec :
echo   ngrok config add-authtoken VOTRE_TOKEN_ICI
echo (Inscrivez-vous gratuitement sur https://ngrok.com)
echo.
python start_external.py
pause
