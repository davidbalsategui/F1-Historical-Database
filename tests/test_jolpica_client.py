"""Pruebas del cliente Jolpica con HTTP y tiempo simulados, sin acceso a red."""

from copy import deepcopy
from datetime import datetime, timezone
from email.utils import format_datetime
from unittest.mock import Mock

import pytest
import requests

from api import jolpica_client as api


class FakeClock:
    """Avanzar los relojes sin introducir esperas en la suite."""

    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def monotonic(self):
        return self.now

    def time(self):
        return 1_700_000_000.0 + self.now - 1000.0

    def sleep(self, seconds):
        assert seconds >= 0
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(api.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(api.time, "time", fake.time)
    monkeypatch.setattr(api.time, "sleep", fake.sleep)
    monkeypatch.setattr(api, "_RATE_LIMITER", api._RateLimiter())
    return fake


@pytest.fixture
def session():
    return Mock(spec=requests.Session)


def response(body=None, status=200, headers=None):
    result = Mock(spec=requests.Response)
    result.status_code = status
    result.headers = {} if headers is None else headers
    result.json.return_value = deepcopy(body)
    return result


def envelope(table, collection, rows, *, limit=100, offset=0, total=None):
    return {
        "MRData": {
            "series": "f1",
            "limit": str(limit),
            "offset": str(offset),
            "total": str(len(rows) if total is None else total),
            table: {collection: rows},
        }
    }


def driver(identifier="piloto_prueba"):
    return {"driverId": identifier, "givenName": "Nombre", "familyName": "Apellido"}


def race(round_number="1", results=None):
    item = {
        "season": "2024",
        "round": round_number,
        "raceName": "Carrera ficticia de prueba",
        "Circuit": {
            "circuitId": "circuito_prueba",
            "circuitName": "Circuito ficticio",
            "Location": {"country": "Pruebas"},
        },
        "date": "2024-01-01",
    }
    if results is not None:
        item["Results"] = results
    return item


def result(identifier="piloto_prueba"):
    return {"Driver": driver(identifier), "position": "1", "points": "25"}


@pytest.mark.parametrize(
    ("method", "args", "path", "table", "collection", "rows"),
    [
        ("get_seasons", (), "seasons.json", "SeasonTable", "Seasons", [{"season": "2024"}]),
        ("get_drivers", (), "2024/drivers.json", "DriverTable", "Drivers", [driver()]),
        (
            "get_constructors", (), "2024/constructors.json", "ConstructorTable",
            "Constructors", [{"constructorId": "constructor_prueba", "name": "Equipo"}],
        ),
        (
            "get_circuits", (), "2024/circuits.json", "CircuitTable", "Circuits",
            [race()["Circuit"]],
        ),
        ("get_race_schedule", (), "2024/races.json", "RaceTable", "Races", [race()]),
        (
            "get_race_results", (), "2024/results.json", "RaceTable", "Races",
            [race(results=[result()])],
        ),
        (
            "get_race_results", (2024, 1), "2024/1/results.json", "RaceTable", "Races",
            [race(results=[result()])],
        ),
    ],
)
def test_endpoints_verificados_y_cabeceras_de_aplicacion(
    session, method, args, path, table, collection, rows
):
    reply = response(envelope(table, collection, rows))
    session.get.return_value = reply

    with api.JolpicaClient(session=session, timeout=(2.0, 8.0)) as client:
        assert getattr(client, method)(*args) == rows

    session.get.assert_called_once_with(
        f"{api.DEFAULT_BASE_URL}/{path}",
        params={"limit": 100, "offset": 0},
        timeout=(2.0, 8.0),
        headers={"User-Agent": api.USER_AGENT, "Accept": "application/json"},
        allow_redirects=False,
    )
    assert "F1-Historical-Database" in api.USER_AGENT
    reply.close.assert_called_once_with()
    session.close.assert_not_called()


def test_temporada_alternativa_y_base_url_configurable(session):
    session.get.return_value = response(envelope("DriverTable", "Drivers", []))
    client = api.JolpicaClient(session=session, base_url="https://example.test/f1/")

    assert client.get_drivers(2023) == []

    assert session.get.call_args.args[0] == "https://example.test/f1/2023/drivers.json"


def test_paginacion_plana_recupera_todos_sin_peticion_extra(session):
    rows = [driver("piloto_a"), driver("piloto_b"), driver("piloto_c")]
    replies = [
        response(envelope("DriverTable", "Drivers", rows[:2], limit=2, total=3)),
        response(envelope("DriverTable", "Drivers", rows[2:], limit=2, offset=2, total=3)),
    ]
    session.get.side_effect = replies

    assert api.JolpicaClient(session=session, page_size=2).get_drivers() == rows

    assert [call.kwargs["params"] for call in session.get.call_args_list] == [
        {"limit": 2, "offset": 0}, {"limit": 2, "offset": 2}
    ]
    for reply in replies:
        reply.close.assert_called_once_with()


def test_respeta_limite_devuelto_por_servidor_sin_saltar_filas(session):
    rows = [driver("piloto_a"), driver("piloto_b"), driver("piloto_c")]
    session.get.side_effect = [
        response(envelope("DriverTable", "Drivers", [row], limit=1, offset=i, total=3))
        for i, row in enumerate(rows)
    ]

    assert api.JolpicaClient(session=session, page_size=100).get_drivers() == rows

    assert [call.kwargs["params"]["offset"] for call in session.get.call_args_list] == [0, 1, 2]


def test_resultados_paginados_fusionan_una_carrera_dividida(session):
    results = [result("piloto_a"), result("piloto_b"), result("piloto_c")]
    session.get.side_effect = [
        response(envelope("RaceTable", "Races", [race(results=results[:2])], limit=2, total=3)),
        response(envelope("RaceTable", "Races", [race(results=results[2:])], limit=2, offset=2, total=3)),
    ]

    actual = api.JolpicaClient(session=session, page_size=2).get_race_results()

    assert actual == [race(results=results)]
    assert session.get.call_count == 2
    assert session.get.call_args.kwargs["params"]["offset"] == 2


def test_resultados_paginados_cuentan_pilotos_no_carreras(session):
    a, b, c = result("piloto_a"), result("piloto_b"), result("piloto_c")
    session.get.side_effect = [
        response(envelope("RaceTable", "Races", [race("1", [a]), race("2", [b])], limit=2, total=3)),
        response(envelope("RaceTable", "Races", [race("2", [c])], limit=2, offset=2, total=3)),
    ]

    actual = api.JolpicaClient(session=session, page_size=2).get_race_results()

    assert actual == [race("1", [a]), race("2", [b, c])]


def test_lista_vacia_es_ausencia_de_datos_valida_y_se_cachea(session):
    session.get.return_value = response(envelope("DriverTable", "Drivers", []))
    client = api.JolpicaClient(session=session)

    assert client.get_drivers(2099) == []
    assert client.get_drivers(2099) == []

    assert session.get.call_count == 1


def test_campos_opcionales_ausentes_o_nulos_no_se_inventan(session):
    row = race(results=[{"Driver": driver(), "position": None, "positionText": None}])
    row.pop("date")
    row["time"] = None
    row["Circuit"]["Location"]["lat"] = None
    session.get.return_value = response(envelope("RaceTable", "Races", [row]))

    actual = api.JolpicaClient(session=session).get_race_results()

    assert actual == [row]
    assert "points" not in actual[0]["Results"][0]
    assert "FastestLap" not in actual[0]["Results"][0]
    assert "date" not in actual[0]


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"MRData": []},
        {"MRData": {"limit": "100", "offset": "0", "total": "0"}},
        envelope("DriverTable", "Drivers", ["objeto_incorrecto"]),
        envelope("DriverTable", "Drivers", None, total=0),
    ],
)
def test_estructura_json_invalida_falla_sin_reintentos(session, body):
    reply = response(body)
    session.get.return_value = reply

    with pytest.raises(api.JolpicaResponseError):
        api.JolpicaClient(session=session).get_drivers()

    session.get.assert_called_once()
    reply.close.assert_called_once_with()


