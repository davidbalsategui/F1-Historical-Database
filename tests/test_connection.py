"""Pruebas de conexiones y transacciones sin acceder a un servidor MySQL."""

from unittest.mock import Mock
import traceback

import mysql.connector
import pytest

from config import DatabaseConfig
from database import connection as db


@pytest.fixture
def settings():
    return DatabaseConfig(
        host="localhost",
        port=3306,
        user="usuario_prueba",
        password="clave_ficticia",
        database="f1_historical_database",
    )


@pytest.fixture
def connector(monkeypatch):
    client = Mock(name="conexion_mysql")
    connect = Mock(return_value=client)
    monkeypatch.setattr(db.mysql.connector, "connect", connect)
    return connect, client


def connector_error(errno=2013):
    return mysql.connector.Error(
        msg="Error con clave_ficticia y SELECT dato_privado", errno=errno
    )


def assert_sanitized(error):
    rendered = "".join(traceback.format_exception(error))
    assert "clave_ficticia" not in rendered
    assert "dato_privado" not in rendered


def test_create_connection_passes_credentials_and_safe_options(settings, connector):
    connect, client = connector

    assert db.create_connection(settings) is client

    connect.assert_called_once_with(
        host="localhost",
        port=3306,
        user="usuario_prueba",
        password="clave_ficticia",
        database="f1_historical_database",
        autocommit=False,
        connection_timeout=5,
        charset="utf8mb4",
    )


def test_create_connection_loads_configuration_only_when_called(
    settings, connector, monkeypatch
):
    connect, client = connector
    load = Mock(return_value=settings)
    monkeypatch.setattr(db, "load_config", load)

    assert db.create_connection() is client

    load.assert_called_once_with()
    assert connect.call_args.kwargs["database"] == settings.database


@pytest.mark.parametrize("errno", [1045, 1049, 2002, 2003, 1044, 9999])
def test_connection_errors_are_actionable_and_hide_driver_details(
    settings, connector, errno
):
    connect, _ = connector
    connect.side_effect = connector_error(errno)

    with pytest.raises(db.DatabaseConnectionError) as raised:
        db.create_connection(settings)

    assert str(errno) in str(raised.value)
    assert_sanitized(raised.value)


def test_connection_context_closes_after_success(settings, connector):
    _, client = connector

    with db.connection(settings) as active:
        assert active is client
        client.close.assert_not_called()

    client.close.assert_called_once_with()
    client.commit.assert_not_called()


def test_connection_context_preserves_application_error(settings, connector):
    _, client = connector
    failure = ValueError("Dato no válido")

    with pytest.raises(ValueError) as raised:
        with db.connection(settings):
            raise failure

    assert raised.value is failure
    client.close.assert_called_once_with()


def test_connection_context_translates_driver_error(settings, connector):
    _, client = connector

    with pytest.raises(db.DatabaseError) as raised:
        with db.connection(settings):
            raise connector_error()

    assert_sanitized(raised.value)
    client.close.assert_called_once_with()


def test_close_error_is_reported_when_there_is_no_previous_error(settings, connector):
    _, client = connector
    client.close.side_effect = connector_error()

    with pytest.raises(db.DatabaseError) as raised:
        with db.connection(settings):
            pass

    assert_sanitized(raised.value)
    client.close.assert_called_once_with()


def test_close_error_does_not_replace_application_error(settings, connector):
    _, client = connector
    client.close.side_effect = connector_error()
    failure = ValueError("Error original")

    with pytest.raises(ValueError) as raised:
        with db.connection(settings):
            raise failure

    assert raised.value is failure
    client.close.assert_called_once_with()


def test_cursor_is_buffered_and_closes():
    client = Mock()
    sql_cursor = client.cursor.return_value

    with db.cursor(client) as active:
        assert active is sql_cursor
        sql_cursor.close.assert_not_called()

    client.cursor.assert_called_once_with(buffered=True)
    sql_cursor.close.assert_called_once_with()


def test_cursor_creation_error_is_sanitized():
    client = Mock()
    client.cursor.side_effect = connector_error()

    with pytest.raises(db.DatabaseError) as raised:
        with db.cursor(client):
            pytest.fail("El cursor no llegó a abrirse")

    assert_sanitized(raised.value)


def test_cursor_execution_error_closes_and_is_sanitized():
    client = Mock()
    sql_cursor = client.cursor.return_value
    sql_cursor.execute.side_effect = connector_error()

    with pytest.raises(db.DatabaseError) as raised:
        with db.cursor(client) as active:
            active.execute("SELECT %s", ("dato_privado",))

    sql_cursor.close.assert_called_once_with()
    assert_sanitized(raised.value)


