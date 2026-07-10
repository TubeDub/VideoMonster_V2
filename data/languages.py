LANGUAGES = {
    "Русский": "ru",
    "English": "en",
    "Украинский": "uk",
    "Deutsch": "de",
    "Français": "fr",
    "Español": "es",
    "Italiano": "it",
    "Português": "pt",
    "Polski": "pl",
    "Türkçe": "tr",
    "Čeština": "cs",
    "Nederlands": "nl",
    "Română": "ro",
    "Български": "bg",
    "Ελληνικά": "el",
    "中文": "zh-CN",
    "日本語": "ja",
    "한국어": "ko",
    "हिन्दी": "hi",
    "العربية": "ar",
}

LANG_CODE_TO_NAME = {v: k for k, v in LANGUAGES.items()}

VOICES = {
    "ru": [
        {"id": "ru-RU-DmitryNeural", "name": "Дмитрий (муж.)"},
        {"id": "ru-RU-SvetlanaNeural", "name": "Светлана (жен.)"},
    ],
    "en": [
        {"id": "en-US-JennyNeural", "name": "Jenny US (жен.)"},
        {"id": "en-US-GuyNeural", "name": "Guy US (муж.)"},
        {"id": "en-GB-SoniaNeural", "name": "Sonia GB (жен.)"},
        {"id": "en-GB-RyanNeural", "name": "Ryan GB (муж.)"},
    ],
    "de": [
        {"id": "de-DE-KatjaNeural", "name": "Katja (жен.)"},
        {"id": "de-DE-ConradNeural", "name": "Conrad (муж.)"},
    ],
    "fr": [
        {"id": "fr-FR-DeniseNeural", "name": "Denise (жен.)"},
        {"id": "fr-FR-HenriNeural", "name": "Henri (муж.)"},
    ],
    "es": [
        {"id": "es-ES-ElviraNeural", "name": "Elvira (жен.)"},
        {"id": "es-ES-AlvaroNeural", "name": "Álvaro (муж.)"},
    ],
    "uk": [
        {"id": "uk-UA-PolinaNeural", "name": "Поліна (жен.)"},
        {"id": "uk-UA-OstapNeural", "name": "Остап (муж.)"},
    ],
    "zh-CN": [
        {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓 (жен.)"},
        {"id": "zh-CN-YunxiNeural", "name": "云希 (муж.)"},
    ],
    "ja": [
        {"id": "ja-JP-NanamiNeural", "name": "Nanami (жен.)"},
        {"id": "ja-JP-KeitaNeural", "name": "Keita (муж.)"},
    ],
    "it": [
        {"id": "it-IT-ElsaNeural", "name": "Elsa (жен.)"},
        {"id": "it-IT-DiegoNeural", "name": "Diego (муж.)"},
    ],
    "pt": [
        {"id": "pt-BR-FranciscaNeural", "name": "Francisca (жен.)"},
        {"id": "pt-BR-AntonioNeural", "name": "Antônio (муж.)"},
    ],
    "tr": [
        {"id": "tr-TR-EmelNeural", "name": "Emel (жен.)"},
        {"id": "tr-TR-AhmetNeural", "name": "Ahmet (муж.)"},
    ],
    "pl": [
        {"id": "pl-PL-ZofiaNeural", "name": "Zofia (жен.)"},
        {"id": "pl-PL-MarekNeural", "name": "Marek (муж.)"},
    ],
    "ko": [
        {"id": "ko-KR-SunHiNeural", "name": "SunHi (жен.)"},
        {"id": "ko-KR-InJoonNeural", "name": "InJoon (муж.)"},
    ],
    "ar": [
        {"id": "ar-SA-ZariyahNeural", "name": "Zariyah (жен.)"},
        {"id": "ar-SA-HamedNeural", "name": "Hamed (муж.)"},
    ],
    "cs": [
        {"id": "cs-CZ-VlastaNeural", "name": "Vlasta (жен.)"},
        {"id": "cs-CZ-AntoninNeural", "name": "Antonin (муж.)"},
    ],
    "nl": [
        {"id": "nl-NL-FennaNeural", "name": "Fenna (жен.)"},
        {"id": "nl-NL-MaartenNeural", "name": "Maarten (муж.)"},
    ],
    "hi": [
        {"id": "hi-IN-SwaraNeural", "name": "Swara (жен.)"},
        {"id": "hi-IN-MadhurNeural", "name": "Madhur (муж.)"},
    ],
}
VOICES = VOICES
DEFAULT_VOICE = "ru-RU-DmitryNeural"
DEFAULT_LANG = "ru"
