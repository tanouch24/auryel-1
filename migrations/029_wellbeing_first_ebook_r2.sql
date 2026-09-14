-- 029_wellbeing_first_ebook_r2.sql — publication du premier ebook via R2
-- Additif et idempotent : l'entrée catalogue existante est mise à jour par son
-- slug stable, sans créer de doublon ni toucher aux données utilisateur.
UPDATE wellbeing_ebooks
SET pdf_url = 'https://pub-19c78d4dc57a41849a27c0e73ed231ce.r2.dev/ebooks/30-jours-pour-prendre-soin-de-soi/Auryel_30_jours_pour_prendre_soin_de_soi.pdf',
    cover_url = 'https://pub-19c78d4dc57a41849a27c0e73ed231ce.r2.dev/ebooks/30-jours-pour-prendre-soin-de-soi/Auryel_30_jours_pour_prendre_soin_de_soi_cover.png',
    updated_at = NOW()
WHERE slug = '30-jours-pour-prendre-soin-de-soi';
