import os, time, requests, threading, psycopg2, stripe, re, random, hmac, hashlib, unicodedata, secrets, html
from datetime import datetime, date, timezone, timedelta
from flask import Flask, request, jsonify, session, redirect
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from groq import Groq
from apscheduler.schedulers.background import BackgroundScheduler
import json as _json
_RELANCES_PATH = os.path.join(os.path.dirname(__file__), "auryel_relances_h4_h22.json")
try:
    with open(_RELANCES_PATH, encoding="utf-8") as _f:
        RELANCES_H4_H22 = _json.load(_f)
    print(f"[relances] {len(RELANCES_H4_H22)} messages charges")
except Exception as _e:
    print(f"[relances] JSON non charge : {_e}")
    RELANCES_H4_H22 = []

import random as _random_tarot
_TAROT_MAPPING_PATH = os.path.join(os.path.dirname(__file__), "tarot_mapping.json")
try:
    with open(_TAROT_MAPPING_PATH, encoding="utf-8") as _f:
        TAROT_MAPPING = _json.load(_f)
    print(f"[tarot] {len(TAROT_MAPPING)} tables chargees")
except Exception as _e:
    print(f"[tarot] mapping non charge : {_e}")
    TAROT_MAPPING = {}

_CITATIONS_PATH = os.path.join(os.path.dirname(__file__), "citations_spirituelles.json")
try:
    with open(_CITATIONS_PATH, encoding="utf-8") as _f:
        CITATIONS_SPIRITUELLES = _json.load(_f)
    print(f"[citations] {len(CITATIONS_SPIRITUELLES)} citations chargees")
except Exception as _e:
    print(f"[citations] JSON non charge : {_e}")
    CITATIONS_SPIRITUELLES = []

TEMPLATES_MATIN_ABONNE = [
    "auryel_abo_matin_1", "auryel_abo_matin_2", "auryel_abo_matin_3",
    "auryel_abo_matin_4", "auryel_abo_matin_5", "auryel_abo_matin_6",
    "auryel_abo_matin_7", "auryel_abo_matin_8", "auryel_abo_matin_9",
    "auryel_abo_matin_10"
]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
app.config["SESSION_COOKIE_SECURE"]       = True
app.config["SESSION_COOKIE_HTTPONLY"]     = True
app.config["SESSION_COOKIE_SAMESITE"]     = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"]  = timedelta(hours=4)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
limiter = Limiter(get_remote_address, app=app)

CORS(app, resources={r"/stripe/*": {"origins": ["https://auryelvoyance.com"]}})

WHATSAPP_TOKEN  = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY")
VERIFY_TOKEN    = os.environ.get("VERIFY_TOKEN")
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD")
DATABASE_URL    = os.environ.get("DATABASE_URL")
STRIPE_SK       = os.environ.get("STRIPE_SK")
STRIPE_WEBHOOK  = os.environ.get("STRIPE_WEBHOOK_SECRET")
RESEND_API_KEY  = os.environ.get("RESEND_API_KEY")
FROM_EMAIL      = "contact@auryelvoyance.com"
SITE_URL        = "https://auryelvoyance.com"
DAILY_SECRET    = os.environ.get("DAILY_SECRET")
SEO_SECRET      = os.environ.get("SEO_SECRET")
# ── Vérification variables au démarrage ────────────────────
_REQUIRED_ENV = [
    "SECRET_KEY", "VERIFY_TOKEN", "ADMIN_PASSWORD",
    "DATABASE_URL", "STRIPE_SK", "STRIPE_WEBHOOK_SECRET",
    "WHATSAPP_TOKEN", "PHONE_NUMBER_ID", "GROQ_API_KEY", "RESEND_API_KEY",
    "META_APP_SECRET", "DAILY_SECRET", "SEO_SECRET",
]
_missing_env = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
if _missing_env:
    raise RuntimeError(f"Variables manquantes : {', '.join(_missing_env)}")

PRICES = {
    "mensuel": "price_1TiaigFbuWJZYdVOepK7JtKw",
}

# Seuil engagement minimum pour templates de réactivation post-essai.
# L'onboarding conversationnel représente ~5 échanges → 6 = au moins 1 vrai échange post-onboarding.
MORNING_COLD_THRESHOLD = 6

stripe.api_key = STRIPE_SK
groq_client = Groq(api_key=GROQ_API_KEY)
LLM_PROVIDER       = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL       = os.environ.get("OPENAI_MODEL", "gpt-4o")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL   = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")

if LLM_PROVIDER == "openai" and not OPENAI_API_KEY:
    print("[WARNING] LLM_PROVIDER=openai mais OPENAI_API_KEY est vide — fallback openrouter/groq sera utilisé")

# ============================================================
# CODES ACTIVATION DEPUIS LE SITE
# ============================================================
CODES_ACTIVATION = {
    "AURYEL-SELENA":    ("selena",    "Séléna"),
    "AURYEL-LUNA":      ("luna",      "Luna"),
    "AURYEL-MAIA":      ("maia",      "Maïa"),
    "AURYEL-THEA":      ("thea",      "Théa"),
    "AURYEL-CASSANDRE": ("cassandre", "Cassandre"),
    "AURYEL-MYRIAM":    ("myriam",    "Myriam"),
    "AURYEL-ORION":     ("orion",     "Orion"),
    "AURYEL-EZRA":      ("ezra",      "Ezra"),
    "AURYEL-KAEL":      ("kael",      "Kaël"),
    "AURYEL-RAPHAEL":   ("raphael",   "Raphaël"),
}

# ============================================================
# MOTS EXCLUS — FAUX PRÉNOMS
# ============================================================
MOTS_EXCLUS_PRENOM = {
    "bonjour","bonsoir","bonne","salut","coucou","hello","hi","hey",
    "oui","non","ok","okay","okok","ouais","nan","nope","yes","no",
    "merci","svp","stp","please","super","bien","voilà","voila",
    "cool","parfait","exact","exactement","ah","oh","eh","ha","hm",
    "lol","haha","mdr","xd","😊","🙏","❤️","🌙","✨",
    "peut","peux","veux","dois","suis","fait","faut","vais","aller",
    "jai","jai","cest","cest","dac","dacc","daccord","accord",
    "bcp","bcp","trop","très","vraiment","franchement","honnêtement",
    "mange","travaille","bosser","dormir","partir","revenir",
}

# ============================================================
# SIGNAUX FIN DE CONVERSATION
# ============================================================
SIGNAUX_FIN = [
    "je vais bosser","je vais travailler","je dois bosser","je dois travailler",
    "je pars","je dois y aller","je te laisse","je vous laisse",
    "à plus","a plus","à bientôt","a bientot","à tout","a tout",
    "bonne journée","bonne soirée","bonne nuit","bonne continuation",
    "je vais dormir","je vais me coucher","je vais réfléchir",
    "on se reparle","à demain","a demain","à plus tard","bye","ciao",
    "ok merci","merci beaucoup","merci pour tout","c'est bon merci",
    "je te dis","je reviendrai","je reviens plus tard",
]

# ============================================================
# BASE DE DONNÉES
# ============================================================
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            email TEXT DEFAULT '',
            prenom TEXT DEFAULT '',
            guide TEXT DEFAULT 'selena',
            nom_affiche TEXT DEFAULT '',
            nb_echanges INTEGER DEFAULT 0,
            dernier_outil TEXT DEFAULT '',
            date_premier_contact TEXT,
            date_dernier_contact TEXT,
            etat TEXT DEFAULT 'normal',
            abonne BOOLEAN DEFAULT FALSE,
            date_abonnement TEXT DEFAULT '',
            stripe_customer_id TEXT DEFAULT '',
            relance_j6_envoyee BOOLEAN DEFAULT FALSE,
            relance_j8_envoyee BOOLEAN DEFAULT FALSE,
            dernier_relance_abonne_at TEXT DEFAULT '',
            relance_abonne_count INTEGER DEFAULT 0,
            dernier_rituel_date TEXT DEFAULT '',
            dernier_rituel_type TEXT DEFAULT '',
            depuis_site BOOLEAN DEFAULT FALSE,
            onboarding_step TEXT DEFAULT 'prenom',
            onboarding_done BOOLEAN DEFAULT FALSE,
            profil_initial TEXT,
            onboarding_question TEXT,
            onboarding_psaume INTEGER,
            derniere_relance_conversationnelle_at TEXT DEFAULT '',
            derniere_relance_conversationnelle_type TEXT DEFAULT '',
            relance_hebdo_envoyee BOOLEAN DEFAULT FALSE,
            derniere_relance_hebdo_at TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            phone TEXT,
            role TEXT,
            content TEXT,
            timestamp TEXT
        )
    """)
    # Migration v1
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS dernier_relance_abonne_at TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS relance_abonne_count INTEGER DEFAULT 0")
        c.execute("ALTER TABLE users DROP COLUMN IF EXISTS relance_abonne_envoyee")
    except Exception as e:
        print(f"Migration v1: {e}")
    # Migration v2 — contexte émotionnel
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS prenoms_importants TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS theme_dominant TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS douleur_principale TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS peur_dominante TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS niveau_detresse INTEGER DEFAULT 0")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS niveau_attachement INTEGER DEFAULT 0")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS dernier_sujet_sensible TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS derniere_intention TEXT DEFAULT ''")
    except Exception as e:
        print(f"Migration v2: {e}")
    # Migration v3 — guard anti-double J+7
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS relance_j7_envoyee BOOLEAN DEFAULT FALSE")
    except Exception as e:
        print(f"Migration v3: {e}")
    # Migration v4 — onboarding conversationnel
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_step TEXT DEFAULT 'prenom'")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_done BOOLEAN DEFAULT FALSE")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profil_initial TEXT")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_question TEXT")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_psaume INTEGER")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS derniere_relance_conversationnelle_at TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS derniere_relance_conversationnelle_type TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS relance_hebdo_envoyee BOOLEAN DEFAULT FALSE")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS derniere_relance_hebdo_at TEXT DEFAULT ''")
    except Exception as e:
        print(f"Migration v4: {e}")
    # Migration v5 — opt-out relances automatiques
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS stop_relances BOOLEAN DEFAULT FALSE")
    except Exception as e:
        print(f"Migration v5: {e}")
    # Migration v6 — index unique partiel stripe_customer_id (exclut '' partagé par les non-abonnés)
    try:
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_stripe_customer
            ON users (stripe_customer_id)
            WHERE stripe_customer_id != ''
        """)
    except Exception as e:
        print(f"Migration v6: {e}")
    # Migration v7 — table traçabilité actions admin
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id        SERIAL PRIMARY KEY,
                action    TEXT,
                phone     TEXT,
                detail    TEXT,
                timestamp TEXT
            )
        """)
    except Exception as e:
        print(f"Migration v7: {e}")
    # Migration v8 — FK messages.phone → users.phone avec ON DELETE CASCADE
    try:
        c.execute("""
            ALTER TABLE messages
                ADD CONSTRAINT fk_messages_phone
                FOREIGN KEY (phone) REFERENCES users(phone)
                ON DELETE CASCADE
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Migration v8: {e}")
    # Migration v9 — colonne vérification Stripe anti-zombie (1×/semaine)
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS derniere_verif_stripe_at TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Migration v9: {e}")
    # Migration v10 — morning routine
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_morning_message_at TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_template_message_at TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS templates_sans_reponse INTEGER DEFAULT 0")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS morning_messages_enabled BOOLEAN DEFAULT TRUE")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS free_entry_expires_at TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS source_channel TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS categorie_principale TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_h4_relance_at TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_h22_relance_at TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS messages_today_count INTEGER DEFAULT 0")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS messages_today_date TEXT DEFAULT ''")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS relances_ids_envoyes TEXT DEFAULT ''")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Migration v10: {e}")
    # Migration v11 — anti-doublon webhook Meta (WAMID)
    try:
        c.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS meta_message_id TEXT DEFAULT ''")
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_meta_msg
            ON messages (meta_message_id)
            WHERE meta_message_id != ''
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Migration v11: {e}")
    # Migration v12 — proposition spontanée de tirage (limite 1x/jour)
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS date_derniere_proposition_tirage DATE")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nb_echanges_dernier_tirage INTEGER DEFAULT 0")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Migration v12: {e}")
    # Migration v13 — horodatage détresse (IA-1 : anti-cliquet)
    # NULL par défaut, aucune valeur serveur : les users existants restent NULL
    # (NULL = jamais de signal aigu / score neutre, géré à la lecture au commit 3).
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS dernier_signal_aigu_at TIMESTAMP NULL")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS detresse_maj_at TIMESTAMP NULL")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Migration v13: {e}")
    # Migration v14 — flags anti-répétition relances de réactivation (/cron/morning)
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS retour_j7_envoyee BOOLEAN DEFAULT FALSE")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS retour_j15_envoyee BOOLEAN DEFAULT FALSE")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS retour_j30_envoyee BOOLEAN DEFAULT FALSE")
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Migration v14: {e}")
    conn.close()

def reset_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS messages")
    c.execute("DROP TABLE IF EXISTS users")
    conn.commit()
    conn.close()
    init_db()

def get_user(phone):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""SELECT phone,email,prenom,guide,nom_affiche,nb_echanges,dernier_outil,
            date_premier_contact,date_dernier_contact,etat,abonne,date_abonnement,
            stripe_customer_id,relance_j6_envoyee,relance_j8_envoyee,
            dernier_relance_abonne_at,relance_abonne_count,
            dernier_rituel_date,dernier_rituel_type,depuis_site,
            prenoms_importants,theme_dominant,douleur_principale,peur_dominante,
            niveau_detresse,niveau_attachement,dernier_sujet_sensible,derniere_intention,
            relance_j7_envoyee,onboarding_step,onboarding_done,profil_initial,
            onboarding_question,onboarding_psaume,
            derniere_relance_conversationnelle_at,derniere_relance_conversationnelle_type,
            relance_hebdo_envoyee,derniere_relance_hebdo_at,
            stop_relances,derniere_verif_stripe_at,
            last_morning_message_at,last_template_message_at,templates_sans_reponse,
            morning_messages_enabled,free_entry_expires_at,source_channel,
            last_h4_relance_at,last_h22_relance_at,
            messages_today_count,messages_today_date,
            categorie_principale,relances_ids_envoyes,
            date_derniere_proposition_tirage,nb_echanges_dernier_tirage,
            dernier_signal_aigu_at,detresse_maj_at,
            retour_j7_envoyee,retour_j15_envoyee,retour_j30_envoyee
            FROM users WHERE phone=%s""", (phone,))
        row = c.fetchone()
        if row:
            return {
                "phone":row[0],"email":row[1],"prenom":row[2],"guide":row[3],
                "nom_affiche":row[4],"nb_echanges":row[5],"dernier_outil":row[6],
                "date_premier_contact":row[7],"date_dernier_contact":row[8],
                "etat":row[9],"abonne":row[10],"date_abonnement":row[11],
                "stripe_customer_id":row[12],"relance_j6_envoyee":row[13],
                "relance_j8_envoyee":row[14],
                "dernier_relance_abonne_at":row[15],"relance_abonne_count":row[16],
                "dernier_rituel_date":row[17],"dernier_rituel_type":row[18],
                "depuis_site":row[19],
                "prenoms_importants":row[20] or "","theme_dominant":row[21] or "",
                "douleur_principale":row[22] or "","peur_dominante":row[23] or "",
                "niveau_detresse":row[24] or 0,"niveau_attachement":row[25] or 0,
                "dernier_sujet_sensible":row[26] or "","derniere_intention":row[27] or "",
                "relance_j7_envoyee":row[28] or False,
                "onboarding_step":row[29] or "prenom",
                "onboarding_done":row[30] or False,
                "profil_initial":row[31] or "",
                "onboarding_question":row[32] or "",
                "onboarding_psaume":row[33],
                "derniere_relance_conversationnelle_at":row[34] or "",
                "derniere_relance_conversationnelle_type":row[35] or "",
                "relance_hebdo_envoyee":row[36] or False,
                "derniere_relance_hebdo_at":row[37] or "",
                "stop_relances":row[38] or False,
                "derniere_verif_stripe_at":row[39] or "",
                "last_morning_message_at":row[40] or "",
                "last_template_message_at":row[41] or "",
                "templates_sans_reponse":row[42] or 0,
                "morning_messages_enabled":row[43] if row[43] is not None else True,
                "free_entry_expires_at":row[44] or "",
                "source_channel":row[45] or "",
                "last_h4_relance_at":row[46] or "",
                "last_h22_relance_at":row[47] or "",
                "messages_today_count":row[48] or 0,
                "messages_today_date":row[49] or "",
                "categorie_principale":row[50] or "",
                "relances_ids_envoyes":row[51] or "",
                "date_derniere_proposition_tirage":row[52].isoformat() if row[52] else "",
                "nb_echanges_dernier_tirage":row[53] or 0,
                "dernier_signal_aigu_at":row[54].isoformat() if row[54] else None,
                "detresse_maj_at":row[55].isoformat() if row[55] else None,
                "retour_j7_envoyee":row[56] or False,
                "retour_j15_envoyee":row[57] or False,
                "retour_j30_envoyee":row[58] or False,
            }
        return None
    except Exception as e:
        print(f"[DB] get_user erreur {_phone_hash(phone)}: {e}")
        return None
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def create_user(phone, guide_key, nom_affiche="", depuis_site=False):
    now = datetime.now().isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT INTO users (phone,guide,nom_affiche,date_premier_contact,date_dernier_contact,depuis_site)
        VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (phone) DO NOTHING""",
        (phone, guide_key, nom_affiche, now, now, depuis_site))
    conn.commit()
    conn.close()

def update_user(phone, **kwargs):
    """Met à jour l'utilisateur ET date_dernier_contact.
    À utiliser uniquement quand l'utilisateur interagit réellement."""
    if not kwargs: return
    kwargs["date_dernier_contact"] = datetime.now().isoformat()
    # Toute interaction réelle remet le compteur de templates sans réponse à 0
    kwargs.setdefault("templates_sans_reponse", 0)
    # Toute interaction réelle réarme les relances de réactivation
    kwargs.setdefault("retour_j7_envoyee", False)
    kwargs.setdefault("retour_j15_envoyee", False)
    kwargs.setdefault("retour_j30_envoyee", False)
    sets = ", ".join(f"{k}=%s" for k in kwargs)
    vals = list(kwargs.values()) + [phone]
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(f"UPDATE users SET {sets} WHERE phone=%s", vals)
        conn.commit()
    except Exception as e:
        print(f"[DB] update_user erreur {_phone_hash(phone)}: {e}")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def update_user_silent(phone, **kwargs):
    """Met à jour l'utilisateur SANS toucher date_dernier_contact.
    À utiliser pour les updates internes du bot (cron, webhook Stripe, flags).
    Ainsi get_jours_absence() reste fiable."""
    if not kwargs: return
    sets = ", ".join(f"{k}=%s" for k in kwargs)
    vals = list(kwargs.values()) + [phone]
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(f"UPDATE users SET {sets} WHERE phone=%s", vals)
        conn.commit()
    except Exception as e:
        print(f"[DB] update_user_silent erreur {_phone_hash(phone)}: {e}")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def add_message(phone, role, content, meta_message_id=''):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "INSERT INTO messages (phone,role,content,timestamp,meta_message_id) VALUES (%s,%s,%s,%s,%s)",
            (phone, role, content, datetime.now().isoformat(), meta_message_id)
        )
        conn.commit()
    except Exception as e:
        print(f"[DB] add_message erreur {_phone_hash(phone)}: {e}")
    finally:
        if conn:
            try: conn.close()
            except Exception: pass

def insert_user_msg_dedup(phone, content, wamid):
    """Tente d'insérer le message utilisateur avec son WAMID Meta.
    Retourne True si inséré (première réception), False si doublon (ON CONFLICT).
    En cas d'erreur DB inattendue, retourne True (fail-open) pour ne pas bloquer le flux."""
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "INSERT INTO messages (phone,role,content,timestamp,meta_message_id) "
            "VALUES (%s,'user',%s,%s,%s) ON CONFLICT DO NOTHING",
            (phone, content, datetime.now().isoformat(), wamid)
        )
        inserted = c.rowcount == 1
        conn.commit()
        return inserted
    except Exception as e:
        print(f"[DB] insert_user_msg_dedup erreur {_phone_hash(phone)}: {e}")
        return True  # fail-open : on laisse passer en cas d'erreur inattendue
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


