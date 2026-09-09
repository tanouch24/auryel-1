# Inventaire audio Auryel — lecture seule (Partie D.5)

Recensement effectué **sans déplacer, renommer, modifier ni committer** aucun
fichier audio. Aucun fichier audio n'est copié dans les dépôts. Aucune
destination de stockage n'est autorisée dans ce lot : **rien n'a été téléversé.**

## Dossier des 50 méditations finalisées (retenu)

| Chemin | `~/Desktop/AURYEL_70_MEDITATIONS/` |
|---|---|
| `audios/` | **50 fichiers `.mp3`** — **176,4 Mo** au total |
| `textes/` | 70 scripts `.txt` (numérotés 01–70 ; 50 sont enregistrés en audio) |
| `MANIFESTE.txt` | présent — 70 lignes `NN \| <code voix> \| <catégorie> \| <titre> \| <NN> mots` |

Nommage des audios : `NN-slug-en-toutes-lettres.mp3`
(ex. `01-quand-tout-devient-trop-lourd.mp3`) — directement exploitable comme
`slug` dans `meditation_catalog`.

Catégories vues dans `MANIFESTE.txt` : « Stress & calme », « Confiance »,
« Amour & relations », « Sommeil », etc.

## Autres dossiers audio trouvés sur le Desktop (non retenus — bruts / tests / vocaux)

| Chemin | Contenu |
|---|---|
| `~/Desktop/Auryel-Meditations/audios-bruts/` | 51 `.wav` + 5 `.mp3` (75,9 Mo) — pistes brutes / itérations de test |
| `~/Desktop/Auryel-Meditations/audios-bruts/batch-10/` | 10 `.mp3` (50,1 Mo) |
| `~/Desktop/Auryel-Meditations/` (racine) | 9 `.wav` (7,7 Mo) + 8 scripts `.py` + 1 `.sh` |
| `~/Desktop/Auryel_Vocaux_Conseillers/` | 10 `.mp3` (2,5 Mo) — vocaux de présentation des 10 conseillers (déjà en partie dans `auryel-app/assets/vocaux/`) |

## Aucun secret

Aucun jeton / clé / mot de passe trouvé dans ces dossiers ; `MANIFESTE.txt`
ne contient que des métadonnées éditoriales (les codes à 4 caractères sont des
étiquettes de lot de voix, pas des secrets — non reproduits ici).

## Import futur (hors périmètre de ce lot)

1. Téléverser les 50 `.mp3` sur un stockage **HTTPS autorisé**
   (`CONTENT_MEDIA_ALLOWED_HOSTS` restreindra le domaine).
2. Renseigner `audio_url` dans `docs/meditation_import_template.{json,csv}`.
3. POSTer chaque entrée sur `/admin/content/meditation` (auth admin + CSRF), ou
   lancer un script d'import qui réutilise la même validation `_validate_media_url`.
4. `is_active` / `published_at` / `sort_order` pilotent ensuite l'affichage sans
   republier l'application.
