"""Cliente HTTP de Jolpica compatible con Ergast, independiente de MySQL.

Documentación verificada: https://github.com/jolpica/jolpica-f1/tree/main/docs
"""

from collections import deque
from copy import deepcopy
from email.utils import parsedate_to_datetime
import math
from threading import Lock
import time
from typing import Any
from urllib.parse import urlsplit

import requests


DEFAULT_BASE_URL = "https://api.jolpi.ca/ergast/f1"
USER_AGENT = "F1-Historical-Database/0.1.0"
_TEMPORARY_STATUS = {408, 429, 500, 502, 503, 504}


class JolpicaError(RuntimeError):
    """Error controlado del cliente; nunca incluye el cuerpo HTTP completo."""


class JolpicaHTTPError(JolpicaError):
    """Respuesta HTTP no satisfactoria."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class JolpicaRateLimitError(JolpicaHTTPError):
    """El servidor o el limitador local exigen esperar antes de continuar."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class JolpicaTimeoutError(JolpicaError):
    """La petición agotó el tiempo configurado."""


class JolpicaConnectionError(JolpicaError):
    """No se pudo completar la comunicación HTTP."""


class JolpicaResponseError(JolpicaError):
    """JSON o estructura Ergast inválidos; no es una ausencia normal de datos."""


class _RateLimiter:
    """Ventanas móviles de 4/segundo y 500/hora, incluidos los reintentos."""

    def __init__(self) -> None:
        self._requests: deque[float] = deque()
        self._not_before = 0.0
        self._lock = Lock()

    def defer(self, delay: float) -> None:
        with self._lock:
            self._not_before = max(self._not_before, time.monotonic() + delay)

    def acquire(self, max_wait: float) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._requests and self._requests[0] <= now - 3600:
                    self._requests.popleft()
                delay = max(0.0, self._not_before - now)
                if len(self._requests) >= 4:
                    delay = max(delay, self._requests[-4] + 1.0 - now)
                if len(self._requests) >= 500:
                    delay = max(delay, self._requests[0] + 3600.0 - now)
                if delay > max_wait:
                    raise JolpicaRateLimitError(
                        "Es necesario esperar antes de consultar Jolpica; "
                        "la demora supera max_retry_delay.",
                        retry_after=delay,
                    )
                if delay <= 0:
                    self._requests.append(now)
                    return
            # Permitir que otros clientes registren un 429 mientras esperamos.
            time.sleep(delay)


# Los clientes del proceso comparten cuota y pausas del servidor.
_RATE_LIMITER = _RateLimiter()


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    """Aceptar números enteros JSON o sus cadenas Ergast, nunca booleanos."""
    if type(value) is int:
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        try:
            number = int(value)
        except ValueError:
            raise JolpicaResponseError(f"Campo {name} inválido.") from None
    else:
        raise JolpicaResponseError(f"Campo {name} ausente o no entero.")
    if number < minimum:
        raise JolpicaResponseError(f"Campo {name} fuera de rango.")
    return number


def _positive_number(value: Any, name: str, *, allow_zero: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (not allow_zero and value == 0)
    ):
        raise ValueError(f"{name} debe ser un número finito {'no negativo' if allow_zero else 'positivo'}.")
    return float(value)


