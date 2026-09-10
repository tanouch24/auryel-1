"""
r2_media_sync.py — Synchronisation ADDITIVE Cloudflare R2 -> catalogues média.

Module DÉDIÉ et TESTABLE. Aucune dépendance à Flask ni à la session utilisateur.
La connexion DB (`get_conn`) et le client de listing R2 sont INJECTABLES : tous
les tests tournent 100 % hors ligne (aucun accès au vrai Cloudflare).

Principe produit : Nathanyel dépose un fichier dans R2, il apparaît dans l'app
sous ~15 min, sans release, sans script manuel.

    R2  méditations/*.mp3        -> 1 ligne meditation_catalog
    R2  relaxation-videos/*.mp4  -> 1 ligne relaxation_video_catalog

RÈGLE ABSOLUE : 1 MP3 = 1 méditation, 1 MP4 = 1 visuel. Une vidéo ne crée
JAMAIS de méditation. Aucun produit cartésien : les deux catalogues sont
strictement séparés.

Garanties :
  - Anti-doublon au niveau DB : chaque ligne synchronisée porte
    `r2_object_key` UNIQUE (index partiel). Rejouer le sync, ou deux crons qui
    se chevauchent, ne créent jamais de doublon (INSERT ... ON CONFLICT).
  - Verrou consultatif Postgres : deux exécutions simultanées -> la seconde
    sort proprement (« already_running »).
  - Additif : un objet retiré de R2 n'est JAMAIS supprimé de la base, seulement
    signalé « missing ».
  - Reprise des 50 méditations (et 12 vidéos) déjà importées : reconnaissance
    par URL publique OU par slug -> `r2_object_key` renseigné SANS créer de
    doublon et SANS toucher aux métadonnées éditoriales existantes.
  - Aucun secret n'est loggé ni renvoyé.
"""

import os
import re
import unicodedata
import urllib.parse
import uuid
from datetime import datetime, timezone

# Préfixes EXACTS dans le bucket. Le premier contient réellement un « é ».
AUDIO_PREFIX = "méditations/"
VIDEO_PREFIX = "relaxation-videos/"

_R2_ENDPOINT_TMPL = "https://{account_id}.r2.cloudflarestorage.com"

# Verrou consultatif Postgres : clé arbitraire stable propre à ce cron.
_ADVISORY_LOCK_KEY = 918273645

# Catégorie générique des méditades auto-synchronisées : aucune métadonnée ne
# permet de deviner une vraie catégorie éditoriale -> valeur neutre assumée.
DEFAULT_MEDITATION_CATEGORY = "general"
DEFAULT_VIDEO_CATEGORY = "calm"  # -> is_generic = true (cf. LOT 9)

_ORDER_PREFIX_RE = re.compile(r"^0*(\d{1,4})[-_.\s]+(.+)$")
_TECH_VIDEO_RE = re.compile(r"^(\d{3,})")
_SLUG_OK_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")  # miroir _MEDITATION_SLUG_RE


class R2ConfigError(RuntimeError):
    """Configuration R2 absente ou incomplète (aucun credential inventé)."""


