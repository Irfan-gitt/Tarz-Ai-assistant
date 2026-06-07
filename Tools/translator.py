from deep_translator import GoogleTranslator

# lang other than eng only print on terminal , with audio in future updates

LANGUAGES = {
    "arabic":     "ar", "chinese":  "zh-CN", "english": "en",
    "french":     "fr", "german":   "de",    "hindi":   "hi",
    "japanese":   "ja", "kannada":  "kn",    "korean":  "ko",
    "malayalam":  "ml", "portuguese": "pt",   "russian": "ru",
    "spanish":    "es", "tamil":    "ta",    "telugu":  "te",
    "urdu":       "ur", "arabic":   "ar"
}


def run_translate(text: str, target_lang: str, source_lang: str = "auto") -> str:
    try:

        target = LANGUAGES.get(target_lang.lower(), target_lang.lower())
        source = LANGUAGES.get(source_lang.lower(), source_lang.lower())

        result = GoogleTranslator(source=source, target=target).translate(text)
        return f"Translation ({target_lang}): {result}"

    except Exception as e:
        return f"Translation failed: {e}"