def test_cursor_close_error_is_sanitized():
    client = Mock()
    client.cursor.return_value.close.side_effect = connector_error()

    with pytest.raises(db.DatabaseError) as raised:
        with db.cursor(client):
            pass

    assert_sanitized(raised.value)


def test_cursor_close_does_not_replace_application_error():
    client = Mock()
    client.cursor.return_value.close.side_effect = connector_error()
    failure = ValueError("Error original del cursor")

    with pytest.raises(ValueError) as raised:
        with db.cursor(client):
            raise failure

    assert raised.value is failure
    client.cursor.return_value.close.assert_called_once_with()


def test_transaction_starts_commits_and_closes_in_order(settings, connector):
    _, client = connector
    actions = Mock()
    actions.attach_mock(client.start_transaction, "inicio")
    actions.attach_mock(client.commit, "confirmacion")
    actions.attach_mock(client.close, "cierre")

    with db.transaction(settings) as active:
        assert active is client
        actions.operacion()

    assert [call[0] for call in actions.mock_calls] == [
        "inicio",
        "operacion",
        "confirmacion",
        "cierre",
    ]
    client.rollback.assert_not_called()


@pytest.mark.parametrize("failure", [ValueError("Dato no válido"), KeyboardInterrupt()])
def test_transaction_rolls_back_and_preserves_body_error(settings, connector, failure):
    _, client = connector

    with pytest.raises(type(failure)) as raised:
        with db.transaction(settings):
            raise failure

    assert raised.value is failure
    client.rollback.assert_called_once_with()
    client.commit.assert_not_called()
    client.close.assert_called_once_with()


def test_transaction_driver_error_rolls_back_and_hides_details(settings, connector):
    _, client = connector

    with pytest.raises(db.DatabaseError) as raised:
        with db.transaction(settings):
            raise connector_error()

    client.rollback.assert_called_once_with()
    client.commit.assert_not_called()
    client.close.assert_called_once_with()
    assert_sanitized(raised.value)


def test_commit_failure_rolls_back_and_closes(settings, connector):
    _, client = connector
    client.commit.side_effect = connector_error()
    actions = Mock()
    actions.attach_mock(client.commit, "confirmacion")
    actions.attach_mock(client.rollback, "reversion")
    actions.attach_mock(client.close, "cierre")

    with pytest.raises(db.DatabaseError) as raised:
        with db.transaction(settings):
            pass

    assert [call[0] for call in actions.mock_calls] == [
        "confirmacion",
        "reversion",
        "cierre",
    ]
    assert_sanitized(raised.value)


def test_rollback_and_close_failures_preserve_original_error(settings, connector):
    _, client = connector
    client.rollback.side_effect = connector_error()
    client.close.side_effect = connector_error()
    failure = ValueError("Error original de la operación")

    with pytest.raises(ValueError) as raised:
        with db.transaction(settings):
            raise failure

    assert raised.value is failure
    client.rollback.assert_called_once_with()
    client.close.assert_called_once_with()


@pytest.mark.parametrize("failure_at", ["body", "commit"])
def test_rollback_failure_keeps_warning_and_hides_driver_message(
    settings, connector, failure_at
):
    _, client = connector
    original = connector_error(2013)
    client.rollback.side_effect = connector_error(2006)
    if failure_at == "commit":
        client.commit.side_effect = original

    with pytest.raises(db.DatabaseError) as raised:
        with db.transaction(settings):
            if failure_at == "body":
                raise original

    assert_sanitized(raised.value)
    assert any(
        "falló el rollback" in note
        for note in getattr(raised.value, "__notes__", ())
    )
    client.rollback.assert_called_once_with()
    client.close.assert_called_once_with()


def test_interrupted_commit_rolls_back_and_closes(settings, connector):
    _, client = connector
    interruption = KeyboardInterrupt()
    client.commit.side_effect = interruption

    with pytest.raises(KeyboardInterrupt) as raised:
        with db.transaction(settings):
            pass

    assert raised.value is interruption
    client.rollback.assert_called_once_with()
    client.close.assert_called_once_with()


def test_start_failure_closes_without_committing(settings, connector):
    _, client = connector
    client.start_transaction.side_effect = connector_error()

    with pytest.raises(db.DatabaseError) as raised:
        with db.transaction(settings):
            pytest.fail("La transacción no llegó a iniciarse")

    client.commit.assert_not_called()
    client.close.assert_called_once_with()
    assert_sanitized(raised.value)


def test_transaction_connection_failure_does_not_open_transaction(settings, connector):
    connect, client = connector
    connect.side_effect = connector_error(2003)

    with pytest.raises(db.DatabaseConnectionError):
        with db.transaction(settings):
            pytest.fail("La conexión no llegó a abrirse")

    client.start_transaction.assert_not_called()
    client.commit.assert_not_called()
