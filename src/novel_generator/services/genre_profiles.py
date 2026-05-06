from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_GENRE_PROFILE = "sci_fi_thriller"


@dataclass(frozen=True)
class GenreProfile:
    id: str
    label: str
    description: str
    story_bible_focus: list[str] = field(default_factory=list)
    outline_focus: list[str] = field(default_factory=list)
    drafting_focus: list[str] = field(default_factory=list)
    continuity_focus: list[str] = field(default_factory=list)
    lint_focus: list[str] = field(default_factory=list)
    qa_focus: list[str] = field(default_factory=list)
    genre_contract: list[str] = field(default_factory=list)
    default_genre_state: dict[str, str] = field(default_factory=dict)


GENRE_PROFILES: dict[str, GenreProfile] = {
    "sci_fi_thriller": GenreProfile(
        id="sci_fi_thriller",
        label="Sci-Fi Thriller",
        description="Plot-forward speculative fiction with system rules, technical cost, ideology pressure, and civilian fallout.",
        story_bible_focus=[
            "Track system rules, faction pressure, ideological fault lines, and the real cost of control.",
            "Make the ending promise hinge on a high-stakes decision with public consequences.",
        ],
        outline_focus=[
            "Escalate clue chains, technical consequences, and public-risk fallout.",
            "Alternate hard pressure with breathers so the manuscript does not become nonstop alarm language.",
        ],
        drafting_focus=[
            "Show the price of each technical win on the page.",
            "Keep civilian consequence and ideological conflict visible inside the action.",
        ],
        continuity_focus=[
            "Preserve patch status, system state, faction pressure, and public consequences.",
        ],
        lint_focus=[
            "Flag zero-cost technical wins, abstract endings, and escalation fatigue.",
        ],
        qa_focus=[
            "Judge whether the manuscript sustains pressure without losing human cost.",
        ],
        genre_contract=[
            "System rules stay legible and matter to the plot.",
            "Each breakthrough creates a new public or technical consequence.",
            "The ending resolves one central dilemma rather than multiple competing finales.",
        ],
        default_genre_state={
            "system_pressure": "The governing system has not been meaningfully destabilized yet.",
            "civilian_fallout": "Civilian consequences are still latent rather than visible.",
            "ideology_balance": "Belief conflict is established but not yet broken open.",
        },
    ),
    "cyberpunk": GenreProfile(
        id="cyberpunk",
        label="Cyberpunk",
        description="High-tech, low-life pressure with body autonomy, class conflict, surveillance, and street-level compromise.",
        story_bible_focus=[
            "Track corporate power, surveillance control, body autonomy, and class asymmetry.",
            "Make the protagonist's moral compromise inseparable from the world they move through.",
        ],
        outline_focus=[
            "Keep pressure moving between institutions, street actors, and personal survival.",
            "Let victories stain the protagonist's body, identity, or social position.",
        ],
        drafting_focus=[
            "Favor tactile street detail, social rot, and compromised alliances over abstract futurism.",
        ],
        continuity_focus=[
            "Track leverage, debt, augment damage, and shifting alliance trust.",
        ],
        lint_focus=[
            "Flag generic tech-noir texture if social or bodily cost is missing.",
        ],
        qa_focus=[
            "Judge whether the story feels socially grounded instead of just neon and terminals.",
        ],
        genre_contract=[
            "Technology amplifies inequality, not just spectacle.",
            "Personal autonomy is always under pressure from money, systems, or ownership.",
            "Street-level texture should matter alongside the main conspiracy or chase.",
        ],
        default_genre_state={
            "surveillance_pressure": "The system's gaze is active but not yet unavoidable.",
            "body_autonomy_risk": "The protagonist has not yet paid a bodily cost for access or survival.",
            "class_pressure": "Institutional power still outweighs any street leverage.",
        },
    ),
    "fantasy_epic": GenreProfile(
        id="fantasy_epic",
        label="Fantasy Epic",
        description="Large-scale fantasy with factions, mythic consequence, magic cost, and layered loyalties.",
        story_bible_focus=[
            "Track world rules, faction loyalties, prophecy or myth pressure, and the cost of power.",
            "Build a long-arc ending promise with political and personal consequence.",
        ],
        outline_focus=[
            "Balance quest momentum with loyalty conflict, world texture, and large-scope consequence.",
            "Let revelations reframe duty, lineage, or the price of magic.",
        ],
        drafting_focus=[
            "Keep scene-level human stakes visible inside the wider mythic canvas.",
        ],
        continuity_focus=[
            "Track faction alignment, magic debt, vows, and public consequence.",
        ],
        lint_focus=[
            "Flag empty lore-dumps and worldbuilding that never alters character choice.",
        ],
        qa_focus=[
            "Judge whether the manuscript earns scale through consequence, not just names and lore.",
        ],
        genre_contract=[
            "Magic or power always has cost, limit, or corruption pressure.",
            "Faction loyalties and oaths should shape major turns.",
            "The ending must close the central oath, war, or mythic promise cleanly.",
        ],
        default_genre_state={
            "faction_pressure": "Major factions are in tension but not fully committed.",
            "magic_cost": "The true price of power has not yet fully landed.",
            "quest_burden": "The long-arc obligation is active but still survivable.",
        },
    ),
    "mystery": GenreProfile(
        id="mystery",
        label="Mystery",
        description="Clue-driven fiction that tracks suspects, evidence, motive, opportunity, deduction, and fair-play timing.",
        story_bible_focus=[
            "Track the crime or central puzzle, suspect field, motive pressure, and fair-play clue logic.",
            "Make the ending promise about a reveal the reader can retrospectively earn.",
        ],
        outline_focus=[
            "Ensure chapters alternate discovery, misdirection, pressure, and interpretive turns.",
            "Keep red herrings and suspect pressure distinct from the real clue line.",
        ],
        drafting_focus=[
            "Surface evidence physically and emotionally so clues are not just recap exposition.",
        ],
        continuity_focus=[
            "Track clue state, suspect pressure, and what the protagonist believes at each stage.",
        ],
        lint_focus=[
            "Flag chapters that move without adding clue value, suspect pressure, or deduction movement.",
        ],
        qa_focus=[
            "Judge whether the reveal feels fair, planted, and timed rather than arbitrary.",
        ],
        genre_contract=[
            "Each chapter should advance evidence, suspect pressure, or deductive interpretation.",
            "Motive and opportunity must stay coherent as the suspect field shifts.",
            "The final reveal should feel surprising but retrospectively fair.",
        ],
        default_genre_state={
            "case_pressure": "The central case is open and still poorly understood.",
            "suspect_field": "The suspect field is wide and unstable.",
            "clue_chain": "The clue line exists but has not yet cohered into a theory.",
        },
    ),
    "horror": GenreProfile(
        id="horror",
        label="Horror",
        description="Dread-first fiction that tracks threat rules, revelation pacing, fear response, isolation, and sensory unease.",
        story_bible_focus=[
            "Track fear logic, threat rules, taboo pressure, and the emotional cost of surviving knowledge.",
            "Make the ending promise hinge on what the characters must endure or become.",
        ],
        outline_focus=[
            "Escalate dread and revelation timing instead of revealing the whole threat too quickly.",
            "Let isolation, sensory unease, and fear response shape decisions.",
        ],
        drafting_focus=[
            "Favor atmosphere, bodily fear, and unstable safety over constant explanation.",
        ],
        continuity_focus=[
            "Track threat exposure, taboo violations, fear state, and who still believes the threat rules.",
        ],
        lint_focus=[
            "Flag chapters that explain away dread or lose sensory unease.",
        ],
        qa_focus=[
            "Judge whether fear accumulates and mutates rather than repeating the same scare beat.",
        ],
        genre_contract=[
            "Threat rules should become clearer even as safety collapses.",
            "Dread must rise through uncertainty, sensory detail, and consequence.",
            "The ending should leave a strong emotional afterimage, not just explain the monster.",
        ],
        default_genre_state={
            "dread_level": "Unease is present but not yet overwhelming.",
            "threat_rule_clarity": "The threat is partly understood and partly forbidden.",
            "isolation_pressure": "Support structures exist but are weakening.",
        },
    ),
    "romance": GenreProfile(
        id="romance",
        label="Romance",
        description="Relationship-forward fiction that tracks attraction, trust, vulnerability, rupture, repair, and emotional payoff.",
        story_bible_focus=[
            "Track the relationship arc, emotional wound, trust pressure, and the real barrier to intimacy.",
            "Make the ending promise about romantic payoff, emotional honesty, or earned repair.",
        ],
        outline_focus=[
            "Each chapter should move attraction, trust, vulnerability, rupture, or repair.",
            "Keep external plot pressure in service of the relationship arc instead of replacing it.",
        ],
        drafting_focus=[
            "Prioritize chemistry, subtext, emotional honesty, and scene-level intimacy pressure.",
        ],
        continuity_focus=[
            "Track relationship state, trust shifts, vulnerability exposure, and unresolved rupture.",
        ],
        lint_focus=[
            "Flag chapters that move the plot but leave the relationship emotionally static.",
        ],
        qa_focus=[
            "Judge whether intimacy builds, breaks, and repairs in a satisfying curve.",
        ],
        genre_contract=[
            "The relationship arc must move in most chapters, even when the external plot leads.",
            "Trust and vulnerability shifts should be visible, not just summarized.",
            "The ending payoff must feel emotionally earned.",
        ],
        default_genre_state={
            "relationship_state": "Attraction is unstable and trust is incomplete.",
            "trust_level": "The core pair does not yet feel safe telling the truth.",
            "rupture_status": "The central emotional barrier is active and unresolved.",
        },
    ),
    "cozy": GenreProfile(
        id="cozy",
        label="Cozy",
        description="Comfort-forward fiction that values community texture, routine, gentle stakes, emotional warmth, and satisfying small-scale change.",
        story_bible_focus=[
            "Track community ties, rituals, home-space comfort, and gentle but meaningful stakes.",
            "Make the ending promise about belonging, healing, or a repaired social fabric.",
        ],
        outline_focus=[
            "Balance plot motion with scenes of community, craft, meals, routines, or place-based warmth.",
            "Let conflict disturb comfort without abandoning the promise of restoration.",
        ],
        drafting_focus=[
            "Favor sensory warmth, hospitality, social detail, and emotionally safe humor where appropriate.",
        ],
        continuity_focus=[
            "Track community trust, place-based rituals, and the state of the home or village ecosystem.",
        ],
        lint_focus=[
            "Flag chapters that become generic action or suspense with no comforting social texture.",
        ],
        qa_focus=[
            "Judge whether the story preserves charm and warmth while still changing something meaningful.",
        ],
        genre_contract=[
            "Community texture and daily life should appear regularly on the page.",
            "Conflict should matter, but the emotional promise stays restorative rather than punishing.",
            "The ending should feel settled, welcoming, and earned.",
        ],
        default_genre_state={
            "community_trust": "The core community is stable but not fully healed.",
            "comfort_space": "The central place of belonging is active but under light strain.",
            "gentle_stakes": "Small-scale consequences matter deeply to the people involved.",
        },
    ),
    "web_serial": GenreProfile(
        id="web_serial",
        label="Web Serial",
        description="Installment-first fiction that emphasizes hooks, recall, escalation, payoff cadence, and a strong per-chapter reading experience.",
        story_bible_focus=[
            "Track long-arc promise, installment rhythm, audience recall needs, and recurring hook machinery.",
            "Keep the world expandable without making the current arc shapeless.",
        ],
        outline_focus=[
            "Every chapter should have a local hook, a progress beat, and a strong return incentive.",
            "Balance short-term payoff with clear long-arc momentum.",
        ],
        drafting_focus=[
            "Favor readability, memorable turns, and strong chapter landings that invite the next click.",
        ],
        continuity_focus=[
            "Track open arcs, chapter hooks, and what the audience most recently learned or expects.",
        ],
        lint_focus=[
            "Flag chapters that neither pay off something recent nor generate a compelling next-click hook.",
        ],
        qa_focus=[
            "Judge whether the manuscript has strong serial cadence instead of reading like one undifferentiated block.",
        ],
        genre_contract=[
            "Each chapter should feel satisfying as an installment, not just as a fragment.",
            "Hooks should be concrete and varied rather than all abstract stakes.",
            "The long arc should stay legible despite expanding subplots.",
        ],
        default_genre_state={
            "arc_visibility": "The long arc is visible but still lightly sketched.",
            "hook_pressure": "Installment hooks matter as much as scene-level closure.",
            "reader_recall": "Important callbacks should stay fresh and legible.",
        },
    ),
}


def genre_profile(profile_name: str | None) -> GenreProfile:
    key = (profile_name or DEFAULT_GENRE_PROFILE).strip() or DEFAULT_GENRE_PROFILE
    return GENRE_PROFILES.get(key, GENRE_PROFILES[DEFAULT_GENRE_PROFILE])


def genre_profile_options() -> list[dict[str, str]]:
    return [
        {
            "name": profile.id,
            "label": profile.label,
            "description": profile.description,
        }
        for profile in GENRE_PROFILES.values()
    ]
