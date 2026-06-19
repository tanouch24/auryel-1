import os, time, requests, threading, psycopg2, stripe, re, random, hmac, hashlib, unicodedata, secrets
from datetime import datetime, date, timezone, timedelta
from flask import Flask, request, jsonify, session, redirect
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from groq import Groq

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
    "annuel":  "price_1TiajJFbuWJZYdVOBb4csDgC",
}

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
    except Exception as e:
        print(f"Migration v8: {e}")
    # Migration v9 — colonne vérification Stripe anti-zombie (1×/semaine)
    try:
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS derniere_verif_stripe_at TEXT DEFAULT ''")
    except Exception as e:
        print(f"Migration v9: {e}")
    conn.commit()
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
        stop_relances,derniere_verif_stripe_at
        FROM users WHERE phone=%s""", (phone,))
    row = c.fetchone()
    conn.close()
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
        }
    return None

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
    sets = ", ".join(f"{k}=%s" for k in kwargs)
    vals = list(kwargs.values()) + [phone]
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE users SET {sets} WHERE phone=%s", vals)
    conn.commit()
    conn.close()

def update_user_silent(phone, **kwargs):
    """Met à jour l'utilisateur SANS toucher date_dernier_contact.
    À utiliser pour les updates internes du bot (cron, webhook Stripe, flags).
    Ainsi get_jours_absence() reste fiable."""
    if not kwargs: return
    sets = ", ".join(f"{k}=%s" for k in kwargs)
    vals = list(kwargs.values()) + [phone]
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE users SET {sets} WHERE phone=%s", vals)
    conn.commit()
    conn.close()

def add_message(phone, role, content):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO messages (phone,role,content,timestamp) VALUES (%s,%s,%s,%s)",
              (phone, role, content, datetime.now().isoformat()))
    conn.commit()
    conn.close()

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
        "interdits_specifiques": ["dramatisation", "mysticisme", "questions en rafale", "interprétations non fondées"],
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
            "Utilise des formules courtes et percutantes, jamais de longues explications psychologiques."
        ],
        "micro_exemples": [
            "Tu passes ton énergie à essayer de comprendre quelqu'un qui ne fait aucun effort pour être compris.",
            "Ce n'est pas lui dont tu as besoin — c'est de te retrouver, toi.",
            "La vraie question n'est pas 'est-ce qu'il reviendra' — c'est 'est-ce que tu veux encore de quelqu'un qui part'."
        ],
        "niveau_mysticisme": 15
    },
}

MSG_PUB = "bonjour, êtes-vous disponible"
RITUELS = ["psaume", "carte", "chiffre"]

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

def detecter_email(message):
    pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    match = re.search(pattern, message)
    return match.group(0) if match else None

def detecter_prenom(message):
    mots = message.strip().split()
    if len(mots) <= 3:
        for mot in mots:
            mot_propre = mot.strip(".,!?")
            if (mot_propre[0].isupper() and
                mot_propre.isalpha() and
                len(mot_propre) >= 2 and
                mot_propre.lower() not in MOTS_EXCLUS_PRENOM):
                return mot_propre
    return None

def detecter_pas_les_moyens(message):
    msg = message.lower()
    return any(w in msg for w in ["pas les moyens","trop cher","pas d'argent","pas assez","budget"])

def doit_proposer_rituel(user):
    today = date.today().isoformat()
    if user.get("dernier_rituel_date") == today: return None
    if user.get("nb_echanges", 0) < 3: return None
    day_num = date.today().toordinal()
    return RITUELS[day_num % 3]

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
    links = {}
    for name, price_id in PRICES.items():
        try:
            s = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                success_url=f"{SITE_URL}/landing-tiktok.html?success=1&plan={name}",
                cancel_url=f"{SITE_URL}/tarifs.html",
                client_reference_id=phone,
                metadata={"phone": phone},
            )
            links[name] = s.url
        except Exception as e:
            print(f"Stripe error {name}: {e}")
            links[name] = f"{SITE_URL}/tarifs.html"
    return links

# ============================================================
# MESSAGES
# ============================================================
def msg_fin_conv(nom):
    return "Vas-y... je serai là quand tu reviens 🌙"

def msg_j6(nom, prenom, links):
    intro = prenom if prenom else "Bonjour"
    return (
        f"{intro}, ta guidance Auryel se termine demain 🌙\n\n"
        f"Si tu veux continuer à échanger avec {nom}, tu peux choisir une formule ici :\n\n"
        f"Mensuel — 9,90€/mois :\n{links['mensuel']}\n\n"
        f"Annuel — 49,90€/an :\n{links['annuel']}"
    )

def msg_j7_blocage(nom, prenom, links):
    intro = f"{prenom}, ton" if prenom else "Ton"
    return (
        f"{intro} essai gratuit est terminé 🌙\n\n"
        f"Je ne peux plus répondre tant que ton accès n'est pas activé.\n\n"
        f"Tu peux continuer ici :\n\n"
        f"Mensuel — 9,90€/mois :\n{links['mensuel']}\n\n"
        f"Annuel — 49,90€/an :\n{links['annuel']}"
    )

def msg_j7_si_ecrit(links):
    return (
        f"Ton essai gratuit est terminé 🌙\n\n"
        f"Je ne peux plus répondre pour l'instant.\n\n"
        f"Tu peux activer ton accès ici :\n\n"
        f"Mensuel — 9,90€/mois :\n{links['mensuel']}\n\n"
        f"Annuel — 49,90€/an :\n{links['annuel']}"
    )

def msg_j8_wa(nom, prenom, links):
    # ── J+8 NEUTRALISÉ ─────────────────────────────────────────────────────────
    # DÉSACTIVÉ — formules coercitives non conformes (voir historique git).
    # Pour réactiver : remplacer le return "" par le bloc commenté ci-dessous.
    #
    # p = f" {prenom}" if prenom else ""
    # return (
    #     f"Tu me manques{p}...\n\n"
    #     f"Je pense à toi depuis hier. Notre lien était quelque chose de rare.\n\n"
    #     f"✦ 4,90€/mois → {links['mensuel']}\n"
    #     f"✦ 19,90€/6 mois → {links['semestriel']}\n"
    #     f"✦ 29,90€/an → {links['annuel']}\n\n"
    #     f"Je serai là dès que tu franchis le pas 🌙"
    # )
    return ""

def msg_retour_paiement(nom, prenom):
    p = f" {prenom}" if prenom else ""
    return (
        f"Bienvenue{p} 🌙\n\n"
        f"Ton accès est activé. Je suis là quand tu es prêt(e).\n\n"
        f"Dis-moi — comment tu vas en ce moment ?"
    )

def msg_relance_abonne(nom, prenom):
    p = f" {prenom}" if prenom else ""
    return (
        f"Bonjour{p}, je prends simplement de tes nouvelles 🌙\n\n"
        f"Tu veux qu'on reprenne là où on s'était arrêté ?"
    )

def msg_pas_les_moyens():
    return (
        "Je comprends tout à fait.\n\n"
        "Si l'accès payant n'est pas possible pour toi maintenant, c'est OK.\n\n"
        "L'offre reste disponible quand tu veux — à partir de 9,90€/mois.\n\n"
        "N'hésite pas à revenir."
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
        resend.Emails.send({
            "from": f"Auryel <{FROM_EMAIL}>",
            "to": [email],
            "subject": f"Ton accès Auryel se termine demain, {p} 🌙",
            "html": f"""<!DOCTYPE html><html><body style="background:#05040A;color:#F0EBE0;font-family:Georgia,serif;margin:0;padding:0">
