# Contrat `GET /api/app/content/relaxation-videos` — et garantie de compatibilité

Catalogue distant des **vidéos d'ambiance** jouées EN FOND (toujours **muettes**)
d'une méditation, 100 % piloté en base (`relaxation_video_catalog`, Migration
v45) + admin (`/admin/content/relaxation-video`). **Aucune liste figée côté
Flutter, aucun plafond, aucun enum fermé de catégorie.** Une ligne backend valide
de plus = une vidéo de plus dans l'app **sans release**. Le système est conçu
pour fonctionner **avec zéro vidéo** (l'app retombe sur un fond statique).

## Requête

```
GET /api/app/content/relaxation-videos     (Bearer)
If-None-Match: "<catalog_version>"          (facultatif)
```

## Réponse `200`

```json
{
  "version": 1,
  "catalog_version": "b3f1…(32 hex)",
  "generic_category": "calm",
  "compatibility_map": {
    "stress-calme":   ["ocean", "forest", "clouds", "rain", "nature"],
    "sommeil":        ["night", "rain", "ocean", "clouds", "stars"],
    "confiance":      ["mountains", "forest", "sunrise", "nature", "space"],
    "amour":          ["sunset", "ocean", "nature", "clouds"],
    "rupture-manque": ["rain", "ocean", "night", "clouds"],
    "motivation":     ["mountains", "sunrise", "forest", "space"],
    "lacher-prise":   ["ocean", "clouds", "forest", "river"]
  },
  "videos": [
    {
      "id": "9d2c…-uuid",
      "slug": "relaxation-11210466",
      "title": "Ambiance apaisante 01",
      "description": "",
      "video_url": "https://media.auryel.app/relaxation-videos/11210466-….mp4",
      "thumbnail_url": null,
      "category": "calm",
      "tags": [],
      "sort_order": 1,
      "published_at": null,
      "version": 1,
      "compatible_meditation_categories": [],
      "is_generic": true
    }
  ]
}
```

En-têtes : `ETag: "<catalog_version>"`, `Cache-Control: no-cache`.

- **Filtre** : `is_active = TRUE` **et** (`published_at IS NULL` ou `<= NOW()`).
  Les entrées inactives ou programmées dans le futur ne sont **jamais** servies.
  `video_url` est `NOT NULL` (schéma) et validée HTTPS à l'écriture admin → toute
  vidéo servie a une URL exploitable.
- **Ordre** : `ORDER BY sort_order ASC, id ASC` — déterministe.
- **Catalogue vide** : `videos: []` avec un `catalog_version` stable. L'app
  affiche alors un fond statique ; l'absence de vidéo **ne bloque jamais** une
  méditation.

## Réponse `304`

Si `If-None-Match` == `catalog_version` courant → `304`, corps `{}`, en-tête
`ETag`. L'app ne retélécharge rien.

## `catalog_version` / ETag

`sha256( "id:version:updated_at" | … pour chaque ligne servie )[:32]`
(fonction commune `_catalog_version_tag`, partagée avec les méditations).
Il **change** dès qu'une ligne servie est créée, éditée, réordonnée, activée ou
désactivée. Sinon → `304`.

| Action admin | Effet | ETag change ? |
|---|---|---|
| **Création** (`POST /admin/content/relaxation-video`) | +1 ligne servie | **oui** |
| **Édition** (même slug → upsert) | `version += 1`, `updated_at = NOW()` | **oui** |
| **Réordonnancement** (`sort_order`) | passe par l'upsert → `version += 1` | **oui** |
| **Activation / désactivation** (`/toggle`) | `version += 1` ; ligne entre/sort du set servi | **oui** |
| Rien | — | non → `304` |

## Compatibilité méditation ↔ vidéo

**Pas de relation 1:1.** À la lecture d'une méditation, l'app choisit
**automatiquement** une vidéo compatible **au hasard** parmi les compatibles.

- Le mapping est une **config backend** : `_MEDITATION_VIDEO_COMPATIBILITY`
  (`auryel_bot.py`). Le modifier ne nécessite **aucune migration**. Il est
  renvoyé en entier dans `compatibility_map` (transparence / debug).
- Chaque vidéo porte deux champs calculés :
  - `compatible_meditation_categories` : catégories de méditation dont la liste
    de correspondance intersecte `{category} ∪ tags` de la vidéo.
  - `is_generic` : `true` si `category == "calm"` **ou** si la vidéo ne matche
    **aucune** entrée du mapping. Une vidéo générique est utilisable pour
    **toutes** les méditations (repli — implémente la consigne « si aucune
    compatibilité trouvée → toutes les vidéos actives, marquées calm/générique »).
- **Sélection recommandée côté app** :
  `compatibles = vidéos where (is_generic || compatible_meditation_categories.contains(catégorieMéditation))`
  puis, si `compatibles` est vide, **toutes** les vidéos actives.

## Compatibilité montante (une ancienne version publiée doit continuer à marcher)

- **Nouveaux champs** toujours **optionnels**. Une ancienne app qui ne lit que
  `video_url` / `title` n'est pas affectée par `tags`,
  `compatible_meditation_categories`, `is_generic`, `compatibility_map`,
  `generic_category`.
- **`category`** : champ **texte libre** backend (`ocean`, `rain`, `forest`,
  `night`, `space`, `calm`, …). Jamais un enum fermé. Défaut `calm`.
- **`tags`** : tableau de chaînes, éventuellement vide.
- **`id` / `video_url` manquants** → l'entrée doit être ignorée par l'app
  (jamais de crash) ; les autres passent.
- **Retrait d'un champ** : à éviter — plutôt le laisser et le vider.

## Endpoints admin (protégés : session `admin_logged` + `X-CSRF-Token`)

| Endpoint | Rôle |
|---|---|
| `POST /admin/content/relaxation-video` | **créer OU modifier** (upsert par `slug`, `ON CONFLICT DO UPDATE`, `version += 1`) — champs : `slug, title, description, category, tags, video_url (HTTPS), thumbnail_url, sort_order, published_at`. Idempotent. |
| `POST /admin/content/relaxation-video/toggle` | `{id, is_active}` → activer / désactiver, `version += 1`. |
| `GET /admin/content` | page HTML : listes + formulaires + jeton CSRF (méditations, vidéos, contenu du jour). |

`_validate_media_url` : `video_url` / `thumbnail_url` doivent être **HTTPS**,
bornées (≤ 2048), hôte présent ; si `CONTENT_MEDIA_ALLOWED_HOSTS` est défini,
l'hôte doit y figurer. `tags` : normalisés (`[a-z0-9_-]`, ≤ 40 car., ≤ 20 tags,
sans doublon) ; les tags invalides sont ignorés silencieusement.

## Son

Le **son de la méditation vient exclusivement du MP3**
(`meditation_catalog.audio_url`). La vidéo est **toujours muette** côté app
(`volume = 0`), même si le fichier possède une piste audio. En cas d'échec vidéo
(404, format non supporté, timeout, réseau coupé) : **l'audio continue**, la
vidéo est remplacée par un **fond statique Auryel**, aucune erreur bloquante.
