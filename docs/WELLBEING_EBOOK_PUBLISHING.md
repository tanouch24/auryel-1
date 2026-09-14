# Publication des ebooks Bien-être Auryel

Ce document est la procédure officielle pour publier un ebook dans
`Bien-être → Bibliothèque Auryel`.

Un objet présent dans Cloudflare R2 seul n'est pas un ebook publié. La
publication est complète uniquement lorsque les fichiers, les URLs, le
catalogue et la vérification dans l'application sont tous terminés.

## Infrastructure

- Bucket R2 : `auryel-meditations`
- Domaine public :
  `https://pub-19c78d4dc57a41849a27c0e73ed231ce.r2.dev`
- Préfixe ebook : `ebooks/<slug>/`

Structure recommandée :

```text
ebooks/<slug>/ebook.pdf
ebooks/<slug>/cover.png
```

Pour le premier ebook, les noms historiques conservés sont :

```text
ebooks/30-jours-pour-prendre-soin-de-soi/Auryel_30_jours_pour_prendre_soin_de_soi.pdf
ebooks/30-jours-pour-prendre-soin-de-soi/Auryel_30_jours_pour_prendre_soin_de_soi_cover.png
```

Les credentials R2 doivent rester dans l'environnement de l'opérateur ou du
processus d'administration. Ils ne doivent jamais être ajoutés au dépôt,
aux logs, aux URLs ou à Flutter.

## Procédure mensuelle

### 1. Valider les assets

Préparer le PDF final et sa couverture. Vérifier localement que le PDF est
lisible et que la couverture correspond à l'ebook. Ne pas remplacer un asset
déjà publié sans validation éditoriale.

### 2. Uploader dans R2

Uploader les deux fichiers sous le préfixe stable du slug :

```text
ebooks/<slug>/ebook.pdf
ebooks/<slug>/cover.png
```

Utiliser les types suivants :

```text
ebook.pdf  -> application/pdf
cover.png  -> image/png
```

L'upload ne constitue pas encore une publication.

### 3. Vérifier les URLs publiques

Construire les URLs à partir du domaine public et de la clé R2. Vérifier
chaque ressource avec une requête HTTP :

```bash
curl -sSIL '<PDF_URL>'
curl -sSIL '<COVER_URL>'
```

Les deux réponses doivent être `200 OK`, avec respectivement
`Content-Type: application/pdf` et `Content-Type: image/png`. Ne jamais
utiliser un chemin local, `localhost` ou une URL temporaire.

### 4. Créer l'entrée catalogue

La table est `wellbeing_ebooks`. L'entrée doit contenir au minimum :

- `slug`
- `title`
- `subtitle`
- `description`
- `cover_url`
- `pdf_url`
- `publication_date`
- `month_label` si utilisé
- `version`
- `active = TRUE`
- `featured` selon le choix éditorial
- `push_type = 'ebook_monthly'`
- `push_title`
- `push_body`
- `push_deeplink = 'wellbeing_library'`
- `push_active` selon le choix de notification
- `notification_sent_at` laissé vide avant l'envoi réussi

Le slug est unique. Une mise à jour doit cibler ce slug stable et ne doit
jamais créer une seconde ligne pour le même ebook.

Pour une nouvelle publication durable, ajouter une migration SQL additive et
idempotente dans `migrations/`, puis son bloc correspondant dans `init_db()`.
La migration `029_wellbeing_first_ebook_r2.sql` est le modèle du premier
ebook publié. Une simple modification manuelle d'une base de production ne
constitue pas une configuration reproductible.

### 5. Vérifier le catalogue backend

Après application de la configuration, vérifier avec un compte authentifié :

```text
GET /api/app/wellbeing-ebooks
```

L'entrée doit apparaître avec ses URLs HTTPS R2, `active: true` et sa date de
publication atteinte. Le programme 30 jours doit continuer à référencer le
même ebook catalogue via `wellbeing_program_ebook_config.ebook_id`.

### 6. Vérifier dans l'application

Dans `Bien-être → Bibliothèque Auryel`, contrôler :

- couverture visible ;
- titre et sous-titre corrects ;
- bouton `Lire l'ebook` visible ;
- ouverture effective du PDF ;
- retour possible vers l'application ;
- accès identique pour Free et Premium ;
- aucune Étoile, aucun paiement et aucun temps de consultation consommé.

Si `pdf_url` ou `cover_url` est absent, l'application doit afficher un état
propre et ne pas afficher de bouton cassé. L'ebook n'est alors pas prêt à
être annoncé comme disponible.

## Push mensuel

Le catalogue prépare les données du futur push avec le type
`ebook_monthly`, l'identifiant de l'ebook, un titre, un corps et le deeplink
`wellbeing_library`. Le texte doit rester configurable côté serveur.

Le système Push production n'est pas déclenché par cette procédure. Lorsqu'il
sera activé, il devra :

1. sélectionner un ebook actif dont la date est atteinte ;
2. vérifier que `notification_sent_at` est vide ;
3. envoyer une seule notification pour cet ebook ;
4. renseigner `notification_sent_at` uniquement après un envoi réussi ;
5. laisser l'état inchangé en cas d'échec afin de permettre une reprise sûre.

La déduplication doit être faite côté serveur. Flutter ne doit jamais décider
qu'une notification a déjà été envoyée.

## Checklist de publication

- [ ] PDF final validé et lisible
- [ ] couverture validée
- [ ] PDF uploadé dans le bon préfixe R2
- [ ] couverture uploadée dans le bon préfixe R2
- [ ] types MIME vérifiés
- [ ] URL PDF HTTPS publique vérifiée en `200`
- [ ] URL couverture HTTPS publique vérifiée en `200`
- [ ] entrée `wellbeing_ebooks` créée par migration/configuration reproductible
- [ ] slug unique vérifié
- [ ] `active = TRUE`
- [ ] `publication_date` atteinte
- [ ] `push_type`, texte et deeplink renseignés
- [ ] programme 30 jours relié au bon ebook si nécessaire
- [ ] catalogue API vérifié
- [ ] couverture visible dans l'application
- [ ] bouton `Lire l'ebook` vérifié
- [ ] PDF ouvert réellement
- [ ] accès Free et Premium vérifié
- [ ] aucun push envoyé avant validation complète
- [ ] `notification_sent_at` renseigné seulement après envoi réussi

## Automatisation future

Un script d'administration de type `publish_wellbeing_ebook.py` pourra
automatiser l'upload R2, la construction des URLs, la création idempotente du
catalogue et les contrôles HTTP. Il devra lire les credentials depuis des
variables d'environnement ou un gestionnaire de secrets, ne rien afficher de
sensible et ne devra pas envoyer de push sans action explicite.

Ce script n'est pas créé dans le présent lot ; la procédure manuelle reste la
référence jusqu'à sa validation.
