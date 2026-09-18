import pytest
from fastapi.testclient import TestClient
from lxml import etree
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.memory.glossary_files import read_glossary
from app.main import app
from app.models import Glossary, Project

PASSWORD = "test-password-123456789"
TERMS = [
    {"source": "Silver Tower", "translation": "Tour d’argent", "category": "lieu",
     "description": "Toujours avec l’article ; « tour » au féminin.", "locked": True, "accepted": True},
    {"source": "pendant", "translation": "pendentif", "category": "objet", "description": "",
     "locked": False, "accepted": False},
    {"source": "-kun", "translation": "-kun", "category": "honorifique", "description": "=garder tel quel",
     "locked": False, "accepted": True},
    {"source": "Bob; \"the elder\", jr", "translation": "Bob, « l’aîné »; fils", "category": "personnage",
     "description": "Deux lignes\net des séparateurs ; , \t fin", "locked": True, "accepted": False},
]  # fmt: skip


def terms(pid: str) -> list[dict]:
    with SessionLocal() as db:
        return [
            {key: getattr(term, key) for key in TERMS[0]}
            for term in db.scalars(select(Glossary).where(Glossary.project_id == pid).order_by(Glossary.source))
        ]


def reset(pid: str, values: list[dict]) -> None:
    with SessionLocal() as db:
        for term in db.scalars(select(Glossary).where(Glossary.project_id == pid)):
            db.delete(term)
        for value in values:
            db.add(Glossary(project_id=pid, **value))
        db.commit()


def imported(client, pid: str, name: str, content: bytes) -> dict:
    response = client.post(f"/api/projects/{pid}/glossary/import", files={"file": (name, content)})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("format", ["json", "csv", "tbx"])
def test_glossary_round_trip(seeded, format):
    pid = seeded[0]
    reset(pid, TERMS)
    expected = terms(pid)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD})
        exported = client.get(f"/api/projects/{pid}/glossary/export/{format}")
        assert exported.status_code == 200
        assert f'filename="glossary.{format}"' in exported.headers["content-disposition"]
        reset(pid, [])
        # Content detection: the file name does not tell the format.
        assert imported(client, pid, "export", exported.content) == {"imported": 4, "skipped": 0}
    restored = terms(pid)
    if format == "tbx":
        # TBX has one administrative status per term: a locked proposal comes back as a proposal.
        locked_proposal = next(term for term in expected if term["locked"] and not term["accepted"])
        locked_proposal["locked"] = False
    assert restored == expected


def test_tbx_export_is_a_tbx_basic_document(seeded):
    pid = seeded[0]
    reset(pid, TERMS[:2])
    with SessionLocal() as db:
        db.get(Project, pid).target_language = "fr-FR"
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD})
        content = client.get(f"/api/projects/{pid}/glossary/export/tbx").content
    root = etree.fromstring(content)
    namespace = {"t": "urn:iso:std:iso:30042:ed-2"}
    assert root.tag == "{urn:iso:std:iso:30042:ed-2}tbx" and root.get("type") == "TBX-Basic"
    entries = root.findall(".//t:conceptEntry", namespace)
    assert len(entries) == 2
    languages = [
        s.get("{http://www.w3.org/XML/1998/namespace}lang") for s in entries[0].findall("t:langSec", namespace)
    ]
    assert languages == ["en", "fr-FR"]
    statuses = [n.text for n in root.findall(".//t:termNote[@type='administrativeStatus']", namespace)]
    assert statuses == ["preferredTerm-admn-sts", "deprecatedTerm-admn-sts"]  # sorted by source


TBX_V2 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE martif SYSTEM "TBXBasiccoreStructV02.dtd">
<martif type="TBX-Basic" xml:lang="en-US">
 <martifHeader><fileDesc><sourceDesc><p>Other tool</p></sourceDesc></fileDesc></martifHeader>
 <text><body>
  <termEntry id="1">
   <descrip type="subjectField">magic</descrip>
   <langSet xml:lang="de"><tig><term>Zauberstab</term></tig></langSet>
   <langSet xml:lang="fr-CA"><tig><term>baguette</term>
     <termNote type="administrativeStatus">preferredTerm-admn-sts</termNote></tig></langSet>
   <langSet xml:lang="en-GB"><ntig><termGrp><term>wand</term></termGrp></ntig>
     <descrip type="definition">A stick for spells.</descrip></langSet>
  </termEntry>
  <termEntry id="2">
   <langSet xml:lang="en"><tig><term>orphan</term></tig></langSet>
  </termEntry>
 </body></text>