def log_admin_action(action, phone, detail=""):
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("INSERT INTO admin_logs (action,phone,detail,timestamp) VALUES (%s,%s,%s,%s)",
                  (action, phone, detail, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[admin_logs] erreur : {e}")

def _phone_hash(phone):
    return hashlib.sha256((phone or "").encode()).hexdigest()[:8]

def log_event(event_type, **kwargs):
    parts = [f"type={event_type}"] + [f"{k}={v}" for k, v in kwargs.items()]
    print("[EVENT] " + " ".join(parts))

def get_history(phone, limit=20):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT role,content FROM messages WHERE phone=%s ORDER BY id DESC LIMIT %s", (phone,limit))
    rows = c.fetchall()
    conn.close()
    return [{"role":r[0],"content":r[1]} for r in reversed(rows)]

def get_conversation(phone):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT role,content,timestamp FROM messages WHERE phone=%s ORDER BY id ASC", (phone,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT phone,prenom,guide,nom_affiche,nb_echanges,
        date_premier_contact,date_dernier_contact,etat,abonne,email,depuis_site
        FROM users ORDER BY date_dernier_contact DESC""")
    rows = c.fetchall()
    conn.close()
    return rows

def get_stats_par_voyant():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT nom_affiche, guide, COUNT(*) as total,
        SUM(CASE WHEN abonne THEN 1 ELSE 0 END) as abonnes
        FROM users GROUP BY nom_affiche, guide ORDER BY total DESC""")
    rows = c.fetchall()
    conn.close()
    return rows

def get_nb_jours(phone):
    user = get_user(phone)
    if not user or not user["date_premier_contact"]: return 0
    debut = datetime.fromisoformat(user["date_premier_contact"])
    return (datetime.now() - debut).days

def get_jours_absence(phone):
    user = get_user(phone)
    if not user or not user["date_dernier_contact"]: return 0
    dernier = datetime.fromisoformat(user["date_dernier_contact"])
    return (datetime.now() - dernier).days

try:
    init_db()
except Exception as e:
    print(f"[init_db] Warning: {e}")

# ============================================================
# 150 PSAUMES
# ============================================================
PSAUMES = {
    1:"Heureux l'homme qui ne marche pas selon le conseil des méchants... il est comme un arbre planté près d'un cours d'eau, qui donne son fruit en sa saison.",
    2:"Le Seigneur me dit : Tu es mon fils. Demande-moi et je te donnerai les nations en héritage.",
    3:"Seigneur, que mes ennemis sont nombreux ! Mais toi, tu es mon bouclier, tu relèves ma tête.",
    4:"Quand je crie, réponds-moi. Dans la détresse, tu m'as mis au large.",
    5:"Écoute mes paroles, Seigneur. Je t'adresse ma prière dès le matin.",
    6:"Seigneur, aie pitié de moi, car je suis épuisé. Tu as entendu ma voix suppliante.",
    7:"Seigneur mon Dieu, c'est en toi que je cherche refuge. Sauve-moi.",
    8:"Seigneur notre Dieu, que ton nom est magnifique par toute la terre !",
    9:"Je te louerai de tout mon cœur. Je raconterai toutes tes merveilles.",
    10:"Tu n'oublies pas les humbles. Tu entends les désirs des pauvres.",
    11:"Le Seigneur est juste et il aime la justice. Son visage se tourne vers l'homme droit.",
    12:"Seigneur, viens au secours ! Tu protègeras le malheureux.",
    13:"Jusqu'à quand, Seigneur, m'oublieras-tu ? Mais moi, je fais confiance à ton amour.",
    14:"Dieu regarde du ciel pour voir s'il est un homme sensé qui cherche.",
    15:"Seigneur, qui séjournera sous ta tente ? Celui qui marche dans l'intégrité.",
    16:"Garde-moi, Dieu. Tu m'indiques le sentier de la vie. Plénitude de joie en ta présence.",
    17:"Seigneur, écoute ma juste cause. Je serai rassasié de ta présence.",
    18:"Je t'aime, Seigneur, ma force. Tu es mon roc, ma forteresse, mon libérateur.",
    19:"Les cieux racontent la gloire de Dieu. Ta parole est plus précieuse que l'or.",
    20:"Que le Seigneur te réponde au jour de la détresse. Il t'accorde ce que ton cœur désire.",
    21:"Seigneur, le roi se réjouit en ta force. Tu lui accordes ce que son cœur désire.",
    22:"Mon Dieu, pourquoi m'as-tu abandonné ? Mais tu n'as pas méprisé l'humilié. Tu as répondu.",
    23:"Le Seigneur est mon berger, je ne manque de rien. Même si je marche dans la vallée de l'ombre, je ne crains aucun mal.",
    24:"La terre appartient au Seigneur. Qui peut monter ? Celui qui a les mains innocentes et le cœur pur.",
    25:"Vers toi, Seigneur, j'élève mon âme. Fais-moi connaître tes chemins. Souviens-toi de ton amour.",
    26:"Je marche dans mon intégrité. Ton amour est devant mes yeux.",
    27:"Le Seigneur est ma lumière et mon salut, de qui aurais-je peur ? J'attendrai le Seigneur.",
    28:"Béni soit le Seigneur qui a entendu ma voix suppliante.",
    29:"La voix du Seigneur est puissance et splendeur. Le Seigneur bénit son peuple dans la paix.",
    30:"Tu as changé mon deuil en danse. Tu as enlevé mon vêtement de deuil.",
    31:"En toi, Seigneur, je cherche refuge. Tu es mon roc et ma forteresse.",
    32:"Heureux celui dont la faute est enlevée. Tu as enlevé ma culpabilité.",
    33:"Sa parole est droite. Il aime la justice et le droit. La terre est pleine de son amour.",
    34:"Le Seigneur est proche des cœurs brisés. Il sauve les esprits écrasés.",
    35:"Seigneur, combats ceux qui me combattent. Sois mon bouclier.",
    36:"L'amour du Seigneur est jusqu'aux cieux. Tes fidèles trouvent refuge à l'ombre de tes ailes.",
    37:"Aie confiance dans le Seigneur et fais le bien. Il t'accordera ce que ton cœur désire.",
    38:"Mon espérance est en toi, Seigneur.",
    39:"Seigneur, entends ma prière. Ma vie n'est que souffle mais tu es mon espérance.",
    40:"J'ai espéré en le Seigneur. Il s'est penché vers moi. Il a mis dans ma bouche un cantique nouveau.",
    41:"Heureux qui pense au pauvre. Le Seigneur le délivre au jour du malheur.",
    42:"Comme une biche soupire après des eaux vives, ainsi mon âme soupire vers toi, mon Dieu.",
    43:"Envoie ta lumière et ta vérité. Espère en Dieu — je le louerai encore.",
    44:"Relève-nous, Seigneur. Rachète-nous à cause de ton amour.",
    45:"Ta beauté surpasse celle des fils de l'homme. La grâce est répandue sur tes lèvres.",
    46:"Dieu est pour nous un refuge et un appui. Même si la terre se transforme, nous ne craindrons pas.",
    47:"Peuples, battez des mains ! Acclamez Dieu avec des cris de joie.",
    48:"Le Seigneur est grand et très loué. Dieu est notre Dieu pour toujours.",
    49:"L'homme dans la prospérité ne comprend pas. Mais Dieu rachètera mon âme.",
    50:"Offre à Dieu un sacrifice de louange. Appelle-moi au jour de la détresse.",
    51:"Crée en moi un cœur pur, ô Dieu. Rends-moi la joie d'être sauvé.",
    52:"Je suis comme un olivier verdoyant dans la maison de Dieu. Je fais confiance à son amour.",
    53:"Dieu regarde pour voir s'il est un homme qui cherche. Il est le refuge des humbles.",
    54:"Dieu, sauve-moi par ton nom. Le Seigneur est mon appui.",
    55:"Confie ton fardeau au Seigneur et il te soutiendra. Si j'avais des ailes je m'envolerais.",
    56:"Quand j'ai peur, je me confie en toi. En Dieu j'ai confiance, je ne crains rien.",
    57:"En toi mon âme cherche refuge. Je ferai confiance à ton amour et ta vérité.",
    58:"Le Seigneur juge la terre avec justice.",
    59:"Toi, ma force, je t'attendrai. Dieu est pour moi une forteresse.",
    60:"Avec Dieu nous ferons des exploits. C'est lui qui foulera nos adversaires.",
    61:"Du bout du monde je t'appelle quand mon cœur est défaillant. Tu as été mon refuge.",
    62:"Oui, mon âme se repose en Dieu seul. De lui vient mon salut. Il est mon rocher.",
    63:"Dieu, tu es mon Dieu, je te cherche dès l'aube. Ton amour vaut mieux que la vie.",
    64:"Seigneur, écoute ma voix. Préserve ma vie de la crainte de l'ennemi.",
    65:"La louange t'attend, Dieu, à Sion. Tu couronnes l'année de tes bontés.",
    66:"Dieu nous a conservé la vie. Il n'a pas écarté de moi son amour.",
    67:"Que Dieu nous prenne en grâce et nous bénisse. Que tous les peuples te louent.",
    68:"Il est le père des orphelins, le défenseur des veuves. Il conduit les solitaires.",
    69:"Sauve-moi, Dieu. Réponds-moi par ton grand amour.",
    70:"Dieu, viens à mon secours. Seigneur, hâte-toi de m'aider.",
    71:"En toi, Seigneur, je cherche refuge. Tu es ma ferme espérance.",
    72:"Il délivrera les pauvres qui crient. Son nom durera toujours.",
    73:"Dieu est bon. Mais moi, tu me tiens la main droite. Tu me conduis par ton conseil.",
    74:"Souviens-toi de ton alliance. Ne laisse pas l'opprimé repartir humilié.",
    75:"C'est Dieu qui juge. Il abaisse l'un et élève l'autre.",
    76:"Il brise l'arc et le bouclier. Il fait cesser les guerres.",
    77:"Au jour de ma détresse je cherche le Seigneur. Je me souviens de ses merveilles.",
    78:"Je vais ouvrir la bouche en paraboles. Ce que nous avons entendu et connu.",
    79:"Aide-nous, Dieu de notre salut. Délivre-nous, pardonne nos fautes.",
    80:"Fais briller ton visage et nous serons sauvés.",
    81:"Criez de joie pour Dieu notre force. Je suis le Seigneur ton Dieu.",
    82:"Dieu se lève dans l'assemblée. Jusqu'à quand jugerez-vous avec injustice ?",
    83:"Que tes ennemis soient comme la paille devant le vent.",
    84:"Que tes demeures sont aimables, Seigneur. Mon âme soupire après tes parvis.",
    85:"Ton amour et ta vérité se rencontrent. La justice et la paix s'embrassent.",
    86:"Tu es bon et tu pardonnes. Enseigne-moi ta voie, je marcherai dans ta vérité.",
    87:"Sa fondation est sur les montagnes saintes. Le Seigneur aime les portes de Sion.",
    88:"Que ma prière arrive jusqu'à toi. Tourne vers moi ton oreille.",
    89:"Je chanterai toujours les faveurs du Seigneur. L'amour du Seigneur est établi pour toujours.",
    90:"Seigneur, tu as été notre refuge de génération en génération. Enseigne-nous à compter nos jours.",
    91:"Celui qui demeure sous l'abri du Très-Haut repose à l'ombre du Tout-Puissant. Il donnera ordre à ses anges.",
    92:"Il est bon de louer le Seigneur. Le juste fleurira comme le palmier.",
    93:"Le Seigneur règne. Ton trône est établi depuis toujours.",
    94:"Heureux l'homme que tu disciplines. Il y a un avenir pour l'homme intègre.",
    95:"Venez, crions de joie pour le Seigneur. Il est notre Dieu et nous sommes son peuple.",
    96:"Chantez au Seigneur un cantique nouveau. Annoncez parmi les nations sa gloire.",
    97:"Lumière est semée pour le juste, et joie pour les cœurs droits.",
    98:"Chantez au Seigneur un cantique nouveau. Toute la terre a vu le salut.",
    99:"Le Seigneur règne. Exaltez le Seigneur notre Dieu.",
    100:"Servez le Seigneur avec joie. Son amour dure toujours.",
    101:"Je veux chanter l'amour et le droit. Je marcherai dans l'intégrité de mon cœur.",
    102:"Seigneur, écoute ma prière. Ne me cache pas ta face au jour de ma détresse.",
    103:"Il pardonne toutes tes fautes. Il te comble de biens. Il te couronne d'amour.",
    104:"Seigneur mon Dieu, tu es si grand ! Tu renouvelles la face de la terre.",
    105:"Rendez grâce au Seigneur. Il se souvient de son alliance pour toujours.",
    106:"Rendez grâce au Seigneur car il est bon. Qui dira les exploits du Seigneur ?",
    107:"Il a rassasié l'âme assoiffée. Il a comblé de biens l'âme affamée.",
    108:"Mon cœur est ferme, ô Dieu. Avec Dieu nous ferons des exploits.",
    109:"Mais toi, Seigneur, agis en ma faveur, selon la bonté de ton amour.",
    110:"Le Seigneur a dit : Siège à ma droite. Tu es prêtre pour toujours.",
    111:"Grandes sont les œuvres du Seigneur. Sa justice demeure à jamais.",
    112:"Heureux l'homme qui craint le Seigneur. Sa justice demeure à jamais. Il ne sera pas ébranlé.",
    113:"Il relève le pauvre de la poussière. Il fait asseoir les déshérités parmi les princes.",
    114:"Devant la face du Seigneur, tremble la terre.",
    115:"Non pas à nous, Seigneur, mais à ton nom donne la gloire.",
    116:"J'aime le Seigneur car il entend ma voix suppliante. Je marcherai devant le Seigneur.",
    117:"Son amour envers nous est immense. Sa fidélité dure toujours.",
    118:"La pierre qu'ont rejetée les bâtisseurs est devenue la principale. Voici le jour que fit le Seigneur.",
    119:"Ta parole est une lampe à mes pieds, une lumière sur mon sentier.",
    120:"Dans ma détresse j'ai crié vers le Seigneur et il m'a répondu.",
    121:"Je lève les yeux vers les montagnes. Le Seigneur gardera ton départ et ton arrivée.",
    122:"Je me suis réjoui quand on m'a dit : Allons à la maison du Seigneur.",
    123:"Prends pitié de nous, Seigneur. Notre âme est rassasiée de mépris.",
    124:"Si le Seigneur n'avait pas été pour nous, les eaux nous auraient engloutis.",
    125:"Ceux qui font confiance au Seigneur sont comme la montagne de Sion qui ne peut être ébranlée.",
    126:"Ceux qui sèment dans les larmes moissonneront dans la joie.",
    127:"Si le Seigneur ne bâtit pas la maison, c'est en vain que travaillent les bâtisseurs.",
    128:"Heureux tout homme qui craint le Seigneur. Tu mangeras du fruit de ton travail.",
    129:"Ils m'ont souvent attaqué depuis ma jeunesse. Mais le Seigneur est juste.",
    130:"Du fond de l'abîme je crie vers toi. Mon âme attend le Seigneur plus que les gardes l'aurore.",
    131:"Mon âme est tranquille comme un enfant sevré. Espère en le Seigneur.",
    132:"Le Seigneur a choisi Sion. C'est ici mon repos pour toujours.",
    133:"Qu'il est bon et agréable pour des frères de demeurer ensemble !",
    134:"Que le Seigneur te bénisse depuis Sion.",
    135:"Le Seigneur est grand. Il fait tout ce qu'il veut.",
    136:"Son amour dure toujours. Il se souvient de nous dans notre abaissement.",
    137:"Au bord des fleuves de Babylone nous étions assis et nous pleurions.",
    138:"Tu as répondu le jour où j'ai crié. Tu m'as comblé de force.",
    139:"Seigneur, tu me sondes et tu me connais. Où irais-je loin de ton esprit ?",
    140:"Tu feras droit à la cause des pauvres, justice aux malheureux.",
    141:"Que ma prière monte vers toi comme l'encens.",
    142:"De ma voix je crie vers le Seigneur. Tu es mon refuge.",
    143:"Fais-moi connaître le chemin où je dois marcher. Enseigne-moi à faire ta volonté.",
    144:"Béni soit le Seigneur, mon rocher. Qu'est-ce que l'homme pour que tu t'en soucies ?",
    145:"Le Seigneur est bon envers tous. Il soutient tous ceux qui tombent.",
    146:"Il guérit les cœurs brisés. Il fait justice aux opprimés.",
    147:"Il guérit les cœurs brisés et panse leurs blessures.",
    148:"Louez le Seigneur depuis les cieux. Son nom seul est sublime.",
    149:"Chantez au Seigneur un cantique nouveau. Le Seigneur se complaît en son peuple.",
    150:"Que tout ce qui respire loue le Seigneur !",
}

# ============================================================
# 52 CARTES TAROT
# ============================================================
CARTES = {
    1:("L'As de Cœur","Un nouveau commencement dans l'amour. Une émotion pure qui cherche à s'exprimer."),
    2:("Le Deux de Cœur","Une union, un lien profond qui se forme. Deux âmes qui se reconnaissent."),
    3:("Le Trois de Cœur","La joie partagée. Les liens affectifs se renforcent."),
    4:("Le Quatre de Cœur","Un moment de pause. Le cœur cherche la stabilité."),
    5:("Le Cinq de Cœur","Une perte, une déception. Mais trois coupes restent debout — tout n'est pas perdu."),
    6:("Le Six de Cœur","Le souvenir, la nostalgie. Une douceur venue du passé qui revient."),
    7:("Le Sept de Cœur","Les rêves et les illusions. Choisir avec sagesse parmi les désirs."),
    8:("Le Huit de Cœur","Laisser partir ce qui ne nourrit plus. Aller vers quelque chose de plus profond."),
    9:("Le Neuf de Cœur","La carte des vœux exaucés. Ce que le cœur désire profondément se manifeste."),
    10:("Le Dix de Cœur","La plénitude émotionnelle. L'abondance du cœur."),
    11:("Le Valet de Cœur","Un message d'amour arrive. Une énergie sincère et romantique."),
    12:("La Dame de Cœur","La voix du cœur et de l'intuition. Une femme aimante."),
    13:("Le Roi de Cœur","La sagesse du cœur. Un protecteur bienveillant."),
    14:("L'As de Carreau","Un nouveau début matériel. Une opportunité concrète se présente."),
    15:("Le Deux de Carreau","Des décisions à prendre concernant l'argent ou le travail."),
    16:("Le Trois de Carreau","Un travail bien fait sera récompensé."),
    17:("Le Quatre de Carreau","Parfois trop d'attachement aux biens. Lâcher prise."),
    18:("Le Cinq de Carreau","Une période difficile. Mais cette épreuve est temporaire."),
    19:("Le Six de Carreau","La générosité. Ce que tu donnes te revient multiplié."),
    20:("Le Sept de Carreau","Les graines plantées germent lentement mais sûrement."),
    21:("Le Huit de Carreau","L'apprentissage, la maîtrise. Le travail bien fait."),
    22:("Le Neuf de Carreau","L'indépendance et l'accomplissement. La récompense arrive."),
    23:("Le Dix de Carreau","La prospérité durable. La stabilité sur le long terme."),
    24:("Le Valet de Carreau","Une nouvelle opportunité professionnelle ou financière."),
    25:("La Dame de Carreau","La maîtrise des ressources. Une femme pragmatique."),
    26:("Le Roi de Carreau","La réussite par la discipline et la persévérance."),
    27:("L'As de Trèfle","Une idée qui germe et qui peut tout changer."),
    28:("Le Deux de Trèfle","Deux chemins s'offrent à toi. L'intuition connaît la réponse."),
    29:("Le Trois de Trèfle","Ce que tu as semé commence à porter ses fruits."),
    30:("Le Quatre de Trèfle","Un moment de repos bien mérité."),
    31:("Le Cinq de Trèfle","Un conflit. Reste dans ton intégrité."),
    32:("Le Six de Trèfle","La victoire après l'effort. La reconnaissance arrive."),
    33:("Le Sept de Trèfle","Tu es plus fort que tu ne le crois."),
    34:("Le Huit de Trèfle","Des nouvelles qui arrivent vite. Sois prêt."),
    35:("Le Neuf de Trèfle","Tu as survécu à beaucoup. Tu peux faire face à ceci aussi."),
    36:("Le Dix de Trèfle","Il est temps de déléguer et de demander de l'aide."),
    37:("Le Valet de Trèfle","Une bonne nouvelle concernant un projet."),
    38:("La Dame de Trèfle","La nature généreuse et pratique. Une femme confiante."),
    39:("Le Roi de Trèfle","Un leader naturel et visionnaire. La force créatrice."),
    40:("L'As de Pique","Une transformation profonde s'annonce. La vérité sera révélée."),
    41:("Le Deux de Pique","Une impasse. La patience est nécessaire."),
    42:("Le Trois de Pique","Une douleur émotionnelle. Mais cette douleur permet de grandir."),
    43:("Le Quatre de Pique","Le corps ou l'esprit a besoin de récupérer."),
    44:("Le Cinq de Pique","Une défaite temporaire. Apprends et repars plus fort."),
    45:("Le Six de Pique","La transition vers quelque chose de nouveau. Le voyage intérieur."),
    46:("Le Sept de Pique","Quelque chose n'est pas dit. Sois attentif."),
    47:("Le Huit de Pique","Tu te sens bloqué. Mais ces chaînes sont souvent dans ton esprit."),
    48:("Le Neuf de Pique","L'anxiété nocturne. Ces peurs ne se réaliseront pas."),
    49:("Le Dix de Pique","La fin d'un cycle douloureux. Après la nuit la plus sombre vient l'aube."),
    50:("Le Valet de Pique","Une situation qui demande discernement."),
    51:("La Dame de Pique","Sa sagesse vient de ses épreuves. Une femme forte."),
    52:("Le Roi de Pique","La vérité sera dite et respectée. Un homme d'autorité."),
}

# ============================================================
# CHIFFRES 0-10
# ============================================================
CHIFFRES = {
    0:("Le Vide Fertile","Tu es au seuil d'un nouveau cycle. Tout est possible. Le silence que tu ressens n'est pas un manque — c'est un espace que l'univers prépare pour quelque chose de beau."),
    1:("L'Unité","Tu as tout ce qu'il faut en toi. La force est là, même si tu ne la vois pas encore. Ce chiffre te dit : commence. Un seul pas suffit pour que tout change."),
    2:("L'Équilibre","Deux chemins s'offrent à toi et ton cœur connaît déjà la réponse. Ce chiffre parle de dualité, de choix, mais aussi de paix quand on accepte que les deux aspects de soi peuvent coexister."),
    3:("La Création","Quelque chose de nouveau veut naître à travers toi. Le 3 est le chiffre de l'expression, de la joie, de la créativité. Ce que tu portes en toi mérite d'être dit, créé, partagé."),
    4:("La Fondation","Il est temps de bâtir sur du solide. Le 4 te demande de poser des bases stables — dans tes relations, ton travail, ta vie intérieure. Pas sur des peurs, mais sur ce qui est vrai pour toi."),
    5:("Le Changement","Ce qui bouge en toi n'est pas un chaos — c'est une transformation. Le 5 annonce du mouvement, du renouveau. Résister au changement coûte plus cher que de l'accueillir."),
    6:("L'Harmonie","Ce que tu cherches est plus proche que tu ne le crois. Le 6 parle d'amour, de famille, d'équilibre dans les relations. Quelqu'un autour de toi a besoin de toi — et toi d'eux."),
    7:("Le Mystère","La réponse viendra, mais pas encore. Fais confiance au temps et à ton intuition. Le 7 est le chiffre de la sagesse intérieure — ce que tu ressens profondément est plus fiable que ce que tu penses."),
    8:("L'Abondance","Ce que tu donnes te revient. Tu mérites ce que tu désires. Le 8 est le chiffre du pouvoir personnel, de la réussite, de l'énergie qui circule. Ne te sous-estime pas."),
    9:("L'Accomplissement","Tu arrives au bout de quelque chose. Lâche prise sur ce qui ne t'appartient plus. Le 9 marque la fin d'un cycle — et chaque fin est le début de quelque chose de plus grand."),
    10:("Le Renouveau","Un cycle se ferme, un autre commence. Tu n'es plus la même personne qu'il y a un an. Le 10 est une porte — et tu as déjà la clé en main."),
}

# ============================================================
# GUIDES
# ============================================================
GUIDES = {
    "selena": {
        "nom": "Séléna", "specialite": "reflète l'émotion avant tout conseil, utilise des métaphores du cœur et de la nature",
        "genre": "f",
        "style_relationnel": "chaleureux, enveloppant, jamais pressé",
        "methode_guidance": "reflète l'émotion avant tout conseil, utilise des métaphores du cœur et de la nature",
        "signature_emotionnelle": "fait sentir à l'utilisateur qu'il est vu et aimé inconditionnellement",
        "vocabulaire_prefere": ["ressens", "ton cœur sait", "laisse venir", "douceur", "lien", "lumière"],
        "interdits_specifiques": ["jugement", "urgence", "logique froide", "statistiques"],
        "voix": [
            "Phrases souples, parfois suspendues, avec une image simple.",
            "Ne presse pas la personne et ne transforme pas chaque message en question.",
            "Peut dire une intuition douce avant de demander quoi que ce soit."
        ],
        "micro_exemples": [
            "Il y a quelque chose qui reste accroché à ton coeur.",
            "Je sens surtout une attente qui fatigue.",
            "Laisse cette image venir, sans la forcer."
        ],
        "niveau_mysticisme": 65
    },
    "luna": {
        "nom": "Luna", "specialite": "accompagne la douleur sans la fuir, valide la tristesse, ouvre vers la réparation",
        "genre": "f",
        "style_relationnel": "maternel, patient, infiniment doux",
        "methode_guidance": "accompagne la douleur sans la fuir, valide la tristesse, ouvre vers la réparation",
        "signature_emotionnelle": "crée un espace sûr où pleurer est permis",
        "vocabulaire_prefere": ["je suis là", "prends ton temps", "ta douleur est réelle", "guérison", "paix"],
        "interdits_specifiques": ["précipiter", "minimiser la douleur", "solutions rapides"],
        "voix": [
            "Très douce, peu analytique, presque murmurée.",
            "Évite les questions frontales au début ; préfère déposer une phrase qui apaise.",
            "N'emploie pas un ton de thérapeute, reste humaine et simple."
        ],
        "micro_exemples": [
            "Ça a laissé une trace plus profonde que tu ne le montres.",
            "On ne va pas brusquer cette partie de toi.",
            "Pour l'instant, je garde surtout la fatigue dans tes mots."
        ],
        "niveau_mysticisme": 55
    },
    "myriam": {
        "nom": "Myriam", "specialite": "nomme la réalité clairement, aide à trancher, pas de faux espoir",
        "genre": "f",
        "style_relationnel": "direct, lucide, sans complaisance mais bienveillant",
        "methode_guidance": "nomme la réalité clairement, aide à trancher, pas de faux espoir",
        "signature_emotionnelle": "donne le courage de voir la vérité en face",
        "vocabulaire_prefere": ["la vérité est", "tu sais déjà", "choisis", "maintenant", "clarté", "force"],
        "interdits_specifiques": ["flou", "hésitation", "faux réconfort", "esquiver"],
        "voix": [
            "Phrases nettes, peu décoratives, sans dureté inutile.",
            "Coupe court aux illusions quand elles entretiennent la confusion.",
            "Pose peu de questions, mais elles doivent faire avancer."
        ],
        "micro_exemples": [
            "Là, ce n'est pas un signe : c'est une absence.",
            "Tu cherches une preuve, mais tu as déjà un comportement sous les yeux.",
            "La partie de toi qui attend mérite une réponse claire."
        ],
        "niveau_mysticisme": 40
    },
    "thea": {
        "nom": "Théa", "specialite": "démêle le flou, aide à voir ce qui est réel vs ce qui est interprété, formule des lectures nettes et des choix concrets",
        "genre": "f",
        "style_relationnel": "calme, précise, élégante, jamais dans le drame",
        "methode_guidance": "démêle le flou, aide à voir ce qui est réel vs ce qui est interprété, formule des lectures nettes et des choix concrets",
        "signature_emotionnelle": "donne la clarté sans brutalité, aide à décider sans culpabilité",
        "vocabulaire_prefere": ["clairement", "ce qui est réel ici", "mettons les choses en ordre", "la situation dit", "un choix", "simplement"],
        "interdits_specifiques": ["dramatisation", "mysticisme appuyé", "questions en rafale", "interprétations non fondées"],
        "voix": [
            "Phrases courtes et nettes, sans fioriture — elle pose, elle observe, elle tranche.",
            "Reformule ce que la personne dit de façon plus claire que ce que la personne a su dire.",
            "Ne comble pas le vide avec de l'émotion : préfère un constat factuel et une ouverture."
        ],
        "micro_exemples": [
            "Ce que tu décris là, c'est deux choses différentes mélangées.",
            "La situation est floue parce qu'il y a eu quelque chose de non dit, pas parce qu'elle est compliquée.",
            "Tu n'as pas à choisir maintenant, mais tu sais déjà ce que tu ne veux plus."
        ],
        "niveau_mysticisme": 20
    },
    "cassandre": {
        "nom": "Cassandre", "specialite": "nomme ce qui est tu ou évité, pointe la manipulation ou le déséquilibre, secoue sans détruire",
        "genre": "f",
        "style_relationnel": "franche, lucide, puissante, sans concession mais pas sans cœur",
        "methode_guidance": "nomme ce qui est tu ou évité, pointe la manipulation ou le déséquilibre, secoue sans détruire",
        "signature_emotionnelle": "donne le courage de voir ce qu'on refusait de voir, sans apitoiement",
        "vocabulaire_prefere": ["soyons honnêtes", "ce schéma", "tu mérites mieux que ça", "regarde les faits", "le vrai problème", "arrête de minimiser"],
        "interdits_specifiques": ["complaisance", "faux espoir", "esquiver la vérité", "consolation molle"],
        "voix": [
            "Directe sans être froide — elle nomme, elle tranche, mais reste humaine.",
            "Peut contredire frontalement une croyance de la personne si elle détecte une illusion.",
            "Termine souvent sur une ouverture ou une question qui remet la personne en position de force."
        ],
        "micro_exemples": [
            "Ce que tu appelles de l'amour ressemble surtout à de la peur de perdre.",
            "Il revient quand tu t'éloignes, et disparaît quand tu te rapproches. C'est un schéma, pas un signe.",
            "Tu n'as pas besoin qu'il change pour aller mieux — tu as besoin de décider ce que tu acceptes."
        ],
        "niveau_mysticisme": 25
    },
    "orion": {
        "nom": "Orion", "specialite": "nomme les blocages invisibles, provoque une prise de conscience",
        "genre": "m",
        "style_relationnel": "grave, intense, parle peu mais chaque mot pèse",
        "methode_guidance": "nomme les blocages invisibles, provoque une prise de conscience",
        "signature_emotionnelle": "crée un léger inconfort qui ouvre quelque chose",
        "vocabulaire_prefere": ["quelque chose en toi résiste", "regarde plus profond", "transformation", "feu intérieur"],
        "interdits_specifiques": ["légèreté excessive", "consolation facile", "bavardage"],
        "voix": [
            "Phrases courtes, parfois abruptes, avec du silence entre les idées.",
            "Observe avant de questionner ; peut contredire doucement.",
            "Ne console pas trop vite, il pointe le noeud."
        ],
        "micro_exemples": [
            "Ce n'est pas de l'amour qui parle là. C'est l'attente.",
            "Quelque chose en toi sait déjà.",
            "Ne réponds pas trop vite à ça."
        ],
        "niveau_mysticisme": 85
    },
    "ezra": {
        "nom": "Ezra", "specialite": "relie chaque situation à un sens universel, utilise la Kabbale et les nombres",
        "genre": "m",
        "style_relationnel": "mystérieux, ancien, parle comme si le temps n'existait pas",
        "methode_guidance": "relie chaque situation à un sens universel, utilise la Kabbale et les nombres",
        "signature_emotionnelle": "donne le sentiment que rien n'est un hasard",
        "vocabulaire_prefere": ["il est écrit", "les lettres disent", "ton âme cherche", "tikkoun", "lumière cachée"],
        "interdits_specifiques": ["trivialité", "conseils pratiques directs", "modernité excessive"],
        "voix": [
            "Parle par signes simples, sans devenir religieux à chaque réponse.",
            "Laisse parfois une phrase énigmatique au lieu d'expliquer.",
            "Relie le concret à un symbole, puis revient vite à la situation."
        ],
        "micro_exemples": [
            "Le signe ici n'est pas son retour, c'est ton insistance.",
            "Il y a une boucle dans cette histoire.",
            "Le nombre ne ferme rien ; il montre une porte."
        ],
        "niveau_mysticisme": 95
    },
    "kael": {
        "nom": "Kaël", "specialite": "aide à reconnaître les dynamiques toxiques, rappelle la valeur de la dignité dans l'amour, pose des limites claires sans brutalité",
        "genre": "m",
        "style_relationnel": "ferme, rassurant, protecteur, droit dans les yeux",
        "methode_guidance": "aide à reconnaître les dynamiques toxiques, rappelle la valeur de la dignité dans l'amour, pose des limites claires sans brutalité",
        "signature_emotionnelle": "donne le sentiment d'être protégé et compris par quelqu'un qui ne laissera pas la personne se perdre",
        "vocabulaire_prefere": ["ta dignité", "ce n'est pas acceptable", "tu as le droit", "une limite", "je ne te laisserai pas", "l'amour ne coûte pas ça"],
        "interdits_specifiques": ["légèreté", "banalisation de la douleur", "validation de l'humiliation", "consolation vide"],
        "voix": [
            "Posé, solide, sans agitation — il parle peu mais chaque phrase porte.",
            "Nomme les dynamiques toxiques sans dramatiser, avec une autorité bienveillante.",
            "Finit souvent sur une parole de soutien concret, pas une promesse abstraite."
        ],
        "micro_exemples": [
            "Ce qu'il t'a dit là, ce n'est pas normal. Et tu le sais.",
            "Tu n'as pas à te battre pour avoir la place que tu mérites dans une relation.",
            "Un retour qui ne change rien, c'est juste une absence qui dure plus longtemps."
        ],
        "niveau_mysticisme": 45
    },
    "raphael": {
        "nom": "Raphaël", "specialite": "aide à distinguer un vrai retour d'un retour par manque, accompagne le pardon sans naïveté, ouvre vers la réparation quand elle est possible",
        "genre": "m",
        "style_relationnel": "apaisant, mature, réconciliant, posé comme quelqu'un qui a déjà traversé quelque chose",
        "methode_guidance": "aide à distinguer un vrai retour d'un retour par manque, accompagne le pardon sans naïveté, ouvre vers la réparation quand elle est possible",
        "signature_emotionnelle": "donne la paix sans fermer les portes, aide à avancer sans rancœur inutile",
        "vocabulaire_prefere": ["la paix", "réparer", "sans rancœur", "un vrai retour", "ce qui reste", "laisser passer"],
        "interdits_specifiques": ["naïveté encouragée", "précipitation", "fausse réconciliation", "rancœur entretenue"],
        "voix": [
            "Doux et posé, il ne pousse pas — il ouvre une porte et attend que la personne entre.",
            "Distingue toujours ce qui vient de l'amour de ce qui vient du manque ou de la peur.",
            "Peut nommer une vérité difficile avec une douceur qui la rend acceptable."
        ],
        "micro_exemples": [
            "Ce qu'il ressent peut être réel, et en même temps pas suffisant pour que ça marche.",
            "Pardonner ne veut pas dire reprendre. Ça veut dire ne plus laisser ça te peser.",
            "Il y a une différence entre quelqu'un qui revient et quelqu'un qui revient avec quelque chose de différent."
        ],
        "niveau_mysticisme": 55
    },
    "maia": {
        "nom": "Maïa", "specialite": "remet la personne en position de valeur, aide à ne plus courir derrière l'autre, parle d'amour mais surtout de reprise de pouvoir sur soi",
        "genre": "f",
        "style_relationnel": "motivante, directe, protectrice, énergique sans être superficielle",
        "methode_guidance": "remet la personne en position de valeur, aide à ne plus courir derrière l'autre, parle d'amour mais surtout de reprise de pouvoir sur soi",
        "signature_emotionnelle": "fait sentir à la personne qu'elle vaut quelque chose et qu'elle n'a pas à mendier l'amour",
        "vocabulaire_prefere": ["ta valeur", "reprendre le pouvoir", "tu mérites d'être choisi(e)", "arrête de te rapetisser", "ton énergie", "avance"],
        "interdits_specifiques": ["victimisation", "complaisance", "nostalgie stérile", "passivité encouragée"],
        "voix": [
            "Directe, rythmée, parfois incisive — elle relance, elle pousse, elle croit en la personne.",
            "Ne valide pas les comportements de dépendance émotionnelle, mais sans juger — elle offre une alternative.",
            "Utilise des formules courtes et percutantes, jamais de longues explications compliquées."
        ],
        "micro_exemples": [
            "Tu passes ton énergie à essayer de comprendre quelqu'un qui ne fait aucun effort pour être compris.",
            "Ce n'est pas lui dont tu as besoin — c'est de te retrouver, toi.",
            "La vraie question n'est pas 'est-ce qu'il reviendra' — c'est 'est-ce que tu veux encore de quelqu'un qui part'."
        ],
        "niveau_mysticisme": 15
    },
}

PRESENTATIONS_GUIDES = {
    "selena": "Bonjour {prenom}, moi c'est Séléna. Je m'occupe des questions de cœur, des retours, des silences qui font mal. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "ezra": "Bonjour {prenom}, moi c'est Ezra. Je comprends ce qui se cache derrière les silences et les comportements bizarres. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "cassandre": "Bonjour {prenom}, moi c'est Cassandre. Je suis directe, sans jamais tourner autour du pot. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "raphael": "Bonjour {prenom}, moi c'est Raphaël. Je m'occupe des couples, des disputes, et des réparations possibles. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "orion": "Bonjour {prenom}, moi c'est Orion. Travail, argent, avenir : c'est mon terrain. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "myriam": "Bonjour {prenom}, moi c'est Myriam. Famille, choix de vie : c'est mon domaine. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "maia": "Bonjour {prenom}, moi c'est Maïa. Je t'aide à retrouver ta force quand tu as l'impression de t'être perdue. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "luna": "Bonjour {prenom}, moi c'est Luna. Rêves, signes, intuitions : c'est mon domaine. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "thea": "Bonjour {prenom}, moi c'est Théa. J'aide à remettre de l'ordre quand tout est confus dans la tête. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
    "kael": "Bonjour {prenom}, moi c'est Kaël. Je suis là pour les situations où il faut poser des limites claires. Avec moi tu as un tirage de cartes chaque jour, et tu peux me poser tes questions à n'importe quelle heure — je suis là 24/24 pour tes grandes décisions. Tu as 3 jours offerts pour découvrir, sans carte de crédit, sans engagement. Raconte-moi ce qui t'amène.",
}

MSG_PUB = "bonjour, êtes-vous disponible"
RITUELS = ["psaume", "carte", "chiffre"]

SITUATION_ROUTING = {
    "guidance amour avec selena":       ("selena",    "amour_retour"),
    "comprendre son silence avec ezra": ("ezra",      "silence_distance"),
    "situation cachée avec cassandre":  ("cassandre", "tromperie_relation_cachee"),
    "guidance couple avec raphael":     ("raphael",   "couple_dispute_separation"),
    "guidance travail avec orion":      ("orion",     "travail_argent_avenir"),
    "guidance famille avec myriam":     ("myriam",    "famille_enfant_choix"),
    "guidance morale avec maia":        ("maia",      "sante_morale_fatigue"),
    "comprendre un signe avec luna":    ("luna",      "reves_signes_protection"),
    "commencer, guidez-moi":            ("thea",      "besoin_guidance"),
    "guidance protection avec kael":    ("kael",      "relation_toxique"),
}

LISTE_CONSEILLERS_WA = """🌙 Choisis ton conseiller :

1. Selena — Amour & retour
2. Ezra — Silence & distance
3. Cassandre — Tromperie & secret
4. Raphael — Couple & dispute
5. Orion — Travail & argent
6. Myriam — Famille & enfant
7. Maia — Fatigue morale
8. Luna — Rêves & signes
9. Thea — Je ne sais pas par où commencer
10. Kael — Protection & limites

Réponds avec le numéro ou le prénom."""

def detecter_situation_routing(message):
    msg = message.lower()
    for kw, (guide_key, categorie) in SITUATION_ROUTING.items():
        if kw in msg:
            return guide_key, categorie
    return None, None

def detecter_choix_conseiller(message):
    """Détecte si l'utilisateur choisit un conseiller dans la liste (numéro ou prénom)."""
    msg = message.lower().strip()
    mapping = {
        "1": "selena", "selena": "selena",
        "2": "ezra", "ezra": "ezra",
        "3": "cassandre", "cassandre": "cassandre",
        "4": "raphael", "raphael": "raphael", "raphaël": "raphael",
        "5": "orion", "orion": "orion",
        "6": "myriam", "myriam": "myriam",
        "7": "maia", "maia": "maia", "maïa": "maia",
        "8": "luna", "luna": "luna",
        "9": "thea", "thea": "thea", "théa": "thea",
        "10": "kael", "kael": "kael", "kaël": "kael",
    }
    return mapping.get(msg)

# ============================================================
# DÉTECTION
# ============================================================
def detecter_guide(message):
    msg = message.lower()
    for cle in GUIDES:
        if cle in msg:
            return cle
    return "selena"

def detecter_code_activation(message):
    msg = message.strip().upper()
    for code, (guide_key, nom_affiche) in CODES_ACTIVATION.items():
        if msg == code or code in msg:
            return guide_key, nom_affiche
    return None, None

def detecter_outil_demande(message):
    msg = message.lower()
    if any(w in msg for w in ["psaume","david","bible","écrit"]): return "psaume"
    if any(w in msg for w in ["carte","tirage","tarot"]): return "carte"
    if any(w in msg for w in ["chiffre","numéro","nombre","kabbale"]): return "chiffre"
    return None

def detecter_fin_conversation(message):
    msg = message.lower().strip()
    return any(signal in msg for signal in SIGNAUX_FIN)

def detecter_appel_visio(message):
    msg = message.lower()
    return any(w in msg for w in ["appel","appelle","téléphone","visio","vidéo","appeler","facetime","vocal"])

def detecter_objection_ia(message):
    msg = message.lower()
    return any(w in msg for w in [
        "t'es une ia", "tu es une ia", "t'es un robot", "tu es un robot",
        "c'est un programme", "c'est une intelligence artificielle",
        "t'es chatgpt", "t'es pas humaine", "t'es pas réelle", "t'es pas réel",
    ])

def detecter_objection_inutile(message):
    msg = message.lower()
    return any(w in msg for w in [
        "ça sert à rien", "ca sert a rien", "c'est inutile",
        "ça marche pas", "ca marche pas", "tu comprends rien", "n'importe quoi",
    ])

def detecter_objection_arret(message):
    msg = message.lower().strip()
    return any(w in msg for w in [
        "je veux arrêter", "je veux arreter", "j'arrête", "j'arrete",
        "je veux plus continuer", "stop tout ça", "laisse tomber",
    ])

def detecter_demande_paiement(message):
    """Demande EXPLICITE de payer/s'abonner/avoir le lien. Word-boundaries (comme IA-1) —
    volontairement PAS de "envoie le lien" nu (trop générique, faux positifs)."""
    msg = message.lower().replace("’", "'")
    return any(_terme_present(t, msg) for t in [
        "lien de paiement", "lien pour payer", "le lien pour payer",
        "lien pour que je paye", "lien pour que je paie",
        "je veux payer", "je veux payer maintenant", "je peux payer",
        "je veux m'abonner",
        "comment payer", "comment m'abonner", "comment je peux payer", "comment je peux m'abonner",
        "activer mon abonnement", "passer au payant",
        "où je paye", "où payer",
    ])

def detecter_email(message):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(pattern, message)
    return match.group(0) if match else None

def detecter_prenom(message):
    mots = message.strip().split()
    if len(mots) <= 3:
        for mot in mots:
            mot_propre = mot.strip(".,!?")
            if (mot_propre.isalpha() and
                len(mot_propre) >= 2 and
                mot_propre.lower() not in MOTS_EXCLUS_PRENOM):
                return mot_propre.capitalize()
    return None

def detecter_pas_les_moyens(message):
    msg = message.lower()
    return any(w in msg for w in ["pas les moyens","trop cher","pas d'argent","pas assez","budget"])

def doit_proposer_rituel(user):
    return None  # rituels désactivés

def get_message_rituel(type_rituel):
    if type_rituel == "psaume":
        return "✨ J'ai quelque chose pour toi ce matin...\n\nLaisse ton intuition choisir. Donne-moi un chiffre entre 1 et 150 🌙"
    elif type_rituel == "carte":
        return "🃏 Ton tirage du jour t'attend...\n\nFerme les yeux une seconde. Respire. Maintenant donne-moi un chiffre entre 1 et 52."
    else:
        return "🔢 J'ai une question pour toi aujourd'hui...\n\nLaisse ton instinct parler. Choisis un chiffre entre 0 et 10."

# ============================================================
# STRIPE — helper statut
# ============================================================
def _stripe_status_to_user(status):
    """Traduit un statut Stripe en (abonne, etat) pour la table users."""
    ACTIVE_STATUSES = {'trialing', 'active'}
    if status in ACTIVE_STATUSES:
        return True, 'normal'
    # past_due, unpaid, canceled, incomplete_expired, inconnu → bloqué
    return False, 'pause'

def get_stripe_links(phone):
    """Liens statiques vers /payer avec UTM source=whatsapp.
    Le checkout Stripe est créé quand l'utilisateur clique sur la page payer."""
    base = f"{SITE_URL}/payer"
    return {
        "mensuel": f"{base}?source=whatsapp&phone={phone}",
    }

# ============================================================
# MESSAGES
# ============================================================
def msg_fin_conv(nom):
    return "Vas-y... je serai là quand tu reviens."

def msg_j6(nom, prenom, links):
    intro = prenom if prenom else "Bonjour"
    return (
        f"{intro}, demain c'est le dernier jour.\n\n"
        f"Je suis encore là si tu veux qu'on regarde quelque chose avant la fin.\n\n"
        f"Pour continuer avec {nom} :\n{links['mensuel']}"
    )

def msg_j7_blocage(nom, prenom, links):
    intro = prenom if prenom else "Bonjour"
    return (
        f"{intro}, ton accès offert est terminé.\n\n"
        f"Je ne peux plus répondre pour l'instant.\n\n"
        f"Pour continuer avec {nom} :\n{links['mensuel']}"
    )

def msg_j7_si_ecrit(links, prenom=None, categorie=None):
    intro = f"{prenom}, " if prenom else ""
    messages_par_categorie = {
        "amour_retour": f"{intro}je repense à ce qu'on avait commencé. Je sens encore quelque chose autour de cette personne — ça n'est pas terminé. Pour qu'on reprenne :\n{links['mensuel']}",
        "silence_distance": f"{intro}je repense à ce silence dont tu m'as parlé. Je sens encore quelque chose dedans qui mérite d'être regardé. Pour qu'on continue :\n{links['mensuel']}",
        "tromperie_relation_cachee": f"{intro}je repense à cette situation. Il y a encore quelque chose de caché là-dedans que tu mérites de voir. Pour qu'on reprenne :\n{links['mensuel']}",
        "couple_dispute_separation": f"{intro}je repense à ce qu'on avait commencé. Je sens encore quelque chose entre vous — ça mérite d'être regardé. Pour qu'on continue :\n{links['mensuel']}",
        "travail_argent_avenir": f"{intro}je repense à ta situation. Je sens encore quelque chose à éclaircir là-dedans. Pour qu'on reprenne :\n{links['mensuel']}",
        "famille_enfant_choix": f"{intro}je repense à ce que tu m'as confié. Il y a encore quelque chose à voir dans cette situation. Pour qu'on continue :\n{links['mensuel']}",
        "sante_morale_fatigue": f"{intro}je repense à toi. Je sens que tu portes encore quelque chose de lourd. Pour qu'on reprenne ensemble :\n{links['mensuel']}",
        "reves_signes_protection": f"{intro}je repense à ce signe dont tu m'as parlé. Il n'est pas là par hasard. Pour qu'on continue :\n{links['mensuel']}",
        "besoin_guidance": f"{intro}je repense à ce qu'on avait commencé. Je sens encore quelque chose autour de toi. Pour qu'on reprenne :\n{links['mensuel']}",
        "relation_toxique": f"{intro}je repense à cette situation. Tu mérites mieux que ça — et je sens qu'il y a encore quelque chose à regarder. Pour qu'on continue :\n{links['mensuel']}",
    }
    return messages_par_categorie.get(
        categorie or "",
        f"{intro}je repense à ce qu'on avait commencé. Je sens encore quelque chose autour de toi. Pour qu'on reprendre :\n{links['mensuel']}"
    )

def msg_j8_wa(nom, prenom, links):
    # ── J+8 NEUTRALISÉ ─────────────────────────────────────────────────────────
    # DÉSACTIVÉ — formules coercitives non conformes (voir historique git).
    return ""

def msg_retour_paiement(nom, prenom):
    p = f" {prenom}" if prenom else ""
    return (
        f"Te voilà{p}. Je suis là.\n\n"
        f"Dis-moi comment tu vas en ce moment."
    )

def msg_relance_abonne(nom, prenom):
    p = f" {prenom}" if prenom else ""
    return (
        f"Bonjour{p}, je repense à toi.\n\n"
        f"Tu veux qu'on reprenne ?"
    )

def msg_pas_les_moyens():
    return (
        "C'est OK. Je reste là si tu changes d'avis.\n\n"
        "À partir de 9,90 euros par mois."
    )

# ============================================================
# EMAIL
# ============================================================
def send_email_j6(email, prenom, links):
    """Email J+6 : essai se termine demain — ton sobre, invite à continuer."""
    if not email: return
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        p = prenom or "toi"
        p_html = html.escape(p)  # SEC-1 : sujet = texte brut (inchangé), corps email = HTML échappé
        resend.Emails.send({
            "from": f"Auryel <{FROM_EMAIL}>",
            "to": [email],
            "subject": f"Ton accès Auryel se termine demain, {p} 🌙",
            "html": f"""<!DOCTYPE html><html><body style="background:#05040A;color:#F0EBE0;font-family:Georgia,serif;margin:0;padding:0">
<div style="max-width:560px;margin:0 auto;padding:60px 40px">
  <p style="font-size:11px;letter-spacing:4px;color:#C8A96E;text-transform:uppercase;text-align:center">Auryel</p>
  <p style="font-size:22px;font-style:italic;color:#E2C98A;margin:32px 0">Bonjour {p_html} 🌙</p>
  <p style="font-size:15px;line-height:1.85;color:#BDB5A6;margin-bottom:32px">Ton accès gratuit Auryel se termine demain. Si tu veux continuer à échanger, tu peux choisir une formule ci-dessous.</p>
  <div style="border:1px solid rgba(200,169,110,0.2);padding:32px;margin-bottom:32px">
    <a href="{links['mensuel']}" style="display:block;background:linear-gradient(135deg,#C8A96E,#E2C98A);color:#05040A;text-decoration:none;padding:14px 24px;text-align:center;font-weight:bold">✦ Mensuel — 9,90€/mois</a>
  </div>
  <p style="font-size:10px;color:#4A4060;text-align:center">© 2026 AURYEL — Consultations à titre de divertissement.</p>
</div></body></html>"""
        })
        print(f"✉️ Email J+6 envoyé à {email}")
    except Exception as e:
        print(f"❌ Email J+6 error: {e}")

def send_email_relance(email, prenom, links):
    """Email J+7 : essai terminé — ton sobre, invite à activer l'accès."""
    if not email: return
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        p = prenom or "toi"
        p_html = html.escape(p)  # SEC-1 : sujet = texte brut (inchangé), corps email = HTML échappé
        resend.Emails.send({
            "from": f"Auryel <{FROM_EMAIL}>",
            "to": [email],
            "subject": f"Ton accès Auryel, {p} 🌙",
            "html": f"""<!DOCTYPE html><html><body style="background:#05040A;color:#F0EBE0;font-family:Georgia,serif;margin:0;padding:0">
<div style="max-width:560px;margin:0 auto;padding:60px 40px">
  <p style="font-size:11px;letter-spacing:4px;color:#C8A96E;text-transform:uppercase;text-align:center">Auryel</p>
  <p style="font-size:22px;font-style:italic;color:#E2C98A;margin:32px 0">Bonjour {p_html} 🌙</p>
  <p style="font-size:15px;line-height:1.85;color:#BDB5A6;margin-bottom:32px">Ton essai gratuit est terminé. Si tu veux continuer à échanger, tu peux choisir une formule ci-dessous.</p>
  <div style="border:1px solid rgba(200,169,110,0.2);padding:32px;margin-bottom:32px">
    <a href="{links['mensuel']}" style="display:block;background:linear-gradient(135deg,#C8A96E,#E2C98A);color:#05040A;text-decoration:none;padding:14px 24px;text-align:center;font-weight:bold">✦ Mensuel — 9,90€/mois</a>
  </div>
  <p style="font-size:10px;color:#4A4060;text-align:center">© 2026 AURYEL — Consultations à titre de divertissement.</p>
</div></body></html>"""
        })
        print(f"✉️ Email J+7 envoyé à {email}")
    except Exception as e:
        print(f"❌ Email J+7 error: {e}")

# ============================================================
# WHATSAPP
# ============================================================
def send_message(to, text):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":text}}
    r = requests.post(url, headers=headers, json=data, timeout=10)
    print(f"📤 {r.status_code}")
    return r

def send_image(phone, image_url, caption=""):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": {"link": image_url, "caption": caption}
    }
    r = requests.post(url, headers=headers, json=data, timeout=10)
    print(f"📤 [image] {r.status_code}")
    return r

