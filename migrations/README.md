# migrations/

Historique versionné des modifications de schéma de la base Auryel.

## Règle en vigueur

Toute nouvelle modification de schéma (nouvelle colonne, nouvel index, contrainte, etc.)
doit être faite en **deux endroits simultanément** :

1. **Un nouveau fichier ici**, numéroté en séquence (`002_...sql`, `003_...sql`, etc.)
2. **Un nouveau bloc dans `init_db()`** dans `auryel_bot.py` (migration vN+1), avec
   `IF NOT EXISTS` et son propre `try/except`

Les deux restent en parallèle jusqu'à ce qu'un système de migrations automatique soit
mis en place (prévu dans un sous-lot ultérieur). En attendant, `init_db()` est la source
d'exécution réelle ; les fichiers ici sont la source de vérité lisible.

## Fichiers

| Fichier | Description | Date |
|---------|-------------|------|
| `000_schema_initial.sql` | État complet de la DB à l'ouverture du versionnage | 2026-06-17 |
| `001_index_stripe_customer.sql` | Index unique partiel sur `stripe_customer_id` | 2026-06-17 |
| `002_admin_logs.sql` | Table `admin_logs` — traçabilité des actions admin | 2026-06-17 |

## Référence

- Schéma complet actuel : `schema.sql` à la racine du repo
- Exécution réelle au démarrage : `init_db()` dans `auryel_bot.py` (L.112+)
