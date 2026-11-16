"""Pruebas de configuración aisladas de credenciales y servicios locales."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import Mock

import pytest

import config
from config import ConfigurationError, DatabaseConfig, load_config


DB_VARIABLES = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Cada prueba restaura el entorno original, incluso al cargar dotenv."""
    for variable in DB_VARIABLES:
        monkeypatch.setenv(variable, "valor_temporal")
        monkeypatch.delenv(variable)


def test_reads_real_dotenv_file_with_utf8(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=db.local\nDB_PORT=3307\nDB_USER=analista\n"
        'DB_PASSWORD="contraseña_ficticia"\nDB_NAME=pruebas_f1\n',
        encoding="utf-8",
    )

    settings = load_config(env_file)

    assert settings == DatabaseConfig(
        host="db.local",
        port=3307,
        user="analista",
        password="contraseña_ficticia",
        database="pruebas_f1",
    )


def test_environment_takes_precedence_over_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=archivo.local\nDB_PORT=3307\nDB_USER=archivo\n"
        "DB_PASSWORD=clave_del_archivo\nDB_NAME=archivo_f1\n",
        encoding="utf-8",
    )
    values = {
        "DB_HOST": "entorno.local",
        "DB_PORT": "3308",
        "DB_USER": "entorno",
        "DB_PASSWORD": "clave_del_entorno",
        "DB_NAME": "entorno_f1",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = load_config(env_file)

    assert settings == DatabaseConfig(
        "entorno.local", 3308, "entorno", "clave_del_entorno", "entorno_f1"
    )


def test_defaults_require_only_explicit_password(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PASSWORD", "clave_ficticia")

    settings = load_config(tmp_path / "archivo_inexistente")

    assert settings == DatabaseConfig(
        "localhost", 3306, "root", "clave_ficticia", "f1_historical_database"
    )


def test_default_dotenv_path_is_root_relative_not_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PASSWORD", "clave_ficticia")
    dotenv_loader = Mock(return_value=False)
    monkeypatch.setattr(config, "load_dotenv", dotenv_loader)

    load_config()

    dotenv_loader.assert_called_once_with(
        dotenv_path=Path(config.__file__).resolve().parent / ".env",
        override=False,
        encoding="utf-8",
    )


def test_missing_password_reports_the_variable(tmp_path):
    with pytest.raises(ConfigurationError, match="Falta DB_PASSWORD"):
        load_config(tmp_path / ".env")


@pytest.mark.parametrize("password", ("your_password", " YOUR_PASSWORD "))
def test_placeholder_password_is_rejected(monkeypatch, tmp_path, password):
    monkeypatch.setenv("DB_PASSWORD", password)

    with pytest.raises(ConfigurationError, match="valor de ejemplo") as error:
        load_config(tmp_path / ".env")

    assert password not in str(error.value)


def test_explicit_empty_password_is_allowed(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DB_PASSWORD=\n", encoding="utf-8")

    assert load_config(env_file).password == ""


def test_password_whitespace_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PASSWORD", " clave ficticia ")

    assert load_config(tmp_path / ".env").password == " clave ficticia "


@pytest.mark.parametrize("port", ("", "abc", "3306.5", "0", "-1", "65536"))
def test_invalid_port_is_rejected_without_echoing_values(monkeypatch, tmp_path, port):
    monkeypatch.setenv("DB_PASSWORD", "clave_ficticia")
    monkeypatch.setenv("DB_PORT", port)

    with pytest.raises(ConfigurationError, match="DB_PORT") as error:
        load_config(tmp_path / ".env")

    assert "clave_ficticia" not in str(error.value)


@pytest.mark.parametrize("port", (1, 65535))
def test_boundary_ports_are_accepted(monkeypatch, tmp_path, port):
    monkeypatch.setenv("DB_PASSWORD", "clave_ficticia")
    monkeypatch.setenv("DB_PORT", str(port))

    assert load_config(tmp_path / ".env").port == port


@pytest.mark.parametrize("variable", ("DB_HOST", "DB_USER", "DB_NAME"))
@pytest.mark.parametrize("value", ("", " \t "))
def test_empty_connection_fields_are_rejected(monkeypatch, tmp_path, variable, value):
    monkeypatch.setenv("DB_PASSWORD", "clave_ficticia")
    monkeypatch.setenv(variable, value)

    with pytest.raises(ConfigurationError, match=variable):
        load_config(tmp_path / ".env")


def test_config_is_immutable_and_hides_password():
    settings = DatabaseConfig("localhost", 3306, "root", "clave_ficticia", "f1")

    assert "clave_ficticia" not in repr(settings)
    assert "password" not in repr(settings)
    assert "clave_ficticia" not in str(settings)
    with pytest.raises(FrozenInstanceError):
        settings.password = "otra_clave"


@pytest.mark.parametrize("port", (True, "3306", 3306.0, 0, 65536))
def test_direct_configuration_construction_validates_port(port):
    with pytest.raises(ConfigurationError, match="DB_PORT"):
        DatabaseConfig("localhost", port, "root", "clave_ficticia", "f1")