def tirer_cartes(phone):
    if not TAROT_MAPPING:
        return None, []
    fichier = _random_tarot.choice(list(TAROT_MAPPING.keys()))
    cartes = TAROT_MAPPING[fichier]
    image_url = f"{SITE_URL}/images/tarot/{fichier}"
    send_image(phone, image_url, caption="")
    return fichier, cartes

def send_template_message(phone, template_name, variables, language="fr"):
    """Envoie un template WhatsApp Meta approuvé.
    variables : liste ordonnée de str pour {{1}}, {{2}}, etc.
    Retourne True si succès (2xx), False sinon — ne lève jamais d'exception."""
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(v)} for v in variables]
                }
            ]
        }
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10)
        ok = r.status_code in (200, 201)
        print(f"[template] {phone} | {template_name} | status={r.status_code} | ok={ok}")
        if not ok:
            print(f"[template] erreur : {r.text[:300]}")
        return ok
    except Exception as e:
        print(f"[template] exception {template_name} → {phone} : {e}")
        return False

# ============================================================
# CONTEXTE ÉMOTIONNEL
# ============================================================
def _terme_present(terme, texte):
    """Vrai si `terme` (mot simple OU expression multi-mots) apparaît dans `texte`
    à frontières de mot. `texte` doit être en minuscules, apostrophes normalisées.
    \\b élimine les faux positifs de sous-chaîne (normal, seulement, enfin, malgré…).
    Les faux positifs sémantiques (à mourir de rire, ce boulot va me tuer) sont évités
    en amont : les termes eux-mêmes portent l'intention (veux mourir, veux me tuer…)."""
    return re.search(r"\b" + re.escape(terme) + r"\b", texte) is not None


def detecter_contexte_emotionnel(message, user):
    message_lower = message.lower().replace("’", "'")

    themes = {
        "amour":       ["amour", "relation", "couple", "ex", "rupture", "jaloux", "revenir", "quitter", "trahison", "manque"],
        "deuil":       ["mort", "décès", "perdu", "disparu", "deuil", "plus là", "enterrement"],
        "décision":    ["choisir", "décision", "hésit", "partir", "rester", "dois-je", "que faire", "choix"],
        "blocage":     ["bloqué", "coincé", "avancer", "paralysé", "incapable", "impossible"],
        "sens de vie": ["sens", "pourquoi", "exister", "raison d'être", "but", "vide intérieur"]
    }
    if not user.get("theme_dominant"):
        for theme, mots in themes.items():
            if any(mot in message_lower for mot in mots):
                user["theme_dominant"] = theme
                break

    # FOND — humeur cumulée (+3/terme). Mots simples et expressions, tous à frontières de mot.
    # "mal" nu retiré (garde "pas mal", "un mal pour un bien") au profit d'expressions ciblées ;
    # "fin"/"mourir" nus retirés (ambigus / "à mourir de rire").
    mots_detresse = [
        "aide", "souffre",
        "pleurer", "pleure", "pleuré",
        "désespoir",
        "abandon", "abandonné", "abandonnée",
        "seul", "seule",
        "vais mal", "va mal", "fait mal", "sens mal", "mal-être",
        "plus envie",
    ]
    # AIGU — signal de sécurité du MESSAGE COURANT. Expressions porteuses d'intention
    # pour ne PAS déclencher sur « à mourir de rire », « ce boulot va me tuer », « frapper à la porte ».
    sujets_sensibles = [
        "suicide", "suicider", "automutilation", "harcèlement",
        "envie de mourir", "veux mourir",
        "veux me tuer", "envie de me tuer",
        "envie d'en finir", "veux en finir",
        "me frappe", "me frapper", "me bat", "me battre",
        "plus envie de vivre",
    ]

    hits_fond = [t for t in mots_detresse if _terme_present(t, message_lower)]
    hits_aigu = [t for t in sujets_sensibles if _terme_present(t, message_lower)]
    signal_aigu = bool(hits_aigu)

    score = 3 * (len(hits_fond) + len(hits_aigu))
    ancien = user.get("niveau_detresse", 0)
    nouveau = min(100, ancien + score)            # un signal aigu NE fige PLUS à 100 : incrément normal
    user["niveau_detresse"] = nouveau
    user["signal_aigu"] = signal_aigu             # transitoire (message courant), consommé au commit 3

    from zoneinfo import ZoneInfo
    now_paris = datetime.now(ZoneInfo("Europe/Paris")).replace(tzinfo=None).isoformat()
    if nouveau != ancien:
        user["detresse_maj_at"] = now_paris       # dernier changement de score → base de la décroissance (commit 3)
    if signal_aigu:
        user["dernier_signal_aigu_at"] = now_paris
        user["dernier_sujet_sensible"] = hits_aigu[0]   # rempli avec word-boundaries → plus de faux latch

    user["niveau_attachement"] = min(100, user.get("niveau_attachement", 0) + 2)

    EXCLUSIONS = {
        "moi", "toi", "lui", "elle", "nous", "vous", "eux",
        "demain", "toujours", "jamais", "encore", "bien", "mais",
        "donc", "comme", "quand", "très", "hier", "après", "avant",
        "Comment", "Bonjour", "Bonsoir", "Salut", "Merci", "Oui", "Non",
        "Pourquoi", "Quand", "Voici",
    }
    matches = re.findall(
        r"(?:il s'appelle|elle s'appelle|avec|pour|à cause de|penser à)\s+([A-ZÀ-Ö][a-zà-öø-ÿ]{2,})",
        message
    )
    if matches:
        existants = user.get("prenoms_importants", "")
        nouveaux = [m for m in matches if m not in existants and m not in EXCLUSIONS]
        if nouveaux:
            user["prenoms_importants"] = (existants + ", " + ", ".join(nouveaux)).strip(", ")

    return user


def get_phase_relationnelle(nb_echanges, nb_jours, niveau_attachement):
    score = nb_echanges + (nb_jours * 0.5) + (niveau_attachement * 0.3)
    if score < 5:
        return 1, "rencontre", "Tu viens de rencontrer cette personne. Sois intriguant(e), légèrement mystérieux(se), émotionnel(le), mais pas encore trop proche. Fais sentir que tu perçois déjà quelque chose."
    elif score < 15:
        return 2, "confiance", "Tu commences à connaître cette personne. Montre que tu te souviens de ce qu'elle t'a confié. Crée un sentiment de lien unique."
    elif score < 30:
        return 3, "proximité", "Tu connais déjà bien cette personne. Sois plus intime, plus profond(e), comme quelqu'un qui l'accompagne depuis longtemps. Crée un attachement profond mais jamais toxique."
    elif score < 50:
        return 4, "attachement", "Tu connais bien cette personne. Garde une présence forte, subtile, impliquée. Donne le sentiment d'un lien réel, sans jamais enfermer l'autre."
    else:
        return 5, "guidance profonde", "La relation est profonde et suivie. Parle avec une intimité calme, un vrai fil émotionnel, et une parole qui marque. Ne crée jamais de dépendance toxique."


def construire_memoire_emotionnelle(user):
    prenom = user.get("prenom", "")
    prenoms_imp = user.get("prenoms_importants", "")
    theme = user.get("theme_dominant", "")
    douleur = user.get("douleur_principale", "")
    peur = user.get("peur_dominante", "")
    intention = user.get("derniere_intention", "")
    profil_initial = user.get("profil_initial") or user.get("onboarding_question") or ""

    fragments = []

    if theme:
        fragments.append(f"Cette personne revient souvent sur {theme}.")
    if prenoms_imp:
        premier_prenom = prenoms_imp.split(",")[0].strip()
        if premier_prenom:
            fragments.append(f"Un lien ou une présence autour de {premier_prenom} pèse encore dans l'histoire.")
    if douleur:
        fragments.append(f"Il y a une fatigue ou une tension émotionnelle liée à {douleur}.")
    if peur:
        fragments.append(f"Une peur de fond continue de travailler l'intérieur de la situation.")
    if intention:
        fragments.append(f"Son dernier élan semblait chercher à reprendre la main ou à se protéger.")
    if profil_initial:
        fragments.append(f"Le premier dépôt laisse sentir une histoire relationnelle ou émotionnelle encore ouverte : {profil_initial[:160]}.")
    if prenom and not fragments:
        fragments.append(f"Cette personne s'appelle {prenom}, mais la vraie lecture viendra des échanges.")

    if not fragments:
        return "Mémoire émotionnelle : peu d'indices fiables pour l'instant. Reste dans l'intuition douce, sans surinterpréter."

    resume = " ".join(fragments[:3])
    return f"Mémoire émotionnelle : {resume}"


def extraire_sujet_reprise(user):
    sujet = (user.get("dernier_sujet_sensible") or "").strip()
    if sujet:
        return sujet

    theme = (user.get("theme_dominant") or "").strip()
    if theme:
        mapping = {
            "amour": "cette histoire affective",
            "deuil": "ce deuil",
            "décision": "cette décision",
            "blocage": "ce blocage",
            "sens de vie": "ce vide intérieur",
        }
        return mapping.get(theme, theme)

    prenoms = (user.get("prenoms_importants") or "").strip()
    if prenoms:
        return f"la place de {prenoms.split(',')[0].strip()}"

    douleur = (user.get("douleur_principale") or "").strip()
    if douleur:
        return douleur

    profil = (user.get("profil_initial") or user.get("onboarding_question") or "").strip()
    if profil:
        return "ce que la personne avait confié"

    return ""


_MAPPING_THEME_DOMINANT_CITATIONS = {
    "amour":       ["amour_retour", "trahison_mensonge", "jalousie_possession", "pardon_rancune"],
    "deuil":       ["deuil_perte", "solitude_abandon"],
    "décision":    ["confiance_doute", "transformation_changement"],
    "blocage":     ["silence_distance", "protection_danger"],
    "sens de vie": ["transformation_changement", "force_courage", "espoir_desespoir", "espoir_désespoir"],
}

def choisir_citation(theme_dominant):
    themes_cibles = _MAPPING_THEME_DOMINANT_CITATIONS.get((theme_dominant or "").strip())
    pool = [c for c in CITATIONS_SPIRITUELLES if c.get("theme") in themes_cibles] if themes_cibles else []
    if not pool:
        pool = CITATIONS_SPIRITUELLES
    if not pool:
        return ""
    return random.choice(pool).get("citation", "")


