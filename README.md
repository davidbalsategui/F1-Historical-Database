# F1 Historical Database

## Overview

F1 Historical Database is a personal software engineering project for collecting,
storing, querying and analyzing historical Formula 1 data with Python and MySQL.
The planned relational database will cover drivers, constructors, circuits,
seasons, races, race results and championship standings.

Phase 1 establishes the project structure, configuration template, dependency
list and executable entry point. API access, database integration and analysis
features are planned for later phases.

## Technologies

| Technology | Role |
| --- | --- |
| Python 3.12+ | Application development and future data processing |
| MySQL | Planned relational data storage |
| MySQL Workbench | Planned database modeling and administration |
| Jolpica F1 API | Planned source of historical Formula 1 data |
| PyCharm | Python development environment |
| Git and GitHub | Version control and repository hosting |
| pytest | Framework for future automated tests |

The dependency list also includes `requests` for future HTTP communication,
`mysql-connector-python` for future MySQL access and `python-dotenv` for future
environment configuration. The Phase 1 entry point uses only the Python standard
library.

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
│   ├── __init__.py
│   └── jolpica_client.py
├── database/
│   ├── __init__.py
│   ├── connection.py
│   └── repositories.py
├── models/
│   ├── __init__.py
│   └── entities.py
├── services/
│   ├── __init__.py
│   ├── import_service.py
│   └── statistics_service.py
├── sql/
│   ├── schema.sql
│   └── queries.sql
├── tests/
│   └── test_import_service.py
├── main.py
├── config.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── LICENSE
```

| Path | Responsibility |
| --- | --- |
| `api/` | Future external API communication, pagination and error handling |
| `database/` | Future MySQL connections, transactions and repositories |
| `models/` | Future domain entities |
| `services/` | Future import coordination, validation and statistics |
| `sql/` | Future schema, relationships, constraints and query scripts |
| `tests/` | Future automated tests using pytest |
| `main.py` | Minimal executable entry point |
| `config.py` | Placeholder for future configuration management |
| `.env.example` | Environment-variable template with example values |

The application modules, SQL scripts and test module are placeholders in Phase 1.

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

   Expected output:

   ```text
   ========================================
          F1 HISTORICAL DATABASE
   ========================================
   Project initialized successfully!
   ```

No database, API connection or environment configuration is required for this
execution. `.env.example` documents the intended configuration keys; `config.py`
does not load them yet. Keep real credentials out of version control.

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

### Phase 6 — Interface and Testing (planned)

- Command-line interface
- Automated tests
- Documentation improvements
- Optional data visualizations

## Data Source

The planned data source is the [Jolpica F1 API](https://api.jolpi.ca/).
Data retrieval has not been implemented in Phase 1.

## Project Status

**Early Development — Phase 1: Project Initialization.**

The initial structure and executable entry point are ready. Database design,
persistence, API integration and analysis remain future work.

## License

This project is licensed under the [MIT License](LICENSE).
