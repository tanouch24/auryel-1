# Contrat `GET /api/app/content/meditations` — et garantie de compatibilité

Catalogue distant des méditations (« Ton Moment »), 100 % piloté en base
(`meditation_catalog`, Migration v43) + admin (`/admin/content/meditation`).
**Aucune liste figée côté Flutter, aucun plafond, aucun enum fermé de données.**
Une ligne backend valide de plus = une méditation de plus dans l'app **sans
release**.

## Requête

```
GET /api/app/content/meditations        (Bearer)
If-None-Match: "<catalog_version>"       (facultatif)
```

## Réponse `200`

```json
{
  "version": 1,
  "catalog_version": "b3f1…(32 hex)",
  "meditations": [
    {
      "id": "9d2c…-uuid",
      "slug": "quand-tout-devient-trop-lourd",
      "title": "Quand tout devient trop lourd",
      "description": "",
      "category": "stress-calme",
      "duration_seconds": 0,
      "audio_url": "https://media.auryel.app/meditations/01-….mp3",
      "image_url": null,
      "sort_order": 1,
      "published_at": null,
      "version": 1
    }
  ]
}
```

En-têtes : `ETag: "<catalog_version>"`, `Cache-Control: no-cache`.

- **Filtre** : `is_active = TRUE` **et** (`published_at IS NULL` ou `<= NOW()`).
  Les entrées inactives ou programmées dans le futur ne sont **jamais** servies.
- **Ordre** : `ORDER BY sort_order ASC, id ASC` — déterministe (départage stable
  par `id` UUID à `sort_order` égal).
- **Catalogue vide** : `meditations: []` avec un `catalog_version` stable (celui
  d'une liste vide). L'app retombe alors sur son cache / contenu embarqué.

## Réponse `304`

Si `If-None-Match` == `catalog_version` courant → `304`, corps `{}`, en-tête
`ETag`. L'app ne retélécharge rien.

## `catalog_version` / ETag

`sha256( "id:version:updated_at" | … pour chaque ligne servie )[:32]`.
Il **change** dès qu'une ligne servie est :

| Action admin | Effet | ETag change ? |
|---|---|---|
| **Création** d'une méditation (`POST /admin/content/meditation`) | +1 ligne servie | **oui** |
| **Édition** (même slug → upsert) | `version += 1`, `updated_at = NOW()` | **oui** |
| **Réordonnancement** (`sort_order` modifié) | passe par l'upsert → `version += 1` | **oui** |
| **Activation / désactivation** (`/toggle`) | `version += 1` ; ligne entre/sort du set servi | **oui** |
| Rien | — | non → `304` |

## Endpoints admin (existants, protégés : session `admin_logged` + `X-CSRF-Token`)

| Endpoint | Rôle |
|---|---|
| `POST /admin/content/meditation` | **créer OU modifier** (upsert par `slug`, `ON CONFLICT DO UPDATE`, `version += 1`) — champs : `slug, title, description, category, duration_seconds, sort_order, audio_url (HTTPS), image_url, published_at`. Idempotent : rejouer ne crée pas de doublon. |
| `POST /admin/content/meditation/toggle` | `{id, is_active}` → activer / désactiver, `version += 1`. |
| `GET /admin/content` | page HTML : liste + formulaires + jeton CSRF. |

`_validate_media_url` : `audio_url`/`image_url` doivent être **HTTPS**, bornées
(≤ 2048), hôte présent ; si `CONTENT_MEDIA_ALLOWED_HOSTS` est défini, l'hôte doit
y figurer.

## Compatibilité montante (une ancienne version publiée doit continuer à marcher)

- **Nouveaux champs** : toujours **optionnels**. Le parseur Flutter
  (`MeditationItem.tryFromJson`) lit `id`, `title`, `audio_url`, `asset_path`,
  `duration_seconds` / `duration_minutes`, `description`, `category` et **ignore
  toute clé inconnue** — pas d'`assert`, pas de schéma strict. Ajouter un champ
  au JSON (ex. `theme`, `narrator`, `transcript_url`) **ne casse aucune version**
  déjà publiée.
- **`id` / `title` manquants** → l'entrée est ignorée par l'app (jamais de
  crash) ; les autres passent.
- **`duration_seconds`** : `0` accepté (« durée inconnue », affichée « 0 s » puis
  corrigée au montage de l'audio).
- **`category`** : champ **texte libre** côté backend (taxonomie éditoriale :
  `stress-calme`, `confiance`, `amour`, `sommeil`, `motivation`,
  `rupture-manque`, `lacher-prise`, …). ⚠️ La version d'app **actuellement
  publiée** possède un enum d'affichage restreint
  (`detente / respiration / sommeil / recentrage / lacherPrise`) et **retombe
  sur « Détente » pour toute valeur non reconnue** — sans erreur. Conséquence :
  d'ici la prochaine release, les méditations dont la catégorie n'est pas
  `sommeil` s'affichent regroupées sous « Détente ». Ce n'est **pas** une
  dépendance de données (le backend n'est pas contraint par l'enum) : une future
  version d'app rendra les catégories libres. Si le regroupement gêne avant la
  release, un admin peut fixer `category` sur une valeur reconnue par l'app
  actuelle au cas par cas.
- **Retrait d'un champ** : à éviter — plutôt le laisser et le vider.