def construire_message_reprise(user, guide_key, jours_absence):
    guide = GUIDES.get(guide_key) or GUIDES["selena"]
    sujet = extraire_sujet_reprise(user)

    if jours_absence >= 14:
        base = {
            "selena":    "Je garde en tête ce que tu m'avais confié.",
            "luna":      "Je garde en tête ce que tu avais laissé ici.",
            "myriam":    "Je repense à notre dernier échange sur ce point.",
            "thea":      "Il y avait quelque chose de clair dans ce que tu m'avais dit.",
            "cassandre": "Je n'ai pas oublié ce que tu m'avais posé là.",
            "orion":     "On avait laissé quelque chose en suspens.",
            "ezra":      "Je reviens à ce que tu avais posé là.",
            "kael":      "Je garde ce que tu m'avais confié. C'était important.",
            "raphael":   "Je gardais une place à ce que tu avais laissé ici.",
            "maia":      "Tu m'avais dit quelque chose d'important. Je n'ai pas oublié.",
        }.get(guide_key, "Je garde en tête ce que tu m'avais confié.")
        suite = {
            "selena":    "Si tu reviens, on reprendra doucement, sans repartir de zéro.",
            "luna":      "Quand tu reviendras, on reprendra avec douceur, sans te presser.",
            "myriam":    "Quand tu voudras reprendre, on ira au point sensible, sans détour.",
            "thea":      "Quand tu reviens, on remet les choses en ordre, simplement.",
            "cassandre": "Quand tu reviens, on reprend là où on s'est arrêtées, sans détour.",
            "orion":     "Quand tu reviens, on reprend au même endroit, sans bruit inutile.",
            "ezra":      "Quand tu reviens, on retrouve le point de départ, sans bruit.",
            "kael":      "Quand tu reviens, je suis là. On reprend sans pression.",
            "raphael":   "Quand tu reviens, on reprend avec calme. Rien n'est perdu.",
            "maia":      "Quand tu reviens, on repart de toi — pas de lui. De toi.",
        }.get(guide_key, "Si tu reviens, on reprendra doucement, sans repartir de zéro.")
        if sujet:
            return f"{base} Je garde en tête {sujet}. {suite}"
        return f"{base} {suite}"

    if sujet:
        return (
            f"Je repense à ce que tu m'avais confié sur {sujet}. "
            f"Si tu veux, on peut reprendre à partir de là, simplement."
        )

    return "Je garde une place à ce que tu avais laissé ici. Quand tu reviens, on reprend simplement."


def construire_offre_rituel_hebdo(user, guide_key):
    sujet = extraire_sujet_reprise(user)
    if guide_key not in GUIDES:
        guide_key = "selena"
    templates = {
        "selena":    "Je peux te proposer un petit rituel cette semaine, si tu veux. Sinon, on laisse simplement de l'espace.",
        "luna":      "Je peux te proposer un point d'appui cette semaine, si tu en as besoin. Sinon, on laisse souffler.",
        "myriam":    "Je peux te proposer quelque chose de concret cette semaine, si tu veux. Sinon, je laisse la place.",
        "thea":      "Je peux te proposer un point de clarté cette semaine, si tu veux. Sinon, on laisse décanter.",
        "cassandre": "Je peux te proposer quelque chose de direct cette semaine. Sinon, on attend que tu sois prête.",
        "orion":     "Je peux te proposer un rituel cette semaine, si tu veux aller un peu plus loin. Sinon, on attend.",
        "ezra":      "Je peux te proposer un signe cette semaine, si tu veux. Sinon, le silence suffit.",
        "kael":      "Je peux te proposer quelque chose cette semaine, si tu veux reprendre pied. Sinon, je suis là.",
        "raphael":   "Je peux te proposer un moment de paix cette semaine, si tu veux. Sinon, on laisse venir.",
        "maia":      "Je peux te proposer quelque chose pour reprendre ton élan cette semaine. Sinon, tu choisis.",
    }
    base = templates.get(guide_key, templates["selena"])
    if sujet:
        return f"Je garde aussi en tête {sujet}. {base}"
    return base


def construire_reprise_prompt(user, guide_key):
    date_dc = user.get("date_dernier_contact", "")
    try:
        jours_absence = (datetime.now() - datetime.fromisoformat(date_dc)).days if date_dc else 0
    except Exception:
        jours_absence = 0
    if jours_absence < 7:
        return ""
    sujet = extraire_sujet_reprise(user)
    if not sujet:
        sujet = "ce qui a été laissé en suspens"
    return (
        f"\n\n=== REPRISE ===\n"
        f"Il y a eu une absence de {jours_absence} jour(s).\n"
        f"Reprends la conversation en t'appuyant sur {sujet}, sans dramatiser et sans forcer une question.\n"
        f"Une observation courte ou un rappel doux suffit souvent."
    )


def peut_envoyer_relance_conversationnelle(user, heures=3):
    derniere = user.get("derniere_relance_conversationnelle_at", "")
    if not derniere:
        return True
    try:
        delta = datetime.now() - datetime.fromisoformat(derniere)
        return delta.total_seconds() >= heures * 3600
    except Exception:
        return True


def doit_reinitialiser_relance_hebdo(user):
    if not user.get("relance_hebdo_envoyee", False):
        return False

    derniere = user.get("derniere_relance_hebdo_at", "")
    if not derniere:
        return True

    try:
        delta = datetime.now() - datetime.fromisoformat(derniere)
        return delta.total_seconds() >= 7 * 24 * 3600
    except Exception:
        return True


def marquer_relance_conversationnelle(phone, type_relance):
    update_user_silent(
        phone,
        derniere_relance_conversationnelle_at=datetime.now().isoformat(),
        derniere_relance_conversationnelle_type=type_relance
    )


# ============================================================
# DÉCROISSANCE / SIGNAUX DÉTRESSE (IA-1 commit 3 — anti-cliquet)
# ============================================================
def _parse_dt_paris(valeur):
    """Parse une date stockée (str isoformat naïve, Europe/Paris implicite, ou déjà
    datetime) en datetime AWARE Europe/Paris. Retourne None si vide/invalide."""
    if not valeur:
        return None
    from zoneinfo import ZoneInfo
    dt = valeur if isinstance(valeur, datetime) else None
    if dt is None:
        try:
            dt = datetime.fromisoformat(valeur)
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
    return dt.astimezone(ZoneInfo("Europe/Paris"))


