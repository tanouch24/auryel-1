-- migrations/003_fk_messages_users.sql
-- Contrainte FK messages.phone → users.phone avec ON DELETE CASCADE
-- Garantit qu'aucun message orphelin ne peut exister en base.
-- Déjà appliqué en production via init_db() migration v8.

ALTER TABLE messages
    ADD CONSTRAINT fk_messages_phone
    FOREIGN KEY (phone) REFERENCES users(phone)
    ON DELETE CASCADE;
