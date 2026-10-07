import io
import json
import os
import pytest

from PIL import Image
from werkzeug.datastructures import FileStorage

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")

import auryel_bot as A


def _photo_bytes(fmt="JPEG", size=(720, 960)):
    image = Image.new("RGB", size, color=(170, 140, 110))
    output = io.BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def test_photo_normalization_orients_and_resizes_in_memory():
    with A.app.test_request_context("/", method="POST"):
        upload = FileStorage(
            stream=io.BytesIO(_photo_bytes(size=(2400, 1800))),
            filename="hand.jpg",
            content_type="image/jpeg",
        )
        normalized = A._normalize_explorer_photo(upload)
    decoded = Image.open(io.BytesIO(normalized))
    assert decoded.format == "JPEG"
    assert max(decoded.size) <= A._EXPLORER_PHOTO_MAX_EDGE
    assert decoded.width >= 480 and decoded.height >= 480


def test_photo_normalization_rejects_mime_mismatch():
    with A.app.test_request_context("/", method="POST"):
        upload = FileStorage(
            stream=io.BytesIO(_photo_bytes("PNG")),
            filename="photo.jpg",
            content_type="image/jpeg",
        )
        try:
            A._normalize_explorer_photo(upload)
        except A.ExplorerPhotoError as exc:
            assert exc.code == "invalid_photo_type"
            assert exc.status == 415
        else:
            raise AssertionError("mismatched image MIME must be rejected")


def test_photo_normalization_rejects_missing_corrupt_and_oversized_uploads():
    with A.app.test_request_context("/", method="POST"):
        for upload in (
            None,
            FileStorage(
                stream=io.BytesIO(b"not an image"),
                filename="photo.jpg",
                content_type="image/jpeg",
            ),
            FileStorage(
                stream=io.BytesIO(b"x" * (A._EXPLORER_PHOTO_MAX_BYTES + 1)),
                filename="large.jpg",
                content_type="image/jpeg",
            ),
        ):
            try:
                A._normalize_explorer_photo(upload)
            except A.ExplorerPhotoError:
                pass
            else:
                raise AssertionError("invalid photo upload must be rejected")