@pytest.mark.parametrize(("field", "value"), [("limit", "0"), ("offset", "-1"), ("total", "abc"), ("total", True)])
def test_metadatos_invalidos_no_provocan_paginacion_infinita(session, field, value):
    body = envelope("DriverTable", "Drivers", [driver()])
    body["MRData"][field] = value
    session.get.return_value = response(body)

    with pytest.raises(api.JolpicaResponseError):
        api.JolpicaClient(session=session).get_drivers()

    session.get.assert_called_once()


def test_offset_no_coincidente_se_rechaza(session):
    session.get.return_value = response(envelope("DriverTable", "Drivers", [driver()], offset=1, total=2))

    with pytest.raises(api.JolpicaResponseError):
        api.JolpicaClient(session=session).get_drivers()

    session.get.assert_called_once()


def test_pagina_sin_progreso_con_total_pendiente_se_rechaza(session):
    session.get.return_value = response(envelope("DriverTable", "Drivers", [], total=2))

    with pytest.raises(api.JolpicaResponseError):
        api.JolpicaClient(session=session).get_drivers()

    session.get.assert_called_once()


def test_total_que_cambia_entre_paginas_no_entrega_resultados_parciales(session):
    session.get.side_effect = [
        response(envelope("DriverTable", "Drivers", [driver("a")], limit=1, total=2)),
        response(envelope("DriverTable", "Drivers", [driver("b")], limit=1, offset=1, total=3)),
    ]

    with pytest.raises(api.JolpicaResponseError):
        api.JolpicaClient(session=session, page_size=1).get_drivers()

    assert session.get.call_count == 2


