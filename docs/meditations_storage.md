# Stockage des audios de méditation — recommandation

**But** : héberger les 50 MP3 (`~/Desktop/AURYEL_70_MEDITATIONS/audios/`, ~176 Mo)
puis les suivants (51, 52, … 500) sur un stockage **objet HTTPS + CDN**, sans
jamais mettre les fichiers dans PostgreSQL ni de secret dans l'app.

> ⚠️ Ce lot **ne téléverse rien**. Il prépare : schéma (déjà en place), manifest
> (`scripts/meditations_manifest.json`), script d'import
> (`scripts/import_meditations.py`), admin (déjà en place), tests. Le choix du
> stockage et l'upload réel se font au lot suivant, sur validation explicite.

---

## Contraintes

| Besoin | Pourquoi |
|---|---|
| URL HTTPS **stable** par fichier | `meditation_catalog.audio_url` (validé `_validate_media_url` : HTTPS obligatoire). Une URL qui change casse le catalogue. |
| **Range requests** (`Accept-Ranges: bytes`, `206`) | lecture en streaming / seek dans le lecteur Flutter (`just_audio` / natif) sans télécharger tout le MP3. |
| **CDN / cache** en edge | 50→500 fichiers servis à toute la base ; latence + coût de sortie maîtrisés. |
| **Coût raisonnable** | audio statique, trafic surtout en lecture. |
| **Pas de secret dans l'app** | le bucket est **public en lecture** (objets non listables) ; aucune clé, aucune signature côté client. |
| Domaine **fixe** | `CONTENT_MEDIA_ALLOWED_HOSTS` peut alors verrouiller l'hôte accepté par l'admin. |

---

## Recommandation : **Cloudflare R2** (+ domaine personnalisé)

**Pourquoi R2 en premier choix**
- **Zéro frais de sortie** (egress gratuit) — le poste de coût principal d'un
  catalogue audio grand public disparaît. Stockage ~0,015 $/Go/mois → 176 Mo ≈
  négligeable ; 5 Go (≈ 1 500 méditations) ≈ 0,08 $/mois.
- **S3-compatible** : `aws s3` / `rclone` / `boto3` fonctionnent tels quels pour
  l'upload (opération d'admin, hors dépôt).
- **CDN Cloudflare** natif + **range requests** supportées (`206 Partial
  Content`) → streaming/seek OK.
- **Domaine personnalisé** : brancher un sous-domaine (ex.
  `media.auryel.app` ou `cdn.auryelvoyance.com`) sur le bucket → URL stables
  `https://media.auryel.app/meditations/01-quand-tout-devient-trop-lourd.mp3`,
  indépendantes du compte R2.
- Bucket en **lecture publique** (via le domaine), **listing désactivé**.

**Mise en place (résumé — à faire au lot upload)**
1. Créer le bucket `auryel-media` (région auto).
2. Attacher le domaine personnalisé `media.auryel.app` (enregistrement CNAME géré
   par Cloudflare) → active le cache edge automatiquement.
3. Uploader sous le préfixe `meditations/` en conservant les noms
   `NN-slug.mp3` :
   `rclone copy ~/Desktop/AURYEL_70_MEDITATIONS/audios/ r2:auryel-media/meditations/ --content-type audio/mpeg`
4. Vérifier : `curl -I -H 'Range: bytes=0-1' https://media.auryel.app/meditations/01-quand-tout-devient-trop-lourd.mp3`
   → `206`, `Accept-Ranges: bytes`, `Content-Type: audio/mpeg`, `Cache-Control`.
5. Backend : `CONTENT_MEDIA_ALLOWED_HOSTS=media.auryel.app` (Railway) — l'admin
   ne pourra alors coller que des URL de ce domaine.
6. Import : `python3 scripts/import_meditations.py --base-url https://<backend>
   --audio-base-url https://media.auryel.app/meditations`.

---

## Alternatives équivalentes (si R2 indisponible)

| Option | Verdict |
|---|---|
| **AWS S3 + CloudFront** | Fonctionne parfaitement (range + CDN). Moins bon sur le coût : egress CloudFront facturé (~0,085 $/Go). OK si volume faible, mais R2 reste préférable. |
| **Backblaze B2 + Cloudflare** (Bandwidth Alliance) | Egress gratuit vers Cloudflare, S3-compatible, bon marché. Bonne option n°2. Domaine via Cloudflare. |
| **Bunny.net Storage + CDN** | Simple, pas cher, range OK, domaine perso inclus. Bonne option n°3. |
| **DigitalOcean Spaces + CDN** | S3-compatible, CDN inclus, egress 1 Go offert/mois puis facturé. Correct. |

## À NE PAS utiliser

- **Le disque du conteneur Railway** : éphémère (perdu à chaque redéploiement),
  pas de CDN, pas d'URL stable. → non.
- **Railway Volumes** : persistants mais **pas exposés en HTTPS public**, pas de
  CDN, pas de range servi par un edge — inadaptés à de l'audio grand public
  servi mondialement. À réserver à des données applicatives, pas à des médias.
- **PostgreSQL (bytea / large objects)** : interdit (coût, backups, streaming).
- **GitHub / le dépôt** : pas de binaires audio dans les repos.

---

## Contrat côté app (inchangé)

Le client Flutter lit `audio_url` tel quel depuis
`GET /api/app/content/meditations`. Aucun credential, aucune signature, aucune
logique de stockage côté mobile. Changer de fournisseur plus tard = ré-héberger
sous le **même domaine** (ou faire un ré-import avec les nouvelles URL) —
`meditation_catalog.version` s'incrémente, l'ETag change, l'app récupère les
nouvelles URL sans mise à jour.
