import re

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
            if term.locked and mentioned(term.source, source) and not mentioned(term.translation, target):
                add(
                    "locked_term",
                    f"Traduction verrouillée absente : {term.source} → {term.translation}.",
                    "error",
                )
    return issues
