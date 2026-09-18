# Recommandations de contenu dans Consultation

Le chemin app peut demander au modèle une réponse JSON `{reply, recommendation}`.
La recommandation est facultative et porte seulement un type (`ebook`,
`meditation`, `exercise`) et un identifiant. Le serveur revalide toujours cet
identifiant contre le catalogue actif et renvoie le snapshot canonique à
Flutter ; une valeur inconnue ne produit donc aucune carte.

Une nouvelle recommandation d’ebook est autorisée au maximum une fois sur une
fenêtre glissante de 30 jours par compte. Le même ebook peut être rappelé. Les
méditations et exercices n’ont pas de quota mensuel, mais le même contenu est
dédupliqué sur une courte fenêtre pour éviter le spam. Les recommandations sont
liées au conseiller stable, au compte, au message assistant et à l’historique.

`POST /api/app/content-recommendations/<id>/event` accepte uniquement
`opened` et, pour un ebook, `download_requested`. Les écritures sont
idempotentes et protégées par Bearer auth + isolation `user_id`.

Le suivi ebook est produit par le job `personal_guidance` existant, à J+3,
avec ses caps/cooldown/déduplication. Il dit « commencé » uniquement si une
ouverture est connue, sinon « jeter un œil » ; une ouverture ou un téléchargement
ne signifie jamais que le livre a été lu.

Flutter réutilise le lecteur ebook, `MeditationScreen` et
`ExerciseDetailScreen`. Le téléchargement passe par un fichier temporaire puis
le partage/enregistrement système Android ; aucune permission de stockage n’est
ajoutée. Limitation actuelle : le contrat JSON dépend de l’obéissance du
fournisseur LLM ; toute sortie non JSON reste une réponse texte sans carte.
