# F1 Historical Database

## Overview

F1 Historical Database is a personal software engineering project for collecting,
storing, querying and analyzing historical Formula 1 data with Python and MySQL.
The planned relational database will cover drivers, constructors, circuits,
seasons, races, race results and championship standings.

The project includes its initial structure, configuration template, dependency
list and an interactive terminal menu with automated Python tests. Version 1.0
will initially focus on the 2024 season, while the architecture is intended to
support additional seasons. API access, database integration and analysis
features remain planned work.

## Technologies

| Technology | Role |
| --- | --- |
| Python 3.12+ | Application development and future data processing |
| MySQL | Planned relational data storage |
| MySQL Workbench | Planned database modeling and administration |
| Jolpica F1 API | Planned source of historical Formula 1 data |
| PyCharm | Python development environment |
| Git and GitHub | Version control and repository hosting |
| pytest | Automated terminal-menu tests and future application tests |

The dependency list also includes `requests` for future HTTP communication,
`mysql-connector-python` for future MySQL access and `python-dotenv` for future
environment configuration. The current terminal interface uses only the Python
standard library.

## Planned Features

The following features are planned and are not implemented yet:

- Historical F1 data importation
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
| `api/` | Future external API communication, pagination and error handling |
| `cli/` | Menu rendering, input validation and the interactive terminal loop |
| `database/` | Future MySQL connections, transactions and repositories |
| `models/` | Future domain entities |
| `services/` | Future import coordination, validation and statistics |
| `sql/` | Future schema, relationships, constraints and query scripts |
| `docs/database/` | Database design documentation, maintained separately from the Python application |
| `tests/` | Automated CLI tests using pytest and a placeholder for future import tests |
| `main.py` | Executable entry point that starts the terminal menu |
| `config.py` | Placeholder for future configuration management |
| `.env.example` | Environment-variable template with example values |

The API, persistence, domain-model, service and configuration modules remain
documented placeholders. SQL scripts and the import-service test module also
remain placeholders. Database design is documented separately in the
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

No database, API connection or environment configuration is required for this
execution. `.env.example` documents the intended configuration keys; `config.py`
does not load them yet. Keep real credentials out of version control.

## Running Tests

With the virtual environment activated and dependencies installed, run:

```bash
python -m pytest -q
```

The CLI tests cover menu rendering, valid selections, invalid input, repeated
interaction and application exit behavior. These tests do not require MySQL,
API access or credentials. Import-service tests will be added when that service
is implemented.

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

### Phase 3 — Database Integration (planned)

- MySQL connection
- Repository layer
- CRUD operations

### Phase 4 — API Integration (planned)

- Jolpica API client
- Historical data retrieval
- Pagination
- Data importation

### Phase 5 — Statistics and Analysis (planned)

- SQL queries
- Rankings
- Driver comparisons
- Championship analysis

### Phase 6 — Interface and Testing (partially implemented)

- Interactive terminal menu and CLI tests are implemented
- Feature handlers and application-level tests are planned
- Further documentation improvements are planned
- Optional data visualizations are planned

## Data Source

The planned data source is the [Jolpica F1 API](https://api.jolpi.ca/).
Data retrieval has not been implemented yet.

## Project Status

**Early Development — Python structure and terminal-menu scaffold available.**

The initial structure, interactive menu and CLI tests are ready. Menu features
are placeholders; persistence, API integration and statistical analysis are not
implemented. Database design documentation is maintained separately, and no SQL
schema has been implemented by the Python application.

## License

This project is licensed under the [MIT License](LICENSE).