def test_json_no_decodificable_se_cierra_y_no_se_reintenta(session):
    reply = response()
    reply.json.side_effect = ValueError("JSON roto de prueba")
    session.get.return_value = reply

    with pytest.raises(api.JolpicaResponseError):
        api.JolpicaClient(session=session).get_seasons()

    session.get.assert_called_once()
    reply.close.assert_called_once_with()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 301])
def test_errores_http_permanentes_no_se_reintentan(session, status):
    reply = response(status=status)
    session.get.return_value = reply

    with pytest.raises(api.JolpicaHTTPError) as raised:
        api.JolpicaClient(session=session).get_seasons()

    assert raised.value.status_code == status
    session.get.assert_called_once()
    reply.close.assert_called_once_with()


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
def test_fallos_http_temporales_reintentan_y_se_recuperan(session, status, clock):
    failed = response(status=status)
    succeeded = response(envelope("SeasonTable", "Seasons", [{"season": "2024"}]))
    session.get.side_effect = [failed, succeeded]

    assert api.JolpicaClient(session=session).get_seasons() == [{"season": "2024"}]

    assert session.get.call_count == 2
    assert sum(clock.waits) >= 1.0
    failed.close.assert_called_once_with()
    succeeded.close.assert_called_once_with()


def test_reintentos_http_estan_acotados(session):
    session.get.return_value = response(status=503)

    with pytest.raises(api.JolpicaHTTPError):
        api.JolpicaClient(session=session, max_retries=2).get_seasons()

    assert session.get.call_count == 3


@pytest.mark.parametrize("exception_name", ["Timeout", "ConnectionError"])
def test_reintenta_fallos_de_red_y_se_recupera(session, exception_name):
    session.get.side_effect = [
        getattr(requests, exception_name)("Fallo temporal simulado"),
        response(envelope("SeasonTable", "Seasons", [])),
    ]

    assert api.JolpicaClient(session=session).get_seasons() == []

    assert session.get.call_count == 2


def test_timeout_persistente_devuelve_error_controlado_y_acotado(session):
    session.get.side_effect = requests.Timeout("Fallo simulado")

    with pytest.raises(api.JolpicaTimeoutError):
        api.JolpicaClient(session=session, max_retries=1).get_seasons()

    assert session.get.call_count == 2


def test_error_tls_no_se_reintenta(session):
    session.get.side_effect = requests.exceptions.SSLError("Certificado simulado inválido")

    with pytest.raises(api.JolpicaConnectionError):
        api.JolpicaClient(session=session).get_seasons()

    session.get.assert_called_once()


def test_429_respeta_retry_after_en_segundos(session, clock):
    throttled = response(status=429, headers={"Retry-After": "3"})
    session.get.side_effect = [throttled, response(envelope("SeasonTable", "Seasons", []))]

    assert api.JolpicaClient(session=session).get_seasons() == []

    assert session.get.call_count == 2
    assert sum(clock.waits) >= 3.0
    throttled.close.assert_called_once_with()


def test_429_respeta_retry_after_como_fecha_http(session, clock):
    deadline = format_datetime(datetime.fromtimestamp(clock.time() + 8, tz=timezone.utc), usegmt=True)
    session.get.side_effect = [
        response(status=429, headers={"Retry-After": deadline}),
        response(envelope("SeasonTable", "Seasons", [])),
    ]

    assert api.JolpicaClient(session=session).get_seasons() == []

    assert sum(clock.waits) >= 8.0
    assert session.get.call_count == 2


