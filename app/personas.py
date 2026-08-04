"""Preset system prompts + sampling profiles.

System prompts are deliberately short: every system token is prefilled on every
fresh conversation, and on 2 vCPU that is measured in hundreds of milliseconds.
Keep each under ~60 tokens.
"""

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    glyph: str
    blurb: str
    system: str
    temperature: float
    top_p: float
    top_k: int
    repeat_penalty: float
    max_tokens: int

    def as_dict(self) -> dict:
        return asdict(self)

    def sampling(self) -> dict:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repeat_penalty": self.repeat_penalty,
            "max_tokens": self.max_tokens,
        }


PERSONAS: dict[str, Persona] = {
    p.id: p
    for p in (
        Persona(
            id="concise",
            name="Concise",
            glyph="◆",
            blurb="Short, factual answers",
            system=(
                "You are a precise assistant. Answer directly in at most four "
                "sentences. No preamble, no restating the question."
            ),
            temperature=0.4,
            top_p=0.9,
            top_k=40,
            repeat_penalty=1.08,
            max_tokens=256,
        ),
        Persona(
            id="explainer",
            name="Explainer",
            glyph="◈",
            blurb="Teaches with analogies",
            system=(
                "You are a patient teacher. Explain clearly, build from what the "
                "reader already knows, and use one concrete analogy per answer."
            ),
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            repeat_penalty=1.05,
            max_tokens=512,
        ),
        Persona(
            id="code",
            name="Code",
            glyph="◇",
            blurb="Code first, prose second",
            system=(
                "You are a senior engineer. Lead with a correct, runnable code "
                "block in a fenced markdown block with its language tag. Explain "
                "only what the code does not."
            ),
            temperature=0.2,
            top_p=0.9,
            top_k=40,
            repeat_penalty=1.0,
            max_tokens=512,
        ),
        Persona(
            id="brainstorm",
            name="Brainstorm",
            glyph="✦",
            blurb="Divergent ideas, fast",
            system=(
                "You are an idea generator. Reply with a numbered list of varied, "
                "specific ideas. Favour range over polish. No disclaimers."
            ),
            temperature=0.95,
            top_p=0.95,
            top_k=60,
            repeat_penalty=1.1,
            max_tokens=384,
        ),
    )
}

DEFAULT_PERSONA = "explainer"


def get_persona(persona_id: str | None) -> Persona:
    return PERSONAS.get(persona_id or "", PERSONAS[DEFAULT_PERSONA])


def persona_list() -> list[dict]:
    return [p.as_dict() for p in PERSONAS.values()]
