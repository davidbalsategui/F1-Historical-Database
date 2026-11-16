"""Pruebas de repositorios contra un doble MySQL, sin servidor ni DDL reales."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import time, timedelta
from decimal import Decimal, localcontext
import json
import re

import mysql.connector
import pytest

from database.repositories import (
    MySQLRepository,
    RepositoryValidationError,
    SchemaCompatibilityError,
    SchemaMapping,
    TableMapping,
    TABLE_ROLES,
    NATURAL_KEYS,
)


# Este esquema es exclusivamente un doble de prueba; no propone el esquema real.
FIELDS = {
    "SEASON": ("year",),
    "DRIVER": ("api_id", "given_name", "family_name", "code", "permanent_number"),
    "CONSTRUCTOR": ("api_id", "name"),
    "CIRCUIT": ("api_id", "name", "latitude", "longitude"),
    "RACE": ("season_id", "circuit_id", "round", "name", "date", "time"),
    "RESULT": ("race_id", "driver_id", "constructor_id", "points", "position", "status"),
}


def sample_mapping():
    return SchemaMapping({
        role: TableMapping(role, "id_" + role.lower(), {field: field for field in FIELDS[role]})
        for role in TABLE_ROLES
    })


class FakeCursor:
    """Interpretar las operaciones probadas, conservando filas entre upserts."""

    def __init__(self, db):
        self.db = db
        self.rows = []
        self.lastrowid = None
        self.rowcount = 0
        self.closed = False

    def execute(self, statement, parameters=()):
        self.db.calls.append((statement, parameters))
        if self.db.fail_next is not None:
            error, self.db.fail_next = self.db.fail_next, None
            raise error
        if statement == "SELECT GET_LOCK(%s, %s)":
            self.rows = [(self.db.lock_result,)]
        elif statement == "SELECT @@SESSION.foreign_key_checks":
            self.rows = [(self.db.foreign_key_checks,)]
        elif "information_schema.TABLES" in statement:
            self.rows = deepcopy(self.db.tables)
        elif "information_schema.COLUMNS" in statement:
            self.rows = deepcopy(self.db.columns)
        elif "information_schema.STATISTICS" in statement:
            self.rows = deepcopy(self.db.indexes)
        elif "information_schema.KEY_COLUMN_USAGE" in statement:
            self.rows = deepcopy(self.db.foreign_keys)
        elif statement.endswith("FOR UPDATE"):
            table = re.search(r"FROM `([A-Za-z_]+)`", statement)[1]
            selected = re.findall(r"`([A-Za-z_]+)`", statement.split(" FROM ", 1)[0])
            keys = re.findall(r"`([A-Za-z_]+)` = %s", statement.split(" WHERE ", 1)[1])
            self.rows = [
                tuple(row.get(column) for column in selected)
                for row in self.db.data[table]
                if all(row[column] == value for column, value in zip(keys, parameters))
            ]
        elif statement.startswith("INSERT INTO"):
            table = re.search(r"INSERT INTO `([A-Za-z_]+)`", statement)[1]
            columns = re.findall(r"`([A-Za-z_]+)`", statement.split(" VALUES ", 1)[0])[1:]
            row = dict(zip(columns, parameters))
            primary = self.db.mapping.tables[table].primary_key
            if primary not in row:
                row[primary] = self.db.next_id
                self.db.next_id += 1
            self.lastrowid = row[primary]
            self.db.data[table].append(row)
            self.rowcount = 1
        elif statement.startswith("UPDATE"):
            table = re.search(r"UPDATE `([A-Za-z_]+)`", statement)[1]
            changed = re.findall(r"`([A-Za-z_]+)` = %s", statement.split(" SET ", 1)[1].split(" WHERE ", 1)[0])
            primary = self.db.mapping.tables[table].primary_key
            for row in self.db.data[table]:
                if row[primary] == parameters[-1]:
                    row.update(zip(changed, parameters[:-1]))
                    self.rowcount = 1
        elif "LEFT JOIN `SEASON`" in statement:
            self.rows = deepcopy(self.db.race_verification)
        elif "FROM `RESULT` t" in statement:
            self.rows = deepcopy(self.db.result_verification)
        else:
            raise AssertionError("Consulta no reconocida por el doble de prueba: " + statement)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        self.closed = True


class FakeDatabase:
    def __init__(self, mapping):
        self.mapping = mapping
        self.lock_result = 1
        self.foreign_key_checks = 1
        self.tables = [(role, "InnoDB", "BASE TABLE") for role in TABLE_ROLES]
        self.columns = []
        self.indexes = []
        self.foreign_keys = []
        self.calls = []
        self.cursors = []
        self.data = {role: [] for role in TABLE_ROLES}
        self.next_id = 100
        self.fail_next = None
        self.race_verification = [(300, 1, 2024, 2024, 2024, 400, 400)]
        self.result_verification = [(1, "piloto_ficticio", "equipo_ficticio", 300, 100, 200, 300, 100, 200, Decimal("25"))]
        for role, table in mapping.tables.items():
            self.columns.append((role, table.primary_key, "bigint", "bigint unsigned", "NO", None, "auto_increment", 20, 0, None))
            self.indexes.append((role, "PRIMARY", 1, table.primary_key, None))
            for field in table.columns:
                nullable = "NO" if field in NATURAL_KEYS[role] else "YES"
                if field == "points":
                    kind, full, precision, scale, length = "decimal", "decimal(10,3)", 10, 3, None
                elif field in {"latitude", "longitude"}:
                    kind, full, precision, scale, length = "decimal", "decimal(9,6)", 9, 6, None
                elif field in {"year", "season_id", "circuit_id", "round", "race_id", "driver_id", "constructor_id", "position", "permanent_number"}:
                    kind, full, precision, scale, length = "int", "int unsigned", 10, 0, None
                elif field in {"date", "time"}:
                    kind, full, precision, scale, length = field, field, None, None, None
                else:
                    kind, full, precision, scale, length = "varchar", "varchar(200)", None, None, 200
                self.columns.append((role, field, kind, full, nullable, None, "", precision, scale, length))
            self.indexes.extend((role, "natural", i, field, None) for i, field in enumerate(NATURAL_KEYS[role], 1))
        for child, field, parent in (
            ("RACE", "season_id", "SEASON"), ("RACE", "circuit_id", "CIRCUIT"),
            ("RESULT", "race_id", "RACE"), ("RESULT", "driver_id", "DRIVER"), ("RESULT", "constructor_id", "CONSTRUCTOR"),
        ):
            self.foreign_keys.append((child, "fk_" + field, field, parent, mapping.tables[parent].primary_key))

    def cursor(self, *, buffered):
        assert buffered
        query = FakeCursor(self)
        self.cursors.append(query)
        return query


@pytest.fixture
def mapping():
    return sample_mapping()


@pytest.fixture
def database(mapping):
    return FakeDatabase(mapping)


@pytest.fixture
def repository(database, mapping):
    repo = MySQLRepository(database, mapping)
    repo.acquire_import_lock()
    repo.validate_schema()
    database.calls.clear()
    return repo


def test_mapping_is_explicit_and_immutable(mapping):
    with pytest.raises(SchemaCompatibilityError):
        SchemaMapping({})
    with pytest.raises(TypeError):
        mapping.tables["SEASON"] = None
    with pytest.raises(TypeError):
        mapping.tables["SEASON"].columns["year"] = "otro"
    with pytest.raises(FrozenInstanceError):
        mapping.tables["SEASON"].table = "otra"


@pytest.mark.parametrize("name", ["tabla; DROP TABLE DRIVER", "base.tabla", "`tabla`", "", "a" * 65, "nombre con espacios"])
def test_mapping_refuses_sql_identifiers_that_cannot_be_quoted_safely(name):
    with pytest.raises(SchemaCompatibilityError):
        TableMapping(name, "id", {"year": "year"})


def test_mapping_copies_callers_dictionaries():
    columns = {"year": "year"}
    table = TableMapping("SEASON", "id", columns)
    columns["year"] = "modificado"
    assert table.columns["year"] == "year"


def test_mapping_file_requires_all_six_roles(mapping, tmp_path):
    path = tmp_path / "mapping.json"
    definitions = {
        role: {"table": definition.table, "primary_key": definition.primary_key, "columns": dict(definition.columns)}
        for role, definition in mapping.tables.items()
    }
    path.write_text(json.dumps({"tables": definitions}), encoding="utf-8")
    loaded = SchemaMapping.from_file(path)
    assert loaded == mapping
    del definitions["RESULT"]
    path.write_text(json.dumps({"tables": definitions}), encoding="utf-8")
    with pytest.raises(SchemaCompatibilityError):
        SchemaMapping.from_file(path)


@pytest.mark.parametrize("content", ["no es JSON", "[]", '{"tables": []}', '{"tables": {}, "password": "ejemplo"}'])
def test_invalid_mapping_file_is_controlled_and_does_not_echo_content(tmp_path, content):
    path = tmp_path / "mapping.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SchemaCompatibilityError) as raised:
        SchemaMapping.from_file(path)
    assert "password" not in str(raised.value)


def test_missing_mapping_file_has_clear_error(tmp_path):
    with pytest.raises(SchemaCompatibilityError, match="JSON de mapeo"):
        SchemaMapping.from_file(tmp_path / "ausente.json")


@pytest.mark.parametrize("change", ["missing_field", "unknown_field", "duplicate_table", "duplicate_column", "pk_is_api_id"])
def test_mapping_refuses_ambiguous_or_unsupported_fields(mapping, change):
    definitions = dict(mapping.tables)
    if change == "missing_field":
        definitions["SEASON"] = TableMapping("SEASON", "id_season", {"url": "url"})
    elif change == "unknown_field":
        definitions["SEASON"] = TableMapping("SEASON", "id_season", {"year": "year", "guess": "guess"})
    elif change == "duplicate_table":
        definitions["SEASON"] = TableMapping("DRIVER", "id_season", {"year": "year"})
    elif change == "pk_is_api_id":
        definitions["DRIVER"] = TableMapping("DRIVER", "api_id", {"api_id": "api_id"})
    else:
        with pytest.raises(SchemaCompatibilityError):
            TableMapping("SEASON", "id", {"year": "year", "url": "year"})
        return
    with pytest.raises(SchemaCompatibilityError):
        SchemaMapping(definitions)


def test_schema_validation_is_only_reading_metadata(database, mapping):
    repo = MySQLRepository(database, mapping)
    repo.validate_schema()
    assert all(query.startswith("SELECT ") for query, _ in database.calls)
    assert all(cur.closed for cur in database.cursors)
    assert all("DROP" not in query and "CREATE" not in query for query, _ in database.calls)


@pytest.mark.parametrize("problem", [
    "missing_table", "non_transactional", "view", "missing_column", "missing_unique",
    "prefix_unique", "nullable_identity", "missing_fk", "non_auto_pk", "non_integer_pk",
    "unmapped_required", "float_points", "numeric_api_id", "foreign_keys_disabled", "composite_pk",
])
def test_schema_preflight_refuses_conditions_that_break_integrity(database, mapping, problem):
    if problem == "missing_table":
        database.tables.pop()
    elif problem == "non_transactional":
        database.tables[0] = ("SEASON", "MyISAM", "BASE TABLE")
    elif problem == "view":
        database.tables[0] = ("SEASON", None, "VIEW")
    elif problem == "missing_column":
        database.columns = [row for row in database.columns if not (row[0] == "DRIVER" and row[1] == "api_id")]
    elif problem == "missing_unique":
        database.indexes = [row for row in database.indexes if not (row[0] == "DRIVER" and row[1] == "natural")]
    elif problem == "prefix_unique":
        database.indexes = [(*row[:4], 5) if row[0] == "DRIVER" and row[1] == "natural" else row for row in database.indexes]
    elif problem == "nullable_identity":
        database.columns = [(*row[:4], "YES", *row[5:]) if row[0] == "DRIVER" and row[1] == "api_id" else row for row in database.columns]
    elif problem == "missing_fk":
        database.foreign_keys.pop()
    elif problem == "non_auto_pk":
        database.columns = [(*row[:6], "", *row[7:]) if row[0] == "DRIVER" and row[1] == "id_driver" else row for row in database.columns]
    elif problem == "non_integer_pk":
        database.columns = [(*row[:2], "varchar", "varchar(20)", *row[4:]) if row[0] == "DRIVER" and row[1] == "id_driver" else row for row in database.columns]
    elif problem == "unmapped_required":
        database.columns.append(("DRIVER", "unknown_required", "int", "int", "NO", None, "", 10, 0, None))
    elif problem == "float_points":
        database.columns = [(*row[:2], "float", "float", *row[4:]) if row[0] == "RESULT" and row[1] == "points" else row for row in database.columns]
    elif problem == "numeric_api_id":
        database.columns = [(*row[:2], "int", "int", *row[4:]) if row[0] == "DRIVER" and row[1] == "api_id" else row for row in database.columns]
    elif problem == "foreign_keys_disabled":
        database.foreign_key_checks = 0
    else:
        database.indexes.append(("DRIVER", "PRIMARY", 2, "api_id", None))
    repo = MySQLRepository(database, mapping)
    with pytest.raises(SchemaCompatibilityError):
        repo.validate_schema()
    assert not repo._schema_validated
    assert not any(query.startswith(("INSERT", "UPDATE")) for query, _ in database.calls)


def test_writes_require_both_schema_validation_and_lock(database, mapping):
    repo = MySQLRepository(database, mapping)
    with pytest.raises(SchemaCompatibilityError):
        repo.upsert("SEASON", {"year": 2024})
    repo.validate_schema()
    with pytest.raises(RepositoryValidationError, match="bloqueo"):
        repo.upsert("SEASON", {"year": 2024})


@pytest.mark.parametrize("lock_result", [0, None])
def test_import_lock_failure_never_permits_a_write(database, mapping, lock_result):
    database.lock_result = lock_result
    repo = MySQLRepository(database, mapping)
    with pytest.raises(RepositoryValidationError):
        repo.acquire_import_lock()
    assert not repo._lock_acquired
    assert database.calls == [("SELECT GET_LOCK(%s, %s)", ("f1-historical-import-2024", 0))]


def test_upsert_resolves_internal_key_and_is_idempotent(repository, database):
    first = repository.upsert("DRIVER", {"api_id": "piloto_prueba", "given_name": "Nombre", "family_name": "Ficticio"})
    second = repository.upsert("DRIVER", {"api_id": "piloto_prueba", "given_name": "Nombre", "family_name": "Ficticio"})
    assert first.action == "inserted"
    assert second.action == "skipped"
    assert first.primary_key == second.primary_key == 100
    assert len(database.data["DRIVER"]) == 1
    assert database.data["DRIVER"][0]["code"] is None
    assert sum(query.startswith("INSERT") for query, _ in database.calls) == 1
    assert all("COMMIT" not in query and "ROLLBACK" not in query for query, _ in database.calls)


def test_physical_column_names_are_taken_from_mapping_instead_of_assumed(mapping):
    tables = dict(mapping.tables)
    tables["DRIVER"] = TableMapping(
        "DRIVER", "driver_surrogate_id", {"api_id": "external_driver_key", "given_name": "first_name"}
    )
    mapped = SchemaMapping(tables)
    database = FakeDatabase(mapped)
    rename = {"api_id": "external_driver_key", "given_name": "first_name"}
    database.columns = [
        (row[0], rename.get(row[1], row[1]), *row[2:]) if row[0] == "DRIVER" else row
        for row in database.columns
    ]
    database.indexes = [
        (*row[:3], rename.get(row[3], row[3]), row[4]) if row[0] == "DRIVER" else row
        for row in database.indexes
    ]
    repository = MySQLRepository(database, mapped)
    repository.acquire_import_lock()
    repository.validate_schema()
    inserted = repository.upsert("DRIVER", {"api_id": "prueba", "given_name": "Ejemplo"})
    repeated = repository.upsert("DRIVER", {"api_id": "prueba", "given_name": "Ejemplo"})
    assert inserted.primary_key == repeated.primary_key
    assert repeated.action == "skipped"
    assert database.data["DRIVER"] == [{"driver_surrogate_id": 100, "external_driver_key": "prueba", "first_name": "Ejemplo"}]


def test_season_may_use_its_year_as_primary_key_without_auto_increment(mapping):
    tables = dict(mapping.tables)
    tables["SEASON"] = TableMapping("SEASON", "year", {"year": "year"})
    mapped = SchemaMapping(tables)
    database = FakeDatabase(mapped)
    repository = MySQLRepository(database, mapped)
    repository.acquire_import_lock()
    repository.validate_schema()
    inserted = repository.upsert("SEASON", {"year": 2024})
    repeated = repository.upsert("SEASON", {"year": 2024})
    assert inserted.primary_key == repeated.primary_key == 2024
    assert inserted.action == "inserted" and repeated.action == "skipped"


def test_upsert_updates_only_changed_provided_columns(repository, database):
    first = repository.upsert("DRIVER", {"api_id": "prueba", "given_name": "A", "code": "TES"})
    database.calls.clear()
    changed = repository.upsert("DRIVER", {"api_id": "prueba", "given_name": "B"})
    assert changed.primary_key == first.primary_key
    assert changed.action == "updated"
    assert database.data["DRIVER"][0]["code"] == "TES"
    updates = [(query, params) for query, params in database.calls if query.startswith("UPDATE")]
    assert len(updates) == 1
    assert "`given_name` = %s" in updates[0][0]
    assert "`code`" not in updates[0][0]
    assert updates[0][1] == ("B", first.primary_key)


def test_explicit_null_clears_nullable_value_but_zero_points_remains_zero(repository, database):
    first = repository.upsert("RESULT", {"race_id": 10, "driver_id": 20, "constructor_id": 30, "points": Decimal("0"), "status": "observado"})
    changed = repository.upsert("RESULT", {"race_id": 10, "driver_id": 20, "constructor_id": None, "points": None, "status": None})
    assert first.action == "inserted" and changed.action == "updated"
    assert database.data["RESULT"][0]["points"] is None
    assert database.data["RESULT"][0]["constructor_id"] is None


def test_all_values_are_parameters_including_injection_text(repository, database):
    source_id = "pilot'; DROP TABLE DRIVER; --"
    outcome = repository.upsert("DRIVER", {"api_id": source_id, "given_name": "O'Prueba"})
    assert outcome.action == "inserted"
    for query, _ in database.calls:
        assert source_id not in query and "O'Prueba" not in query
    assert any(source_id in params for _, params in database.calls)


def test_existing_duplicate_natural_identity_fails_without_updating(repository, database):
    database.data["DRIVER"] = [
        {"id_driver": 1, "api_id": "igual"}, {"id_driver": 2, "api_id": "igual"}
    ]
    with pytest.raises(RepositoryValidationError, match="duplicadas"):
        repository.upsert("DRIVER", {"api_id": "igual"})
    assert not any(query.startswith(("INSERT", "UPDATE")) for query, _ in database.calls)


@pytest.mark.parametrize("points", [Decimal("0"), Decimal("1.25"), Decimal("1.2500"), Decimal("-0.5"), None])
def test_points_keep_decimal_precision_and_legitimate_null(repository, database, points):
    repository.upsert("RESULT", {"race_id": 1, "driver_id": 2, "constructor_id": None, "points": points})
    assert database.data["RESULT"][0]["points"] == points


@pytest.mark.parametrize("points", [Decimal("0.0001"), Decimal("10000000"), Decimal("NaN"), Decimal("Infinity"), 0.1, "0.1"])
def test_unrepresentable_points_fail_without_silent_rounding(repository, database, points):
    with pytest.raises(RepositoryValidationError):
        repository.upsert("RESULT", {"race_id": 1, "driver_id": 2, "constructor_id": None, "points": points})
    assert not database.calls


def test_decimal_validation_is_not_rounded_by_python_global_context(repository):
    points_metadata = repository._columns["RESULT"]["points"]
    points_metadata.update(precision=65, scale=0)
    value = Decimal("9" * 65)
    with localcontext() as context:
        context.prec = 8
        repository._check_value("RESULT", "points", value)


def test_existing_unsigned_decimal_is_compatible_but_negative_values_are_rejected(database, mapping):
    database.columns = [
        (*row[:3], "decimal(10,3) unsigned", *row[4:])
        if row[0] == "RESULT" and row[1] == "points" else row
        for row in database.columns
    ]
    repository = MySQLRepository(database, mapping)
    repository.acquire_import_lock()
    repository.validate_schema()
    repository.upsert("RESULT", {"race_id": 1, "driver_id": 2, "constructor_id": None, "points": Decimal("0.5")})
    with pytest.raises(RepositoryValidationError, match="UNSIGNED"):
        repository.upsert("RESULT", {"race_id": 1, "driver_id": 3, "constructor_id": None, "points": Decimal("-0.5")})


def test_unmapped_optional_fields_are_ignored_but_unknown_fields_are_not(repository, database):
    repository.upsert("DRIVER", {"api_id": "prueba", "nationality": "Ejemplo"})
    assert "nationality" not in database.data["DRIVER"][0]
    with pytest.raises(RepositoryValidationError, match="desconocidos"):
        repository.upsert("DRIVER", {"api_id": "prueba", "typo": "desconocido"})


@pytest.mark.parametrize("identity", [{}, {"api_id": None}])
def test_missing_identity_never_creates_a_record(repository, identity):
    with pytest.raises(RepositoryValidationError, match="identidad"):
        repository.upsert("DRIVER", identity)


def test_required_mapped_field_cannot_be_invented_from_mysql_default(repository, database):
    repository._columns["DRIVER"]["given_name"].update(nullable="NO", default="inventado")
    with pytest.raises(RepositoryValidationError, match="NULL"):
        repository.upsert("DRIVER", {"api_id": "prueba"})
    assert not database.data["DRIVER"]


def test_mysql_time_and_varchar_presentation_numbers_do_not_force_updates(repository, database):
    repository._columns["DRIVER"]["permanent_number"].update(data_type="varchar", column_type="varchar(10)", length=10)
    database.data["DRIVER"] = [{"id_driver": 1, "api_id": "prueba", "permanent_number": "99"}]
    result = repository.upsert("DRIVER", {"api_id": "prueba", "permanent_number": 99})
    assert result.action == "skipped"
    assert repository._same_value(timedelta(hours=15), time(15))


@pytest.mark.parametrize("value", [-1, 2**32, True, "1"])
def test_integer_ranges_prevent_mysql_truncation(repository, value):
    with pytest.raises(RepositoryValidationError):
        repository.upsert("RACE", {"season_id": 1, "circuit_id": 2, "round": value})


def test_driver_text_capacity_is_checked_before_dml(repository, database):
    with pytest.raises(RepositoryValidationError, match="longitud"):
        repository.upsert("DRIVER", {"api_id": "a" * 201})
    assert not database.calls


def test_driver_errors_are_translated_and_resources_close(repository, database):
    database.fail_next = mysql.connector.Error("password_ficticio SQL privado", errno=2013)
    with pytest.raises(Exception) as raised:
        repository.upsert("DRIVER", {"api_id": "prueba"})
    assert "password_ficticio" not in str(raised.value)
    assert "SQL privado" not in str(raised.value)
    assert database.cursors[-1].closed


def test_final_verification_checks_rounds_references_identity_and_exact_points(repository, database):
    report = repository.verify_import(2024, {1}, {(1, "piloto_ficticio"): ("equipo_ficticio", Decimal("25"))})
    assert report.rounds == (1,)
    assert report.result_count == 1
    assert report.foreign_keys_ok
    assert report.duplicates == 0
    assert all(params == (2024,) for _, params in database.calls)


@pytest.mark.parametrize("problem", ["missing_round", "extra_round", "duplicate_round", "wrong_season", "missing_circuit", "missing_driver", "missing_constructor", "duplicate_result", "extra_result", "wrong_points", "wrong_constructor"])
def test_final_verification_refuses_broken_or_extra_data(repository, database, problem):
    if problem == "missing_round":
        database.race_verification.clear()
    elif problem == "extra_round":
        database.race_verification.append((301, 2, 2024, 2024, 2024, 400, 400))
    elif problem == "duplicate_round":
        database.race_verification *= 2
    elif problem == "wrong_season":
        database.race_verification[0] = (300, 1, 2024, 2024, 2023, 400, 400)
    elif problem == "missing_circuit":
        database.race_verification[0] = (300, 1, 2024, 2024, 2024, None, 400)
    elif problem == "missing_driver":
        row = list(database.result_verification[0]); row[7] = None
        database.result_verification[0] = tuple(row)
    elif problem == "missing_constructor":
        row = list(database.result_verification[0]); row[8] = None
        database.result_verification[0] = tuple(row)
    elif problem == "duplicate_result":
        database.result_verification *= 2
    elif problem == "extra_result":
        row = list(database.result_verification[0]); row[1] = "otro_piloto"
        database.result_verification.append(tuple(row))
    elif problem == "wrong_points":
        row = list(database.result_verification[0]); row[-1] = Decimal("0")
        database.result_verification[0] = tuple(row)
    else:
        row = list(database.result_verification[0]); row[2] = "otro_equipo"
        database.result_verification[0] = tuple(row)
    with pytest.raises(RepositoryValidationError):
        repository.verify_import(2024, {1}, {(1, "piloto_ficticio"): ("equipo_ficticio", Decimal("25"))})


def test_null_constructor_is_valid_only_when_it_remains_null(repository, database):
    row = list(database.result_verification[0])
    row[2] = row[5] = row[8] = None
    row[-1] = None
    database.result_verification[0] = tuple(row)
    report = repository.verify_import(2024, {1}, {(1, "piloto_ficticio"): (None, None)})
    assert report.result_count == 1 and report.foreign_keys_ok
