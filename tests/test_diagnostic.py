"""Pruebas del diagnóstico MySQL sin servidor ni credenciales reales."""

from unittest.mock import MagicMock, Mock, call

import pytest

from config import ConfigurationError, DatabaseConfig
from database import diagnostic
from database.connection import DatabaseConnectionError, DatabaseError


# Nombres ficticios para pruebas, sin definir ni aprobar un esquema real.
TABLES = tuple(f"tabla_{letter}" for letter in "abcdef")


@pytest.fixture
def settings():
    return DatabaseConfig(
        host="localhost",
        port=3306,
        user="usuario_pruebas",
        password="secreto_ficticio",
        database="base_pruebas",
    )


@pytest.fixture
def session(monkeypatch, settings):
    """Simular los dos gestores de contexto y todas las respuestas de MySQL."""
    db = Mock()
    db_cursor = Mock()
    db_cursor.fetchone.side_effect = [(1,), (settings.database,)]
    db_cursor.fetchall.return_value = [(name,) for name in TABLES]

    connection_context = MagicMock()
    connection_context.__enter__.return_value = db
    connection_context.__exit__.return_value = False
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = db_cursor
    cursor_context.__exit__.return_value = False

    open_connection = Mock(return_value=connection_context)
    open_cursor = Mock(return_value=cursor_context)
    load_config = Mock(return_value=settings)
    monkeypatch.setattr(diagnostic, "connection", open_connection)
    monkeypatch.setattr(diagnostic, "cursor", open_cursor)
    monkeypatch.setattr(diagnostic, "load_config", load_config)
    return (
        db,
        db_cursor,
        connection_context,
        cursor_context,
        open_connection,
        open_cursor,
        load_config,
    )


def test_verifica_conexion_base_y_seis_tablas(settings, session):
    db, db_cursor, connection_context, cursor_context, connect, cursor, load = session

    report = diagnostic.run_diagnostic(settings, TABLES)

    assert report.ok
    assert report.database == settings.database
    assert report.tables == TABLES
    assert report.problems == ()
    connect.assert_called_once_with(settings)
    cursor.assert_called_once_with(db)
    load.assert_not_called()
    assert db_cursor.execute.call_args_list == [
        call("SELECT 1"),
        call("SELECT DATABASE()"),
        call(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = %s ORDER BY TABLE_NAME",
            (settings.database, "BASE TABLE"),
        ),
    ]
    cursor_context.__exit__.assert_called_once_with(None, None, None)
    connection_context.__exit__.assert_called_once_with(None, None, None)
    db.commit.assert_not_called()


def test_carga_configuracion_solo_si_no_se_proporciona(session):
    load = session[-1]

    assert diagnostic.run_diagnostic(expected_tables=TABLES).ok

    load.assert_called_once_with()


@pytest.mark.parametrize("result", [None, (0,), (2,), ()])
def test_select_uno_incorrecto_no_declara_exito(settings, session, result):
    db_cursor = session[1]
    db_cursor.fetchone.side_effect = [result]

    with pytest.raises(DatabaseError, match="SELECT 1"):
        diagnostic.run_diagnostic(settings, TABLES)

    db_cursor.execute.assert_called_once_with("SELECT 1")
    db_cursor.fetchall.assert_not_called()
    session[2].__exit__.assert_called_once()
    session[3].__exit__.assert_called_once()


def test_rechaza_una_base_seleccionada_distinta(settings, session):
    db_cursor = session[1]
    db_cursor.fetchone.side_effect = [(1,), ("otra_base",)]

    with pytest.raises(DatabaseError, match="no coincide con DB_NAME"):
        diagnostic.run_diagnostic(settings, TABLES)

    assert db_cursor.execute.call_count == 2
    db_cursor.fetchall.assert_not_called()
    session[2].__exit__.assert_called_once()
    session[3].__exit__.assert_called_once()


@pytest.mark.parametrize("visible", [(), TABLES[:-1], TABLES + ("tabla_extra",)])
def test_numero_de_tablas_incorrecto_impide_exito(settings, session, visible):
    session[1].fetchall.return_value = [(name,) for name in visible]

    report = diagnostic.run_diagnostic(settings, TABLES)

    assert not report.ok
    assert report.tables == visible
    assert any(f"se encontraron {len(visible)}" in item for item in report.problems)


