-- Profil onboarding utilisé comme hypothèse de première prise de contact.
-- Additif et rejouable : aucune donnée existante n'est supprimée ou réécrite.

ALTER TABLE app_profiles
    ADD COLUMN IF NOT EXISTS onboarding_profile_status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE app_profiles
    ADD COLUMN IF NOT EXISTS profile_self_description TEXT NOT NULL DEFAULT '';

DO $$
BEGIN
    ALTER TABLE app_profiles
        ADD CONSTRAINT app_profiles_onboarding_profile_status_check
        CHECK (onboarding_profile_status IN ('pending', 'presented', 'confirmed', 'corrected'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

CREATE INDEX IF NOT EXISTS idx_app_profiles_onboarding_profile_status
    ON app_profiles (onboarding_profile_status);
