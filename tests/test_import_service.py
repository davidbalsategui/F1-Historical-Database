"""Pruebas del importador con datos ficticios, sin HTTP ni MySQL reales."""

from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import json
import traceback
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import ConfigurationError
from database.connection import DatabaseError
from services import import_service as service


ROLES = ("SEASON", "DRIVER", "CONSTRUCTOR", "CIRCUIT", "RACE", "RESULT")
NATURAL_KEYS = {
    "SEASON": ("year",),
    "DRIVER": ("api_id",),
    "CONSTRUCTOR": ("api_id",),
    "CIRCUIT": ("api_id",),
    "RACE": ("season_id", "round"),
    "RESULT": ("race_id", "driver_id"),
}


def circuit(identifier):
    return {
        "circuitId": identifier,
        "circuitName": f"Circuito ficticio {identifier}",
        "Location": {"lat": "1.123456", "long": "2.654321", "country": None},
    }


def driver(identifier):
    return {
        "driverId": identifier,
        "givenName": "Nombre ficticio",
        "familyName": identifier,
    }


def constructor(identifier):
    return {"constructorId": identifier, "name": f"Equipo ficticio {identifier}"}


@pytest.fixture
def source():
    calendar = [
        {
            "season": "2024", "round": "1", "raceName": "Carrera ficticia A",
            "Circuit": circuit("circuito_a"), "date": "2024-03-01",
        },
        {
            "season": "2024", "round": "2", "raceName": "Carrera ficticia B",
            "Circuit": circuit("circuito_b"), "date": "2024-03-08",
        },
    ]
    results = deepcopy(calendar)
    results[0]["Results"] = [
        {
            "Driver": driver("piloto_a"), "Constructor": constructor("equipo_a"),
            "position": "1", "points": "12.375", "grid": "0", "laps": "0",
        },
        {
            "Driver": driver("piloto_b"), "Constructor": constructor("equipo_b"),
            "position": None, "points": "0", "status": "Estado ficticio",
        },
    ]
    results[1]["Results"] = [
        {
            "Driver": driver("piloto_a"), "Constructor": constructor("equipo_b"),
            "position": "1", "points": None,
        },
        {
            "Driver": driver("piloto_c"), "Constructor": constructor("equipo_a"),
            "position": "2", "points": "2",
        },
    ]
    return SimpleNamespace(calendar=calendar, results=results)


@pytest.fixture
def client(source):
    client = Mock()
    client.get_race_schedule.return_value = source.calendar
    client.get_race_results.return_value = source.results
    return client


@pytest.fixture
def storage(monkeypatch):
    """Simular persistencia, PK internas, transacciones y comprobación de FKs."""
    db = SimpleNamespace(
        rows={role: {} for role in ROLES},
        next_id=100,
        events=[],
        upserts=[],
        fail_role=None,
        fail_result_number=None,
        result_attempts=0,
        schema_failure=False,
        failure_exception=DatabaseError("Fallo simulado al guardar el registro."),
    )

    @contextmanager
    def transaction(config):
        before = deepcopy(db.rows)
        db.events.append("begin")
        try:
            yield db
        except BaseException:
            db.rows = before
            db.events.append("rollback")
            raise
        else:
            db.events.append("commit")

    class Repository:
        def __init__(self, connection, mapping):
            assert connection is db
            self.db = connection

        def acquire_import_lock(self):
            db.events.append("lock")

        def validate_schema(self):
            db.events.append("schema")
            if db.schema_failure:
                raise DatabaseError("El esquema no es compatible.")

        def upsert(self, role, values):
            db.upserts.append((role, deepcopy(values)))
            if role == "RESULT":
                db.result_attempts += 1
            if role == db.fail_role or (
                role == "RESULT" and db.result_attempts == db.fail_result_number
            ):
                raise db.failure_exception
            assert "schema" in db.events
            key = tuple(values[name] for name in NATURAL_KEYS[role])
            previous = db.rows[role].get(key)
            if previous is None:
                db.next_id += 1
                pk = db.next_id
                action = "inserted"
            else:
                pk = previous["pk"]
                action = "skipped" if previous["values"] == values else "updated"
            db.rows[role][key] = {"pk": pk, "values": deepcopy(values)}
            return SimpleNamespace(primary_key=pk, action=action)

        def verify_import(self, season_id, expected_rounds, expected_results):
            db.events.append("verify")
            assert db.rows["SEASON"][(2024,)]["pk"] == season_id
            drivers = {row["pk"]: key[0] for key, row in db.rows["DRIVER"].items()}
            constructors = {
                row["pk"]: key[0] for key, row in db.rows["CONSTRUCTOR"].items()
            }
            circuits = {row["pk"] for row in db.rows["CIRCUIT"].values()}
            races = {}
            for (parent_id, round_number), row in db.rows["RACE"].items():
                assert parent_id == season_id
                assert row["values"]["circuit_id"] in circuits
                races[row["pk"]] = round_number
            assert set(races.values()) == set(expected_rounds)
            actual = {}
            for row in db.rows["RESULT"].values():
                values = row["values"]
                assert values["race_id"] in races
                assert values["driver_id"] in drivers
                constructor_id = values["constructor_id"]
                assert constructor_id is None or constructor_id in constructors
                key = races[values["race_id"]], drivers[values["driver_id"]]
                assert key not in actual
                actual[key] = (
                    constructors.get(constructor_id), values["points"]
                )
            assert actual == expected_results
            return SimpleNamespace(
                rounds=tuple(sorted(expected_rounds)), result_count=len(actual),
                foreign_keys_ok=True, duplicates=0,
            )

    monkeypatch.setattr(service, "transaction", transaction)
    monkeypatch.setattr(service, "MySQLRepository", Repository)
    return db


