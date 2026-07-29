"""Artifact annotation validation and model-context expansion."""

from coworker.annotations import append_annotation_context, validate_annotations
from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord


def _annotation(**overrides):
    value = {
        "id": "ann-1",
        "comment": "Make this total easier to notice.",
        "artifact": {
            "path": "output/invoice.pdf",
            "name": "invoice.pdf",
            "kind": "pdf",
            "sha256": "a" * 64,
        },
        "target": {
            "kind": "text",
            "page": 1,
            "exact": "Total payable",
            "rect": {"x": 0.6, "y": 0.7, "width": 0.2, "height": 0.05},
        },
        "preview": {
            "data_url": "data:image/png;base64,AA==",
            "width": 200,
            "height": 50,
        },
    }
    value.update(overrides)
    return value


def test_validate_annotations_sanitizes_supported_target():
    annotations, error = validate_annotations([_annotation()])
    assert error is None
    assert annotations[0]["target"]["page"] == 1
    assert annotations[0]["target"]["exact"] == "Total payable"
    assert annotations[0]["comment"] == "Make this total easier to notice."


def test_validate_annotations_rejects_bad_coordinates_and_combined_limit():
    bad = _annotation()
    bad["target"]["rect"]["width"] = 0
    assert validate_annotations([bad])[1] == "Invalid annotation target coordinates."
    assert "limit 8" in (validate_annotations([_annotation()], attachment_count=8)[1] or "")


def test_validate_annotations_rejects_non_hex_artifact_hash():
    bad = _annotation()
    bad["artifact"]["sha256"] = "z" * 64
    assert validate_annotations([bad])[1] == "Invalid annotation artifact identity."


def test_append_annotation_context_adds_grounding_and_preview_without_mutation():
    annotations, _ = validate_annotations([_annotation()])
    original = "Please revise this artifact."
    parts = append_annotation_context(original, annotations)
    assert original == "Please revise this artifact."
    assert parts[0] == {"type": "text", "text": original}
    text = "\n".join(part.get("text", "") for part in parts)
    assert "output/invoice.pdf" in text
    assert "Total payable" in text
    assert "Make this total easier to notice." in text
    images = [part for part in parts if part.get("type") == "image_url"]
    assert images[0]["image_url"]["url"] == "data:image/png;base64,AA=="


def test_annotation_display_sidecar_survives_conversation_store_reload(tmp_path):
    annotation = _annotation()
    store = ConversationStore(tmp_path)
    store.save(
        SessionRecord(
            session_id="annotation-session",
            workspace=str(tmp_path),
            model="gpt-5",
            mode="interactive",
            agent="cowork",
            messages=[
                {
                    "role": "user",
                    "content": "Please revise this.",
                    "_display": {
                        "text": "Please revise this.",
                        "annotations": [annotation],
                    },
                }
            ],
        )
    )

    reloaded = ConversationStore(tmp_path).load("annotation-session")
    assert reloaded is not None
    saved = reloaded.messages[0]["_display"]["annotations"][0]
    assert saved["artifact"]["sha256"] == "a" * 64
    assert saved["target"]["exact"] == "Total payable"
    assert saved["preview"]["data_url"] == "data:image/png;base64,AA=="
