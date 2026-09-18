"""Language codes as people and models read them: names, direction and typographic conventions."""

NAMES = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "eo": "Esperanto",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "gl": "Galician",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "la": "Latin",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "ms": "Malay",
    "nb": "Norwegian Bokmål",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "yi": "Yiddish",
    "zh": "Chinese",
}
REGIONS = {
    "pt-br": "Brazilian Portuguese",
    "pt-pt": "European Portuguese",
    "en-us": "American English",
    "en-gb": "British English",
    "fr-ca": "Canadian French",
    "es-419": "Latin American Spanish",
    "zh-hans": "Simplified Chinese",
    "zh-cn": "Simplified Chinese",
    "zh-hant": "Traditional Chinese",
    "zh-tw": "Traditional Chinese",
}
RTL = {"ar", "arc", "ckb", "dv", "fa", "he", "iw", "ks", "ps", "sd", "syr", "ug", "ur", "yi"}

TYPOGRAPHY = {
    "fr": (
        "French typography: a no-break space (U+202F or U+00A0) before ; : ! ? and », and after «; "
        "guillemets « » for quotations and dialogue, never straight quotes; dialogue turns introduced "
        "by an em dash (—) inside or after the guillemets; typographic apostrophe ’; ellipsis …."
    ),
    "de": "German typography: „…“ quotation marks (or »…«), no space before punctuation, typographic apostrophe ’.",
    "es": (
        "Spanish typography: opening ¿ and ¡, dialogue introduced by a raya (—), quotations in « » "
        "or “ ”, no space before punctuation."
    ),
    "it": "Italian typography: dialogue in « » or introduced by a dash (—), typographic apostrophe ’.",
    "pt": "Portuguese typography: dialogue introduced by a travessão (—), quotations in « » or “ ”.",
    "ru": "Russian typography: quotations in « », dialogue introduced by an em dash (—).",
    "pl": "Polish typography: quotations in „…”, dialogue introduced by an em dash (—).",
    "ja": "Japanese typography: 「」 for dialogue, 『』 for nested quotes, full-width punctuation, no spaces between words.",
    "zh": "Chinese typography: full-width punctuation, “ ” (or 「」 in Traditional Chinese) for dialogue, no spaces between words.",
    "ko": "Korean typography: “ ” for dialogue, spacing between words as usual in Korean.",
    "en": "English typography: curly quotes “ ” and ’, em dash without spaces for interruptions.",
}


def primary(code: str) -> str:
    """`en-US`, `EN_us` and `en` all denote English: compare the primary subtag."""
    return (code or "").strip().replace("_", "-").split("-")[0].casefold()


def language_name(code: str) -> str:
    code = (code or "").strip()
    key = code.replace("_", "-").casefold()
    name = REGIONS.get(key) or NAMES.get(primary(code))
    return f"{name} ({code})" if name else code


def right_to_left(code: str) -> bool:
    return primary(code) in RTL


def typography(code: str) -> str:
    return TYPOGRAPHY.get(primary(code), "")
