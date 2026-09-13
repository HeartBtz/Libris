"""Explicitly synthetic OpenAI-compatible endpoint, used only by the Compose smoke test."""

import asyncio
import json
import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.schemas import (
    AskResult,
    BookBible,
    BookOverview,
    ChapterAnalysis,
    ContextNeeds,
    ReviewResult,
    TranslationResult,
)

app = FastAPI()
controls = {"available": True, "delay": 2.0}


@app.post("/control")
def control(body: dict):
    controls["available"] = bool(body.get("available", True))
    controls["delay"] = min(60, max(0, float(body.get("delay", 2))))
    return controls


@app.get("/v1/models")
def models():
    return {"data": [{"id": "synthetic-literary-test"}]}


@app.post("/v1/chat/completions")
async def complete(body: dict):
    if not controls["available"]:
        return JSONResponse({"error": "Synthetic service outage"}, status_code=503)
    name = body.get("response_format", {}).get("json_schema", {}).get("name", "TranslationResult")
    combined = "\n".join(m["content"] for m in body["messages"])
    if name == "ChapterAnalysis":
        await asyncio.sleep(0.2)
        result = ChapterAnalysis(
            summary="Alice et Bob explorent une tour. Le pendentif est un souvenir familial.",
            characters=[
                {"canonical_name": "Alice", "gender": "female", "pronouns": "she/her"},
                {"canonical_name": "Bob", "gender": "male", "pronouns": "he/him"},
            ],
        )
    elif name in {"BookBible", "BookOverview"}:
        result = (BookOverview if name == "BookOverview" else BookBible)(
            title="La Tour d’argent",
            genre=["Fantasy"],
            tone="Contemplatif",
            summary="Alice et Bob cherchent l’origine d’un pendentif dans une tour ancienne.",
            narrative_style="Troisième personne, narration sobre.",
            tense="Passé",
            translation_guidelines=["Préserver le mystère entourant le pendentif."],
        )
    elif name == "ReviewResult":
        result = ReviewResult()
    elif name == "ContextNeeds":
        result = ContextNeeds(needs=["Earlier ownership of the pendant"])
    elif name == "AskResult":
        result = AskResult(
            answer="Réponse synthétique de test : le contexte présente le pendentif comme un souvenir familial."
        )
    else:
        await asyncio.sleep(controls["delay"])
        found = re.search(r"<TARGET_TEXT>\n(.*?)\n</TARGET_TEXT>", combined, re.S)
        target = json.loads(found[1])
        replacements = {
            "The Silver Tower": "La Tour d’argent",
            "Chapter": "Chapitre",
            "One": "Un",
            "Two": "Deux",
            "Three": "Trois",
            "Navigation": "Sommaire",
            "Table of Contents": "Table des matières",
            "Alice entered the ": "Alice entra dans la ",
            "Silver Tower": "Tour d’argent",
            " and stopped.": " et s’arrêta.",
            "The pendant shone in the moonlight.": "Le pendentif brillait au clair de lune.",
            "She remembered his promise.": "Elle se souvint de sa promesse.",
            "Bob watched the doorway in silence.": "Bob observait la porte en silence.",
            "The wind carried a familiar melody.": "Le vent portait une mélodie familière.",
        }
        units = []
        for unit in target:
            value = unit["text"]
            for source, translated in replacements.items():
                value = value.replace(source, translated)
            units.append({"id": unit["id"], "text": value})
        result = TranslationResult(units=units)
    return {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": result.model_dump_json()}}
        ],
        "usage": {"prompt_tokens": 450, "completion_tokens": 180},
    }