</martif>"""


def test_tbx_v2_termbases_from_other_tools_are_read():
    [term] = read_glossary(TBX_V2.encode(), "base.tbx", "en", "fr")
    assert term.model_dump() == {
        "source": "wand", "translation": "baguette", "category": "magic",
        "description": "A stick for spells.", "locked": True, "accepted": True,
    }  # fmt: skip


def test_tbx_import_refuses_entity_declarations():
    bomb = b"""<?xml version="1.0"?><!DOCTYPE tbx [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;">]>
    <tbx xmlns="urn:iso:std:iso:30042:ed-2"><text><body><conceptEntry id="c">
    <langSec xml:lang="en"><termSec><term>&b;</term></termSec></langSec>
    <langSec xml:lang="fr"><termSec><term>x</term></termSec></langSec></conceptEntry></body></text></tbx>"""
    external = b"""<?xml version="1.0"?><!DOCTYPE tbx [<!ENTITY x SYSTEM "file:///etc/passwd">]>
    <tbx><text><body/></text></tbx>"""
    for content in (bomb, external):
        with pytest.raises(ValueError, match="entités"):
            read_glossary(content, "g.tbx", "en", "fr")
    with pytest.raises(ValueError, match="TBX invalide"):
        read_glossary(b"<html><body>not a termbase</body></html>", "g.xml", "en", "fr")


@pytest.mark.parametrize(
    "content",
    [
        "﻿Terme source;Traduction;Catégorie;Commentaire;Verrouillé;Accepté\r\n"
        "Silver Tower;Tour d’argent;lieu;note;oui;oui\r\n\r\npendant;pendentif;;;non;non\r\n",
        "source,target,type,notes,locked,approved\nSilver Tower,Tour d’argent,lieu,note,TRUE,TRUE\n"
        "pendant,pendentif,,,false,FALSE\n",
        "Term\tTranslation\tCategory\tDescription\tLocked\tAccepted\nSilver Tower\tTour d’argent\tlieu\tnote\t1\t1\n"
        "pendant\tpendentif\t\t\t0\t0\n",
    ],
)
def test_csv_import_tolerates_spreadsheet_dialects(content):
    silver, pendant = read_glossary(content.encode(), "terms.csv", "en", "fr")
    assert silver.model_dump() == {
        "source": "Silver Tower", "translation": "Tour d’argent", "category": "lieu", "description": "note",
        "locked": True, "accepted": True,
    }  # fmt: skip
    assert (pendant.source, pendant.category, pendant.locked, pendant.accepted) == ("pendant", "autre", False, False)


def test_csv_import_reads_windows_encoded_and_headerless_files():
    [term] = read_glossary("Épée;épée\n".encode("cp1252"), "g.csv", "en", "fr")
    assert (term.source, term.translation, term.accepted) == ("Épée", "épée", True)
    [utf16] = read_glossary("source\ttraduction\nsword\tépée\n".encode("utf-16"), "g.txt", "en", "fr")
    assert (utf16.source, utf16.translation) == ("sword", "épée")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"source;valeur\nsword;epee\n", "colonnes source et traduction"),
        (b"source;traduction;verrouille\nsword;epee;peut-etre\n", "ligne 2"),
        (b"source;traduction\nsword;\n", "n° 1"),
        (b'{"source": "x"}', "liste de termes"),
    ],
)
def test_invalid_glossaries_are_explained(seeded, content, message):
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": PASSWORD})
        response = client.post(f"/api/projects/{seeded[0]}/glossary/import", files={"file": ("g", content)})
    assert response.status_code == 422
    assert message in response.json()["detail"]
