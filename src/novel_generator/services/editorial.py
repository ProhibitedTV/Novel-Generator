from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import re
from typing import Any

from ..models import ChapterDraft
from ..schemas import (
    CanonicalEntity,
    ChapterPlan,
    ContinuityLedger,
    DevelopmentalRewritePlan,
    ManuscriptQaReport,
    StoryBible,
    StructuredOutlineEntry,
    normalize_chapter_mode,
)


STOCK_PHRASES = [
    "the weight of",
    "raw emotions",
    "the colony breathed",
    "engineered peace",
    "wild emotions",
    "the future of the colony",
    "mara stared",
]

PUBLICATION_MOTIFS = {
    "brine",
    "copper",
    "damp",
    "guilt",
    "memory",
    "pressure",
    "resonance",
    "structural",
    "truth",
    "wet stone",
}

GENERATED_ABSTRACT_NOUNS = {
    "agency",
    "collapse",
    "consequence",
    "control",
    "fracture",
    "memory",
    "pressure",
    "resonance",
    "stability",
    "structure",
    "truth",
    "vulnerability",
}

DISCIPLINE_DIALOGUE_TERMS = {
    "calculation",
    "cartography",
    "engineering",
    "infrastructure",
    "materials",
    "procedure",
    "system",
    "theory",
}

BREATHER_MODES = {"breather", "aftermath"}

TECHNICAL_SCENE_MODES = {"systems_crisis", "technical_operation"}

STORY_TURN_REQUIRED_FIELDS = (
    "irreversible_change",
    "protagonist_choice",
    "permanent_consequence",
    "why_this_chapter_cannot_be_cut",
    "state_before",
    "state_after",
)

PRONOUN_GROUPS = {
    "she/her": {"she", "her", "hers"},
    "he/him": {"he", "him", "his"},
    "they/them": {"they", "them", "their", "theirs"},
}

ROLE_DRIFT_MARKERS = {
    "ally",
    "antagonist",
    "archivist",
    "captain",
    "doctor",
    "enemy",
    "engineer",
    "guardian",
    "leader",
    "mentor",
    "pilot",
    "scientist",
    "traitor",
    "villain",
}

ABSTRACT_STORY_TURN_PATTERNS = [
    r"\bstakes? (?:rise|increase|escalate)\b",
    r"\bthe story (?:moves|pushes|continues|goes) forward\b",
    r"\bthings? (?:change|changed)\b",
    r"\beverything (?:changes|changed)\b",
    r"\bthe next problem\b",
    r"\bthe future\b",
    r"\bthe choice (?:is|was) clear\b",
]

ABSTRACT_ENDING_PATTERNS = [
    r"\bnext problem\b",
    r"the next step would decide",
    r"\bthe next step\b",
    r"\bnext challenge\b",
    r"\bnext choice\b",
    r"\bchoice ahead\b",
    r"\bproblem lay ahead\b",
    r"\bproblem waited\b",
    r"the choice would define",
    r"the choice was clear",
    r"the future rested",
    r"\bfuture hung\b",
    r"the truth was waiting",
    r"would shape humanity",
    r"would define the next chapter",
    r"would decide the colony['’]s future",
    r"the colony['’]s future rested",
    r"she had begun her journey",
    r"the colony breathed",
]

META_LANGUAGE_PATTERNS = [
    r"\bthe chapter ends(?: on| with| by)?\b",
    r"\bthis (?:lays|sets) (?:the )?groundwork for\b",
    r"\bthe next problem\b",
    r"\bpushing the story forward\b",
    r"\bthe story was not finished\b",
    r"\bthe decision would shape the next chapter\b",
    r"\bthis (?:scene|chapter) (?:shows|reveals|establishes|foreshadows|sets up)\b",
    r"\bthis (?:sets up|foreshadows) (?:the )?next\b",
    r"\bin the next chapter\b",
]

ENDING_ACTION_VERBS = {
    "accepts",
    "accepted",
    "activate",
    "activates",
    "activated",
    "arrive",
    "arrives",
    "arrived",
    "begin",
    "begins",
    "began",
    "block",
    "blocks",
    "blocked",
    "blocks",
    "break",
    "breaks",
    "broke",
    "burn",
    "burns",
    "burned",
    "cast",
    "casts",
    "close",
    "closes",
    "closed",
    "collapse",
    "collapses",
    "collapsed",
    "cut",
    "cuts",
    "delete",
    "deletes",
    "deleted",
    "drop",
    "drops",
    "dropped",
    "enter",
    "enters",
    "entered",
    "erase",
    "erases",
    "erased",
    "execute",
    "executes",
    "executed",
    "fall",
    "falls",
    "fell",
    "fire",
    "fires",
    "fired",
    "hand",
    "hands",
    "handed",
    "hit",
    "hits",
    "knock",
    "knocks",
    "knocked",
    "land",
    "lands",
    "landed",
    "leave",
    "leaves",
    "left",
    "lock",
    "locks",
    "locked",
    "open",
    "opens",
    "opened",
    "press",
    "presses",
    "pressed",
    "publish",
    "publishes",
    "published",
    "pull",
    "pulls",
    "pulled",
    "refuse",
    "refuses",
    "refused",
    "release",
    "releases",
    "released",
    "rang",
    "rings",
    "reach",
    "reaches",
    "reached",
    "reveal",
    "reveals",
    "revealed",
    "rip",
    "rips",
    "ripped",
    "rose",
    "rises",
    "said",
    "says",
    "seal",
    "seals",
    "sealed",
    "send",
    "sends",
    "sent",
    "shatter",
    "shatters",
    "shattered",
    "shift",
    "shifts",
    "shifted",
    "shut",
    "shuts",
    "sign",
    "signs",
    "signed",
    "slam",
    "slams",
    "slammed",
    "speak",
    "speaks",
    "spoke",
    "step",
    "steps",
    "stepped",
    "stop",
    "stops",
    "stopped",
    "strike",
    "strikes",
    "struck",
    "switch",
    "switches",
    "switched",
    "turn",
    "turns",
    "turned",
    "vanish",
    "vanishes",
    "vanished",
    "vote",
    "votes",
    "voted",
    "walk",
    "walks",
    "walked",
    "wake",
    "wakes",
    "woke",
}

ENDING_CONCRETE_NOUNS = {
    "actor",
    "alarm",
    "body",
    "broadcast",
    "button",
    "command",
    "console",
    "corridor",
    "door",
    "drone",
    "elevator",
    "file",
    "floor",
    "hand",
    "hatch",
    "key",
    "lens",
    "letter",
    "light",
    "map",
    "message",
    "panel",
    "route",
    "screen",
    "seal",
    "signal",
    "switch",
    "terminal",
    "voice",
    "vote",
}

TECH_SOLUTION_KEYWORDS = [
    "hack",
    "decrypt",
    "spoof",
    "reroute",
    "override",
    "backdoor",
    "bruteforce",
    "brute force",
    "script",
]

TECHNICAL_FATIGUE_TERMS = [
    "override",
    "backdoor",
    "lockdown",
    "quarantine",
    "countdown",
    "emergency wipe",
    "safe-mode",
    "safe mode",
    "power failure",
    "life-support warning",
    "life support warning",
]

TECHNICAL_FATIGUE_PATTERNS = {
    "override": [r"\boverride(?:s|d|ing)?\b"],
    "backdoor": [r"\bbackdoor(?:s|ed|ing)?\b"],
    "lockdown": [r"\blockdown(?:s)?\b", r"\blocked down\b"],
    "quarantine": [r"\bquarantine(?:s|d|ing)?\b"],
    "reboot": [r"\breboot(?:s|ed|ing)?\b", r"\brestart(?:s|ed|ing)?\b"],
    "alarm": [r"\balarm(?:s|ed|ing)?\b", r"\bsiren(?:s)?\b"],
    "warning banner": [r"\bwarning banner(?:s)?\b", r"\bwarning(?:s)?\b", r"\bred warning\b"],
    "emergency reserve": [
        r"\bemergency reserve(?:s)?\b",
        r"\breserve (?:power|drain|battery|percent|percentage)\b",
        r"\breserve(?:s)? (?:dropped|falling|failed|gone)\b",
    ],
    "core temperature": [r"\bcore temperature\b", r"\btemperature spike(?:s|d)?\b"],
    "critical failure": [r"\bcritical failure(?:s)?\b", r"\bfailure warning(?:s)?\b"],
    "drone breach": [r"\bdrone breach(?:es)?\b", r"\bdrone(?:s)? (?:breached|entered|forced|reached|cut)\b"],
    "countdown": [r"\bcountdown(?:s)?\b"],
    "emergency wipe": [r"\bemergency wipe\b"],
    "safe mode": [r"\bsafe[- ]mode\b"],
    "power failure": [r"\bpower failure\b"],
    "life support": [r"\blife[- ]support warning\b", r"\blife support\b"],
}

CRISIS_LOOP_BEAT_PATTERNS = {
    "tech-room opening": [
        r"\b(?:control room|control bay|maintenance bay|server room|data vault|terminal room|console bay)\b",
        r"\b(?:console|terminal|server rack|diagnostic wall)\b",
    ],
    "access/log operation": [
        r"\b(?:access(?:ed|es|ing)?|enter(?:ed|s|ing)? (?:the )?access code|enter(?:ed|s|ing)? code|input code|typ(?:ed|es|ing) (?:another )?code|unlock(?:ed|s|ing)?)\b",
        r"\b(?:log|logs|archive file|system file|console|terminal|override|backdoor)\b",
    ],
    "alarm/warning activation": [
        r"\b(?:alarm|alarms|siren|sirens|warning banner|warning|red warning|countdown)\b",
    ],
    "lockdown/drone response": [
        r"\b(?:lockdown|locked down|lockout|locked out|quarantine|sealed|seal|drone|drones|hatch sealed)\b",
    ],
    "broadcast/feed consequence": [
        r"\b(?:broadcast|feed|citywide feed|colony-wide feed|public channel|network feed|signal feed)\b",
    ],
    "abstract consequence language": [
        r"\bthe cost (?:was|is) clear\b",
        r"\bthe weight of (?:the )?(?:decision|choice|moment)\b",
        r"\bthe next problem\b",
        r"\bthe future (?:hung|hangs|rested|rests|waited|waits)\b",
        r"\bfuture (?:hung|hangs) in the balance\b",
    ],
}

CRISIS_LOOP_BEAT_ORDER = (
    "tech-room opening",
    "access/log operation",
    "alarm/warning activation",
    "lockdown/drone response",
    "broadcast/feed consequence",
    "abstract consequence language",
)

COST_KEYWORDS = [
    "burned",
    "locked out",
    "lost access",
    "exposed",
    "exposure",
    "corrupted",
    "corruption",
    "injured",
    "bleeding",
    "delay",
    "late",
    "alarms",
    "alarm",
    "collateral",
    "damage",
    "fallout",
    "betrayed",
    "rupture",
    "argument",
    "sacrifice",
    "cost",
    "price",
]

