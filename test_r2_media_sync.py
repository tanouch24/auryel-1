"""
test_r2_media_sync.py — synchro Cloudflare R2 -> catalogues média (module
r2_media_sync). 100 % hors ligne : fausse DB en mémoire + faux client R2
(aucun accès au vrai Cloudflare, aucun credential, aucun secret).

Couvre : dérivations (slug/titre/url, accent, encodage), filtrage
(mp3/mp4 casse-insensible, dossiers, .DS_Store, 0 byte, formats inconnus),
pagination > 1000 objets, 1re détection -> 1 entrée, 2e passage -> pas de
doublon, reprise des 50 méditations sans doublon, 51e MP3, 12/13 MP4,
absence de produit cartésien, suppression R2 non destructive, dry-run,
idempotence/concurrence, config absente -> erreur propre, secrets absents des
logs.
"""
import io
import sys
import contextlib
from datetime import datetime, timezone

import r2_media_sync as R

_S = {"pass": 0, "fail": 0}


def check(cond, label):
    if cond:
        _S["pass"] += 1
        print(f"OK  {label}")
    else:
        _S["fail"] += 1
        print(f"XX  {label}")


NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
BASE = "https://pub-19c78d4dc57a41849a27c0e73ed231ce.r2.dev"


# ===========================================================================
# Fake R2
# ===========================================================================
class FakeR2:
    """`config` factice + `list_objects(prefix)` depuis un dict prefix->list."""

    class _Cfg:
        public_base_url = BASE
        configured = True

    def __init__(self, by_prefix):
        self.config = FakeR2._Cfg()
        self._by_prefix = by_prefix
        self.calls = []

    def list_objects(self, prefix):
        self.calls.append(prefix)
        for o in self._by_prefix.get(prefix, []):
            yield {"key": o[0], "size": o[1]}


# ===========================================================================
# Fake DB
# ===========================================================================
class FakeDB:
    def __init__(self):
        self.meds = []       # dicts
        self.vids = []
        self.state = None
        self.lock_held = False
        self.lock_available = True

    def table(self, name):
        return self.meds if name == "meditation_catalog" else self.vids


