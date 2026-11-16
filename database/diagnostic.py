"""Diagnóstico de solo lectura: python -m database.diagnostic."""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import sys

from config import ConfigurationError, DatabaseConfig, load_config
from database.connection import DatabaseError, connection, cursor


EXPECTED_TABLE_COUNT = 6


@dataclass(frozen=True)
class DiagnosticReport:
    """Resultado sin credenciales; una lista vacía de problemas indica éxito."""

    database: str
    tables: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def run_diagnostic(
    config: DatabaseConfig | None = None,
    expected_tables: Sequence[str] | None = None,
) -> DiagnosticReport:
    """Verificar conexión, base seleccionada y tablas sin ejecutar schema.sql.

    Los seis nombres deben proceder del esquema aprobado. Sin esa lista se
    informa del inventario, pero no se declara verificada su identidad.
    """
    expected = tuple(expected_tables) if expected_tables is not None else None
    if expected is not None and (
        len(expected) != EXPECTED_TABLE_COUNT
        or len(set(expected)) != EXPECTED_TABLE_COUNT
        or any(not name.strip() for name in expected)
    ):
        raise ValueError("Indica exactamente seis nombres de tabla distintos y no vacíos.")

    settings = config if config is not None else load_config()
    with connection(settings) as db, cursor(db) as db_cursor:
        db_cursor.execute("SELECT 1")
        if db_cursor.fetchone() != (1,):
            raise DatabaseError("SELECT 1 no devolvió el resultado esperado.")

        # Conectar seleccionando DB_NAME ya requiere que esa base exista.
        db_cursor.execute("SELECT DATABASE()")
        if db_cursor.fetchone() != (settings.database,):
            raise DatabaseError("La base seleccionada no coincide con DB_NAME.")

        db_cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = %s ORDER BY TABLE_NAME",
            (settings.database, "BASE TABLE"),
        )
        tables = tuple(row[0] for row in db_cursor.fetchall())

    problems = []
    if len(tables) != EXPECTED_TABLE_COUNT:
        problems.append(
            f"Se esperaban seis tablas base visibles y se encontraron {len(tables)}. "
            "Comprueba el esquema aprobado y los permisos del usuario."
        )
    if expected is None:
        problems.append(
            "No se han indicado los seis nombres de tabla aprobados. "
            "Utiliza --expected-tables para verificar su identidad."
        )
    else:
        missing = sorted(set(expected) - set(tables))
        if missing:
            problems.append("Faltan tablas esperadas: " + ", ".join(missing) + ".")
    return DiagnosticReport(settings.database, tables, tuple(problems))


def main(argv: Sequence[str] | None = None) -> int:
    """Devolver 0 solo si conexión, base y seis tablas están verificadas."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected-tables",
        nargs=EXPECTED_TABLE_COUNT,
        metavar="TABLA",
        help="Seis nombres distintos del esquema aprobado; no crea ni modifica tablas.",
    )
    args = parser.parse_args(argv)
    try:
        report = run_diagnostic(expected_tables=args.expected_tables)
    except (ConfigurationError, DatabaseError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print("Conexión MySQL y SELECT 1: correctos.")
    print(f"Base de datos verificada: {report.database}")
    print("Tablas base visibles: " + (", ".join(report.tables) or "ninguna"))
    for problem in report.problems:
        print(f"Error: {problem}", file=sys.stderr)
    if report.ok:
        print("Las seis tablas esperadas existen.")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
