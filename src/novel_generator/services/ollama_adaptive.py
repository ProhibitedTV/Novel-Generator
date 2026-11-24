"""Adaptive request sizing for local and remote Ollama servers.

The server owns hardware allocation. We do not assume the Docker client can
see its GPU, free memory, temperature, or other processes.
"""
from dataclasses import dataclass
import math
import re

POLICY_VERSION = "adaptive-context-v1"
TIERS = (2048, 4096, 8192, 12288, 16384, 24576, 32768, 49152,
         65536, 98304, 131072, 196608, 262144)


def positive_int(value):
    return value if type(value) is int and value > 0 else None


def advertised_context(details):
    info = details.get("model_info") or {}
    if not isinstance(info, dict):
        return None
    architecture = info.get("general.architecture")
    if isinstance(architecture, str):
        return positive_int(info.get(architecture + ".context_length"))
    return None


def estimated_input(messages):
    # Deliberately conservative estimate, not a model tokenizer. Include
    # chat template overhead and avoid assuming non-ASCII is one byte.
    size = sum(len(str(m.get("content", "")).encode("utf-8")) for m in messages)
    return math.ceil(size / 3) + 512 + 16 * len(messages)


def memory_failure(status, body):
    return status in {400, 500, 503, 507} and bool(re.search(
        r"out of memory|not enough (?:system |gpu |free )?memory|"
        r"requires more system memory|failed to allocate|"
        r"cannot allocate memory|cuda_error_out_of_memory|"
        r"insufficient (?:gpu |system )?memory", body, re.I))


@dataclass
class RuntimeState:
    advertised_limit: int | None = None
    resident_context: int | None = None
    resident_bytes: int | None = None
    resident_vram_bytes: int | None = None
    preferred_context: int | None = None
    memory_ceiling: int | None = None
    batch_size: int | None = None

    def candidates(self, ceiling):
        return sorted({*[n for n in TIERS if n <= ceiling], ceiling})

    def choose(self, configured, required):
        ceiling = min(configured, self.advertised_limit or configured)
        if required > ceiling:
            raise ValueError(
                f"Estimated prompt plus protected output needs {required} tokens; "
                f"configured/model ceiling is {ceiling}. Increase the configured context "
                "on capable hardware or reduce workload size; source was not truncated."
            )
        # Retain the last useful allocation instead of reloading the model for
        # every short request. Do not inherit a huge externally loaded context.
        target = next(n for n in self.candidates(ceiling) if n >= max(required, min(8192, ceiling)))
        if self.preferred_context is not None and required <= self.preferred_context <= ceiling:
            target = self.preferred_context
        if self.memory_ceiling is not None and required <= self.memory_ceiling:
            target = min(target, self.memory_ceiling)
        return max(required, target)

    def backoff(self, configured, current, required):
        smaller = [n for n in self.candidates(configured) if required <= n < current]
        if not smaller:
            return None
        # One bounded step down; never reduce reserved output or remove text.
        result = max(smaller)
        self.memory_ceiling = result
        self.preferred_context = result
        return result

    def observe_resident(self, row):
        self.resident_context = positive_int(row.get("context_length"))
        self.resident_bytes = positive_int(row.get("size"))
        vram = row.get("size_vram")
        self.resident_vram_bytes = vram if type(vram) is int and vram >= 0 else None

    def metrics(self, configured, effective, required, retries):
        result = {
            "adaptive_policy": POLICY_VERSION, "configured_context_tokens": configured,
            "effective_context_tokens": effective, "estimated_required_context_tokens": required,
            "adaptive_memory_retries": retries,
            **({"effective_num_batch": self.batch_size} if self.batch_size else {}),
        }
        for key, value in (
            ("model_context_limit", self.advertised_limit),
            ("observed_resident_context", self.resident_context),
            ("observed_resident_bytes", self.resident_bytes),
            ("observed_resident_vram_bytes", self.resident_vram_bytes),
        ):
            if value is not None:
                result[key] = value
        return result
