"""
Vietnamese text normalization for TTS.

Optional integrations such as sea_g2p are used when installed. The built-in
fallback handles common cases that make TTS sound bad: dates, slashes, and
plain numbers.
"""
import re


try:
    import sea_g2p  # type: ignore
except Exception:
    sea_g2p = None


_DIGIT_WORDS = {
    "0": "khong",
    "1": "mot",
    "2": "hai",
    "3": "ba",
    "4": "bon",
    "5": "nam",
    "6": "sau",
    "7": "bay",
    "8": "tam",
    "9": "chin",
}


def _spell_digits(value: str) -> str:
    return " ".join(_DIGIT_WORDS.get(ch, ch) for ch in value)


def _number_to_vi(num: int) -> str:
    if num < 0:
        return "am " + _number_to_vi(abs(num))
    if num < 10:
        return _DIGIT_WORDS[str(num)]
    if num < 100:
        tens, ones = divmod(num, 10)
        if tens == 1:
            prefix = "muoi"
        else:
            prefix = _DIGIT_WORDS[str(tens)] + " muoi"
        if ones == 0:
            return prefix
        if ones == 1 and tens > 1:
            return prefix + " mot"
        if ones == 5:
            return prefix + " lam"
        return prefix + " " + _DIGIT_WORDS[str(ones)]
    if num < 1000:
        hundreds, rest = divmod(num, 100)
        prefix = _DIGIT_WORDS[str(hundreds)] + " tram"
        if rest == 0:
            return prefix
        if rest < 10:
            return prefix + " linh " + _number_to_vi(rest)
        return prefix + " " + _number_to_vi(rest)
    if num < 1000000:
        thousands, rest = divmod(num, 1000)
        prefix = _number_to_vi(thousands) + " nghin"
        if rest == 0:
            return prefix
        return prefix + " " + _number_to_vi(rest)
    return _spell_digits(str(num))


def _normalize_dates(text: str) -> str:
    def repl(match):
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))
        return f"ngay {_number_to_vi(day)} thang {_number_to_vi(month)} nam {_number_to_vi(year)}"

    return re.sub(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", repl, text)


def _normalize_numbers(text: str) -> str:
    def repl(match):
        value = match.group(0)
        if len(value) > 6:
            return _spell_digits(value)
        return _number_to_vi(int(value))

    return re.sub(r"\b\d+\b", repl, text)


def normalize_for_tts(text: str, lang: str = "vi") -> str:
    if not text:
        return ""
    normalized = str(text)
    if lang.startswith("vi"):
        normalized = _normalize_dates(normalized)
        normalized = normalized.replace("/", " tren ")
        normalized = _normalize_numbers(normalized)

    if sea_g2p is not None:
        try:
            if hasattr(sea_g2p, "g2p"):
                normalized = sea_g2p.g2p(normalized)
            elif hasattr(sea_g2p, "transliterate"):
                normalized = sea_g2p.transliterate(normalized)
        except Exception:
            pass
    return re.sub(r"\s+", " ", normalized).strip()
