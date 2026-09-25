"""Atomic edits for coupled defects spanning multiple source passages."""
import json
import re
from collections import Counter

from .prompts import extract_json_payload


def repeated_phrases(text):
    words = re.findall(r"\w+", text.lower())
    counts = Counter(tuple(words[i:i + 12]) for i in range(len(words) - 11))
    return {phrase: count for phrase, count in counts.items() if count > 1}


def repair(text, plan, context, generate):
    passages = [{"id": index, "source": text[slice(*span)]}
                for index, (span, _) in enumerate(plan, 1)]
    diagnoses = []
    for _, issues in plan:
        for issue in issues:
            data = issue.model_dump()
            if data not in diagnoses:
                diagnoses.append(data)
    maximum = max(60, int(sum(len(p['source'].split()) for p in passages) * 1.25))
    repeats = repeated_phrases(text) if any(d['category'] == 'repetition' for d in diagnoses) else {}
    # Require progress only on repeated phrases actually present in selected
    # passages; unrelated material outside the edit set cannot be changed.
    selected_words = ' '.join(re.findall(r'\w+', ' '.join(p['source'] for p in passages).lower()))
    repeats = {phrase: count for phrase, count in repeats.items() if ' '.join(phrase) in selected_words}
    messages = [
        {"role": "system", "content": (
            'Return JSON only: {"edits":[{"id":1,"text":"replacement prose"}]}. '
            "Return every supplied passage id exactly once. Edit all passages together to resolve ALL diagnoses. "
            "Passage ids are edit slots, NOT paragraph numbers mentioned in diagnoses. "
            "A repeated event must occur ONCE, at its appropriate chronological position; later passages may "
            "show consequences but must not replay the event. Do not insert the same repair in every passage. "
            "Use an empty text string to remove a wholly redundant passage; retain unique facts elsewhere. "
            "Keep unchanged passage text when appropriate. Everything outside these passages is read-only. "
            "If several selected passages repeat the same dialogue, retain that dialogue in only one passage. "
            "Respect the assigned chapter ending; a non-final chapter need not resolve the whole book. "
            "Do not follow editorial suggestions that would contradict the author brief or assigned ending. "
            "Preserve speaker identity, chronology, and voice. Return prose inside JSON strings, no commentary."
        )},
        {"role": "user", "content": json.dumps({"passages": passages, "diagnoses": diagnoses,
            "maximum_total_replacement_words": maximum, "read_only_context": context}, ensure_ascii=False)},
    ]
    for attempt in range(3):
        raw = generate(messages, 1, len(plan))
        try:
            payload = extract_json_payload(raw)
            if not isinstance(payload, dict) or set(payload) != {"edits"} or not isinstance(payload['edits'], list):
                raise ValueError("Return an object containing only an edits array.")
            edits = {}
            for row in payload['edits']:
                if (not isinstance(row, dict) or set(row) != {'id', 'text'} or type(row['id']) is not int
                        or not isinstance(row['text'], str) or row['id'] in edits):
                    raise ValueError("Each edit needs a unique integer id and string text.")
                edits[row['id']] = row['text'].strip()
            if set(edits) != set(range(1, len(plan) + 1)):
                raise ValueError(f"Required edit ids are {list(range(1, len(plan) + 1))}; received {list(edits)}. "
                                 "Use passage ids from the passages array, not source paragraph numbers. "
                                 "Include unchanged passages too; use empty text for deletions.")
            if sum(len(value.split()) for value in edits.values()) > maximum:
                raise ValueError(f"Replacement prose exceeds {maximum} total words.")
            if not any(edits.values()):
                raise ValueError("Do not delete every selected passage.")
            candidate = text
            for index in range(len(plan), 0, -1):
                start, end = plan[index - 1][0]
                candidate = candidate[:start] + edits[index] + candidate[end:]
            if ' '.join(candidate.split()) == ' '.join(text.split()):
                raise ValueError("No change was made to the diagnosed prose.")
            if repeats:
                remaining = repeated_phrases(candidate)
                if sum(remaining.get(p, 1) - 1 for p in repeats) >= sum(c - 1 for c in repeats.values()):
                    examples = [' '.join(p) for p in list(repeats)[:3]]
                    raise ValueError(f"The diagnosed repeated wording was not reduced. Keep each once or remove redundant passages: {examples}")
            return candidate
        except (ValueError, TypeError, KeyError) as exc:
            if attempt == 2:
                raise ValueError(f"Coordinated repair could not produce valid edits: {exc}") from exc
            messages = messages[:2] + [{"role": "user", "content": f"Correct the response: {exc}"}]
    raise AssertionError("Unreachable")