def test_seis_tablas_con_nombre_incorrecto_no_son_el_esquema_esperado(settings, session):
    session[1].fetchall.return_value = [
        (name,) for name in TABLES[:-1] + ("tabla_desconocida",)
    ]

    report = diagnostic.run_diagnostic(settings, TABLES)

    assert not report.ok
    assert report.problems == ("Faltan tablas esperadas: tabla_f.",)


def test_sin_nombres_aprobados_no_confunde_inventario_con_verificacion(settings, session):
    report = diagnostic.run_diagnostic(settings)

    assert not report.ok
    assert report.tables == TABLES
    assert len(report.problems) == 1
    assert "--expected-tables" in report.problems[0]


@pytest.mark.parametrize(
    "expected",
    [(), TABLES[:-1], TABLES + ("tabla_g",), ("repetida",) * 6, TABLES[:-1] + (" ",)],
)
def test_valida_nombres_antes_de_cargar_configuracion_o_conectar(session, expected):
    with pytest.raises(ValueError, match="seis nombres de tabla distintos"):
        diagnostic.run_diagnostic(expected_tables=expected)

    session[-1].assert_not_called()
    session[4].assert_not_called()
    session[5].assert_not_called()


def test_nombre_de_base_se_transmite_como_parametro_sql(settings, session):
    malicious_name = "base'; DROP TABLE tabla_a; --"
    config = DatabaseConfig(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=malicious_name,
    )
    session[1].fetchone.side_effect = [(1,), (malicious_name,)]

    assert diagnostic.run_diagnostic(config, TABLES).ok

    query, parameters = session[1].execute.call_args.args
    assert malicious_name not in query
    assert parameters == (malicious_name, "BASE TABLE")


def test_main_acepta_seis_nombres_y_devuelve_cero(session, capsys):
    exit_code = diagnostic.main(["--expected-tables", *TABLES])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "SELECT 1: correctos" in output.out
    assert "Las seis tablas esperadas existen." in output.out
    assert output.err == ""
    assert "secreto_ficticio" not in output.out


def test_main_sin_nombres_informa_conexion_y_devuelve_error(session, capsys):
    exit_code = diagnostic.main([])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "SELECT 1: correctos" in output.out
    assert "--expected-tables" in output.err
    assert "Las seis tablas esperadas existen." not in output.out


def test_main_informa_tablas_ausentes_sin_declarar_exito(session, capsys):
    session[1].fetchall.return_value = []

    exit_code = diagnostic.main(["--expected-tables", *TABLES])

    output = capsys.readouterr()
    assert exit_code == 1
    assert "Tablas base visibles: ninguna" in output.out
    assert "se encontraron 0" in output.err
    assert "Faltan tablas esperadas" in output.err
    assert "Las seis tablas esperadas existen." not in output.out


@pytest.mark.parametrize(
    "error",
    [
        ConfigurationError("Falta DB_PASSWORD en el entorno o en .env."),
        DatabaseConnectionError("Comprueba DB_USER, DB_PASSWORD y los permisos de acceso."),
    ],
)
def test_main_errores_controlados_sin_traceback(monkeypatch, capsys, error):
    run_diagnostic = Mock(side_effect=error)
    monkeypatch.setattr(diagnostic, "run_diagnostic", run_diagnostic)

    assert diagnostic.main([]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == f"Error: {error}\n"
    assert "Traceback" not in output.err
    assert "secreto_ficticio" not in output.err


def test_main_rechaza_nombres_duplicados_sin_conectar(session, capsys):
    assert diagnostic.main(["--expected-tables", *("duplicada",) * 6]) == 1

    session[4].assert_not_called()
    assert "seis nombres de tabla distintos" in capsys.readouterr().err


def test_main_exige_exactamente_seis_argumentos(monkeypatch, capsys):
    run_diagnostic = Mock()
    monkeypatch.setattr(diagnostic, "run_diagnostic", run_diagnostic)

    with pytest.raises(SystemExit) as raised:
        diagnostic.main(["--expected-tables", *TABLES[:-1]])

    assert raised.value.code == 2
    run_diagnostic.assert_not_called()
    assert "--expected-tables" in capsys.readouterr().err
