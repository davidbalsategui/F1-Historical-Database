"""Importación atómica e idempotente de carreras de 2024, sin modificar el esquema."""

import argparse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, time
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
from typing import Any

from api.jolpica_client import JolpicaClient, JolpicaError
from config import ConfigurationError, DatabaseConfig, load_config
from database.connection import DatabaseError, transaction
from database.repositories import MySQLRepository, SchemaMapping


TABLES = ("SEASON", "DRIVER", "CONSTRUCTOR", "CIRCUIT", "RACE", "RESULT")


class SourceValidationError(ValueError):
    """Datos de origen incompletos, ambiguos o incompatibles con una importación completa."""


@dataclass
class RecordCounts:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


def _empty_counts() -> dict[str, RecordCounts]:
    return {table: RecordCounts() for table in TABLES}


@dataclass
class ImportReport:
    """Separar acciones confirmadas de intentos que podrían acabar revertidos."""

    season: int = 2024
    success: bool = False
    status: str = "not_started"
    expected_rounds: tuple[int, ...] = ()
    processed_rounds: tuple[int, ...] = ()
    attempted_rounds: tuple[int, ...] = ()
    source_counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(TABLES, 0))
    counts: dict[str, RecordCounts] = field(default_factory=_empty_counts)
    attempted_counts: dict[str, RecordCounts] = field(default_factory=_empty_counts)
    validations: dict[str, bool | None] = field(default_factory=lambda: {
        "source_rounds": None,
        "source_unique_results": None,
        "schema": None,
        "stored_rounds": None,
        "foreign_keys": None,
        "stored_unique_results": None,
        "stored_points": None,
    })
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Representación JSON sin credenciales ni datos personales de los pilotos."""
        return asdict(self)


class ImportServiceError(RuntimeError):
    """La importación no pudo completarse; report contiene el estado y los contadores."""

    def __init__(self, message: str, report: ImportReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass
class _Payload:
    drivers: dict[str, dict[str, Any]]
    constructors: dict[str, dict[str, Any]]
    circuits: dict[str, dict[str, Any]]
    races: dict[int, dict[str, Any]]
    results: dict[int, list[dict[str, Any]]]

    @property
    def source_counts(self) -> dict[str, int]:
        return {
            "SEASON": 1,
            "DRIVER": len(self.drivers),
            "CONSTRUCTOR": len(self.constructors),
            "CIRCUIT": len(self.circuits),
            "RACE": len(self.races),
            "RESULT": sum(len(rows) for rows in self.results.values()),
        }


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SourceValidationError(f"{field_name} debe ser un objeto JSON.")
    return value


def _optional_object(value: Any, field_name: str) -> dict[str, Any]:
    return {} if value is None else _object(value, field_name)


def _list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SourceValidationError(f"{field_name} debe ser una lista de objetos JSON.")
    return value


def _text(value: Any, field_name: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise SourceValidationError(f"{field_name} debe contener texto{' no vacío' if required else ''}.")
    return value


def _int(value: Any, field_name: str, *, minimum: int = 0, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if type(value) is int:
        result = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        try:
            result = int(value)
        except ValueError:
            raise SourceValidationError(f"{field_name} no es un entero válido.") from None
    else:
        raise SourceValidationError(f"{field_name} debe ser un entero.")
    if result < minimum:
        raise SourceValidationError(f"{field_name} está fuera de rango.")
    return result


def _decimal(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise SourceValidationError(f"{field_name} requiere un decimal exacto, no un float.")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise SourceValidationError(f"{field_name} no es un decimal válido.") from None
    if not result.is_finite():
        raise SourceValidationError(f"{field_name} debe ser un decimal finito.")
    return result


def _date(value: Any, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise SourceValidationError(f"{field_name} debe ser una fecha ISO válida.") from None


def _utc_time(value: Any) -> time | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = time.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
        return parsed.replace(tzinfo=None)
    except ValueError:
        raise SourceValidationError("La hora de carrera debe indicar UTC explícitamente.") from None


def _lap_ms(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d+:[0-5]\d(?:\.\d{1,3})?", value, re.ASCII):
        raise SourceValidationError("FastestLap.Time.time no es una duración válida en milisegundos.")
    minutes, seconds = value.split(":")
    return int((Decimal(minutes) * 60 + Decimal(seconds)) * 1000)


def _merge(target: dict[str, dict[str, Any]], record: dict[str, Any], label: str) -> None:
    """Combinar observaciones parciales; rechazar atributos conocidos contradictorios."""
    key = record["api_id"]
    if key not in target:
        target[key] = record
        return
    previous = target[key]
    for field_name, value in record.items():
        if previous.get(field_name) is None:
            previous[field_name] = value
        elif value is not None and previous[field_name] != value:
            raise SourceValidationError(f"Metadatos contradictorios para {label}.{field_name}.")


def _circuit(raw: Any) -> dict[str, Any]:
    source = _object(raw, "Circuit")
    location = _optional_object(source.get("Location"), "Circuit.Location")
    latitude = _decimal(location.get("lat"), "Circuit.Location.lat")
    longitude = _decimal(location.get("long"), "Circuit.Location.long")
    if latitude is not None and not -90 <= latitude <= 90:
        raise SourceValidationError("Latitud del circuito fuera de rango.")
    if longitude is not None and not -180 <= longitude <= 180:
        raise SourceValidationError("Longitud del circuito fuera de rango.")
    return {
        "api_id": _text(source.get("circuitId"), "Circuit.circuitId", required=True),
        "name": _text(source.get("circuitName"), "Circuit.circuitName"),
        "latitude": latitude,
        "longitude": longitude,
        "locality": _text(location.get("locality"), "Circuit.Location.locality"),
        "country": _text(location.get("country"), "Circuit.Location.country"),
        "url": _text(source.get("url"), "Circuit.url"),
    }


def _driver(raw: Any) -> dict[str, Any]:
    source = _object(raw, "Driver")
    record = {"api_id": _text(source.get("driverId"), "Driver.driverId", required=True)}
    for field_name, api_name in (
        ("given_name", "givenName"), ("family_name", "familyName"),
        ("code", "code"), ("nationality", "nationality"), ("url", "url"),
    ):
        record[field_name] = _text(source.get(api_name), f"Driver.{api_name}")
    record["permanent_number"] = _int(source.get("permanentNumber"), "Driver.permanentNumber")
    record["date_of_birth"] = _date(source.get("dateOfBirth"), "Driver.dateOfBirth")
    return record


def _constructor(raw: Any) -> dict[str, Any]:
    source = _object(raw, "Constructor")
    return {
        "api_id": _text(source.get("constructorId"), "Constructor.constructorId", required=True),
        "name": _text(source.get("name"), "Constructor.name"),
        "nationality": _text(source.get("nationality"), "Constructor.nationality"),
        "url": _text(source.get("url"), "Constructor.url"),
    }


def _round(raw: dict[str, Any]) -> int:
    if _int(raw.get("season"), "season", required=True) != 2024:
        raise SourceValidationError("La importación solo admite datos de la temporada 2024.")
    return _int(raw.get("round"), "round", minimum=1, required=True)


def _result_values(source: dict[str, Any]) -> dict[str, Any]:
    elapsed = _optional_object(source.get("Time"), "Result.Time")
    fastest = _optional_object(source.get("FastestLap"), "Result.FastestLap")
    fastest_time = _optional_object(fastest.get("Time"), "Result.FastestLap.Time")
    return {
        "points": _decimal(source.get("points"), "Result.points"),
        "number": _int(source.get("number"), "Result.number"),
        "position": _int(source.get("position"), "Result.position", minimum=1),
        "position_text": _text(source.get("positionText"), "Result.positionText"),
        "grid": _int(source.get("grid"), "Result.grid"),
        "laps": _int(source.get("laps"), "Result.laps"),
        "status": _text(source.get("status"), "Result.status"),
        # Solo millis explícitos: nunca convertir un déficit +tiempo en duración total.
        "elapsed_time_ms": _int(elapsed.get("millis"), "Result.Time.millis"),
        "fastest_lap_rank": _int(fastest.get("rank"), "Result.FastestLap.rank", minimum=1),
        "fastest_lap_number": _int(fastest.get("lap"), "Result.FastestLap.lap", minimum=1),
        "fastest_lap_time_ms": _lap_ms(fastest_time.get("time")),
    }


def _normalize(calendar: Any, results: Any) -> _Payload:
    payload = _Payload({}, {}, {}, {}, {})
    calendar_rows = _list(calendar, "Calendario")
    if not calendar_rows:
        raise SourceValidationError("El calendario de 2024 está vacío.")
    for raw in calendar_rows:
        round_number = _round(raw)
        if round_number in payload.races:
            raise SourceValidationError("El calendario contiene rondas duplicadas.")
        circuit = _circuit(raw.get("Circuit"))
        _merge(payload.circuits, circuit, "Circuit")
        payload.races[round_number] = {
            "round": round_number,
            "circuit_api_id": circuit["api_id"],
            "name": _text(raw.get("raceName"), "Race.raceName"),
            "date": _date(raw.get("date"), "Race.date"),
            "time": _utc_time(raw.get("time")),
            "url": _text(raw.get("url"), "Race.url"),
        }

    for race in _list(results, "Resultados"):
        round_number = _round(race)
        if round_number not in payload.races:
            raise SourceValidationError("Los resultados incluyen una ronda ajena al calendario.")
        if round_number in payload.results:
            raise SourceValidationError("La API devolvió una ronda de resultados duplicada.")
        scheduled = payload.races[round_number]
        if race.get("Circuit") is not None:
            circuit = _circuit(race["Circuit"])
            if circuit["api_id"] != scheduled["circuit_api_id"]:
                raise SourceValidationError("El circuito de resultados no coincide con el calendario.")
            _merge(payload.circuits, circuit, "Circuit")
        for api_field, field_name in (("date", "date"), ("raceName", "name"), ("time", "time")):
            if race.get(api_field) is not None:
                value = (
                    _date(race[api_field], "Race.date") if api_field == "date"
                    else _utc_time(race[api_field]) if api_field == "time"
                    else _text(race[api_field], "Race.raceName")
                )
                if scheduled[field_name] is not None and scheduled[field_name] != value:
                    raise SourceValidationError("Los metadatos de la carrera contradicen el calendario.")
                if scheduled[field_name] is None:
                    scheduled[field_name] = value
        rows = _list(race.get("Results"), "Race.Results")
        if not rows:
            raise SourceValidationError("Una ronda del calendario no tiene resultados publicados.")
        seen = set()
        payload.results[round_number] = []
        for source in rows:
            driver = _driver(source.get("Driver"))
            if driver["api_id"] in seen:
                raise SourceValidationError("Hay resultados duplicados para un piloto en una ronda.")
            seen.add(driver["api_id"])
            _merge(payload.drivers, driver, "Driver")
            constructor = None
            if source.get("Constructor") is not None:
                constructor = _constructor(source["Constructor"])
                _merge(payload.constructors, constructor, "Constructor")
            payload.results[round_number].append({
                "driver_api_id": driver["api_id"],
                "constructor_api_id": constructor["api_id"] if constructor is not None else None,
                "values": _result_values(source),
            })
    if set(payload.results) != set(payload.races):
        raise SourceValidationError("Faltan resultados de rondas incluidas en el calendario de 2024.")
    return payload


def _print_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class ImportService:
    """Importar exclusivamente 2024; ni el servicio ni el repositorio ejecutan DDL."""

    def __init__(
        self,
        client: JolpicaClient | None = None,
        config: DatabaseConfig | None = None,
        mapping: SchemaMapping | None = None,
        progress: Callable[[str], None] | None = _print_progress,
    ) -> None:
        self.client = client
        self.config = config
        self.mapping = mapping
        self.progress = progress

    def _emit(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def import_2024(self, *, dry_run: bool = False) -> ImportReport:
        report = ImportReport()
        client = self.client
        owns_client = client is None
        in_transaction = False
        validated_transaction = False
        committed = False
        current_table = None
        try:
            if not dry_run:
                if self.mapping is None:
                    raise ConfigurationError(
                        "Falta el mapeo explícito del esquema aprobado de las seis tablas "
                        "(--mapping). sql/schema.sql no permite deducir sus columnas."
                    )
                settings = self.config if self.config is not None else load_config()
            if client is None:
                client = JolpicaClient()
            self._emit("Descargando calendario de 2024...")
            calendar = client.get_race_schedule(2024)
            self._emit("Descargando todos los resultados de 2024 mediante paginación...")
            results = client.get_race_results(2024)
            payload = _normalize(calendar, results)
            report.expected_rounds = tuple(sorted(payload.races))
            report.source_counts = payload.source_counts
            report.validations["source_rounds"] = True
            report.validations["source_unique_results"] = True
            self._emit(
                f"Fuente validada: {len(payload.races)} rondas y "
                f"{report.source_counts['RESULT']} resultados reales."
            )
            if dry_run:
                report.success = True
                report.status = "dry_run"
                return report

            with transaction(settings) as db:
                in_transaction = True
                repository = MySQLRepository(db, self.mapping)
                repository.acquire_import_lock()
                repository.validate_schema()
                report.validations["schema"] = True

                def store(table: str, values: dict[str, Any]) -> int:
                    nonlocal current_table
                    current_table = table
                    outcome = repository.upsert(table, values)
                    if outcome.action not in {"inserted", "updated", "skipped"}:
                        raise DatabaseError("El repositorio devolvió una acción desconocida.")
                    counts = report.attempted_counts[table]
                    setattr(counts, outcome.action, getattr(counts, outcome.action) + 1)
                    current_table = None
                    return outcome.primary_key

                season_id = store("SEASON", {"year": 2024, "url": None})
                identities: dict[str, dict[str, int]] = {}
                for table, records in (
                    ("DRIVER", payload.drivers),
                    ("CONSTRUCTOR", payload.constructors),
                    ("CIRCUIT", payload.circuits),
                ):
                    self._emit(f"Preparando {table}: {len(records)} registros...")
                    identities[table] = {
                        api_id: store(table, records[api_id]) for api_id in sorted(records)
                    }
                race_ids = {}
                for round_number in report.expected_rounds:
                    raw = payload.races[round_number]
                    values = {key: value for key, value in raw.items() if key != "circuit_api_id"}
                    values["season_id"] = season_id
                    values["circuit_id"] = identities["CIRCUIT"][raw["circuit_api_id"]]
                    race_ids[round_number] = store("RACE", values)

                expected_results = {}
                for index, round_number in enumerate(report.expected_rounds, start=1):
                    rows = payload.results[round_number]
                    self._emit(
                        f"Preparando ronda {round_number} ({index}/{len(payload.races)}): "
                        f"{len(rows)} resultados..."
                    )
                    for row in rows:
                        driver_api_id = row["driver_api_id"]
                        constructor_api_id = row["constructor_api_id"]
                        values = dict(row["values"])
                        values.update(
                            race_id=race_ids[round_number],
                            driver_id=identities["DRIVER"][driver_api_id],
                            constructor_id=(
                                identities["CONSTRUCTOR"][constructor_api_id]
                                if constructor_api_id is not None else None
                            ),
                        )
                        store("RESULT", values)
                        expected_results[(round_number, driver_api_id)] = (
                            constructor_api_id, values["points"]
                        )
                    report.attempted_rounds += (round_number,)

                self._emit("Verificando rondas, claves foráneas, unicidad y puntos antes del commit...")
                verification = repository.verify_import(
                    season_id, set(report.expected_rounds), expected_results
                )
                if (
                    tuple(sorted(verification.rounds)) != report.expected_rounds
                    or verification.result_count != report.source_counts["RESULT"]
                    or not verification.foreign_keys_ok
                    or verification.duplicates != 0
                ):
                    raise DatabaseError("La verificación final de la importación no fue satisfactoria.")
                validated_transaction = True
            committed = True
            report.counts = {
                table: RecordCounts(**asdict(counts)) for table, counts in report.attempted_counts.items()
            }
            report.processed_rounds = report.expected_rounds
            for key in ("stored_rounds", "foreign_keys", "stored_unique_results", "stored_points"):
                report.validations[key] = True
            report.success = True
            report.status = "committed"
            self._emit("Importación de 2024 confirmada correctamente.")
            return report
        except (Exception, KeyboardInterrupt) as error:
            if isinstance(error, (JolpicaError, DatabaseError, ConfigurationError, SourceValidationError)):
                message = str(error)
            elif isinstance(error, KeyboardInterrupt):
                message = "Importación interrumpida por el usuario."
            else:
                message = "La importación falló por un error inesperado; no se muestra información sensible."
            report.errors.append(message)
            if committed:
                # Un fallo al notificar progreso no revierte un commit ya realizado.
                report.status = "committed"
            else:
                rollback_uncertain = any("falló el rollback" in note for note in getattr(error, "__notes__", ()))
                report.status = (
                    "uncertain" if validated_transaction or rollback_uncertain
                    else "rolled_back" if in_transaction else "failed"
                )
                if current_table is not None:
                    report.counts[current_table].failed += 1
                    report.attempted_counts[current_table].failed += 1
            if report.status == "uncertain":
                report.errors.append(
                    "No se puede asegurar el resultado del commit/rollback. "
                    "Repite la importación para reconciliar los datos por sus claves únicas."
                )
            raise ImportServiceError(message, report) from None
        finally:
            if owns_client and client is not None:
                active_error = sys.exception()
                try:
                    client.close()
                except Exception:
                    message = "No se pudo cerrar la sesión HTTP del importador."
                    if active_error is not None:
                        active_error.add_note(message)
                    else:
                        report.success = False
                        report.errors.append(message)
                        if report.status == "dry_run":
                            report.status = "failed"
                        raise ImportServiceError(message, report) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validar solo la fuente, sin acceder a MySQL.")
    parser.add_argument("--mapping", type=Path, help="JSON con el mapeo aprobado de tablas, PK y columnas.")
    parser.add_argument("--report", type=Path, help="Guardar el informe JSON, sin credenciales.")
    args = parser.parse_args(argv)
    try:
        mapping = SchemaMapping.from_file(args.mapping) if args.mapping is not None else None
    except (OSError, ValueError, DatabaseError):
        print("No se pudo leer un mapeo de esquema válido.", file=sys.stderr)
        return 1
    try:
        report = ImportService(mapping=mapping).import_2024(dry_run=args.dry_run)
    except ImportServiceError as error:
        report = error.report
    output = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    print(output)
    if args.report is not None:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        except OSError:
            print("No se pudo guardar el informe en la ruta indicada.", file=sys.stderr)
            return 1
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