HUMAN_CONSEQUENCE_TERMS = {
    "argument",
    "betrayed",
    "bleeding",
    "children",
    "civilian",
    "civilians",
    "crowd",
    "evacuated",
    "families",
    "family",
    "fear",
    "fracture",
    "injured",
    "lost",
    "medic",
    "panic",
    "refused",
    "shelter",
    "trust",
    "workers",
}

SIDE_CHARACTER_ACTION_TERMS = {
    "accepted",
    "announced",
    "arrested",
    "betrayed",
    "blocked",
    "broadcast",
    "burned",
    "carried",
    "chose",
    "closed",
    "confessed",
    "cut",
    "deleted",
    "destroyed",
    "dropped",
    "exposed",
    "handed",
    "hid",
    "leaked",
    "leaks",
    "left",
    "locked",
    "opened",
    "ordered",
    "patched",
    "patches",
    "published",
    "refused",
    "refuses",
    "resists",
    "released",
    "rescued",
    "revealed",
    "sealed",
    "sent",
    "signed",
    "stole",
    "switched",
    "took",
    "voted",
}

SIDE_CHARACTER_EXPOSITION_TERMS = {
    "asked",
    "explained",
    "said",
    "told",
    "warned",
    "watched",
    "wondered",
}

FILTER_VERBS = [
    "felt",
    "feel",
    "feels",
    "saw",
    "see",
    "sees",
    "heard",
    "hear",
    "hears",
    "watched",
    "watch",
    "noticed",
    "notice",
    "realized",
    "realize",
    "wondered",
    "wonder",
    "knew",
    "know",
    "thought",
    "think",
    "seemed",
    "seem",
]

ABSTRACT_EMOTION_TERMS = [
    "anger",
    "angry",
    "despair",
    "dread",
    "emotion",
    "emotional",
    "fear",
    "feared",
    "grief",
    "guilt",
    "hope",
    "panic",
    "regret",
    "sadness",
    "shame",
    "sorrow",
    "terror",
]

SENSORY_ANCHOR_TERMS = [
    "air",
    "bitter",
    "cold",
    "dust",
    "echo",
    "heat",
    "light",
    "metal",
    "odor",
    "rain",
    "rough",
    "salt",
    "scent",
    "shadow",
    "sound",
    "taste",
    "warm",
    "wet",
]

CONCRETE_ENDING_CUES = [
    "said",
    "door",
    "alarm",
    "drone",
    "footstep",
    "console",
    "screen",
    "lens",
    "voice",
    "sirens",
    "comm",
    "signal",
    "arrived",
    "opened",
    "blinked",
    "turned",
]

COMMON_PROPER_NOUN_IGNORES = {
    "a",
    "an",
    "and",
    "after",
    "before",
    "but",
    "chapter",
    "he",
    "her",
    "his",
    "i",
    "if",
    "in",
    "it",
    "later",
    "no",
    "she",
    "that",
    "the",
    "their",
    "then",
    "there",
    "they",
    "this",
    "we",
    "when",
    "yes",
}

REPAIR_SCOPE_PRIORITY = {
    "none": 0,
    "voice_and_texture": 1,
    "targeted_scene_and_ending": 2,
    "full_chapter": 3,
}

MEANINGFUL_TERM_IGNORES = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "beside",
    "but",
    "for",
    "from",
    "had",
    "has",
    "have",
    "her",
    "hers",
    "him",
    "his",
    "in",
    "into",
    "its",
    "more",
    "not",
    "of",
    "off",
    "on",
    "one",
    "onto",
    "or",
    "our",
    "out",
    "over",
    "she",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "they",
    "this",
    "through",
    "to",
    "under",
    "until",
    "was",
    "were",
    "while",
    "with",
    "would",
    "you",
    "your",
}


@dataclass
class ChapterLintResult:
    blocking_issues: list[str] = field(default_factory=list)
    soft_warnings: list[str] = field(default_factory=list)
    repair_scope: str = "none"
    needs_repair: bool = False
    canonical_collision: bool = False

    def combined_findings(self) -> list[str]:
        return [*self.blocking_issues, *self.soft_warnings]


def _normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _opening_signature(content: str, words: int = 24) -> str:
    return " ".join(_normalized_words(content)[:words])


def _normalize_entity_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _meaningful_terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9][a-z0-9\-']+", text.lower())
        if len(term) > 2 and term not in MEANINGFUL_TERM_IGNORES
    }


def _technical_fatigue_hits(text: str) -> Counter[str]:
    lowered = text.lower()
    hits: Counter[str] = Counter()
    for label, patterns in TECHNICAL_FATIGUE_PATTERNS.items():
        count = sum(len(re.findall(pattern, lowered)) for pattern in patterns)
        if count:
            hits[label] = count
    return hits


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z'-]*", text))


def _motif_counts(text: str) -> Counter[str]:
    lowered = text.lower()
    counts: Counter[str] = Counter()
    for motif in PUBLICATION_MOTIFS:
        if " " in motif:
            counts[motif] = lowered.count(motif)
        else:
            counts[motif] = len(re.findall(rf"\b{re.escape(motif)}\b", lowered))
    return Counter({motif: count for motif, count in counts.items() if count})


def _publication_motif_findings(chapters: list[ChapterDraft]) -> list[str]:
    findings: list[str] = []
    manuscript_text = "\n\n".join(chapter.content or "" for chapter in chapters)
    total_words = max(1, _word_count(manuscript_text))
    counts = _motif_counts(manuscript_text)
    for motif, count in counts.most_common():
        per_10k = count / total_words * 10000
        if count >= 8 and per_10k >= 7:
            findings.append(
                f"Publication motif overuse: '{motif}' appears {count} times ({per_10k:.1f} per 10k words)."
            )

    for chapter in chapters:
        chapter_counts = _motif_counts(chapter.content or "")
        dense = [motif for motif, count in chapter_counts.items() if count >= 3]
        if dense:
            findings.append(
                f"Chapter {chapter.chapter_number} repeats atmospheric motif(s) too often: "
                + ", ".join(sorted(dense)[:5])
                + "."
            )
    return findings


def _chapter_opening_similarity_findings(chapters: list[ChapterDraft]) -> list[str]:
    findings: list[str] = []
    opening_terms = []
    for chapter in chapters:
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n+", chapter.content or "") if paragraph.strip()]
        if not paragraphs:
            continue
        terms = set(sorted(_meaningful_terms(paragraphs[0]))[:40])
        if terms:
            opening_terms.append((chapter.chapter_number, terms))

    for index, (left_number, left_terms) in enumerate(opening_terms):
        for right_number, right_terms in opening_terms[index + 1 :]:
            overlap = len(left_terms & right_terms) / max(1, min(len(left_terms), len(right_terms)))
            if overlap >= 0.62:
                findings.append(
                    f"Chapters {left_number} and {right_number} have highly similar opening texture; vary the entry image and dramatic mode."
                )
                if len(findings) >= 6:
                    return findings
    return findings


def _generated_abstract_cluster_findings(chapters: list[ChapterDraft]) -> list[str]:
    findings: list[str] = []
    for chapter in chapters:
        text = (chapter.content or "").lower()
        words = max(1, _word_count(text))
        hits = sum(len(re.findall(rf"\b{re.escape(term)}\b", text)) for term in GENERATED_ABSTRACT_NOUNS)
        if hits >= 18 and hits / words * 1000 >= 8:
            findings.append(
                f"Chapter {chapter.chapter_number} has a generated-feeling abstract noun cluster ({hits} theme/system terms)."
            )
    return findings


def _dialogue_discipline_findings(chapters: list[ChapterDraft]) -> list[str]:
    findings: list[str] = []
    for chapter in chapters:
        dialogue = " ".join(re.findall(r'"([^"]+)"', chapter.content or ""))
        if not dialogue:
            continue
        lowered = dialogue.lower()
        hits = [term for term in DISCIPLINE_DIALOGUE_TERMS if re.search(rf"\b{re.escape(term)}\b", lowered)]
        if len(hits) >= 4:
            findings.append(
                f"Chapter {chapter.chapter_number} dialogue leans on discipline labels ({', '.join(sorted(hits)[:5])}) instead of private human friction."
            )
    return findings


def _pattern_phrases(text: str, patterns: list[str], limit: int = 3) -> list[str]:
    phrases: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            phrase = " ".join(match.group(0).split())
            if phrase:
                phrases.append(phrase)
            if len(phrases) >= limit:
                return list(dict.fromkeys(phrases))
    return list(dict.fromkeys(phrases))


def _crisis_loop_profile(chapter: ChapterDraft) -> dict[str, Any]:
    content = chapter.content or ""
    opening = " ".join(_sentences(content)[:3])
    beat_hits: dict[str, list[str]] = {}
    for label, patterns in CRISIS_LOOP_BEAT_PATTERNS.items():
        search_text = opening if label == "tech-room opening" else content
        phrases = _pattern_phrases(search_text, patterns)
        if phrases:
            beat_hits[label] = phrases
    sequence = [label for label in CRISIS_LOOP_BEAT_ORDER if label in beat_hits]
    ending_hits = _pattern_phrases(_ending_text(content) or content[-600:], CRISIS_LOOP_BEAT_PATTERNS["abstract consequence language"])
    if ending_hits:
        beat_hits["abstract consequence language"] = ending_hits
        if "abstract consequence language" not in sequence:
            sequence.append("abstract consequence language")
    return {
        "chapter_number": chapter.chapter_number,
        "mode": _chapter_mode_from_summary(chapter.outline_summary or ""),
        "sequence": sequence,
        "beat_hits": beat_hits,
        "fatigue_hits": _technical_fatigue_hits(content),
        "final_beat_terms": _meaningful_terms(_final_beat_text(content)),
    }


def _profile_phrases(profile: dict[str, Any], labels: list[str], limit: int = 4) -> list[str]:
    phrases: list[str] = []
    beat_hits = profile.get("beat_hits", {})
    for label in labels:
        phrase = next(iter(beat_hits.get(label, []) or []), "")
        if phrase:
            phrases.append(f"{label}: '{phrase}'")
            if len(phrases) >= limit:
                return phrases
    for label in labels:
        for phrase in (beat_hits.get(label, []) or [])[1:]:
            phrases.append(f"{label}: '{phrase}'")
            if len(phrases) >= limit:
                return phrases
    for label in _technical_fatigue_labels(profile.get("fatigue_hits", Counter()), limit=limit):
        phrases.append(f"mechanic: '{label}'")
        if len(phrases) >= limit:
            return phrases
    return phrases


def _crisis_loop_severity(shared_count: int, same_mode: bool, has_abstract_consequence: bool) -> str:
    score = shared_count + (1 if same_mode else 0) + (1 if has_abstract_consequence else 0)
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _crisis_loop_fix(severity: str, same_mode: bool, has_abstract_consequence: bool) -> str:
    if severity == "high":
        return "merge or cut one repeated crisis beat, then preserve only the stronger permanent consequence."
    if same_mode:
        return "vary scene mode and replace one technical escalation with interpersonal or civilian consequence."
    if has_abstract_consequence:
        return "replace abstract consequence language with a visible irreversible cost."
    return "replace one repeated technical step with interpersonal consequence."


