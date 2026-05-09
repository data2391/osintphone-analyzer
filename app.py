import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import threading
import subprocess
import sys
import json
import base64
import time
import random
import io
from pathlib import Path

def ensure_deps():
    try:
        from playwright.sync_api import sync_playwright  # noqa
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright", "-q"])
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    try:
        from PIL import Image  # noqa
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow", "-q"])
    try:
        import telethon  # noqa
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "telethon", "-q"])

BASE_DIR   = Path(__file__).parent
WA_DIR     = BASE_DIR / "sessions" / "whatsapp"
TG_DIR     = BASE_DIR / "sessions" / "telegram"
RESULT_DIR = BASE_DIR / "results"
PHOTO_DIR  = BASE_DIR / "results" / "photos"
for d in [WA_DIR, TG_DIR, RESULT_DIR, PHOTO_DIR]:
    d.mkdir(parents=True, exist_ok=True)

def has_session(path: Path) -> bool:
    """Retourne True si le dossier session existe (même vide) — le nav sera lancé."""
    path.mkdir(parents=True, exist_ok=True)
    return True  # On laisse toujours le navigateur s'ouvrir

# ══════════════════════════════════════════════════════════════════════════════
#  WHATSAPP
# ══════════════════════════════════════════════════════════════════════════════
def fetch_whatsapp(numero: str, log_fn) -> dict:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    result = {"active": False, "name": None, "photo_b64": None,
              "about": None, "session_missing": False}
    numero_clean = numero.replace(" ", "").replace("-", "")
    if not numero_clean.startswith("+"):
        numero_clean = "+" + numero_clean

    with sync_playwright() as p:
        log_fn("🌐 Lancement Chromium WhatsApp Web...")
        launch_kwargs = dict(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--start-maximized"],
            viewport=None, no_viewport=True,
        )
        ctx = None
        for attempt, kw in enumerate([
            launch_kwargs,
            {"headless": False, "args": ["--no-sandbox"], "viewport": {"width": 1280, "height": 900}},
            {"headless": False, "args": ["--no-sandbox", "--disable-gpu"], "viewport": {"width": 1280, "height": 900}},
        ]):
            try:
                log_fn(f"   Tentative {attempt+1}/3 lancement Chromium...")
                ctx = p.chromium.launch_persistent_context(str(WA_DIR), **kw)
                log_fn(f"   ✅ Chromium lancé (tentative {attempt+1}).")
                break
            except Exception as e_launch:
                log_fn(f"   ⚠️ Tentative {attempt+1} échouée : {e_launch}")
        if ctx is None:
            log_fn("❌ Impossible de lancer Chromium. Vérifie : playwright install chromium")
            return result
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # 1. Charger WhatsApp Web
        log_fn("🔗 Chargement de web.whatsapp.com...")
        try:
            page.goto("https://web.whatsapp.com", timeout=60000,
                      wait_until="domcontentloaded")
        except PWTimeout:
            log_fn("⚠️ Timeout chargement WhatsApp Web.")
            ctx.close(); return result

        # 2. Attendre QR ou session
        log_fn("⏳ Attente chargement complet (60s max)...")
        try:
            page.wait_for_selector(
                "canvas[aria-label='Scan this QR code to link a device'], "
                "div[data-testid='chat-list'], #side",
                timeout=60000)
        except PWTimeout:
            log_fn("❌ WhatsApp Web n'a pas chargé.")
            ctx.close(); return result

        # 3. QR → attendre scan sans fermer
        qr = page.locator("canvas[aria-label='Scan this QR code to link a device']")
        if qr.count() > 0:
            log_fn("📱 QR Code — scannez avec votre téléphone (3 min max)...")
            result["session_missing"] = True
            try:
                page.wait_for_selector("div[data-testid='chat-list'], #side",
                                       timeout=180000)
                log_fn("✅ Session enregistrée ! Relancez le scan.")
            except PWTimeout:
                log_fn("❌ QR non scanné.")
            ctx.close(); return result

        log_fn("✅ Session WhatsApp active.")

        # 4. Naviguer vers la conversation
        wa_url = f"https://web.whatsapp.com/send?phone={numero_clean}"
        log_fn(f"🔍 Ouverture conversation {numero_clean}...")
        try:
            page.goto(wa_url, timeout=60000, wait_until="domcontentloaded")
        except PWTimeout:
            log_fn("⚠️ Timeout, rechargement...")
            try:
                page.reload(timeout=60000, wait_until="domcontentloaded")
            except PWTimeout:
                log_fn("❌ Impossible de charger."); ctx.close(); return result

        # 5. Attente active stabilisation page (E2E keys loading)
        log_fn("⏳ Stabilisation page WhatsApp (30s max)...")
        stabilize_sel = (
            "div[data-testid='conversation-header'], #main header, "
            "div[data-testid='confirm-popup'], div[data-testid='alert-dialog'], "
            "footer div[data-testid='compose-box-input']"
        )
        try:
            page.wait_for_selector(stabilize_sel, timeout=30000)
            log_fn("✅ Page stabilisée.")
        except PWTimeout:
            log_fn("⏳ Chargement lent, +15s...")
            time.sleep(15)

        # 6. Numéro invalide ?
        invalid = page.locator(
            "div[data-testid='confirm-popup'], div[data-testid='alert-dialog'], "
            "span:has-text('Phone number shared via url is invalid')")
        if invalid.count() > 0:
            log_fn("❌ Numéro non enregistré sur WhatsApp.")
            ctx.close(); return result

        # 7. Vérifier header conversation
        chat_ok = False
        for sel in ["div[data-testid='conversation-header']", "#main header", "div#main header"]:
            if page.locator(sel).count() > 0:
                chat_ok = True; break
        if not chat_ok:
            log_fn("⏳ Dernier recours +10s...")
            time.sleep(10)
            for sel in ["div[data-testid='conversation-header']", "#main header"]:
                if page.locator(sel).count() > 0:
                    chat_ok = True; break
        if not chat_ok:
            log_fn("❌ Conversation non chargée.")
            ctx.close(); return result

        result["active"] = True
        log_fn("✅ Numéro actif sur WhatsApp !")

        # 8. Récupérer nom depuis le header
        try:
            header = page.locator("#main header, div[data-testid='conversation-header']").first
            for sel in ["span[data-testid='conversation-info-header-chat-title']",
                        "span[title]", "._21S-L span"]:
                el = header.locator(sel).first
                if el.count() > 0:
                    val = el.get_attribute("title") or el.inner_text()
                    if val and val.strip():
                        result["name"] = val.strip()
                        log_fn(f"👤 Nom : {result['name']}")
                        break
        except Exception as e:
            log_fn(f"⚠️ Nom : {e}")

        # 9. Récupérer photo de profil en HAUTE RÉSOLUTION
        log_fn("📷 Récupération photo WhatsApp...")
        photo_ok = False

        # ── Ouvrir le panneau profil via clic sur l'en-tête ────────────────────
        try:
            # Cliquer sur le nom/header pour ouvrir le panneau droit
            header = page.locator("#main header").first
            header.click(timeout=5000)
            time.sleep(2)

            # Récupérer le statut/about dans le panneau
            for sel in [
                "div[data-testid='status-descript'] span",
                "div[data-testid='about-link-icon'] ~ div span",
                "span[data-testid='about-link-icon'] ~ span",
            ]:
                el = page.locator(sel).first
                if el.count() > 0:
                    txt = el.inner_text().strip()
                    if txt:
                        result["about"] = txt
                        log_fn(f"💬 Statut : {txt}")
                        break
        except Exception:
            pass

        # ── Méthode 1 : lire les imgs, extraire l'URL CDN, dl via requests+cookies ─
        try:
            log_fn("📷 Méthode 1 : lecture imgs chargées en page...")
            time.sleep(1)
            imgs_info = page.evaluate("""
                () => {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    return imgs
                        .filter(i => i.naturalWidth > 50 && i.src)
                        .map(i => ({src: i.src, w: i.naturalWidth, h: i.naturalHeight}))
                        .sort((a,b) => (b.w*b.h) - (a.w*a.h));
                }
            """)
            log_fn(f"   {len(imgs_info)} img(s) : {[(i['w'],i['h'],i['src'][:50]) for i in imgs_info[:3]]}")

            # Récupérer les cookies de session du navigateur
            cookies = ctx.cookies()
            cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                      if "whatsapp" in c.get("domain",""))

            for img_info in imgs_info:
                src = img_info["src"]
                w   = img_info["w"]
                if w < 50:
                    continue

                # ── Cas blob : fetch JS (même origine, pas de CORS) ──────────
                if src.startswith("blob:"):
                    try:
                        img_data = page.evaluate(f"""
                            async () => {{
                                const r = await fetch('{src}');
                                const buf = await r.arrayBuffer();
                                return Array.from(new Uint8Array(buf));
                            }}
                        """)
                        if img_data and len(img_data) > 1000:
                            result["photo_b64"] = base64.b64encode(bytes(img_data)).decode()
                            photo_ok = True
                            log_fn(f"📷 Photo blob {w}px ({len(img_data)//1024} KB).")
                            break
                    except Exception:
                        pass

                # ── Cas URL CDN (cdn.whatsapp.net) : téléchargement Python ───
                elif src.startswith("http"):
                    try:
                        import urllib.request
                        req = urllib.request.Request(src, headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/124.0.0.0 Safari/537.36"
                            ),
                            "Referer": "https://web.whatsapp.com/",
                            "Origin":  "https://web.whatsapp.com",
                            "Cookie":  cookie_header,
                        })
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            body = resp.read()
                        if len(body) > 1000:
                            result["photo_b64"] = base64.b64encode(body).decode()
                            photo_ok = True
                            log_fn(f"📷 Photo CDN {w}px ({len(body)//1024} KB).")
                            break
                    except Exception as e_cdn:
                        log_fn(f"   ⚠️ CDN {w}px échoué : {e_cdn}")
                        continue
        except Exception as e:
            log_fn(f"⚠️ Méthode 1 : {e}")

        # ── Méthode 2 : screenshot de chaque img de taille > 80px ──────────────
        if not photo_ok:
            try:
                log_fn("📷 Méthode 2 : screenshot des éléments img...")
                for sel in [
                    "img[src*='blob']",
                    "div[data-testid='profile-avatar'] img",
                    "div[data-testid='profile-photo'] img",
                    "#app-mount img",
                ]:
                    els = page.locator(sel).all()
                    for img_el in els:
                        try:
                            bb = img_el.bounding_box()
                            if bb and bb["width"] >= 80:
                                shot = img_el.screenshot()
                                if len(shot) > 3000:
                                    result["photo_b64"] = base64.b64encode(shot).decode()
                                    photo_ok = True
                                    log_fn(f"📷 Screenshot img {int(bb['width'])}px ({len(shot)//1024} KB).")
                                    break
                        except Exception:
                            continue
                    if photo_ok:
                        break
            except Exception as e:
                log_fn(f"⚠️ Méthode 2 : {e}")

        # ── Méthode 3 : screenshot de la zone de la photo dans le panneau ───────
        if not photo_ok:
            try:
                log_fn("📷 Méthode 3 : screenshot zone panneau profil...")
                for sel in [
                    "div[data-testid='profile-avatar']",
                    "section div._2au8k",
                    "div._3W2ap",
                    "div._1WliW",
                    "div._3Whw5",
                ]:
                    el = page.locator(sel).first
                    if el.count() > 0:
                        bb = el.bounding_box()
                        if bb and bb["width"] >= 60:
                            shot = el.screenshot()
                            if len(shot) > 1000:
                                result["photo_b64"] = base64.b64encode(shot).decode()
                                photo_ok = True
                                log_fn(f"📷 Screenshot panneau ({int(bb['width'])}px, {len(shot)//1024} KB).")
                                break
            except Exception as e:
                log_fn(f"⚠️ Méthode 3 : {e}")

        # ── Méthode 4 : screenshot avatar header (fallback 40px) ────────────────
        if not photo_ok:
            try:
                header = page.locator("#main header").first
                avatar_zone = header.locator("div[data-testid='avatar'], div[role='img']").first
                if avatar_zone.count() > 0:
                    shot = avatar_zone.screenshot()
                    if len(shot) > 200:
                        result["photo_b64"] = base64.b64encode(shot).decode()
                        photo_ok = True
                        log_fn("⚠️ Photo avatar header (40px — profil peut-être privé).")
            except Exception as e:
                log_fn(f"⚠️ Méthode 4 : {e}")

        page.keyboard.press("Escape")
        if not photo_ok:
            log_fn("⚠️ Photo non disponible (profil privé ou non défini).")

        ctx.close()
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM — désactivé dans cette version
# ══════════════════════════════════════════════════════════════════════════════
TG_API_FILE     = BASE_DIR / "sessions" / "tg_api.json"
TG_SESSION_FILE = str(BASE_DIR / "sessions" / "telethon_session")

