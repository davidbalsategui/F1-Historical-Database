# Importación de la temporada 2024

El importador consulta el calendario y todos los resultados de carrera de 2024,
extrae las entidades compartidas y escribe mediante repositorios MySQL. Esta
fase cubre `SEASON`, `DRIVER`, `CONSTRUCTOR`, `CIRCUIT`, `RACE` y `RESULT`. No
importa clasificaciones de sprint, entrenamientos ni otras temporadas.

## Estado del esquema y requisitos

En este repositorio, `sql/schema.sql` continúa siendo un comentario. La
documentación de diseño contiene una propuesta diferente de 11 entidades.
Los nombres de las seis tablas solicitadas no permiten deducir sus columnas,
claves ni restricciones. Por ese motivo, el importador requiere un mapeo
explícito hacia las columnas del esquema aprobado; no genera un esquema ni
supone nombres de columnas.

Antes de importar a MySQL se necesitan:

1. Las seis tablas existentes y su DDL aprobado.
2. Un archivo JSON que relacione los campos del importador con sus columnas.
3. Las credenciales de una cuenta autorizada para consultar, insertar y
   actualizar esas tablas.

Para obtener el DDL sin modificar tablas, selecciona la base correspondiente en
MySQL Workbench y ejecuta:

```sql
SHOW CREATE TABLE SEASON;
SHOW CREATE TABLE DRIVER;
SHOW CREATE TABLE CONSTRUCTOR;
SHOW CREATE TABLE CIRCUIT;
SHOW CREATE TABLE RACE;
SHOW CREATE TABLE RESULT;
```

Esos resultados permiten completar el mapeo y comprobar claves primarias,
índices únicos, claves foráneas, nulabilidad y precisión de los puntos. No
ejecutes `DROP`, `TRUNCATE` ni instrucciones de creación para probar el importador.

## Validar la fuente sin MySQL

Desde la raíz del proyecto, con las dependencias instaladas:

```powershell
.\.venv\Scripts\python.exe -m services.import_service --dry-run
```

Este modo descarga el calendario y los resultados mediante el cliente Jolpica,
valida sus relaciones e informa de los recuentos reales de la fuente. No carga
credenciales MySQL ni abre una conexión a la base de datos. Los contadores de
filas insertadas y actualizadas permanecen en cero.

La paginación corresponde al cliente existente. Se consulta el endpoint de
resultados de temporada, no una petición por piloto o por ronda. Los resultados
que llegan en páginas distintas se reúnen por temporada y ronda.

## Mapeo del esquema existente

`database.repositories.SchemaMapping.from_file(ruta)` carga un documento con
esta estructura. El siguiente fragmento es únicamente una explicación del
formato; sus columnas son marcadores y no representan un esquema aprobado:

```json
{
  "tables": {
    "SEASON": {
      "table": "SEASON",
      "primary_key": "clave_primaria_aprobada",
      "columns": {
        "year": "columna_del_ano_aprobada"
      }
    }
  }
}
```

El archivo real debe contener las seis entradas completas. A la izquierda de
`columns` se utilizan los roles del importador; a la derecha, los nombres
exactos de las columnas existentes. `primary_key` nombra la columna física de
la clave primaria.

| Tabla lógica | Campos que deben estar mapeados | Identidad utilizada para evitar duplicados |
| --- | --- | --- |
| `SEASON` | `year` | `year` |
| `DRIVER` | `api_id` | `api_id` |
| `CONSTRUCTOR` | `api_id` | `api_id` |
| `CIRCUIT` | `api_id` | `api_id` |
| `RACE` | `season_id`, `circuit_id`, `round` | `season_id`, `round` |
| `RESULT` | `race_id`, `driver_id`, `constructor_id`, `points` | `race_id`, `driver_id` |

Los identificadores `api_id` conservan los identificadores de Jolpica. Las
relaciones usan las claves internas devueltas por MySQL, nunca el nombre de una
persona, una escudería o un circuito para resolver identidades.

Los campos adicionales se pueden mapear si existen en el esquema aprobado:

- `SEASON`: `url`.
- `DRIVER`: `given_name`, `family_name`, `permanent_number`, `code`,
  `date_of_birth`, `nationality`, `url`.
- `CONSTRUCTOR`: `name`, `nationality`, `url`.
- `CIRCUIT`: `name`, `latitude`, `longitude`, `locality`, `country`, `url`.
- `RACE`: `name`, `date`, `time`, `url`.
- `RESULT`: `number`, `position`, `position_text`, `grid`, `laps`, `status`,
  `elapsed_time_ms`, `fastest_lap_rank`, `fastest_lap_number`,
  `fastest_lap_time_ms`.

El repositorio comprueba el esquema mediante consultas de lectura antes de
escribir. Exige tablas transaccionales InnoDB, claves primarias compatibles,
identidades protegidas por restricciones únicas y las relaciones foráneas
necesarias. La clave primaria puede ser el propio año de `SEASON`; las claves
internas generadas deben tener la configuración de autoincremento adecuada.
Los puntos requieren un tipo decimal exacto y una precisión que permita guardar
los valores recibidos sin redondeo. Una columna obligatoria sin valor
predeterminado no puede quedar sin cubrir por el mapeo.