def _crisis_loop_finding(
    chapter_numbers: list[int],
    pattern_labels: list[str],
    representative_phrases: list[str],
    *,
    severity: str,
    suggested_fix: str,
) -> str:
    chapters = ", ".join(str(number) for number in chapter_numbers)
    pattern = " -> ".join(pattern_labels) if pattern_labels else "repeated crisis mechanics"
    phrases = "; ".join(representative_phrases) if representative_phrases else "No representative phrases captured."
    return (
        f"Chapters {chapters} repeat crisis-loop pattern: {pattern}. "
        f"Representative phrases: {phrases}. "
        f"Severity: {severity}. "
        f"Suggested fix: {suggested_fix}"
    )


def _crisis_loop_findings(chapters: list[ChapterDraft]) -> list[str]:
    sorted_chapters = sorted(chapters, key=lambda item: item.chapter_number)
    profiles = [_crisis_loop_profile(chapter) for chapter in sorted_chapters]
    findings: list[str] = []

    for previous, current in zip(profiles, profiles[1:]):
        previous_sequence = set(previous["sequence"])
        current_sequence = set(current["sequence"])
        shared_sequence = [label for label in CRISIS_LOOP_BEAT_ORDER if label in previous_sequence and label in current_sequence]
        shared_mechanics = set(previous["fatigue_hits"]) & set(current["fatigue_hits"])
        final_overlap = previous["final_beat_terms"] & current["final_beat_terms"]
        same_mode = bool(previous["mode"] and previous["mode"] == current["mode"])
        has_abstract = "abstract consequence language" in shared_sequence
        if (
            len(shared_sequence) >= 3
            or (len(shared_mechanics) >= 3 and same_mode)
            or (len(shared_mechanics) >= 2 and len(final_overlap) >= 2)
        ):
            repeated_labels = shared_sequence or sorted(shared_mechanics)[:5]
            severity = _crisis_loop_severity(len(repeated_labels), same_mode, has_abstract)
            phrases = [
                f"Ch{previous['chapter_number']} {item}"
                for item in _profile_phrases(previous, repeated_labels, limit=3)
            ]
            phrases.extend(
                f"Ch{current['chapter_number']} {item}"
                for item in _profile_phrases(current, repeated_labels, limit=3)
            )
            findings.append(
                _crisis_loop_finding(
                    [previous["chapter_number"], current["chapter_number"]],
                    repeated_labels,
                    phrases,
                    severity=severity,
                    suggested_fix=_crisis_loop_fix(severity, same_mode, has_abstract),
                )
            )

    opening_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for profile in profiles:
        opening_signature = tuple(
            label
            for label in ("tech-room opening", "access/log operation", "alarm/warning activation")
            if label in profile["sequence"]
        )
        if len(opening_signature) >= 2:
            opening_groups.setdefault(opening_signature, []).append(profile)
    for signature, grouped_profiles in opening_groups.items():
        if len(grouped_profiles) < 2:
            continue
        chapter_numbers = [profile["chapter_number"] for profile in grouped_profiles]
        phrases: list[str] = []
        for profile in grouped_profiles[:3]:
            phrases.extend(
                f"Ch{profile['chapter_number']} {item}"
                for item in _profile_phrases(profile, list(signature), limit=1)
            )
        findings.append(
            _crisis_loop_finding(
                chapter_numbers,
                list(signature),
                phrases,
                severity="medium",
                suggested_fix="vary chapter openings before the next technical escalation begins.",
            )
        )

    sequence_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for profile in profiles:
        core_sequence = tuple(
            label
            for label in (
                "access/log operation",
                "alarm/warning activation",
                "lockdown/drone response",
                "broadcast/feed consequence",
            )
            if label in profile["sequence"]
        )
        if len(core_sequence) >= 3:
            sequence_groups.setdefault(core_sequence, []).append(profile)
    for sequence, grouped_profiles in sequence_groups.items():
        if len(grouped_profiles) < 2:
            continue
        chapter_numbers = [profile["chapter_number"] for profile in grouped_profiles]
        phrases: list[str] = []
        for profile in grouped_profiles[:3]:
            phrases.extend(
                f"Ch{profile['chapter_number']} {item}"
                for item in _profile_phrases(profile, list(sequence), limit=2)
            )
        findings.append(
            _crisis_loop_finding(
                chapter_numbers,
                list(sequence),
                phrases[:6],
                severity="high",
                suggested_fix="merge or reorder the duplicated access-warning-lockout chain, then replace one beat with interpersonal consequence.",
            )
        )

    abstract_profiles = [
        profile
        for profile in profiles
        if "abstract consequence language" in profile["sequence"]
    ]
    if len(abstract_profiles) >= 2:
        chapter_numbers = [profile["chapter_number"] for profile in abstract_profiles]
        phrases = [
            f"Ch{profile['chapter_number']} {item}"
            for profile in abstract_profiles[:4]
            for item in _profile_phrases(profile, ["abstract consequence language"], limit=1)
        ]
        findings.append(
            _crisis_loop_finding(
                chapter_numbers,
                ["abstract consequence language"],
                phrases,
                severity="medium",
                suggested_fix="replace repeated summary-stakes endings with visible objects, decisions, or losses.",
            )
        )

    return list(dict.fromkeys(findings))


def _technical_fatigue_score(hits: Counter[str], adjacent_overlap_count: int = 0) -> int:
    if not hits:
        return 0
    unique_hits = len(hits)
    total_hits = sum(hits.values())
    repeated_hits = max(0, total_hits - unique_hits)
    return min(10, unique_hits * 2 + repeated_hits + adjacent_overlap_count * 2)


def _technical_fatigue_labels(hits: Counter[str], limit: int = 5) -> list[str]:
    return sorted(hits, key=lambda label: (-hits[label], label))[:limit]


def _chapter_mode_from_summary(summary: str) -> str:
    match = re.search(
        r"\bMode:\s*([^.;\n]+?)(?=\s+(?:Obstacle|Conflict turn|Reveal|Cost if success|Ending state|Genre state):|[.;\n]|$)",
        summary or "",
        flags=re.IGNORECASE,
    )
    return normalize_chapter_mode(match.group(1)) if match else ""


def _adjacent_prior_chapter(chapter_number: int, prior_chapters: list[ChapterDraft]) -> ChapterDraft | None:
    candidates = [
        previous
        for previous in prior_chapters
        if previous.chapter_number < chapter_number and (previous.content or "")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda previous: previous.chapter_number)


def _has_human_visible_consequence(text: str) -> bool:
    lowered = text.lower()
    terms = set(_normalized_words(text))
    cost_terms = [keyword for keyword in COST_KEYWORDS if keyword not in {"alarm", "alarms"}]
    return bool(terms & HUMAN_CONSEQUENCE_TERMS) or any(keyword in lowered for keyword in cost_terms)


def _payload_name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("name", "")).strip()
    return str(getattr(item, "name", "")).strip()


def _major_side_character_names(story_bible: StoryBible | dict[str, Any]) -> list[str]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    cast = bible.get("cast") or []
    protagonist = _payload_name(cast[0]) if cast else ""
    names: list[str] = []
    for member in cast[1:]:
        name = _payload_name(member)
        if name and name != protagonist:
            names.append(name)
    for agenda in bible.get("character_agendas") or []:
        name = _payload_name(agenda)
        if name and name != protagonist:
            names.append(name)
    return list(dict.fromkeys(names))


def _story_character_name_terms(story_bible: StoryBible | dict[str, Any]) -> set[str]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    names = [_payload_name(member) for member in bible.get("cast") or []]
    names.extend(_payload_name(agenda) for agenda in bible.get("character_agendas") or [])
    return {
        term
        for name in names
        for term in _meaningful_terms(name)
    }


def _mentions_name(text: str, name: str) -> bool:
    return bool(name) and bool(re.search(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE))


def _sentences_with_name(text: str, name: str) -> list[str]:
    return [sentence for sentence in _sentences(text) if _mentions_name(sentence, name)]


def _payload_role(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("role", "")).strip()
    return str(getattr(item, "role", "")).strip()


def _chapter_continuity_update(chapter: ChapterDraft) -> dict[str, Any]:
    update = chapter.continuity_update or {}
    if isinstance(update, dict):
        return update
    return getattr(update, "model_dump", lambda: {})()


def _canon_character_roles(story_bible: StoryBible | dict[str, Any]) -> dict[str, str]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    roles: dict[str, str] = {}
    for member in bible.get("cast") or []:
        name = _payload_name(member)
        role = _payload_role(member)
        if name:
            roles[name] = role
    for entity in bible.get("canon_registry") or []:
        if str(entity.get("kind", "")).strip().lower() != "person":
            continue
        name = _payload_name(entity)
        role = _payload_role(entity)
        if name and role:
            roles.setdefault(name, role)
    return roles


def _canon_system_roles(story_bible: StoryBible | dict[str, Any]) -> dict[str, str]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    systems: dict[str, str] = {}
    for entity in bible.get("canon_registry") or []:
        if str(entity.get("kind", "")).strip().lower() not in {"system", "project"}:
            continue
        name = _payload_name(entity)
        role = _payload_role(entity)
        if name:
            systems[name] = role or "Core system or project in canon registry."
    return systems


def _name_similarity_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def _names_are_confusable(left: str, right: str) -> bool:
    left_key = _name_similarity_key(left)
    right_key = _name_similarity_key(right)
    if not left_key or not right_key or left_key == right_key:
        return False
    shorter = min(len(left_key), len(right_key))
    if shorter < 4:
        return _levenshtein_distance(left_key, right_key) <= 1
    distance = _levenshtein_distance(left_key, right_key)
    if distance <= (2 if shorter <= 6 else 3):
        return True
    return left_key[:4] == right_key[:4] and abs(len(left_key) - len(right_key)) <= 3


def _pronoun_group_from_text(text: str) -> str:
    lowered = text.lower()
    for group in PRONOUN_GROUPS:
        if group in lowered:
            return group
    words = _normalized_words(text)
    counts = Counter(
        group
        for group, pronouns in PRONOUN_GROUPS.items()
        for word in words
        if word in pronouns
    )
    if not counts:
        return ""
    dominant, count = counts.most_common(1)[0]
    if len(counts) > 1 and counts.most_common(2)[1][1] == count:
        return ""
    return dominant


def _pronoun_group_near_name(chapter: ChapterDraft, name: str) -> str:
    sentences = _sentences(chapter.content or "")
    snippets: list[str] = []
    for index, sentence in enumerate(sentences):
        if not _mentions_name(sentence, name):
            continue
        snippets.append(sentence)
        if index + 1 < len(sentences):
            snippets.append(sentences[index + 1])
    return _pronoun_group_from_text(" ".join(snippets))


def _role_terms(text: str) -> set[str]:
    return {word for word in _normalized_words(text) if word in ROLE_DRIFT_MARKERS}


