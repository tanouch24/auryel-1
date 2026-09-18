# Recommandations de contenu dans Consultation

Le chemin app peut demander au modèle une réponse JSON `{reply, recommendation}`.
La recommandation est facultative et porte le type `ebook` ou `meditation` et
un identifiant. Les ebooks viennent de `wellbeing_ebooks`; les méditations
viennent exclusivement de `meditation_video_catalog`. Le serveur revalide
toujours l’identifiant contre le catalogue actif et renvoie le snapshot
canonique à Flutter ; une valeur inconnue ne produit donc aucune carte.
Les nouvelles méditations sont des cartes de navigation vers l’espace
Méditations, sans lecteur audio dans le chat. Les anciennes lignes `meditation`
et `exercise` restent lisibles dans l’historique.

Une nouvelle recommandation d’ebook est autorisée au maximum une fois sur une
fenêtre glissante de 30 jours par compte. Le même ebook peut être rappelé. Les recommandations sont
liées au conseiller stable, au compte, au message assistant et à l’historique.

`POST /api/app/content-recommendations/<id>/event` accepte uniquement
`opened` et, pour un ebook, `download_requested`. Les écritures sont
idempotentes et protégées par Bearer auth + isolation `user_id`.

Le suivi ebook est produit par le job `personal_guidance` existant, à J+3,
avec ses caps/cooldown/déduplication. Il dit « commencé » uniquement si une
ouverture est connue, sinon « jeter un œil » ; une ouverture ou un téléchargement
ne signifie jamais que le livre a été lu.

Flutter réutilise le lecteur ebook. Une nouvelle recommandation de méditation
ouvre la bibliothèque sans lancer de MP3/MP4 depuis le chat. Les anciennes
cartes de méditation et d’exercice restent reconstruites depuis l’historique.
Le téléchargement passe par un fichier temporaire puis
le partage/enregistrement système Android ; aucune permission de stockage n’est
ajoutée. Une sortie non JSON reste une réponse texte, sauf si le fallback
serveur retrouve un titre ebook canonique, explicite, actif et unique dans le
catalogue courant.
