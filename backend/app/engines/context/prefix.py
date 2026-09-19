"""Order of the prompt sections, most stable first, so that providers can reuse a cached prefix.

Providers with prefix caching (OpenAI and compatible servers automatically from 1024 tokens, by
blocks of 128; vLLM, llama.cpp and DeepSeek likewise) bill or recompute only what follows the first
byte that differs from an earlier request. Before 0.6 the neighbouring passages came first, right
after the system prompt: every request differed from the previous one there, and nothing but the
system prompt could ever be reused.

Sections are now written in tiers: what is the same for the whole book (book context), then for the
chapter or the job (user rules, chapter context), then what depends on the names the passage
mentions (glossary, characters, conventions), then the neighbours, the working material of the
operation (current translation, review) and the passage itself, always last. Inside a tier the
order chosen by the context builder is kept. The sections and their contents are unchanged: only
their order is, which the prompts never relied on (every section is named).
"""

TIERS = {
    # Same for every passage of the book, until the bible is revised.
    "EDITORIAL_BOOK_CONTEXT": 0,
    "CHARACTER_REGISTRY": 0,
    # Same for the passages of a chapter or of a job.
    "USER_RULES": 1,
    "EDITORIAL_CHAPTER_CONTEXT": 1,
    # Selected by the names the passage and its neighbours mention: often the same several times in a row.
    "LOCKED_GLOSSARY": 2,
    "GLOSSARY": 2,
    "CONFIRMED_IDENTITIES": 2,
    "EDITORIAL_CHARACTERS": 2,
    "CHARACTER_RELATIONSHIPS": 2,
    "SERIES_CONVENTIONS": 2,
    # Parallel analysis: what the passages before this one established (app.engines.memory.timeline).
    "KNOWN_IDENTITIES": 2,
    "KNOWN_RELATIONSHIPS": 2,
    "KNOWN_TERMS": 2,
    "RECENT_CHARACTERS": 3,
    "EARLIER_PASSAGES": 3,
    # Changes with every passage.
    "CHAPTER_STATE": 3,
    "PREVIOUS_CONTEXT": 4,
    "NEXT_CONTEXT": 4,
}
RETRIEVED = 3  # OPENVIKING_* memories: chosen for this passage
WORKING = 5  # CURRENT_TRANSLATION, REVIEW, WEB_EVIDENCE…: what the operation works on
TARGET = "TARGET_TEXT"


def tier(name: str) -> int:
    if name == TARGET:
        return WORKING + 1
    if name.startswith("OPENVIKING_"):
        return RETRIEVED
    return TIERS.get(name, WORKING)


def ordered(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(name, content) pairs, stable tiers first, the passage to process last."""
    return sorted(sections, key=lambda pair: tier(pair[0]))
