# Synchronisation automatique Cloudflare R2 → catalogues Auryel

**Objectif** : déposer un fichier dans Cloudflare R2 suffit pour qu'il apparaisse
dans l'application. **Aucun script à lancer, aucune nouvelle version Android /
iOS, aucun changement Flutter.**

---

## Pour AJOUTER UNE MÉDITATION

1. Préparer un fichier **`.mp3`** correctement nommé (voir conventions plus bas).
2. L'**uploader dans le dossier** `méditations/` du bucket `auryel-meditations`
   (le dossier a bien un **« é »**).
3. Attendre **~15 min maximum** (le cron passe toutes les 15 minutes).
4. La méditation apparaît **dans la bibliothèque** de l'app.

## Pour AJOUTER UN VISUEL

1. Préparer un fichier **`.mp4`**.
2. L'**uploader dans le dossier** `relaxation-videos/`.
3. Attendre **~15 min maximum**.
4. Le visuel apparaît dans **« Choisir le visuel »** (lecteur de méditation).

> **Aucune release d'application n'est nécessaire.** Les catalogues audio et
> vidéo sont 100 % distants. Le nombre d'entrées n'a aucun plafond.

---

## Règle absolue

**1 MP3 = 1 méditation. 1 MP4 = 1 visuel.**
Une vidéo ne crée **jamais** une méditation. Une méditation n'est **jamais**
dupliquée parce que plusieurs vidéos existent. Les deux catalogues sont
totalement séparés (aucun produit cartésien).

---

## Convention de nommage recommandée

### Audio (`méditations/`)

```
51-retrouver-le-calme-apres-une-dispute.mp3
```

- préfixe numérique d'ordre **facultatif** (`51-`) : il sert au tri, il est
  **retiré du titre** affiché ;
- le reste devient le **titre** (`Retrouver le calme après une dispute`) et le
  **slug** (`retrouver-le-calme-apres-une-dispute`) ;
- utiliser des **tirets**, pas d'espaces ; les accents du nom de fichier sont
  perdus dans le titre auto — les corriger ensuite via l'admin si besoin ;
- l'extension `.mp3` n'est **jamais** affichée.

Catégorie : les fichiers auto-synchronisés reçoivent la catégorie neutre
**`general`** (aucune métadonnée ne permet de deviner une vraie catégorie).
L'admin peut l'affiner après coup.

### Vidéo (`relaxation-videos/`)

```
ocean-coucher-soleil.mp4      → titre « Ocean coucher soleil »
16318782-hd_1080_1920_60fps.mp4 → titre technique NON exposé → l'app affiche « Visuel N »
```

- nom **humain** → titre lisible ;
- nom **technique de banque d'images** (commence par un identifiant numérique) →
  le backend stocke un libellé technique et **l'app affiche « Visuel 1 », « Visuel 2 », …** ;
- catégorie par défaut : **`calm`** → `is_generic = true` (le visuel est
  compatible avec toutes les méditations).

### Ignoré automatiquement

Dossiers, fichiers cachés, `.DS_Store`, extensions autres que `.mp3` / `.mp4`,
objets de taille 0.

---

## Fonctionnement technique

| Élément | Détail |
|---|---|
| **Détection** | API S3 de R2, `ListObjectsV2` **paginé** (supporte > 1000 objets), préfixes `méditations/` et `relaxation-videos/`. **Jamais** de parsing d'une page HTML publique. |
| **URL publique** | `<R2_PUBLIC_BASE_URL>/<clé d'objet URL-encodée>` — le `é` de `méditations/` est percent-encodé (`m%C3%A9ditations/…`), identique au format déjà en base. |
| **Anti-doublon** | colonne `r2_object_key` **UNIQUE** (index partiel `WHERE r2_object_key IS NOT NULL`) sur chaque catalogue. `INSERT … ON CONFLICT (r2_object_key)` → rejouer le sync, ou deux crons qui se chevauchent, ne crée **jamais** de doublon. |
| **Concurrence** | verrou consultatif Postgres (`pg_try_advisory_lock`) : une seconde exécution simultanée sort proprement (`already_running`). |
| **Reprise de l'existant** | un objet R2 dont l'URL publique **ou** le slug correspond à une ligne existante **sans** `r2_object_key` est **adopté** (on renseigne `r2_object_key`) — **aucun doublon**, **aucune métadonnée éditoriale touchée**. C'est ainsi que les **50 méditations** (et les 12 vidéos après leur import) sont reconnues au **premier** passage. |
| **Additif** | un objet **supprimé de R2** n'est **jamais** supprimé de la base : il est compté `missing_objects` et listé dans le log / l'endpoint de statut. |
| **Durée audio** | non extraite (nécessiterait de lire chaque fichier) → `duration_seconds = 0` (valeur optionnelle du modèle). |