def importer(client, **kwargs):
    return service.ImportService(client=client, config=object(), mapping=object(), **kwargs)


def assert_counts(report, field, expected):
    assert {role: getattr(report.counts[role], field) for role in ROLES} == expected


def test_importa_entidades_en_orden_y_resuelve_las_claves_internas(client, storage):
    report = importer(client).import_2024()

    assert report.success
    assert report.status == "committed"
    assert report.season == 2024
    assert tuple(report.expected_rounds) == (1, 2)
    assert tuple(report.processed_rounds) == (1, 2)
    expected = dict(zip(ROLES, (1, 3, 2, 2, 2, 4)))
    assert report.source_counts == expected
    assert_counts(report, "inserted", expected)
    assert_counts(report, "updated", dict.fromkeys(ROLES, 0))
    assert_counts(report, "skipped", dict.fromkeys(ROLES, 0))
    assert storage.events == ["begin", "lock", "schema", "verify", "commit"]
    actual_order = [ROLES.index(role) for role, _ in storage.upserts]
    assert actual_order == sorted(actual_order)
    assert all(row["pk"] > 100 for rows in storage.rows.values() for row in rows.values())
    client.get_race_schedule.assert_called_once_with(2024)
    client.get_race_results.assert_called_once_with(2024)


def test_reimportar_la_misma_fuente_no_inserta_ni_duplica_registros(client, storage):
    instance = importer(client)
    first = instance.import_2024()
    snapshot = deepcopy(storage.rows)

    second = instance.import_2024()

    assert first.success and second.success
    assert storage.rows == snapshot
    assert_counts(second, "inserted", dict.fromkeys(ROLES, 0))
    assert_counts(second, "updated", dict.fromkeys(ROLES, 0))
    assert_counts(second, "skipped", second.source_counts)


def test_una_correccion_de_puntos_actualiza_el_resultado_existente(client, source, storage):
    instance = importer(client)
    instance.import_2024()
    original_ids = {key: row["pk"] for key, row in storage.rows["RESULT"].items()}
    source.results[0]["Results"][0]["points"] = "13.125"

    report = instance.import_2024()

    assert report.counts["RESULT"].updated == 1
    assert report.counts["RESULT"].inserted == 0
    assert {key: row["pk"] for key, row in storage.rows["RESULT"].items()} == original_ids


def test_preserva_decimales_null_y_ceros_sin_asignar_constructor_al_piloto(client, storage):
    importer(client).import_2024()

    values = [row["values"] for row in storage.rows["RESULT"].values()]
    assert {row["points"] for row in values} == {Decimal("12.375"), Decimal("0"), Decimal("2"), None}
    assert all(row["points"] is None or isinstance(row["points"], Decimal) for row in values)
    first = values[0]
    assert first["grid"] == 0
    assert first["laps"] == 0
    assert values[1]["position"] is None
    assert all("constructor_id" not in row["values"] for row in storage.rows["DRIVER"].values())
    api_to_pk = {key[0]: row["pk"] for key, row in storage.rows["DRIVER"].items()}
    driver_a_results = [row for row in values if row["driver_id"] == api_to_pk["piloto_a"]]
    assert len({row["constructor_id"] for row in driver_a_results}) == 2