def _role_has_drift(canon_role: str, observed_role: str) -> bool:
    observed_terms = _role_terms(observed_role)
    if not observed_terms:
        return False
    canon_terms = _role_terms(canon_role)
    return not bool(canon_terms & observed_terms)


def _transition_payload(transition: Any) -> dict[str, Any]:
    if isinstance(transition, dict):
        return transition
    return getattr(transition, "model_dump", lambda: {})()


def _transition_system_name(transition: Any) -> str:
    return str(_transition_payload(transition).get("system_name", "")).strip()


def _continuity_bible_notes(
    chapters: list[ChapterDraft],
    story_bible: StoryBible | dict[str, Any],
) -> tuple[list[str], list[dict[str, str]]]:
    character_roles = _canon_character_roles(story_bible)
    system_roles = _canon_system_roles(story_bible)
    findings: list[str] = []
    rows: list[dict[str, str]] = []

    names = list(dict.fromkeys([*character_roles.keys(), *system_roles.keys()]))
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if _names_are_confusable(left, right):
                findings.append(
                    f"Continuity bible name collision: '{left}' and '{right}' are visually or phonetically similar; suggested rename one with a distinct first syllable before export."
                )

    pronouns_by_character: dict[str, dict[str, list[int]]] = {
        name: {}
        for name in character_roles
    }
    latest_character_state: dict[str, str] = {}
    system_state_by_name: dict[str, str] = dict(system_roles)
    transition_counts: Counter[str] = Counter()
    sorted_chapters = sorted(chapters, key=lambda item: item.chapter_number)

    for chapter in sorted_chapters:
        update = _chapter_continuity_update(chapter)
        character_states = update.get("character_states", {}) if isinstance(update.get("character_states", {}), dict) else {}
        for name, canon_role in character_roles.items():
            prose_group = _pronoun_group_near_name(chapter, name)
            state_text = str(character_states.get(name, "")).strip()
            state_group = _pronoun_group_from_text(state_text)
            group = state_group or prose_group
            if group:
                pronouns_by_character.setdefault(name, {}).setdefault(group, []).append(chapter.chapter_number)
            if state_text:
                latest_character_state[name] = state_text
                if _role_has_drift(canon_role, state_text):
                    findings.append(
                        f"Continuity bible role drift: {name} is canonically '{canon_role}' but chapter {chapter.chapter_number} records '{state_text}' without a canon-fix note."
                    )

        for entity in update.get("new_entities_introduced", []) or []:
            if str(entity.get("kind", "")).strip().lower() != "person":
                continue
            name = _payload_name(entity)
            if name in character_roles and _role_has_drift(character_roles[name], _payload_role(entity)):
                findings.append(
                    f"Continuity bible role drift: {name} is canonically '{character_roles[name]}' but chapter {chapter.chapter_number} reintroduces them as '{_payload_role(entity)}'."
                )

        transitions = [_transition_payload(item) for item in update.get("system_state_transitions", []) or []]
        transition_names = {_transition_system_name(item) for item in transitions if _transition_system_name(item)}
        for transition in transitions:
            system_name = str(transition.get("system_name", "")).strip()
            previous_state = str(transition.get("previous_state", "")).strip()
            new_state = str(transition.get("new_state", "")).strip()
            cause = str(transition.get("cause", "")).strip()
            if not (system_name and previous_state and new_state and cause):
                findings.append(
                    f"Continuity bible system-state warning: chapter {chapter.chapter_number} has an incomplete system_state_transition for '{system_name or 'unnamed system'}'."
                )
                continue
            prior_state = system_state_by_name.get(system_name, "")
            if prior_state and previous_state.lower() != prior_state.lower():
                findings.append(
                    f"Continuity bible system-state mismatch: chapter {chapter.chapter_number} moves '{system_name}' from '{previous_state}', but the prior tracked state is '{prior_state}'."
                )
            system_state_by_name[system_name] = new_state
            transition_counts[system_name] += 1

        entity_state_changes = update.get("entity_state_changes", {})
        if isinstance(entity_state_changes, dict):
            for system_name in system_roles:
                if system_name in entity_state_changes and system_name not in transition_names:
                    findings.append(
                        f"Continuity bible system-state warning: chapter {chapter.chapter_number} changes core system '{system_name}' without a structured system_state_transition."
                    )

    for name, groups in pronouns_by_character.items():
        if len(groups) <= 1:
            continue
        rendered = ", ".join(
            f"{group} in chapter(s) {', '.join(str(number) for number in numbers)}"
            for group, numbers in groups.items()
        )
        findings.append(
            f"Continuity bible pronoun drift: {name} uses inconsistent pronouns across the manuscript: {rendered}."
        )

    for name, role in character_roles.items():
        groups = pronouns_by_character.get(name, {})
        pronoun_note = ", ".join(groups) if groups else "No pronoun evidence captured."
        rows.append(
            {
                "item_type": "character",
                "name": name,
                "canon_status": role or "No canon role recorded.",
                "observed_status": latest_character_state.get(name, "No stored character state recorded."),
                "notes": pronoun_note,
            }
        )

    for name, canon_state in system_roles.items():
        transition_count = transition_counts.get(name, 0)
        rows.append(
            {
                "item_type": "system",
                "name": name,
                "canon_status": canon_state,
                "observed_status": system_state_by_name.get(name, "No structured system state recorded."),
                "notes": f"{transition_count} structured transition(s) recorded.",
            }
        )

    return findings, rows


def _dedupe_continuity_bible_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        key = (
            row.get("item_type", ""),
            row.get("name", ""),
            row.get("canon_status", ""),
            row.get("observed_status", ""),
            row.get("notes", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _qa_row_value(row: Any, field_name: str) -> str:
    if isinstance(row, dict):
        return str(row.get(field_name, "")).strip()
    return str(getattr(row, field_name, "")).strip()


def _markdown_cell(value: str) -> str:
    return value.replace("|", "\\|") or " "


def _render_continuity_bible_table(rows: list[Any]) -> list[str]:
    if not rows:
        return ["- No continuity bible table recorded."]
    rendered = [
        "| Type | Name | Canon Status | Observed Status | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        rendered.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(_qa_row_value(row, "item_type")),
                    _markdown_cell(_qa_row_value(row, "name")),
                    _markdown_cell(_qa_row_value(row, "canon_status")),
                    _markdown_cell(_qa_row_value(row, "observed_status")),
                    _markdown_cell(_qa_row_value(row, "notes")),
                ]
            )
            + " |"
        )
    return rendered


def _has_side_character_action(sentences: list[str]) -> bool:
    for sentence in sentences:
        terms = set(_normalized_words(sentence))
        if terms & SIDE_CHARACTER_ACTION_TERMS:
            return True
    return False


def _looks_exposition_only(sentences: list[str]) -> bool:
    if not sentences:
        return False
    combined_terms = set(_normalized_words(" ".join(sentences)))
    return bool(combined_terms & SIDE_CHARACTER_EXPOSITION_TERMS) and not _has_side_character_action(sentences)


def _sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def _paragraphs(text: str) -> list[str]:
    return [paragraph.strip() for paragraph in re.split(r"\n\s*\n+", text.strip()) if paragraph.strip()]


def _ending_text(content: str, paragraphs: int = 3) -> str:
    chunks = _paragraphs(content)
    if not chunks:
        return ""
    return "\n\n".join(chunks[-paragraphs:])


def _final_beat_text(content: str) -> str:
    chunks = _paragraphs(content)
    final_paragraph = chunks[-1] if chunks else content.strip()
    sentences = _sentences(final_paragraph)
    return sentences[-1] if sentences else final_paragraph


def _sentence_start_key(sentence: str, words: int) -> str:
    normalized = _normalized_words(sentence)
    return " ".join(normalized[:words])


def _set_repair_scope(result: ChapterLintResult, scope: str) -> None:
    if REPAIR_SCOPE_PRIORITY.get(scope, 0) > REPAIR_SCOPE_PRIORITY.get(result.repair_scope, 0):
        result.repair_scope = scope


def _entity_payload(entity: CanonicalEntity | dict[str, Any]) -> CanonicalEntity:
    return entity if isinstance(entity, CanonicalEntity) else CanonicalEntity.model_validate(entity)


def _canon_terms(entity: CanonicalEntity | dict[str, Any]) -> list[str]:
    payload = _entity_payload(entity)
    return [payload.name, *payload.aliases]


def _approved_proper_nouns(
    story_bible: StoryBible | dict[str, Any],
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    plan: ChapterPlan | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
) -> set[str]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    chapter_plan = plan if isinstance(plan, dict) else plan.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()

    approved: set[str] = set()
    for entity in [*(bible.get("canon_registry") or []), *(ledger.get("active_entities") or [])]:
        for term in _canon_terms(entity):
            if term:
                approved.add(_normalize_entity_key(term))

    approved_sources = [
        entry,
        chapter_plan,
        bible.get("cast") or [],
        bible.get("character_agendas") or [],
    ]
    for source in approved_sources:
        text = str(source)
        for match in re.findall(r"\b[A-Z][A-Za-z0-9]*(?:[- ][A-Z0-9][A-Za-z0-9-]*)*\b", text):
            normalized = _normalize_entity_key(match)
            if normalized:
                approved.add(normalized)
    return approved


def _proper_noun_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    multi_token = re.findall(r"\b[A-Z][A-Za-z0-9]*(?:[- ][A-Z0-9][A-Za-z0-9-]*)+\b", text)
    for match in multi_token:
        normalized = _normalize_entity_key(match)
        if normalized:
            candidates.add(normalized)

    singles = Counter(re.findall(r"\b[A-Z][A-Za-z0-9-]{2,}\b", text))
    for match, count in singles.items():
        lowered = match.lower()
        if count < 2 or lowered in COMMON_PROPER_NOUN_IGNORES:
            continue
        normalized = _normalize_entity_key(match)
        if normalized:
            candidates.add(normalized)
    return candidates


def _style_avoid_terms(story_bible: StoryBible | dict[str, Any]) -> list[str]:
    bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()
    style_profile = bible.get("style_profile") or {}
    return [
        str(item).strip()
        for item in style_profile.get("avoid", []) or []
        if str(item).strip()
    ]


def _has_concrete_ending_action(text: str) -> bool:
    terms = set(_normalized_words(text))
    has_action = bool(terms & ENDING_ACTION_VERBS)
    has_concrete_noun = bool(terms & ENDING_CONCRETE_NOUNS)
    return has_action and has_concrete_noun


def _looks_like_internal_or_atmospheric_ending(text: str) -> bool:
    terms = set(_normalized_words(text))
    has_abstract_emotion = bool(terms & set(ABSTRACT_EMOTION_TERMS))
    has_theme_language = bool({"future", "truth", "choice", "destiny", "hope", "meaning"} & terms)
    return has_abstract_emotion or has_theme_language


def _meta_language_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in META_LANGUAGE_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            hits.append(match.group(0).strip())
    return list(dict.fromkeys(hits))


