"""The corpus for this build: system/model cards with extractable numeric tables.

Two reliable source families (both discovered to exist at build time):
- OpenAI Deployment Safety Hub pages (HTML, real <table> DOM).
- Google DeepMind model-card PDFs (text layer + benchmark/safety tables).
"""

import dataclasses


@dataclasses.dataclass(frozen=True)
class Source:
    model_id: str
    label: str
    kind: str            # "html" | "pdf"
    origin_url: str


_HUB = "https://deploymentsafety.openai.com"
_DM = "https://storage.googleapis.com/deepmind-media/Model-Cards"

SOURCES: tuple[Source, ...] = (
    Source("gpt-5.5", "GPT-5.5 system card", "html", f"{_HUB}/gpt-5-5"),
    Source("gpt-5.2", "GPT-5.2 system card", "html", f"{_HUB}/gpt-5-2"),
    Source("gpt-5.1", "GPT-5.1 system card", "html", f"{_HUB}/gpt-5-1"),
    Source("gpt-5", "GPT-5 system card", "html", f"{_HUB}/gpt-5"),
    Source("o3", "OpenAI o3 system card", "html", f"{_HUB}/o3"),
    Source("sora-2", "Sora 2 system card", "html", f"{_HUB}/sora-2"),
    Source("gpt-oss", "gpt-oss system card", "html", f"{_HUB}/gpt-oss"),
    Source("gemini-3-pro", "Gemini 3 Pro model card", "pdf", f"{_DM}/Gemini-3-Pro-Model-Card.pdf"),
    Source("gemini-3.1-pro", "Gemini 3.1 Pro model card", "pdf", f"{_DM}/Gemini-3-1-Pro-Model-Card.pdf"),
    Source("gemini-2.5-pro", "Gemini 2.5 Pro model card", "pdf", f"{_DM}/Gemini-2-5-Pro-Model-Card.pdf"),
    Source("gemini-2.5-flash", "Gemini 2.5 Flash model card", "pdf", f"{_DM}/Gemini-2-5-Flash-Model-Card.pdf"),
    Source("gemini-2.0-flash", "Gemini 2.0 Flash model card", "pdf", f"{_DM}/Gemini-2-0-Flash-Model-Card.pdf"),
)