def test_constructor_desconocido_sigue_null_sin_crear_un_equipo(client, source, storage):
    source.results[0]["Results"][0].pop("Constructor")
    source.results[1]["Results"][1]["Constructor"] = None

    report = importer(client).import_2024()

    assert report.success
    assert report.source_counts["CONSTRUCTOR"] == 1
    assert sum(row["values"]["constructor_id"] is None for row in storage.rows["RESULT"].values()) == 2


def test_no_confunde_un_deficit_de_tiempo_con_duracion_total(client, source, storage):
    first = source.results[0]["Results"][0]
    first["Time"] = {"time": "+1.234"}
    first["FastestLap"] = {"rank": "1", "lap": "7", "Time": {"time": "1:23.456"}}
    source.results[0]["Results"][1]["Time"] = {"millis": "123456", "time": "2:03.456"}

    importer(client).import_2024()

    values = [row["values"] for row in storage.rows["RESULT"].values()]
    assert values[0]["elapsed_time_ms"] is None
    assert values[0]["fastest_lap_time_ms"] == 83456
    assert values[1]["elapsed_time_ms"] == 123456


def test_dry_run_no_consulta_configuracion_ni_abre_mysql(client, monkeypatch):
    load = Mock(side_effect=AssertionError("No se debe cargar MySQL"))
    transaction = Mock(side_effect=AssertionError("No se debe abrir MySQL"))
    monkeypatch.setattr(service, "load_config", load)
    monkeypatch.setattr(service, "transaction", transaction)

    report = service.ImportService(client=client).import_2024(dry_run=True)

    assert report.success
    assert report.status == "dry_run"
    assert report.source_counts["RESULT"] == 4
    assert all(count.inserted == count.updated == count.skipped == count.failed == 0 for count in report.counts.values())
    load.assert_not_called()
    transaction.assert_not_called()
    json.dumps(report.as_dict(), ensure_ascii=False)


def test_progreso_y_reporte_no_modifican_los_datos_de_la_api(client, source, storage):
    before = deepcopy(source)
    progress = Mock()

    report = importer(client, progress=progress).import_2024()

    assert progress.call_count >= 2
    assert all(isinstance(call.args[0], str) for call in progress.call_args_list)
    assert source.calendar == before.calendar
    assert source.results == before.results
    assert json.loads(json.dumps(report.as_dict()))["season"] == 2024


@pytest.mark.parametrize("mutation", [
    "missing_round", "unknown_round", "wrong_calendar_season", "wrong_result_season",
    "duplicate_calendar_round", "duplicate_result_round", "duplicate_driver_result",
    "missing_driver_id", "missing_circuit_id", "mismatched_circuit",
    "missing_constructor_id", "empty_calendar", "empty_results", "empty_race_results",
])
def test_fuente_inconsistente_falla_antes_de_escribir(client, source, storage, mutation):
    if mutation == "missing_round":
        source.results.pop()
    elif mutation == "unknown_round":
        source.results[-1]["round"] = "3"
    elif mutation == "wrong_calendar_season":
        source.calendar[0]["season"] = "2023"
    elif mutation == "wrong_result_season":
        source.results[0]["season"] = "2025"
    elif mutation == "duplicate_calendar_round":
        source.calendar.append(deepcopy(source.calendar[0]))
    elif mutation == "duplicate_result_round":
        source.results.append(deepcopy(source.results[0]))
    elif mutation == "duplicate_driver_result":
        source.results[0]["Results"].append(deepcopy(source.results[0]["Results"][0]))
    elif mutation == "missing_driver_id":
        source.results[0]["Results"][0]["Driver"].pop("driverId")
    elif mutation == "missing_circuit_id":
        source.calendar[0]["Circuit"].pop("circuitId")
    elif mutation == "mismatched_circuit":
        source.results[0]["Circuit"]["circuitId"] = "otro_circuito"
    elif mutation == "missing_constructor_id":
        source.results[0]["Results"][0]["Constructor"].pop("constructorId")
    elif mutation == "empty_calendar":
        source.calendar.clear()
    elif mutation == "empty_results":
        source.results.clear()
    else:
        source.results[0]["Results"].clear()

    with pytest.raises(service.ImportServiceError) as raised:
        importer(client).import_2024()

    assert not raised.value.report.success
    assert not storage.upserts
    assert all(not rows for rows in storage.rows.values())