# ---------------------------------------------------------------------------
# Configuration (exclusivement depuis l'environnement)
# ---------------------------------------------------------------------------
class R2Config:
    __slots__ = ("account_id", "access_key_id", "secret_access_key",
                 "bucket", "public_base_url")

    def __init__(self, account_id="", access_key_id="", secret_access_key="",
                 bucket="", public_base_url=""):
        self.account_id = (account_id or "").strip()
        self.access_key_id = (access_key_id or "").strip()
        self.secret_access_key = (secret_access_key or "").strip()
        self.bucket = (bucket or "").strip()
        self.public_base_url = (public_base_url or "").strip().rstrip("/")

    @classmethod
    def from_env(cls, env=None):
        env = env or os.environ
        return cls(
            account_id=env.get("R2_ACCOUNT_ID", ""),
            access_key_id=env.get("R2_ACCESS_KEY_ID", ""),
            secret_access_key=env.get("R2_SECRET_ACCESS_KEY", ""),
            bucket=env.get("R2_BUCKET_NAME", ""),
            public_base_url=env.get("R2_PUBLIC_BASE_URL", ""),
        )

    @property
    def configured(self):
        return all((self.account_id, self.access_key_id, self.secret_access_key,
                    self.bucket, self.public_base_url))

    @property
    def endpoint_url(self):
        return _R2_ENDPOINT_TMPL.format(account_id=self.account_id)

    def masked(self):
        """Résumé NON SENSIBLE pour les logs / le rapport."""
        ak = self.access_key_id
        return {
            "account_id": self.account_id[:6] + "…" if self.account_id else "",
            "bucket": self.bucket,
            "public_base_url": self.public_base_url,
            "access_key_id": (ak[:4] + "…") if ak else "",
            "secret_access_key": "***" if self.secret_access_key else "",
            "configured": self.configured,
        }

    def require(self):
        if not self.configured:
            missing = [n for n, v in (
                ("R2_ACCOUNT_ID", self.account_id),
                ("R2_ACCESS_KEY_ID", self.access_key_id),
                ("R2_SECRET_ACCESS_KEY", self.secret_access_key),
                ("R2_BUCKET_NAME", self.bucket),
                ("R2_PUBLIC_BASE_URL", self.public_base_url),
            ) if not v]
            raise R2ConfigError(
                "configuration R2 incomplète — variables manquantes : "
                + ", ".join(missing)
            )


# ---------------------------------------------------------------------------
# Client de listing R2 (S3 ListObjectsV2, paginé)
# ---------------------------------------------------------------------------
class R2Client:
    """Liste les objets d'un préfixe via l'API S3-compatible de R2.

    `s3_factory` est injectable (tests) : un appelable `() -> objet exposant
    `get_paginator('list_objects_v2')`. Par défaut, un client boto3 est créé à
    la demande (import paresseux : le module reste importable sans boto3)."""

    def __init__(self, config: R2Config, s3_factory=None):
        self.config = config
        self._s3_factory = s3_factory
        self._s3 = None

    def _client(self):
        if self._s3 is not None:
            return self._s3
        if self._s3_factory is not None:
            self._s3 = self._s3_factory()
            return self._s3
        self.config.require()
        try:
            import boto3  # import paresseux — non requis en test
        except ImportError as exc:  # pragma: no cover
            raise R2ConfigError(
                "le paquet boto3 est requis pour lister R2 "
                "(ajouté à requirements.txt)"
            ) from exc
        self._s3 = boto3.client(
            "s3",
            endpoint_url=self.config.endpoint_url,
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            region_name="auto",
        )
        return self._s3

    def list_objects(self, prefix):
        """Génère des dicts {"key": str, "size": int} pour tous les objets du
        préfixe. Pagination transparente (supporte > 1000 objets)."""
        s3 = self._client()
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.config.bucket, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                yield {"key": obj.get("Key", ""),
                       "size": int(obj.get("Size", 0) or 0)}


# ---------------------------------------------------------------------------
# Dérivations (filename -> slug / titre / url) — pures, testables
# ---------------------------------------------------------------------------
def _basename(key):
    return key.rsplit("/", 1)[-1]


def _split_ext(name):
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot + 1:].lower()