def test_photo_vision_generation_is_one_call_and_returns_structured_result(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "quality_ok": True,
                "quality_reason": "",
                "hook": "Sur ta photo, trois lignes se distinguent avec des reliefs différents, et c'est leur contraste qui donne sa couleur à cette paume.",
                "observations": {
                    "heart_line": {"visible": True, "description": "La ligne traverse la paume avec une courbe douce et régulière.", "features": ["courbe douce", "tracé régulier"], "confidence": 0.82},
                    "head_line": {"visible": True, "description": "La ligne centrale paraît longue et légèrement inclinée vers le bord externe.", "features": ["longue", "légèrement inclinée"], "confidence": 0.76},
                    "life_line": {"visible": True, "description": "Elle dessine un arc assez ouvert autour de la base du pouce.", "features": ["arc ouvert", "tracé continu"], "confidence": 0.71},
                },
                "reading": {
                    "heart_line": "Sur ta photo, ta ligne de cœur paraît régulière et sa courbe reste douce sur la partie visible. Dans une lecture symbolique, cette continuité évoque une façon d'accorder de la place aux liens sans devoir dramatiser chaque émotion.",
                    "head_line": "Ta ligne de tête semble s'étirer et s'incliner légèrement. Symboliquement, ce mouvement est souvent associé à une pensée qui aime relier les faits à l'intuition avant de choisir sa direction.",
                    "life_line": "Ta ligne de vie forme un arc ouvert autour du pouce, sans que sa longueur puisse dire quoi que ce soit sur la durée de vie. Dans une lecture symbolique, cet espace évoque plutôt une manière de préserver ton élan tout en gardant un ancrage familier.",
                    "synthesis": "Ce qui ressort ici, c'est l'association d'une ligne de cœur plutôt régulière et d'une ligne de tête orientée : le lien et la réflexion semblent pouvoir avancer ensemble. L'arc ouvert de la ligne de vie ajoute une image d'équilibre entre appui et mouvement, sans transformer ces signes en vérité sur toi.",
                    "guide_question": "Dans une décision qui compte pour toi en ce moment, quelle place aimerais-tu laisser à ton ressenti avant de tout analyser ?",
                },
                "guide_summary": "La photo montre une ligne de cœur régulière, une ligne de tête légèrement inclinée et un arc de vie ouvert. La lecture symbolique relie sensibilité et réflexion; explorer la place du ressenti dans ses décisions.",
                "confidence": 0.62,
            })}}], "usage": {}}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(A, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(A.requests, "post", fake_post)
    monkeypatch.setattr(A, "_record_llm_usage", lambda **kwargs: None)
    monkeypatch.setattr(A, "_llm_output_safety_filter", lambda _value: (True, None))
    result = A._explorer_photo_generate_once("palm", {}, b"normalized-jpeg")
    assert len(calls) == 1
    assert calls[0][0] == "https://api.openai.com/v1/chat/completions"
    assert calls[0][1]["json"]["response_format"] == {"type": "json_object"}
    assert result["quality_ok"] is True
    assert result["observations"]["heart_line"]["features"]
    assert result["reading"]["heart_line"].startswith("Sur ta photo")
    assert len(result["reading"]["synthesis"]) > 80
    assert result["reading"]["guide_question"].endswith("?")
    assert result["guide_summary"].startswith("La photo")
    assert "lifespan" not in result and "duration_of_life" not in result
    assert len(calls[0][1]["json"]["messages"]) == 2
    prompt = calls[0][1]["json"]["messages"][0]["content"]
    assert "ne renseigne jamais sur la durée de vie" in prompt


def test_palm_rejects_poor_visible_only_result(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "quality_ok": True,
                "quality_reason": "",
                "hook": "Une lecture de ta main se dessine avec prudence et attention.",
                "observations": {
                    line: {"visible": True, "description": "visible", "features": ["visible"], "confidence": 0.7}
                    for line in ("heart_line", "head_line", "life_line")
                },
                "reading": {
                    "heart_line": "visible", "head_line": "visible", "life_line": "visible",
                    "synthesis": "Les lignes sont nettes et peuvent parler de plusieurs aspects de ta personnalité.",
                    "guide_question": "Qu'as-tu envie d'explorer avec ton guide aujourd'hui ?",
                },
                "guide_summary": "Les lignes sont visibles.", "confidence": 0.7,
            })}}], "usage": {}}

    monkeypatch.setattr(A, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(A.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(A, "_record_llm_usage", lambda **kwargs: None)
    try:
        A._explorer_photo_generate_once("palm", {}, b"normalized-jpeg")
    except RuntimeError as exc:
        assert str(exc) == "explorer_invalid_generation"
    else:
        raise AssertionError("a generic visible-only Palm result must be rejected")


@pytest.mark.parametrize(
    "photo_case",
    [
        "NORMAL_PHONE_PALM_PHOTO",
        "SLIGHTLY_IMPERFECT_LIGHTING",
        "MINOR_CROP",
        "MAIN_LINES_VISIBLE",
    ],
)
def test_palm_usable_phone_photos_are_accepted_even_with_uncertainty(
    monkeypatch, photo_case
):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "quality_ok": True,
                "quality_reason": "",
                "hook": "Ta paume reste lisible malgré une légère variation de lumière, et ses lignes principales offrent une base suffisante à cette lecture symbolique.",
                "observations": {
                    "heart_line": {"visible": True, "description": "La ligne est discernable sur la majeure partie visible de la paume, avec une courbe douce.", "features": ["courbe douce"], "confidence": 0.48},
                    "head_line": {"visible": True, "description": "Le tracé central reste identifiable malgré un contraste modéré sur son extrémité.", "features": ["tracé identifiable"], "confidence": 0.43},
                    "life_line": {"visible": True, "description": "L'arc autour du pouce apparaît sur la zone cadrée, sans détail secondaire certain.", "features": ["arc visible"], "confidence": 0.39},
                },
                "reading": {
                    "heart_line": "Sur la partie visible, ta ligne de cœur semble dessiner une courbe assez douce. Dans une lecture symbolique, elle peut évoquer une manière d'approcher les liens avec sensibilité, sans que cette image définisse qui tu es.",
                    "head_line": "Le tracé central paraît identifiable, même si son extrémité manque un peu de contraste. Symboliquement, on peut y voir une invitation à laisser coexister réflexion et intuition, sans tirer de conclusion trop précise.",
                    "life_line": "L'arc autour du pouce est visible dans la zone cadrée, mais ses détails restent incertains. Cette ligne est lue symboliquement comme une image d'élan et ne renseigne pas sur la durée de vie.",
                    "synthesis": "Les lignes principales offrent ici des repères suffisants, tandis que leur contraste inégal invite à garder la lecture ouverte. Le lien entre sensibilité et réflexion peut servir de piste, plutôt que de portrait définitif.",
                    "guide_question": "Quelle part de cette lecture résonne le plus avec ce que tu traverses en ce moment ?",
                },
                "guide_summary": "Paume globalement lisible; lignes principales visibles avec détails incertains. Lecture symbolique prudente autour du lien et de la réflexion.",
                "confidence": 0.44,
            })}}], "usage": {}}

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(A, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(A.requests, "post", fake_post)
    monkeypatch.setattr(A, "_record_llm_usage", lambda **kwargs: None)
    monkeypatch.setattr(A, "_llm_output_safety_filter", lambda _value: (True, None))
    result = A._explorer_photo_generate_once("palm", {"case": photo_case}, b"jpeg")

    assert len(calls) == 1
    assert result["quality_ok"] is True
    assert result["confidence"] == 0.44
    assert result["observations"]["life_line"]["confidence"] == 0.39
    prompt = calls[0]["json"]["messages"][0]["content"]
    assert "seuil de qualité inclusif" in prompt
    assert "flou sévère" in prompt
    assert "petit décentrage" in prompt


@pytest.mark.parametrize(
    "reason",
    [
        "Aucune main identifiable.",
        "Le dos de la main est seul visible.",
        "La paume est presque entièrement hors cadre.",
        "L'image est presque totalement noire.",
        "Le flou sévère empêche de distinguer les lignes principales.",
    ],
)
def test_palm_still_rejects_unusable_photos_in_single_vision_call(
    monkeypatch, reason
):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "quality_ok": False,
                "quality_reason": reason,
            })}}], "usage": {}}

    monkeypatch.setattr(A, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(A.requests, "post", lambda *args, **kwargs: (calls.append(1) or Response()))
    monkeypatch.setattr(A, "_record_llm_usage", lambda **kwargs: None)
    result = A._explorer_photo_generate_once("palm", {}, b"jpeg")
    assert result == {"quality_ok": False, "quality_reason": reason}
    assert len(calls) == 1


def test_palm_safety_rejects_lifespan_and_medical_claims():
    assert not A._palm_result_claims_safe({"reading": {"life_line": "Tu vivras longtemps."}})
    assert not A._palm_result_claims_safe({"reading": {"heart_line": "Tu as une maladie."}})
    assert A._palm_result_claims_safe({"reading": {"life_line": "La ligne de vie est lue comme un symbole, pas comme une durée de vie."}})


def test_photo_quality_rejection_returns_no_interpretation(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "quality_ok": False,
                "quality_reason": "La tasse est trop sombre.",
            })}}], "usage": {}}

    monkeypatch.setattr(A, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(A.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(A, "_record_llm_usage", lambda **kwargs: None)
    result = A._explorer_photo_generate_once("coffee", {}, b"normalized-jpeg")
    assert result == {
        "quality_ok": False,
        "quality_reason": "La tasse est trop sombre.",
    }


def test_photo_input_never_persists_photo_or_user_content():
    input_data, error = A._structured_explorer_input("palm", {"idempotency_key": "x"})
    assert error is None
    assert input_data == {}
    assert A._validate_explorer_experience_type("palm") == "palm"
    assert A._validate_explorer_experience_type("coffee") == "coffee"


def test_palm_guide_context_uses_only_persisted_summary_not_photo():
    raw_marker = "PRIVATE_RAW_IMAGE_MARKER"
    row = (
        "reading-1", "user-1", "palm", "luna", {},
        {"guide_summary": "La paume associe une courbe régulière à une ligne de tête inclinée.",
         "raw_photo": raw_marker, "hook": "Long hook not needed for guide context."},
        "2026-10-07T10:00:00Z",
    )
    context = A.render_structured_explorer_context(row)
    assert "La paume associe" in context
    assert raw_marker not in context
    assert "hook" not in context