@pytest.mark.parametrize("points", ["NaN", "Infinity", "no_es_numero", True])
def test_puntos_invalidos_no_se_convierten_en_cero(client, source, storage, points):
    source.results[0]["Results"][0]["points"] = points

    with pytest.raises(service.ImportServiceError):
        importer(client).import_2024()

    assert not storage.upserts


def test_rollback_no_informa_inserciones_confirmadas_y_permite_reiniciar(client, storage):
    storage.fail_result_number = 2
    instance = importer(client)

    with pytest.raises(service.ImportServiceError) as raised:
        instance.import_2024()

    failed = raised.value.report
    assert not failed.success
    assert failed.status == "rolled_back"
    assert storage.events[-1] == "rollback"
    assert all(not rows for rows in storage.rows.values())
    assert all(count.inserted == count.updated == count.skipped == 0 for count in failed.counts.values())
    assert sum(count.inserted for count in failed.attempted_counts.values()) > 0
    assert failed.counts["RESULT"].failed == 1
    storage.fail_result_number = None

    restarted = instance.import_2024()

    assert restarted.success
    assert restarted.counts["RESULT"].inserted == 4
    assert len(storage.rows["RESULT"]) == 4


def test_esquema_incompatible_revierte_sin_dml(client, storage):
    storage.schema_failure = True

    with pytest.raises(service.ImportServiceError) as raised:
        importer(client).import_2024()

    assert not raised.value.report.success
    assert not storage.upserts
    assert storage.events[-1] == "rollback"


def test_interrupcion_revierte_y_entrega_un_informe_sin_inserciones_confirmadas(client, storage):
    storage.fail_result_number = 2
    storage.failure_exception = KeyboardInterrupt()

    with pytest.raises(service.ImportServiceError) as raised:
        importer(client).import_2024()

    report = raised.value.report
    assert not report.success
    assert report.status == "rolled_back"
    assert "interrumpida" in str(raised.value)
    assert storage.events[-1] == "rollback"
    assert all(not rows for rows in storage.rows.values())
    assert all(count.inserted == count.updated == count.skipped == 0 for count in report.counts.values())
    storage.failure_exception = DatabaseError("Fallo simulado al guardar el registro.")
    storage.fail_result_number = None
    assert importer(client).import_2024().success


def test_sin_mapeo_aprobado_falla_antes_de_hacer_peticiones(client):
    with pytest.raises(service.ImportServiceError) as raised:
        service.ImportService(client=client, config=object()).import_2024()

    assert not raised.value.report.success
    assert raised.value.report.status == "failed"
    client.get_race_schedule.assert_not_called()
    client.get_race_results.assert_not_called()


def test_configuracion_ausente_falla_antes_de_descargar(client, monkeypatch):
    monkeypatch.setattr(
        service, "load_config", Mock(side_effect=ConfigurationError("Falta DB_PASSWORD."))
    )

    with pytest.raises(service.ImportServiceError) as raised:
        service.ImportService(client=client, mapping=object()).import_2024()

    assert "DB_PASSWORD" in str(raised.value)
    client.get_race_schedule.assert_not_called()
    client.get_race_results.assert_not_called()


def test_fallo_http_no_abre_transaccion(client, storage):
    from api.jolpica_client import JolpicaTimeoutError

    client.get_race_results.side_effect = JolpicaTimeoutError("Tiempo de espera agotado.")

    with pytest.raises(service.ImportServiceError) as raised:
        importer(client).import_2024()

    assert not raised.value.report.success
    assert storage.events == []
    assert not storage.upserts


