"""Persistencia del importador, sin crear ni deducir un esquema físico.

El mapeo de columnas es obligatorio y se contrasta con ``information_schema``
antes de escribir. Las transacciones pertenecen al servicio que usa el repositorio.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time, timedelta
from decimal import Decimal
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from database.connection import DatabaseError, cursor


TABLE_ROLES = ("SEASON", "DRIVER", "CONSTRUCTOR", "CIRCUIT", "RACE", "RESULT")
NATURAL_KEYS = {
    "SEASON": ("year",),
    "DRIVER": ("api_id",),
    "CONSTRUCTOR": ("api_id",),
    "CIRCUIT": ("api_id",),
    "RACE": ("season_id", "round"),
    "RESULT": ("race_id", "driver_id"),
}
REQUIRED_FIELDS = {
    "SEASON": {"year"},
    "DRIVER": {"api_id"},
    "CONSTRUCTOR": {"api_id"},
    "CIRCUIT": {"api_id"},
    "RACE": {"season_id", "circuit_id", "round"},
    "RESULT": {"race_id", "driver_id", "constructor_id", "points"},
}
OPTIONAL_FIELDS = {
    "SEASON": {"url"},
    "DRIVER": {"given_name", "family_name", "permanent_number", "code", "date_of_birth", "nationality", "url"},
    "CONSTRUCTOR": {"name", "nationality", "url"},
    "CIRCUIT": {"name", "latitude", "longitude", "locality", "country", "url"},
    "RACE": {"name", "date", "time", "url"},
    "RESULT": {
        "number", "position", "position_text", "grid", "laps", "status", "elapsed_time_ms",
        "fastest_lap_rank", "fastest_lap_number", "fastest_lap_time_ms",
    },
}
_INTEGER_TYPES = {"tinyint", "smallint", "mediumint", "int", "bigint"}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_IMPORT_LOCK = "f1-historical-import-2024"


class SchemaCompatibilityError(DatabaseError):
    """El mapeo o el esquema existente no permiten garantizar una importación segura."""


class RepositoryValidationError(DatabaseError):
    """Los datos o la comprobación final no cumplen el contrato del importador."""


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise SchemaCompatibilityError("Identificador SQL inválido en el mapeo de columnas.")
    return value


def _quote(value: str) -> str:
    return "`" + _identifier(value) + "`"


@dataclass(frozen=True)
class TableMapping:
    """Nombres físicos proporcionados expresamente a partir del esquema aprobado."""

    table: str
    primary_key: str
    columns: Mapping[str, str]

    def __post_init__(self) -> None:
        _identifier(self.table)
        _identifier(self.primary_key)
        if not isinstance(self.columns, Mapping) or not self.columns:
            raise SchemaCompatibilityError("Cada tabla necesita un mapeo de columnas explícito.")
        copied = dict(self.columns)
        for field, column in copied.items():
            _identifier(field)
            _identifier(column)
        if len(set(copied.values())) != len(copied):
            raise SchemaCompatibilityError("Dos campos lógicos no pueden usar la misma columna física.")
        object.__setattr__(self, "columns", MappingProxyType(copied))


@dataclass(frozen=True)
class SchemaMapping:
    """Mapeo completo de los seis roles, sin valores físicos predeterminados."""

    tables: Mapping[str, TableMapping]

    def __post_init__(self) -> None:
        if not isinstance(self.tables, Mapping) or set(self.tables) != set(TABLE_ROLES):
            raise SchemaCompatibilityError("El mapeo debe definir SEASON, DRIVER, CONSTRUCTOR, CIRCUIT, RACE y RESULT.")
        copied = dict(self.tables)
        for role, table in copied.items():
            if not isinstance(table, TableMapping):
                raise SchemaCompatibilityError("Cada tabla debe utilizar TableMapping.")
            fields = set(table.columns)
            if not REQUIRED_FIELDS[role] <= fields:
                raise SchemaCompatibilityError(f"Faltan campos lógicos obligatorios en {role}.")
            if fields - (REQUIRED_FIELDS[role] | OPTIONAL_FIELDS[role]):
                raise SchemaCompatibilityError(f"El mapeo contiene campos lógicos desconocidos en {role}.")
            if table.primary_key in table.columns.values() and not (
                role == "SEASON" and table.columns["year"] == table.primary_key
            ):
                raise SchemaCompatibilityError(f"La PK interna de {role} no puede representar otro campo lógico.")
        if len({table.table for table in copied.values()}) != len(TABLE_ROLES):
            raise SchemaCompatibilityError("Los seis roles deben corresponder a seis tablas diferentes.")
        object.__setattr__(self, "tables", MappingProxyType(copied))

    @classmethod
    def from_file(cls, path: str | Path) -> "SchemaMapping":
        """Leer JSON sin ejecutar SQL ni interpretar un DDL inexistente."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SchemaCompatibilityError("No se pudo leer el archivo JSON de mapeo del esquema.") from None
        if not isinstance(payload, dict) or set(payload) != {"tables"}:
            raise SchemaCompatibilityError("El JSON de mapeo debe contener únicamente el objeto tables.")
        tables = payload["tables"]
        if not isinstance(tables, dict):
            raise SchemaCompatibilityError("tables debe ser un objeto JSON.")
        mapped = {}
        for role, definition in tables.items():
            if not isinstance(definition, dict) or set(definition) != {"table", "primary_key", "columns"}:
                raise SchemaCompatibilityError("Cada tabla necesita table, primary_key y columns.")
            mapped[role] = TableMapping(**definition)
        return cls(mapped)


