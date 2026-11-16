# F1 Historical Database

## Overview

F1 Historical Database is a personal software engineering project for collecting,
storing, querying and analyzing historical Formula 1 data with Python and MySQL.
The planned relational database will cover drivers, constructors, circuits,
seasons, races, race results and championship standings.

El proyecto incluye su estructura inicial, plantilla de configuración,
dependencias y menú interactivo con pruebas automatizadas. La versión 1.0 se
centrará inicialmente en 2024 y la arquitectura permitirá otras temporadas.
La conexión MySQL, el cliente Jolpica y el importador de 2024 están implementados.
La ejecución real del importador requiere el DDL aprobado y credenciales MySQL;
el análisis sigue pendiente.

La [guía de importación de 2024](docs/import/import_2024.md) describe la ejecución,
el mapeo de las seis tablas y la recuperación ante errores. El
[informe de la fase 5](docs/import/phase5_report.md) distingue los 479 resultados
reales obtenidos de Jolpica de las escrituras MySQL, todavía pendientes.

El [cliente Jolpica de la fase 4](docs/api/jolpica_client.md) permite consultar
temporadas, pilotos, constructores, circuitos, calendarios y resultados de 2024,
con paginación, límites de uso, reintentos y pruebas mediante mocks.

La carga de `.env`, la conexión MySQL, las transacciones y el diagnóstico de
solo lectura están implementados. Consulta la
[guía de conexión MySQL](docs/database/mysql_connection.md) para configurar
credenciales, ejecutar el diagnóstico y revisar el procedimiento de Workbench.
`sql/schema.sql` sigue siendo un marcador; la documentación propone 11 entidades
y todavía no identifica las seis tablas aprobadas que solicita esta fase.

## Technologies

| Technology | Role |
| --- | --- |
| Python 3.12+ | Application development and future data processing |
| MySQL | Conexión, transacciones y diagnóstico de solo lectura; esquema pendiente |
| MySQL Workbench | Planned database modeling and administration |
| Jolpica F1 API | Cliente HTTP implementado para consultar datos históricos |
| PyCharm | Python development environment |
| Git and GitHub | Version control and repository hosting |
| pytest | Automated terminal-menu tests and future application tests |

Las dependencias incluyen `requests` para la comunicación HTTP con Jolpica,
`mysql-connector-python` para conexión MySQL y `python-dotenv` para cargar la
configuración local. El menú actual utiliza solo la biblioteca estándar de Python.

## Planned Features

The following features are planned and are not implemented yet:

- Importación histórica de otras temporadas (2024 implementada, pendiente de validación MySQL real)
- Driver and constructor management
- Race results storage
- Championship standings
- Historical statistics
- Driver comparisons
- Rankings
- Data exports
- Future visualizations

## Project Structure

```text
F1-Historical-Database/
├── api/
├── cli/
│   ├── __init__.py
│   └── menu.py
├── database/
├── models/
├── services/
├── sql/
│   ├── schema.sql
│   └── queries.sql
├── docs/
│   └── database/
│       ├── er_diagram.mmd
│       ├── relational_model.md
│       └── design_decisions.md
├── tests/
│   ├── __init__.py
│   ├── test_import_service.py
│   └── test_menu.py
├── main.py
└── README.md
```

This tree shows the main project structure. Existing package modules,
`config.py`, `requirements.txt`, `.env.example`, `.gitignore` and `LICENSE`
remain in the repository.

| Path | Responsibility |
| --- | --- |
| `api/` | Cliente Jolpica con Session, paginación, caché y errores controlados |
| `cli/` | Menu rendering, input validation and the interactive terminal loop |
| `database/` | Conexión, transacciones, diagnóstico y repositorios con mapeo del esquema |
| `models/` | Future domain entities |
| `services/` | Importación y validación de 2024; estadísticas pendientes |
| `sql/` | Future schema, relationships, constraints and query scripts |
| `docs/database/` | Database design documentation, maintained separately from the Python application |
| `tests/` | Pruebas de CLI, configuración, MySQL, API, repositorios e importación con mocks |
| `main.py` | Executable entry point that starts the terminal menu |
| `config.py` | Carga de dotenv y validación de configuración MySQL |
| `.env.example` | Environment-variable template with example values |

Los modelos de dominio y el servicio de estadísticas siguen como marcadores.
Los scripts SQL permanecen sin implementar. Conexión, API, repositorios e
importador cuentan con pruebas mediante mocks. El importador valida el esquema
existente antes de escribir y no lo crea ni lo modifica.
El diseño se documenta por separado en
[entity-relationship diagram](docs/database/er_diagram.mmd),
[relational model](docs/database/relational_model.md) and
[design decisions](docs/database/design_decisions.md).

## Getting Started

Install Python 3.12 or later and Git. MySQL and MySQL Workbench will be needed
for later phases; they are not required to run the current entry point.