def test_importacion_con_cliente_real_y_paginas_http_simuladas(source, storage, monkeypatch):
    """Recorrer HTTP, paginación y servicio sin usar Internet ni MySQL."""
    from api import jolpica_client as api

    monkeypatch.setattr(api, "_RATE_LIMITER", api._RateLimiter())
    first_results = deepcopy(source.results)
    first_results[-1]["Results"] = first_results[-1]["Results"][:1]
    last_results = [deepcopy(source.results[-1])]
    last_results[0]["Results"] = last_results[0]["Results"][1:]

    def response(rows, offset, total):
        item = Mock(status_code=200, headers={})
        item.json.return_value = {
            "MRData": {
                "limit": "3", "offset": str(offset), "total": str(total),
                "RaceTable": {"Races": rows},
            }
        }
        return item

    session = Mock()
    session.get.side_effect = [
        response(deepcopy(source.calendar), 0, 2),
        response(first_results, 0, 4),
        response(last_results, 3, 4),
    ]

    with api.JolpicaClient(session=session, page_size=3, max_retries=0) as actual_client:
        report = importer(actual_client).import_2024()

    assert report.success
    assert report.source_counts["RESULT"] == 4
    assert len(storage.rows["RESULT"]) == 4
    assert session.get.call_count == 3
    assert [call.kwargs["params"]["offset"] for call in session.get.call_args_list] == [0, 0, 3]
    assert session.get.call_args_list[0].args[0].endswith("/2024/races.json")
    assert all(call.args[0].endswith("/2024/results.json") for call in session.get.call_args_list[1:])


@pytest.mark.parametrize("container", ["Location", "Time", "FastestLap", "FastestLap.Time"])
@pytest.mark.parametrize("invalid", [[], "", 0, False])
def test_contenedores_opcionales_invalidos_no_se_convierten_en_null(
    client, source, storage, container, invalid
):
    if container == "Location":
        source.calendar[0]["Circuit"]["Location"] = invalid
    elif container == "FastestLap.Time":
        source.results[0]["Results"][0]["FastestLap"] = {"Time": invalid}
    else:
        source.results[0]["Results"][0][container] = invalid

    with pytest.raises(service.ImportServiceError) as raised:
        importer(client).import_2024()

    assert not raised.value.report.success
    assert storage.events == []
    assert not storage.upserts


def test_fallo_de_cierre_preserva_el_error_http_original_sin_exponer_secretos(
    client, storage, monkeypatch
):
    from api.jolpica_client import JolpicaTimeoutError

    secret = "clave_ficticia_cierre_647"
    client.get_race_results.side_effect = JolpicaTimeoutError("Tiempo de espera agotado.")
    client.close.side_effect = RuntimeError(secret)
    monkeypatch.setattr(service, "JolpicaClient", Mock(return_value=client))

    with pytest.raises(service.ImportServiceError) as raised:
        service.ImportService(config=object(), mapping=object()).import_2024()

    error = raised.value
    assert str(error) == "Tiempo de espera agotado."
    assert error.report.status == "failed"
    assert not error.report.success
    assert any("cerrar la sesión HTTP" in note for note in error.__notes__)
    assert secret not in "".join(traceback.format_exception(error))
    assert secret not in json.dumps(error.report.as_dict())
    assert storage.events == []
    client.close.assert_called_once_with()


def test_fallo_de_cierre_tras_commit_conserva_los_contadores_confirmados(
    client, storage, monkeypatch
):
    secret = "clave_ficticia_cierre_647"
    client.close.side_effect = RuntimeError(secret)
    monkeypatch.setattr(service, "JolpicaClient", Mock(return_value=client))

    with pytest.raises(service.ImportServiceError) as raised:
        service.ImportService(config=object(), mapping=object()).import_2024()

    error = raised.value
    assert not error.report.success
    assert error.report.status == "committed"
    assert storage.events[-1] == "commit"
    assert_counts(error.report, "inserted", error.report.source_counts)
    assert tuple(error.report.processed_rounds) == (1, 2)
    assert len(storage.rows["RESULT"]) == 4
    assert secret not in "".join(traceback.format_exception(error))
    assert secret not in json.dumps(error.report.as_dict())
    client.close.assert_called_once_with()


def test_fallo_de_cierre_en_dry_run_no_informa_una_validacion_exitosa(
    client, monkeypatch
):
    secret = "clave_ficticia_cierre_647"
    client.close.side_effect = RuntimeError(secret)
    monkeypatch.setattr(service, "JolpicaClient", Mock(return_value=client))

    with pytest.raises(service.ImportServiceError) as raised:
        service.ImportService().import_2024(dry_run=True)

    error = raised.value
    assert not error.report.success
    assert error.report.status == "failed"
    assert error.report.source_counts["RESULT"] == 4
    assert all(count.inserted == count.updated == count.skipped == 0 for count in error.report.counts.values())
    assert secret not in "".join(traceback.format_exception(error))
    client.close.assert_called_once_with()