@dataclass(frozen=True)
class UpsertOutcome:
    primary_key: int
    action: str


@dataclass(frozen=True)
class VerificationReport:
    rounds: tuple[int, ...]
    result_count: int
    foreign_keys_ok: bool
    duplicates: int


class MySQLRepository:
    """Upserts parametrizados y verificaciones dentro de una conexión transaccional."""

    def __init__(self, db: Any, mapping: SchemaMapping) -> None:
        if not isinstance(mapping, SchemaMapping):
            raise SchemaCompatibilityError("Se necesita el mapeo explícito del esquema aprobado.")
        self.db = db
        self.mapping = mapping
        self._schema_validated = False
        self._lock_acquired = False
        self._columns: dict[str, dict[str, dict[str, Any]]] = {}

    def acquire_import_lock(self) -> None:
        """Serializar importadores del servidor; el cierre de sesión libera el bloqueo."""
        with cursor(self.db) as query:
            query.execute("SELECT GET_LOCK(%s, %s)", (_IMPORT_LOCK, 0))
            row = query.fetchone()
        if row != (1,):
            raise RepositoryValidationError("Ya hay otra importación de 2024 en ejecución o no se pudo obtener el bloqueo.")
        self._lock_acquired = True

    def validate_schema(self) -> None:
        """Contrastar columnas, tipos, claves y motores sin cambiar ninguna tabla."""
        self._schema_validated = False
        self._columns = {}
        names = tuple(self.mapping.tables[role].table for role in TABLE_ROLES)
        slots = ", ".join("%s" for _ in names)
        with cursor(self.db) as query:
            query.execute("SELECT @@SESSION.foreign_key_checks")
            if query.fetchone() != (1,):
                raise SchemaCompatibilityError("La conexión debe mantener foreign_key_checks activado.")
            query.execute(
                "SELECT TABLE_NAME, ENGINE, TABLE_TYPE FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({slots})",
                names,
            )
            tables = {row[0]: (row[1], row[2]) for row in query.fetchall()}
            query.execute(
                "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, COLUMN_TYPE, IS_NULLABLE, "
                "COLUMN_DEFAULT, EXTRA, NUMERIC_PRECISION, NUMERIC_SCALE, CHARACTER_MAXIMUM_LENGTH "
                "FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({slots})",
                names,
            )
            columns = defaultdict(dict)
            labels = ("data_type", "column_type", "nullable", "default", "extra", "precision", "scale", "length")
            for row in query.fetchall():
                columns[row[0]][row[1]] = dict(zip(labels, row[2:]))
            query.execute(
                "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART "
                "FROM information_schema.STATISTICS "
                f"WHERE TABLE_SCHEMA = DATABASE() AND NON_UNIQUE = 0 AND TABLE_NAME IN ({slots}) "
                "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
                names,
            )
            indexes = defaultdict(lambda: defaultdict(list))
            for table, name, position, column, prefix in query.fetchall():
                indexes[table][name].append((int(position), column, prefix))
            query.execute(
                "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
                "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_SCHEMA = DATABASE() "
                f"AND TABLE_NAME IN ({slots}) ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION",
                names,
            )
            foreign_keys = defaultdict(list)
            for table, constraint, column, parent, parent_column in query.fetchall():
                foreign_keys[(table, constraint)].append((column, parent, parent_column))

        for role, definition in self.mapping.tables.items():
            table = definition.table
            if table not in tables or tables[table] != ("InnoDB", "BASE TABLE"):
                raise SchemaCompatibilityError(f"{role} debe existir como tabla InnoDB en la base seleccionada.")
            actual = columns[table]
            needed = set(definition.columns.values()) | {definition.primary_key}
            if not needed <= set(actual):
                raise SchemaCompatibilityError(f"Faltan columnas mapeadas en {role}.")
            unique_indexes = {
                name: tuple(column for _, column, _ in sorted(entries))
                for name, entries in indexes[table].items()
                if all(column is not None and prefix is None for _, column, prefix in entries)
            }
            if unique_indexes.get("PRIMARY") != (definition.primary_key,):
                raise SchemaCompatibilityError(f"{role} debe tener la PK simple indicada en el mapeo.")
            primary = actual[definition.primary_key]
            if primary["data_type"] not in _INTEGER_TYPES:
                raise SchemaCompatibilityError(f"La PK interna de {role} debe ser entera.")
            natural = tuple(definition.columns[field] for field in NATURAL_KEYS[role])
            if not any(set(index) == set(natural) and len(index) == len(natural) for index in unique_indexes.values()):
                raise SchemaCompatibilityError(f"Falta una clave UNIQUE completa para la identidad de {role}.")
            if any(actual[column]["nullable"] != "NO" for column in natural):
                raise SchemaCompatibilityError(f"La identidad natural de {role} no puede admitir NULL.")
            year_is_primary = role == "SEASON" and definition.primary_key == definition.columns["year"]
            if not year_is_primary and "auto_increment" not in primary["extra"].lower():
                raise SchemaCompatibilityError(f"La PK interna de {role} debe ser AUTO_INCREMENT.")
            for column, metadata in actual.items():
                if column not in needed and (
                    metadata["nullable"] == "NO" and metadata["default"] is None
                    and "auto_increment" not in metadata["extra"].lower()
                    and "generated" not in metadata["extra"].lower()
                ):
                    raise SchemaCompatibilityError(f"{role} contiene una columna obligatoria sin mapear.")
            for field in ("year", "season_id", "circuit_id", "round", "race_id", "driver_id", "constructor_id"):
                if field in definition.columns and actual[definition.columns[field]]["data_type"] not in _INTEGER_TYPES:
                    raise SchemaCompatibilityError(f"{role}.{field} debe mapear una columna entera.")
            for field, column in definition.columns.items():
                if "generated" in actual[column]["extra"].lower():
                    raise SchemaCompatibilityError(f"{role}.{field} no puede mapear una columna generada.")
            if "api_id" in definition.columns and actual[definition.columns["api_id"]]["data_type"] not in {"char", "varchar", "text"}:
                raise SchemaCompatibilityError(f"{role}.api_id debe conservar el identificador textual de la API.")
            self._columns[role] = actual

        points = self._columns["RESULT"][self.mapping.tables["RESULT"].columns["points"]]
        if points["data_type"] != "decimal":
            raise SchemaCompatibilityError("RESULT.points debe ser DECIMAL para conservar la precisión.")
        required_links = (
            ("RACE", "season_id", "SEASON"), ("RACE", "circuit_id", "CIRCUIT"),
            ("RESULT", "race_id", "RACE"), ("RESULT", "driver_id", "DRIVER"),
            ("RESULT", "constructor_id", "CONSTRUCTOR"),
        )
        for child_role, field, parent_role in required_links:
            child = self.mapping.tables[child_role]
            parent = self.mapping.tables[parent_role]
            expected = [(child.columns[field], parent.table, parent.primary_key)]
            if not any(table == child.table and links == expected for (table, _), links in foreign_keys.items()):
                raise SchemaCompatibilityError(f"Falta la FK de {child_role}.{field} a {parent_role}.")
        self._schema_validated = True

    def _require_ready(self) -> None:
        if not self._schema_validated:
            raise SchemaCompatibilityError("Debes validar el esquema antes de consultar o escribir registros del importador.")
        if not self._lock_acquired:
            raise RepositoryValidationError("Debes obtener el bloqueo de importación antes de escribir o verificar.")

    def _check_value(self, role: str, column: str, value: Any) -> None:
        metadata = self._columns[role][column]
        if value is None:
            if metadata["nullable"] != "YES":
                raise RepositoryValidationError(f"{role} no admite NULL en un campo proporcionado por la API.")
            return
        data_type = metadata["data_type"]
        if data_type == "decimal":
            if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
                raise RepositoryValidationError(f"{role} necesita Decimal o entero para una columna DECIMAL.")
            number = Decimal(value)
            precision, scale = metadata["precision"], metadata["scale"]
            if not number.is_finite() or precision is None or scale is None:
                raise RepositoryValidationError(f"{role} contiene un decimal inválido.")
            # No usar normalize()/abs() bajo el contexto Decimal global: una
            # columna MySQL puede tener más precisión que ese contexto.
            digits = list(number.as_tuple().digits)
            exponent = number.as_tuple().exponent
            while digits and digits[-1] == 0:
                digits.pop()
                exponent += 1
            if (digits and exponent < -int(scale)) or number.copy_abs() >= Decimal(f"1e{int(precision) - int(scale)}"):
                raise RepositoryValidationError(f"Un decimal de {role} no cabe exactamente en la precisión del esquema.")
            if "unsigned" in metadata["column_type"].lower() and number < 0:
                raise RepositoryValidationError(f"Un decimal negativo de {role} no cabe en una columna UNSIGNED.")
        elif data_type in _INTEGER_TYPES:
            if type(value) is not int:
                raise RepositoryValidationError(f"{role} necesita enteros para una columna entera.")
            bits = {"tinyint": 8, "smallint": 16, "mediumint": 24, "int": 32, "bigint": 64}[data_type]
            unsigned = "unsigned" in metadata["column_type"].lower()
            lower, upper = (0, 2**bits - 1) if unsigned else (-2 ** (bits - 1), 2 ** (bits - 1) - 1)
            if not lower <= value <= upper:
                raise RepositoryValidationError(f"Un entero de {role} excede la capacidad del esquema.")
        elif metadata["length"] is not None and len(str(value)) > metadata["length"]:
            raise RepositoryValidationError(f"Un texto de {role} excede la longitud del esquema.")

    @staticmethod
    def _same_value(old: Any, new: Any) -> bool:
        if old == new:
            return True
        if isinstance(old, str) and new is not None:
            return old == str(new)
        if isinstance(old, timedelta) and isinstance(new, time) and new.tzinfo is None:
            return old == timedelta(hours=new.hour, minutes=new.minute, seconds=new.second, microseconds=new.microsecond)
        return False

    def upsert(self, table_role: str, values: Mapping[str, Any]) -> UpsertOutcome:
        """Resolver la PK por la clave natural; insertar, actualizar o conservar."""
        self._require_ready()
        if table_role not in self.mapping.tables or not isinstance(values, Mapping):
            raise RepositoryValidationError("Tabla lógica o valores de upsert inválidos.")
        definition = self.mapping.tables[table_role]
        if set(values) - (REQUIRED_FIELDS[table_role] | OPTIONAL_FIELDS[table_role]):
            raise RepositoryValidationError(f"Campos lógicos desconocidos al guardar {table_role}.")
        identity = NATURAL_KEYS[table_role]
        if any(field not in values or values[field] is None for field in identity):
            raise RepositoryValidationError(f"Falta la identidad natural de {table_role}.")
        supplied = {field: value for field, value in values.items() if field in definition.columns}
        for field, value in supplied.items():
            self._check_value(table_role, definition.columns[field], value)
        names = list(definition.columns)
        selected = [definition.primary_key] + [definition.columns[field] for field in names]
        where = " AND ".join(f"{_quote(definition.columns[field])} = %s" for field in identity)
        with cursor(self.db) as query:
            query.execute(
                f"SELECT {', '.join(_quote(column) for column in selected)} "
                f"FROM {_quote(definition.table)} WHERE {where} FOR UPDATE",
                tuple(values[field] for field in identity),
            )
            rows = query.fetchall()
            if len(rows) > 1:
                raise RepositoryValidationError(f"Ya existen identidades duplicadas en {table_role}.")
            if rows:
                row = rows[0]
                primary_key = row[0]
                old = dict(zip(names, row[1:]))
                changed = {
                    field: value for field, value in supplied.items()
                    if definition.columns[field] != definition.primary_key and not self._same_value(old[field], value)
                }
                if not changed:
                    return UpsertOutcome(primary_key, "skipped")
                assignments = ", ".join(f"{_quote(definition.columns[field])} = %s" for field in changed)
                query.execute(
                    f"UPDATE {_quote(definition.table)} SET {assignments} WHERE {_quote(definition.primary_key)} = %s",
                    (*changed.values(), primary_key),
                )
                return UpsertOutcome(primary_key, "updated")

            # Una ausencia opcional nueva es NULL, nunca un default inventado.
            for field in definition.columns:
                if field not in supplied:
                    supplied[field] = None
                    self._check_value(table_role, definition.columns[field], None)
            columns = [definition.columns[field] for field in supplied]
            query.execute(
                f"INSERT INTO {_quote(definition.table)} ({', '.join(_quote(column) for column in columns)}) "
                f"VALUES ({', '.join('%s' for _ in columns)})",
                tuple(supplied.values()),
            )
            primary_field = next(
                (field for field, column in definition.columns.items() if column == definition.primary_key), None
            )
            primary_key = supplied[primary_field] if primary_field is not None else query.lastrowid
            if type(primary_key) is not int or primary_key < 1:
                raise RepositoryValidationError(f"No se pudo resolver la PK interna de {table_role}.")
            return UpsertOutcome(primary_key, "inserted")

    def verify_import(
        self,
        season_id: int,
        expected_rounds: set[int],
        expected_results: Mapping[tuple[int, str], tuple[str | None, Decimal | None]],
    ) -> VerificationReport:
        """Contrastar el estado transaccional con las identidades reales de la API."""
        self._require_ready()
        tables = self.mapping.tables

        def col(role: str, field: str, alias: str) -> str:
            return f"{alias}.{_quote(tables[role].columns[field])}"

        def pk(role: str, alias: str) -> str:
            return f"{alias}.{_quote(tables[role].primary_key)}"

        with cursor(self.db) as query:
            query.execute(
                f"SELECT {pk('RACE', 'r')}, {col('RACE', 'round', 'r')}, {col('RACE', 'season_id', 'r')}, "
                f"{pk('SEASON', 's')}, {col('SEASON', 'year', 's')}, {pk('CIRCUIT', 'c')}, {col('RACE', 'circuit_id', 'r')} "
                f"FROM {_quote(tables['RACE'].table)} r "
                f"LEFT JOIN {_quote(tables['SEASON'].table)} s ON {col('RACE', 'season_id', 'r')} = {pk('SEASON', 's')} "
                f"LEFT JOIN {_quote(tables['CIRCUIT'].table)} c ON {col('RACE', 'circuit_id', 'r')} = {pk('CIRCUIT', 'c')} "
                f"WHERE {col('RACE', 'season_id', 'r')} = %s",
                (season_id,),
            )
            race_rows = query.fetchall()
            actual_rounds = [row[1] for row in race_rows]
            if set(actual_rounds) != set(expected_rounds) or len(actual_rounds) != len(set(actual_rounds)):
                raise RepositoryValidationError("Las rondas guardadas no coinciden exactamente con el calendario de la API.")
            if any(
                saved_season != season_id or parent_season != season_id or year != 2024
                or (circuit_fk is not None and circuit_pk != circuit_fk)
                for _, _, saved_season, parent_season, year, circuit_pk, circuit_fk in race_rows
            ):
                raise RepositoryValidationError("Una carrera tiene una temporada o una referencia a circuito incorrecta.")
            query.execute(
                f"SELECT {col('RACE', 'round', 'r')}, {col('DRIVER', 'api_id', 'd')}, {col('CONSTRUCTOR', 'api_id', 'c')}, "
                f"{col('RESULT', 'race_id', 't')}, {col('RESULT', 'driver_id', 't')}, {col('RESULT', 'constructor_id', 't')}, "
                f"{pk('RACE', 'r')}, {pk('DRIVER', 'd')}, {pk('CONSTRUCTOR', 'c')}, {col('RESULT', 'points', 't')} "
                f"FROM {_quote(tables['RESULT'].table)} t "
                f"JOIN {_quote(tables['RACE'].table)} r ON {col('RESULT', 'race_id', 't')} = {pk('RACE', 'r')} "
                f"LEFT JOIN {_quote(tables['DRIVER'].table)} d ON {col('RESULT', 'driver_id', 't')} = {pk('DRIVER', 'd')} "
                f"LEFT JOIN {_quote(tables['CONSTRUCTOR'].table)} c ON {col('RESULT', 'constructor_id', 't')} = {pk('CONSTRUCTOR', 'c')} "
                f"WHERE {col('RACE', 'season_id', 'r')} = %s",
                (season_id,),
            )
            result_rows = query.fetchall()

        observed = {}
        for round_number, driver_api, constructor_api, race_fk, driver_fk, constructor_fk, race_pk, driver_pk, constructor_pk, points in result_rows:
            if race_fk != race_pk or driver_pk is None or driver_fk != driver_pk or (
                constructor_fk is not None and constructor_fk != constructor_pk
            ):
                raise RepositoryValidationError("Un resultado contiene una referencia foránea sin resolver.")
            key = (round_number, driver_api)
            if key in observed:
                raise RepositoryValidationError("Existen resultados duplicados para una carrera y un piloto.")
            observed[key] = (constructor_api, points)
        if set(observed) != set(expected_results):
            raise RepositoryValidationError("Las identidades de los resultados guardados no coinciden con la API.")
        if any(observed[key] != expected_results[key] for key in observed):
            raise RepositoryValidationError("Las atribuciones de constructor o los puntos no coinciden con la API.")
        return VerificationReport(tuple(sorted(actual_rounds)), len(observed), True, 0)
