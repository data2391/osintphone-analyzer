@echo off
title OSINT Phone Analyzer — Insubmersible
echo Installation des dépendances...
pip install playwright pillow -q
python -m playwright install chromium
echo Démarrage de l'application...
python app.py
pause
