import pytest

from app.engines.quality.checks import checks, locked_term_error
from app.models import Glossary


def findings(source: str, target: str, term_source: str, translation: str) -> list[dict]:
    term = Glossary(source=term_source, translation=translation, locked=True)
    found = checks([{"id": "u1", "text": source}], [{"id": "u1", "text": target}], [term], "en", "fr")
    return [item for item in found if item["code"] == "locked_term"]


@pytest.mark.parametrize(
    ("source", "target", "term", "translation"),
    [
        ("She drew the Sword.", "Elle dégaina l'Épée.", "Sword", "l’Épée"),
        ("She drew the Sword.", "Elle dégaina l’Épée.", "Sword", "l'Épée"),
        ("A blow of the Sword.", "Un coup d’Épée.", "Sword", "l’Épée"),
        ("The Silver Tower rose.", "La Tour d’argent se dressait.", "Silver Tower", "Tour d’argent"),
        ("The Adventurer guild.", "La guilde des Aventuriers.", "Adventurer", "Aventurier"),
        ("Word of the Elder Council.", "La parole du Conseil des Anciens.", "Elder Council", "le Conseil des Anciens"),
        ("Talk to the Elder Council.", "Parle au Conseil des Anciens.", "Elder Council", "le Conseil des Anciens"),
        ("THE SWORD", "L'EPEE", "SWORD", "l’Épée"),
        ("The Familia gathered.", "La Familia se réunit.", "Familia", "Familia"),
    ],
)
def test_typography_and_grammar_do_not_break_a_locked_term(source, target, term, translation):
    assert findings(source, target, term, translation) == []


def test_a_name_is_not_triggered_by_its_lowercase_homograph():
    assert findings("He will come tomorrow.", "Il viendra demain.", "Will", "Wil") == []
    assert findings("Then Will came.", "Puis Guillaume arriva.", "Will", "Wil")


@pytest.mark.parametrize(
    ("target", "translation"),
    [
        ("La tour argentée se dressait.", "Tour d’argent"),
        ("La Tournée d’argent.", "Tour d’argent"),  # word boundaries still apply
        ("Le Conseil se réunit.", "le Conseil des Anciens"),
    ],
)
def test_a_real_violation_is_still_reported(target, translation):
    assert findings("The Silver Tower and the Elder Council.", target, "Silver Tower", translation)


def test_the_error_names_the_expected_terms_once():
    term = Glossary(source="Silver Tower", translation="Tour d’argent", locked=True)
    found = checks(
        [{"id": "u1", "text": "The Silver Tower."}, {"id": "u2", "text": "Silver Tower again."}],
        [{"id": "u1", "text": "La tour argentée."}, {"id": "u2", "text": "Encore la tour."}],
        [term],
        "en",
        "fr",
    )
    error = locked_term_error(found)
    assert error.startswith("Glossaire verrouillé non respecté.")
    assert error.count("Silver Tower → Tour d’argent") == 1
    assert locked_term_error([]) is None
