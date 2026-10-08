import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A


def test_valid_names_are_normalised():
    assert A._validate_person_in_mind("  Thomas ") == ("Thomas", None)
    assert A._validate_person_in_mind("Jean-Marc") == ("Jean-Marc", None)
    assert A._validate_person_in_mind("Anne  Sophie") == ("Anne Sophie", None)
    assert A._validate_person_in_mind("Zoë") == ("Zoë", None)
    assert A._validate_person_in_mind("") == (None, None)


def test_invalid_values_are_rejected():
    for bad in (123, "Thomas2", "<script>", "a" * 41, "Thomas, Julie"):
        assert A._validate_person_in_mind(bad)[1] == "invalid_prenom_en_tete"


def test_merge_puts_name_first_without_duplicates():
    assert A._merge_prenoms_importants("", "Thomas") == "Thomas"
    assert A._merge_prenoms_importants("Julie, thomas", "Thomas") == "Thomas, Julie"
    assert A._merge_prenoms_importants(None, "Léa") == "Léa"
