"""Conservative deterministic Portuguese text normalization for speech synthesis."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass

from num2words import num2words

TEXT_NORMALIZER_VERSION = "pt_br_text_normalizer_v1"

_PROTECTED_PATTERNS = (
    re.compile(r"`[^`]*`"),
    re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
    re.compile(r"\b[0-9a-f]{32,}\b", re.I),
    re.compile(r"(?<!\w)(?:[~/]|\./|\.\./)[^\s]+"),
    re.compile(r"\b[A-ZÁ-Ý]{2,}\s+\d+[A-Za-z-]*\b"),
    re.compile(r"\b(?:[A-Za-zÀ-ÿ]+\d|\d+[A-Za-zÀ-ÿ])[A-Za-zÀ-ÿ0-9_.-]*\b"),
    re.compile(r"\b[vV]?\d+(?:\.\d+){2,}\b"),
)

_MONTHS = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

_UNITS = {
    "km": ("quilômetro", "quilômetros"),
    "kg": ("quilograma", "quilogramas"),
    "g": ("grama", "gramas"),
    "m": ("metro", "metros"),
    "gb": ("gigabyte", "gigabytes"),
    "mb": ("megabyte", "megabytes"),
    "tb": ("terabyte", "terabytes"),
    "khz": ("quilohertz", "quilohertz"),
    "mhz": ("megahertz", "megahertz"),
    "ghz": ("gigahertz", "gigahertz"),
}


@dataclass(frozen=True)
class TextNormalizationResult:
    canonical_text: str
    normalized_text: str
    version: str
    identity: str


def _integer_words(value: int) -> str:
    return str(num2words(value, lang="pt_BR"))


def _number_words(value: str) -> str:
    normalized = value.replace(".", ",")
    if "," not in normalized:
        return _integer_words(int(normalized))
    integer, fraction = normalized.split(",", 1)
    spoken_fraction = " ".join(_integer_words(int(digit)) for digit in fraction)
    return f"{_integer_words(int(integer))} vírgula {spoken_fraction}"


class PortugueseTextNormalizer:
    def __init__(self, *, version: str = TEXT_NORMALIZER_VERSION):
        if not version.strip():
            raise ValueError("text normalizer version must not be empty")
        self.version = version
        canonical = json.dumps(
            {
                "version": version,
                "locale": "pt_BR",
                "protected_token_policy": "neovids-identifiers-v1",
                "units": _UNITS,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.identity = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def normalize(self, canonical_text: str) -> TextNormalizationResult:
        protected: list[str] = []

        def protect(match: re.Match[str]) -> str:
            protected.append(match.group(0))
            return chr(0xE000 + len(protected) - 1)

        result = canonical_text
        for pattern in _PROTECTED_PATTERNS:
            result = pattern.sub(protect, result)

        result = re.sub(
            r"(?<!\w)(\d{1,2})/(\d{1,2})/(\d{4})(?!\w)",
            self._replace_date,
            result,
        )
        result = re.sub(
            r"(?<!\w)([01]?\d|2[0-3]):([0-5]\d)(?!\d)",
            self._replace_time,
            result,
        )
        result = re.sub(
            r"(?<!\w)(R\$|US\$)\s*(\d+)(?:[,.](\d+))?(?!\w)",
            self._replace_currency,
            result,
        )
        result = re.sub(
            r"(?<!\w)(\d+(?:[,.]\d+)?)\s*%(?!\w)",
            lambda match: f"{_number_words(match.group(1))} por cento",
            result,
        )
        result = re.sub(
            r"(?<!\w)(\d+(?:[,.]\d+)?)\s*(kHz|MHz|GHz|GB|MB|TB|km|kg|g|m)\b",
            self._replace_unit,
            result,
            flags=re.IGNORECASE,
        )
        result = re.sub(
            r"(?<![\w,.])(\d+[,.]\d+)(?![\w,.])",
            lambda match: _number_words(match.group(1)),
            result,
        )
        result = re.sub(
            r"(?<!\w)(\d+)(?!\w)",
            lambda match: _integer_words(int(match.group(1))),
            result,
        )

        for index, original in enumerate(protected):
            result = result.replace(chr(0xE000 + index), original)
        return TextNormalizationResult(
            canonical_text=canonical_text,
            normalized_text=result,
            version=self.version,
            identity=self.identity,
        )

    @staticmethod
    def _replace_date(match: re.Match[str]) -> str:
        day, month, year = (int(part) for part in match.groups())
        try:
            dt.date(year, month, day)
        except ValueError:
            return match.group(0)
        return f"{_integer_words(day)} de {_MONTHS[month - 1]} de {_integer_words(year)}"

    @staticmethod
    def _replace_time(match: re.Match[str]) -> str:
        hours, minutes = (int(part) for part in match.groups())
        hour_unit = "hora" if hours == 1 else "horas"
        if minutes == 0:
            return f"{_integer_words(hours)} {hour_unit}"
        minute_unit = "minuto" if minutes == 1 else "minutos"
        return f"{_integer_words(hours)} {hour_unit} e {_integer_words(minutes)} {minute_unit}"

    @staticmethod
    def _replace_currency(match: re.Match[str]) -> str:
        symbol, integer, fraction = match.groups()
        amount = int(integer)
        if symbol == "R$":
            major = "real" if amount == 1 else "reais"
        else:
            major = "dólar americano" if amount == 1 else "dólares americanos"
        spoken = f"{_integer_words(amount)} {major}"
        if fraction and int(fraction):
            cents = int(fraction[:2].ljust(2, "0"))
            cent_unit = "centavo" if cents == 1 else "centavos"
            spoken += f" e {_integer_words(cents)} {cent_unit}"
        return spoken

    @staticmethod
    def _replace_unit(match: re.Match[str]) -> str:
        raw_number, raw_unit = match.groups()
        singular, plural = _UNITS[raw_unit.casefold()]
        unit = singular if raw_number.replace(",", ".") == "1" else plural
        return f"{_number_words(raw_number)} {unit}"