def test_429_sin_retry_after_usa_espera_y_reintentos_acotados(session, clock):
    session.get.return_value = response(status=429)

    with pytest.raises(api.JolpicaRateLimitError) as raised:
        api.JolpicaClient(session=session, max_retries=1).get_seasons()

    assert raised.value.status_code == 429
    assert sum(clock.waits) >= 1.0
    assert session.get.call_count == 2


def test_429_con_espera_excesiva_no_reintenta_antes_de_lo_indicado(session, clock):
    session.get.return_value = response(status=429, headers={"Retry-After": "3600"})

    with pytest.raises(api.JolpicaRateLimitError) as raised:
        api.JolpicaClient(session=session, max_retry_delay=60).get_seasons()

    assert raised.value.retry_after == 3600
    session.get.assert_called_once()
    assert clock.waits == []


def test_limite_de_rafaga_es_compartido_entre_clientes(session, clock):
    session.get.return_value = response(envelope("SeasonTable", "Seasons", []))
    clients = [api.JolpicaClient(session=session, cache_ttl=0) for _ in range(2)]

    for index in range(5):
        clients[index % 2].get_seasons()

    assert session.get.call_count == 5
    assert sum(clock.waits) >= 1.0


def test_limite_horario_no_se_supera(session):
    session.get.return_value = response(envelope("SeasonTable", "Seasons", []))
    client = api.JolpicaClient(session=session, cache_ttl=0)
    for _ in range(500):
        client.get_seasons()

    with pytest.raises(api.JolpicaRateLimitError) as raised:
        client.get_seasons()

    assert raised.value.retry_after > 60
    assert session.get.call_count == 500


def test_cache_evade_solicitudes_duplicadas_y_aisla_mutaciones(session):
    session.get.return_value = response(envelope("DriverTable", "Drivers", [driver()]))
    client = api.JolpicaClient(session=session)

    rows = client.get_drivers()
    rows[0]["givenName"] = "Modificado por quien llama"

    assert client.get_drivers() == [driver()]
    session.get.assert_called_once()


def test_cache_caduca_y_puede_vaciarse(session, clock):
    session.get.return_value = response(envelope("DriverTable", "Drivers", [driver()]))
    client = api.JolpicaClient(session=session, cache_ttl=10)
    client.get_drivers()
    clock.now += 11
    client.get_drivers()
    client.clear_cache()
    client.get_drivers()

    assert session.get.call_count == 3


def test_respuestas_fallidas_no_se_cachean(session):
    session.get.side_effect = [response(None), response(envelope("DriverTable", "Drivers", [driver()]))]
    client = api.JolpicaClient(session=session)

    with pytest.raises(api.JolpicaResponseError):
        client.get_drivers()
    assert client.get_drivers() == [driver()]

    assert session.get.call_count == 2


def test_sesion_propia_se_cierra_al_salir_incluso_con_error(monkeypatch, session):
    monkeypatch.setattr(api.requests, "Session", Mock(return_value=session))
    session.get.side_effect = requests.Timeout("Fallo simulado")

    with pytest.raises(api.JolpicaTimeoutError):
        with api.JolpicaClient(max_retries=0) as client:
            client.get_seasons()

    session.close.assert_called_once_with()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_size": 0}, {"page_size": 101}, {"page_size": True},
        {"max_retries": -1}, {"max_retries": True},
        {"timeout": 0}, {"timeout": (1, 0)}, {"timeout": float("nan")},
        {"cache_ttl": -1}, {"backoff_factor": -1}, {"max_retry_delay": -1},
    ],
)
def test_configuracion_invalida_falla_antes_de_http(session, kwargs):
    with pytest.raises(ValueError):
        api.JolpicaClient(session=session, **kwargs)

    session.get.assert_not_called()


@pytest.mark.parametrize("season", [True, 0, -1, "2024/1", None])
def test_temporada_invalida_no_se_interpreta_como_ruta(session, season):
    with pytest.raises(ValueError):
        api.JolpicaClient(session=session).get_drivers(season)

    session.get.assert_not_called()


@pytest.mark.parametrize("round_number", [True, 0, -1, "1/results"])
def test_ronda_invalida_falla_antes_de_http(session, round_number):
    with pytest.raises(ValueError):
        api.JolpicaClient(session=session).get_race_results(2024, round_number)

    session.get.assert_not_called()
