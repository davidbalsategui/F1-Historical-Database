"""Conexiones MySQL y transacciones sin efectos secundarios al importar."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import mysql.connector
from mysql.connector import Error as ConnectorError
from mysql.connector.abstracts import MySQLConnectionAbstract

from config import DatabaseConfig, load_config


class DatabaseError(RuntimeError):
    """Error de MySQL seguro para mostrar sin SQL ni credenciales."""


class DatabaseConnectionError(DatabaseError):
    """No se ha podido establecer la conexión con MySQL."""


def _safe_error(error: ConnectorError, operation: str) -> str:
    """No incorporar mensajes del servidor: pueden contener SQL o secretos."""
    advice = {
        1044: "Comprueba los permisos del usuario sobre DB_NAME.",
        1045: "Comprueba DB_USER, DB_PASSWORD y los permisos de acceso.",
        1049: "DB_NAME no existe. Revisa el esquema aprobado en MySQL Workbench.",
        2002: "Comprueba el servicio MySQL, DB_HOST y DB_PORT.",
        2003: "Comprueba el servicio MySQL, DB_HOST y DB_PORT.",
        2005: "No se puede resolver DB_HOST. Comprueba el nombre del servidor.",
        2006: "Se perdió la conexión con MySQL. Comprueba el servicio y la red.",
        2013: "Se perdió la conexión con MySQL. Comprueba el servicio y la red.",
    }
    code = error.errno
    label = f" (código MySQL {code})" if isinstance(code, int) else ""
    detail = advice.get(code, "Comprueba la disponibilidad y los permisos de MySQL.")
    return f"No se pudo {operation}{label}. {detail}"


def create_connection(config: DatabaseConfig | None = None) -> MySQLConnectionAbstract:
    """Abrir MySQL; quien llama debe cerrar la conexión, preferiblemente con connection()."""
    settings = config if config is not None else load_config()
    try:
        return mysql.connector.connect(
            host=settings.host,
            port=settings.port,
            user=settings.user,
            password=settings.password,
            database=settings.database,
            autocommit=False,
            connection_timeout=5,
            charset="utf8mb4",
        )
    except ConnectorError as error:
        raise DatabaseConnectionError(_safe_error(error, "conectar con MySQL")) from None


@contextmanager
def _managed_resource(resource: Any, label: str) -> Iterator[Any]:
    """Cerrar siempre, conservando el error original si también falla el cierre."""
    failed = False
    try:
        yield resource
    except ConnectorError as error:
        failed = True
        raise DatabaseError(_safe_error(error, "completar la operación")) from None
    except BaseException:
        failed = True
        raise
    finally:
        try:
            resource.close()
        except ConnectorError as error:
            if not failed:
                raise DatabaseError(_safe_error(error, f"cerrar {label}")) from None


@contextmanager
def connection(config: DatabaseConfig | None = None) -> Iterator[MySQLConnectionAbstract]:
    """Cerrar la conexión al salir; no confirmar cambios automáticamente."""
    with _managed_resource(create_connection(config), "la conexión") as db:
        yield db


@contextmanager
def cursor(db: MySQLConnectionAbstract) -> Iterator[Any]:
    """Crear un cursor con resultados almacenados y cerrarlo incluso si hay errores."""
    try:
        db_cursor = db.cursor(buffered=True)
    except ConnectorError as error:
        raise DatabaseError(_safe_error(error, "crear el cursor")) from None
    with _managed_resource(db_cursor, "el cursor") as db_cursor:
        yield db_cursor


@contextmanager
def transaction(config: DatabaseConfig | None = None) -> Iterator[MySQLConnectionAbstract]:
    """Transacción independiente: commit al terminar, rollback ante cualquier excepción.

    Usar tablas transaccionales (por ejemplo, InnoDB). No ejecutar DDL dentro de
    este bloque: MySQL puede confirmar implícitamente esas sentencias.
    """
    with connection(config) as db:
        db.start_transaction()
        try:
            yield db
            db.commit()
        except BaseException as error:
            failure = (
                DatabaseError(_safe_error(error, "completar la transacción"))
                if isinstance(error, ConnectorError)
                else error
            )
            try:
                db.rollback()
            except ConnectorError:
                failure.add_note(
                    "También falló el rollback; no se puede asegurar el resultado "
                    "de la transacción. Se intentará cerrar la conexión."
                )
            if failure is not error:
                raise failure from None
            raise