def _load_tg_api():
    return None, None

def _save_tg_api(api_id, api_hash):
    pass

def fetch_telegram(numero: str, log_fn) -> dict:
    log_fn("ℹ️  Module Telegram non disponible dans cette version.")
    return {"active": False, "name": None, "username": None,
            "photo_b64": None, "session_missing": False}

# ══════════════════════════════════════════════════════════════════════════════
#  INTERFACE GRAPHIQUE
# ══════════════════════════════════════════════════════════════════════════════
BG       = "#0f0f0f"
SURFACE  = "#161616"
SURFACE2 = "#1e1e1e"
BORDER   = "#2a2a2a"
ACCENT   = "#00b894"
TEXT     = "#e0e0e0"
MUTED    = "#666666"
WA_COL   = "#25D366"
TG_COL   = "#2AABEE"
RED      = "#ff6b6b"
YELLOW   = "#f9ca24"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("📡 OSINT Phone Analyzer v1.0 — by Data2391")
        self.geometry("980x800")
        self.configure(bg=BG)
        self.resizable(True, True)
        self._wa_result = {}
        self._tg_result = {}
        self._build_ui()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=24, pady=(18, 0))
        tk.Label(hdr, text="📡", font=("Segoe UI Emoji", 24), bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="  OSINT Phone Analyzer",
                 font=("Segoe UI", 17, "bold"), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="  by Data2391  |  v1.0",
                 font=("Segoe UI", 10), bg=BG, fg=MUTED).pack(side="left", pady=(5, 0))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)

        # Saisie
        inp = tk.Frame(self, bg=BG)
        inp.pack(fill="x", padx=24)
        tk.Label(inp, text="Numéro (format international) :",
                 font=("Segoe UI", 11), bg=BG, fg=TEXT).pack(anchor="w")
        row = tk.Frame(inp, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        self.entry_num = tk.Entry(
            row, font=("Courier New", 15), bg=SURFACE2, fg=ACCENT,
            insertbackground=ACCENT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        self.entry_num.insert(0, "+33")
        self.entry_num.pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 10))
        self.btn_scan = tk.Button(
            row, text="🔍  SCANNER", font=("Segoe UI", 11, "bold"),
            bg=ACCENT, fg="#000", relief="flat", bd=0,
            padx=22, pady=8, cursor="hand2", command=self._start_scan)
        self.btn_scan.pack(side="left")

        # Badges session + boutons de connexion
        sess_row = tk.Frame(self, bg=BG)
        sess_row.pack(fill="x", padx=24, pady=(8, 0))
        self.wa_sess_lbl = tk.Label(sess_row, font=("Segoe UI", 9), bg=BG)
        self.wa_sess_lbl.pack(side="left")
        tk.Button(sess_row, text="⚙️ Connecter WA",
                  font=("Segoe UI", 8), bg=SURFACE2, fg=WA_COL,
                  relief="flat", bd=0, padx=8, pady=3, cursor="hand2",
                  command=self._open_session_wa).pack(side="left", padx=(6, 20))
        self.tg_sess_lbl = tk.Label(sess_row, font=("Segoe UI", 9), bg=BG)
        self.tg_sess_lbl.pack(side="left")
        tk.Label(sess_row, text="✈️  TG : bientôt disponible",
                  font=("Segoe UI", 9), bg=BG, fg="#444").pack(side="left")
        self._refresh_session_labels()

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)

        # Cards résultats
        cards = tk.Frame(self, bg=BG)
        cards.pack(fill="x", padx=24)
        self._make_card(cards, "WhatsApp", WA_COL, "📱").pack(
            side="left", fill="both", expand=True, padx=(0, 8))
        self._make_card(cards, "Telegram", TG_COL, "✈️").pack(
            side="left", fill="both", expand=True, padx=(8, 0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=10)

        # Log
        tk.Label(self, text="Journal d'investigation :",
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(anchor="w", padx=24)
        log_wrap = tk.Frame(self, bg=SURFACE, highlightthickness=1,
                            highlightbackground=BORDER)
        log_wrap.pack(fill="both", expand=True, padx=24, pady=(4, 8))
        self.log_box = tk.Text(log_wrap, bg=SURFACE, fg="#aaa",
                               font=("Courier New", 9),
                               relief="flat", bd=0, state="disabled", wrap="word")
        sc = ttk.Scrollbar(log_wrap, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sc.set)
        sc.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=6)

        # Footer
        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=24, pady=(0, 12))
        tk.Button(foot, text="🗑️  Purger (Zéro Trace)",
                  font=("Segoe UI", 9), bg="#2a1a1a", fg=RED,
                  relief="flat", bd=0, padx=12, pady=5,
                  cursor="hand2", command=self._purge).pack(side="left")
        tk.Button(foot, text="💾  Exporter JSON",
                  font=("Segoe UI", 9), bg="#1a2a1a", fg=ACCENT,
                  relief="flat", bd=0, padx=12, pady=5,
                  cursor="hand2", command=self._export_json).pack(side="left", padx=8)
        tk.Button(foot, text="🔄  Réinitialiser sessions",
                  font=("Segoe UI", 9), bg="#2a2a1a", fg=YELLOW,
                  relief="flat", bd=0, padx=12, pady=5,
                  cursor="hand2", command=self._reset_sessions).pack(side="left")
        self.status_lbl = tk.Label(foot, text="En attente...",
                                   font=("Segoe UI", 9), bg=BG, fg=MUTED)
        self.status_lbl.pack(side="right")

    def _make_card(self, parent, platform, color, icon):
        attr = platform.lower()
        BUBBLE = 110

        card = tk.Frame(parent, bg=SURFACE, highlightthickness=1,
                        highlightbackground=BORDER, pady=16, padx=16)

        # Titre + badge
        h = tk.Frame(card, bg=SURFACE)
        h.pack(fill="x", pady=(0, 10))
        tk.Label(h, text=f"{icon}  {platform}",
                 font=("Segoe UI", 13, "bold"), bg=SURFACE, fg=color).pack(side="left")
        badge = tk.Label(h, text="—", font=("Segoe UI", 9, "bold"),
                         bg=SURFACE2, fg=MUTED, padx=10, pady=3)
        badge.pack(side="right")
        setattr(self, f"{attr}_badge", badge)

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=(0, 14))

        # Corps : bulle | infos
        body = tk.Frame(card, bg=SURFACE)
        body.pack(fill="x")

        # Colonne gauche : bulle circulaire
        bubble_col = tk.Frame(body, bg=SURFACE)
        bubble_col.pack(side="left", padx=(0, 18), anchor="n")

        canvas = tk.Canvas(bubble_col, width=BUBBLE, height=BUBBLE,
                           bg=SURFACE, highlightthickness=0)
        canvas.pack()
        # Anneau coloré
        canvas.create_oval(2, 2, BUBBLE-2, BUBBLE-2,
                           fill="#252525", outline=color, width=3, tags="ring")
        canvas.create_text(BUBBLE//2, BUBBLE//2 - 8, text="📷",
                           font=("Segoe UI Emoji", 24), fill="#555", tags="ph_icon")
        canvas.create_text(BUBBLE//2, BUBBLE//2 + 18, text="aucune photo",
                           font=("Segoe UI", 7), fill="#555", tags="ph_txt")
        setattr(self, f"{attr}_canvas", canvas)
        setattr(self, f"{attr}_bubble_size", BUBBLE)
        setattr(self, f"{attr}_photo_tk", None)

        btn_dl = tk.Button(
            bubble_col, text="⬇ Télécharger",
            font=("Segoe UI", 8), bg="#1a2a1a", fg=ACCENT,
            relief="flat", bd=0, padx=8, pady=4, cursor="hand2",
            command=lambda p=platform: self._download_photo(p))
        btn_dl.pack(pady=(8, 0))
        btn_dl.configure(state="disabled")
        setattr(self, f"{attr}_dl_btn", btn_dl)

        # Colonne droite : infos
        info_col = tk.Frame(body, bg=SURFACE)
        info_col.pack(side="left", fill="both", expand=True, anchor="n")

        name_lbl = tk.Label(info_col, text="—",
                            font=("Segoe UI", 14, "bold"),
                            bg=SURFACE, fg=TEXT, wraplength=230, justify="left")
        name_lbl.pack(anchor="w")
        setattr(self, f"{attr}_name", name_lbl)

        if platform == "Telegram":
            user_lbl = tk.Label(info_col, text="@—",
                                font=("Courier New", 11), bg=SURFACE,
                                fg=color, wraplength=230)
            user_lbl.pack(anchor="w", pady=(8, 0))
            setattr(self, f"{attr}_user", user_lbl)
        else:
            about_lbl = tk.Label(info_col, text="",
                                 font=("Segoe UI", 9, "italic"),
                                 bg=SURFACE, fg=MUTED, wraplength=230, justify="left")
            about_lbl.pack(anchor="w", pady=(8, 0))
            setattr(self, f"{attr}_about", about_lbl)

        return card

    def _draw_bubble(self, platform: str, photo_b64: str, color: str):
        """Dessine la photo recadrée en cercle dans le canvas."""
        from PIL import Image, ImageDraw, ImageTk
        attr = platform.lower()
        canvas = getattr(self, f"{attr}_canvas")
        size   = getattr(self, f"{attr}_bubble_size")
        btn    = getattr(self, f"{attr}_dl_btn")

        if not photo_b64:
            canvas.delete("photo_img")
            canvas.itemconfigure("ph_icon", state="normal")
            canvas.itemconfigure("ph_txt",  state="normal")
            return

        try:
            raw = base64.b64decode(photo_b64)
            img = Image.open(io.BytesIO(raw)).convert("RGBA")

            # Recadrer en carré centré
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top  = (h - side) // 2
            img = img.crop((left, top, left + side, top + side))
            img = img.resize((size, size), Image.LANCZOS)

            # Masque circulaire
            mask = Image.new("L", (size, size), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((2, 2, size-2, size-2), fill=255)
            result_img = Image.new("RGBA", (size, size), (0,0,0,0))
            result_img.paste(img, (0, 0), mask)

            photo_tk = ImageTk.PhotoImage(result_img)
            setattr(self, f"{attr}_photo_tk", photo_tk)  # éviter GC

            canvas.delete("photo_img")
            canvas.itemconfigure("ph_icon", state="hidden")
            canvas.itemconfigure("ph_txt",  state="hidden")
            canvas.create_image(size//2, size//2, image=photo_tk, tags="photo_img")
            # Redessiner l'anneau par-dessus
            canvas.delete("ring")
            canvas.create_oval(2, 2, size-2, size-2,
                               fill="", outline=color, width=3, tags="ring")
            btn.configure(state="normal")
        except Exception as e:
            canvas.itemconfigure("ph_icon", state="normal")
            canvas.itemconfigure("ph_txt",  state="normal")
            btn.configure(state="normal")

    def _refresh_session_labels(self):
        wa_ok = has_session(WA_DIR)
        tg_ok = False  # Telegram désactivé v1.0
        self.wa_sess_lbl.configure(
            text=f"📱 WA : {'✅ Session OK' if wa_ok else '❌ Pas de session'}",
            fg=WA_COL if wa_ok else RED)
        self.tg_sess_lbl.configure(
            text="✈️  TG : prochainement",
            fg=TG_COL if tg_ok else RED)

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _reset_cards_ui(self):
        for a in ["whatsapp_badge", "telegram_badge"]:
            getattr(self, a).configure(text="⏳", fg=MUTED)
        for a in ["whatsapp_name", "telegram_name"]:
            getattr(self, a).configure(text="\u2014", fg=MUTED)
        self.telegram_user.configure(text="@\u2014", fg=MUTED)
        self.whatsapp_about.configure(text="", fg=MUTED)
        for a in ["whatsapp_dl_btn", "telegram_dl_btn"]:
            getattr(self, a).configure(state="disabled")
        for plat, col in [("whatsapp", WA_COL), ("telegram", TG_COL)]:
            canvas = getattr(self, f"{plat}_canvas")
            size   = getattr(self, f"{plat}_bubble_size")
            canvas.delete("photo_img")
            canvas.itemconfigure("ph_icon", state="normal")
            canvas.itemconfigure("ph_txt",  state="normal")
            canvas.delete("ring")
            canvas.create_oval(2, 2, size-2, size-2,
                               fill="#252525", outline=col, width=3, tags="ring")


    # ── Scan ──────────────────────────────────────────────────────────────────────
    def _start_scan(self):
        numero = self.entry_num.get().strip()
        if len(numero) < 8:
            messagebox.showwarning("Numéro invalide", "Format attendu : +33XXXXXXXXX")
            return
        self.btn_scan.configure(state="disabled", text="⏳ Scan...")
        self.status_lbl.configure(text="Analyse en cours...")
        self._reset_cards_ui()
        self._log(f"\n{'─'*52}")
        self._log(f"🎯 Cible : {numero}")
        self._log(f"{'─'*52}")
        threading.Thread(target=self._run_scan, args=(numero,), daemon=True).start()

    def _run_scan(self, numero):
        try:
            ensure_deps()
            self._log("\n[WhatsApp]")
            wa = fetch_whatsapp(numero, self._log)
            self._wa_result = wa
            self.after(0, self._update_wa, wa)
            time.sleep(random.uniform(2, 3))
            self._log("\n[Telegram]")
            tg = fetch_telegram(numero, self._log)
            self._tg_result = tg
            self.after(0, self._update_tg, tg)
            self._log("\n✅ Analyse terminée.")
            self.after(0, lambda: self.status_lbl.configure(text="Terminé."))
            self.after(0, self._refresh_session_labels)
        except Exception as e:
            self._log(f"\n❌ Erreur : {e}")
            self.after(0, lambda: self.status_lbl.configure(text="Erreur."))
        finally:
            self.after(0, lambda: self.btn_scan.configure(
                state="normal", text="🔍  SCANNER"))

    def _update_wa(self, wa):
        if wa.get("session_missing"):
            self.whatsapp_badge.configure(text="\U0001f4f1 SCAN QR", fg=YELLOW)
            self.whatsapp_name.configure(text="Scannez le QR puis relancez.", fg=YELLOW)
            return
        if wa.get("active"):
            self.whatsapp_badge.configure(text="\u2705 ACTIF", fg=WA_COL)
            self.whatsapp_name.configure(text=wa.get("name") or "\u2014", fg=TEXT)
            self.whatsapp_about.configure(text=wa.get("about") or "", fg=MUTED)
            self._draw_bubble("whatsapp", wa.get("photo_b64"), WA_COL)
        else:
            self.whatsapp_badge.configure(text="\u274c INACTIF", fg=RED)
            self.whatsapp_name.configure(text="Non enregistr\u00e9", fg=RED)

    def _update_tg(self, tg):
        if tg.get("session_missing"):
            self.telegram_badge.configure(text="\U0001f4f1 CONNEXION", fg=YELLOW)
            self.telegram_name.configure(text="Connectez-vous puis relancez.", fg=YELLOW)
            self.telegram_user.configure(text="@\u2014", fg=MUTED)
            return
        if tg.get("active"):
            self.telegram_badge.configure(text="\u2705 ACTIF", fg=TG_COL)
            self.telegram_name.configure(text=tg.get("name") or "\u2014", fg=TEXT)
            uname = tg.get("username") or "\u2014"
            self.telegram_user.configure(
                text=uname if uname.startswith("@") else f"@{uname}", fg=TG_COL)
            self._draw_bubble("telegram", tg.get("photo_b64"), TG_COL)
        else:
            self.telegram_badge.configure(text="\u274c INACTIF", fg=RED)
            self.telegram_name.configure(text="Non enregistr\u00e9", fg=RED)
            self.telegram_user.configure(text="@\u2014", fg=MUTED)

    def _set_photo(self, platform: str, photo_b64, color):
        self._draw_bubble(platform, photo_b64, color)

    def _download_photo(self, platform: str):
        result = self._wa_result if platform == "WhatsApp" else self._tg_result
        photo_b64 = result.get("photo_b64")
        if not photo_b64:
            messagebox.showinfo("Photo", "Aucune photo disponible.")
            return
        numero = self.entry_num.get().strip().replace("+", "").replace(" ", "")
        default_name = f"photo_{platform.lower()}_{numero}.png"
        filepath = filedialog.asksaveasfilename(
            title=f"Enregistrer la photo {platform}",
            initialfile=default_name,
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("Tous", "*.*")]
        )
        if not filepath:
            return
        try:
            from PIL import Image
            raw = base64.b64decode(photo_b64)
            img = Image.open(io.BytesIO(raw))
            # Sauvegarder en pleine résolution
            img.save(filepath)
            self._log(f"💾 Photo {platform} sauvegardée : {filepath}")
            messagebox.showinfo("Téléchargement", f"Photo sauvegardée :\n{filepath}")
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de sauvegarder : {e}")

    # ── Actions footer ─────────────────────────────────────────────────────────
    def _purge(self):
        if messagebox.askyesno("Zéro Trace", "Supprimer tous les résultats et photos ?"):
            import shutil
            shutil.rmtree(RESULT_DIR, ignore_errors=True)
            RESULT_DIR.mkdir()
            PHOTO_DIR.mkdir()
            self._wa_result = {}
            self._tg_result = {}
            self._log("🗑️  Résultats purgés.")

    def _open_session_wa(self):
        """Ouvre Chromium sur WhatsApp Web pour scanner le QR."""
        import threading
        def _run():
            from playwright.sync_api import sync_playwright
            self._log("\n🌐 Ouverture WhatsApp Web pour connexion...")
            try:
                with sync_playwright() as p:
                    launch_kw = dict(headless=False,
                                     args=["--no-sandbox","--start-maximized"],
                                     viewport=None, no_viewport=True)
                    ctx = None
                    for kw_try in [
                        launch_kw,
                        {"headless": False, "args": ["--no-sandbox"], "viewport": {"width":1280,"height":900}},
                    ]:
                        try:
                            ctx = p.chromium.launch_persistent_context(str(WA_DIR), **kw_try)
                            break
                        except Exception as e_o:
                            self._log(f"   ⚠️ Launch : {e_o}")
                    if ctx is None:
                        self._log("❌ Chromium introuvable. Lance : playwright install chromium")
                        return
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.goto("https://web.whatsapp.com", timeout=30000,
                              wait_until="domcontentloaded")
                    self._log("📱 WhatsApp Web ouvert — scannez le QR puis fermez le navigateur.")
                    # Attendre que l'utilisateur ferme
                    ctx.wait_for_event("close", timeout=300000)
            except Exception as e:
                self._log(f"⚠️ Session WA : {e}")
            self.after(0, self._refresh_session_labels)
        threading.Thread(target=_run, daemon=True).start()

    def _reset_sessions(self):
        if messagebox.askyesno("Sessions",
                               "Supprimer les sessions WA et TG ?\n(Reconnexion requise)"):
            import shutil
            for d in [WA_DIR, TG_DIR]:
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True)
            self._log("🔄 Sessions réinitialisées.")
            self._refresh_session_labels()

    def _export_json(self):
        if not self._wa_result and not self._tg_result:
            messagebox.showinfo("Export", "Aucun résultat.")
            return
        numero = self.entry_num.get().strip().replace("+", "").replace(" ", "")
        data = {
            "numero": self.entry_num.get().strip(),
            "whatsapp": {k: v for k, v in self._wa_result.items() if k != "photo_b64"},
            "telegram": {k: v for k, v in self._tg_result.items() if k != "photo_b64"},
        }
        out = RESULT_DIR / f"osint_{numero}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        self._log(f"💾 Exporté : {out}")
        messagebox.showinfo("Export JSON", f"Fichier :\n{out}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