class JolpicaClient:
    """Consultar recursos completos con paginación, caché y reintentos limitados.

    Reutilizar una instancia dentro de ``with``. La sesión inyectada pertenece
    a quien llama; únicamente se cierra automáticamente una sesión propia.
    La caché y la Session no se diseñan para uso concurrente de una instancia.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float | tuple[float, float] = (5.0, 30.0),
        page_size: int = 100,
        max_retries: int = 2,
        backoff_factor: float = 1.0,
        max_retry_delay: float = 60.0,
        cache_ttl: float = 300.0,
        session: requests.Session | None = None,
    ) -> None:
        if not isinstance(base_url, str):
            raise ValueError("base_url debe ser una URL HTTP o HTTPS absoluta.")
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.netloc
            or parts.query
            or parts.fragment
            or parts.username is not None
            or parts.password is not None
            or any(char.isspace() for char in base_url)
        ):
            raise ValueError("base_url debe ser una URL HTTP o HTTPS sin credenciales ni parámetros.")
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("page_size debe ser un entero entre 1 y 100.")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries debe ser un entero entre 0 y 10.")
        if isinstance(timeout, tuple):
            if len(timeout) != 2:
                raise ValueError("timeout debe ser un número o una pareja (conexión, lectura).")
            self.timeout = tuple(_positive_number(part, "timeout") for part in timeout)
        else:
            self.timeout = _positive_number(timeout, "timeout")
        self.backoff_factor = _positive_number(backoff_factor, "backoff_factor")
        self.max_retry_delay = _positive_number(max_retry_delay, "max_retry_delay")
        self.cache_ttl = _positive_number(cache_ttl, "cache_ttl", allow_zero=True)
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.max_retries = max_retries
        self._session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._closed = False
        self._rate_limiter = _RATE_LIMITER
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def __enter__(self) -> "JolpicaClient":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Liberar una sesión propia; es seguro llamar varias veces."""
        if not self._closed:
            self._closed = True
            self.clear_cache()
            if self._owns_session:
                self._session.close()

    def clear_cache(self) -> None:
        """Descartar respuestas completas almacenadas en memoria."""
        self._cache.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise JolpicaError("El cliente Jolpica está cerrado.")

    @staticmethod
    def _season(season: int) -> int:
        if type(season) is not int or not 1950 <= season <= 9999:
            raise ValueError("season debe ser un año entero entre 1950 y 9999.")
        return season

    def get_seasons(self) -> list[dict[str, Any]]:
        """Obtener temporadas disponibles, sin rellenar atributos ausentes."""
        return self._fetch_all("seasons.json", "SeasonTable", "Seasons")

    def get_drivers(self, season: int = 2024) -> list[dict[str, Any]]:
        """Obtener pilotos de una temporada."""
        return self._fetch_all(f"{self._season(season)}/drivers.json", "DriverTable", "Drivers")

    def get_constructors(self, season: int = 2024) -> list[dict[str, Any]]:
        """Obtener constructores de una temporada."""
        return self._fetch_all(f"{self._season(season)}/constructors.json", "ConstructorTable", "Constructors")

    def get_circuits(self, season: int = 2024) -> list[dict[str, Any]]:
        """Obtener circuitos utilizados en una temporada."""
        return self._fetch_all(f"{self._season(season)}/circuits.json", "CircuitTable", "Circuits")

    def get_race_schedule(self, season: int = 2024) -> list[dict[str, Any]]:
        """Obtener calendario con las sesiones que realmente devuelva la API."""
        return self._fetch_all(f"{self._season(season)}/races.json", "RaceTable", "Races")

    def get_race_results(
        self, season: int = 2024, round_number: int | None = None
    ) -> list[dict[str, Any]]:
        """Obtener carreras con Results completos, para una ronda o toda la temporada."""
        year = self._season(season)
        if round_number is not None and (type(round_number) is not int or round_number < 1):
            raise ValueError("round_number debe ser un entero positivo.")
        route = str(year) if round_number is None else f"{year}/{round_number}"
        return self._fetch_all(f"{route}/results.json", "RaceTable", "Races", results=True)

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            seconds = float(value)
            return seconds if math.isfinite(seconds) and seconds >= 0 else None
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    return None
                return max(0.0, date.timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                return None

    def _request_json(self, path: str, offset: int) -> Any:
        for attempt in range(self.max_retries + 1):
            self._rate_limiter.acquire(self.max_retry_delay)
            try:
                response = self._session.get(
                    f"{self.base_url}/{path}",
                    params={"limit": self.page_size, "offset": offset},
                    timeout=self.timeout,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    allow_redirects=False,
                )
            except requests.exceptions.SSLError:
                raise JolpicaConnectionError("No se pudo verificar la conexión TLS con Jolpica.") from None
            except (requests.Timeout, requests.ConnectionError) as error:
                if isinstance(error, requests.Timeout):
                    failure = JolpicaTimeoutError("Se agotó el tiempo de espera al consultar Jolpica.")
                else:
                    failure = JolpicaConnectionError("No se pudo conectar con Jolpica.")
                if attempt == self.max_retries:
                    raise failure from None
                delay = self.backoff_factor * 2**attempt
                if delay > self.max_retry_delay:
                    raise failure from None
                self._rate_limiter.defer(delay)
                continue
            except requests.RequestException:
                raise JolpicaConnectionError("No se pudo completar la petición a Jolpica.") from None

            try:
                status = response.status_code
                if status == 200:
                    try:
                        return response.json()
                    except ValueError:
                        raise JolpicaResponseError("Jolpica devolvió JSON inválido.") from None

                retry_after = self._retry_after(response.headers.get("Retry-After"))
                if status == 429:
                    failure = JolpicaRateLimitError(
                        "Jolpica respondió HTTP 429: límite de solicitudes alcanzado.",
                        retry_after=retry_after,
                    )
                else:
                    failure = JolpicaHTTPError(f"Jolpica respondió HTTP {status}.", status)
                if status not in _TEMPORARY_STATUS:
                    raise failure
                delay = max(self.backoff_factor * 2**attempt, retry_after or 0.0)
                self._rate_limiter.defer(delay)
                if attempt == self.max_retries or delay > self.max_retry_delay:
                    raise failure
            finally:
                response.close()
        raise AssertionError("Bucle de reintentos finalizado sin resultado.")

    @staticmethod
    def _objects(value: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise JolpicaResponseError(f"{label} debe ser una lista de objetos JSON.")
        return value

    @staticmethod
    def _validate_objects(items: list[dict[str, Any]]) -> None:
        """Validar contenedores conocidos sin exigir métricas históricas opcionales."""
        for item in items:
            for key in (
                "Driver", "Constructor", "Circuit", "Location", "Time", "FastestLap",
                "FirstPractice", "SecondPractice", "ThirdPractice", "Qualifying",
                "Sprint", "SprintQualifying", "SprintShootout", "AverageSpeed",
            ):
                nested = item.get(key)
                if nested is not None:
                    if not isinstance(nested, dict):
                        raise JolpicaResponseError(f"{key} debe ser un objeto JSON o null.")
                    JolpicaClient._validate_objects([nested])

    def _fetch_all(
        self, path: str, table_name: str, list_name: str, *, results: bool = False
    ) -> list[dict[str, Any]]:
        self._ensure_open()
        cached = self._cache.get(path)
        if cached is not None and time.monotonic() - cached[0] < self.cache_ttl:
            return deepcopy(cached[1])
        # No conservar una entrada caducada si la nueva petición falla.
        self._cache.pop(path, None)
        collected: list[dict[str, Any]] = []
        races: dict[tuple[int, int], dict[str, Any]] = {}
        offset = 0
        total = None
        while True:
            payload = self._request_json(path, offset)
            if not isinstance(payload, dict) or not isinstance(payload.get("MRData"), dict):
                raise JolpicaResponseError("Falta el objeto MRData en la respuesta.")
            metadata = payload["MRData"]
            limit = _integer(metadata.get("limit"), "MRData.limit", 1)
            page_offset = _integer(metadata.get("offset"), "MRData.offset")
            page_total = _integer(metadata.get("total"), "MRData.total")
            if limit > 100 or page_offset != offset:
                raise JolpicaResponseError("La paginación devuelta por Jolpica no coincide con la petición.")
            if total is not None and page_total != total:
                raise JolpicaResponseError("El total cambió durante la paginación; vuelve a consultar.")
            total = page_total
            table = metadata.get(table_name)
            if not isinstance(table, dict):
                raise JolpicaResponseError(f"Falta el objeto {table_name} en MRData.")
            items = self._objects(table.get(list_name), f"{table_name}.{list_name}")
            self._validate_objects(items)

            count = 0
            if results:
                for race in items:
                    year = _integer(race.get("season"), "Races.season", 1)
                    round_number = _integer(race.get("round"), "Races.round", 1)
                    rows = self._objects(race.get("Results"), "Races.Results")
                    self._validate_objects(rows)
                    if not rows:
                        raise JolpicaResponseError("Una carrera de resultados no contiene Results.")
                    count += len(rows)
                    key = (year, round_number)
                    if key in races:
                        existing = races[key]
                        if {k: v for k, v in existing.items() if k != "Results"} != {
                            k: v for k, v in race.items() if k != "Results"
                        }:
                            raise JolpicaResponseError("Los datos de una carrera cambiaron entre páginas.")
                        existing["Results"].extend(deepcopy(rows))
                    else:
                        races[key] = deepcopy(race)
                        collected.append(races[key])
            else:
                count = len(items)
                collected.extend(deepcopy(items))

            if count > limit or offset + count > total:
                raise JolpicaResponseError("La cantidad de datos no coincide con la paginación.")
            if offset + count == total:
                break
            if count == 0:
                raise JolpicaResponseError("La paginación no avanza; faltan datos anunciados por Jolpica.")
            offset += count

        if self.cache_ttl > 0:
            self._cache[path] = (time.monotonic(), deepcopy(collected))
        return collected