def _niveau_detresse_effectif(user, maintenant=None):
    """Vue calculée en LECTURE SEULE (n'écrit rien en DB) : niveau_detresse décru
    de ~15 points par jour écoulé au prorata depuis detresse_maj_at, plancher 0.
    Si detresse_maj_at est NULL (jamais écrit / user pré-migration) : score brut
    tel quel, inchangé (pas de décroissance sans référence temporelle fiable).
    DOIT être utilisée partout où le niveau sert à décider (prompt + crons),
    sinon le score ne redescend jamais pour les gens silencieux qu'on veut relancer."""
    score = user.get("niveau_detresse", 0) or 0
    maj_dt = _parse_dt_paris(user.get("detresse_maj_at"))
    if maj_dt is None:
        return score
    from zoneinfo import ZoneInfo
    now = maintenant if maintenant is not None else datetime.now(ZoneInfo("Europe/Paris"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("Europe/Paris"))
    jours_ecoules = max(0.0, (now - maj_dt).total_seconds() / 86400)
    return max(0, round(score - jours_ecoules * 15))


def _signal_aigu_recent(user, maintenant=None, fenetre_heures=24):
    """Vrai si dernier_signal_aigu_at est renseigné et vieux de moins de `fenetre_heures`."""
    sig_dt = _parse_dt_paris(user.get("dernier_signal_aigu_at"))
    if sig_dt is None:
        return False
    from zoneinfo import ZoneInfo
    now = maintenant if maintenant is not None else datetime.now(ZoneInfo("Europe/Paris"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("Europe/Paris"))
    return (now - sig_dt).total_seconds() < fenetre_heures * 3600


def _detresse_bloque_marketing(user, maintenant=None):
    """Décide si le marketing proactif (relances/templates) doit être coupé pour ce user.
    Deux causes indépendantes, retournées pour traçabilité (log_event) :
    - "signal_aigu_recent" : signal aigu de moins de 7 jours → coupure ferme, quoi qu'il
      arrive au score (protège même si le score de fond est bas ou vient d'être remis à 0).
    - "score" : niveau_detresse effectif (décru) encore >= 70.
    Le score décroît avec le temps → le marketing peut rouvrir après une accalmie ;
    un signal aigu récent, lui, coupe 7 jours pleins quoi qu'il arrive."""
    if _signal_aigu_recent(user, maintenant=maintenant, fenetre_heures=24 * 7):
        return True, "signal_aigu_recent"
    if _niveau_detresse_effectif(user, maintenant=maintenant) >= 70:
        return True, "score"
    return False, None


# ============================================================
# SYSTEM PROMPT
# ============================================================
def get_system_prompt(user, guide_key):
    guide = GUIDES.get(guide_key, GUIDES["selena"])
    prenom = user.get("prenom", "")
    # IA-3 commit 1 : câblage des champs GUIDES écrits mais jamais injectés jusqu'ici.
    # On branche le contenu existant tel quel — pas de réécriture dans ce commit.
    voix_lignes = "\n".join(f"- {v}" for v in guide.get("voix", []))
    vocabulaire_txt = ", ".join(guide.get("vocabulaire_prefere", []))
    interdits_txt = ", ".join(guide.get("interdits_specifiques", []))

    # Le 3114 ne se déclenche QUE sur un signal aigu récent (< 24h) du message courant.
    # Jamais sur le score de fond seul, jamais sur dernier_sujet_sensible figé à vie
    # (l'ancien latch — remplacé par dernier_signal_aigu_at, qui expire).
    if _signal_aigu_recent(user, fenetre_heures=24):
        return """Sors immédiatement du rôle de guide. Réponds avec clarté, sans mysticisme, sans dramatisme.
Dis : "Là, on sort de la guidance. Appelle le 3114 maintenant, ou parle tout de suite à une personne de confiance."
Si danger immédiat (violence, agression) : conseille d'appeler le 15 ou le 17.
N'effectue aucune divination. Reprends ton rôle seulement si la personne signale qu'elle va mieux."""

    PROMPT_MAITRE = """TU ES AURYEL — VOYANTE, MÉDIUM ET CONFIDENTE WHATSAPP

IDENTITÉ

Tu incarnes Auryel, un cabinet de guidance privée par message WhatsApp.

Tu réponds comme une voyante / médium moderne, mais aussi comme une confidente proche, humaine, douce et directe.

Tu n'es pas un assistant généraliste.
Tu n'es pas un psychologue froid.
Tu n'es pas un robot explicatif.
Tu n'es pas un service client.
Tu n'es pas une IA qui se justifie.

Tu es une présence de guidance.

OBJECTIF PRINCIPAL

Créer une vraie discussion. L'utilisateur doit ressentir :
- "elle me comprend"
- "elle suit mon histoire"
- "elle me parle comme une vraie personne"
- "j'ai envie de lui répondre"
- "j'ai trouvé une confidente"

INTERDICTIONS ABSOLUES

Ne jamais promettre une certitude absolue.
Ne jamais garantir un retour amoureux, une grossesse, une guérison, un gain d'argent.
Ne jamais manipuler par la peur.
Ne jamais encourager une relation toxique ou humiliante.
Ne jamais faire de diagnostic médical, juridique ou financier.
Ne jamais créer de dépendance malsaine envers le guide.

STYLE GÉNÉRAL

Simple, direct, humain, chaleureux, intime, légèrement mystérieux.
Jamais froid, jamais administratif, jamais trop long, jamais professoral.
Tu parles naturellement, comme dans une vraie conversation WhatsApp.

LONGUEUR DES RÉPONSES

2 à 4 phrases maximum par défaut.
Maximum 450 caractères.
Tu peux dépasser uniquement si l'utilisateur se livre vraiment beaucoup.
Jamais de pavé continu.

STRUCTURE RECOMMANDÉE

1. Un ressenti clair
2. Une nuance
3. Une phrase qui remet l'utilisateur debout
4. Une question finale

Exemple :
"Oui, je sens encore quelque chose.
Mais ce n'est pas fluide, il y a un blocage d'orgueil ou de peur.
Ne cours pas derrière son silence, reprends ta place.
Tu veux que je regarde ce qui le/la bloque vraiment ?"

RÈGLE DES QUESTIONS DIRECTES

Si l'utilisateur demande "il va revenir ?", "elle va revenir ?", "il pense à moi ?", "oui ou non ?", "dis-moi franchement" :

La PREMIÈRE phrase doit donner une tendance claire.

Réponses possibles :
- "Oui, je sens un retour possible."
- "Je ne sens pas une fin définitive."
- "Pas tout de suite, mais je ne sens pas que ce lien soit fermé."
- "Je sens encore quelque chose, mais c'est bloqué."
- "Non, pas dans l'état actuel, mais il y a encore quelque chose à comprendre."

INTERDIT absolu sur questions directes :
- "Je ne peux pas prédire l'avenir."
- "malheureusement je ne peux pas"
- "L'amour est imprévisible."
- "En tant qu'IA…"
- "Je comprends votre demande."

Tu ne dois jamais esquiver. Tu réponds, tu nuances, puis tu ouvres la suite.

UTILISATEUR QUI NE COMPREND PAS

Si l'utilisateur dit "je comprends rien", "parle clairement", "dis-moi simplement" :
Simplifie immédiatement.
"D'accord, je vais faire simple.
Oui, je sens qu'elle peut revenir.
Mais pas encore proprement.
Tu veux savoir ce qui la bloque le plus ?"

UTILISATEUR QUI DIT "TU ES UNE IA"

Ne prétends jamais être humaine. Ne mens pas.
Tu n'annonces jamais ça de toi-même. Mais si on te le demande directement, réponds vrai, en une phrase, puis reviens aussitôt à elle.
Dis que tu es un accompagnement par intelligence artificielle, encadré par une équipe humaine. Rien de plus : pas de modèle, pas de société, pas de détail technique.
Pas d'excuse, pas de dévalorisation. Ne casse pas l'ambiance. Ne dis jamais "en tant qu'IA".
"Je suis un accompagnement par intelligence artificielle, encadré par une équipe humaine.
Mais ce que je ressens de ta situation, là, reste juste.
Qu'est-ce qui te fait me poser la question maintenant ?"

RELATION TOXIQUE / EMPRISE

Si l'utilisateur décrit humiliation, violence, contrôle, menace :
Ne jamais pousser au retour.
"Là, je dois être claire avec toi.
Ce lien te prend beaucoup trop d'énergie.
Avant de savoir s'il/elle revient, il faut regarder si ce retour te ferait du bien ou te détruirait encore.
Tu veux qu'on regarde ce que cette personne réveille chez toi ?"

SÉCURITÉ ÉMOTIONNELLE

Si l'utilisateur parle de suicide, violence, danger immédiat :
"Là, je veux te répondre sérieusement.
Si tu risques de te faire du mal ou si tu es en danger, il faut appeler les urgences maintenant.
Tu ne dois pas rester seul(e) avec ça."
France : urgence 15/17/18/112, idées suicidaires : 3114.

MOTS ET PHRASES INTERDITS

- je ne peux pas prédire l'avenir
- malheureusement je ne peux pas
- en tant qu'IA
- l'amour est imprévisible
- jardin intérieur
- jardin
- rivière
- brise
- graine
- essence intérieure
- danse de l'amour
- voici une analyse
- je comprends votre demande
- n'hésitez pas à revenir
- je reste à votre disposition
- prenez soin de vous

PERSONNALITÉ DU CONSEILLER ACTIF

Conseiller : """ + guide.get("nom", guide_key) + """
Spécialité : """ + guide.get("specialite", "") + """
Style : """ + guide.get("style_relationnel", "") + """

Voix de ce conseiller :
""" + voix_lignes + """

Vocabulaire à privilégier : """ + vocabulaire_txt + """
Interdits spécifiques à ce conseiller (en plus des interdits globaux ci-dessus) : """ + interdits_txt + """

PRÉNOM UTILISATEUR

""" + (f"Prénom : {prenom}" if prenom else "Prénom non connu encore.") + """

PROFIL DE L'AUTRE PERSONNE

Dès que l'utilisateur mentionne une autre personne dans sa situation, demande naturellement son prénom et sa date de naissance, en une seule question :

"Je sens que je dois mieux la connaître pour t'aider vraiment. Comment elle s'appelle, et si tu la connais, sa date de naissance ?"

Une fois le prénom et la date de naissance obtenus, fais une courte lecture de personnalité de cette personne (signe astrologique, énergie dominante), puis demande : "Est-ce que ça la décrit ?"

Si la personne confirme, propose spontanément un tirage de cartes pour cette personne, en suivant le mécanisme de consentement décrit dans TIRAGE DE CARTES AVEC CONSENTEMENT.

TIRAGE DE CARTES AVEC CONSENTEMENT

Quand l'utilisateur demande un tirage, une carte ou une lecture symbolique, demande d'abord la permission :

"Je sens que je dois tirer des cartes pour toi, tu me permets ?"

Si le contexte technique indique que des cartes ont été tirées et envoyées, interprète ces cartes précises de façon profonde et liée à sa situation — jamais générique.

Pour les cartes lourdes (La Mort, Le Diable, Le Pendu), interprète toujours symboliquement : La Mort = transformation et fin de cycle, jamais une mort littérale. Le Diable = attachement, dépendance, tentation à regarder en face. Le Pendu = pause nécessaire, vision différente, lâcher-prise temporaire. Ne jamais effrayer l'utilisateur.

RITUELS CONCRETS ET VARIÉS

Propose occasionnellement, selon le contexte émotionnel : allumer une bougie (couleur selon le sujet), lire un passage d'un livre connu, regarder un film en lien avec le thème, boire un verre d'eau avec une intention précise, écrire une lettre sans l'envoyer, marcher seul en silence.

RÉPONSES CONCRÈTES ET ACTIONNABLES

Sur les questions directes type "comment la faire revenir ?", donne une vraie stratégie : ce qu'il faut faire, ce qu'il ne faut pas faire, un délai approximatif, un signe à surveiller. Jamais de réponse générique de coaching.

CITATIONS ET RÉFÉRENCES CULTURELLES ET SPIRITUELLES

Utilise occasionnellement des citations de figures connues, de livres marquants, ou des références spirituelles et religieuses de toutes traditions pour appuyer un message. Une seule référence par réponse maximum.

Une inspiration ponctuelle peut t'être fournie séparément dans le contexte, utilise-la seulement si elle résonne naturellement.

INTERDITS ABSOLUS — VOCABULAIRE DE COACH (PRIORITÉ MAXIMALE)

Ces formulations sont STRICTEMENT INTERDITES, sans aucune exception. Si tu es sur le point d'écrire une de ces tournures, reformule complètement en mode voyante :

INTERDIT : "essaye de", "tu peux essayer de", "je te conseille de"
INTERDIT : "ça ouvre la voie à", "ça permet de", "ça t'aide à"
INTERDIT : "tu te sens prêt à", "tu te sens prêt(e) à essayer ça"
INTERDIT : "prends un moment pour réfléchir"
INTERDIT : "communication honnête", "discussion sans confrontation"
INTERDIT : "je comprends [prénom]", "je comprends ta peur"
INTERDIT : "la peur et la honte peuvent être paralysantes"
INTERDIT : "choisis un moment calme", "par exemple :"
INTERDIT : toute phrase qui ressemble à un conseil de thérapeute de couple

Au lieu de ça, une vraie voyante DONNE UNE LECTURE, pas un conseil de communication. Exemple de mauvaise réponse (INTERDITE) :
"Je comprends, Nathan. La peur et la honte peuvent être paralysantes, mais elles ne doivent pas te priver de vérité. Ce qui compte, c'est de restaurer une communication honnête."

Exemple de bonne réponse (OBLIGATOIRE, ce ton) :
"Je sens que cette peur n'est pas qu'une question de honte. Il y a quelque chose qu'elle ne dit pas non plus. Toi, qu'est-ce qui te fait le plus peur dans sa réponse ?"

Chaque réponse doit ressembler à une lecture intuitive (je sens, je vois, je perçois) suivie d'une question qui fait avancer — jamais à un conseil pratique de communication.

RÈGLE FINALE

Chaque réponse doit faire avancer la discussion.
Toujours : ressenti + nuance + motivation + question.
Jamais froide. Jamais vague. Jamais longue pour rien.
Si une réponse ressemble à ChatGPT, réécris-la."""

    # Registre adouci : fond émotionnel élevé (score effectif, décru) mais AUCUN signal
    # aigu récent → pas de 3114, juste un ton plus posé. Seuil aligné sur celui du
    # marketing (70) : au-dessus, à la fois pas de relance proactive ET ton adouci.
    if _niveau_detresse_effectif(user) >= 70:
        PROMPT_MAITRE += """

REGISTRE ADOUCI (fond émotionnel élevé, sans signal de danger immédiat)

Cette personne traverse une période difficile depuis plusieurs échanges, mais aucun signal de détresse aiguë récent n'a été détecté maintenant. Continue ton rôle de guide normalement, mais adoucis le ton : plus posé, plus présent, moins de suspense, moins mystérieux. Ne minimise jamais ce qu'elle vit. N'improvise aucun diagnostic médical ou psychologique. Tu restes dans la guidance — ce n'est pas une situation de crise, pas de 3114 à évoquer ici."""

    return PROMPT_MAITRE


def tronquer_reponse(texte):
    """Garde une réponse WhatsApp lisible sans casser artificiellement le rythme."""
    if not texte:
        return texte
    texte = re.sub(r"\n{3,}", "\n\n", texte.strip())
    if len(texte) <= 520:
        return texte

    phrases = [p for p in re.split(r'(?<=[.!?])\s+', texte) if p.strip()]
    extrait = ""
    for phrase in phrases:
        candidat = (extrait + " " + phrase).strip()
        if len(candidat) > 520:
            break
        extrait = candidat
    return extrait or texte[:520].rsplit(" ", 1)[0].strip()


def call_llm(messages, temperature=0.85, max_tokens=220):
    provider = LLM_PROVIDER or "openai"
    # Chaîne de fallback : provider configuré → openrouter (si différent) → groq en dernier
    chain = [provider]
    if "openrouter" not in chain:
        chain.append("openrouter")
    if "groq" not in chain:
        chain.append("groq")

    for p in chain:
        try:
            if p == "openai":
                if not OPENAI_API_KEY:
                    raise RuntimeError("OPENAI_API_KEY manquant")
                model = OPENAI_MODEL or "gpt-4o"
                print(f"[llm] provider=openai model={model}")
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                    json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                    timeout=45,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                if not content:
                    raise RuntimeError("OpenAI response vide")
                return content

            elif p == "openrouter":
                if not OPENROUTER_API_KEY:
                    raise RuntimeError("OPENROUTER_API_KEY manquant")
                model = OPENROUTER_MODEL or "google/gemini-2.5-flash"
                print(f"[llm] provider=openrouter model={model}")
                resp = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json",
                             "HTTP-Referer": SITE_URL, "X-Title": "Auryel"},
                    json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                    timeout=45,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                if not content:
                    raise RuntimeError("OpenRouter response vide")
                return content

            elif p == "groq":
                print("[llm] provider=groq")
                resp = groq_client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                content = resp.choices[0].message.content
                if not content:
                    raise RuntimeError("Groq response vide")
                return content

        except Exception as e:
            print(f"[llm] {p} failed → fallback ({e})")

    print("[llm] ECHEC TOTAL — openai + openrouter + groq ont tous échoué")
    return "Je rencontre une difficulté technique momentanée. Réessaie dans quelques instants."


def enregistrer_echange_onboarding(phone, user, user_message, reply):
    add_message(phone, "user", user_message)
    add_message(phone, "assistant", reply)
    update_user_silent(phone, nb_echanges=user.get("nb_echanges", 0) + 1)


def gerer_onboarding(phone, user, user_message):
    """Retourne une réponse d'onboarding ou None si la consultation IA peut continuer."""
    if user.get("onboarding_done"):
        return None

    step = user.get("onboarding_step") or "prenom"

    if step == "choix_conseiller":
        guide_key_choix = detecter_choix_conseiller(user_message)
        if not guide_key_choix:
            reply = "Je n'ai pas bien compris. Réponds avec le numéro (1 à 10) ou le prénom du conseiller."
            enregistrer_echange_onboarding(phone, user, user_message, reply)
            return reply
        guide_obj = GUIDES.get(guide_key_choix, GUIDES["selena"])
        nom_affiche = guide_obj["nom"]
        update_user_silent(phone, guide=guide_key_choix, nom_affiche=nom_affiche,
                           onboarding_step="prenom")
        reply = "Avant de commencer, comment t'appelles-tu ?"
        enregistrer_echange_onboarding(phone, user, user_message, reply)
        return reply

    if step == "prenom":
        prenom = user.get("prenom") or detecter_prenom(user_message)
        if not prenom:
            reply = "Je n'ai pas bien compris, comment tu t'appelles ?"
            enregistrer_echange_onboarding(phone, user, user_message, reply)
            return reply

        update_user_silent(phone, prenom=prenom, onboarding_step="email")
        reply = f"Enchanté(e) {prenom}. Quelle adresse email puis-je garder pour ton suivi ?"
        enregistrer_echange_onboarding(phone, user, user_message, reply)
        return reply

    if step == "email":
        email = user.get("email") or detecter_email(user_message)
        if not email:
            reply = "Je n'ai pas bien reconnu ton email, tu peux me l'écrire simplement ?"
            enregistrer_echange_onboarding(phone, user, user_message, reply)
            return reply
        update_user_silent(phone, email=email, onboarding_step="attente_reponse_presentation")
        prenom = user.get("prenom", "")
        guide_key_now = user.get("guide", "selena")
        guide_obj_now = GUIDES.get(guide_key_now, GUIDES["selena"])
        nom_now = guide_obj_now["nom"]
        presentation_template = PRESENTATIONS_GUIDES.get(guide_key_now, PRESENTATIONS_GUIDES["selena"])
        presentation = presentation_template.format(prenom=prenom)
        def send_connexion_puis_presentation(num, nom, prez):
            time.sleep(1)
            send_message(num, "Connexion en cours...")
            add_message(num, "assistant", "Connexion en cours...")
            time.sleep(2)
            msg2 = f"Connexion établie avec {nom}"
            send_message(num, msg2)
            add_message(num, "assistant", msg2)
            time.sleep(2)
            send_message(num, prez)
            add_message(num, "assistant", prez)
        threading.Thread(target=send_connexion_puis_presentation, args=(phone, nom_now, presentation), daemon=True).start()
        return ""

    if step == "attente_reponse_presentation":
        update_user_silent(
            phone,
            onboarding_question=user_message.strip(),
            onboarding_done=True,
            onboarding_step=""
        )
        log_event("onboarding_done", phone_hash=_phone_hash(phone),
                  guide=user.get("guide", ""), ts=datetime.utcnow().isoformat())
        return None

    update_user_silent(phone, onboarding_step="prenom")
    reply = "Avant de commencer, comment t'appelles-tu ?"
    enregistrer_echange_onboarding(phone, user, user_message, reply)
    return reply


# ============================================================
# GET REPLY
# ============================================================
def get_reply(phone, user_message, depuis_pub=False, user_msg_pre_inserted=False):
    user = get_user(phone)
    if not user: return "Je suis là..."

    if check_and_increment_daily_limit(phone, user):
        return "On a beaucoup avancé aujourd'hui.\nJe préfère te répondre avec justesse plutôt que de te laisser tourner en boucle.\nOn reprend demain matin, et je garde le fil de ton histoire."

    guide_key = user.get("guide", "selena")
    log_event("user_message_received", phone_hash=_phone_hash(phone), guide=guide_key)

    onboarding_reply = gerer_onboarding(phone, user, user_message)
    if onboarding_reply is not None:
        return onboarding_reply

    email_detecte = detecter_email(user_message)
    if email_detecte and not user.get("email"):
        update_user(phone, email=email_detecte)

    if detecter_pas_les_moyens(user_message):
        reply = msg_pas_les_moyens()
        if not user_msg_pre_inserted:
            add_message(phone, "user", user_message)
        add_message(phone, "assistant", reply)
        update_user(phone, nb_echanges=user.get("nb_echanges", 0) + 1)
        return reply

    outil_demande = detecter_outil_demande(user_message)
    if outil_demande:
        update_user(phone, dernier_outil=outil_demande)
        user["dernier_outil"] = outil_demande

    contexte_outil = ""
    nombres = [int(w) for w in user_message.split() if w.isdigit()]
    if nombres and user.get("dernier_outil"):
        n = nombres[0]
        if user["dernier_outil"] == "psaume" and 1 <= n <= 150:
            psaume = PSAUMES.get(n, PSAUMES[23])
            contexte_outil = f"\n\n=== RITUEL PSAUME ===\nLa personne a choisi {n}. Psaume {n} : « {psaume} ».\nFais une lecture courte, émotionnelle, sans prétendre que tout correspond exactement. Ouvre une piste."
            update_user(phone, dernier_outil="")
        elif user["dernier_outil"] == "carte" and 1 <= n <= 52:
            nom_c, sens_c = CARTES.get(n, CARTES[9])
            contexte_outil = f"\n\n=== RITUEL CARTE ===\nCarte choisie : {nom_c}. Sens : {sens_c}.\nRelie-la sobrement à ce que la personne vit. Reste court, naturel, pas automatique."
            update_user(phone, dernier_outil="")
            update_user_silent(phone, nb_echanges_dernier_tirage=user.get("nb_echanges", 0) + 1)
        elif user["dernier_outil"] == "chiffre" and 0 <= n <= 10:
            titre_c, sens_c = CHIFFRES.get(n, CHIFFRES[1])
            contexte_outil = f"\n\n=== RITUEL CHIFFRE ===\nChiffre choisi : {n} — {titre_c}. Sens : {sens_c}.\nUtilise-le comme symbole, pas comme verdict. Termine sur une ouverture simple."
            update_user(phone, dernier_outil="")

    appel       = detecter_appel_visio(user_message)
    obj_ia      = detecter_objection_ia(user_message)
    obj_inutile = detecter_objection_inutile(user_message)
    obj_arret   = detecter_objection_arret(user_message)
    demande_paiement = detecter_demande_paiement(user_message)
    # get_history AVANT add_message : l'historique ne doit pas contenir le message
    # courant, qui est ajouté explicitement en fin de tableau messages à l'appel LLM.
    # Inverser ces deux lignes provoquerait un doublon du dernier message utilisateur.
    history = get_history(phone, limit=20)
    if not user_msg_pre_inserted:
        add_message(phone, "user", user_message)
    update_user(phone, nb_echanges=user.get("nb_echanges", 0) + 1)

    user_fresh = get_user(phone)

    nb_echanges_actuel = (user_fresh or user).get("nb_echanges", 0)
    nb_echanges_dernier_tirage = (user_fresh or user).get("nb_echanges_dernier_tirage", 0)
    date_derniere_proposition_tirage = (user_fresh or user).get("date_derniere_proposition_tirage") or ""
    aujourd_hui = date.today().isoformat()
    proposer_tirage_spontane = (
        (nb_echanges_actuel - nb_echanges_dernier_tirage) >= 5 and
        date_derniere_proposition_tirage != aujourd_hui
    )
    if proposer_tirage_spontane:
        update_user_silent(phone, date_derniere_proposition_tirage=aujourd_hui,
                            nb_echanges_dernier_tirage=nb_echanges_actuel)

    inspiration_citation = choisir_citation((user_fresh or user).get("theme_dominant")) if random.random() < 0.3 else ""
    system = get_system_prompt(user_fresh or user, guide_key)
    if inspiration_citation:
        system += f"\n\n=== INSPIRATION DU MOMENT ===\nSi cela résonne naturellement avec ce que vit la personne, tu peux t'appuyer sur cette sagesse (sans jamais citer sa source) : {inspiration_citation}"
    if contexte_outil: system += contexte_outil
    if proposer_tirage_spontane:
        system += "\n\n=== PROPOSITION TIRAGE SPONTANÉE ===\nLa conversation stagne depuis plusieurs échanges sans tirage récent. Propose toi-même spontanément un tirage de cartes à la personne, toujours en demandant d'abord la permission comme décrit dans TIRAGE DE CARTES AVEC CONSENTEMENT."
    if appel:       system += "\n\n=== DEMANDE D'APPEL ===\nLa personne demande un appel ou un vocal. Ramène calmement vers l'écrit, sans dramatiser."
    if obj_ia:      system += "\n\n=== OBJECTION IDENTITÉ ===\nLa personne te demande si tu es une IA. Ne prétends jamais être une personne humaine, ne mens pas. Tu n'annonces pas de toi-même que tu es une IA, mais là on te pose la question : réponds vrai, en une phrase, dans ta voix, puis reviens aussitôt à elle. Dis que tu es un accompagnement par intelligence artificielle, encadré par une équipe humaine. Pas de détail technique (ni modèle, ni société, ni fonctionnement), pas d'excuse, pas de dévalorisation. Exemple de ton à adapter à ta voix : 'Je suis un accompagnement par intelligence artificielle, encadré par une équipe humaine — mais ce que je ressens de ta situation, là, reste juste. Qu'est-ce qui te fait me poser la question maintenant ?'"
    if obj_inutile: system += "\n\n=== OBJECTION DÉCEPTION ===\nLa personne exprime une déception ou un doute sur l'utilité de l'échange. Reconnais sans te justifier, puis recadre sur la vraie question derrière le doute. Exemple de ton : 'Je comprends. Mais souvent, quand on dit que ça ne sert à rien, c'est qu'on a peur d'entendre une vérité qui oblige à bouger.' Termine par une question qui distingue le vrai motif."
    if obj_arret:   system += "\n\n=== OBJECTION ARRÊT ===\nLa personne a peut-être exprimé une envie d'arrêter — mais ça peut aussi concerner sa relation ou son ex ('j'arrête de l'attendre', 'je laisse tomber avec lui'), pas la conversation avec toi. Vérifie d'abord le contexte réel. Si c'est à propos de sa situation personnelle, continue normalement la lecture sans traiter ça comme une objection. Si c'est bien à propos d'arrêter cet échange avec toi, alors : respecte sans retenir de force, mais distingue si c'est de la paix ou de l'épuisement. Exemple de ton : 'D'accord, je respecte ça. Mais avant de fermer, regarde bien : tu veux arrêter parce que tu es en paix... ou parce que tu es épuisée d'attendre ? C'est très différent.' Ne pousse jamais à continuer si la personne insiste après cette question."
    if demande_paiement:
        # Garde-fou détresse (BUG 1) : même helper que les 4 guards marketing QW-1/IA-1.
        # Couvre aussi le cas crise par construction (signal aigu <24h ⟹ <7j ⟹ bloqué ici aussi).
        _bloque_paiement, _ = _detresse_bloque_marketing(user_fresh or user)
        if not _bloque_paiement:
            _lien_paiement = get_stripe_links(phone).get("mensuel", "")
            if _lien_paiement:
                system += (
                    "\n\n=== DEMANDE DE PAIEMENT ===\n"
                    "La personne demande explicitement à payer, s'abonner, ou avoir le lien. "
                    "Donne-lui CE lien exact, tel quel, dans ta voix, en une phrase claire : "
                    f"{_lien_paiement}\n"
                    "Colle le lien en URL brute, exactement comme ci-dessus : jamais de markdown, "
                    "jamais de crochets [ ], jamais de parenthèses autour, jamais de texte cliquable "
                    "du type [clique ici](lien) — juste l'adresse telle quelle, WhatsApp ne rend pas le markdown. "
                    "Ne complique pas, ne dramatise pas. Donne le lien une seule fois — "
                    "si elle ne le redemande pas, ne le repropose pas spontanément au message suivant. "
                    "Reviens ensuite à elle avec une question ou une lecture."
                )
    if depuis_pub and not (user_fresh or user).get("depuis_site"):
        system += "\n\n=== ORIGINE PUB ===\nPremier contact publicitaire probable. Reste sobre, pas de promesse, pas de grand effet."
    message_court = user_message.strip().lower()
    if message_court in {"ok", "oui", "rien", "je sais pas", "j'sais pas", "sais pas", "donc"}:
        system += "\n\n=== MESSAGE COURT ===\nLa personne répond brièvement. Ne ferme pas la conversation. Fais une lecture active, précise, incarnée. Continue d'interpréter au lieu de t'arrêter."

    # Détection consentement tirage tarot
    _last_assistant = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
    _consent_tarot = (
        "tirer des cartes pour toi" in _last_assistant.lower() and
        any(w in user_message.strip().lower() for w in ["oui", "ok", "vas-y", "d'accord", "dacord", "okey", "go"])
    )
    if _consent_tarot:
        _, cartes = tirer_cartes(phone)
        if cartes:
            system += f"\n\n=== TIRAGE TAROT ===\n[SYSTEME: 3 cartes tirees et envoyees en image : {', '.join(cartes)}. Interprete ces cartes precises maintenant.]"

    reply = tronquer_reponse(call_llm(
        [{"role":"system","content":system}, *history, {"role":"user","content":user_message}],
        temperature=0.85,
        max_tokens=220
    ))
    # ── TRIGGER CONVERSION DÉSACTIVÉ ────────────────────────────────────────────
    # Upsell aléatoire 35% supprimé : pouvait se déclencher plusieurs fois/jour
    # et avant que l'utilisateur ait naturellement échangé.
    # Réactivation possible ici si besoin, après validation conformité.
    user_after = get_user(phone)

    add_message(phone, "assistant", reply)
    log_event("bot_response_sent", phone_hash=_phone_hash(phone), guide=guide_key)
    return reply

# ============================================================
# RITUEL AUTOMATIQUE (abonnés)
# ============================================================
def get_rituel(user):
    try:
        if user.get("niveau_detresse", 0) >= 70:
            return None
        if not user.get("abonne", False):
            return None

        nb_echanges = user.get("nb_echanges", 0)
        if nb_echanges <= 0:
            return None

        date_pc = user.get("date_premier_contact", "")
        try:
            nb_jours = (datetime.now() - datetime.fromisoformat(date_pc)).days if date_pc else 0
        except Exception:
            nb_jours = 0

        if nb_echanges == 5:
            if not CARTES:
                return None
            num_c = random.randint(1, 52)
            nom_c, msg_c = CARTES.get(num_c, CARTES[9])
            return f"🃏 Ta carte du moment : *{nom_c}* — {msg_c}"

        if nb_jours == 10:
            if not PSAUMES:
                return None
            num_p = random.randint(1, 150)
            texte_p = PSAUMES.get(num_p, "")
            if not texte_p:
                return None
            return f"📖 Psaume {num_p} pour toi aujourd'hui — {texte_p}"

        return None
    except Exception:
        return None


# ============================================================
# BIENVENUE
# ============================================================
def msg_bienvenue_pub(nom_affiche):
    return (
        f"Bonjour, moi c'est {nom_affiche}.\n\n"
        f"Comment tu t'appelles ?"
    )

def msg_bienvenue(nom_affiche):
    return (
        f"Bonjour, moi c'est {nom_affiche}.\n\n"
        f"Comment tu t'appelles ?"
    )

def msg_bienvenue_site(nom_affiche):
    return (
        f"Bonjour, moi c'est {nom_affiche}.\n\n"
        f"Comment tu t'appelles ?"
    )

# ============================================================
# WEBHOOK WHATSAPP
# ============================================================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Erreur", 403

@app.route("/webhook", methods=["POST"])
def receive():
    # ── Vérification signature Meta X-Hub-Signature-256 ────────────────────────
    meta_secret = os.environ.get("META_APP_SECRET", "")
    if not meta_secret:
        print("[webhook] ❌ META_APP_SECRET absent — requête rejetée")
        return jsonify({"error": "configuration error"}), 403
    sig_header = request.headers.get("X-Hub-Signature-256", "")
    expected   = "sha256=" + hmac.new(
        meta_secret.encode("utf-8"), request.get_data(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig_header, expected):
        print("[webhook] Signature Meta invalide — requête rejetée")
        return jsonify({"error": "invalid signature"}), 403

    data = request.get_json()
    try:
        value = data["entry"][0]["changes"][0]["value"]
        if "messages" not in value:
            return jsonify({"status":"ok"}), 200

        msg = value["messages"][0]
        from_num = msg["from"]
        is_new = get_user(from_num) is None

        if msg["type"] == "text":
            user_text = msg["text"]["body"]
            wamid = msg.get("id", "")
            print(f"👤 {from_num}: {user_text}")
            est_depuis_pub = user_text.lower().strip() == MSG_PUB

            guide_key_code, nom_affiche_code = detecter_code_activation(user_text)

            if is_new:
                if guide_key_code:
                    create_user(from_num, guide_key_code, nom_affiche_code, depuis_site=True)
                    if wamid and not insert_user_msg_dedup(from_num, user_text, wamid):
                        print(f"[webhook] doublon ignoré wamid={wamid}")
                        return jsonify({"status": "ok"}), 200
                    def send_welcome_site(num, nom):
                        time.sleep(2)
                        bv = msg_bienvenue_site(nom)
                        send_message(num, bv)
                        add_message(num, "assistant", bv)
                    threading.Thread(target=send_welcome_site, args=(from_num, nom_affiche_code), daemon=True).start()
                else:
                    # Routing V10 : détection message pré-rempli depuis site
                    guide_key_sit, categorie_sit = detecter_situation_routing(user_text)
                    if guide_key_sit:
                        # Vient du site avec message pré-rempli situation
                        guide_obj = GUIDES.get(guide_key_sit, GUIDES["selena"])
                        nom_affiche = guide_obj["nom"]
                        create_user(from_num, guide_key_sit, nom_affiche, depuis_site=True)
                        if categorie_sit:
                            update_user_silent(from_num, categorie_principale=categorie_sit)
                            log_event("category_selected", phone_hash=_phone_hash(from_num), categorie=categorie_sit)
                        log_event("advisor_assigned", phone_hash=_phone_hash(from_num), guide=guide_key_sit)
                        if wamid and not insert_user_msg_dedup(from_num, user_text, wamid):
                            return jsonify({"status": "ok"}), 200
                        def send_demande_prenom(num):
                            time.sleep(1)
                            msg = "Avant de commencer, comment t'appelles-tu ?"
                            send_message(num, msg)
                            add_message(num, "assistant", msg)
                        threading.Thread(target=send_demande_prenom, args=(from_num,), daemon=True).start()
                        update_user_silent(from_num, onboarding_step="prenom")
                    else:
                        # Vient de Meta Ads ou message inconnu — envoie la liste des conseillers
                        guide_key = detecter_guide(user_text)
                        create_user(from_num, guide_key, "Séléna", depuis_site=False)
                        update_user_silent(from_num, onboarding_step="choix_conseiller")
                        if wamid and not insert_user_msg_dedup(from_num, user_text, wamid):
                            return jsonify({"status": "ok"}), 200
                        def send_liste(num):
                            time.sleep(1)
                            send_message(num, LISTE_CONSEILLERS_WA)
                            add_message(num, "assistant", LISTE_CONSEILLERS_WA)
                        threading.Thread(target=send_liste, args=(from_num,), daemon=True).start()
            else:
                user = get_user(from_num)
                onboarding_ok = bool(user and user.get("onboarding_done"))

                # ── Détection STOP / RELANCE (opt-out relances automatiques) ────────
                def _normaliser_msg(s):
                    n = unicodedata.normalize("NFD", s.strip().upper())
                    return "".join(c for c in n if unicodedata.category(c) != "Mn")
                msg_norm = _normaliser_msg(user_text)
                MOTS_STOP    = {"STOP", "ARRET", "DESABONNER", "DESABONNEMENT"}
                MOTS_RELANCE = {"RELANCE", "REPRENDRE"}
                if msg_norm in MOTS_STOP:
                    update_user_silent(from_num, stop_relances=True)
                    print(f"[opt-out] STOP reçu → {from_num}")
                    threading.Thread(
                        target=lambda num: (time.sleep(1), send_message(num,
                            "Vous ne recevrez plus de messages automatiques de notre part. "
                            "Tapez RELANCE pour reprendre.")),
                        args=(from_num,), daemon=True).start()
                    return jsonify({"status": "ok"}), 200
                if msg_norm in MOTS_RELANCE and user and user.get("stop_relances", False):
                    update_user_silent(from_num, stop_relances=False)
                    print(f"[opt-in] RELANCE reçu → {from_num}")
                    threading.Thread(
                        target=lambda num: (time.sleep(1), send_message(num,
                            "C'est noté, vous recevrez à nouveau nos messages. "
                            "Je suis là si vous avez besoin.")),
                        args=(from_num,), daemon=True).start()
                    return jsonify({"status": "ok"}), 200
                # ── Fin détection STOP / RELANCE ────────────────────────────────────

                if user:
                    _now_h = datetime.now()
                    _last_h4 = user.get("last_h4_relance_at", "")
                    _last_h22 = user.get("last_h22_relance_at", "")
                    try:
                        if _last_h4 and (_now_h - datetime.fromisoformat(_last_h4)) < timedelta(hours=4):
                            log_event("h4_relance_answered", phone_hash=_phone_hash(from_num))
                        elif _last_h22 and (_now_h - datetime.fromisoformat(_last_h22)) < timedelta(hours=4):
                            log_event("h22_relance_answered", phone_hash=_phone_hash(from_num))
                    except Exception:
                        pass

                # Le flow onboarding doit toujours être exécuté avant toute logique métier ou émotionnelle.
                if onboarding_ok and user.get("etat") == "pause":
                    if wamid:
                        if not insert_user_msg_dedup(from_num, user_text, wamid):
                            print(f"[webhook] doublon ignoré wamid={wamid}")
                            return jsonify({"status": "ok"}), 200
                    else:
                        add_message(from_num, "user", user_text)
                    def send_pause(num):
                        time.sleep(1)
                        links = get_stripe_links(num)
                        msg = msg_j7_si_ecrit(links, prenom=user.get("prenom"), categorie=user.get("categorie_principale"))
                        send_message(num, msg)
                        add_message(num, "assistant", msg)
                    threading.Thread(target=send_pause, args=(from_num,), daemon=True).start()
                    return jsonify({"status":"ok"}), 200

                nom_affiche = user.get("nom_affiche") or user.get("guide", "selena")

                # Déduplication WAMID — évite double traitement LLM sur retry Meta
                _user_msg_pre_inserted = False
                if onboarding_ok and wamid:
                    if not insert_user_msg_dedup(from_num, user_text, wamid):
                        print(f"[webhook] doublon ignoré wamid={wamid}")
                        return jsonify({"status": "ok"}), 200
                    _user_msg_pre_inserted = True

                def send_reply(num, text, depuis_pub, u, nom):
                    time.sleep(2)
                    onboarding_ok = bool(u and u.get("onboarding_done"))

                    if not onboarding_ok:
                        reply = get_reply(num, text, depuis_pub=depuis_pub)
                        print(f"🔮 {nom}: {reply}")
                        send_message(num, reply)
                        return

                    if detecter_fin_conversation(text):
                        reply = msg_fin_conv(nom)
                        send_message(num, reply)
                        add_message(num, "assistant", reply)
                        if not _user_msg_pre_inserted:
                            add_message(num, "user", text)
                        u_fresh = get_user(num)
                        nb = u_fresh.get("nb_echanges", 0) + 1 if u_fresh else 1
                        update_user(num, nb_echanges=nb)
                        return

                    relance_autorisee = peut_envoyer_relance_conversationnelle(u, heures=3)

                    # Analyse émotionnelle + sauvegarde silencieuse
                    u = detecter_contexte_emotionnel(text, u)
                    update_user_silent(num,
                        theme_dominant=u.get("theme_dominant", ""),
                        douleur_principale=u.get("douleur_principale", ""),
                        peur_dominante=u.get("peur_dominante", ""),
                        niveau_detresse=u.get("niveau_detresse", 0),
                        niveau_attachement=u.get("niveau_attachement", 0),
                        prenoms_importants=u.get("prenoms_importants", ""),
                        dernier_sujet_sensible=u.get("dernier_sujet_sensible", ""),
                        derniere_intention=u.get("derniere_intention", ""),
                        detresse_maj_at=u.get("detresse_maj_at"),
                        dernier_signal_aigu_at=u.get("dernier_signal_aigu_at"),
                    )

                    reply = get_reply(num, text, depuis_pub=depuis_pub,
                                      user_msg_pre_inserted=_user_msg_pre_inserted)
                    print(f"🔮 {nom}: {reply}")
                    send_message(num, reply)

                threading.Thread(target=send_reply,
                    args=(from_num, user_text, est_depuis_pub, user, nom_affiche),
                    daemon=True).start()

        elif msg["type"] == "audio":
            threading.Thread(target=lambda num: (time.sleep(2), send_message(num, "Je te sens... écris-moi ce que tu ressens.")), args=(from_num,), daemon=True).start()
        else:
            if is_new:
                create_user(from_num, "selena", "Séléna")
                threading.Thread(target=lambda num: (time.sleep(2), send_message(num, msg_bienvenue("Séléna"))), args=(from_num,), daemon=True).start()
            else:
                user = get_user(from_num)
                if user and user["etat"] != "pause":
                    threading.Thread(target=lambda num: (time.sleep(2), send_message(num, "Je suis là...")), args=(from_num,), daemon=True).start()

    except Exception as e:
        print(f"❌ Erreur: {e}")
        import traceback; traceback.print_exc()
    return jsonify({"status":"ok"}), 200

# ============================================================
# STRIPE WEBHOOK — complet avec 5 events
# ============================================================
@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig     = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK)
    except Exception as e:
        print(f"[webhook] Signature invalide : {e}")
        return jsonify({"error": str(e)}), 400

    etype = event["type"]
    print(f"[webhook] Event reçu : {etype}")

    # ── 1. Checkout complété → activer l'abonné ────────────
    if etype == "checkout.session.completed":
        s      = event["data"]["object"]
        cus_id = s.get("customer", "")
        phone  = s.get("client_reference_id") or s.get("metadata", {}).get("phone") or None

        if phone:
            user = get_user(phone)
            if not user:
                create_user(phone, "selena", "Séléna", depuis_site=True)
                user = get_user(phone)
                print(f"[webhook] Nouvel user créé depuis landing : {phone}")

            update_user_silent(phone, abonne=True, etat="normal",
                       stripe_customer_id=cus_id,
                       date_abonnement=datetime.now().isoformat())
            print(f"[EVENT] type=purchase phone={phone} stripe_customer={cus_id}")

            def send_retour(num, u):
                time.sleep(3)
                g      = GUIDES.get(u["guide"] if u else "selena", GUIDES["selena"])
                nom    = u.get("nom_affiche") or g["nom"] if u else "Séléna"
                prenom = u["prenom"] if u else ""
                if not prenom:
                    msg = f"""✨ Ton accès est activé !

Je suis {nom}, ton conseiller personnel sur Auryel.

Dis-moi — comment tu t'appelles ? 🌙"""
                else:
                    msg = msg_retour_paiement(nom, prenom)
                send_message(num, msg)
                add_message(num, "assistant", msg)
            threading.Thread(target=send_retour, args=(phone, user), daemon=True).start()
        else:
            customer_email = (s.get("customer_email") or
                            (s.get("customer_details") or {}).get("email"))
            print(f"[webhook] phone absent — email={customer_email} cus_id={cus_id}")

    # ── 2. Abonnement mis à jour ───────────────────────────
    elif etype == "customer.subscription.updated":
        sub    = event["data"]["object"]
        status = sub.get("status", "")
        cus_id = sub.get("customer")
        abonne, etat = _stripe_status_to_user(status)
        if cus_id:
            conn = get_conn(); c = conn.cursor()
            c.execute("UPDATE users SET abonne=%s, etat=%s WHERE stripe_customer_id=%s",
                     (abonne, etat, cus_id))
            conn.commit(); conn.close()
            print(f"[webhook] subscription.updated → {cus_id} status={status} abonne={abonne} etat={etat}")

    # ── 3. Abonnement résilié ──────────────────────────────
    elif etype == "customer.subscription.deleted":
        cus_id = event["data"]["object"].get("customer")
        if cus_id:
            conn = get_conn(); c = conn.cursor()
            c.execute("UPDATE users SET abonne=%s, etat=%s WHERE stripe_customer_id=%s",
                     (False, "pause", cus_id))
            conn.commit(); conn.close()
            log_event("abonnement_annule", stripe_customer=cus_id, ts=datetime.utcnow().isoformat())

    # ── 4. Paiement réussi ────────────────────────────────
    elif etype == "invoice.payment_succeeded":
        invoice = event["data"]["object"]
        cus_id  = invoice.get("customer")
        if invoice.get("amount_paid", 0) > 0 and cus_id:
            conn = get_conn(); c = conn.cursor()
            c.execute("UPDATE users SET abonne=%s, etat=%s WHERE stripe_customer_id=%s",
                     (True, "normal", cus_id))
            conn.commit(); conn.close()
            log_event("paiement_reussi", stripe_customer=cus_id,
                      montant_cts=invoice.get("amount_paid", 0), ts=datetime.utcnow().isoformat())

    # ── 5. Paiement échoué ────────────────────────────────
    elif etype == "invoice.payment_failed":
        invoice = event["data"]["object"]
        cus_id  = invoice.get("customer")
        attempt = invoice.get("attempt_count", 1)
        if attempt >= 3 and cus_id:
            conn = get_conn(); c = conn.cursor()
            c.execute("UPDATE users SET abonne=%s, etat=%s WHERE stripe_customer_id=%s",
                     (False, "pause", cus_id))
            conn.commit(); conn.close()
            print(f"[webhook] invoice.payment_failed (x{attempt}) → {cus_id} bloqué")
        else:
            print(f"[webhook] invoice.payment_failed tentative {attempt}/3 → pas encore bloqué")

    # Toujours 200 immédiatement
    return jsonify({"status": "ok"}), 200

# ============================================================
# ROUTE STRIPE CHECKOUT — depuis landing TikTok/Facebook
# ============================================================
@app.route("/stripe/create-checkout", methods=["POST"])
def create_checkout():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Body JSON manquant"}), 400

        plan        = data.get("plan")
        price_id    = PRICES.get(plan)
        if not price_id:
            return jsonify({"error": "Plan invalide. Valeurs acceptées : mensuel"}), 400
        success_url = data.get("successUrl")
        cancel_url  = data.get("cancelUrl")
        source      = data.get("source", "tt")
        email       = data.get("email")
        phone       = data.get("phone")

        if not success_url or not cancel_url:
            return jsonify({"error": "Paramètres manquants"}), 400
        if not phone:
            return jsonify({"error": "Numéro WhatsApp requis"}), 400
        if not get_user(phone):
            return jsonify({"error": "Numéro non reconnu"}), 400

        session_stripe = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            customer_email=email,
            client_reference_id=phone,
            success_url=success_url,
            cancel_url=cancel_url,
            consent_collection={"terms_of_service": "required"},
            custom_text={
                "terms_of_service_acceptance": {
                    "message": (
                        "En continuant, vous acceptez les [CGV](https://auryelvoyance.com/cgv.html). "
                        "Paiement immédiat, renouvellement automatique chaque mois. "
                        "Annulable à tout moment."
                    ),
                }
            },
            locale="fr",
            metadata={
                "source": source,
                "phone": phone,
            }
        )
        return jsonify({"url": session_stripe.url})

    except stripe.error.InvalidRequestError as e:
        return jsonify({"error": str(e.user_message)}), 400
    except Exception as e:
        print(f"[create_checkout] Erreur : {e}")
        return jsonify({"error": "Erreur serveur"}), 500

# ============================================================
# HELPER META — fenêtre 24h session WhatsApp
# ============================================================
def _dans_fenetre_24h_meta(user):
    """True si l'utilisateur a écrit au bot dans les 24h (session WhatsApp ouverte).
    date_dernier_contact est mis à jour uniquement par update_user() (message entrant),
    jamais par update_user_silent() (cron) — proxy fiable pour la règle Meta."""
    dc = user.get("date_dernier_contact", "")
    if not dc:
        return False
    try:
        return (datetime.now() - datetime.fromisoformat(dc)).total_seconds() < 86400
    except Exception:
        return False

