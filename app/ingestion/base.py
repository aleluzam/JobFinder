"""Clase base de los conectores de ingesta por fuente.

Define la interfaz común que deben cumplir los clientes de las fuentes
externas (Adzuna, Jooble, InfoJobs...) para que el worker de ingesta pueda
usarlos de forma intercambiable sin conocer los detalles de cada API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.schemas.job import RawJob

__all__ = ["BaseJobClient"]


class BaseJobClient(ABC):
    """Interfaz base de un conector de ofertas de empleo.

    Cada subclase declara su ``source`` y traduce las respuestas de su API a
    :class:`RawJob`. El ciclo de vida del cliente HTTP es gestionado por esta
    clase: se puede inyectar un ``httpx.AsyncClient`` existente (p. ej. uno
    compartido con rate limiting) o dejar que la instancia cree el suyo.
    """

    #: Identificador de la fuente, debe coincidir con la columna ``jobs.source``.
    source: str = ""

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "BaseJobClient":
        self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        """Cliente HTTP subyacente, creado bajo demanda."""
        return self._ensure_client()

    async def aclose(self) -> None:
        """Cierra el cliente HTTP si fue creado por esta instancia."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    @abstractmethod
    async def fetch_jobs(
        self, category: str, province: str, page: int = 1
    ) -> list[RawJob]:
        """Descarga una página de ofertas de la fuente externa.

        Args:
            category: Categoría o sector a buscar (ej. ``"informatica"``).
            province: Provincia española de búsqueda (ej. ``"Madrid"``).
            page: Página a recuperar, empezando en 1.

        Returns:
            Ofertas crudas de la página solicitada. Puede estar vacía si la
            fuente no tiene más resultados.

        Raises:
            httpx.HTTPStatusError: Error definitivo de la fuente (tras agotar
                los reintentos).
            httpx.TimeoutException: La fuente no respondió a tiempo.
        """
        raise NotImplementedError