def _coerce_story_turn(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    return getattr(value, "model_dump", lambda: {})()


def _story_turn_from_plan(chapter_plan: dict[str, Any]) -> dict[str, Any]:
    return _coerce_story_turn(chapter_plan.get("story_turn"))


def _story_turn_from_chapter(chapter: ChapterDraft) -> dict[str, Any]:
    continuity_update = chapter.continuity_update or {}
    if not isinstance(continuity_update, dict):
        continuity_update = getattr(continuity_update, "model_dump", lambda: {})()
    continuity_turn = _coerce_story_turn(continuity_update.get("story_turn"))
    if continuity_turn:
        return continuity_turn
    plan_text = (chapter.plan or "").strip()
    if not plan_text:
        return {}
    try:
        plan_payload = json.loads(plan_text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(plan_payload, dict):
        return {}
    return _coerce_story_turn(plan_payload.get("story_turn"))


def _story_turn_text(story_turn: dict[str, Any]) -> str:
    parts: list[str] = []
    for field_name in STORY_TURN_REQUIRED_FIELDS:
        parts.append(str(story_turn.get(field_name, "")))
    parts.extend(str(item) for item in story_turn.get("choice_alternatives", []) or [])
    return " ".join(part for part in parts if part).strip()


def _missing_story_turn_fields(story_turn: dict[str, Any]) -> list[str]:
    missing = [
        field_name
        for field_name in STORY_TURN_REQUIRED_FIELDS
        if not str(story_turn.get(field_name, "")).strip()
    ]
    if not _clean_story_turn_alternatives(story_turn):
        missing.append("choice_alternatives")
    return missing


def _clean_story_turn_alternatives(story_turn: dict[str, Any]) -> list[str]:
    return [
        str(item).strip()
        for item in story_turn.get("choice_alternatives", []) or []
        if str(item).strip()
    ]


def _abstract_story_turn_hits(story_turn: dict[str, Any]) -> list[str]:
    text = _story_turn_text(story_turn)
    hits: list[str] = []
    for pattern in ABSTRACT_STORY_TURN_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            hits.append(match.group(0).strip())
    return list(dict.fromkeys(hits))


def _story_turn_terms(story_turn: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(story_turn.get(field_name, ""))
        for field_name in (
            "irreversible_change",
            "protagonist_choice",
            "permanent_consequence",
            "state_after",
        )
    )
    return _meaningful_terms(text)


def _story_turn_similarity(first: dict[str, Any], second: dict[str, Any]) -> float:
    first_terms = _story_turn_terms(first)
    second_terms = _story_turn_terms(second)
    if not first_terms or not second_terms:
        return 0
    return len(first_terms & second_terms) / max(1, min(len(first_terms), len(second_terms)))


def detect_canonical_entity_collisions(
    existing_entities: list[CanonicalEntity | dict[str, Any]],
    new_entities: list[CanonicalEntity | dict[str, Any]],
) -> list[str]:
    collisions: list[str] = []
    lookup: dict[str, tuple[str, str]] = {}

    def register(entity: CanonicalEntity | dict[str, Any], allow_existing: bool) -> None:
        payload = _entity_payload(entity)
        canonical_key = _normalize_entity_key(payload.name)
        for term in [payload.name, *payload.aliases]:
            key = _normalize_entity_key(term)
            if not key:
                continue
            owner = lookup.get(key)
            if owner and owner[0] != canonical_key:
                collisions.append(
                    f"Canonical entity collision: '{term}' conflicts with existing entity '{owner[1]}'."
                )
            if allow_existing or key not in lookup:
                lookup[key] = (canonical_key, payload.name)

    for entity in existing_entities:
        register(entity, allow_existing=True)
    for entity in new_entities:
        register(entity, allow_existing=False)

    return list(dict.fromkeys(collisions))


def merge_canonical_entities(
    existing_entities: list[CanonicalEntity | dict[str, Any]],
    new_entities: list[CanonicalEntity | dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, CanonicalEntity] = {}
    for entity in [*existing_entities, *new_entities]:
        payload = _entity_payload(entity)
        key = _normalize_entity_key(payload.name)
        current = merged.get(key)
        if current is None:
            merged[key] = payload
            continue
        aliases = sorted({*current.aliases, *payload.aliases})
        merged[key] = current.model_copy(
            update={
                "kind": current.kind or payload.kind,
                "role": current.role or payload.role,
                "aliases": aliases,
                "approved": current.approved or payload.approved,
                "locked": current.locked or payload.locked,
            }
        )
    return [entity.model_dump() for entity in merged.values()]


def lint_chapter(
    chapter: ChapterDraft,
    outline_entry: StructuredOutlineEntry | dict[str, Any],
    plan: ChapterPlan | dict[str, Any],
    story_bible: StoryBible | dict[str, Any],
    continuity_ledger: ContinuityLedger | dict[str, Any],
    prior_chapters: list[ChapterDraft],
) -> ChapterLintResult:
    entry = outline_entry if isinstance(outline_entry, dict) else outline_entry.model_dump()
    chapter_plan = plan if isinstance(plan, dict) else plan.model_dump()
    ledger = continuity_ledger if isinstance(continuity_ledger, dict) else continuity_ledger.model_dump()
    content = (chapter.content or "").strip()
    lowered = content.lower()
    words = _normalized_words(content)
    result = ChapterLintResult()

    meta_language_hits = _meta_language_hits(content)
    if meta_language_hits:
        rendered_hits = ", ".join(f"'{hit}'" for hit in meta_language_hits[:5])
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} contains meta/outlining language {rendered_hits} that belongs in planning notes, not manuscript prose."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"

    story_turn = _story_turn_from_plan(chapter_plan)
    missing_story_turn_fields = _missing_story_turn_fields(story_turn)
    if missing_story_turn_fields:
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} is missing required story_turn fields: "
            + ", ".join(missing_story_turn_fields)
            + "."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"
    else:
        abstract_hits = _abstract_story_turn_hits(story_turn)
        if abstract_hits:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} has an abstract or reversible story_turn: "
                + ", ".join(f"'{hit}'" for hit in abstract_hits[:5])
                + "."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        for previous in prior_chapters:
            previous_turn = _story_turn_from_chapter(previous)
            if previous_turn and _story_turn_similarity(story_turn, previous_turn) >= 0.75:
                result.soft_warnings.append(
                    f"Chapter {chapter.chapter_number} repeats a similar irreversible story turn from chapter {previous.chapter_number}."
                )
                result.needs_repair = True
                result.repair_scope = "targeted_scene_and_ending"
                break

    ending_text = _ending_text(content)
    final_beat = _final_beat_text(content)
    ending_lowered = ending_text.lower()
    tail = lowered[-220:]
    for pattern in ABSTRACT_ENDING_PATTERNS:
        if re.search(pattern, tail) or re.search(pattern, ending_lowered):
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} ends in abstract or outline-summary language instead of a concrete hook."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
            break

    if final_beat and not _has_concrete_ending_action(final_beat):
        if _looks_like_internal_or_atmospheric_ending(final_beat):
            message = (
                f"Chapter {chapter.chapter_number} ends on internal emotion, theme, or atmosphere "
                "without a tangible event."
            )
        else:
            message = (
                f"Chapter {chapter.chapter_number} final beat lacks a concrete external action "
                "or visible consequence."
            )
        result.blocking_issues.append(message)
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"

    ending_hook = entry.get("concrete_ending_hook") or {}
    hook_terms = _meaningful_terms(
        " ".join(
            [
                str(ending_hook.get("trigger", "")),
                str(ending_hook.get("visible_object_or_actor", "")),
                str(ending_hook.get("next_problem", "")),
            ]
        )
    )
    tail_terms = _meaningful_terms(content[-320:])
    if hook_terms and not (hook_terms & tail_terms):
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} misses the planned concrete ending hook."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"
    elif not any(cue in tail for cue in CONCRETE_ENDING_CUES):
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} may still end too abstractly; the final beat lacks a strong visible action cue."
        )

    for phrase in STOCK_PHRASES:
        count = lowered.count(phrase)
        if count >= 2:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} repeats atmospheric phrase '{phrase}' {count} times."
            )

    sentences = _sentences(content)
    if len(sentences) >= 6:
        first_word_counts = Counter(
            start
            for sentence in sentences
            if (start := _sentence_start_key(sentence, 1))
        )
        repeated_first_words = [
            (start, count)
            for start, count in first_word_counts.items()
            if count >= max(4, len(sentences) // 3)
        ]
        three_word_starts = []
        for sentence in sentences:
            start = _sentence_start_key(sentence, 3)
            if start and len(start.split()) == 3:
                three_word_starts.append(start)
        repeated_three_word_starts = [
            (start, count)
            for start, count in Counter(three_word_starts).items()
            if count >= 3
        ]
        if repeated_first_words or repeated_three_word_starts:
            repeated = repeated_three_word_starts[0] if repeated_three_word_starts else repeated_first_words[0]
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} repeats sentence openings around '{repeated[0]}' {repeated[1]} times."
            )
            result.needs_repair = True
            _set_repair_scope(result, "voice_and_texture")

    filter_hits = [word for word in words if word in FILTER_VERBS]
    if len(filter_hits) >= 6:
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} leans on filter verbs {len(filter_hits)} times; convert perception summaries into direct sensory action."
        )
        result.needs_repair = True
        _set_repair_scope(result, "voice_and_texture")

    abstract_emotion_hits = [word for word in words if word in ABSTRACT_EMOTION_TERMS]
    sensory_hits = [word for word in words if word in SENSORY_ANCHOR_TERMS]
    if len(abstract_emotion_hits) >= 8 and len(sensory_hits) <= 2:
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} names abstract emotions heavily without enough concrete sensory anchors."
        )
        result.needs_repair = True
        _set_repair_scope(result, "voice_and_texture")

    for phrase in _style_avoid_terms(story_bible):
        count = lowered.count(phrase.lower())
        if count:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} uses style-avoid phrase '{phrase}' {count} times."
            )
            result.needs_repair = True
            _set_repair_scope(result, "voice_and_texture")

    opening_signature = _opening_signature(content)
    opening_prefix = _opening_signature(content, words=10)
    prior_signatures = {_opening_signature(previous.content or "") for previous in prior_chapters if previous.content}
    prior_prefixes = {_opening_signature(previous.content or "", words=10) for previous in prior_chapters if previous.content}
    if opening_signature and opening_signature in prior_signatures:
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} reuses an opening signature from an earlier chapter."
        )
        result.needs_repair = True
        result.repair_scope = result.repair_scope if result.repair_scope != "none" else "targeted_scene_and_ending"
    elif opening_prefix and opening_prefix in prior_prefixes:
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} reuses an opening signature from an earlier chapter."
        )
        result.needs_repair = True
        result.repair_scope = result.repair_scope if result.repair_scope != "none" else "targeted_scene_and_ending"

    uses_technical_solution = any(keyword in lowered for keyword in TECH_SOLUTION_KEYWORDS) or any(
        keyword in str(chapter_plan).lower() for keyword in TECH_SOLUTION_KEYWORDS
    )
    has_visible_cost = any(keyword in lowered for keyword in COST_KEYWORDS)
    if uses_technical_solution and not has_visible_cost:
        result.blocking_issues.append(
            f"Chapter {chapter.chapter_number} resolves a technical problem without showing a concrete cost or exposure."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"

    chapter_mode = normalize_chapter_mode(chapter_plan.get("chapter_mode") or entry.get("chapter_mode", ""))
    if chapter_mode in BREATHER_MODES:
        civilian_terms = _meaningful_terms(str(entry.get("civilian_life_detail", "")))
        emotional_terms = _meaningful_terms(str(entry.get("emotional_reveal", "")))
        ideology_terms = _meaningful_terms(str(entry.get("ideology_pressure", "")))
        content_terms = _meaningful_terms(content)

        if civilian_terms and len(civilian_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned civilian-life detail for a breather or aftermath chapter."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        if emotional_terms and len(emotional_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned emotional reveal for a breather or aftermath chapter."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        if ideology_terms and len(ideology_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned ideology pressure for a breather or aftermath chapter."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
        if uses_technical_solution:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is tagged as a breather or aftermath chapter but still centers technical problem-solving."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"

    friction = str(entry.get("side_character_friction", "")).strip()
    if friction:
        friction_terms = _meaningful_terms(friction)
        content_terms = _meaningful_terms(content)
        if friction_terms and len(friction_terms & content_terms) < 2:
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned side-character friction."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"

    independent_move = str(
        chapter_plan.get("independent_side_character_move")
        or entry.get("independent_side_character_move")
        or ""
    ).strip()
    if independent_move:
        move_terms = _meaningful_terms(independent_move) - _story_character_name_terms(story_bible)
        content_terms = _meaningful_terms(content)
        if move_terms and len(move_terms & content_terms) < min(2, len(move_terms)):
            result.blocking_issues.append(
                f"Chapter {chapter.chapter_number} is missing the planned independent side-character move."
            )
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"
    elif friction:
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} has side-character friction but no planned independent side-character move."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"

    for name in _major_side_character_names(story_bible):
        name_sentences = _sentences_with_name(content, name)
        if not name_sentences:
            continue
        if not _has_side_character_action(name_sentences):
            if _looks_exposition_only(name_sentences):
                issue = (
                    f"Side character {name} appears only to warn, explain, ask, or observe without an "
                    "independent action that changes the plot."
                )
            else:
                issue = (
                    f"Side character {name} appears without a clear independent action that changes the plot."
                )
            result.soft_warnings.append(issue)
            result.needs_repair = True
            result.repair_scope = "targeted_scene_and_ending"

    genre_expectations = [
        *(entry.get("genre_specific_beats") or []),
        entry.get("genre_state_change", ""),
        chapter_plan.get("genre_specific_focus", ""),
        *(chapter_plan.get("genre_specific_beats") or []),
    ]
    genre_terms = _meaningful_terms(" ".join(str(item) for item in genre_expectations if item))
    if genre_terms:
        content_terms = _meaningful_terms(content)
        required_overlap = min(2, len(genre_terms))
        if len(genre_terms & content_terms) < required_overlap:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} may be missing its planned genre-specific beat or state change."
            )

    approved_nouns = _approved_proper_nouns(story_bible, entry, chapter_plan, continuity_ledger)
    candidate_nouns = _proper_noun_candidates(content)
    unknown_nouns = sorted(candidate for candidate in candidate_nouns if candidate not in approved_nouns)
    if unknown_nouns:
        rendered = ", ".join(unknown_nouns[:5])
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} introduces possible unapproved proper nouns: {rendered}."
        )
        result.needs_repair = True
        result.repair_scope = result.repair_scope if result.repair_scope != "none" else "targeted_scene_and_ending"

    fatigue_hits = _technical_fatigue_hits(content)
    adjacent_chapter = _adjacent_prior_chapter(chapter.chapter_number, prior_chapters)
    adjacent_hits = _technical_fatigue_hits(adjacent_chapter.content or "") if adjacent_chapter else Counter()
    adjacent_overlap = set(fatigue_hits) & set(adjacent_hits)
    fatigue_score = _technical_fatigue_score(fatigue_hits, len(adjacent_overlap))
    adjacent_mode = _chapter_mode_from_summary(adjacent_chapter.outline_summary or "") if adjacent_chapter else ""
    if fatigue_score >= 6:
        labels = _technical_fatigue_labels(fatigue_hits)
        message = (
            f"Chapter {chapter.chapter_number} leans on too many technical emergency beats at once: "
            + ", ".join(labels)
            + ". Shift pressure toward interpersonal, political, physical, civilian, or emotional consequences."
        )
        if chapter_mode in BREATHER_MODES:
            result.blocking_issues.append(message)
        else:
            result.soft_warnings.append(message)
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"
    if len(adjacent_overlap) >= 2:
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} repeats technical emergency mechanics from adjacent chapter "
            f"{adjacent_chapter.chapter_number}: {', '.join(sorted(adjacent_overlap)[:5])}."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"
    if adjacent_chapter and adjacent_mode and adjacent_mode == chapter_mode and adjacent_overlap:
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} repeats the same scene mode as adjacent chapter "
            f"{adjacent_chapter.chapter_number} ({chapter_mode}) and reuses crisis mechanics: "
            + ", ".join(sorted(adjacent_overlap)[:5])
            + ". Vary the dominant dramatic mode or replace the repeated mechanic."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"
    if chapter_mode and chapter_mode not in TECHNICAL_SCENE_MODES and fatigue_score >= 4:
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} is tagged as {chapter_mode} but the prose falls back into technical crisis mechanics."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"
    if fatigue_score >= 4 and not _has_human_visible_consequence(content):
        result.soft_warnings.append(
            f"Chapter {chapter.chapter_number} uses system-crisis pressure without a human-visible consequence."
        )
        result.needs_repair = True
        result.repair_scope = "targeted_scene_and_ending"

    if ledger.get("memory_damage"):
        memory_terms = _meaningful_terms(str(ledger.get("memory_damage")))
        if memory_terms and len(memory_terms & _meaningful_terms(content)) < 1:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} may be dropping an ongoing memory-damage consequence."
            )
    if ledger.get("trust_fractures"):
        fracture_terms = _meaningful_terms(str(ledger.get("trust_fractures")))
        if fracture_terms and len(fracture_terms & _meaningful_terms(content)) < 1:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} may be dropping an ongoing trust fracture."
            )
    if ledger.get("civilian_pressure_points"):
        civilian_pressure_terms = _meaningful_terms(str(ledger.get("civilian_pressure_points")))
        if civilian_pressure_terms and len(civilian_pressure_terms & _meaningful_terms(content)) < 1:
            result.soft_warnings.append(
                f"Chapter {chapter.chapter_number} may be ignoring existing civilian consequences."
            )

    collisions = detect_canonical_entity_collisions(
        ledger.get("active_entities") or [],
        (chapter.continuity_update or {}).get("new_entities_introduced", []),
    )
    if collisions:
        result.blocking_issues.extend(collisions)
        result.canonical_collision = True
        result.needs_repair = True
        result.repair_scope = "full_chapter"

    result.blocking_issues = list(dict.fromkeys(result.blocking_issues))
    result.soft_warnings = list(dict.fromkeys(result.soft_warnings))
    if result.repair_scope == "none" and result.needs_repair:
        result.repair_scope = "targeted_scene_and_ending"
    return result


