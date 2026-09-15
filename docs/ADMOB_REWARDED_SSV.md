# AdMob Rewarded SSV Auryel

Le crédit d'une publicité Rewarded est effectué uniquement par le callback
SSV signé par Google. Le callback client Flutter affiche l'état de validation
et ne crédite jamais le wallet.

## Configuration AdMob

Pour l'unité Rewarded Android Auryel :

- unité : `ca-app-pub-6355299363807052/1344137680`
- reward amount : `6`
- reward item : `stars`
- URL SSV : `https://web-production-93330.up.railway.app/api/app/rewards/admob/ssv`

L'URL ne pourra répondre qu'après déploiement de la version backend qui
contient la migration SSV. Le test de l'outil AdMob doit utiliser une URL
HTTPS publique et une base où les migrations sont appliquées.

## Fonctionnement

Avant d'afficher l'annonce, l'app crée une session authentifiée. Son UUID
opaque est envoyé à AdMob dans `custom_data`; l'UUID du compte Auryel est
envoyé dans `user_id`. Aucun email, jeton ou secret n'est transmis.

Google appelle ensuite l'URL publique. Le serveur :

1. vérifie la signature ECDSA SHA-256 avec les clés officielles ;
2. conserve la chaîne de requête brute dans son ordre d'origine ;
3. recharge les clés si `key_id` est inconnu et ne garde jamais le cache plus
   de 24 heures ;
4. vérifie l'unité, le montant, le type de récompense, la transaction et la
   session liée au compte ;
5. insère `transaction_id` dans `admob_reward_events` et crédite +6 étoiles
   dans une seule transaction PostgreSQL.

`transaction_id` est la clé primaire. Un callback Google rejoué répond 200
avec `already_processed` et ne produit aucun second crédit. Une réponse 200
est également nécessaire pour que Google ne réessaie pas indéfiniment un
callback déjà traité.

## Variables

`ADMOB_REWARDED_REWARD_ITEM` est optionnelle et vaut `stars` par défaut. Elle
ne contient aucun secret. Les clés publiques sont téléchargées depuis le
serveur officiel `https://www.gstatic.com/admob/reward/verifier-keys.json`.

La table `admob_reward_sessions` et la table `admob_reward_events` sont
créées par la migration v58 (`migrations/031_admob_rewarded_ssv.sql`).
