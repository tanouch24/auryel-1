# Checklist release Android — Push et Billing

Cette note décrit les éléments qui restent à configurer hors du code. Aucun
secret, jeton FCM ou fichier de compte de service ne doit être commité.

## Push Firebase

Le code utilise Firebase Cloud Messaging HTTP v1. Les variables serveur sont
fournies par l'environnement Railway :

- `FIREBASE_PROJECT_ID`
- `FIREBASE_SERVICE_ACCOUNT_JSON`
- `PUSH_ENABLED`
- `PUSH_DRY_RUN`
- `PUSH_CRON_SECRET`

Le compte de service doit avoir le droit d'envoyer des messages FCM. Vérifier
d'abord avec `PUSH_DRY_RUN=true`, puis activer les envois après un test ciblé.
Les routes `/cron/push-tick` et `/cron/push-test` exigent le secret cron ; le
secret ne doit jamais apparaître dans Flutter, Git ou les journaux.

Les types `wellbeing_daily` et `ebook_monthly` passent par le même moteur que
les notifications historiques. Le premier est limité aux programmes ayant
activé le rappel ; le second utilise `notification_sent_at` et une clé de
déduplication fondée sur l'identifiant de l'ebook.

## Billing Google Play

Produits utilisés par l'application :

- abonnement : `auryel_premium_monthly`
- consommable : `auryel_extra_hour`

Le serveur vérifie le purchase token auprès de Google avant tout entitlement
ou crédit. Les écritures sont idempotentes : un abonnement est reprojeté et un
consommable ne crédite qu'une seule fois par purchase token.

Avant release, vérifier manuellement dans Play Console :

1. `auryel_premium_monthly` est bien un abonnement actif avec un prix France
   de 4,99 € par mois et un base plan actif.
2. `auryel_extra_hour` est actif avec son prix commercial voulu.
3. le compte de licence de test et la piste de test sont correctement liés.

L'application n'affiche l'identifiant App Open de production que lorsque
`ADMOB_APP_OPEN_PROD_ID` est fourni au build release. Aucun identifiant n'est
inventé dans le dépôt.

## SSV Rewarded AdMob

La récompense rewarded est déjà protégée côté client contre les doubles taps
et côté serveur par l'événement idempotent. La vérification SSV AdMob reste
une exigence avant production : créer/configurer la callback URL SSV dans
AdMob, transmettre son identifiant signé au backend et vérifier la signature
avec la clé publique AdMob. Ne jamais remplacer cette vérification par la
confiance dans un simple appel Flutter et ne jamais mettre une clé secrète
dans l'APK.
