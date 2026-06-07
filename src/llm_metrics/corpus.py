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

# Temporarily narrowed to gpt-oss only (the full corpus is preserved in git
# history; restore the other Source rows to re-enable them).
SOURCES: tuple[Source, ...] = (
    Source("gpt-oss", "gpt-oss system card", "html", f"{_HUB}/gpt-oss"),
)
