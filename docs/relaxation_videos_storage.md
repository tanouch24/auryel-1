# Stockage des vidéos apaisantes — recommandation

**But** : héberger les vidéos d'ambiance (`~/Desktop/videos apaisantes auryel/`,
**12 fichiers `.mp4`**, ~530 Mo) puis les suivantes (13, 14, … 100) sur un
stockage **objet HTTPS + CDN**, sans jamais mettre les fichiers dans PostgreSQL
ni de secret dans l'app.

> ⚠️ Ce lot **ne téléverse rien**. Il prépare : schéma (Migration v45), manifest
> (`scripts/relaxation_videos_manifest.json`), script d'import
> (`scripts/import_relaxation_videos.py`), admin (`/admin/content`), endpoint
> (`GET /api/app/content/relaxation-videos`), tests. Le choix du stockage et
> l'upload réel se font au lot suivant, sur validation explicite.

---

## Contraintes

| Besoin | Pourquoi |
|---|---|
| URL HTTPS **stable** par fichier | `relaxation_video_catalog.video_url` (validé `_validate_media_url` : HTTPS obligatoire). Une URL qui change casse le catalogue. |
| **Range requests** (`Accept-Ranges: bytes`, `206`) | lecture en streaming dans le lecteur Flutter (`video_player`) sans télécharger toute la vidéo. Essentiel sur données mobiles. |
| **CDN / cache** en edge | vidéos servies à toute la base ; latence + coût de sortie maîtrisés. |
| **Coût de sortie maîtrisé** | la vidéo pèse bien plus lourd que l'audio → l'egress est le poste critique. |
| **Pas de secret dans l'app** | bucket **public en lecture** (objets non listables) ; aucune clé, aucune signature côté client. |
| Domaine **fixe** | `CONTENT_MEDIA_ALLOWED_HOSTS` verrouille alors l'hôte accepté par l'admin (partagé avec les audios). |

---

## Recommandation : **Cloudflare R2** (+ domaine personnalisé), même bucket que l'audio

**Pourquoi R2**
- **Zéro frais de sortie** (egress gratuit) — décisif pour de la vidéo grand
  public. Stockage ~0,015 $/Go/mois → 530 Mo ≈ négligeable ; 5 Go (≈ 100 vidéos
  compressées) ≈ 0,08 $/mois.
- **S3-compatible** : `rclone` / `aws s3` / `boto3` pour l'upload (opération
  d'admin, hors dépôt).
- **CDN Cloudflare** natif + **range requests** (`206 Partial Content`) →
  streaming OK.
- **Domaine personnalisé** : `media.auryel.app` branché sur le bucket → URL
  stables `https://media.auryel.app/relaxation-videos/<fichier>.mp4`.
- Bucket en **lecture publique** (via le domaine), **listing désactivé**.

**Mise en place (résumé — à faire au lot upload)**
1. Réutiliser le bucket média (`auryel-media` ou `auryel-meditations`).
2. Préfixe dédié `relaxation-videos/` en conservant les noms de fichiers
   d'origine (le manifest référence `filename` tel quel) :
   `rclone copy ~/Desktop/"videos apaisantes auryel"/ r2:auryel-media/relaxation-videos/ --content-type video/mp4`
3. Vérifier :
   `curl -I -H 'Range: bytes=0-1' https://media.auryel.app/relaxation-videos/11210466-hd_1080_1920_30fps.mp4`
   → `206`, `Accept-Ranges: bytes`, `Content-Type: video/mp4`, `Cache-Control`.
4. Backend : `CONTENT_MEDIA_ALLOWED_HOSTS=media.auryel.app` (déjà requis pour
   les audios) — l'admin ne pourra alors coller que des URL de ce domaine.
5. Import : `python3 scripts/import_relaxation_videos.py --base-url https://<backend>
   --video-base-url https://media.auryel.app/relaxation-videos`.

**Recommandé avant upload (hors périmètre de ce lot)** : ré-encoder en H.264
1080×1920, ~2–3 Mbps, `-movflags +faststart` (moov atom en tête → lecture
immédiate). Les sources UHD 2160×3840 sont surdimensionnées pour un fond de
téléphone.

---

## Alternatives équivalentes (si R2 indisponible)

| Option | Verdict |
|---|---|
| **Backblaze B2 + Cloudflare** (Bandwidth Alliance) | Egress gratuit vers Cloudflare, S3-compatible. Bonne option n°2. |
| **Bunny.net Stream / CDN** | Spécialisé vidéo (transcodage, HLS), pas cher, range OK, domaine perso inclus. Bon si on veut de l'adaptatif plus tard. |
| **AWS S3 + CloudFront** | Fonctionne (range + CDN) mais egress CloudFront facturé (~0,085 $/Go) — coûteux sur de la vidéo. |
| **DigitalOcean Spaces + CDN** | S3-compatible, CDN inclus, egress limité offert puis facturé. Correct. |

## À NE PAS utiliser

- **Disque du conteneur Railway** : éphémère, pas de CDN, pas d'URL stable.
- **Railway Volumes** : pas exposés en HTTPS public, pas de CDN, pas de range edge.
- **PostgreSQL (bytea / large objects)** : interdit (coût, backups, streaming).
- **GitHub / le dépôt** : aucun binaire vidéo dans les repos.

---

## Contrat côté app

Le client Flutter lit `video_url` tel quel depuis
`GET /api/app/content/relaxation-videos`, joue la vidéo **en fond, toujours
muette** (`volume = 0`), en streaming. Aucun credential, aucune signature, aucune
logique de stockage côté mobile. Le **son** de la méditation vient
**exclusivement** du MP3 (`meditation_catalog.audio_url`). Si la vidéo échoue
(404, format non supporté, réseau coupé) l'app garde un **fond statique** et
**l'audio continue**. Changer de fournisseur plus tard = ré-héberger sous le
**même domaine** — `version` s'incrémente, l'ETag change, l'app récupère les
nouvelles URL **sans mise à jour**.
