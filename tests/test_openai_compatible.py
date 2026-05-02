from __future__ import annotations

from novel_generator.services.openai_compatible import parse_openai_chat_payload


def test_parse_openai_chat_payload_reads_string_content() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Hello from a compatible endpoint.",
                }
            }
        ]
    }

    assert parse_openai_chat_payload(payload) == "Hello from a compatible endpoint."


def test_parse_openai_chat_payload_reads_array_content() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ]
                }
            }
        ]
    }

    assert parse_openai_chat_payload(payload) == "Hello world"