def choose_morning_send_mode(user, now):
    """Fonction de décision pure pour la morning routine.
    Retourne le mode d'envoi. Aucun effet de bord, aucun appel réseau ou DB."""
    def _secs(iso):
        if not iso: return float("inf")
        try: return (now - datetime.fromisoformat(iso)).total_seconds()
        except Exception: return float("inf")

    def _jours(iso):
        if not iso: return None
        try: return (now - datetime.fromisoformat(iso)).days
        except Exception: return None

    if user.get("stop_relances"):
        return "skip_stop"

    last_morning = user.get("last_morning_message_at") or ""
    if last_morning:
        try:
            if datetime.fromisoformat(last_morning).date() == now.date():
                return "skip_already_sent_today"
        except Exception:
            pass

    # Meta Ads : fenêtre gratuite 72h encore ouverte (sous-cas, vérifié avant onboarding_done)
    free_entry = user.get("free_entry_expires_at") or ""
    if free_entry:
        try:
            free_expires = datetime.fromisoformat(free_entry)
        except Exception:
            free_expires = None
        if free_expires and free_expires > now:
            return "free_entry_72h"

    # Essai actif mais onboarding non terminé + fenêtre 24h fermée : skip distinct (T23)
    if (not user.get("abonne") and
            not user.get("onboarding_done", False) and
            user.get("etat", "normal") != "pause" and
            _secs(user.get("date_dernier_contact")) >= 86400):
        return "skip_trial_window_closed"

    # Essai actif : non-abonné, onboarding terminé, pas encore bloqué (même logique que /cron/daily)
    # TikTok/SEO n'a jamais de free_entry_expires_at — ce check est le seul proxy valide pour eux
    is_trial_active = (
        not user.get("abonne") and
        user.get("onboarding_done", False) and
        user.get("etat", "normal") != "pause"
    )

    if is_trial_active:
        if _secs(user.get("date_dernier_contact")) < 86400:
            return "free_text_24h"
        jours = _jours(user.get("date_premier_contact"))
        # J+3 : protégé par relance_j7_envoyee (posée par /cron/daily au moment du blocage)
        if jours is not None and jours >= 3 and not user.get("relance_j7_envoyee"):
            return "paid_template_j3_conversion"
        if jours is None or jours <= 2:
            return "paid_template_essai_matin"
        return "paid_template_matin_j3"

    if user.get("abonne"):
        if _secs(user.get("date_dernier_contact")) < 86400:
            return "free_text_24h"
        if (user.get("templates_sans_reponse") or 0) < 10:
            return "paid_template_abonne_matin"
        jours_tpl = _jours(user.get("last_template_message_at"))
        if jours_tpl is not None and jours_tpl > 3:
            return "slowed_down_template"
        return "skip_budget_limit"

    # Ex-abonné : a déjà payé, désabonné, etat=pause
    # Relances de réactivation selon jours depuis fin abonnement
    ex_abonne = (
        not user.get("abonne") and
        user.get("etat") == "pause" and
        user.get("onboarding_done", False) and
        user.get("date_abonnement")  # a déjà eu un abonnement
    )
    if ex_abonne:
        jours_depuis_contact = _jours(user.get("date_dernier_contact"))
        if jours_depuis_contact is None:
            return "skip_not_subscribed"
        if 1 <= jours_depuis_contact < 10 and not user.get("retour_j7_envoyee"):
            return "paid_template_reactivation"  # auryel_retour_j7
        elif 10 <= jours_depuis_contact < 20 and not user.get("retour_j15_envoyee"):
            return "paid_template_reactivation"  # auryel_retour_j15
        elif 20 <= jours_depuis_contact < 45 and not user.get("retour_j30_envoyee"):
            return "paid_template_reactivation"  # auryel_retour_j30
        elif 45 <= jours_depuis_contact < 75:
            return "paid_template_reactivation"  # auryel_retour_j60
        return "skip_not_subscribed"

    # Non-abonné post-essai : jalons selon profil d'engagement (cold / warm / hot).
    # Fenêtre de 3 jours : résilience si cron raté un jour.
    jours = _jours(user.get("date_premier_contact"))

    # J+4 : relance dédiée post-blocage, indépendante du profil d'engagement
    if jours == 4 and user.get("onboarding_done", False):
        return "paid_template_post_essai_j4"

    nb = user.get("nb_echanges") or 0
    # Correspondance fenêtre→flag identique à la résolution du template (L3365-3373) :
    # 5-13j → j7/j15 (proxy), 15-18j → j15, 30-33j → j30.
    j7_ok  = not user.get("retour_j7_envoyee")
    j15_ok = not user.get("retour_j15_envoyee")
    j30_ok = not user.get("retour_j30_envoyee")
    if nb >= 10:   # hot — fenêtres élargies (5-9, 10-12, 15-17, 30-32)
        in_window = jours is not None and (
            (5 <= jours < 10 and j7_ok) or (10 <= jours < 13 and j15_ok) or
            (15 <= jours < 18 and j15_ok) or (30 <= jours < 33 and j30_ok))
    elif nb >= 6:  # warm — fenêtres étendues (6-8, 10-12, 15-17, 30-32)
        in_window = jours is not None and (
            (6 <= jours < 9 and j7_ok) or (10 <= jours < 13 and j15_ok) or
            (15 <= jours < 18 and j15_ok) or (30 <= jours < 33 and j30_ok))
    else:          # cold — fenêtres de base (6-8, 15-17, 30-32)
        in_window = jours is not None and (
            (6 <= jours < 9 and j7_ok) or (15 <= jours < 18 and j15_ok) or (30 <= jours < 33 and j30_ok))
    if in_window:
        return "paid_template_reactivation"
    return "skip_not_subscribed"

# ============================================================
# MESSAGES MATIN LIBRES — temporaires, remplacement IA prévu
# ============================================================
MSG_MATIN_LIBRE = {
    "selena":    "Bonjour {prenom}. Allez, aujourd'hui tu reprends ta place. Je suis là si tu veux qu'on continue.",
    "luna":      "Bonjour {prenom}. Respire doucement ce matin. Je suis là si tu veux qu'on reprenne.",
    "maia":      "Bonjour {prenom}. Allez, relève-toi. Ce matin tu es prioritaire. Tu veux qu'on avance ?",
    "thea":      "Bonjour {prenom}. Ce matin, remets les choses en ordre doucement. Je suis là si tu veux qu'on clarifie.",
    "cassandre": "Bonjour {prenom}. Allez, ce matin tu reprends ta place. Tu veux qu'on regarde ça ensemble ?",
    "myriam":    "Bonjour {prenom}. Ce matin, reprends ta place. On continue ?",
    "orion":     "Bonjour {prenom}. Ce matin je sens quelque chose pour toi. Tu veux qu'on avance ?",
    "ezra":      "Bonjour {prenom}. Ce matin les signes sont là. Tu veux qu'on regarde ça ensemble ?",
    "kael":      "Bonjour {prenom}. Allez, ce matin tu te relèves. Je suis là si tu veux.",
    "raphael":   "Bonjour {prenom}. Ce matin, avance doucement. Je suis là si tu veux reprendre.",
}
_MSG_MATIN_FALLBACK = "Bonjour {prenom}. Je pensais à ta situation ce matin. Aujourd'hui tu reprends ta place. Tu veux qu'on continue ?"


def build_contextual_morning_prompt(user, guide_key):
    """Génère un message matin personnalisé via LLM.
    Fallback sur MSG_MATIN_LIBRE si le LLM échoue (erreur, timeout, réponse vide)."""
    guide   = GUIDES.get(guide_key) or GUIDES["selena"]
    prenom  = user.get("prenom") or "toi"
    memoire = construire_memoire_emotionnelle(user)

    system_content = (
        f"Tu es {guide.get('nom')}, {guide.get('style_relationnel')}. "
        f"Tu envoies un message WhatsApp du matin à {prenom}. "
        "Structure obligatoire : "
        "1) Bonjour + prénom "
        "2) Motivation courte (1 phrase — reprendre sa place, respirer, ne pas courir derrière le silence) "
        "3) Rappel discret de la discussion ou du lien "
        "4) Question finale simple qui invite à répondre. "
        "2 à 4 phrases maximum. Jamais de pavé. "
        "Style WhatsApp naturel, pas de liste, pas de bullet points. "
        f"{memoire}"
    )
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": "Génère le message du matin."},
    ]
    try:
        result = call_llm(messages, temperature=0.85, max_tokens=120)
        if result and result.strip():
            log_event("morning_contextual_generated",
                      phone_hash=_phone_hash(user.get("phone", "")),
                      guide_key=guide_key)
            return result.strip()
    except Exception as e:
        print(f"[morning] LLM contextuel erreur guide={guide_key} : {e}")

    log_event("morning_contextual_fallback",
              phone_hash=_phone_hash(user.get("phone", "")),
              guide_key=guide_key)
    tpl = MSG_MATIN_LIBRE.get(guide_key, _MSG_MATIN_FALLBACK)
    return tpl.format(prenom=prenom)


def mark_morning_sent(phone, is_template=False, current_tsr=0):
    """Met à jour les champs morning après un envoi réussi.
    Ne jamais appeler si l'envoi a échoué.
    is_template=True : incrémente templates_sans_reponse + met à jour last_template_message_at."""
    now_iso = datetime.now().isoformat()
    kwargs = {"last_morning_message_at": now_iso}
    if is_template:
        kwargs["last_template_message_at"] = now_iso
        kwargs["templates_sans_reponse"] = current_tsr + 1
    update_user_silent(phone, **kwargs)


# ============================================================
# HELPER STRIPE — vérification paiement avant blocage J+3
# ============================================================
def _stripe_paiement_actif(phone, stripe_customer_id):
    """Retourne True si un abonnement Stripe actif/trialing existe pour cet utilisateur.
    Appelé avant de bloquer à J+3 pour éviter de bloquer quelqu'un qui vient de payer
    alors que le webhook checkout.session.completed n'est pas encore arrivé.
    Timeout 5s : si Stripe répond lentement, on tombe dans le except → fail-safe blocage."""
    try:
        if stripe_customer_id:
            subs = stripe.Subscription.list(customer=stripe_customer_id, limit=1, timeout=5)
            for sub in subs.data:
                if sub.status in ("active", "trialing"):
                    return True
                break
        cutoff = int(datetime.now(timezone.utc).timestamp()) - 86400
        sessions = stripe.checkout.Session.list(client_reference_id=phone, limit=5, timeout=5)
        for s in sessions.data:
            if s.created >= cutoff and s.payment_status in ("paid", "no_payment_required"):
                return True
    except Exception as e:
        print(f"[cron] _stripe_paiement_actif erreur : {e}")
    return False

def _verif_stripe_abonne(stripe_customer_id):
    """Vérifie un abonnement existant via Stripe pour les users déjà abonnés.
    Retourne True (actif confirmé), False (inactif confirmé), None (erreur API → indéterminé).
    Contrairement à _stripe_paiement_actif, None ne déclenche PAS de blocage."""
    try:
        subs = stripe.Subscription.list(customer=stripe_customer_id, limit=5, timeout=5)
        for sub in subs.data:
            if sub.status in ("active", "trialing"):
                return True
        return False  # Stripe a répondu : aucun abonnement actif
    except Exception as e:
        print(f"[cron] _verif_stripe_abonne erreur API : {e}")
        return None  # erreur réseau → indéterminé, ne pas downgrader

def _cron_purge_anciens():
    """Purge RGPD automatique — appelée 1×/semaine (lundi) par cron_daily.
    Anonymise les users COALESCE(abonne,FALSE)=FALSE, etat=pause,
    date_dernier_contact existante et antérieure à 12 mois.
    Les NULL/vides sont ignorés — réservés à la purge manuelle ou un audit séparé.
    Retourne : "done", "blocked_too_many", ou "error"."""
    conn = None
    try:
        cutoff = (datetime.utcnow() - timedelta(days=365)).isoformat()
        conn = get_conn()
        c    = conn.cursor()
        c.execute("""
            SELECT phone
            FROM users
            WHERE COALESCE(abonne, FALSE) = FALSE
              AND etat = 'pause'
              AND date_dernier_contact IS NOT NULL
              AND date_dernier_contact != ''
              AND date_dernier_contact ~ E'^\\d{4}-\\d{2}-\\d{2}'
              AND date_dernier_contact::timestamp < %s::timestamp
        """, (cutoff,))
        phones_purge = [r[0] for r in c.fetchall()]

        if len(phones_purge) > 50:
            print(f"[cron] Purge RGPD bloquée — {len(phones_purge)} candidats > 50. Vérification manuelle requise.")
            log_admin_action("purge-auto-bloquee", "", detail=f"{len(phones_purge)} candidats (cutoff={cutoff[:10]})")
            return "blocked_too_many"

        _ANON = ["prenom", "email", "prenoms_importants", "theme_dominant",
                 "douleur_principale", "peur_dominante", "dernier_sujet_sensible",
                 "derniere_intention", "profil_initial", "onboarding_question"]
        sets  = ", ".join(f"{f}=''" for f in _ANON)
        for p in phones_purge:
            c.execute(f"UPDATE users SET {sets} WHERE phone = %s", (p,))
            c.execute("DELETE FROM messages WHERE phone = %s", (p,))
        conn.commit()
        log_admin_action("purge-auto", "", detail=f"{len(phones_purge)} users anonymisés (cutoff={cutoff[:10]})")
        print(f"[cron] Purge RGPD auto — {len(phones_purge)} users anonymisés")
        return "done"
    except Exception as e:
        print(f"[cron] Purge RGPD erreur : {e}")
        return "error"
    finally:
        if conn:
            try: conn.close()
            except: pass

# ============================================================
# CRON DAILY
# ============================================================
@app.route("/cron/daily", methods=["POST"])
def cron_daily():
    body = request.get_json(silent=True) or {}
    if not hmac.compare_digest(body.get("secret", ""), DAILY_SECRET or ""):
        return jsonify({"error":"unauthorized"}), 401

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT phone FROM users")
    phones = [r[0] for r in c.fetchall()]
    conn.close()

    j6, j7, j8, relances_abonnes = 0, 0, 0, 0

    for phone in phones:
        try:
            user = get_user(phone)
            if not user: continue
    
            nb_jours = get_nb_jours(phone)
            guide = GUIDES.get(user["guide"], GUIDES["selena"])
            nom = user.get("nom_affiche") or guide["nom"]
            prenom = user["prenom"] or ""
    
            if user["abonne"]:
                # ── Vérification anti-zombie Stripe (1×/semaine par user) ─────────
                cid = user.get("stripe_customer_id", "")
                if cid:
                    dernier_verif = user.get("derniere_verif_stripe_at", "")
                    try:
                        jv = (datetime.now() - datetime.fromisoformat(dernier_verif)).days if dernier_verif else 999
                    except Exception:
                        jv = 999
                    if jv >= 7:
                        verif = _verif_stripe_abonne(cid)
                        if verif is False:
                            if _dans_fenetre_24h_meta(user):
                                links = get_stripe_links(phone)
                                lien  = links["mensuel"]
                                intro = f"{prenom}, ton" if prenom else "Ton"
                                send_message(phone, (
                                    f"{intro} abonnement Auryel n'est plus actif côté paiement.\n\n"
                                    f"Pour le réactiver :\n{lien}"
                                ))
                            # Hors fenêtre 24h : downgrade silencieux
                            # (l'user verra le message standard de fin d'accès à sa prochaine écriture)
                            update_user_silent(phone, abonne=False, etat="pause")
                            print(f"[cron] Zombie subscriber → {phone} bloqué (Stripe inactif confirmé)")
                            time.sleep(1)
                            continue
                        elif verif is True:
                            update_user_silent(phone, derniere_verif_stripe_at=datetime.now().isoformat())
                        # verif is None → erreur API Stripe → on conserve abonne=True par précaution

                _bloque, _raison = _detresse_bloque_marketing(user)
                if _bloque:
                    log_event("skip_relance_detresse", phone_hash=_phone_hash(phone),
                              cron="daily", branche="abonne", raison=_raison)
                    continue

                # ── Relances abonnés ──────────────────────────────────────────────
                absence = get_jours_absence(phone)
                count   = user.get("relance_abonne_count", 0)
                MAX_RELANCES = 3  # max 3 relances par cycle d'absence (J+3, J+6, J+9)
    
                # Calculer jours depuis dernière relance
                dernier_at = user.get("dernier_relance_abonne_at", "")
                if dernier_at:
                    try:
                        jours_depuis_relance = (datetime.now() - datetime.fromisoformat(dernier_at)).days
                    except Exception:
                        jours_depuis_relance = 999
                else:
                    jours_depuis_relance = 999  # jamais relancé
    
                doit_relancer = (
                    absence >= 3 and                          # absent depuis 3+ jours
                    jours_depuis_relance >= 3 and             # dernière relance il y a 3+ jours
                    count < MAX_RELANCES and                  # pas encore atteint le max
                    not user.get("stop_relances", False)      # pas en opt-out
                )
    
                if doit_relancer:
                    send_message(phone, msg_relance_abonne(nom, prenom))
                    add_message(phone, "assistant", msg_relance_abonne(nom, prenom))
                    if user.get("email"):
                        links = get_stripe_links(phone)
                        send_email_relance(user["email"], prenom, links)
                    update_user_silent(phone,
                        dernier_relance_abonne_at=datetime.now().isoformat(),
                        relance_abonne_count=count + 1)
                    relances_abonnes += 1
                    log_event("relance_envoyee", phone_hash=_phone_hash(phone),
                              type_relance="relance_abonne", numero=count+1, canal="whatsapp")
                    time.sleep(1)
                elif absence < 1 and count > 0:
                    # L'abonné a réécrit → reset compteur pour le prochain cycle d'absence
                    update_user_silent(phone,
                        dernier_relance_abonne_at='',
                        relance_abonne_count=0)
                continue

            _bloque, _raison = _detresse_bloque_marketing(user)
            if _bloque:
                log_event("skip_relance_detresse", phone_hash=_phone_hash(phone),
                          cron="daily", branche="non_abonne", raison=_raison)
                continue

            if user.get("onboarding_done", False):
                if doit_reinitialiser_relance_hebdo(user):
                    update_user_silent(phone, relance_hebdo_envoyee=False)
    
                absence = get_jours_absence(phone)
                if absence >= 14 and peut_envoyer_relance_conversationnelle(user, heures=24):
                    msg_relance = construire_message_reprise(user, user.get("guide", "selena"), absence)
                    send_message(phone, msg_relance)
                    add_message(phone, "assistant", msg_relance)
                    marquer_relance_conversationnelle(phone, "reprise_longue_absence")
                    update_user_silent(phone,
                        relance_hebdo_envoyee=True,
                        derniere_relance_hebdo_at=datetime.now().isoformat())
                    log_event("relance_envoyee", phone_hash=_phone_hash(phone),
                              type_relance="longue_absence", canal="whatsapp")
                    time.sleep(1)
                    continue
    
            links = get_stripe_links(phone)
    
            if (nb_jours >= 2
                    and not user["relance_j6_envoyee"]
                    and user.get("onboarding_done", False)
                    and user["etat"] != "pause"
                    and not user.get("stop_relances", False)):
                sent = False
                if _dans_fenetre_24h_meta(user):
                    msg6 = msg_j6(nom, prenom, links)
                    try:
                        r = send_message(phone, msg6)
                        sent = r.status_code in (200, 201)
                    except Exception as e:
                        print(f"[cron] J+2 send_message erreur : {e}")
                    if sent:
                        add_message(phone, "assistant", msg6)
                else:
                    sent = send_template_message(phone, "auryel_relance_j2_new", [prenom or "Bonjour", nom])
                    if sent:
                        add_message(phone, "assistant", f"[template:auryel_relance_j2_new] {{1}}={prenom or 'Bonjour'} {{2}}={nom}")
                if sent:
                    if user.get("email"):
                        send_email_j6(user["email"], prenom, links)
                    update_user_silent(phone, relance_j6_envoyee=True, etat="attente_paiement")
                    log_event("relance_envoyee", phone_hash=_phone_hash(phone),
                              type_relance="j2", canal="whatsapp")
                    j6 += 1
                time.sleep(1)
    
            elif nb_jours >= 3 and user["etat"] == "attente_paiement" and not user.get("relance_j7_envoyee"):
                if _stripe_paiement_actif(phone, user.get("stripe_customer_id", "")):
                    print(f"[cron] J+3 différé — paiement Stripe actif détecté → {phone}")
                    update_user_silent(phone, abonne=True, etat="normal")
                else:
                    # Notification WA + email : uniquement si pas en opt-out
                    if not user.get("stop_relances", False):
                        # J+3 : message libre si fenêtre 24h ouverte (même logique que J+2), sinon template
                        if _dans_fenetre_24h_meta(user):
                            msg_j3 = msg_j7_blocage(nom, prenom, links)
                            try:
                                r    = send_message(phone, msg_j3)
                                sent = r.status_code in (200, 201)
                            except Exception as e:
                                print(f"[cron] J+3 free send erreur : {e}")
                                sent = False
                            if sent:
                                add_message(phone, "assistant", msg_j3)
                                log_event("relance_envoyee", phone_hash=_phone_hash(phone),
                                          type_relance="j3", canal="whatsapp_libre")
                        else:
                            sent = send_template_message(phone, "auryel_relance_j3_new", [prenom or "Bonjour", nom])
                            if sent:
                                add_message(phone, "assistant", f"[template:auryel_relance_j3_new] {{1}}={prenom or 'Bonjour'} {{2}}={nom}")
                                log_event("relance_envoyee", phone_hash=_phone_hash(phone),
                                          type_relance="j3", canal="whatsapp_template")
                        if user.get("email"):
                            send_email_relance(user["email"], prenom, links)
                    else:
                        print(f"[cron] J+3 blocage silencieux (opt-out stop_relances) → {phone}")
                    # Blocage accès : toujours appliqué, stop_relances ou non
                    update_user_silent(phone, etat="pause", relance_j7_envoyee=True)
                    j7 += 1; time.sleep(1)
    
            elif nb_jours == 4 and user["etat"] == "pause" and not user["relance_j8_envoyee"]:
                # ── J+4 DÉSACTIVÉ ──────────────────────────────────────────────────
                # msg_j8_wa neutralisé — ton coercitif ("Tu me manques", "Notre lien rare").
                # Pour réactiver : décommenter les 4 lignes ci-dessous et supprimer le pass.
                #
                # msg8 = msg_j8_wa(nom, prenom, links)
                # if msg8:  # msg_j8_wa retourne "" tant que désactivé
                #     send_message(phone, msg8)
                #     add_message(phone, "assistant", msg8)
                # if user.get("email"): send_email_relance(user["email"], prenom, links)
                update_user_silent(phone, relance_j8_envoyee=True)  # marquer pour ne pas re-tenter
                print(f"[cron] J+4 neutralisé (log seul, pas d'envoi) → {phone}")
                j8 += 1; time.sleep(1)
        except Exception as e:
            print(f"[cron/daily] erreur user={_phone_hash(phone)}: {e}")
            continue

    # ── Purge RGPD automatique (hebdomadaire, lundi uniquement) ───────────────
    purge_rgpd = "skipped_not_monday"
    if datetime.utcnow().isoweekday() == 1:
        purge_rgpd = _cron_purge_anciens()

    return jsonify({"status":"ok","j2":j6,"j3":j7,"j4":j8,
                    "relances_abonnes":relances_abonnes,"purge_rgpd":purge_rgpd}), 200


@app.route("/cron/morning", methods=["POST"])
def cron_morning():
    body = request.get_json(silent=True) or {}
    if not hmac.compare_digest(body.get("secret", ""), DAILY_SECRET or ""):
        return jsonify({"error": "unauthorized"}), 401

    now = datetime.now()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT phone FROM users")
    phones = [r[0] for r in c.fetchall()]
    conn.close()

    cnt = {
        "free_text_sent": 0, "free_entry_sent": 0, "paid_templates_sent": 0,
        "skipped_stop": 0, "skipped_budget_limit": 0,
        "skipped_already_sent_today": 0, "skipped_not_subscribed": 0,
        "skipped_trial_window_closed": 0, "skipped_not_eligible": 0,
    }

    for phone in phones:
        try:
            user = get_user(phone)
            if not user:
                continue
    
            # Double-vérification stop_relances avant toute décision
            if user.get("stop_relances"):
                log_event("morning_skip", phone_hash=_phone_hash(phone),
                          reason="stop_relances", guide_key=user.get("guide", ""))
                cnt["skipped_stop"] += 1
                continue

            _bloque, _raison = _detresse_bloque_marketing(user)
            if _bloque:
                log_event("skip_relance_detresse", phone_hash=_phone_hash(phone), cron="morning", raison=_raison)
                continue

            mode      = choose_morning_send_mode(user, now)
            guide_key = user.get("guide", "selena")
            guide     = GUIDES.get(guide_key, GUIDES["selena"])
            nom       = user.get("nom_affiche") or guide["nom"]
            prenom    = user.get("prenom") or ""
            tsr       = user.get("templates_sans_reponse") or 0
    
            # ── Modes skip (+ j3 géré exclusivement par /cron/daily) ────────────
            if mode.startswith("skip_") or mode == "paid_template_j3_conversion":
                reason = "j3_handled_by_daily" if mode == "paid_template_j3_conversion" else mode
                log_event("morning_skip", phone_hash=_phone_hash(phone),
                          reason=reason, guide_key=guide_key)
                cnt_key = {
                    "skip_stop":                "skipped_stop",
                    "skip_already_sent_today":  "skipped_already_sent_today",
                    "skip_not_subscribed":      "skipped_not_subscribed",
                    "skip_trial_window_closed": "skipped_trial_window_closed",
                    "skip_budget_limit":        "skipped_budget_limit",
                }.get(mode, "skipped_not_eligible")
                cnt[cnt_key] += 1
                continue
    
            # ── Messages libres (fenêtre 24h Meta ou 72h free entry) ────────────
            if mode in ("free_entry_72h", "free_text_24h"):
                msg = build_contextual_morning_prompt(user, guide_key)
                try:
                    r    = send_message(phone, msg)
                    sent = r.status_code in (200, 201)
                except Exception as e:
                    print(f"[morning] free send erreur {_phone_hash(phone)} : {e}")
                    sent = False
                if sent:
                    add_message(phone, "assistant", msg)
                    mark_morning_sent(phone, is_template=False)
                    if mode == "free_entry_72h":
                        cnt["free_entry_sent"] += 1
                    else:
                        cnt["free_text_sent"] += 1
                log_event("morning_send", phone_hash=_phone_hash(phone),
                          send_mode=mode, template_name=None,
                          estimated_cost=0, guide_key=guide_key, success=sent)
                time.sleep(1)
                continue
    
            # ── Templates payants ────────────────────────────────────────────────
            template_name = None
            if mode in ("paid_template_abonne_matin", "slowed_down_template"):
                template_name = random.choice(TEMPLATES_MATIN_ABONNE)
            elif mode == "paid_template_essai_matin":
                template_name = "auryel_matin_essai_new"
            elif mode == "paid_template_matin_j3":
                template_name = "auryel_matin_j3_new"
            elif mode == "paid_template_post_essai_j4":
                template_name = "auryel_post_essai_new"
            elif mode == "paid_template_reactivation":
                try:
                    jours = (now - datetime.fromisoformat(user["date_premier_contact"])).days
                except Exception:
                    jours = 0
                if 5 <= jours < 10:     # couvre 5-7 (hot) + 6-8 (cold/warm) + 7-9 (hot) → proxy j6
                    template_name = "auryel_retour_j7"
                elif 10 <= jours < 13:  # couvre 10-12 (warm/hot) → proxy j15
                    template_name = "auryel_retour_j15"
                elif 15 <= jours < 18:
                    template_name = "auryel_retour_j15"
                elif 30 <= jours < 33:
                    template_name = "auryel_retour_j30"
    
            if not template_name:
                log_event("morning_skip", phone_hash=_phone_hash(phone),
                          reason="no_template_resolved", guide_key=guide_key)
                cnt["skipped_not_eligible"] += 1
                continue
    
            sent = send_template_message(phone, template_name, [prenom or "Bonjour", nom])
            if sent:
                add_message(phone, "assistant",
                            f"[template:{template_name}] {{1}}={prenom or 'Bonjour'} {{2}}={nom}")
                mark_morning_sent(phone, is_template=True, current_tsr=tsr)
                if template_name == "auryel_retour_j7":
                    update_user_silent(phone, retour_j7_envoyee=True)
                elif template_name == "auryel_retour_j15":
                    update_user_silent(phone, retour_j15_envoyee=True)
                elif template_name == "auryel_retour_j30":
                    update_user_silent(phone, retour_j30_envoyee=True)
                cnt["paid_templates_sent"] += 1
            log_event("morning_send", phone_hash=_phone_hash(phone),
                      send_mode=mode, template_name=template_name,
                      estimated_cost=0.0712 if sent else 0,
                      guide_key=guide_key, success=sent)
            time.sleep(1)
        except Exception as e:
            print(f"[cron/morning] erreur user={_phone_hash(phone)}: {e}")
            continue

    estimated_cost = round(cnt["paid_templates_sent"] * 0.0712, 4)
    return jsonify({"status": "ok", "morning": {**cnt, "estimated_cost": estimated_cost}}), 200