class Cur:
    def __init__(self, db):
        self.db = db
        self._one = None
        self._rows = None
        self.rowcount = -1

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows or []

    def close(self):
        pass

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        p = tuple(params or ())
        self._one = None
        self._rows = None
        self.rowcount = -1
        db = self.db

        # Régression : les index d'unicité `uq_<catalogue>_r2_object_key` sont
        # PARTIELS (WHERE r2_object_key IS NOT NULL). PostgreSQL n'accepte un
        # index partiel comme arbitre d'un `ON CONFLICT` QUE si la clause
        # reproduit ce prédicat. Ici on reproduit ce refus : un upsert
        # `ON CONFLICT (r2_object_key)` SANS le `WHERE r2_object_key IS NOT
        # NULL` lève, comme le vrai Postgres l'a fait en prod.
        if ("ON CONFLICT (r2_object_key)" in s
                and "WHERE r2_object_key IS NOT NULL DO UPDATE" not in s):
            raise AssertionError(
                "InvalidColumnReference: there is no unique or exclusion "
                "constraint matching the ON CONFLICT specification "
                "(index partiel -> il faut ON CONFLICT (r2_object_key) "
                "WHERE r2_object_key IS NOT NULL DO UPDATE)"
            )

        if s.startswith("SELECT pg_try_advisory_lock"):
            if db.lock_available and not db.lock_held:
                db.lock_held = True
                self._one = (True,)
            else:
                self._one = (False,)
        elif s.startswith("SELECT pg_advisory_unlock"):
            db.lock_held = False
            self._one = (True,)
        elif s.startswith("SELECT r2_object_key FROM meditation_catalog WHERE r2_object_key IS NOT NULL"):
            self._rows = [(m["r2_object_key"],) for m in db.meds if m.get("r2_object_key")]
        elif s.startswith("SELECT r2_object_key FROM relaxation_video_catalog WHERE r2_object_key IS NOT NULL"):
            self._rows = [(v["r2_object_key"],) for v in db.vids if v.get("r2_object_key")]
        elif s.startswith("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM meditation_catalog"):
            self._one = (max([m["sort_order"] for m in db.meds], default=0) + 1,)
        elif s.startswith("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM relaxation_video_catalog"):
            self._one = (max([v["sort_order"] for v in db.vids], default=0) + 1,)
        elif s.startswith("SELECT 1 FROM meditation_catalog WHERE slug ="):
            self._one = (1,) if any(m["slug"] == p[0] for m in db.meds) else None
        elif s.startswith("SELECT 1 FROM relaxation_video_catalog WHERE slug ="):
            self._one = (1,) if any(v["slug"] == p[0] for v in db.vids) else None
        elif s.startswith("UPDATE meditation_catalog SET r2_last_seen_at = %s WHERE r2_object_key = %s"):
            for m in db.meds:
                if m.get("r2_object_key") == p[1]:
                    m["r2_last_seen_at"] = p[0]
        elif s.startswith("UPDATE relaxation_video_catalog SET r2_last_seen_at = %s WHERE r2_object_key = %s"):
            for v in db.vids:
                if v.get("r2_object_key") == p[1]:
                    v["r2_last_seen_at"] = p[0]
        elif s.startswith("SELECT id FROM meditation_catalog WHERE r2_object_key IS NULL AND (audio_url = %s OR slug = %s)"):
            url, slug = p
            hit = next((m for m in db.meds if not m.get("r2_object_key")
                        and (m["audio_url"] == url or m["slug"] == slug)), None)
            self._one = (hit["id"],) if hit else None
        elif s.startswith("SELECT id FROM relaxation_video_catalog WHERE r2_object_key IS NULL AND (video_url = %s OR slug = %s)"):
            url, slug = p
            hit = next((v for v in db.vids if not v.get("r2_object_key")
                        and (v["video_url"] == url or v["slug"] == slug)), None)
            self._one = (hit["id"],) if hit else None
        elif s.startswith("UPDATE meditation_catalog SET r2_object_key = %s, r2_last_seen_at = %s WHERE id = %s"):
            key, seen, mid = p
            for m in db.meds:
                if m["id"] == mid and not m.get("r2_object_key"):
                    m["r2_object_key"] = key
                    m["r2_last_seen_at"] = seen
        elif s.startswith("UPDATE relaxation_video_catalog SET r2_object_key = %s, r2_last_seen_at = %s WHERE id = %s"):
            key, seen, vid = p
            for v in db.vids:
                if v["id"] == vid and not v.get("r2_object_key"):
                    v["r2_object_key"] = key
                    v["r2_last_seen_at"] = seen
        elif s.startswith("INSERT INTO meditation_catalog"):
            (mid, slug, title, desc, cat, dur, url, sort_o, key, seen) = p
            existing = next((m for m in db.meds if m.get("r2_object_key") == key), None)
            if existing:
                existing["r2_last_seen_at"] = seen
                self._one = (existing["id"],)
            else:
                if any(m["slug"] == slug for m in db.meds):
                    raise AssertionError("slug déjà pris (contrainte UNIQUE)")
                db.meds.append(dict(
                    id=mid, slug=slug, title=title, description=desc,
                    category=cat, duration_seconds=dur, audio_url=url,
                    image_url=None, sort_order=sort_o, is_active=True,
                    published_at=None, r2_object_key=key, r2_last_seen_at=seen,
                    version=1))
                self._one = (mid,)
        elif s.startswith("INSERT INTO relaxation_video_catalog"):
            (vid, slug, title, desc, url, cat, sort_o, key, seen) = p
            existing = next((v for v in db.vids if v.get("r2_object_key") == key), None)
            if existing:
                existing["r2_last_seen_at"] = seen
                self._one = (existing["id"],)
            else:
                if any(v["slug"] == slug for v in db.vids):
                    raise AssertionError("slug déjà pris (contrainte UNIQUE)")
                db.vids.append(dict(
                    id=vid, slug=slug, title=title, description=desc,
                    video_url=url, thumbnail_url=None, category=cat, tags=[],
                    sort_order=sort_o, is_active=True, published_at=None,
                    r2_object_key=key, r2_last_seen_at=seen, version=1))
                self._one = (vid,)
        elif s.startswith("INSERT INTO r2_sync_state"):
            db.state = {
                "last_run_at": p[0], "status": p[1], "audio_objects": p[2],
                "video_objects": p[3], "new_audio": p[4], "new_videos": p[5],
                "adopted_audio": p[6], "adopted_videos": p[7],
                "updated_audio": p[8], "updated_videos": p[9],
                "missing_objects": p[10], "invalid_objects": p[11],
                "errors": p[12], "detail": p[13],
            }
        elif s.startswith("SELECT last_run_at, status, audio_objects"):
            st = db.state
            if not st:
                self._one = None
            else:
                self._one = (
                    st["last_run_at"], st["status"], st["audio_objects"],
                    st["video_objects"], st["new_audio"], st["new_videos"],
                    st["adopted_audio"], st["adopted_videos"],
                    st["updated_audio"], st["updated_videos"],
                    st["missing_objects"], st["invalid_objects"],
                    st["errors"], st["detail"],
                )
        else:
            raise AssertionError("SQL non géré par le fake r2 : " + s)


class Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return Cur(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def make_get_conn(db):
    return lambda: Conn(db)


def run_sync(db, r2, dry_run=False):
    sync = R.R2MediaCatalogSync(make_get_conn(db), r2, public_base_url=BASE,
                                now_fn=lambda: NOW)
    return sync.run(dry_run=dry_run)


# ===========================================================================
# 1. Dérivations pures
# ===========================================================================
check(R.slugify("Retrouver le Calme !") == "retrouver-le-calme",
      "1a slugify : minuscule, non-alnum -> tiret, jamais de doublon de tiret")
check(R.humanize("retrouver-le_calme-apres") == "Retrouver le calme apres",
      "1b humanize : tirets/underscores -> espaces, 1re lettre capitale")

d = R.derive_meditation("méditations/51-retrouver-le-calme-apres-une-dispute.mp3")
check(d == ("retrouver-le-calme-apres-une-dispute",
            "Retrouver le calme apres une dispute", "general"),
      "1c MP3 : numéro d'ordre retiré du titre, slug propre, catégorie general")
check(R.derive_meditation("méditations/01-quand-tout-devient-trop-lourd.mp3")[0]
      == "quand-tout-devient-trop-lourd",
      "1d MP3 : slug identique à celui de l'import initial (reprise possible)")
check(R.derive_meditation("méditations/-.mp3") is None
      and R.derive_meditation("méditations/---.mp3") is None,
      "1e MP3 sans corps de nom exploitable -> None (ignoré)")

v = R.derive_video("relaxation-videos/16318782-hd_1080_1920_60fps.mp4")
check(v == ("relaxation-16318782", "relaxation-16318782", "calm", True),
      "1f MP4 technique : slug relaxation-<id>, titre = slug (app -> « Visuel N »)")
v2 = R.derive_video("relaxation-videos/ocean-coucher-soleil.mp4")
check(v2 == ("ocean-coucher-soleil", "Ocean coucher soleil", "calm", False),
      "1g MP4 humain : titre lisible")

check(R.public_url(BASE, "méditations/01-quand-tout-devient-trop-lourd.mp3")
      == BASE + "/m%C3%A9ditations/01-quand-tout-devient-trop-lourd.mp3",
      "1h URL publique : accent percent-encodé, « / » conservé, = format en base")
check(R.public_url(BASE, "relaxation-videos/ocean.mp4")
      == BASE + "/relaxation-videos/ocean.mp4",
      "1i URL vidéo : pas d'encodage superflu")

# ===========================================================================
# 2. Filtrage
# ===========================================================================
check(R.is_object_key("méditations/x.mp3") is True
      and R.is_object_key("méditations/") is False
      and R.is_object_key("méditations/.DS_Store") is False
      and R.is_object_key("méditations/.hidden.mp3") is False,
      "2a is_object_key : dossier / .DS_Store / fichier caché exclus")

r2 = FakeR2({
    R.AUDIO_PREFIX: [
        ("méditations/01-alpha-calme.mp3", 1000),
        ("méditations/02-bravo-repos.MP3", 1000),   # casse
        ("méditations/", 0),                          # dossier
        ("méditations/.DS_Store", 64),               # caché
        ("méditations/notes.txt", 500),              # format inconnu
        ("méditations/03-vide.mp3", 0),              # 0 byte
    ],
    R.VIDEO_PREFIX: [
        ("relaxation-videos/ocean.mp4", 2000),
        ("relaxation-videos/forest.MP4", 2000),      # casse
        ("relaxation-videos/cover.jpg", 100),        # format inconnu
    ],
})
db = FakeDB()
res = run_sync(db, r2, dry_run=True)
check(res["audio_objects"] == 2,
      "2b MP3 comptés = 2 (01-alpha, 02-bravo.MP3 ; .txt/.DS_Store/dossier/0-byte exclus)")
check(res["video_objects"] == 2, "2c MP4 comptés = 2 (ocean, forest.MP4 ; .jpg exclu)")
check(res["invalid_objects"] == 3,
      "2d objets invalides = 3 (.txt + .mp3 0 byte + .jpg)")
check(res["new_audio"] == 2 and res["new_videos"] == 2,
      "2e dry-run : créations prévues = 2 méditations + 2 visuels, AUCUN produit cartésien")
check(db.meds == [] and db.vids == [], "2f dry-run : AUCUNE écriture en base")

# ===========================================================================
# 3. Pagination > 1000 objets
# ===========================================================================
big = [(f"méditations/{i:04d}-med-{i}.mp3", 1000) for i in range(2500)]
r2b = FakeR2({R.AUDIO_PREFIX: big, R.VIDEO_PREFIX: []})
resb = run_sync(FakeDB(), r2b, dry_run=True)
check(resb["audio_objects"] == 2500, "3a 2500 objets listés (pagination transparente côté module)")

# ===========================================================================
# 4. Première détection -> 1 entrée ; 2e passage -> pas de doublon
# ===========================================================================
r2c = FakeR2({R.AUDIO_PREFIX: [("méditations/51-retrouver-le-calme.mp3", 4096)],
              R.VIDEO_PREFIX: []})
dbc = FakeDB()
run_sync(dbc, r2c)
check(len(dbc.meds) == 1 and dbc.meds[0]["slug"] == "retrouver-le-calme"
      and dbc.meds[0]["r2_object_key"] == "méditations/51-retrouver-le-calme.mp3"
      and dbc.meds[0]["audio_url"].endswith("/m%C3%A9ditations/51-retrouver-le-calme.mp3"),
      "4a 1er passage : 1 méditation créée, r2_object_key + URL encodée")
r_again = run_sync(dbc, r2c)
check(len(dbc.meds) == 1 and r_again["new_audio"] == 0
      and r_again["updated_audio"] == 1,
      "4b 2e passage : toujours 1 méditation, 0 création, comptée « déjà connue »")

# ===========================================================================
# 5. Reprise des 50 méditations déjà importées (sans doublon)
# ===========================================================================
db50 = FakeDB()
keys50 = []
for i in range(1, 51):
    ok = f"méditations/{i:02d}-med-{i}.mp3"
    keys50.append((ok, 4096))
    db50.meds.append(dict(
        id=f"id-{i}", slug=f"med-{i}", title=f"Med {i}", description="",
        category="stress-calme", duration_seconds=0,
        audio_url=R.public_url(BASE, ok), image_url=None, sort_order=i,
        is_active=True, published_at=None, r2_object_key=None,
        r2_last_seen_at=None, version=3))
r2_50 = FakeR2({R.AUDIO_PREFIX: keys50, R.VIDEO_PREFIX: []})
res50 = run_sync(db50, r2_50)
check(len(db50.meds) == 50 and res50["new_audio"] == 0
      and res50["adopted_audio"] == 50,
      "5a 50 MP3 déjà en base -> 50 adoptés, 0 création, 0 doublon")
check(all(m["r2_object_key"] for m in db50.meds)
      and all(m["category"] == "stress-calme" for m in db50.meds)
      and all(m["version"] == 3 for m in db50.meds),
      "5b reprise : r2_object_key renseigné, métadonnées éditoriales INTACTES (version non bougée)")
# rejouer : plus rien à faire
res50b = run_sync(db50, r2_50)
check(res50b["adopted_audio"] == 0 and res50b["updated_audio"] == 50
      and len(db50.meds) == 50,
      "5c re-run après reprise : 0 adoption, 50 déjà connues, aucun doublon")

# ===========================================================================
# 6. 51e MP3 -> le catalogue passe à 51
# ===========================================================================
keys51 = keys50 + [("méditations/51-nouvelle-seance.mp3", 4096)]
r2_51 = FakeR2({R.AUDIO_PREFIX: keys51, R.VIDEO_PREFIX: []})
res51 = run_sync(db50, r2_51)
check(len(db50.meds) == 51 and res51["new_audio"] == 1
      and any(m["slug"] == "nouvelle-seance" for m in db50.meds),
      "6a 51e MP3 -> 1 création, catalogue = 51")
check(len(db50.vids) == 0, "6b aucun visuel créé par un MP3 (jamais de méditation<->vidéo)")

# ===========================================================================
# 7. 12 MP4 -> 12 visuels ; 13e -> 13
# ===========================================================================
vkeys12 = [(f"relaxation-videos/{n}-hd_1080_1920_30fps.mp4", 5000)
           for n in (11210466, 12113828, 12552663, 13058548, 13800913, 13932290,
                     14232675, 14618955, 14735114, 14735747, 16318782, 20349634)]
dbv = FakeDB()
r2v = FakeR2({R.AUDIO_PREFIX: [], R.VIDEO_PREFIX: vkeys12})
resv = run_sync(dbv, r2v)
check(len(dbv.vids) == 12 and resv["new_videos"] == 12 and resv["new_audio"] == 0,
      "7a 12 MP4 -> 12 visuels, 0 méditation")
check(all(v["category"] == "calm" for v in dbv.vids)
      and all(v["title"] == v["slug"] for v in dbv.vids)
      and all(v["slug"].startswith("relaxation-") for v in dbv.vids),
      "7b visuels : category=calm (is_generic), titre technique = slug -> app « Visuel N »")
r2v13 = FakeR2({R.AUDIO_PREFIX: [], R.VIDEO_PREFIX: vkeys12 + [
    ("relaxation-videos/coucher-de-soleil.mp4", 5000)]})
resv13 = run_sync(dbv, r2v13)
check(len(dbv.vids) == 13 and resv13["new_videos"] == 1
      and any(v["slug"] == "coucher-de-soleil" and v["title"] == "Coucher de soleil"
              for v in dbv.vids),
      "7c 13e MP4 humain -> 13 visuels, titre lisible")

# ===========================================================================
# 8. 50 audios + 13 vidéos = 51 méditations / 13 visuels (jamais 50x13)
# ===========================================================================
dbx = FakeDB()
r2x = FakeR2({R.AUDIO_PREFIX: keys51, R.VIDEO_PREFIX: vkeys12 + [
    ("relaxation-videos/coucher-de-soleil.mp4", 5000)]})
resx = run_sync(dbx, r2x)
check(len(dbx.meds) == 51 and len(dbx.vids) == 13,
      "8 51 MP3 + 13 MP4 -> 51 méditations ET 13 visuels (aucun produit cartésien)")

# ===========================================================================
# 9. Suppression R2 -> pas de suppression destructive DB
# ===========================================================================
r2_less = FakeR2({R.AUDIO_PREFIX: keys50, R.VIDEO_PREFIX: []})  # le 51e a disparu
res_less = run_sync(db50, r2_less)
check(len(db50.meds) == 51 and res_less["missing_objects"] == 1
      and "méditations/51-nouvelle-seance.mp3" in res_less["missing"],
      "9 objet retiré de R2 -> signalé « missing », JAMAIS supprimé de la base")

# ===========================================================================
# 10. dry-run n'écrit rien même avec des adoptions/créations prévues
# ===========================================================================
db_dry = FakeDB()
db_dry.meds.append(dict(
    id="id-x", slug="deja-la", title="Déjà là", description="", category="amour",
    duration_seconds=0, audio_url=R.public_url(BASE, "méditations/09-deja-la.mp3"),
    image_url=None, sort_order=9, is_active=True, published_at=None,
    r2_object_key=None, r2_last_seen_at=None, version=2))
r2_dry = FakeR2({R.AUDIO_PREFIX: [("méditations/09-deja-la.mp3", 4096),
                                  ("méditations/52-toute-neuve.mp3", 4096)],
                 R.VIDEO_PREFIX: []})
res_dry = run_sync(db_dry, r2_dry, dry_run=True)
check(res_dry["adopted_audio"] == 1 and res_dry["new_audio"] == 1
      and db_dry.meds[0]["r2_object_key"] is None and len(db_dry.meds) == 1
      and db_dry.state is None,
      "10 dry-run : plan correct (1 adoption + 1 création) mais AUCUNE écriture")

# ===========================================================================
# 11. Concurrence : verrou consultatif -> 2e run simultané sort proprement
# ===========================================================================
db_lock = FakeDB()
db_lock.lock_available = False   # simulate : verrou déjà tenu par un autre run
res_lock = run_sync(db_lock, FakeR2({R.AUDIO_PREFIX: [
    ("méditations/01-repos-doux.mp3", 10)], R.VIDEO_PREFIX: []}))
check(res_lock["status"] == "already_running" and db_lock.meds == [],
      "11a run concurrent : status=already_running, aucune écriture")
# idempotence : rejouer normalement fonctionne
db_lock.lock_available = True
run_sync(db_lock, FakeR2({R.AUDIO_PREFIX: [("méditations/01-repos-doux.mp3", 10)],
                          R.VIDEO_PREFIX: []}))
run_sync(db_lock, FakeR2({R.AUDIO_PREFIX: [("méditations/01-repos-doux.mp3", 10)],
                          R.VIDEO_PREFIX: []}))
check(len(db_lock.meds) == 1, "11b deux exécutions successives -> 1 seule ligne")

# ===========================================================================
# 12. r2_sync_state persisté + relu par read_sync_state
# ===========================================================================
dbs = FakeDB()
run_sync(dbs, FakeR2({R.AUDIO_PREFIX: [("méditations/01-alpha-calme.mp3", 10)],
                      R.VIDEO_PREFIX: []}))
st = R.read_sync_state(make_get_conn(dbs))
check(st and st["status"] == "success" and st["audio_objects"] == 1
      and st["new_audio"] == 1 and st["last_run_at"] == NOW.isoformat(),
      "12 r2_sync_state écrit puis relu (compteurs, horodatage ISO)")

# ===========================================================================
# 13. Config : credentials absents -> erreur explicite, aucun secret
# ===========================================================================
cfg_empty = R.R2Config.from_env(env={})
check(cfg_empty.configured is False, "13a R2Config sans variables -> non configuré")
raised = ""
try:
    cfg_empty.require()
except R.R2ConfigError as e:
    raised = str(e)
check("R2_ACCOUNT_ID" in raised and "R2_SECRET_ACCESS_KEY" in raised,
      "13b require() -> R2ConfigError listant les variables manquantes (noms only)")
cfg_full = R.R2Config(account_id="acc-1234567", access_key_id="AKIAABCDEF",
                      secret_access_key="topsecretvalue", bucket="auryel-meditations",
                      public_base_url=BASE)
m = cfg_full.masked()
check("topsecretvalue" not in repr(m) and m["secret_access_key"] == "***"
      and m["access_key_id"] == "AKIA…" and m["configured"] is True,
      "13c masked() : secret jamais exposé, access key tronquée")

# ===========================================================================
# 14. Aucun secret dans les logs du sync
# ===========================================================================
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    run_sync(FakeDB(), FakeR2({R.AUDIO_PREFIX: [("méditations/01-alpha-calme.mp3", 10)],
                               R.VIDEO_PREFIX: []}))
out = buf.getvalue()
check("topsecret" not in out.lower() and "secret_access_key" not in out
      and "aws_secret" not in out.lower(),
      "14 run() n'imprime aucun secret")

# ===========================================================================
# 15. RÉGRESSION — ON CONFLICT compatible avec l'index UNIQUE PARTIEL
#     (bug prod : « there is no unique or exclusion constraint matching the
#      ON CONFLICT specification »). Le faux curseur REFUSE désormais un
#      upsert dont l'arbitre ne reproduit pas le prédicat partiel.
# ===========================================================================

# 15a — le SQL réellement émis par le module porte le bon arbitre partiel.
class _SqlSpy(Cur):
    seen = []

    def execute(self, sql, params=()):
        _SqlSpy.seen.append(" ".join(sql.split()))
        return super().execute(sql, params)


class _SpyConn(Conn):
    def cursor(self):
        return _SqlSpy(self.db)


_SqlSpy.seen = []
_spy_db = FakeDB()
R.R2MediaCatalogSync(
    lambda: _SpyConn(_spy_db),
    FakeR2({R.AUDIO_PREFIX: [("méditations/60-toute-neuve-seance.mp3", 4096)],
            R.VIDEO_PREFIX: [("relaxation-videos/16318782-hd_1080_1920_60fps.mp4",
                              5000)]}),
    public_base_url=BASE, now_fn=lambda: NOW,
).run(dry_run=False)
_med_ins = [q for q in _SqlSpy.seen if q.startswith("INSERT INTO meditation_catalog")]
_vid_ins = [q for q in _SqlSpy.seen if q.startswith("INSERT INTO relaxation_video_catalog")]
check(_med_ins and "ON CONFLICT (r2_object_key) WHERE r2_object_key IS NOT NULL DO UPDATE"
      in _med_ins[0],
      "15a upsert meditation_catalog : ON CONFLICT (r2_object_key) WHERE "
      "r2_object_key IS NOT NULL DO UPDATE")
check(_vid_ins and "ON CONFLICT (r2_object_key) WHERE r2_object_key IS NOT NULL DO UPDATE"
      in _vid_ins[0],
      "15b upsert relaxation_video_catalog : même arbitre partiel")
check(len(_spy_db.meds) == 1 and len(_spy_db.vids) == 1
      and _spy_db.meds[0]["r2_object_key"] == "méditations/60-toute-neuve-seance.mp3"
      and _spy_db.vids[0]["r2_object_key"]
      == "relaxation-videos/16318782-hd_1080_1920_60fps.mp4",
      "15c avec le bon arbitre : 1 méditation + 1 vidéo créées (INSERT accepté)")

# 15d — le faux curseur REFUSE un ON CONFLICT sans prédicat (= comportement
#       PostgreSQL sur index partiel) : c'est ce qui aurait attrapé le bug.
_bad = Cur(FakeDB())
try:
    _bad.execute(
        "INSERT INTO relaxation_video_catalog (id, r2_object_key) "
        "VALUES (%s, %s) ON CONFLICT (r2_object_key) DO UPDATE "
        "SET r2_last_seen_at = EXCLUDED.r2_last_seen_at", ("x", "k"))
    _rejected = False
except AssertionError as e:
    _rejected = "no unique or exclusion constraint" in str(e)
check(_rejected,
      "15d un ON CONFLICT (r2_object_key) SANS prédicat partiel est rejeté "
      "(reproduit l'erreur prod)")

# 15e — l'index de migration v46 crée bien le prédicat partiel correspondant
#       (lecture texte de auryel_bot.py — pas d'import du monolithe).
import os as _os
_bot = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "auryel_bot.py")
_src = open(_bot, encoding="utf-8").read()
_i46 = _src.find("Migration v46")
_seg = " ".join(_src[_i46:_i46 + 3000].split())
check(_i46 != -1
      and "CREATE UNIQUE INDEX IF NOT EXISTS uq_" in _seg
      and "(r2_object_key) WHERE r2_object_key IS NOT NULL" in _seg,
      "15e migration v46 : index UNIQUE PARTIEL (WHERE r2_object_key IS NOT "
      "NULL) — cohérent avec l'arbitre de l'upsert")