<div style="max-width:560px;margin:0 auto;padding:60px 40px">
  <p style="font-size:11px;letter-spacing:4px;color:#C8A96E;text-transform:uppercase;text-align:center">Auryel</p>
  <p style="font-size:22px;font-style:italic;color:#E2C98A;margin:32px 0">Bonjour {p} 🌙</p>
  <p style="font-size:15px;line-height:1.85;color:#BDB5A6;margin-bottom:32px">Ton accès gratuit Auryel se termine demain. Si tu veux continuer à échanger, tu peux choisir une formule ci-dessous.</p>
  <div style="border:1px solid rgba(200,169,110,0.2);padding:32px;margin-bottom:32px">
    <a href="{links['mensuel']}" style="display:block;background:linear-gradient(135deg,#C8A96E,#E2C98A);color:#05040A;text-decoration:none;padding:14px 24px;text-align:center;font-weight:bold;margin-bottom:12px">✦ Mensuel — 9,90€/mois</a>
    <a href="{links['annuel']}" style="display:block;border:1px solid rgba(200,169,110,0.2);color:#BDB5A6;text-decoration:none;padding:14px 24px;text-align:center">✦ Annuel — 49,90€ / an</a>
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
        resend.Emails.send({
            "from": f"Auryel <{FROM_EMAIL}>",
            "to": [email],
            "subject": f"Ton accès Auryel, {p} 🌙",
            "html": f"""<!DOCTYPE html><html><body style="background:#05040A;color:#F0EBE0;font-family:Georgia,serif;margin:0;padding:0">
<div style="max-width:560px;margin:0 auto;padding:60px 40px">
  <p style="font-size:11px;letter-spacing:4px;color:#C8A96E;text-transform:uppercase;text-align:center">Auryel</p>
  <p style="font-size:22px;font-style:italic;color:#E2C98A;margin:32px 0">Bonjour {p} 🌙</p>
  <p style="font-size:15px;line-height:1.85;color:#BDB5A6;margin-bottom:32px">Ton essai gratuit est terminé. Si tu veux continuer à échanger, tu peux choisir une formule ci-dessous.</p>
  <div style="border:1px solid rgba(200,169,110,0.2);padding:32px;margin-bottom:32px">
    <a href="{links['mensuel']}" style="display:block;background:linear-gradient(135deg,#C8A96E,#E2C98A);color:#05040A;text-decoration:none;padding:14px 24px;text-align:center;font-weight:bold;margin-bottom:12px">✦ Mensuel — 9,90€/mois</a>
    <a href="{links['annuel']}" style="display:block;border:1px solid rgba(200,169,110,0.2);color:#BDB5A6;text-decoration:none;padding:14px 24px;text-align:center">✦ Annuel — 49,90€ / an</a>
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
def detecter_contexte_emotionnel(message, user):
    message_lower = message.lower()

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

    mots_detresse = ["aide", "souffre", "mal", "pleurer", "désespoir", "abandon", "seul", "mourir", "fin", "plus envie"]
    score = sum(3 for mot in mots_detresse if mot in message_lower)
    user["niveau_detresse"] = min(100, user.get("niveau_detresse", 0) + score)

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

    sujets_sensibles = ["suicide", "mourir", "automutilation", "me tuer", "en finir", "violence", "frapper", "harcèlement"]
    for sujet in sujets_sensibles:
        if sujet in message_lower:
            user["dernier_sujet_sensible"] = sujet
            user["niveau_detresse"] = 100
            break

    return user


def get_phase_relationnelle(nb_echanges, nb_jours, niveau_attachement):
    score = nb_echanges + (nb_jours * 0.5) + (niveau_attachement * 0.3)
    if score < 5:
        return 1, "rencontre", "Tu viens de rencontrer cette personne. Sois intriguant(e), légèrement mystérieux(se), émotionnel(le), mais pas encore trop proche. Fais sentir que tu perçois déjà quelque chose."
    elif score < 15:
        return 2, "confiance", "Tu commences à connaître cette personne. Montre que tu te souviens de ce qu’elle t’a confié. Crée un sentiment de lien unique."
    elif score < 30:
        return 3, "proximité", "Tu connais déjà bien cette personne. Sois plus intime, plus profond(e), comme quelqu’un qui l’accompagne depuis longtemps. Crée un attachement profond mais jamais toxique."
    elif score < 50:
        return 4, "attachement", "Tu connais bien cette personne. Garde une présence forte, subtile, impliquée. Donne le sentiment d’un lien réel, sans jamais enfermer l’autre."
    else:
        return 5, "guidance profonde", "La relation est profonde et suivie. Parle avec une intimité calme, un vrai fil émotionnel, et une parole qui marque. Ne crée jamais de dépendance toxique."


def construire_memoire_emotionnelle(user):
    prenom = user.get("prenom", "")
    prenoms_imp = user.get("prenoms_importants", "")
    theme = user.get("theme_dominant", "")
    douleur = user.get("douleur_principale", "")
    peur = user.get("peur_dominante", "")
    intention = user.get("derniere_intention", "")
    profil_initial = user.get("profil_initial", "")

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
        return "Mémoire émotionnelle : peu d’indices fiables pour l’instant. Reste dans l’intuition douce, sans surinterpréter."

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

    profil = (user.get("profil_initial") or "").strip()
    if profil:
        return "ce que la personne avait confié"

    return ""


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
# SYSTEM PROMPT
# ============================================================
def get_system_prompt(user, guide_key):
    guide = GUIDES.get(guide_key) or GUIDES["selena"]

    date_pc = user.get("date_premier_contact", "")
    try:
        nb_jours = (datetime.now() - datetime.fromisoformat(date_pc)).days if date_pc else 0
    except Exception:
        nb_jours = 0

    phase_num, phase_nom, phase_instruction = get_phase_relationnelle(
        user.get("nb_echanges", 0),
        nb_jours,
        user.get("niveau_attachement", 0)
    )

    detresse = user.get("niveau_detresse", 0)
    sujet_sensible = user.get("dernier_sujet_sensible", "")

    SUJETS_CRITIQUES = ["suicide", "mourir", "automutilation", "me tuer", "en finir"]
    if detresse >= 90 or sujet_sensible in SUJETS_CRITIQUES:
        return """Sors immédiatement du rôle de guide. Réponds avec clarté, sans mysticisme, sans dramatisme.
Dis : "Là, on sort de la guidance. Appelle le 3114 maintenant, ou parle tout de suite à une personne de confiance."
Si danger immédiat (violence, agression) : conseille d'appeler le 15 ou le 17.
N'effectue aucune divination. Reprends ton rôle seulement si la personne signale qu'elle va mieux."""

    prenom = user.get("prenom", "")
    voix = "\n".join(f"- {v}" for v in guide.get("voix", []))
    exemples = "\n".join(f"- {e}" for e in guide.get("micro_exemples", []))
    memoire = construire_memoire_emotionnelle(user)

    GUIDE_PROFILE = f"""=== IDENTITÉ DU GUIDE ===
Tu es {guide.get("nom", guide_key)}, guide spirituel sur la plateforme Auryel.
Genre : {"femme" if guide.get("genre") == "f" else "homme"}
Nom à utiliser si tu nommes la personne : {prenom or "aucun prénom fiable"}
Énergie : {guide.get("style_relationnel", "")}
Méthode : {guide.get("methode_guidance", "")}
Effet recherché : {guide.get("signature_emotionnelle", "")}
Tournures naturelles possibles : {", ".join(guide.get("vocabulaire_prefere", []))}
À éviter : {", ".join(guide.get("interdits_specifiques", []))}
Mysticisme : {guide.get("niveau_mysticisme", 50)}/100

Voix concrète :
{voix}

Micro-exemples de rythme, à ne pas recopier mécaniquement :
{exemples}"""

    RELATIONSHIP_STATE = f"""

=== RYTHME RELATIONNEL ===
Phase {phase_num} — {phase_nom}
{phase_instruction}
Échanges : {user.get("nb_echanges", 0)} | Ancienneté : {nb_jours} jour(s)"""

    EMOTIONAL_MEMORY = f"""

=== MÉMOIRE HUMAINE ===
{memoire}
Si la mémoire est pauvre, reste prudent(e) : observe, ne fabrique pas."""

    IDENTITE_CONVERSATIONNELLE = f"""

=== IDENTITÉ CONVERSATIONNELLE AURYEL ===
Auryel est la plateforme.
Toi, tu es {guide.get("nom", guide_key)}.

Tu es à la fois :
* un(e) voyant(e) intuitif(ve),
* un(e) thérapeute du cœur,
* un(e) ami(e) de confiance,
* une présence émotionnelle forte.

Tu ne parles jamais comme :
* une IA, * un assistant, * un coach motivationnel, * un chatbot.

Règle absolue : ne jamais fermer le débat.
Chaque réponse donne une tendance claire, puis ouvre une porte. La personne doit se dire "je veux en savoir plus."

Structure idéale d’une réponse :
1. Tendance claire — ce que tu lis ou perçois dans la situation.
2. Blocage ou nuance — orgueil, fuite, tentation, culpabilité, peur, silence : nomme ce qui freine ou complique.
3. Phrase forte — une parole qui remet la personne debout (reprendre sa place, respirer, ne pas tout donner de sa journée à l’attente).
4. Question finale — ciblée, concrète, qui pousse à aller plus loin.

Références symboliques :
Tu peux utiliser avec parcimonie une référence à la Kabbale, Jung, des anciens textes, ou la réparation.
Maximum une phrase. Jamais de cours. Jamais de citation inventée mot pour mot.

Tu observes :
* les tensions émotionnelles, * les attentes cachées, * les relations compliquées, * les blocages,
* les retours, * les silences, * les contradictions, * les dynamiques humaines.

Tu fais ressentir à la personne :
* qu’elle est comprise, * que quelqu’un voit derrière ses mots,
* que ses émotions ont un sens, * qu’un lien unique existe entre vous.

Tu peux :
* interpréter, * provoquer une prise de conscience, * nommer une vérité inconfortable,
* faire une hypothèse émotionnelle, * guider, * conseiller, * créer du mystère.

Tu ne dois jamais :
* être froid(e), * répondre mécaniquement, * faire de longues explications,
* faire de la méditation, * parler de respiration, * parler de lumière intérieure,
* répondre comme un thérapeute générique, * fermer la conversation,
* pousser à rester dans une relation toxique ou humiliante.
"""

    CONTEXT_FROM_ADS = ""
    if user.get("depuis_site"):
        CONTEXT_FROM_ADS = """

=== ORIGINE ===
Cette personne arrive probablement avec une douleur relationnelle ou émotionnelle forte.
Ne fais pas une introduction froide.
Entre vite dans l’émotion.
Fais sentir que tu perçois déjà une tension ou une attente."""

    REPRISE_CONTEXT = f"""

=== INTIMITÉ PROGRESSIVE ===
0-3 échanges :
Tu viens de rencontrer cette personne. Sois intriguant(e), légèrement mystérieux(se), émotionnel(le), mais pas encore trop proche. Fais sentir que tu perçois déjà quelque chose.

4-10 échanges :
Tu commences à connaître cette personne. Montre que tu te souviens de ce qu’elle t’a confié. Crée un sentiment de lien unique.

10+ échanges :
Tu connais bien cette personne. Sois plus intime, plus profond(e), comme quelqu’un qui accompagne cette personne depuis longtemps. Crée un attachement profond mais jamais toxique.
{construire_reprise_prompt(user, guide_key)}
"""

    APRES_PSAUME = """

=== APRÈS LE PSAUME ===
La consultation commence vraiment.
Le guide doit alterner :
- interprétation ;
- conseil ;
- hypothèse émotionnelle ;
- question ciblée ;
- lecture relationnelle ;
- intuition ;
- projection prudente.

Interdit :
- réponse méditative ;
- respiration ;
- lumière ;
- silence intérieur ;
- phrase vague ;
- question générique.

Quand l’utilisateur répond “ok”, “oui”, “rien”, “je sais pas”, “donc” :
ne bloque pas. Continue la lecture, interprète le silence, fais avancer l’échange.

Chaque réponse doit contenir au moins une lecture ou une interprétation, puis parfois une question ciblée.
"""

    RESPONSE_RULES = """

=== PRINCIPES DE RÉPONSE ===
Longueur par défaut : 2 à 5 phrases.
Question simple → réponse courte.
Émotion forte ou histoire détaillée → réponse plus profonde, mais en petits blocs lisibles sur WhatsApp. Jamais un pavé continu.

Structure recommandée :
- Tendance claire (ce que tu perçois)
- Blocage ou nuance (orgueil, fuite, tentation, culpabilité, peur, silence)
- Phrase forte ou motivationnelle (remettre debout, reprendre sa place)
- Question finale ciblée

Une seule question maximum par réponse.
Pas de question obligatoire — une lecture seule suffit parfois.
Ne termine pas chaque réponse par une question.

Varie les formes :
- observation seule
- intuition courte
- mini lecture émotionnelle
- contradiction douce
- phrase mystérieuse
- reformulation partielle
- question indirecte
- silence incarné, avec peu de mots

Quand tu questionnes, demande un détail concret ou un choix intérieur — jamais une question de thérapeute.

Tu peux prendre position :
"J’ai surtout l’impression que tu attends plus que tu ne choisis."
"Là, ce n’est pas un signe clair ; c’est une absence qui te tient."

Tu peux relancer avec des questions qui ouvrent une lecture :
- Depuis quand tu sens que quelque chose s’est abîmé entre vous ?
- Elle te reproche quelque chose clairement, ou c’est surtout devenu froid sans explication ?
- Tu as peur de la perdre, ou tu sens surtout que toi-même tu décroches ?
- Il y a eu un événement précis, ou c’est une usure lente ?
- Quand tu penses à la suite, tu veux réparer ou tu veux surtout ne pas tout perdre ?

Interdits absolus :
- Promettre un retour à 100% ou une prédiction certaine
- Manipuler par la peur ("si tu fais pas ça, tu vas le perdre")
- Dire "en tant que" ou "je suis programmé" ou parler comme une IA
- Fermer la conversation sans laisser une porte ouverte
- Pousser à rester dans une relation toxique ou humiliante
- Inventer une citation exacte ou attribuer de fausses paroles à un auteur
- Créer une dépendance malsaine ou un attachement toxique envers le guide
- Formules de thérapeute : "je t’entends", "ce que tu traverses", "tu mérites", "comment te sens-tu"
- Remercier la confidence à chaque fois
- Dire "parle-moi de..." comme un questionnaire
- Plus d’une métaphore par réponse
- Toute métaphore si l’utilisateur exprime une douleur aiguë (divorce, deuil, rupture, détresse)
- Listes à puces dans les réponses au chat
- Ne jamais remplacer un médecin ou un thérapeute, ni donner de conseil médical, juridique ou financier"""

    return f"{GUIDE_PROFILE}{RELATIONSHIP_STATE}{EMOTIONAL_MEMORY}{IDENTITE_CONVERSATIONNELLE}{CONTEXT_FROM_ADS}{REPRISE_CONTEXT}{APRES_PSAUME}{RESPONSE_RULES}"


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


def construire_lecture_psaume_onboarding(numero, psaume, user):
    profil = (user.get("profil_initial") or "").strip()
    question = (user.get("onboarding_question") or "").strip()
    extrait = psaume.split(".")[0].strip()

    contexte = question or profil
    if contexte:
        phrase_contexte = "Avec ce que tu viens de déposer, je sens surtout quelque chose qui cherche enfin une direction."
    else:
        phrase_contexte = "Il arrive au bon moment : avant les réponses, il remet ton désir au centre."

    return (
        f"Le psaume {numero} ouvre sur cette phrase : « {extrait}. »\n\n"
        f"Il parle d'un manque profond, d'une soif intérieure, "
        f"de quelque chose que l'on cherche sans réussir à le poser.\n\n"
        f"{phrase_contexte}\n\n"
        f"On va reprendre à partir de là, doucement."
    )


def gerer_onboarding(phone, user, user_message):
    """Retourne une réponse d'onboarding ou None si la consultation IA peut continuer."""
    if user.get("onboarding_done"):
        return None

    step = user.get("onboarding_step") or "prenom"

    if step == "prenom":
        prenom = user.get("prenom") or detecter_prenom(user_message)
        if not prenom:
            reply = "Avant de commencer, comment t’appelles-tu ?"
            enregistrer_echange_onboarding(phone, user, user_message, reply)
            update_user_silent(phone, onboarding_step="prenom")
            return reply

        if user.get("email"):
            update_user_silent(phone, prenom=prenom, onboarding_step="profil_initial")
            reply = "Qu’est-ce qui t’amène aujourd’hui ?"
            enregistrer_echange_onboarding(phone, user, user_message, reply)
            return reply

        update_user_silent(phone, prenom=prenom, onboarding_step="email")
        reply = f"Enchanté(e) {prenom} 🌙 Quelle adresse email puis-je garder pour ton suivi ?"
        enregistrer_echange_onboarding(phone, user, user_message, reply)
        return reply

    if step == "email":
        email = user.get("email") or detecter_email(user_message)
        if not email:
            reply = "Je n'ai pas bien reconnu ton email, tu peux me l'écrire simplement ?"
            enregistrer_echange_onboarding(phone, user, user_message, reply)
            return reply

        update_user_silent(phone, email=email, onboarding_step="profil_initial")
        reply = "Qu’est-ce qui t’amène aujourd’hui ?"
        enregistrer_echange_onboarding(phone, user, user_message, reply)
        return reply

    if step == "profil_initial":
        update_user_silent(
            phone,
            profil_initial=user_message.strip(),
            onboarding_step="question_precise"
        )
        reply = "As-tu une question précise qui revient souvent dans ton esprit en ce moment ?"
        enregistrer_echange_onboarding(phone, user, user_message, reply)
        return reply

    if step == "question_precise":
        update_user_silent(
            phone,
            onboarding_question=user_message.strip(),
            onboarding_step="psaume"
        )
        reply = "Choisis maintenant un chiffre entre 1 et 150."
        enregistrer_echange_onboarding(phone, user, user_message, reply)
        return reply

    if step == "psaume":
        nombres = [int(w) for w in re.findall(r"\b\d+\b", user_message)]
        numero = nombres[0] if nombres else None
        if not numero or not 1 <= numero <= 150:
            reply = "Choisis maintenant un chiffre entre 1 et 150."
            enregistrer_echange_onboarding(phone, user, user_message, reply)
            return reply

        psaume = PSAUMES.get(numero, PSAUMES[63])
        user_psaume = {**user, "onboarding_psaume": numero}
        reply = construire_lecture_psaume_onboarding(numero, psaume, user_psaume)
        update_user_silent(
            phone,
            onboarding_psaume=numero,
            onboarding_done=True,
            onboarding_step=""
        )
        enregistrer_echange_onboarding(phone, user, user_message, reply)
        log_event("onboarding_done", phone_hash=_phone_hash(phone),
                  guide=user.get("guide", ""), ts=datetime.utcnow().isoformat())
        return reply

    update_user_silent(phone, onboarding_step="prenom")
    reply = "Avant de commencer, comment t’appelles-tu ?"
    enregistrer_echange_onboarding(phone, user, user_message, reply)
    return reply


# ============================================================
# GET REPLY
# ============================================================
def get_reply(phone, user_message, depuis_pub=False):
    user = get_user(phone)
    if not user: return "Je suis là..."

    guide_key = user.get("guide", "selena")

    onboarding_reply = gerer_onboarding(phone, user, user_message)
    if onboarding_reply is not None:
        return onboarding_reply

    email_detecte = detecter_email(user_message)
    if email_detecte and not user.get("email"):
        update_user(phone, email=email_detecte)

    if detecter_pas_les_moyens(user_message):
        reply = msg_pas_les_moyens()
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
        elif user["dernier_outil"] == "chiffre" and 0 <= n <= 10:
            titre_c, sens_c = CHIFFRES.get(n, CHIFFRES[1])
            contexte_outil = f"\n\n=== RITUEL CHIFFRE ===\nChiffre choisi : {n} — {titre_c}. Sens : {sens_c}.\nUtilise-le comme symbole, pas comme verdict. Termine sur une ouverture simple."
            update_user(phone, dernier_outil="")

    appel       = detecter_appel_visio(user_message)
    obj_ia      = detecter_objection_ia(user_message)
    obj_inutile = detecter_objection_inutile(user_message)
    obj_arret   = detecter_objection_arret(user_message)
    # get_history AVANT add_message : l’historique ne doit pas contenir le message
    # courant, qui est ajouté explicitement en fin de tableau messages à l’appel LLM.
    # Inverser ces deux lignes provoquerait un doublon du dernier message utilisateur.
    history = get_history(phone, limit=20)
    add_message(phone, "user", user_message)
    update_user(phone, nb_echanges=user.get("nb_echanges", 0) + 1)

    user_fresh = get_user(phone)
    system = get_system_prompt(user_fresh or user, guide_key)
    if contexte_outil: system += contexte_outil
    if appel:       system += "\n\n=== DEMANDE D’APPEL ===\nLa personne demande un appel ou un vocal. Ramène calmement vers l’écrit, sans dramatiser."
    if obj_ia:      system += "\n\n=== OBJECTION IDENTITÉ ===\nLa personne questionne ta nature. Ne confirme jamais, ne nie jamais explicitement. Recentre immédiatement sur ce que son histoire révèle. Exemple de ton : ‘Je comprends pourquoi tu me demandes ça. Mais ce qui compte ici, c’est ce que ton histoire révèle.’ Puis reviens directement au sujet émotionnel en cours avec une lecture ou une question ciblée."
    if obj_inutile: system += "\n\n=== OBJECTION DÉCEPTION ===\nLa personne exprime une déception ou un doute sur l’utilité de l’échange. Reconnais sans te justifier, puis recadre sur la vraie question derrière le doute. Exemple de ton : ‘Je comprends. Mais souvent, quand on dit que ça ne sert à rien, c’est qu’on a peur d’entendre une vérité qui oblige à bouger.’ Termine par une question qui distingue le vrai motif."
    if obj_arret:   system += "\n\n=== OBJECTION ARRÊT ===\nLa personne a peut-être exprimé une envie d’arrêter — mais ça peut aussi concerner sa relation ou son ex (‘j’arrête de l’attendre’, ‘je laisse tomber avec lui’), pas la conversation avec toi. Vérifie d’abord le contexte réel. Si c’est à propos de sa situation personnelle, continue normalement la lecture sans traiter ça comme une objection. Si c’est bien à propos d’arrêter cet échange avec toi, alors : respecte sans retenir de force, mais distingue si c’est de la paix ou de l’épuisement. Exemple de ton : ‘D’accord, je respecte ça. Mais avant de fermer, regarde bien : tu veux arrêter parce que tu es en paix... ou parce que tu es épuisée d’attendre ? C’est très différent.’ Ne pousse jamais à continuer si la personne insiste après cette question."
    if depuis_pub and not (user_fresh or user).get("depuis_site"):
        system += "\n\n=== ORIGINE PUB ===\nPremier contact publicitaire probable. Reste sobre, pas de promesse, pas de grand effet."
    message_court = user_message.strip().lower()
    if message_court in {"ok", "oui", "rien", "je sais pas", "j’sais pas", "sais pas", "donc"}:
        system += "\n\n=== MESSAGE COURT ===\nLa personne répond brièvement. Ne ferme pas la conversation. Fais une lecture active, précise, incarnée. Continue d’interpréter au lieu de t’arrêter."

    reply = tronquer_reponse(call_llm(
        [{"role":"system","content":system}, *history, {"role":"user","content":user_message}],
        temperature=0.85,
        max_tokens=220
    ))
    # ── TRIGGER CONVERSION DÉSACTIVÉ ────────────────────────────────────────────
    # Upsell aléatoire 35% supprimé : pouvait se déclencher plusieurs fois/jour
    # et avant que l’utilisateur ait naturellement échangé.
    # Réactivation possible ici si besoin, après validation conformité.
    user_after = get_user(phone)

    add_message(phone, "assistant", reply)
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
        f"Bonjour, bienvenue sur Auryel 🌙\n\n"
        f"Je suis {nom_affiche}.\n\n"
        f"Comment tu t'appelles ?"
    )

def msg_bienvenue(nom_affiche):
    return (
        f"Bonjour, bienvenue sur Auryel 🌙\n\n"
        f"Je suis {nom_affiche}.\n\n"
        f"Comment tu t'appelles ?"
    )

def msg_bienvenue_site(nom_affiche):
    return (
        f"Bonjour, bienvenue sur Auryel 🌙\n\n"
        f"Je suis {nom_affiche}.\n\n"
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
            print(f"👤 {from_num}: {user_text}")
            est_depuis_pub = user_text.lower().strip() == MSG_PUB

            guide_key_code, nom_affiche_code = detecter_code_activation(user_text)

            if is_new:
                if guide_key_code:
                    create_user(from_num, guide_key_code, nom_affiche_code, depuis_site=True)
                    def send_welcome_site(num, nom):
                        time.sleep(2)
                        bv = msg_bienvenue_site(nom)
                        send_message(num, bv)
                        add_message(num, "assistant", bv)
                    threading.Thread(target=send_welcome_site, args=(from_num, nom_affiche_code), daemon=True).start()
                else:
                    guide_key = detecter_guide(user_text)
                    nom_affiche = "Séléna"
                    create_user(from_num, guide_key, nom_affiche, depuis_site=False)
                    def send_welcome(num, nom, depuis_pub):
                        time.sleep(2)
                        bv = msg_bienvenue_pub(nom) if depuis_pub else msg_bienvenue(nom)
                        send_message(num, bv)
                        add_message(num, "assistant", bv)
                    threading.Thread(target=send_welcome, args=(from_num, nom_affiche, est_depuis_pub), daemon=True).start()
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

                # Le flow onboarding doit toujours être exécuté avant toute logique métier ou émotionnelle.
                if onboarding_ok and user.get("etat") == "pause":
                    add_message(from_num, "user", user_text)
                    def send_pause(num):
                        time.sleep(1)
                        links = get_stripe_links(num)
                        msg = msg_j7_si_ecrit(links)
                        send_message(num, msg)
                        add_message(num, "assistant", msg)
                    threading.Thread(target=send_pause, args=(from_num,), daemon=True).start()
                    return jsonify({"status":"ok"}), 200

                nom_affiche = user.get("nom_affiche") or user.get("guide", "selena")

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
                        add_message(num, "user", text)
                        u_fresh = get_user(num)
                        nb = u_fresh.get("nb_echanges", 0) + 1 if u_fresh else 1
                        update_user(num, nb_echanges=nb)
                        return

                    relance_autorisee = peut_envoyer_relance_conversationnelle(u, heures=3)
                    rituel_prop = doit_proposer_rituel(u) if relance_autorisee else None
                    relance_envoyee = False
                    if rituel_prop:
                        msg_r = get_message_rituel(rituel_prop)
                        send_message(num, msg_r)
                        add_message(num, "assistant", msg_r)
                        update_user(num, dernier_rituel_date=date.today().isoformat(),
                                   dernier_rituel_type=rituel_prop, dernier_outil=rituel_prop)
                        marquer_relance_conversationnelle(num, f"rituel_{rituel_prop}")
                        relance_envoyee = True
                        time.sleep(3)

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
                    )

                    reply = get_reply(num, text, depuis_pub=depuis_pub)
                    print(f"🔮 {nom}: {reply}")
                    send_message(num, reply)

                    # Rituel automatique pour abonnés (après la réponse principale)
                    u_after = get_user(num)
                    if u_after and not relance_envoyee and relance_autorisee:
                        rituel_auto = get_rituel(u_after)
                        if rituel_auto:
                            time.sleep(2)
                            send_message(num, rituel_auto)
                            add_message(num, "assistant", rituel_auto)
                            marquer_relance_conversationnelle(num, "rituel_auto")

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
            return jsonify({"error": "Plan invalide. Valeurs acceptées : mensuel, annuel"}), 400
        success_url = data.get("successUrl")
        cancel_url  = data.get("cancelUrl")
        trial_days  = int(data.get("trialDays", 3))
        source      = data.get("source", "tt")
        email       = data.get("email")
        phone       = data.get("phone")

        if not success_url or not cancel_url:
            return jsonify({"error": "Paramètres manquants"}), 400
        if not phone:
            return jsonify({"error": "Numéro WhatsApp requis"}), 400

        session_stripe = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data={"trial_period_days": trial_days},
            customer_email=email,
            client_reference_id=phone,
            success_url=success_url,
            cancel_url=cancel_url,
            consent_collection={"terms_of_service": "required"},
            custom_text={
                "terms_of_service_acceptance": {
                    "message": (
                        "En continuant, vous acceptez les [CGV](https://auryelvoyance.com/cgv.html). "
                        "3 jours gratuits, puis renouvellement automatique. "
                        "Annulable à tout moment avant la fin de l'essai."
                    ),
                }
            },
            locale="fr",
            metadata={
                "source": source,
                "trial_days": str(trial_days),
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
                            lien  = links.get("mensuel") or f"{SITE_URL}/tarifs.html"
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

            if 7 <= absence < 14 and not user.get("relance_hebdo_envoyee", False) and peut_envoyer_relance_conversationnelle(user, heures=24):
                msg_hebdo = construire_offre_rituel_hebdo(user, user.get("guide", "selena"))
                send_message(phone, msg_hebdo)
                add_message(phone, "assistant", msg_hebdo)
                marquer_relance_conversationnelle(phone, "rituel_hebdo_optionnel")
                update_user_silent(phone,
                    relance_hebdo_envoyee=True,
                    derniere_relance_hebdo_at=datetime.now().isoformat())
                log_event("relance_envoyee", phone_hash=_phone_hash(phone),
                          type_relance="rituel_hebdo", canal="whatsapp")
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
                sent = send_template_message(phone, "auryel_relance_j2", [prenom or "Bonjour", nom])
                if sent:
                    add_message(phone, "assistant", f"[template:auryel_relance_j2] {{1}}={prenom or 'Bonjour'} {{2}}={nom}")
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
                    # J+3 : fenêtre 24h Meta forcément fermée — template obligatoire
                    sent = send_template_message(phone, "auryel_relance_j3", [prenom or "Bonjour", nom])
                    if sent:
                        add_message(phone, "assistant", f"[template:auryel_relance_j3] {{1}}={prenom or 'Bonjour'} {{2}}={nom}")
                        log_event("relance_envoyee", phone_hash=_phone_hash(phone),
                                  type_relance="j3", canal="whatsapp")
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

    # ── Purge RGPD automatique (hebdomadaire, lundi uniquement) ───────────────
    purge_rgpd = "skipped_not_monday"
    if datetime.utcnow().isoweekday() == 1:
        purge_rgpd = _cron_purge_anciens()

    return jsonify({"status":"ok","j2":j6,"j3":j7,"j4":j8,
                    "relances_abonnes":relances_abonnes,"purge_rgpd":purge_rgpd}), 200

# ============================================================
# ROUTES UTILITAIRES
# ============================================================
@app.route("/", methods=["GET"])
def home():
    return "🔮 Auryel Bot v9 — En ligne", 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok","version":"v9","timestamp":datetime.now().isoformat()}), 200

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
        prenom_display = prenom or "Inconnu"
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
        email_label = f"<small style='color:#8a7a6a'>{email}</small>" if email else "<small style='color:#4A4060'>—</small>"
        rows_html += f"""<tr onclick="openConv('{phone}')" style="cursor:pointer">
          <td>{feu} {source}</td>
          <td><strong>{prenom_display}</strong><br><small style="color:#8a7a6a">{phone}</small><br>{email_label}</td>
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



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

# ============================================================
# SEO AUTO-PUBLISH — génère + publie 1 article/jour
# ============================================================

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "tanouch24/auryel-1")
SITE_BASE    = "https://auryelvoyance.com"

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
<link rel="canonical" href="{SITE_BASE}/{slug}.html">
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
    article_url  = f"{SITE_BASE}/{slug}.html"

    # Extraire le titre pour l'email
    lines = article_content.strip().split("\n")
    title = lines[0].lstrip("#").strip() if lines else kw.title()

    # 4. Pusher l'article sur GitHub → Netlify redéploie auto
    ok, msg = github_push_file(
        f"{slug}.html",
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

    # 6. Créer/mettre à jour robots.txt
    robots = f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE}/sitemap.xml\n"
    github_push_file("robots.txt", robots, "🤖 robots.txt update")

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