def lint_manuscript(chapters: list[ChapterDraft]) -> list[str]:
    findings: list[str] = []
    if not chapters:
        return findings

    heading_duplicates = [
        chapter.chapter_number
        for chapter in chapters
        if re.match(r"^\s*chapter\s+\d+\b", (chapter.content or "").strip(), flags=re.IGNORECASE)
    ]
    if heading_duplicates:
        findings.append(
            "Duplicate chapter heading text remained inside chapter prose for chapters "
            + ", ".join(str(number) for number in heading_duplicates)
            + "."
        )

    openings = Counter()
    for chapter in chapters:
        signature = _opening_signature(chapter.content or "")
        if signature:
            openings[signature] += 1
    repeated_openings = [signature for signature, count in openings.items() if count > 1]
    if repeated_openings:
        findings.append("At least two chapters share nearly identical opening paragraphs.")

    manuscript_text = "\n\n".join(chapter.content or "" for chapter in chapters).lower()
    for phrase in STOCK_PHRASES:
        count = manuscript_text.count(phrase)
        if count >= 4:
            findings.append(f"Repeated stock phrase '{phrase}' appears {count} times.")
    findings.extend(_publication_motif_findings(chapters))
    findings.extend(_chapter_opening_similarity_findings(chapters))
    findings.extend(_generated_abstract_cluster_findings(chapters))
    findings.extend(_dialogue_discipline_findings(chapters))

    for chapter in chapters:
        for hit in _meta_language_hits(chapter.content or ""):
            findings.append(
                f"Chapter {chapter.chapter_number} contains meta/outlining language '{hit}' that belongs in planning notes, not manuscript prose."
            )

    for previous, current in zip(chapters, chapters[1:]):
        previous_words = set(_normalized_words(previous.summary or previous.content or ""))
        current_words = set(_normalized_words(current.summary or current.content or ""))
        if previous_words and current_words:
            overlap = len(previous_words & current_words) / max(1, len(current_words))
            if overlap >= 0.85:
                findings.append(
                    f"Chapter {current.chapter_number} appears to restate too much of chapter {previous.chapter_number}."
                )

    final_chapters = chapters[-3:]
    if len(final_chapters) >= 2:
        final_signatures = {_opening_signature(chapter.summary or chapter.content or "", words=16) for chapter in final_chapters}
        if len(final_signatures) < len(final_chapters):
            findings.append("The final act appears to repeat climax or ending beats.")

    for chapter in chapters:
        if not (chapter.summary or "").strip():
            findings.append(f"Chapter {chapter.chapter_number} is missing a continuity summary.")

    return findings