# 15f — adoption + création + 2e passage idempotent + aucun doublon, via le
#       faux curseur STRICT (rejette tout upsert au mauvais arbitre).
_db = FakeDB()
_db.meds.append(dict(
    id="m-1", slug="revenir-au-calme", title="Revenir au calme", description="",
    category="stress-calme", duration_seconds=0,
    audio_url=R.public_url(BASE, "méditations/02-revenir-au-calme.mp3"),
    image_url=None, sort_order=2, is_active=True, published_at=None,
    r2_object_key=None, r2_last_seen_at=None, version=4))
_r2 = FakeR2({
    R.AUDIO_PREFIX: [("méditations/02-revenir-au-calme.mp3", 4096)],
    R.VIDEO_PREFIX: [("relaxation-videos/ocean.mp4", 5000)],
})
_p1 = run_sync(_db, _r2)
check(_p1["status"] == "success" and _p1["errors"] == 0
      and _p1["adopted_audio"] == 1 and _p1["new_videos"] == 1
      and _db.meds[0]["r2_object_key"] == "méditations/02-revenir-au-calme.mp3"
      and _db.meds[0]["version"] == 4  # métadonnées éditoriales intactes
      and len(_db.vids) == 1,
      "15f 1er passage : média existant adopté + 1 vidéo créée, 0 erreur")
_p2 = run_sync(_db, _r2)
check(_p2["status"] == "success" and _p2["errors"] == 0
      and _p2["adopted_audio"] == 0 and _p2["new_videos"] == 0
      and _p2["updated_audio"] == 1 and _p2["updated_videos"] == 1
      and len(_db.meds) == 1 and len(_db.vids) == 1,
      "15g 2e passage : idempotent, aucune création, aucun doublon")

# ===========================================================================
print("-" * 64)
print(f"RÉSULTAT : {_S['pass']} ok / {_S['fail']} ko")
sys.exit(1 if _S["fail"] else 0)
