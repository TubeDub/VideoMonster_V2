import logging
import socket

from engines.translation import translate_text as core_translate

logger = logging.getLogger("tubedub.engines.translation_compat")


def translate_text(text, target="ru", source="en"):
    return core_translate(text, source, target)


def detect_language(text):
    if not text or not str(text).strip():
        return "en"
    try:
        from langdetect import detect

        code = detect(str(text)[:5000])
        aliases = {"zh-cn": "zh-CN", "zh-tw": "zh-TW"}
        return aliases.get(code.lower(), code)
    except Exception as e:
        logger.warning("[detect_language] failed: %s", e)
        return "en"


def has_internet(timeout: float = 3.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        socket.create_connection(("8.8.8.8", 53), timeout=timeout)
        return True
    except OSError:
        return False
