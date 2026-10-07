import io
import json
import os

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
                "visible_elements": ["Une ligne fine au centre"],
                "interpretation": {
                    "heart_line": "Non lisible",
                    "head_line": "Une ligne discrète",
                    "life_line": "Non lisible",
                    "overall": "Une lecture symbolique prudente.",
                },
                "reflection": "Qu’as-tu envie d’éclaircir ?",
                "guide_summary": "La paume montre une ligne centrale discrète.",
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
    assert result["guide_summary"].startswith("La paume")


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
