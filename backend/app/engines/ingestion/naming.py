"""Series names, volume and chapter numbers inferred from file names and metadata.

Priority of a volume number: the user's correction (applied by the caller), the consensus of the
whole batch of file names, the file name alone, the book's metadata, then a fallback that is never
kept without the user's confirmation.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from os.path import commonprefix

HIGH, MEDIUM, LOW = "high", "medium", "low"
ROMAN = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
NUMBER = r"(\d{1,5}(?:[.,]\d{1,2})?)"
VOLUME_KEYWORD = re.compile(
    r"(?<![^\W\d_])(?:volume|vol|tome|book|livre|band|part|partie)\.?\s*(?:n[°o]\.?\s*)?[#:]?\s*"
    r"(\d{1,4}|[ivxlcdm]{1,8})(?![^\W_])",
    re.IGNORECASE,
)
VOLUME_SHORT = re.compile(r"(?<![^\W\d_])v\.?\s?(\d{1,4})(?!\d)", re.IGNORECASE)
VOLUME_HASH = re.compile(r"#\s*(\d{1,4})(?!\d)")
VOLUME_BRACKET = re.compile(r"[\[(]\s*(\d{1,4})\s*[\])]")
CHAPTER_KEYWORD = re.compile(
    r"(?<![^\W\d_])(?:chapter|chapitre|chap|ch|c|capitulo|capítulo|kapitel|episode|ep)\.?\s*[-_#]?\s*" + NUMBER
    + r"(?![\d])",
    re.IGNORECASE,
)
LEADING_NUMBER = re.compile(r"^\s*" + NUMBER + r"(?!\d)")
SEPARATORS = " \t-_–—.,:;#([{"


def normalize_series(name: str) -> str:
    """How two spellings of one series compare: spacing and case do not matter."""
    return " ".join((name or "").split()).casefold()


def display_series(name: str) -> str:
    return " ".join((name or "").split())[:500]


def natural_key(text: str) -> tuple:
    """"Chapter 2" before "Chapter 10": digits compare as numbers, the rest without case or accents."""
    folded = unicodedata.normalize("NFKD", text).casefold()
    parts = re.split(r"(\d+)", folded)
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in parts if part)


def roman_value(text: str) -> int | None:
    value = text.upper()
    if not value or not ROMAN.match(value):
        return None
    total = 0
    for index, char in enumerate(value):
        current = ROMAN_VALUES[char]
        following = ROMAN_VALUES[value[index + 1]] if index + 1 < len(value) else 0
        total += -current if current < following else current
    return total or None


def stem(name: str) -> str:
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base


def clean_title(name: str) -> str:
    """Default chapter or volume title: the file name without extension, underscores or extra spaces."""
    value = stem(name).replace("_", " ")
    return " ".join(value.split())[:500]


def _number(text: str) -> float | None:
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= 100000 else None


def as_int(value: float | None) -> int | None:
    return int(value) if value is not None and float(value).is_integer() else None


@dataclass
class Guess:
    value: float | None = None
    confidence: str = LOW
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


def volume_from_name(name: str) -> Guess:
    text = stem(name)
    for pattern, reason in (
        (VOLUME_KEYWORD, "mot-clé de volume dans le nom du fichier"),
        (VOLUME_SHORT, "abréviation « v » suivie d’un numéro dans le nom du fichier"),
        (VOLUME_HASH, "numéro précédé de « # » dans le nom du fichier"),
        (VOLUME_BRACKET, "numéro entre crochets ou parenthèses dans le nom du fichier"),
    ):
        matches = pattern.findall(text)
        values = []
        for match in matches:
            value = int(match) if match.isdigit() else roman_value(match)
            if value is not None:
                values.append(value)
        if len(set(values)) == 1:
            return Guess(values[0], MEDIUM, reason)
        if len(set(values)) > 1:
            return Guess(None, LOW, "plusieurs numéros de volume contradictoires dans le nom du fichier")
    return Guess()


def _batch_variable_parts(stems: list[str]) -> tuple[str, str, list[str]]:
    folded = [s.casefold() for s in stems]
    prefix = commonprefix(folded)
    suffix = commonprefix([s[::-1] for s in folded])[::-1]
    # A shared digit must not be eaten by the prefix ("Vol 10" / "Vol 11" share "Vol 1").
    while prefix and prefix[-1].isdigit():
        prefix = prefix[:-1]
    while suffix and suffix[0].isdigit():
        suffix = suffix[1:]
    parts = []
    for original in stems:
        end = len(original) - len(suffix) if suffix else len(original)
        parts.append(original[len(prefix) : max(len(prefix), end)])
    return prefix, suffix, parts


def batch_volumes(names: list[str]) -> list[Guess]:
    """Numbers read from what varies between the file names of one batch."""
    stems = [stem(name) for name in names]
    if len(stems) < 2:
        return [Guess() for _ in stems]
    _, _, parts = _batch_variable_parts(stems)
    values: list[int | None] = []
    for part in parts:
        token = part.strip(SEPARATORS + ")]}")
        digits = re.fullmatch(r"(?:vol(?:ume)?|tome|book|v)?\.?\s*#?\s*(\d{1,4})", token, re.IGNORECASE)
        if digits:
            values.append(int(digits.group(1)))
            continue
        roman = re.fullmatch(r"(?:vol(?:ume)?|tome|book)?\.?\s*([ivxlcdm]{1,8})", token, re.IGNORECASE)
        values.append(roman_value(roman.group(1)) if roman else None)
    if any(value is None for value in values) or len(set(values)) != len(values):
        return [Guess() for _ in stems]
    return [Guess(value, HIGH, "numéro qui varie entre les noms des fichiers du lot") for value in values]


@dataclass
class SeriesGuess:
    name: str = ""
    confidence: str = LOW
    reason: str = ""


def series_from_names(names: list[str]) -> SeriesGuess:
    """The shared part of the file names, without the volume words and numbers around it."""
    stems = [stem(name) for name in names]
    if not stems:
        return SeriesGuess()
    if len(stems) == 1:
        base = stems[0]
        for pattern in (VOLUME_KEYWORD, VOLUME_SHORT, VOLUME_HASH, VOLUME_BRACKET):
            match = pattern.search(base)
            if match:
                base = base[: match.start()]
        confidence, reason = MEDIUM, "nom du fichier sans son numéro de volume"
    else:
        prefix, _, _ = _batch_variable_parts(stems)
        base = stems[0][: len(prefix)]
        confidence, reason = HIGH, "début commun aux noms des fichiers du lot"
    base = re.sub(r"(?:\b(?:vol(?:ume)?|tome|book|livre|part|partie)\.?|\bv)\s*$", "", base.strip(), flags=re.I)
    name = display_series(base.replace("_", " ").strip(SEPARATORS))
    if len(name) < 2:
        return SeriesGuess(reason="aucun nom commun dans les noms de fichiers")
    return SeriesGuess(name, confidence, reason)


@dataclass
class VolumeProposal:
    number: int | None
    confidence: str
    reason: str
    warnings: list[str]


def propose_volumes(names: list[str], metadata: list[dict]) -> list[VolumeProposal]:
    """One proposal per file: batch consensus, then the file name, then EPUB metadata, then nothing."""
    batch = batch_volumes(names)
    proposals = []
    for index, name in enumerate(names):
        own = volume_from_name(name)
        meta = metadata[index] if index < len(metadata) else {}
        declared = meta.get("series_index")
        warnings: list[str] = []
        consensus = batch[index]
        if consensus.value is not None:
            if own.value is not None and own.value != consensus.value:
                warnings.append(
                    f"Le nom du fichier indique aussi le volume {as_int(own.value)} ; le numéro retenu vient du lot."
                )
            chosen = VolumeProposal(as_int(consensus.value), HIGH, consensus.reason, warnings)
        elif own.value is not None:
            chosen = VolumeProposal(as_int(own.value), MEDIUM, own.reason, warnings)
        elif declared is not None and as_int(declared):
            chosen = VolumeProposal(as_int(declared), MEDIUM, "métadonnées de série de l’EPUB", warnings)
        else:
            chosen = VolumeProposal(None, LOW, "aucun numéro de volume trouvé", warnings)
        if declared is not None and chosen.number is not None and as_int(declared) not in (None, chosen.number):
            chosen.warnings.append(f"Les métadonnées de l’EPUB indiquent le volume {as_int(declared)}.")
        proposals.append(chosen)
    return proposals


def chapter_from_name(name: str) -> Guess:
    text = stem(name)
    keyword = CHAPTER_KEYWORD.search(text)
    if keyword:
        return Guess(_number(keyword.group(1)), HIGH, "mot-clé de chapitre dans le nom du fichier")
    leading = LEADING_NUMBER.match(text)
    if leading:
        return Guess(_number(leading.group(1)), HIGH, "numéro au début du nom du fichier")
    return Guess()


def propose_chapters(names: list[str]) -> list[Guess]:
    """Chapter numbers from each name, or from the part that varies across the batch."""
    own = [chapter_from_name(name) for name in names]
    stems = [stem(name) for name in names]
    if len(stems) >= 2 and any(guess.value is None for guess in own):
        _, _, parts = _batch_variable_parts(stems)
        trailing = []
        for part in parts:
            match = re.search(NUMBER + r"\s*$", part.strip(SEPARATORS))
            trailing.append(_number(match.group(1)) if match else None)
        if all(value is not None for value in trailing) and len(set(trailing)) == len(trailing):
            return [
                guess if guess.value is not None else Guess(value, MEDIUM, "numéro final qui varie dans le lot")
                for guess, value in zip(own, trailing, strict=True)
            ]
    return own


def missing_numbers(numbers: list[int], start: int = 1) -> list[int]:
    present = sorted(set(numbers))
    if not present:
        return []
    return [value for value in range(min(start, present[0]), present[-1]) if value not in set(present)]
