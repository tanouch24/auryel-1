# AdMob Rewarded SSV Auryel

Le crédit d'une publicité Rewarded est effectué uniquement par le callback
SSV signé par Google. Le callback client Flutter affiche l'état de validation
et ne crédite jamais le wallet.

## Configuration AdMob

Pour l'unité Rewarded Android Auryel :

- unité Rewarded SDK : `ca-app-pub-9787163762873138/6173561021`
- `ad_unit` SSV accepté : `6173561021` (format numérique documenté par Google)
  ou l'identifiant complet ci-dessus si transmis par l'environnement
- reward amount : `1`
- reward item : `consultation_question`
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
5. insère `transaction_id` dans `admob_reward_events` et crédite une question
   conseiller dans une seule transaction PostgreSQL. Chaque dixième callback
   crédite en plus 300 secondes, sans remplacer les questions disponibles.

`transaction_id` est la clé primaire. Un callback Google rejoué répond 200
avec `already_processed` et ne produit aucun second crédit. Une réponse 200
est également nécessaire pour que Google ne réessaie pas indéfiniment un
callback déjà traité.

## Variables

Le contrat SSV actif est fixe : `reward_item=consultation_question` et
`reward_amount=1`. Il ne contient aucun secret. Les clés publiques sont téléchargées depuis le
serveur officiel `https://www.gstatic.com/admob/reward/verifier-keys.json`.

Les tables `admob_reward_sessions` et `admob_reward_events` sont créées par
la migration v58 (`migrations/031_admob_rewarded_ssv.sql`). La migration v59
(`migrations/032_rewarded_ad_12_stars.sql`) conserve uniquement l'historique
de l'ancien contrat et n'est plus utilisé pour le crédit Consultation.
