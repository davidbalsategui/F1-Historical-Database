"""Pruebas del presupuesto de solicitudes con reloj simulado y sin red."""

from dataclasses import dataclass, field
from unittest.mock import Mock

import pytest

from api import jolpica_client as jolpica


@dataclass
class FakeClock:
    """Avanzar el tiempo únicamente cuando la prueba o el cliente lo solicitan."""

    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self):
        return self.now

    def time(self):
        return 1_700_000_000.0 + self.now

    def sleep(self, delay):
        assert delay >= 0
        self.sleeps.append(delay)
        self.now += delay


@pytest.fixture
def clock(monkeypatch):
    simulated = FakeClock()
    # Sustituir la referencia local evita alterar el reloj global de pytest.
    monkeypatch.setattr(jolpica, "time", simulated)
    monkeypatch.setattr(jolpica, "_RATE_LIMITER", jolpica._RateLimiter())
    return simulated


def response(status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = {
        "MRData": {
            "limit": "100",
            "offset": "0",
            "total": "0",
            "SeasonTable": {"Seasons": []},
        }
    }
    return result


def test_quinta_solicitud_espera_hasta_terminar_la_ventana_de_un_segundo(clock):
    limiter = jolpica._RateLimiter()
    for _ in range(4):
        limiter.acquire(max_wait=60)

    assert clock.sleeps == []

    limiter.acquire(max_wait=60)

    assert clock.sleeps == [1.0]
    assert clock.now == 1.0


def test_ventana_movil_espera_solo_el_tiempo_restante(clock):
    limiter = jolpica._RateLimiter()
    for _ in range(4):
        limiter.acquire(max_wait=60)
    clock.now = 0.6

    limiter.acquire(max_wait=60)

    assert clock.sleeps == pytest.approx([0.4])
    assert clock.now == pytest.approx(1.0)


def test_rechazar_una_espera_no_reserva_otra_solicitud(clock):
    limiter = jolpica._RateLimiter()
    for _ in range(4):
        limiter.acquire(max_wait=60)

    with pytest.raises(jolpica.JolpicaRateLimitError) as error:
        limiter.acquire(max_wait=0.5)

    assert error.value.status_code == 429
    assert error.value.retry_after == 1.0
    assert clock.sleeps == []

    clock.now = 1.0
    for _ in range(4):
        limiter.acquire(max_wait=0.5)
    assert clock.sleeps == []

    with pytest.raises(jolpica.JolpicaRateLimitError) as next_error:
        limiter.acquire(max_wait=0.5)
    assert next_error.value.retry_after == 1.0


def test_se_respetan_500_solicitudes_por_hora_y_la_caducidad_exacta(clock):
    limiter = jolpica._RateLimiter()
    for _ in range(500):
        limiter.acquire(max_wait=60)

    assert clock.now == 124.0
    assert len(clock.sleeps) == 124

    with pytest.raises(jolpica.JolpicaRateLimitError) as error:
        limiter.acquire(max_wait=60)
    assert error.value.retry_after == 3476.0
    assert clock.now == 124.0

    clock.now = 3600.0
    prior_sleeps = len(clock.sleeps)
    for _ in range(4):
        limiter.acquire(max_wait=0.5)
    assert len(clock.sleeps) == prior_sleeps

    with pytest.raises(jolpica.JolpicaRateLimitError) as next_error:
        limiter.acquire(max_wait=0.5)
    assert next_error.value.retry_after == 1.0


def test_un_defer_mas_corto_no_adelanta_la_pausa_existente(clock):
    limiter = jolpica._RateLimiter()
    limiter.defer(10)
    clock.now = 2.0
    limiter.defer(1)

    limiter.acquire(max_wait=60)

    assert clock.sleeps == [8.0]
    assert clock.now == 10.0


def test_un_defer_puede_ampliar_la_pausa_existente(clock):
    limiter = jolpica._RateLimiter()
    limiter.defer(10)
    clock.now = 2.0
    limiter.defer(15)

    limiter.acquire(max_wait=60)

    assert clock.sleeps == [15.0]
    assert clock.now == 17.0


def test_cooldown_superior_al_maximo_no_duerme_ni_consume_cuota(clock):
    limiter = jolpica._RateLimiter()
    limiter.defer(120)

    with pytest.raises(jolpica.JolpicaRateLimitError) as error:
        limiter.acquire(max_wait=60)
    assert error.value.retry_after == 120.0
    assert clock.sleeps == []

    clock.now = 120.0
    for _ in range(4):
        limiter.acquire(max_wait=60)
    assert clock.sleeps == []


def test_dos_clientes_comparten_el_presupuesto_por_segundo(clock):
    first_session = Mock()
    second_session = Mock()
    first_session.get.return_value = response()
    second_session.get.return_value = response()
    first = jolpica.JolpicaClient(session=first_session, cache_ttl=0)
    second = jolpica.JolpicaClient(session=second_session, cache_ttl=0)

    for client in (first, second, first, second):
        assert client.get_seasons() == []
    assert clock.sleeps == []

    assert second.get_seasons() == []

    assert clock.sleeps == [1.0]
    assert first_session.get.call_count == 2
    assert second_session.get.call_count == 3


def test_el_429_de_un_cliente_pausa_tambien_al_otro(clock):
    first_session = Mock()
    first_response = response(429, {"Retry-After": "10"})
    first_session.get.return_value = first_response
    second_session = Mock()
    second_session.get.return_value = response()
    first = jolpica.JolpicaClient(session=first_session, max_retries=0)
    second = jolpica.JolpicaClient(session=second_session)

    with pytest.raises(jolpica.JolpicaRateLimitError):
        first.get_seasons()
    assert clock.sleeps == []

    assert second.get_seasons() == []

    assert clock.sleeps == [10.0]
    first_response.close.assert_called_once_with()


def test_reintentos_http_tambien_consumen_la_cuota_por_segundo(clock):
    session = Mock()
    temporary_responses = [response(503) for _ in range(4)]
    session.get.side_effect = [*temporary_responses, response()]
    client = jolpica.JolpicaClient(
        session=session,
        max_retries=4,
        backoff_factor=0.01,
    )

    assert client.get_seasons() == []

    assert session.get.call_count == 5
    assert clock.sleeps == pytest.approx([0.01, 0.02, 0.04, 0.93])
    assert clock.now == pytest.approx(1.0)
    for temporary in temporary_responses:
        temporary.close.assert_called_once_with()
