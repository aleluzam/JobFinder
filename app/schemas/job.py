"""Esquema común de ofertas de empleo.

Define dos modelos:

- ``RawJob``: contenedor flexible y tolerante a fallos para los datos tal como
  llegan de una fuente externa (JSON parseado de Adzuna, Jooble, InfoJobs...).
  No valida nada: su objetivo es no romper la ingesta aunque la fuente cambie.
- ``NormalizedJob``: modelo estricto y saneado que representa la oferta en el
  esquema único del sistema. Todas las entidades de ingesta deben poder
  convertirse a este modelo antes de tocar la base de datos.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = ["RawJob", "NormalizedJob"]

_CURRENCY_RE = re.compile(r"[€$£\s]|EUR|USD|GBP|anuales|/año|bruto", re.IGNORECASE)
_THOUSANDS_DOT_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")
_FALSY_VALUES = {"", "0", "false", "no", "n", "f", "off", "presencial", "on_site"}
_TRUTHY_VALUES = {"1", "true", "yes", "y", "t", "on", "s", "si", "sí", "remoto", "remote"}


def _clean_optional_text(value: Any) -> Optional[str]:
    """Convierte texto vacío o sin sentido en ``None`` y recorta espacios."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "n/a", "na", "-", "no informado", "no especificado"}:
        return None
    return text


def _parse_salary(value: Any) -> Optional[float]:
    """Normaliza salarios admitidos como número o texto en formatos habituales.

    Acepta ``30000``, ``"30000"``, ``"30.000"``, ``"30.000,50"``, ``"30,000.50"``,
    ``"28.000 €"``, ``"€30000"``, etc. Devuelve ``None`` si no hay valor y lanza
    ``ValueError`` si el texto no es interpretable como importe.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("el salario no puede ser booleano")
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        text = _CURRENCY_RE.sub("", str(value)).strip()
        if not text:
            return None
        has_dot = "." in text
        has_comma = "," in text
        if has_dot and has_comma:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif has_comma:
            decimals = len(text.split(",")[-1])
            text = text.replace(",", ".") if decimals != 3 else text.replace(",", "")
        elif has_dot and not _THOUSANDS_DOT_RE.match(text):
            pass
        elif has_dot:
            text = text.replace(".", "")
        try:
            amount = float(text)
        except ValueError as exc:
            raise ValueError(f"salario no interpretable: {value!r}") from exc
    if amount < 0:
        raise ValueError("el salario no puede ser negativo")
    return amount


class RawJob(BaseModel):
    """Datos crudos de una oferta, tal como los devuelve la fuente externa.

    Tolera cualquier tipo y campos desconocidos para que un cambio inesperado
    en una API no detenga el lote de ingesta.
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    source: Any = None
    source_id: Any = None
    title: Any = None
    description: Any = None
    original_url: Any = None
    company: Any = None
    province: Any = None
    city: Any = None
    salary_min: Any = None
    salary_max: Any = None
    contract_type: Any = None
    work_schedule: Any = None
    is_remote: Any = None
    published_at: Any = None


class NormalizedJob(BaseModel):
    """Oferta validada y saneada lista para persistir en la tabla ``jobs``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=50, examples=["adzuna"])
    source_id: str = Field(min_length=1, max_length=150, examples=["42"])
    title: str = Field(min_length=1, max_length=255, examples=["Desarrollador Python"])
    description: str = Field(min_length=1, examples=["Buscamos ingeniero de software..."])
    original_url: str = Field(
        examples=["https://www.example.com/oferta/desarrollador-python"]
    )

    company: Optional[str] = Field(default=None, max_length=255)
    province: Optional[str] = Field(default=None, max_length=100)
    city: Optional[str] = Field(default=None, max_length=100)
    salary_min: Optional[float] = Field(default=None, ge=0, examples=[30000.0])
    salary_max: Optional[float] = Field(default=None, ge=0, examples=[45000.0])
    contract_type: Optional[str] = Field(default=None, max_length=50)
    work_schedule: Optional[str] = Field(default=None, max_length=50)
    is_remote: bool = False
    published_at: Optional[datetime] = None

    @field_validator("source", "source_id", "title", "description", mode="after")
    @classmethod
    def _required_text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no puede estar vacío")
        return value.strip()

    @field_validator(
        "company", "province", "city", "contract_type", "work_schedule", mode="before"
    )
    @classmethod
    def _sanitize_optional_text(cls, value: Any) -> Optional[str]:
        return _clean_optional_text(value)

    @field_validator("original_url", mode="after")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("la URL debe usar http o https")
        if not parsed.netloc:
            raise ValueError("la URL no tiene dominio")
        return value

    @field_validator("salary_min", "salary_max", mode="before")
    @classmethod
    def _coerce_salary(cls, value: Any) -> Optional[float]:
        return _parse_salary(value)

    @field_validator("is_remote", mode="before")
    @classmethod
    def _coerce_is_remote(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        if text in _TRUTHY_VALUES:
            return True
        if text in _FALSY_VALUES:
            return False
        raise ValueError(f"valor de is_remote no reconocido: {value!r}")

    @field_validator("published_at", mode="before")
    @classmethod
    def _coerce_published_at(cls, value: Any) -> Optional[datetime]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @model_validator(mode="after")
    def _check_salary_range(self) -> "NormalizedJob":
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min no puede ser mayor que salary_max")
        return self
