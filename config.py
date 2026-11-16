"""Configuración MySQL desde el entorno y un archivo .env local."""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv


DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"


class ConfigurationError(ValueError):
    """La configuración necesaria falta o contiene un valor inválido."""


@dataclass(frozen=True)
class DatabaseConfig:
    """Parámetros de conexión; la contraseña queda fuera de la representación."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str

    def __post_init__(self) -> None:
        for variable, value in (
            ("DB_HOST", self.host),
            ("DB_USER", self.user),
            ("DB_NAME", self.database),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(f"{variable} no puede estar vacío.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ConfigurationError("DB_PORT debe ser un entero entre 1 y 65535.")
        if not isinstance(self.password, str):
            raise ConfigurationError("Falta DB_PASSWORD en el entorno o en .env.")
        if self.password.strip().casefold() == "your_password":
            raise ConfigurationError(
                "DB_PASSWORD conserva el valor de ejemplo; configura la contraseña real."
            )


def load_config(env_file: str | Path | None = None) -> DatabaseConfig:
    """Carga .env sin sustituir variables ya definidas en el entorno.

    La ruta predeterminada pertenece a la raíz del proyecto, independientemente
    del directorio de ejecución. DB_PASSWORD debe estar definido; una cadena
    vacía explícita permite una cuenta local de MySQL sin contraseña. Nunca se
    usa .env.example como fuente de configuración.
    """
    load_dotenv(
        dotenv_path=DEFAULT_ENV_FILE if env_file is None else env_file,
        override=False,
        encoding="utf-8",
    )

    password = os.environ.get("DB_PASSWORD")
    if password is None:
        raise ConfigurationError("Falta DB_PASSWORD en el entorno o en .env.")

    try:
        port = int(os.environ.get("DB_PORT", "3306"))
    except ValueError:
        raise ConfigurationError(
            "DB_PORT debe ser un entero entre 1 y 65535."
        ) from None

    return DatabaseConfig(
        host=os.environ.get("DB_HOST", "localhost"),
        port=port,
        user=os.environ.get("DB_USER", "root"),
        password=password,
        database=os.environ.get("DB_NAME", "f1_historical_database"),
    )