def manuscript_quality_notes(
    chapters: list[ChapterDraft],
    story_bible: StoryBible | dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    bible = None
    if story_bible is not None:
        bible = story_bible if isinstance(story_bible, dict) else story_bible.model_dump()

    notes = {
        "chapter_ending_quality_notes": [],
        "easy_win_warnings": [],
        "proper_noun_continuity_findings": [],
        "side_character_agency_notes": [],
        "repetition_risks": [],
        "atmospheric_repetition_findings": [],
        "emotional_pacing_notes": [],
        "ideology_consistency_findings": [],
        "civilian_texture_findings": [],
        "technical_escalation_fatigue_findings": [],
        "crisis_loop_findings": [],
        "scene_mode_distribution_notes": [],
        "story_turn_quality_notes": [],
        "genre_contract_notes": [],
        "continuity_bible_findings": [],
        "continuity_bible_table": [],
    }

    manuscript_text = "\n\n".join(chapter.content or "" for chapter in chapters).lower()
    chapter_fatigue_hits = {
        chapter.chapter_number: _technical_fatigue_hits(chapter.content or "")
        for chapter in chapters
    }
    for chapter in chapters:
        qa = chapter.qa_notes or {}
        warnings = [*qa.get("warnings", []), *qa.get("soft_warnings", []), *qa.get("blocking_issues", [])]
        lowered_warnings = " ".join(warnings).lower()

        if qa.get("ending_concreteness_score", 10) <= 5 or "ending" in lowered_warnings:
            notes["chapter_ending_quality_notes"].append(
                f"Chapter {chapter.chapter_number} still shows ending risk: {', '.join(warnings[:2]) or 'ending needs sharper specificity.'}"
            )
        if qa.get("cost_consequence_realism_score", 10) <= 5 or "cost" in lowered_warnings or "technical problem" in lowered_warnings:
            notes["easy_win_warnings"].append(
                f"Chapter {chapter.chapter_number} may still resolve a major problem too cleanly."
            )
        if qa.get("side_character_independence_score", 10) <= 5 or "side-character" in lowered_warnings:
            notes["side_character_agency_notes"].append(
                f"Chapter {chapter.chapter_number} may need stronger side-character resistance or competing goals."
            )
        if qa.get("emotional_depth_score", 10) <= 5 or "emotional" in lowered_warnings or "memory-damage consequence" in lowered_warnings:
            notes["emotional_pacing_notes"].append(
                f"Chapter {chapter.chapter_number} may need more emotional aftermath or breathing room."
            )
        if qa.get("ideology_clarity_score", 10) <= 5 or "ideology" in lowered_warnings:
            notes["ideology_consistency_findings"].append(
                f"Chapter {chapter.chapter_number} may be blurring or contradicting a character's beliefs."
            )
        if qa.get("civilian_texture_score", 10) <= 5 or "civilian" in lowered_warnings:
            notes["civilian_texture_findings"].append(
                f"Chapter {chapter.chapter_number} may need more concrete civilian-life texture."
            )
        if "proper noun" in lowered_warnings or "canonical entity collision" in lowered_warnings or qa.get("proper_noun_continuity_score", 10) <= 5:
            notes["proper_noun_continuity_findings"].append(
                f"Chapter {chapter.chapter_number} raised proper-noun continuity concerns."
            )
        if (
            qa.get("technical_escalation_fatigue_score", 0) >= 6
            or "technical emergency beats" in lowered_warnings
            or "system-crisis pressure" in lowered_warnings
        ):
            notes["technical_escalation_fatigue_findings"].append(
                f"Chapter {chapter.chapter_number} may be too dense with emergency-system language."
            )
        if (
            qa.get("irreversibility_score", 10) <= 5
            or qa.get("choice_clarity_score", 10) <= 5
            or qa.get("cuttable_chapter_risk_score", 0) >= 6
            or "story turn" in lowered_warnings
            or "cuttable" in lowered_warnings
        ):
            notes["story_turn_quality_notes"].append(
                f"Chapter {chapter.chapter_number} may need a stronger irreversible choice/consequence turn."
            )
        if qa.get("genre_contract_score", 10) <= 5 or "genre" in lowered_warnings:
            notes["genre_contract_notes"].append(
                f"Chapter {chapter.chapter_number} may need stronger delivery against the selected genre contract."
            )
        story_turn = _story_turn_from_chapter(chapter)
        missing_fields = _missing_story_turn_fields(story_turn)
        if missing_fields:
            notes["story_turn_quality_notes"].append(
                f"Chapter {chapter.chapter_number} is missing stored story_turn fields: "
                + ", ".join(missing_fields)
                + "."
            )
        else:
            abstract_hits = _abstract_story_turn_hits(story_turn)
            if abstract_hits:
                notes["story_turn_quality_notes"].append(
                    f"Chapter {chapter.chapter_number} has an abstract or reversible stored story_turn: "
                    + ", ".join(f"'{hit}'" for hit in abstract_hits[:5])
                    + "."
                )
        for item in qa.get("genre_contract_findings", []) or []:
            notes["genre_contract_notes"].append(f"Chapter {chapter.chapter_number}: {item}")
        for phrase in STOCK_PHRASES:
            count = (chapter.content or "").lower().count(phrase)
            if count >= 2:
                notes["atmospheric_repetition_findings"].append(
                    f"Chapter {chapter.chapter_number} repeats '{phrase}' {count} times."
                )
        for entity in ((chapter.continuity_update or {}).get("new_entities_introduced", []) or []):
            name = str(entity.get("name", "")).strip()
            if name and not entity.get("approved"):
                notes["proper_noun_continuity_findings"].append(
                    f"Chapter {chapter.chapter_number} introduced unapproved canon entity '{name}'."
                )

    sorted_chapters = sorted(chapters, key=lambda item: item.chapter_number)
    notes["crisis_loop_findings"].extend(_crisis_loop_findings(sorted_chapters))
    for previous, current in zip(sorted_chapters, sorted_chapters[1:]):
        previous_hits = chapter_fatigue_hits.get(previous.chapter_number, Counter())
        current_hits = chapter_fatigue_hits.get(current.chapter_number, Counter())
        overlap = set(previous_hits) & set(current_hits)
        if len(overlap) >= 2:
            notes["technical_escalation_fatigue_findings"].append(
                f"Chapters {previous.chapter_number}-{current.chapter_number} repeat emergency mechanics: "
                + ", ".join(sorted(overlap)[:5])
                + "."
            )
        previous_mode = _chapter_mode_from_summary(previous.outline_summary or "")
        current_mode = _chapter_mode_from_summary(current.outline_summary or "")
        if previous_mode and current_mode and previous_mode == current_mode:
            if overlap:
                notes["scene_mode_distribution_notes"].append(
                    f"Chapters {previous.chapter_number}-{current.chapter_number} repeat scene mode {current_mode} and crisis mechanics: "
                    + ", ".join(sorted(overlap)[:5])
                    + "."
                )
            else:
                notes["scene_mode_distribution_notes"].append(
                    f"Chapters {previous.chapter_number}-{current.chapter_number} repeat scene mode {current_mode}; consider varying the dominant dramatic mode."
                )
        previous_turn = _story_turn_from_chapter(previous)
        current_turn = _story_turn_from_chapter(current)
        if previous_turn and current_turn and _story_turn_similarity(previous_turn, current_turn) >= 0.75:
            notes["story_turn_quality_notes"].append(
                f"Chapters {previous.chapter_number}-{current.chapter_number} have equivalent irreversible story turns; consider merge, cut, or a replacement choice/consequence."
            )

    chapter_modes = [
        _chapter_mode_from_summary(chapter.outline_summary or "")
        for chapter in sorted_chapters
    ]
    chapter_modes = [mode for mode in chapter_modes if mode]
    if chapter_modes:
        mode_counts = Counter(chapter_modes)
        distribution = ", ".join(
            f"{mode}: {count}"
            for mode, count in sorted(mode_counts.items(), key=lambda item: (-item[1], item[0]))
        )
        notes["scene_mode_distribution_notes"].append(f"Scene mode distribution: {distribution}.")
        dominant_mode, dominant_count = mode_counts.most_common(1)[0]
        if len(chapter_modes) >= 4 and dominant_count / len(chapter_modes) > 0.5:
            notes["scene_mode_distribution_notes"].append(
                f"Scene mode {dominant_mode} dominates {dominant_count} of {len(chapter_modes)} chapters; broaden the chapter-mode mix."
            )

    mechanic_chapter_counts: Counter[str] = Counter()
    for hits in chapter_fatigue_hits.values():
        mechanic_chapter_counts.update(hits.keys())
    repeated_mechanics = [
        mechanic
        for mechanic, chapter_count in mechanic_chapter_counts.items()
        if chapter_count >= 3
    ]
    if repeated_mechanics:
        notes["technical_escalation_fatigue_findings"].append(
            "Manuscript repeatedly returns to the same emergency mechanics: "
            + ", ".join(sorted(repeated_mechanics)[:6])
            + "."
        )

    notes["atmospheric_repetition_findings"].extend(_publication_motif_findings(sorted_chapters))
    notes["atmospheric_repetition_findings"].extend(_chapter_opening_similarity_findings(sorted_chapters))
    notes["repetition_risks"] = notes.get("repetition_risks", [])
    notes["repetition_risks"].extend(_generated_abstract_cluster_findings(sorted_chapters))
    notes["side_character_agency_notes"].extend(_dialogue_discipline_findings(sorted_chapters))

    if bible:
        side_decision_counts: Counter[str] = Counter()
        for chapter in chapters:
            decisions = (chapter.continuity_update or {}).get("side_character_decisions", {}) or {}
            for name, moves in decisions.items():
                side_decision_counts[str(name)] += len(moves or [])
        for name in _major_side_character_names(bible):
            count = side_decision_counts.get(name, 0)
            if count == 0:
                notes["side_character_agency_notes"].append(
                    f"Major side character {name} has no tracked independent decisions in continuity."
                )
            elif count < 2:
                notes["side_character_agency_notes"].append(
                    f"Major side character {name} has only {count} tracked independent decision."
                )
            else:
                notes["side_character_agency_notes"].append(
                    f"Major side character {name} has {count} tracked independent decisions."
                )

        for entity in bible.get("canon_registry") or []:
            name = str(entity.get("name", "")).strip()
            if not name:
                continue
            kind = str(entity.get("kind", "")).strip().lower()
            aliases = [alias for alias in entity.get("aliases", []) if alias]
            if not entity.get("approved"):
                notes["proper_noun_continuity_findings"].append(
                    f"Canonical {kind or 'entity'} '{name}' is present in the story bible but not approved yet."
                )
            canonical_mentions = manuscript_text.count(name.lower())
            alias_mentions = sum(manuscript_text.count(alias.lower()) for alias in aliases)
            if canonical_mentions == 0 and kind in {"project", "faction", "system", "location"}:
                notes["proper_noun_continuity_findings"].append(
                    f"Canonical {kind} '{name}' never appears in the manuscript."
                )
            if alias_mentions > 0 and canonical_mentions == 0:
                notes["proper_noun_continuity_findings"].append(
                    f"Alias drift risk: aliases for '{name}' appear without the canonical name."
                )
        continuity_bible_findings, continuity_bible_rows = _continuity_bible_notes(chapters, bible)
        notes["continuity_bible_findings"].extend(continuity_bible_findings)
        notes["continuity_bible_table"].extend(continuity_bible_rows)

    notes["chapter_ending_quality_notes"] = list(dict.fromkeys(notes["chapter_ending_quality_notes"]))
    notes["easy_win_warnings"] = list(dict.fromkeys(notes["easy_win_warnings"]))
    notes["proper_noun_continuity_findings"] = list(dict.fromkeys(notes["proper_noun_continuity_findings"]))
    notes["side_character_agency_notes"] = list(dict.fromkeys(notes["side_character_agency_notes"]))
    notes["atmospheric_repetition_findings"] = list(dict.fromkeys(notes["atmospheric_repetition_findings"]))
    notes["repetition_risks"] = list(dict.fromkeys(notes.get("repetition_risks", [])))
    notes["emotional_pacing_notes"] = list(dict.fromkeys(notes["emotional_pacing_notes"]))
    notes["ideology_consistency_findings"] = list(dict.fromkeys(notes["ideology_consistency_findings"]))
    notes["civilian_texture_findings"] = list(dict.fromkeys(notes["civilian_texture_findings"]))
    notes["technical_escalation_fatigue_findings"] = list(dict.fromkeys(notes["technical_escalation_fatigue_findings"]))
    notes["crisis_loop_findings"] = list(dict.fromkeys(notes["crisis_loop_findings"]))
    notes["scene_mode_distribution_notes"] = list(dict.fromkeys(notes["scene_mode_distribution_notes"]))
    notes["story_turn_quality_notes"] = list(dict.fromkeys(notes["story_turn_quality_notes"]))
    notes["genre_contract_notes"] = list(dict.fromkeys(notes["genre_contract_notes"]))
    notes["continuity_bible_findings"] = list(dict.fromkeys(notes["continuity_bible_findings"]))
    notes["continuity_bible_table"] = _dedupe_continuity_bible_rows(notes["continuity_bible_table"])
    return notes


def render_qa_report_markdown(report: ManuscriptQaReport) -> str:
    sections = [
        "# Manuscript QA Report",
        "",
        f"**Overall verdict:** {report.overall_verdict}",
        "",
    ]
    if report.publication_readiness_scores or report.publication_readiness_label or report.publication_readiness_summary:
        sections.extend(
            [
                "## Publication Readiness",
                "",
                f"**Status:** {report.publication_readiness_label or 'Not assessed'}",
                "",
                report.publication_readiness_summary or "No publication-readiness summary recorded.",
                "",
            ]
        )
        if report.publication_readiness_scores:
            sections.extend(
                [
                    "| Area | Score |",
                    "| --- | ---: |",
                    *[
                        f"| {label.replace('_', ' ').title()} | {score}/10 |"
                        for label, score in report.publication_readiness_scores.items()
                    ],
                    "",
                ]
            )
    sections.extend(
        [
            "## Strengths",
            "",
            *([f"- {item}" for item in report.strengths] or ["- No strengths recorded."]),
            "",
        "## Warnings",
        "",
        *([f"- {item}" for item in report.warnings] or ["- No major warnings recorded."]),
        "",
        "## Continuity Risks",
        "",
        *([f"- {item}" for item in report.continuity_risks] or ["- No continuity risks recorded."]),
        "",
        "## Continuity Bible Findings",
        "",
        *([f"- {item}" for item in report.continuity_bible_findings] or ["- No continuity bible findings recorded."]),
        "",
        "## Continuity Bible Table",
        "",
        *_render_continuity_bible_table(report.continuity_bible_table),
        "",
        "## Repetition Risks",
        "",
        *([f"- {item}" for item in report.repetition_risks] or ["- No repetition risks recorded."]),
        "",
        "## Ending Coherence Notes",
        "",
        *([f"- {item}" for item in report.ending_coherence_notes] or ["- No ending notes recorded."]),
        "",
        "## Chapter Ending Quality",
        "",
        *([f"- {item}" for item in report.chapter_ending_quality_notes] or ["- No chapter ending quality notes recorded."]),
        "",
        "## Easy Win Warnings",
        "",
        *([f"- {item}" for item in report.easy_win_warnings] or ["- No easy-win warnings recorded."]),
        "",
        "## Proper Noun Continuity",
        "",
        *([f"- {item}" for item in report.proper_noun_continuity_findings] or ["- No proper-noun continuity findings recorded."]),
        "",
        "## Side Character Agency",
        "",
        *([f"- {item}" for item in report.side_character_agency_notes] or ["- No side-character agency notes recorded."]),
        "",
        "## Atmospheric Repetition",
        "",
        *([f"- {item}" for item in report.atmospheric_repetition_findings] or ["- No atmospheric repetition findings recorded."]),
        "",
        "## Emotional Pacing",
        "",
        *([f"- {item}" for item in report.emotional_pacing_notes] or ["- No emotional pacing notes recorded."]),
        "",
        "## Ideology Consistency",
        "",
        *([f"- {item}" for item in report.ideology_consistency_findings] or ["- No ideology consistency findings recorded."]),
        "",
        "## Civilian Texture",
        "",
        *([f"- {item}" for item in report.civilian_texture_findings] or ["- No civilian texture findings recorded."]),
        "",
        "## Technical Escalation Fatigue",
        "",
        *([f"- {item}" for item in report.technical_escalation_fatigue_findings] or ["- No technical escalation fatigue findings recorded."]),
        "",
        "## Crisis Loop Findings",
        "",
        *([f"- {item}" for item in report.crisis_loop_findings] or ["- No crisis-loop findings recorded."]),
        "",
        "## Scene Mode Distribution",
        "",
        *([f"- {item}" for item in report.scene_mode_distribution_notes] or ["- No scene-mode distribution notes recorded."]),
        "",
        "## Story Turn Quality",
        "",
        *([f"- {item}" for item in report.story_turn_quality_notes] or ["- No story-turn quality notes recorded."]),
        "",
        "## Genre Contract",
        "",
        *([f"- {item}" for item in report.genre_contract_notes] or ["- No genre contract notes recorded."]),
        "",
        "## Deterministic Lint Findings",
        "",
        *([f"- {item}" for item in report.lint_findings] or ["- No deterministic lint findings recorded."]),
        "",
        ]
    )
    return "\n".join(sections).strip() + "\n"


def render_developmental_rewrite_report_markdown(
    plan: DevelopmentalRewritePlan,
    qa_report: ManuscriptQaReport,
) -> str:
    action_lines = []
    for item in plan.chapter_actions:
        chapters = ", ".join(f"Chapter {number}" for number in item.chapter_numbers) or "Unassigned chapter"
        action_lines.extend(
            [
                f"### {chapters}: {item.action.replace('_', ' ').title()}",
                "",
                f"- Reason: {item.reason or 'No reason recorded.'}",
                f"- Required story change: {item.required_story_change or 'No required story change recorded.'}",
                f"- Permanent consequence: {item.permanent_consequence or 'No permanent consequence recorded.'}",
                "",
            ]
        )
    sections = [
        "# Developmental Rewrite Report",
        "",
        f"**Overall diagnosis:** {plan.overall_diagnosis}",
        "",
        "## Act Structure Notes",
        "",
        *([f"- {item}" for item in plan.act_structure_notes] or ["- No act-structure notes recorded."]),
        "",
        "## Chapter Actions",
        "",
        *(action_lines or ["- No chapter actions recorded.", ""]),
        "## Merge Candidates",
        "",
        *([f"- {item}" for item in plan.merge_candidates] or ["- No merge candidates recorded."]),
        "",
        "## Cut Candidates",
        "",
        *([f"- {item}" for item in plan.cut_candidates] or ["- No cut candidates recorded."]),
        "",
        "## Continuity Repairs",
        "",
        *([f"- {item}" for item in plan.continuity_repairs] or ["- No continuity repairs recorded."]),
        "",
        "## Theme Arc Repairs",
        "",
        *([f"- {item}" for item in plan.theme_arc_repairs] or ["- No theme-arc repairs recorded."]),
        "",
        "## Prose Pattern Repairs",
        "",
        *([f"- {item}" for item in plan.prose_pattern_repairs] or ["- No prose-pattern repairs recorded."]),
        "",
        "## QA Comparison",
        "",
        "### Pre-Rewrite Risks",
        "",
        *(
            [f"- {item}" for item in plan.pre_rewrite_risks]
            or [
                f"- {item}"
                for item in [
                    *qa_report.warnings,
                    *qa_report.repetition_risks,
                    *qa_report.continuity_risks,
                    *qa_report.continuity_bible_findings,
                    *qa_report.crisis_loop_findings,
                ]
            ]
            or ["- No pre-rewrite risks recorded."]
        ),
        "",
        "### Post-Rewrite Risk Targets",
        "",
        *([f"- {item}" for item in plan.post_rewrite_risk_targets] or ["- No post-rewrite risk targets recorded."]),
        "",
    ]
    return "\n".join(sections).strip() + "\n"


def render_developmental_qa_comparison_markdown(
    plan: DevelopmentalRewritePlan,
    qa_report: ManuscriptQaReport,
) -> str:
    pre_rewrite_risks = (
        plan.pre_rewrite_risks
        or [
            *qa_report.warnings,
            *qa_report.continuity_risks,
            *qa_report.repetition_risks,
            *qa_report.chapter_ending_quality_notes,
            *qa_report.technical_escalation_fatigue_findings,
            *qa_report.crisis_loop_findings,
            *qa_report.continuity_bible_findings,
            *qa_report.story_turn_quality_notes,
        ]
    )
    sections = [
        "# Developmental QA Comparison",
        "",
        f"**Pre-rewrite verdict:** {qa_report.overall_verdict}",
        "",
        "## Pre-Rewrite Risks",
        "",
        *([f"- {item}" for item in pre_rewrite_risks] or ["- No pre-rewrite risks recorded."]),
        "",
        "## Post-Rewrite Risk Targets",
        "",
        *([f"- {item}" for item in plan.post_rewrite_risk_targets] or ["- No post-rewrite risk targets recorded."]),
        "",
        "## Verification Focus",
        "",
        "- Run manuscript QA again after applying the revised outline.",
        "- Confirm cut or merged chapters still preserve their permanent consequences.",
        "- Confirm repetition, continuity, and cuttable-chapter risks trend down from the pre-rewrite report.",
        "",
    ]
    return "\n".join(sections).strip() + "\n"


def render_revised_outline_markdown(
    project_title: str,
    plan: DevelopmentalRewritePlan,
    chapters: list[ChapterDraft],
) -> str:
    chapter_lookup = {chapter.chapter_number: chapter for chapter in chapters}
    sections = [
        "# Revised Outline",
        "",
        f"Project: {project_title}",
        "",
        "This outline is a developmental rewrite map, not rewritten prose.",
        "",
    ]
    for item in plan.chapter_actions:
        chapters = [chapter_lookup.get(number) for number in item.chapter_numbers]
        titles = [
            f"Chapter {chapter.chapter_number}: {chapter.title}"
            for chapter in chapters
            if chapter is not None
        ]
        heading = ", ".join(titles) or ", ".join(f"Chapter {number}" for number in item.chapter_numbers) or "Unassigned chapter"
        sections.extend(
            [
                f"## {heading}",
                "",
                f"- Action: {item.action.replace('_', ' ').title()}",
                f"- Reason: {item.reason or 'No reason recorded.'}",
                f"- Required story change: {item.required_story_change or 'No required story change recorded.'}",
                f"- Permanent consequence: {item.permanent_consequence or 'No permanent consequence recorded.'}",
                "",
            ]
        )
    return "\n".join(sections).strip() + "\n"
