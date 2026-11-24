"""Compare installed Ollama writers on fixed fiction tasks without touching production runs."""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

from .author_model_profile import author_messages
from .ollama import OllamaClient


TASKS = (
    ("aftermath", "chapter_draft", """Write a 350–500 word scene in close third person.
Mara has already proved the forgery at a hearing and surrendered her job. The hearing is over.
At dawn she helps Ivo repair a ferry. She is ashamed to accept his help. He wants the ferry to
run before the first commuters arrive. The original ledger was paper and is now in court custody.
End with the ferry's first departure and Mara accepting one concrete task. Do not repeat the
hearing, restore her job, introduce a new conspiracy, or recount the plot in summary.
Use grounded details, differentiated dialogue and subtext. Return prose only."""),
    ("voice", "chapter_draft", """Write a 350–500 word scene set in a mundane apartment kitchen.
An estranged brother and sister, Owen and June, must decide what to do with their late mother's
untouched birthday cake. Owen wants to take it to the neighbors; June wants to throw it away.
Neither says why outright. No flashback, supernatural event, or new character enters the scene.
End with a concrete decision that changes their relationship. Return prose only."""),
    ("revision", "chapter_revision", """Revise the passage below into 200–300 words.
Keep the time (Tuesday morning), the owner of the key (Ivo), the locked gate, and Mara's decision
to leave without it. Make the dialogue less expository, remove repetitive fog imagery, and retain
the restrained third-person point of view. Do not change the outcome or introduce new evidence.
PASSAGE:
Tuesday morning, Mara stood at the locked gate. Fog was everywhere, a fog of uncertainty.
Ivo held the key. 'I cannot give this to you because the rules prohibit it,' he said.
'I understand the rules prohibit it,' Mara said. The fog was heavy. She stepped back and left
without the key, feeling the weight of her decision. Return prose only."""),
)

AUDITS = (
    ("consistent", "Earlier: Ivo kept the key. Later: Ivo unlocked the gate with his key.", False),
    ("contradiction", "Earlier: Ivo destroyed the only key. Later: Ivo unlocked the gate with that same undamaged key. No replacement or repair occurred.", True),
)


def run_audition(client, models, output: Path, *, seed: int = 42):
    output.mkdir(parents=True, exist_ok=False)
    # Save identities separately from the anonymized prose files. Review prose before
    # opening the key. This is a small audition, not a publication-quality score.
    ordered = list(models)
    random.Random(seed).shuffle(ordered)
    identities = {}
    records = []
    for index, model in enumerate(ordered, 1):
        label = f"writer-{index}"
        identities[label] = client.model_identity(model)
        for name, stage, prompt in TASKS:
            messages = [{"role": "system", "content": "You are a fiction writer. Return prose only."},
                        {"role": "user", "content": prompt}]
            messages = author_messages(messages, provider_name="ollama", model_name=model,
                                       stage=stage, content_kind="fiction")
            started = time.monotonic()
            try:
                prose = client.chat(model, messages)
                (output / f"{label}-{name}.txt").write_text(prose, encoding="utf-8")
                records.append({"writer": label, "task": name, "words": len(prose.split()),
                                "chapter_reference_hint": bool(re.search(r"\\bchapter\\s+\\d+", prose, re.I)),
                                "seconds": round(time.monotonic() - started, 2),
                                "metrics": dict(client.last_chat_metrics)})
            except Exception as exc:
                records.append({"writer": label, "task": name, "error": str(exc),
                                "seconds": round(time.monotonic() - started, 2)})
        for name, source, expected in AUDITS:
            messages = [{"role": "system", "content": 'Return JSON only: {"contradiction": true or false, "evidence": "exact source quote or empty string"}. Do not infer unstated facts.'},
                        {"role": "user", "content": source}]
            try:
                raw = client.chat(model, messages)
                (output / f"{label}-audit-{name}.txt").write_text(raw, encoding="utf-8")
                result = json.loads(raw)
                quote = result.get("evidence", "")
                valid = result.get("contradiction") is expected and (
                    not expected or isinstance(quote, str) and bool(quote.strip()) and quote in source)
                records.append({"writer": label, "task": f"audit-{name}", "passed": valid,
                                "metrics": dict(client.last_chat_metrics)})
            except Exception as exc:
                records.append({"writer": label, "task": f"audit-{name}", "passed": False, "error": str(exc)})
    (output / "blind-results.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (output / "model-key.json").write_text(json.dumps(identities, indent=2), encoding="utf-8")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-tokens", type=int, default=8192)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.context_tokens < 4096:
        parser.error("Use at least 4096 context tokens.")
    if len(set(args.models)) != len(args.models):
        parser.error("Specify each model only once.")
    client = OllamaClient(args.base_url, 120, 0, chat_timeout_seconds=1800,
                          num_ctx=args.context_tokens, num_predict=2048, think=False)
    for model in args.models:
        client.ensure_model(model)
    records = run_audition(client, args.models, args.output_dir)
    print(json.dumps(records, indent=2))
    return 1 if any("error" in item or item.get("passed") is False for item in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
