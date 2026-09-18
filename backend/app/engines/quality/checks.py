import re
import unicodedata

from app.engines.context.builder import mentioned
from app.engines.epub.text import plain, validate_codes
from app.schemas import TranslationResult


def validate_translation(units: list[dict], result: TranslationResult) -> None:
    if [u["id"] for u in units] != [u.id for u in result.units]:
        raise ValueError("Paragraphes manquants, ajoutés ou désordonnés.")
    for source, translation in zip(units, result.units, strict=True):
        if plain(source["text"]).strip() and not plain(translation.text).strip():
            raise ValueError("Traduction vide.")
        if re.search(r"<(?:think|analysis|reasoning)(?:>|\s)", translation.text, re.I) and not re.search(
            r"<(?:think|analysis|reasoning)(?:>|\s)", source["text"], re.I
        ):
            raise ValueError("Bloc de raisonnement détecté dans la traduction.")
        validate_codes(source["text"], translation.text)


ARTICLES = {
    "le", "la", "les", "un", "une", "des", "du", "de", "au", "aux",
    "the", "a", "an", "el", "los", "las", "il", "lo", "gli", "der", "die", "das",
}  # fmt: skip
CJK = re.compile(r"[㐀-鿿぀-ヿ]")
APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'", "`": "'", "´": "'"})


def fold(text: str) -> str:
    # Typography is not meaning: curly apostrophes, no-break spaces, case and accents (capitals are
    # often left unaccented) must not turn a respected locked term into a violation.
    text = unicodedata.normalize("NFKC", text).translate(APOSTROPHES)
    text = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).casefold().strip()


def source_mentions(term: str, source: str) -> bool:
    # A capitalised term is a name: "Will" must not be triggered by the verb "will".
    if term != term.lower() and not CJK.search(term):
        return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", source))
    return mentioned(term, source)


def locked_term_respected(translation: str, target: str) -> bool:
    expected, text = fold(translation), fold(target)
    if not expected:
        return True
    if CJK.search(expected):
        return expected in text
    words = expected.split(" ")
    # The leading article follows the sentence: "le Conseil" becomes "du Conseil", "l'Épée" "d'Épée".
    if len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    elif re.match(r"[ld]'.", words[0]):
        words[0] = words[0][2:]
    pattern = r"\s+".join(re.escape(word) + r"(?:e?s|x)?" for word in words)
    return bool(re.search(r"(?<!\w)" + pattern + r"(?!\w)", text))


def locked_term_error(findings: list[dict]) -> str | None:
    missing = [i["message"].split(" : ", 1)[-1] for i in findings if i["code"] == "locked_term"]
    if not missing:
        return None
    return "Glossaire verrouillé non respecté. " + " ".join(dict.fromkeys(missing))


def checks(
    units: list[dict], translations: list[dict], glossary: list, source_lang: str, target_lang: str
) -> list[dict]:
    issues = []
    for original, translated in zip(units, translations, strict=True):
        source, target = plain(original["text"]).strip(), plain(translated["text"]).strip()

        def add(code: str, message: str, severity: str = "warning"):
            issues.append({"code": code, "message": f"{original['id']} : {message}", "severity": severity})

        if source_lang != target_lang and source == target and len(source.split()) > 5:
            add("unchanged", "Texte identique à la source ; vérifier la langue.")
        ratio = len(target) / max(1, len(source))
        if len(source) > 80 and not 0.25 <= ratio <= 3.5:
            add("length", f"Ratio de longueur inhabituel ({ratio:.2f}).")
        if re.search(r"(.{20,}?)\1\1", target) and not re.search(r"(.{20,}?)\1\1", source):
            add("repetition", "Répétition anormale probable.")
        for term in glossary:
            if (
                term.locked
                and source_mentions(term.source, source)
                and not locked_term_respected(term.translation, target)
            ):
                add(
                    "locked_term",
                    f"Traduction verrouillée absente : {term.source} → {term.translation}.",
                    "error",
                )
    return issues