Una incompatibilidad produce un error antes de insertar datos. El importador
no añade restricciones ni adapta las tablas por su cuenta.

## Configurar y ejecutar una importación real

Configura `.env` localmente según la
[guía de conexión MySQL](../database/mysql_connection.md). Para crear el archivo
sin sobrescribir uno existente:

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
```

Las variables requeridas son `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` y
`DB_NAME`. Las variables definidas en el entorno prevalecen sobre `.env`.
No guardes credenciales en el mapeo, en los informes ni en archivos versionados.

Una vez completado el mapeo con el DDL real:

```powershell
.\.venv\Scripts\python.exe -m services.import_service --mapping ruta/al/mapeo.json --report data/cache/import_2024_report.json
```

El directorio `data/cache/` está excluido de Git. El informe distingue la
validación de la fuente de la confirmación de filas en MySQL.

Desde Python:

```python
from database.repositories import SchemaMapping
from services.import_service import ImportService, ImportServiceError

mapping = SchemaMapping.from_file("ruta/al/mapeo.json")
try:
    report = ImportService(mapping=mapping).import_2024()
except ImportServiceError as error:
    report = error.report

print(report.as_dict())
```

También se puede inyectar el cliente Jolpica o una configuración MySQL ya
validada. La importación está limitada explícitamente a 2024.

## Transacciones, reimportaciones y reinicios

La descarga y normalización de la fuente terminan antes de abrir la transacción
de escritura. Después se validan el esquema y los datos, se resuelven las
entidades y se escriben en este orden:

1. Temporada.
2. Pilotos.
3. Constructores.
4. Circuitos.
5. Carreras, con sus claves internas de temporada y circuito.
6. Resultados, con sus claves internas de carrera, piloto y constructor.

La temporada se confirma en una sola transacción. Un bloqueo MySQL de sesión
evita que dos importadores del proyecto escriban simultáneamente. Las
restricciones únicas del esquema siguen siendo necesarias para impedir
duplicados, también frente a otras aplicaciones.

El repositorio busca cada registro por su identidad estable y compara los
valores: inserta si no existe, actualiza lo que ha cambiado y omite la escritura
si ya coincide. Todas las consultas pasan los valores mediante parámetros `%s`.
Una segunda ejecución con la misma fuente debe conservar las claves internas
y omitir las filas ya idénticas.

Si falla una escritura o una validación anterior al commit, la transacción se
revierte. Para reiniciar, vuelve a ejecutar la importación completa: las
identidades estables permiten reutilizar registros que ya existieran antes
de la ejecución. No se necesita borrar datos ni crear un punto de control.

Si se pierde la conexión al confirmar, el resultado del commit puede ser
incierto. El informe no debe afirmar una reversión demostrada en ese caso.
La recuperación consiste en volver a consultar la base y repetir el proceso
idempotente cuando se haya restablecido la conexión.

Los valores ausentes permanecen como `NULL` donde el esquema lo permite.
Las filas se tratan como una instantánea autoritativa: también al actualizar,
un atributo ausente de la API se normaliza a `NULL`, sin rellenarlo con un cero
ni con otro dato supuesto. Si hay varias observaciones de la misma entidad en
una descarga, se conservan sus atributos conocidos no contradictorios.
Los puntos se representan mediante `Decimal`, distinguiendo un cero real de
un valor desconocido. No se crean resultados para completar un número supuesto
de participantes; el número correcto procede de la API.

## Informe y validaciones

El servicio muestra el progreso y devuelve un `ImportReport` serializable
mediante `as_dict()`. Incluye:

- Estado y éxito de la ejecución.
- Rondas esperadas y procesadas.
- Recuentos de la fuente por entidad.
- Contadores `inserted`, `updated`, `skipped` y `failed` por tabla.
- Contadores de operaciones intentadas separados de las confirmadas.
- Resultado de las validaciones y mensajes de error.

Los contadores confirmados solo representan una transacción terminada con
éxito. En un fallo no se presentan como insertadas las operaciones que estaban
pendientes de commit. `attempted_counts` conserva el avance previo para
explicar dónde se interrumpió la ejecución.

La comprobación final compara las rondas almacenadas con las recibidas,
comprueba la temporada de cada carrera, la existencia de los padres de cada
resultado, las identidades de pilotos y constructores, los puntos exactos y la
ausencia de resultados duplicados. Los recuentos esperados se calculan desde
la fuente; no se fijan en código como 24 carreras o 480 resultados.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_import_service.py tests/test_repositories.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

Las pruebas utilizan datos de prueba identificados como tales y repositorios
o respuestas simuladas. Cubren reimportación, correcciones, nulos,
precisión decimal, duplicados, datos incompletos, resolución de claves,
interrupciones y rollback. Los mocks no acreditan el comportamiento de una
instancia MySQL real ni la compatibilidad con un DDL que no está disponible.

Resultado de esta fase: **134 pruebas de importación y repositorios aprobadas**
(52 del servicio y 82 del repositorio), además de las pruebas de fases anteriores.

El [informe de la fase 5](phase5_report.md) registra los datos realmente
consultados y las comprobaciones que quedan pendientes.
