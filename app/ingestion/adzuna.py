"""Conector de la API de Adzuna (https://developer.adzuna.com/).

Descarte ofertas de empleo por provincia y categoría en España y las traduce a
:class:`RawJob`. Las credenciales se leen de ``ADZUNA_APP_ID`` y
``ADZUNA_APP_KEY`` salvo que se pasen explícitamente al constructor.

La petición aplica:

- Rate limiting con :class:`aiolimiter.AsyncLimiter` (1 petición/segundo por
  defecto).
- Reintentos con backoff exponencial (``tenacity``) ante errores 429/5xx,
  timeouts y fallos de red.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx
from aiolimiter import AsyncLimiter
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.ingestion.base import BaseJobClient
from app.schemas.job import RawJob

__all__ = ["AdzunaClient"]

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.adzuna.com/v1/api/jobs/es/search/1"
_MAX_ATTEMPTS = 5
_CONTRACT_TIME_TO_SCHEDULE = {
    "full_time": "completa",
    "part_time": "parcial",
}


def _should_retry(exc: BaseException) -> bool:
    """Reintenta ante 429/5xx, timeouts y errores de red; el resto, no."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


def _log_retry(retry_state: RetryCallState) -> None:
    """Registra el reintento sin exponer credenciales (van en la URL)."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP {exc.response.status_code}"
    else:
        detail = type(exc).__name__ if exc is not None else "desconocido"
    logger.warning(
        "Reintento %s/%s para %s tras %s",
        retry_state.attempt_number,
        _MAX_ATTEMPTS,
        AdzunaClient.source,
        detail,
    )


class AdzunaClient(BaseJobClient):
    """Cliente asíncrono de la búsqueda de empleo de Adzuna para España."""

    source = "adzuna"

    def __init__(
        self,
        *,
        app_id: Optional[str] = None,
        app_key: Optional[str] = None,
        results_per_page: int = 50,
        rate_limit: float = 1.0,
        timeout: float = 15.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(timeout=timeout, client=client)
        self._app_id = app_id or os.getenv("ADZUNA_APP_ID", "")
        self._app_key = app_key or os.getenv("ADZUNA_APP_KEY", "")
        if not self._app_id or not self._app_key:
            raise ValueError(
                "faltan credenciales de Adzuna: define ADZUNA_APP_ID y "
                "ADZUNA_APP_KEY (o pásalas al constructor)"
            )
        if not 1 <= results_per_page <= 100:
            raise ValueError("results_per_page debe estar entre 1 y 100")
        self._results_per_page = results_per_page
        self._limiter = AsyncLimiter(rate_limit, time_period=1)

    async def fetch_jobs(
        self, category: str, province: str, page: int = 1
    ) -> list[RawJob]:
        """Descarga una página de ofertas de Adzuna para una provincia."""
        if page < 1:
            raise ValueError("page debe ser mayor o igual a 1")
        params = {
            "app_id": self._app_id,
            "app_key": self._app_key,
            "what": category,
            "where": province,
            "results_per_page": self._results_per_page,
            "page": page,
        }
        payload = await self._get_json(params)
        results = payload.get("results") or []
        return [self._to_raw_job(item, province) for item in results]

    async def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        """GET con rate limiting y reintentos con backoff exponencial."""
        async for attempt in AsyncRetrying(
            wait=wait_exponential(multiplier=0.5, max=30),
            stop=stop_after_attempt(_MAX_ATTEMPTS),
            retry=retry_if_exception(_should_retry),
            before_sleep=_log_retry,
            reraise=True,
        ):
            with attempt:
                async with self._limiter:
                    response = await self.client.get(_BASE_URL, params=params)
                response.raise_for_status()
                return response.json()
        return {}

    def _to_raw_job(self, item: dict[str, Any], province: str) -> RawJob:
        company = item.get("company") or {}
        location = item.get("location") or {}
        contract_time = item.get("contract_time")
        return RawJob(
            source=self.source,
            source_id=item.get("id"),
            title=item.get("title"),
            description=item.get("description"),
            original_url=item.get("redirect_url"),
            company=company.get("display_name"),
            province=location.get("display_name") or province,
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            work_schedule=_CONTRACT_TIME_TO_SCHEDULE.get(
                contract_time, contract_time
            )
            if contract_time
            else None,
            published_at=item.get("created"),
        )