---

## Exécution manuelle (facultatif)

```bash
python3 scripts/sync_r2_media_catalog.py --dry-run   # n'écrit rien
python3 scripts/sync_r2_media_catalog.py             # mode réel
```

Le dry-run n'affiche que des informations non sensibles (objets trouvés,
nouveaux, adoptés, invalides, manquants). **Aucun credential n'est jamais
affiché.**

---

## Cron Railway (à créer au déploiement)

| Champ | Valeur |
|---|---|
| Nom logique | `auryel-r2-media-sync-cron` |
| Fréquence | `*/15 * * * *` |
| Commande | `POST https://<backend>/cron/r2-media-sync` avec l'en-tête `X-Cron-Secret: <R2_SYNC_CRON_SECRET>` (repli `PUSH_CRON_SECRET` / `DAILY_SECRET`) |
| Alternative (Railway cron service) | `python3 scripts/sync_r2_media_catalog.py` avec les variables ci-dessous |
| Service / environnement cible | `web` / `production` |

> **Ne pas** toucher aux crons FCM (`/cron/push-tick`) ou billing
> (`/cron/billing-reverify`) existants.

### Variables d'environnement requises (Railway, service `web`, env `production`)

| Variable | Rôle | Statut actuel |
|---|---|---|
| `R2_ACCOUNT_ID` | identifiant de compte Cloudflare | **à définir** |
| `R2_ACCESS_KEY_ID` | clé d'accès S3 R2 (jeton API à portée bucket) | **à définir** |
| `R2_SECRET_ACCESS_KEY` | secret associé | **à définir** |
| `R2_BUCKET_NAME` | `auryel-meditations` | **à définir** |
| `R2_PUBLIC_BASE_URL` | `https://pub-19c78d4dc57a41849a27c0e73ed231ce.r2.dev` | **à définir** |
| `R2_SYNC_CRON_SECRET` | secret d'appel du cron (facultatif : repli `PUSH_CRON_SECRET`) | facultatif |
| `CONTENT_MEDIA_ALLOWED_HOSTS` | `pub-19c78d4dc57a41849a27c0e73ed231ce.r2.dev` | **déjà défini** (LOT 10) |

Tant que les 5 premières ne sont pas définies, l'endpoint répond
`200 {"status":"disabled","reason":"r2_not_configured"}` : **aucune erreur, aucun
effet de bord**.

---

## Endpoint de statut (admin)

```
GET /admin/content/r2-sync-status        (session admin)
```

```json
{
  "configured": true,
  "config": { "bucket": "auryel-meditations", "public_base_url": "https://…",
              "access_key_id": "abcd…", "secret_access_key": "***" },
  "state": {
    "last_run_at": "2026-09-10T12:00:00+00:00",
    "status": "success",
    "audio_objects": 51, "video_objects": 12,
    "new_audio": 1, "new_videos": 0,
    "adopted_audio": 0, "adopted_videos": 0,
    "updated_audio": 50, "updated_videos": 12,
    "missing_objects": 0, "invalid_objects": 0, "errors": 0
  }
}
```

Ne renvoie **jamais** de credential R2 (clé d'accès tronquée, secret masqué).