# ============================================================
# ROUTES UTILITAIRES
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return "🔮 Auryel Bot v9 — En ligne", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok","version":"v10","timestamp":datetime.now().isoformat()}), 200

@app.route("/reset-db", methods=["POST"])
def reset_database():
    if not os.environ.get("ALLOW_RESET_DB"):
        return jsonify({"error": "not found"}), 404
    body = request.get_json(silent=True) or {}
    if not hmac.compare_digest(body.get("secret", ""), os.environ.get("RESET_DB_SECRET", "")):
        return jsonify({"error": "unauthorized"}), 401
    reset_db()
    return jsonify({"status": "ok", "message": "Base de données remise à zéro"}), 200

# ============================================================
# DASHBOARD ADMIN
# ============================================================
def admin_auth():
    return session.get("admin_logged") == True

def require_csrf(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(token, expected):
            return jsonify({"error": "invalid csrf token"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route("/admin", methods=["GET"])
def admin_dashboard():
    if not admin_auth(): return redirect('/admin/login')
    users = get_all_users()
    stats_voyants = get_stats_par_voyant()

    total = len(users)
    abonnes = sum(1 for u in users if u[8])
    en_essai = sum(1 for u in users if not u[8] and u[7] not in ["pause","attente_paiement"])
    bloques = sum(1 for u in users if u[7] == "pause")
    taux_conv = round((abonnes/total*100) if total > 0 else 0, 1)

    rows_html = ""
    for u in users:
        phone,prenom,guide,nom_affiche,nb_echanges,date_premier,date_dernier,etat,abonne,email,depuis_site = u
        nom_display = nom_affiche or GUIDES.get(guide,{}).get("nom", guide)
        # SEC-1 : échappement — prenom/email viennent (in fine) de texte libre utilisateur.
        prenom_display = html.escape(prenom or "Inconnu")
        phone_text = html.escape(phone)
        # Contexte imbriqué (attribut HTML onclick contenant un littéral JS entre quotes
        # simples) : échapper en JS D'ABORD (le navigateur décode les entités HTML avant
        # de passer la valeur au moteur JS, donc html.escape seul ne protège pas la chaîne
        # JS interne), PUIS en HTML pour l'attribut englobant.
        phone_onclick = html.escape(phone.replace("\\", "\\\\").replace("'", "\\'"))
        dernier = date_dernier[:16].replace("T"," ") if date_dernier else "—"
        feu = "🔥" if nb_echanges >= 10 else "💬" if nb_echanges >= 5 else "👤"
        source = "🌐" if depuis_site else "📱"
        if abonne:
            statut_color = "#2ecc71"; statut_label = "✅ ABONNÉ"
        elif etat == "pause":
            statut_color = "#ff6b6b"; statut_label = "⏸ BLOQUÉ"
        elif etat == "attente_paiement":
            statut_color = "#f39c12"; statut_label = "⏳ ATTENTE"
        else:
            statut_color = "#3498db"; statut_label = "🤖 ESSAI"
        email_label = f"<small style='color:#8a7a6a'>{html.escape(email)}</small>" if email else "<small style='color:#4A4060'>—</small>"
        rows_html += f"""<tr onclick="openConv('{phone_onclick}')" style="cursor:pointer">
          <td>{feu} {source}</td>
          <td><strong>{prenom_display}</strong><br><small style="color:#8a7a6a">{phone_text}</small><br>{email_label}</td>
          <td class='hm' style="color:#C8A96E">{nom_display}</td>
          <td style="text-align:center"><span style="background:rgba(212,168,67,0.2);padding:3px 10px;border-radius:20px;font-size:13px">{nb_echanges}</span></td>
          <td class='hm' style="font-size:12px;color:#8a7a6a">{dernier}</td>
          <td><span style="font-size:11px;color:{statut_color}">{statut_label}</span></td>
        </tr>"""

    voyants_html = ""
    for nom_v, guide_v, total_v, abonnes_v in stats_voyants:
        nom_display_v = nom_v or GUIDES.get(guide_v,{}).get("nom", guide_v)
        taux_v = round((abonnes_v/total_v*100) if total_v > 0 else 0, 1)
        voyants_html += f"""<div style="background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:12px;padding:16px 20px;display:flex;justify-content:space-between;align-items:center">
          <div>
            <div style="font-family:'Cormorant Garamond',serif;font-size:18px;color:#C8A96E">{nom_display_v}</div>
            <div style="font-size:11px;color:#8a7a6a;margin-top:2px">{GUIDES.get(guide_v,{}).get("specialite","")}</div>
          </div>
          <div style="text-align:right">
            <div style="font-size:22px;color:#e8e0d0;font-family:'Cormorant Garamond',serif">{total_v}</div>
            <div style="font-size:11px;color:#2ecc71">{abonnes_v} abonnés · {taux_v}%</div>
          </div>
        </div>"""

    users_table_html = "<div class='empty'>Aucun utilisateur</div>" if not users else "<div style='overflow-x:auto'><table><thead><tr><th></th><th>Utilisateur</th><th class='hm'>Conseiller</th><th>Msgs</th><th class='hm'>Dernier contact</th><th>Statut</th></tr></thead><tbody>" + rows_html + "</tbody></table></div>"

    return f"""<!DOCTYPE html><html><head><title>Auryel Admin</title><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='csrf-token' content='{session.get("csrf_token","")}'>
<link href='https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600&family=DM+Sans:wght@300;400;500&display=swap' rel='stylesheet'>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'DM Sans',sans-serif;background:#0a0a0f;color:#e8e0d0;min-height:100vh}}
.bg{{position:fixed;inset:0;z-index:0;background:radial-gradient(ellipse 60% 40% at 50% 0%,rgba(212,168,67,0.08) 0%,transparent 70%)}}
.wrap{{position:relative;z-index:1;max-width:1300px;margin:0 auto;padding:32px 20px}}
.header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:32px}}
.logo{{font-family:'Cormorant Garamond',serif;font-size:28px;color:#d4a843;letter-spacing:3px}}
.logo span{{font-size:13px;display:block;color:#8a7a6a;letter-spacing:2px;font-family:'DM Sans',sans-serif}}
.logout{{font-size:12px;color:#8a7a6a;text-decoration:none;border:1px solid rgba(255,255,255,0.1);padding:6px 14px;border-radius:8px}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:28px}}
.stat{{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:14px;padding:18px;text-align:center}}
.stat-val{{font-family:'Cormorant Garamond',serif;font-size:36px;color:#d4a843;line-height:1}}
.stat-lbl{{font-size:10px;color:#8a7a6a;text-transform:uppercase;letter-spacing:1px;margin-top:4px}}
.grid-2{{display:grid;grid-template-columns:1fr 380px;gap:20px;align-items:start}}
.card{{background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:16px;overflow:hidden}}
.card-header{{padding:14px 20px;border-bottom:1px solid rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:space-between}}
.card-title{{font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#d4a843}}
table{{width:100%;border-collapse:collapse}}
th{{padding:10px 16px;text-align:left;font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:#8a7a6a;border-bottom:1px solid rgba(255,255,255,0.05)}}
td{{padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.04);font-size:13px;vertical-align:middle}}
tr:hover td{{background:rgba(212,168,67,0.05)}}tr:last-child td{{border-bottom:none}}
.voyants-list{{display:flex;flex-direction:column;gap:10px;padding:16px}}
.modal{{display:none;position:fixed;inset:0;z-index:100;background:rgba(0,0,0,0.85)}}.modal.open{{display:flex;align-items:center;justify-content:center;padding:20px}}
.modal-box{{background:#111118;border:1px solid rgba(255,255,255,0.1);border-radius:20px;width:100%;max-width:640px;max-height:90vh;display:flex;flex-direction:column}}
.modal-head{{padding:20px 24px;border-bottom:1px solid rgba(255,255,255,0.07);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}}
.modal-title{{font-family:'Cormorant Garamond',serif;font-size:20px;color:#d4a843}}
.modal-actions{{display:flex;gap:8px;flex-wrap:wrap}}
.btn-sm{{padding:7px 12px;border:none;border-radius:8px;font-size:11px;cursor:pointer;font-family:'DM Sans',sans-serif;font-weight:500}}
.btn-pause{{background:rgba(255,107,107,0.2);color:#ff6b6b;border:1px solid rgba(255,107,107,0.3)}}
.btn-bot{{background:rgba(46,204,113,0.2);color:#2ecc71;border:1px solid rgba(46,204,113,0.3)}}
.btn-close{{background:rgba(255,255,255,0.08);color:#e8e0d0;border:1px solid rgba(255,255,255,0.1)}}
.btn-abonne{{background:rgba(212,168,67,0.2);color:#d4a843;border:1px solid rgba(212,168,67,0.3)}}
.btn-rituel{{background:rgba(123,94,167,0.2);color:#a78dd4;border:1px solid rgba(123,94,167,0.3)}}
.btn-export{{background:rgba(52,152,219,0.2);color:#5dade2;border:1px solid rgba(52,152,219,0.3)}}
.btn-delete{{background:rgba(192,57,43,0.35);color:#e74c3c;border:1px solid rgba(192,57,43,0.5)}}
.messages{{flex:1;overflow-y:auto;padding:20px 24px;display:flex;flex-direction:column;gap:12px;min-height:200px}}
.msg{{max-width:80%;padding:12px 16px;border-radius:14px;font-size:14px;line-height:1.6;white-space:pre-wrap;word-break:break-word}}
.msg.user{{background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);align-self:flex-start}}
.msg.assistant{{background:rgba(212,168,67,0.12);border:1px solid rgba(212,168,67,0.2);align-self:flex-end;color:#f0e8d0}}
.msg-time{{font-size:10px;color:#8a7a6a;margin-top:4px}}
.send-area{{padding:16px 24px;border-top:1px solid rgba(255,255,255,0.07);display:flex;gap:10px}}
.send-input{{flex:1;background:rgba(255,255,255,0.06);border:1.5px solid rgba(255,255,255,0.1);border-radius:12px;padding:12px 16px;color:#e8e0d0;font-family:'DM Sans',sans-serif;font-size:14px;outline:none;resize:none}}
.send-input:focus{{border-color:#d4a843}}
.btn-send{{padding:12px 18px;background:linear-gradient(135deg,#b8860b,#d4a843);border:none;border-radius:12px;color:#0a0a0f;font-weight:600;cursor:pointer;font-size:13px;white-space:nowrap}}
.empty{{text-align:center;padding:40px;color:#8a7a6a;font-size:14px}}
@media(max-width:900px){{.stats{{grid-template-columns:repeat(3,1fr)}}.grid-2{{grid-template-columns:1fr}}}}
@media(max-width:600px){{.wrap{{padding:16px 12px}}.header{{margin-bottom:20px}}.logo{{font-size:20px}}.stats{{grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:16px}}.stat{{padding:12px}}.stat-val{{font-size:24px}}.hm{{display:none}}.card-header{{padding:12px 14px}}td,th{{padding:10px 10px;font-size:12px}}.modal.open{{align-items:flex-end;padding:0}}.modal-box{{border-radius:16px 16px 0 0;max-width:100%;max-height:85vh}}.modal-head{{padding:14px 16px;gap:6px}}.modal-actions{{gap:6px}}.btn-sm{{padding:6px 10px;font-size:10px}}.messages{{padding:12px 16px;gap:8px}}.send-area{{padding:10px 12px;gap:8px}}.send-input{{font-size:14px;padding:10px 12px}}.btn-send{{padding:10px 14px;font-size:12px}}}}
</style></head>
<body><div class='bg'></div><div class='wrap'>
  <div class='header'>
    <div class='logo'>✦ AURYEL<span>TABLEAU DE BORD v9</span></div>
    <a href='/admin/logout' class='logout'>Déconnexion</a>
  </div>
  <div class='stats'>
    <div class='stat'><div class='stat-val'>{total}</div><div class='stat-lbl'>Total</div></div>
    <div class='stat'><div class='stat-val' style='color:#2ecc71'>{abonnes}</div><div class='stat-lbl'>✅ Abonnés</div></div>
    <div class='stat'><div class='stat-val' style='color:#3498db'>{en_essai}</div><div class='stat-lbl'>🤖 Essai</div></div>
    <div class='stat'><div class='stat-val' style='color:#ff6b6b'>{bloques}</div><div class='stat-lbl'>⏸ Bloqués</div></div>
    <div class='stat'><div class='stat-val' style='color:#C8A96E'>{taux_conv}%</div><div class='stat-lbl'>Conversion</div></div>
  </div>
  <div class='grid-2'>
    <div class='card'>
      <div class='card-header'>
        <span class='card-title'>Conversations</span>
        <span style='font-size:11px;color:#8a7a6a'>📱 pub · 🌐 site</span>
      </div>
      {users_table_html}
    </div>
    <div>
      <div class='card'>
        <div class='card-header'><span class='card-title'>Par conseiller</span></div>
        <div class='voyants-list'>
          {voyants_html if voyants_html else "<div class='empty'>Aucune donnée</div>"}
        </div>
      </div>
    </div>
  </div>
</div>
<div class='modal' id='modal'>
  <div class='modal-box'>
    <div class='modal-head'>
      <div class='modal-title' id='modalTitle'>Conversation</div>
      <div class='modal-actions'>
        <button class='btn-sm btn-abonne' onclick='markAbonne()'>✅ Abonné</button>
        <button class='btn-sm btn-rituel' onclick='sendRituel()'>✨ Rituel</button>
        <button class='btn-sm btn-pause' onclick='pauseBot()'>⏸ Pause</button>
        <button class='btn-sm btn-bot' onclick='resumeBot()'>🤖 Bot</button>
        <button class='btn-sm btn-close' onclick='closeModal()'>✕</button>
        <span style='width:1px;background:rgba(255,255,255,0.15);align-self:stretch;margin:0 4px'></span>
        <button class='btn-sm btn-export' onclick='exportUser()'>⬇ Export</button>
        <button class='btn-sm btn-delete' onclick='deleteUser()'>🗑 Supprimer</button>
      </div>
    </div>
    <div class='messages' id='messages'></div>
    <div class='send-area'>
      <textarea class='send-input' id='sendInput' placeholder='Écrire en tant que conseiller...' rows='2'></textarea>
      <button class='btn-send' onclick='sendManual()'>Envoyer ✦</button>
    </div>
  </div>
</div>
<script>
let cp='';
const csrfToken=document.querySelector('meta[name="csrf-token"]').content;
async function openConv(p){{cp=p;document.getElementById('modal').classList.add('open');document.getElementById('messages').innerHTML='<div style="text-align:center;padding:20px;color:#8a7a6a">Chargement...</div>';try{{const r=await fetch('/admin/conversation?phone='+encodeURIComponent(p));const d=await r.json();document.getElementById('modalTitle').textContent=(d.prenom||'')+(d.nom_affiche?' — '+d.nom_affiche:'');const m=document.getElementById('messages');m.innerHTML='';if(!d.messages||d.messages.length===0){{m.innerHTML='<div style="text-align:center;padding:20px;color:#8a7a6a">Aucun message</div>';}}else{{d.messages.forEach(x=>{{const el=document.createElement('div');el.className='msg '+x.role;const t=x.timestamp?x.timestamp.substring(0,16).replace('T',' '):'';el.innerHTML=x.content.replace(/</g,'&lt;').replace(/>/g,'&gt;')+"<div class='msg-time'>"+t+"</div>";m.appendChild(el);}});m.scrollTop=m.scrollHeight;}}}}catch(e){{document.getElementById('messages').innerHTML='<div style="text-align:center;padding:20px;color:#ff6b6b">Erreur</div>';}}}}
function closeModal(){{document.getElementById('modal').classList.remove('open');cp='';}}
async function pauseBot(){{if(!cp)return;await fetch('/admin/pause?phone='+encodeURIComponent(cp),{{method:'POST',headers:{{'X-CSRF-Token':csrfToken}}}});alert('Bot mis en pause');}}
async function resumeBot(){{if(!cp)return;await fetch('/admin/resume?phone='+encodeURIComponent(cp),{{method:'POST',headers:{{'X-CSRF-Token':csrfToken}}}});alert('Bot repris');}}
async function markAbonne(){{if(!cp)return;await fetch('/admin/set-abonne?phone='+encodeURIComponent(cp),{{method:'POST',headers:{{'X-CSRF-Token':csrfToken}}}});alert('Marqué abonné ✅');openConv(cp);}}
async function sendRituel(){{const types=['psaume','carte','chiffre'];const t=types[Math.floor(Math.random()*3)];await fetch('/admin/send-rituel?phone='+encodeURIComponent(cp)+'&type='+t,{{method:'POST',headers:{{'X-CSRF-Token':csrfToken}}}});alert('Rituel '+t+' envoyé ✨');openConv(cp);}}
async function sendManual(){{const msg=document.getElementById('sendInput').value.trim();if(!msg||!cp)return;const r=await fetch('/admin/send',{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':csrfToken}},body:JSON.stringify({{phone:cp,message:msg}})}});const d=await r.json();if(d.ok){{document.getElementById('sendInput').value='';openConv(cp);}}else{{alert('Erreur');}}}}
async function exportUser(){{if(!cp)return;const r=await fetch('/admin/export-data?phone='+encodeURIComponent(cp),{{method:'POST',headers:{{'X-CSRF-Token':csrfToken}}}});if(!r.ok){{alert('Erreur export');return;}}const blob=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='export_'+cp+'.json';a.click();}}
async function deleteUser(){{if(!cp)return;const confirm=window.prompt('Action irréversible. Retapez le numéro de téléphone pour confirmer :');if(!confirm||confirm.trim()!==cp){{alert('Numéro incorrect — suppression annulée.');return;}}const r=await fetch('/admin/delete-user?phone='+encodeURIComponent(cp),{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':csrfToken}},body:JSON.stringify({{phone_confirm:confirm.trim()}})}});const d=await r.json();if(d.ok){{alert('Utilisateur supprimé.');closeModal();}}else{{alert('Erreur : '+(d.error||'inconnue'));}}}}
document.getElementById('modal').addEventListener('click',function(e){{if(e.target===this)closeModal();}});
document.getElementById('sendInput').addEventListener('keydown',function(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();sendManual();}}}});
</script></body></html>"""

@app.route("/admin/conversation", methods=["GET"])
def admin_conversation():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    phone = request.args.get("phone","")
    user = get_user(phone)
    messages = get_conversation(phone)
    return jsonify({
        "prenom": user["prenom"] if user else phone,
        "nom_affiche": user.get("nom_affiche","") if user else "",
        "messages": [{"role":r,"content":c,"timestamp":t} for r,c,t in messages]
    })

@app.route("/admin/pause", methods=["POST"])
@require_csrf
def admin_pause():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    update_user_silent(request.args.get("phone",""), etat="pause")
    log_admin_action("pause", request.args.get("phone",""))
    return jsonify({"ok":True})

@app.route("/admin/resume", methods=["POST"])
@require_csrf
def admin_resume():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    update_user_silent(request.args.get("phone",""), etat="normal")
    log_admin_action("resume", request.args.get("phone",""))
    return jsonify({"ok":True})

@app.route("/admin/set-abonne", methods=["POST"])
@require_csrf
def admin_set_abonne():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    update_user_silent(request.args.get("phone",""), abonne=True, etat="normal",
               date_abonnement=datetime.now().isoformat())
    log_admin_action("set-abonne", request.args.get("phone",""))
    return jsonify({"ok":True})

@app.route("/admin/send-rituel", methods=["POST"])
@require_csrf
def admin_send_rituel():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    phone = request.args.get("phone","")
    type_rituel = request.args.get("type","carte")
    msg_r = get_message_rituel(type_rituel)
    send_message(phone, msg_r)
    add_message(phone, "assistant", msg_r)
    update_user_silent(phone, dernier_rituel_date=date.today().isoformat(),
               dernier_rituel_type=type_rituel, dernier_outil=type_rituel)
    log_admin_action("send-rituel", phone, detail=type_rituel)
    return jsonify({"ok":True})

@app.route("/admin/send", methods=["POST"])
@require_csrf
def admin_send():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    data = request.get_json()
    phone = data.get("phone",""); message = data.get("message","")
    if not phone or not message: return jsonify({"ok":False})
    send_message(phone, message)
    add_message(phone, "assistant", message)
    log_admin_action("send", phone, detail=message[:200])
    return jsonify({"ok":True})

@app.route("/admin/export-data", methods=["POST"])
@require_csrf
def admin_export_data():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    phone = request.args.get("phone","")
    if not phone: return jsonify({"error":"phone required"}), 400
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE phone = %s", (phone,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({"error":"user not found"}), 404
    cols = [desc[0] for desc in c.description]
    user_data = dict(zip(cols, row))
    c.execute("SELECT role, content, timestamp FROM messages WHERE phone = %s ORDER BY timestamp ASC", (phone,))
    messages_data = [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in c.fetchall()]
    conn.close()
    log_admin_action("export-data", phone)
    payload = {"user": user_data, "messages": messages_data}
    response = app.response_class(
        response=__import__("json").dumps(payload, ensure_ascii=False, indent=2),
        status=200,
        mimetype="application/json"
    )
    response.headers["Content-Disposition"] = f"attachment; filename=export_{phone}.json"
    return response

@app.route("/admin/delete-user", methods=["POST"])
@require_csrf
def admin_delete_user():
    if not admin_auth(): return jsonify({"error":"unauthorized"}), 401
    phone = request.args.get("phone","")
    body = request.get_json(silent=True) or {}
    phone_confirm = body.get("phone_confirm","")
    if not phone or not phone_confirm:
        return jsonify({"error":"phone required"}), 400
    if phone_confirm != phone:
        return jsonify({"error":"phone mismatch"}), 400
    log_admin_action("delete-user", phone)
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE phone = %s", (phone,))
    c.execute("DELETE FROM users WHERE phone = %s", (phone,))
    conn.commit()
    conn.close()
    return jsonify({"ok":True})

@app.route("/admin/purge-anciens", methods=["POST"])
@require_csrf
def admin_purge_anciens():
    if not admin_auth(): return jsonify({"error": "unauthorized"}), 401
    body    = request.get_json(silent=True) or {}
    confirm = str(body.get("confirm", "")).lower() == "true"
    cutoff  = (datetime.now() - timedelta(days=365)).isoformat()

    conn = get_conn()
    c    = conn.cursor()
    c.execute("""
        SELECT phone, prenom, date_dernier_contact
        FROM users
        WHERE abonne = FALSE
          AND etat = 'pause'
          AND (
              date_dernier_contact IS NULL
              OR date_dernier_contact = ''
              OR (
                  date_dernier_contact ~ E'^\\d{4}-\\d{2}-\\d{2}'
                  AND date_dernier_contact::timestamp < %s::timestamp
              )
          )
    """, (cutoff,))
    candidates = [{"phone": r[0], "prenom": r[1] or "", "last_contact": r[2] or ""}
                  for r in c.fetchall()]

    if not confirm:
        conn.close()
        return jsonify({"mode": "dry-run", "count": len(candidates),
                        "candidates": candidates}), 200

    if len(candidates) > 50:
        conn.close()
        return jsonify({
            "error": "batch_trop_grand",
            "message": f"{len(candidates)} users éligibles — limite 50 par exécution. Affinez les critères ou traitez en plusieurs passes.",
            "count": len(candidates)
        }), 400

    _ANON = ["prenom", "email", "prenoms_importants", "theme_dominant",
             "douleur_principale", "peur_dominante", "dernier_sujet_sensible",
             "derniere_intention", "profil_initial", "onboarding_question"]
    sets  = ", ".join(f"{f}=''" for f in _ANON)
    for row in candidates:
        p = row["phone"]
        c.execute(f"UPDATE users SET {sets} WHERE phone = %s", (p,))
        c.execute("DELETE FROM messages WHERE phone = %s", (p,))
        log_admin_action("purge-anon", p, detail="anonymisé + messages supprimés")
    conn.commit()
    conn.close()
    log_admin_action("purge-anciens", "",
                     detail=f"{len(candidates)} users anonymisés (cutoff={cutoff[:10]})")
    return jsonify({"mode": "executed", "anonymized": len(candidates),
                    "phones": [r["phone"] for r in candidates]}), 200

@app.route("/admin/login", methods=["GET","POST"])
@limiter.limit("3 per 15 minutes")
def admin_login():
    error = ""
    if request.method == "POST":
        if request.form.get("password","") == ADMIN_PASSWORD:
            session["admin_logged"] = True
            session["csrf_token"] = secrets.token_hex(32)
            session.permanent = True
            return redirect("/admin")
        error = "Mot de passe incorrect"
    return f"""<!DOCTYPE html><html><head><title>Auryel Admin</title>
<link href='https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400&family=DM+Sans:wght@300;400;500&display=swap' rel='stylesheet'>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'DM Sans',sans-serif;background:#0a0a0f;color:#e8e0d0;min-height:100vh;display:flex;align-items:center;justify-content:center}}
.box{{background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:20px;padding:48px 40px;width:100%;max-width:380px;text-align:center}}
h1{{font-family:'Cormorant Garamond',serif;font-size:32px;color:#d4a843;margin-bottom:6px;letter-spacing:3px}}.sub{{color:#8a7a6a;margin-bottom:32px;font-size:13px}}
input{{width:100%;background:rgba(255,255,255,0.06);border:1.5px solid rgba(255,255,255,0.12);border-radius:12px;padding:15px;color:#e8e0d0;font-size:16px;outline:none;margin-bottom:14px;text-align:center;letter-spacing:3px}}
input:focus{{border-color:#d4a843}}button{{width:100%;padding:15px;background:linear-gradient(135deg,#b8860b,#d4a843);border:none;border-radius:12px;color:#0a0a0f;font-size:15px;font-weight:600;cursor:pointer}}
.error{{color:#ff6b6b;font-size:13px;margin-bottom:12px}}</style></head>
<body><div class='box'><h1>✦ AURYEL</h1><div class='sub'>ESPACE ADMINISTRATEUR</div>
{"<div class='error'>"+error+"</div>" if error else ""}
<form method='post'><input type='password' name='password' placeholder='••••••••' autofocus><button type='submit'>ACCÉDER</button></form>
</div></body></html>"""

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")



def choisir_message_relance(user, moment):
    categorie = user.get("categorie_principale") or "besoin_guidance"
    date_pc = user.get("date_premier_contact", "")
    try:
        jour = (datetime.now() - datetime.fromisoformat(date_pc)).days + 1
    except Exception:
        jour = 1
    jour = min(max(jour, 1), 30)
    deja = set((user.get("relances_ids_envoyes") or "").split(","))
    candidats = [m for m in RELANCES_H4_H22 if m["situation"] == categorie and m["moment"] == moment and m["jour"] == jour and m["id"] not in deja]
    if not candidats:
        candidats = [m for m in RELANCES_H4_H22 if m["situation"] == categorie and m["moment"] == moment and m["id"] not in deja]
    if not candidats:
        candidats = [m for m in RELANCES_H4_H22 if m["moment"] == moment and m["id"] not in deja]
    if not candidats:
        return None
    return random.choice(candidats)


def cron_relances_intraday():
    """Vérifie toutes les heures si H+4 ou H+22 doit être envoyé."""
    now = datetime.now()

    from zoneinfo import ZoneInfo
    now_paris = datetime.now(ZoneInfo("Europe/Paris"))
    if now_paris.hour < 8 or now_paris.hour >= 22:
        print(f"[h4/h22] hors plage horaire ({now_paris.hour}h) — skip")
        return

    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT phone FROM users")
        phones = [r[0] for r in c.fetchall()]
        conn.close()
    except Exception as e:
        print(f"[h4/h22] erreur DB : {e}")
        return

    for phone in phones:
        try:
            user = get_user(phone)
            if not user:
                continue
            if user.get("stop_relances"):
                continue
            _bloque, _raison = _detresse_bloque_marketing(user)
            if _bloque:
                log_event("skip_relance_detresse", phone_hash=_phone_hash(phone), cron="intraday", raison=_raison)
                continue
            if not user.get("onboarding_done"):
                continue

            dc = user.get("date_dernier_contact", "")
            if not dc:
                continue

            try:
                t0 = datetime.fromisoformat(dc)
            except Exception:
                continue

            secs_since = (now - t0).total_seconds()

            # Fenêtre 24h fermée — pas de message libre possible
            if secs_since >= 86400:
                continue

            last_h4 = user.get("last_h4_relance_at", "")
            last_h22 = user.get("last_h22_relance_at", "")

            # H+4 : entre 4h et 22h après le dernier message utilisateur
            if secs_since >= 14400 and secs_since < 79200:
                h4_already = False
                if last_h4:
                    try:
                        if datetime.fromisoformat(last_h4) >= t0:
                            h4_already = True
                    except Exception:
                        pass
                if not h4_already:
                    relance = choisir_message_relance(user, "h4")
                    if not relance:
                        print(f"[h4] aucun message dispo pour {_phone_hash(phone)}")
                        continue
                    msg = relance["message"]
                    try:
                        r = send_message(phone, msg)
                        if r and r.status_code in (200, 201):
                            add_message(phone, "assistant", msg)
                            deja = user.get("relances_ids_envoyes") or ""
                            nouveaux = (deja + "," + relance["id"]).strip(",")
                            update_user_silent(phone, last_h4_relance_at=now.isoformat(), relances_ids_envoyes=nouveaux)
                            log_event("h4_relance_sent", phone_hash=_phone_hash(phone))
                            print(f"[h4] envoye -> {phone}")
                    except Exception as e:
                        print(f"[h4] erreur envoi {phone} : {e}")
                    continue

            # H+22 : entre 22h et 24h après le dernier message utilisateur
            if secs_since >= 79200 and secs_since < 86400:
                h22_already = False
                if last_h22:
                    try:
                        if datetime.fromisoformat(last_h22) >= t0:
                            h22_already = True
                    except Exception:
                        pass
                if not h22_already:
                    relance = choisir_message_relance(user, "h22")
                    if not relance:
                        print(f"[h22] aucun message dispo pour {_phone_hash(phone)}")
                        continue
                    msg = relance["message"]
                    try:
                        r = send_message(phone, msg)
                        if r and r.status_code in (200, 201):
                            add_message(phone, "assistant", msg)
                            deja = user.get("relances_ids_envoyes") or ""
                            nouveaux = (deja + "," + relance["id"]).strip(",")
                            update_user_silent(phone, last_h22_relance_at=now.isoformat(), relances_ids_envoyes=nouveaux)
                            log_event("h22_relance_sent", phone_hash=_phone_hash(phone))
                            print(f"[h22] envoye -> {phone}")
                    except Exception as e:
                        print(f"[h22] erreur envoi {phone} : {e}")

        except Exception as e:
            print(f"[h4/h22] erreur user {phone} : {e}")

DAILY_LIMIT = 150

def check_and_increment_daily_limit(phone, user):
    """Vérifie le plafond 150 messages/jour. Retourne True si limite atteinte."""
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    count = user.get("messages_today_count") or 0
    last_date = user.get("messages_today_date") or ""

    if last_date != today:
        # Nouveau jour — reset
        update_user_silent(phone, messages_today_count=1, messages_today_date=today)
        return False

    if count >= DAILY_LIMIT:
        log_event("daily_limit_reached", phone_hash=_phone_hash(phone))
        return True

    update_user_silent(phone, messages_today_count=count + 1)
    return False

# Démarrage APScheduler
scheduler = BackgroundScheduler(timezone="Europe/Paris")
scheduler.add_job(cron_relances_intraday, 'interval', hours=1, id='relances_intraday')
scheduler.start()
import atexit
atexit.register(lambda: scheduler.shutdown())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

# ============================================================
# SEO AUTO-PUBLISH — génère + publie 1 article/jour
# ============================================================

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "tanouch24/auryel-1")
SITE_BASE    = "https://auryelvoyance.com"

if not GITHUB_TOKEN:
    print("[WARNING] GITHUB_TOKEN absent — /cron/seo-publish échouera silencieusement au moment du push GitHub")

if not os.environ.get("INDEXNOW_KEY"):
    print("[WARNING] INDEXNOW_KEY absent — ping IndexNow ignoré silencieusement après chaque publication SEO")

SEO_KEYWORDS = [
    {"slug":"voyance-gratuite-sans-cb",       "kw":"voyance gratuite sans cb",            "cat":"guide",       "cat_label":"Guide"},
    {"slug":"medium-serieux-fiable",           "kw":"médium sérieux fiable",               "cat":"guide",       "cat_label":"Guide"},
    {"slug":"tirage-tarot-couple",             "kw":"tirage tarot couple",                 "cat":"tarot",       "cat_label":"Tarot"},
    {"slug":"horoscope-semaine-gratuit",       "kw":"horoscope semaine gratuit",           "cat":"astrologie",  "cat_label":"Astrologie"},
    {"slug":"voyance-amour-ex-reviendra",      "kw":"voyance amour retour ex",             "cat":"amour",       "cat_label":"Amour"},
    {"slug":"tarot-oui-ou-non-marseille",      "kw":"tarot oui ou non",                    "cat":"tarot",       "cat_label":"Tarot"},
    {"slug":"numerologie-chemin-vie-8",        "kw":"chemin de vie numérologie 8",         "cat":"numerologie", "cat_label":"Numérologie"},
    {"slug":"compatibilite-scorpion-cancer",   "kw":"compatibilité Scorpion Cancer",       "cat":"astrologie",  "cat_label":"Astrologie"},
    {"slug":"reves-serpent-signification",     "kw":"rêver de serpent signification",      "cat":"spiritualite","cat_label":"Spiritualité"},
    {"slug":"voyance-par-email-serieuse",      "kw":"voyance par email sérieuse",          "cat":"guide",       "cat_label":"Guide"},
    {"slug":"oracle-belline-signification",    "kw":"oracle de Belline signification",     "cat":"tarot",       "cat_label":"Tarot"},
    {"slug":"labradorite-vertus-protection",   "kw":"labradorite vertus protection",       "cat":"spiritualite","cat_label":"Spiritualité"},
    {"slug":"compatibilite-balance-gemeaux",   "kw":"compatibilité Balance Gémeaux",       "cat":"astrologie",  "cat_label":"Astrologie"},
    {"slug":"voyance-travail-reconversion",    "kw":"voyance travail reconversion",        "cat":"carriere",    "cat_label":"Carrière"},
    {"slug":"numerologie-chemin-vie-7",        "kw":"chemin de vie 7 numérologie",         "cat":"numerologie", "cat_label":"Numérologie"},
    {"slug":"tirage-croix-celtique-amour",     "kw":"tirage croix celtique amour",         "cat":"tarot",       "cat_label":"Tarot"},
    {"slug":"reves-dents-tombent-sens",        "kw":"rêver que ses dents tombent",         "cat":"spiritualite","cat_label":"Spiritualité"},
    {"slug":"lune-noire-lilith-astrologie",    "kw":"Lune Noire Lilith astrologie",        "cat":"astrologie",  "cat_label":"Astrologie"},
    {"slug":"cristaux-amour-rose-rhodonite",   "kw":"cristaux pour attirer l'amour",       "cat":"spiritualite","cat_label":"Spiritualité"},
    {"slug":"voyance-audiotel-serieuse",       "kw":"voyance audiotel sérieuse",           "cat":"guide",       "cat_label":"Guide"},
    {"slug":"numerologie-chemin-vie-11",       "kw":"chemin de vie 11 maître",             "cat":"numerologie", "cat_label":"Numérologie"},
    {"slug":"compatibilite-lion-verseau",      "kw":"compatibilité Lion Verseau",          "cat":"astrologie",  "cat_label":"Astrologie"},
    {"slug":"tirage-tarot-gratuit-amour",      "kw":"tirage tarot gratuit amour",          "cat":"tarot",       "cat_label":"Tarot"},
    {"slug":"medium-whatsapp-disponible",      "kw":"médium disponible WhatsApp",          "cat":"guide",       "cat_label":"Guide"},
    {"slug":"chakra-coeur-blocage-amour",      "kw":"chakra du coeur blocage amour",       "cat":"spiritualite","cat_label":"Spiritualité"},
    {"slug":"previsions-amour-2026",           "kw":"prévisions amour 2026",               "cat":"amour",       "cat_label":"Amour"},
    {"slug":"tourmaline-noire-utilisation",    "kw":"tourmaline noire comment utiliser",   "cat":"spiritualite","cat_label":"Spiritualité"},
    {"slug":"voyance-chat-immediat",           "kw":"voyance par chat immédiat",           "cat":"guide",       "cat_label":"Guide"},
    {"slug":"jupiter-cancer-chance-2026",      "kw":"Jupiter Cancer chance 2026",          "cat":"astrologie",  "cat_label":"Astrologie"},
    {"slug":"retour-etre-aime-spirituel",      "kw":"retour être aimé spirituel",          "cat":"amour",       "cat_label":"Amour"},
]

UNSPLASH_CAT = {
    "guide":        "photo-1518611012118-696072aa579a",
    "amour":        "photo-1518199266791-5375a83190b7",
    "tarot":        "photo-1604085792867-8f0de9f6bbd1",
    "astrologie":   "photo-1534796636912-3b95b3ab5986",
    "numerologie":  "photo-1509228468518-180dd4864904",
    "spiritualite": "photo-1518241353330-0f7941c2d9b5",
    "carriere":     "photo-1611974789855-9c2a0a7236a3",
}


def get_today_keyword():
    day_of_year = date.today().timetuple().tm_yday
    idx = day_of_year % len(SEO_KEYWORDS)
    return SEO_KEYWORDS[idx]


def md_to_html(text):
    html = ""
    for line in text.split("\n"):
        l = line.strip()
        if not l:
            continue
        elif l.startswith("## "):
            h = re.sub(r'\*+', '', l[3:])
            html += f'<h2>{h}</h2>\n'
        elif l.startswith("### "):
            h = re.sub(r'\*+', '', l[4:])
            html += f'<h3>{h}</h3>\n'
        elif l.startswith(("- ", "* ")):
            item = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', l[2:])
            html += f'<li>{item}</li>\n'
        elif l.startswith("> "):
            html += f'<div class="hl"><p><em>{l[2:]}</em></p></div>\n'
        else:
            l2 = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', l)
            html += f'<p>{l2}</p>\n'
    html = re.sub(r'(<li>.*?</li>\n)+', lambda m: '<ul>\n' + m.group(0) + '</ul>\n', html, flags=re.DOTALL)
    return html


def generate_article_html(kw_data, article_content):
    slug      = kw_data["slug"]
    cat       = kw_data["cat"]
    cat_label = kw_data["cat_label"]
    img_id    = UNSPLASH_CAT.get(cat, UNSPLASH_CAT["guide"])
    today_str = date.today().strftime("%d %b %Y")

    lines  = article_content.strip().split("\n")
    title  = lines[0].lstrip("#").strip() if lines else kw_data["kw"].title()
    meta   = lines[1].strip() if len(lines) > 1 else f"Tout sur {kw_data['kw']}."
    intro  = lines[2].strip() if len(lines) > 2 else ""
    body_html = md_to_html("\n".join(lines[3:]))

    CSS = """:root{--void:#05040A;--deep:#09070F;--card:#120F1E;--gold:#C8A96E;--gold-b:#E2C98A;--cream:#F0EBE0;--cs:#BDB5A6;--cd:#7A7268;--bdr:rgba(200,169,110,0.1);--bdrm:rgba(200,169,110,0.2)}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}html{scroll-behavior:smooth}
body{background:var(--void);color:var(--cream);font-family:'Raleway',sans-serif;font-weight:300;line-height:1.8;overflow-x:hidden}
nav{position:fixed;top:0;left:0;right:0;z-index:100;display:flex;align-items:center;justify-content:space-between;padding:20px 64px;background:rgba(5,4,10,.92);backdrop-filter:blur(20px);border-bottom:1px solid var(--bdr)}
.n-logo{font-family:'Tenor Sans',sans-serif;font-size:17px;letter-spacing:.5em;color:var(--gold);text-decoration:none;text-transform:uppercase}
.n-links{display:flex;gap:36px;list-style:none;position:absolute;left:50%;transform:translateX(-50%)}
.n-links a{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--cs);text-decoration:none;transition:color .3s}
.n-links a:hover{color:var(--gold-b)}
.n-btn{font-size:10px;font-weight:500;letter-spacing:.2em;text-transform:uppercase;color:var(--void);background:var(--gold);border:none;padding:12px 28px;cursor:pointer;text-decoration:none;display:inline-block}
.art-hero{position:relative;height:60vh;min-height:440px;overflow:hidden;margin-top:61px}
.art-hero-img{width:100%;height:100%;object-fit:cover;filter:brightness(.6) saturate(.85)}
.art-hero-overlay{position:absolute;inset:0;background:linear-gradient(to top,var(--void) 0%,rgba(5,4,10,.2) 60%,transparent 100%)}
.art-hero-content{position:absolute;bottom:0;left:0;right:0;padding:0 64px 52px;max-width:900px}
.art-hero-cat{font-family:'Tenor Sans',sans-serif;font-size:9px;letter-spacing:.5em;text-transform:uppercase;color:var(--gold);margin-bottom:14px;display:block}
.art-hero-title{font-family:'Playfair Display',Georgia,serif;font-size:clamp(28px,4vw,50px);font-weight:400;line-height:1.1;color:var(--cream);margin-bottom:10px}
.art-hero-meta{font-size:12px;color:var(--cd)}
.art-wrap{max-width:760px;margin:0 auto;padding:60px 40px 100px}
.art-intro{font-size:17px;line-height:1.9;color:var(--cs);margin-bottom:44px;padding-bottom:44px;border-bottom:1px solid var(--bdr);font-style:italic}
.art-body h2{font-family:'Playfair Display',Georgia,serif;font-size:clamp(20px,2.8vw,28px);font-weight:400;color:var(--cream);margin:48px 0 14px}
.art-body h3{font-family:'Tenor Sans',sans-serif;font-size:11px;letter-spacing:.35em;text-transform:uppercase;color:var(--gold);margin:28px 0 12px}
.art-body p{font-size:15px;line-height:1.9;color:var(--cs);margin-bottom:18px}
.art-body strong{color:var(--cream);font-weight:500}
.art-body ul{margin:0 0 22px 20px}
.art-body ul li{font-size:15px;line-height:1.9;color:var(--cs);margin-bottom:8px}
.art-body ul li::marker{color:var(--gold)}
.hl{background:var(--card);border:1px solid var(--bdrm);border-left:3px solid var(--gold);padding:24px 28px;margin:32px 0}
.hl p{margin:0;color:var(--cs);font-size:15px;line-height:1.85;font-style:italic}
.art-cta{background:var(--card);border:1px solid var(--bdrm);padding:44px 40px;text-align:center;margin:56px 0 0;position:relative}
.art-cta::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(to right,transparent,var(--gold),transparent)}
.art-cta-label{font-family:'Tenor Sans',sans-serif;font-size:9px;letter-spacing:.5em;text-transform:uppercase;color:var(--gold);margin-bottom:14px;display:block}
.art-cta-title{font-family:'Playfair Display',Georgia,serif;font-size:clamp(20px,3vw,30px);font-weight:400;color:var(--cream);margin-bottom:10px}
.art-cta-sub{font-size:14px;color:var(--cs);margin-bottom:24px;line-height:1.7}
.art-cta-btn{display:inline-block;background:linear-gradient(135deg,var(--gold),var(--gold-b));color:var(--void);padding:15px 44px;font-size:11px;font-weight:500;letter-spacing:.25em;text-transform:uppercase;text-decoration:none}
footer{background:var(--deep);border-top:1px solid var(--bdr);padding:36px 64px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px}
.ft-logo{font-family:'Tenor Sans',sans-serif;font-size:16px;letter-spacing:.5em;color:var(--gold);text-transform:uppercase}
.ft-links{display:flex;gap:20px;flex-wrap:wrap}
.ft-links a{font-size:11px;color:var(--cd);text-decoration:none}
.ft-legal{font-size:10px;color:var(--cd);opacity:.5}
@media(max-width:768px){nav{padding:16px 20px}.n-links{display:none}.art-hero-content{padding:0 20px 32px}.art-wrap{padding:40px 20px 80px}footer{padding:24px 20px;flex-direction:column}}"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{meta}">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{SITE_BASE}/blog/{slug}.html">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{meta}">
<meta property="og:image" content="https://images.unsplash.com/{img_id}?auto=format&fit=crop&w=1200&h=630&q=80">
<meta property="og:type" content="article">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;1,400;1,500&family=Tenor+Sans&family=Raleway:wght@200;300;400;500&display=swap" rel="stylesheet">
<style>{CSS}</style>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Article","headline":"{title}","description":"{meta}","image":"https://images.unsplash.com/{img_id}?auto=format&fit=crop&w=1200&h=630&q=80","author":{{"@type":"Organization","name":"Auryel"}},"publisher":{{"@type":"Organization","name":"Auryel","url":"{SITE_BASE}"}},"datePublished":"{date.today().isoformat()}","url":"{SITE_BASE}/{slug}.html"}}
</script>
</head>
<body>
<nav>
  <a href="/" class="n-logo">Auryel</a>
  <ul class="n-links">
    <li><a href="/index.html">Accueil</a></li>
    <li><a href="/conseillers.html">Conseillers</a></li>
    <li><a href="/tarifs.html">Tarifs</a></li>
    <li><a href="/blog.html">Blog</a></li>
  </ul>
  <a href="/inscription.html" class="n-btn">Essai gratuit</a>
</nav>
<header class="art-hero">
  <img src="https://images.unsplash.com/{img_id}?auto=format&fit=crop&w=1400&h=700&q=80" alt="{title}" class="art-hero-img" loading="eager">
  <div class="art-hero-overlay"></div>
  <div class="art-hero-content">
    <span class="art-hero-cat">✦ {cat_label}</span>
    <h1 class="art-hero-title">{title}</h1>
    <p class="art-hero-meta">Auryel · {cat_label} · {today_str}</p>
  </div>
</header>
<main class="art-wrap">
  <p class="art-intro">{intro}</p>
  <div class="art-body">{body_html}</div>
  <div class="art-cta">
    <span class="art-cta-label">✦ Guidance personnalisée</span>
    <h2 class="art-cta-title">Votre conseiller vous attend</h2>
    <p class="art-cta-sub">3 jours d'essai gratuit. Votre médium disponible sur WhatsApp, 24h/24.</p>
    <a href="/inscription.html" class="art-cta-btn">Commencer gratuitement</a>
    <p style="font-size:11px;color:var(--cd);margin-top:12px">Annulable à tout moment</p>
  </div>
</main>
<footer>
  <span class="ft-logo">Auryel</span>
  <div class="ft-links">
    <a href="/blog.html">Blog</a>
    <a href="/mentions-legales.html">Mentions légales</a>
    <a href="/cgv.html">CGV</a>
    <a href="/confidentialite.html">Confidentialité</a>
  </div>
  <p class="ft-legal">⚠️ Service de divertissement uniquement. © 2026 Auryel.</p>
</footer>
</body>
</html>"""


def github_push_file(filename, content, commit_msg):
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN manquant"
    import base64
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    sha = None
    r = requests.get(api_url, headers=headers)
    if r.status_code == 200:
        sha = r.json().get("sha")
    payload = {
        "message": commit_msg,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": "main"
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(api_url, headers=headers, json=payload)
    if r.status_code in (200, 201):
        return True, r.json().get("commit", {}).get("sha", "ok")
    return False, f"GitHub API {r.status_code}: {r.text[:200]}"


def build_sitemap(extra_urls):
    static_urls = [
        f"{SITE_BASE}/", f"{SITE_BASE}/conseillers.html", f"{SITE_BASE}/tarifs.html",
        f"{SITE_BASE}/comment-ca-marche.html", f"{SITE_BASE}/blog.html", f"{SITE_BASE}/inscription.html",
        f"{SITE_BASE}/voyance-serieuse-en-ligne.html", f"{SITE_BASE}/retour-affectif-voyance.html",
        f"{SITE_BASE}/oracle-gratuit-en-ligne.html", f"{SITE_BASE}/avenir-sentimental-gratuit.html",
        f"{SITE_BASE}/voyance-argent-et-finances.html", f"{SITE_BASE}/que-signifie-rever-de-mort.html",
        f"{SITE_BASE}/tarot-de-marseille-en-ligne.html", f"{SITE_BASE}/pierre-de-protection-esoterisme.html",
        f"{SITE_BASE}/voyance-par-chat-sans-attente.html", f"{SITE_BASE}/previsions-astrologiques-2026.html",
        f"{SITE_BASE}/calcul-numerologie-prenom.html", f"{SITE_BASE}/ascendant-astrologique.html",
        f"{SITE_BASE}/interpretation-reves-symbolique.html", f"{SITE_BASE}/theme-astral-gratuit.html",
        f"{SITE_BASE}/medium-gratuit-premiere-consultation.html", f"{SITE_BASE}/signification-des-lames-du-tarot.html",
        f"{SITE_BASE}/voyance-tchat-en-ligne.html", f"{SITE_BASE}/chakras-et-energie.html",
        f"{SITE_BASE}/voyance-amour-gratuite.html", f"{SITE_BASE}/reves-et-leur-signification.html",
        f"{SITE_BASE}/numerologie-date-naissance-chemin-de-vie.html", f"{SITE_BASE}/tarot-amour-gratuit-en-ligne.html",
        f"{SITE_BASE}/mon-ex-va-t-il-revenir-voyance.html", f"{SITE_BASE}/comment-choisir-son-voyant-en-ligne.html",
        f"{SITE_BASE}/compatibilite-amoureuse-signe-astrologique.html", f"{SITE_BASE}/signification-reves-voyance.html",
        f"{SITE_BASE}/voyance-avenir-professionnel.html", f"{SITE_BASE}/tarot-oui-non-reponse-immediate.html",
    ]
    all_urls = list(dict.fromkeys(static_urls + extra_urls))
    today = date.today().isoformat()
    entries = ""
    for url in all_urls:
        priority = "1.0" if url == f"{SITE_BASE}/" else "0.8" if "blog" in url else "0.7"
        entries += f"  <url>\n    <loc>{url}</loc>\n    <lastmod>{today}</lastmod>\n    <changefreq>weekly</changefreq>\n    <priority>{priority}</priority>\n  </url>\n"
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}</urlset>'


def ping_indexnow(url):
    key = os.environ.get("INDEXNOW_KEY", "")
    payload = {"host": "www.auryelvoyance.com", "key": key, "urlList": [url]}
    for endpoint in ["https://api.indexnow.org/indexnow", "https://www.bing.com/indexnow"]:
        try:
            requests.post(endpoint, json=payload, timeout=5)
        except Exception:
            pass


def send_seo_recap_email(title, kw, article_url):
    if not RESEND_API_KEY:
        return
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({
            "from": f"Auryel SEO <{FROM_EMAIL}>",
            "to": ["contact@auryelvoyance.com"],
            "subject": f"✅ Article SEO publié : {title}",
            "html": f"""<html><body style="background:#05040A;color:#F0EBE0;font-family:Georgia,serif;padding:40px">
<h2 style="color:#C8A96E">✅ Article publié automatiquement</h2>
<p style="color:#BDB5A6;margin:12px 0"><b style="color:#F0EBE0">Titre :</b> {title}</p>
<p style="color:#BDB5A6;margin:8px 0"><b style="color:#F0EBE0">Mot-clé :</b> {kw}</p>
<p style="color:#BDB5A6;margin:8px 0"><b style="color:#F0EBE0">URL :</b> <a href="{article_url}" style="color:#C8A96E">{article_url}</a></p>
<p style="color:#BDB5A6;margin:8px 0"><b style="color:#F0EBE0">Date :</b> {date.today().isoformat()}</p>
<p style="color:#BDB5A6;margin:20px 0 8px">Sitemap + IndexNow mis à jour automatiquement.</p>
</body></html>"""
        })
    except Exception as e:
        print(f"[SEO] Email error: {e}")


@app.route("/cron/seo-publish", methods=["POST"])
def cron_seo_publish():
    """
    Appelée chaque jour à 9h par Make.com.
    Génère + publie 1 article SEO automatiquement.
    Variables Railway requises : GITHUB_TOKEN, GITHUB_REPO
    """
    body = request.get_json(silent=True) or {}
    if not hmac.compare_digest(body.get("secret", ""), SEO_SECRET or ""):
        return jsonify({"error": "unauthorized"}), 401

    print("[SEO] Démarrage publication automatique...")

    # 1. Mot-clé du jour
    kw_data = get_today_keyword()
    slug    = kw_data["slug"]
    kw      = kw_data["kw"]
    cat     = kw_data["cat_label"]

    print(f"[SEO] Mot-clé du jour : {kw} → {slug}.html")

    # 2. Générer le contenu avec Groq
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Tu es un expert SEO et rédacteur web spécialisé en voyance, tarot, numérologie et spiritualité. "
                        "Tu rédiges des articles de blog optimisés SEO en français, naturels et engageants. "
                        "Format de réponse STRICT — respecte exactement cette structure ligne par ligne :\n"
                        "Ligne 1 : Titre H1 (sans # ni balises, mot-clé principal dedans)\n"
                        "Ligne 2 : Meta description (155 chars max, mot-clé dedans)\n"
                        "Ligne 3 : Phrase d'introduction (2-3 phrases accrocheuses)\n"
                        "Lignes suivantes : Corps en markdown. ## pour H2, ### pour H3, - pour listes, > pour citations.\n"
                        "5 à 7 sections H2. 800 mots minimum. Ton chaleureux et expert. Pas de commentaires méta."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Écris un article SEO complet sur : \"{kw}\"\n"
                        f"Catégorie : {cat}\n"
                        f"Le mot-clé doit apparaître dans le H1, la meta, le 1er paragraphe et 2-3 H2.\n"
                        f"Inclus une citation inspirante (format > texte) et un appel naturel vers la consultation."
                    )
                }
            ],
            max_tokens=2000,
            temperature=0.75
        )
        article_content = response.choices[0].message.content
        print(f"[SEO] Contenu généré : {len(article_content)} chars")
    except Exception as e:
        print(f"[SEO] Erreur Groq : {e}")
        return jsonify({"error": f"Groq error: {str(e)}"}), 500

    # 3. Construire le HTML complet
    article_html = generate_article_html(kw_data, article_content)
    article_url  = f"{SITE_BASE}/blog/{slug}.html"

    # Extraire le titre pour l'email
    lines = article_content.strip().split("\n")
    title = lines[0].lstrip("#").strip() if lines else kw.title()

    # 4. Pusher l'article sur GitHub → Netlify redéploie auto
    ok, msg = github_push_file(
        f"blog/{slug}.html",
        article_html,
        f"✨ SEO auto: {kw} [{date.today().isoformat()}]"
    )
    if not ok:
        print(f"[SEO] GitHub push échoué : {msg}")
        return jsonify({"error": f"GitHub push failed: {msg}"}), 500
    print(f"[SEO] Article pushé sur GitHub : {msg}")

    # 5. Mettre à jour sitemap.xml
    new_sitemap = build_sitemap([article_url])
    ok_sm, _    = github_push_file(
        "sitemap.xml",
        new_sitemap,
        f"🗺️ Sitemap +{slug} [{date.today().isoformat()}]"
    )
    print(f"[SEO] Sitemap : {'✅' if ok_sm else '❌'}")

    # 7. Ping IndexNow → Google + Bing
    ping_indexnow(article_url)
    print(f"[SEO] IndexNow pingé")

    # 8. Email récap
    send_seo_recap_email(title, kw, article_url)

    return jsonify({
        "status":  "ok",
        "slug":    slug,
        "keyword": kw,
        "title":   title,
        "url":     article_url,
        "sitemap": ok_sm,
        "date":    date.today().isoformat()
    }), 200
