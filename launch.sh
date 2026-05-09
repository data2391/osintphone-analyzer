#!/bin/bash
echo "📦 Installation des dépendances..."
pip install playwright pillow -q
python -m playwright install chromium
echo "🚀 Démarrage..."
python app.py