def slugify(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t[:64].strip("-")


def humanize(text):
    """« retrouver-le_calme » -> « Retrouver le calme » (casse phrase FR,
    1re lettre en capitale, le reste inchangé). Jamais d'extension affichée."""
    t = re.sub(r"[-_]+", " ", (text or "")).strip()
    t = re.sub(r"\s{2,}", " ", t)
    if not t:
        return ""
    return t[0].upper() + t[1:]


def is_object_key(key):
    """Vrai si `key` désigne un vrai objet (pas un « dossier », pas un fichier
    caché, pas .DS_Store)."""
    if not key or key.endswith("/"):
        return False
    base = _basename(key)
    if not base or base.startswith("."):
        return False
    if base.lower() == ".ds_store":
        return False
    return True


def public_url(base_url, object_key):
    """URL publique R2 correctement encodée. `méditations/x.mp3` ->
    `<base>/m%C3%A9ditations/x.mp3` (le « / » reste, l'accent est percent-encodé
    en UTF-8). Identique au format stocké lors de l'import initial (LOT 10)."""
    return f"{base_url.rstrip('/')}/{urllib.parse.quote(object_key, safe='/')}"


def derive_meditation(object_key):
    """(slug, title, category) pour un MP3. Retire un éventuel numéro d'ordre
    en tête du titre utilisateur. `None` si le slug dérivé est inexploitable."""
    stem, _ext = _split_ext(_basename(object_key))
    m = _ORDER_PREFIX_RE.match(stem)
    body = m.group(2) if m else stem
    slug = slugify(body) or slugify(stem)
    if len(slug) < 2 or not _SLUG_OK_RE.match(slug):
        return None
    title = humanize(body) or humanize(stem) or slug
    return slug, title[:200], DEFAULT_MEDITATION_CATEGORY


def derive_video(object_key):
    """(slug, title, category, technical) pour un MP4.

    Filename « technique » (banque d'images : commence par un ID numérique) ->
    slug `relaxation-<id>` et titre = slug : le Flutter (LOT 11) affiche alors
    « Visuel N » et n'expose jamais le nom de fichier. Filename humain ->
    titre lisible."""
    stem, _ext = _split_ext(_basename(object_key))
    tech = _TECH_VIDEO_RE.match(stem)
    if tech:
        slug = f"relaxation-{tech.group(1)}"
        if not _SLUG_OK_RE.match(slug):
            return None
        return slug, slug, DEFAULT_VIDEO_CATEGORY, True
    slug = slugify(stem)
    if len(slug) < 2 or not _SLUG_OK_RE.match(slug):
        return None
    return slug, (humanize(stem) or slug)[:200], DEFAULT_VIDEO_CATEGORY, False


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------
class R2MediaCatalogSync:
    """Orchestrateur. `get_conn` et `r2_client` injectables.

    `run(dry_run=False)` renvoie un dict de compteurs NON SENSIBLES.
    dry_run=True : aucune écriture (ni catalogues, ni état, ni last_seen)."""

    def __init__(self, get_conn, r2_client: R2Client, *, public_base_url=None,
                 now_fn=None):
        self._get_conn = get_conn
        self._r2 = r2_client
        self._base_url = (public_base_url
                          or getattr(r2_client.config, "public_base_url", "")
                          or "")
        self._now = now_fn or (lambda: datetime.now(timezone.utc))

    # -- listing + filtrage --------------------------------------------------
    def _collect(self, prefix, ext):
        found, invalid = [], 0
        for obj in self._r2.list_objects(prefix):
            key = obj["key"]
            if not is_object_key(key):
                continue
            _stem, e = _split_ext(_basename(key))
            if e != ext:
                invalid += 1
                continue
            if int(obj.get("size", 0)) <= 0:
                invalid += 1
                continue
            found.append(key)
        return found, invalid

    # -- accès DB ----------------------------------------------------------
    @staticmethod
    def _known_keys(c, table):
        c.execute(
            f"SELECT r2_object_key FROM {table} WHERE r2_object_key IS NOT NULL"
        )
        return {r[0] for r in c.fetchall()}

    @staticmethod
    def _next_sort_order(c, table):
        c.execute(f"SELECT COALESCE(MAX(sort_order), 0) + 1 FROM {table}")
        return int((c.fetchone() or [1])[0] or 1)

    @staticmethod
    def _slug_taken(c, table, slug):
        c.execute(f"SELECT 1 FROM {table} WHERE slug = %s", (slug,))
        return c.fetchone() is not None

    def _free_slug(self, c, table, slug):
        if not self._slug_taken(c, table, slug):
            return slug
        for n in range(2, 1000):
            cand = f"{slug[:60]}-{n}"
            if not self._slug_taken(c, table, cand):
                return cand
        return f"{slug[:52]}-{uuid.uuid4().hex[:8]}"

    # -- coeur ------------------------------------------------------------
    def run(self, dry_run=False):
        started = self._now()
        result = {
            "dry_run": bool(dry_run),
            "started_at": started.isoformat(),
            "audio_objects": 0, "video_objects": 0,
            "new_audio": 0, "new_videos": 0,
            "adopted_audio": 0, "adopted_videos": 0,
            "updated_audio": 0, "updated_videos": 0,
            "invalid_objects": 0, "missing_objects": 0,
            "missing": [], "errors": 0, "status": "success",
        }

        conn = self._get_conn()
        try:
            c = conn.cursor()

            if not dry_run:
                c.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
                got = c.fetchone()
                if got is not None and got[0] is False:
                    result["status"] = "already_running"
                    return result

            audio_keys, inv_a = self._collect(AUDIO_PREFIX, "mp3")
            video_keys, inv_v = self._collect(VIDEO_PREFIX, "mp4")
            result["audio_objects"] = len(audio_keys)
            result["video_objects"] = len(video_keys)
            result["invalid_objects"] = inv_a + inv_v

            self._sync_kind(
                c, result, dry_run, kind="audio", table="meditation_catalog",
                url_col="audio_url", keys=audio_keys, derive=self._derive_audio_row,
            )
            self._sync_kind(
                c, result, dry_run, kind="videos",
                table="relaxation_video_catalog", url_col="video_url",
                keys=video_keys, derive=self._derive_video_row,
            )

            if not dry_run:
                self._persist_state(c, result)
                conn.commit()
            else:
                conn.rollback()
        except Exception as exc:  # jamais de secret dans le message
            result["errors"] += 1
            result["status"] = "failed"
            result["detail"] = type(exc).__name__
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            try:
                if not dry_run:
                    cc = conn.cursor()
                    cc.execute("SELECT pg_advisory_unlock(%s)",
                               (_ADVISORY_LOCK_KEY,))
                    conn.commit()
            except Exception:
                pass
            conn.close()

        if result["errors"] and result["status"] == "success":
            result["status"] = "partial"
        return result

    # -- dérivation d'une ligne prête à écrire ----------------------------
    def _derive_audio_row(self, key):
        d = derive_meditation(key)
        if d is None:
            return None
        slug, title, category = d
        return {
            "slug": slug, "title": title, "category": category,
            "description": None, "duration_seconds": 0,
            "url": public_url(self._base_url, key), "image_or_thumb": None,
        }

    def _derive_video_row(self, key):
        d = derive_video(key)
        if d is None:
            return None
        slug, title, category, _tech = d
        return {
            "slug": slug, "title": title, "category": category,
            "description": None, "duration_seconds": None,
            "url": public_url(self._base_url, key), "image_or_thumb": None,
        }

    def _sync_kind(self, c, result, dry_run, *, kind, table, url_col, keys,
                   derive):
        known = self._known_keys(c, table)
        seen = set()
        next_order = self._next_sort_order(c, table)

        for key in keys:
            seen.add(key)
            try:
                if key in known:
                    if not dry_run:
                        c.execute(
                            f"UPDATE {table} SET r2_last_seen_at = %s "
                            "WHERE r2_object_key = %s",
                            (result["started_at"], key),
                        )
                    result[f"updated_{kind}"] += 1
                    continue

                row = derive(key)
                if row is None:
                    result["invalid_objects"] += 1
                    continue

                # Reprise d'une ligne existante (import initial) : match par URL
                # publique OU par slug, uniquement si r2_object_key est NULL.
                c.execute(
                    f"SELECT id FROM {table} "
                    f"WHERE r2_object_key IS NULL AND ({url_col} = %s OR slug = %s) "
                    "LIMIT 1",
                    (row["url"], row["slug"]),
                )
                hit = c.fetchone()
                if hit is not None:
                    if not dry_run:
                        c.execute(
                            f"UPDATE {table} "
                            "SET r2_object_key = %s, r2_last_seen_at = %s "
                            "WHERE id = %s AND r2_object_key IS NULL",
                            (key, result["started_at"], hit[0]),
                        )
                    result[f"adopted_{kind}"] += 1
                    continue

                # Création.
                result[f"new_{kind}"] += 1
                if dry_run:
                    continue
                slug = self._free_slug(c, table, row["slug"])
                new_id = str(uuid.uuid4())
                if table == "meditation_catalog":
                    c.execute(
                        "INSERT INTO meditation_catalog "
                        "(id, slug, title, description, category, "
                        " duration_seconds, audio_url, image_url, sort_order, "
                        " is_active, published_at, r2_object_key, "
                        " r2_last_seen_at, created_at, updated_at, version) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, TRUE, "
                        "        NULL, %s, %s, NOW(), NOW(), 1) "
                        "ON CONFLICT (r2_object_key) "
                        "  WHERE r2_object_key IS NOT NULL DO UPDATE "
                        "  SET r2_last_seen_at = EXCLUDED.r2_last_seen_at "
                        "RETURNING id",
                        (new_id, slug, row["title"], row["description"],
                         row["category"], int(row["duration_seconds"] or 0),
                         row["url"], next_order, key, result["started_at"]),
                    )
                else:
                    c.execute(
                        "INSERT INTO relaxation_video_catalog "
                        "(id, slug, title, description, video_url, "
                        " thumbnail_url, category, tags, sort_order, is_active, "
                        " published_at, r2_object_key, r2_last_seen_at, "
                        " created_at, updated_at, version) "
                        "VALUES (%s, %s, %s, %s, %s, NULL, %s, '{}', %s, TRUE, "
                        "        NULL, %s, %s, NOW(), NOW(), 1) "
                        "ON CONFLICT (r2_object_key) "
                        "  WHERE r2_object_key IS NOT NULL DO UPDATE "
                        "  SET r2_last_seen_at = EXCLUDED.r2_last_seen_at "
                        "RETURNING id",
                        (new_id, slug, row["title"], row["description"],
                         row["url"], row["category"], next_order, key,
                         result["started_at"]),
                    )
                next_order += 1
            except Exception as exc:
                result["errors"] += 1
                result.setdefault("error_keys", []).append(
                    f"{_basename(key)} :: {type(exc).__name__}"
                )

        # Objets connus en base mais absents de R2 ce passage-ci : signalés,
        # JAMAIS supprimés.
        missing = sorted(known - seen)
        if missing:
            result["missing_objects"] += len(missing)
            result["missing"].extend(missing)

    def _persist_state(self, c, result):
        c.execute(
            "INSERT INTO r2_sync_state "
            "(id, last_run_at, status, audio_objects, video_objects, "
            " new_audio, new_videos, adopted_audio, adopted_videos, "
            " updated_audio, updated_videos, missing_objects, invalid_objects, "
            " errors, detail) "
            "VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "  last_run_at = EXCLUDED.last_run_at, status = EXCLUDED.status, "
            "  audio_objects = EXCLUDED.audio_objects, "
            "  video_objects = EXCLUDED.video_objects, "
            "  new_audio = EXCLUDED.new_audio, new_videos = EXCLUDED.new_videos, "
            "  adopted_audio = EXCLUDED.adopted_audio, "
            "  adopted_videos = EXCLUDED.adopted_videos, "
            "  updated_audio = EXCLUDED.updated_audio, "
            "  updated_videos = EXCLUDED.updated_videos, "
            "  missing_objects = EXCLUDED.missing_objects, "
            "  invalid_objects = EXCLUDED.invalid_objects, "
            "  errors = EXCLUDED.errors, detail = EXCLUDED.detail",
            (result["started_at"], result["status"], result["audio_objects"],
             result["video_objects"], result["new_audio"], result["new_videos"],
             result["adopted_audio"], result["adopted_videos"],
             result["updated_audio"], result["updated_videos"],
             result["missing_objects"], result["invalid_objects"],
             result["errors"], result.get("detail")),
        )


def read_sync_state(get_conn):
    """Ligne d'état du dernier sync (dict) pour l'endpoint admin, ou None."""
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT last_run_at, status, audio_objects, video_objects, "
            "       new_audio, new_videos, adopted_audio, adopted_videos, "
            "       updated_audio, updated_videos, missing_objects, "
            "       invalid_objects, errors, detail "
            "FROM r2_sync_state WHERE id = 1"
        )
        r = c.fetchone()
    finally:
        conn.close()
    if not r:
        return None
    keys = ("last_run_at", "status", "audio_objects", "video_objects",
            "new_audio", "new_videos", "adopted_audio", "adopted_videos",
            "updated_audio", "updated_videos", "missing_objects",
            "invalid_objects", "errors", "detail")
    out = dict(zip(keys, r))
    lr = out.get("last_run_at")
    out["last_run_at"] = lr.isoformat() if hasattr(lr, "isoformat") else lr
    return out
