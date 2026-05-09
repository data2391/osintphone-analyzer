# 📡 OSINT Phone Analyzer — Insubmersible

Outil OSINT pour identifier la présence d'un numéro sur WhatsApp et Telegram,
avec récupération du nom/pseudo public et de la photo de profil.

## ▶️ Lancement

### Windows
Double-cliquer sur `launch.bat`

### Linux / macOS
```bash
chmod +x launch.sh && ./launch.sh
```

### Manuel
```bash
pip install playwright pillow
python -m playwright install chromium
python app.py
```

## 🔐 Première utilisation

Au premier lancement :
1. **WhatsApp** → Une fenêtre Chromium s'ouvre sur WhatsApp Web → Scannez le QR Code avec votre téléphone → La session est sauvegardée localement dans `sessions/whatsapp/`
2. **Telegram** → Une fenêtre Chromium s'ouvre sur Telegram Web → Connectez-vous avec votre numéro + code SMS → Session sauvegardée dans `sessions/telegram/`

Les sessions suivantes ne demandent plus de connexion.

## 📁 Structure

```
osint_phone_app/
├── app.py              ← Application principale
├── launch.bat          ← Lanceur Windows
├── launch.sh           ← Lanceur Linux/macOS
├── sessions/
│   ├── whatsapp/       ← Session persistante WhatsApp Web
│   └── telegram/       ← Session persistante Telegram Web
└── results/            ← Exports JSON (purgés après livraison)
```

## ⚖️ Cadre légal
- Aucun message envoyé à la cible
- Lecture uniquement de données publiquement accessibles
- Engagement Zéro Trace : bouton de purge intégré
- Conforme au cadre OSINT sources ouvertes (RGPD Art. 6.1.f)