1. Clone the repository, replacing `<github-username>` with the repository owner's
   GitHub username:

   ```bash
   git clone https://github.com/<github-username>/F1-Historical-Database.git
   cd F1-Historical-Database
   ```

2. Create a virtual environment:

   ```bash
   python -m venv .venv
   ```

   Activate it on Windows with PowerShell:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   Or activate it on macOS/Linux:

   ```bash
   source .venv/bin/activate
   ```

   If your system provides Python 3 as `python3`, use that command to create the
   virtual environment.

3. Install the project dependencies inside the activated virtual environment:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Run the entry point:

   ```bash
   python main.py
   ```

   The application displays:

   ```text
   =========================================
            F1 HISTORICAL DATABASE
   =========================================

   1. Buscar piloto
   2. Consultar temporada
   3. Consultar Gran Premio
   4. Ranking histórico de pilotos
   5. Estadísticas de constructores
   6. Comparar dos pilotos
   7. Actualizar base de datos
   8. Exportar estadísticas

   0. Salir

   Selecciona una opción:
   ```

Enter an option from `1` to `8` to see an informative message identifying that
feature as pending implementation. The menu then appears again. Invalid input
displays a validation message and lets you retry. Enter `0` to exit; end of input
and `Ctrl+C` also close the menu cleanly. The interface is in Spanish.

El menú no requiere MySQL ni credenciales. Para el diagnóstico de base de datos,
configura un `.env` local siguiendo la [guía MySQL](docs/database/mysql_connection.md)
y ejecuta `python -m database.diagnostic`. El comando señala cualquier problema
sin crear ni modificar tablas. Mantén las credenciales reales fuera de Git.

## Running Tests

With the virtual environment activated and dependencies installed, run:

```bash
python -m pytest -q
```

Las pruebas cubren el menú, la configuración, la conexión, el cierre de recursos,
las transacciones, el diagnóstico, el cliente Jolpica, los repositorios y la
importación de 2024. Utilizan mocks y no requieren MySQL, Internet ni credenciales.

## Development Roadmap

### Phase 1 — Project Initialization (completed)

- Repository structure
- Configuration files
- README and documentation
- Basic execution verification

### Phase 2 — Database Design (planned)

- Entity-relationship diagram
- Tables and attributes
- Primary and foreign keys
- Database normalization
- MySQL schema

### Phase 3 — Database Integration (partially implemented)

- Conexión MySQL, dotenv y transacciones implementados
- Diagnóstico `SELECT 1`, base seleccionada y comprobación de tablas implementado
- Pruebas automatizadas con mocks implementadas
- Verificación real pendiente de credenciales y nombres de las seis tablas
- Repositorios para el importador implementados; CRUD general pendiente

### Phase 4 — API Integration (partially implemented)

- Cliente Jolpica y seis consultas reutilizables implementados
- Paginación y fusión de resultados por carrera implementadas
- Límites de uso, reintentos, caché y pruebas sin red implementados
- Consulta real de la primera ronda de 2024 verificada
- Importador de 2024 implementado en la fase 5; ejecución MySQL real pendiente

### Phase 5 — 2024 Data Importer (implemented, live MySQL validation pending)

- Descarga paginada y normalización del calendario y los resultados
- Resolución de claves internas y upserts idempotentes
- Transacción atómica, rollback y reinicio mediante reimportación
- Progreso, contadores e informe de validación
- Fuente real verificada: 24 rondas y 479 resultados; faltan DDL y credenciales para MySQL

### Phase 6 — Statistics and Analysis (planned)

- SQL queries
- Rankings
- Driver comparisons
- Championship analysis

### Phase 7 — Interface and Testing (partially implemented)

- Interactive terminal menu and CLI tests are implemented
- Feature handlers and application-level tests are planned
- Further documentation improvements are planned
- Optional data visualizations are planned

## Data Source

El cliente consulta la [Jolpica F1 API](https://api.jolpi.ca/) mediante endpoints
estables compatibles con Ergast. Consulta los endpoints verificados y ejemplos
de uso en la [guía del cliente](docs/api/jolpica_client.md). El importador utiliza
estas consultas; su ejecución contra MySQL sigue pendiente de la configuración
y del mapeo del esquema aprobado.

## Project Status

**Early Development — Python structure and terminal-menu scaffold available.**

La estructura inicial, el menú, la configuración, la conexión MySQL, el cliente
Jolpica, los repositorios y el importador de 2024 están disponibles con pruebas
automatizadas. Las funciones del menú y el análisis siguen pendientes.
No se ha implementado ni ejecutado
un esquema SQL: `schema.sql` continúa como marcador y la documentación de diseño
mantiene su propuesta original de 11 entidades.

## License

This project is licensed under the [MIT License](LICENSE).
