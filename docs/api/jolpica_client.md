# Cliente Jolpica F1 — fase 4

`api/jolpica_client.py` consulta la API de Jolpica mediante `requests.Session`.
Recupera temporadas, pilotos, constructores, circuitos, calendarios y resultados
de carrera. No necesita MySQL ni credenciales, no modifica el esquema y no
realiza importaciones a la base de datos. La temporada predeterminada es 2024.

## Endpoints verificados

Se utiliza la base `https://api.jolpi.ca/ergast/f1` y los endpoints estables
compatibles con Ergast. La [documentación general oficial](https://api.jolpi.ca/docs/)
incluye también endpoints nuevos; la documentación del repositorio oficial
describe los compatibles con Ergast utilizados aquí.

| Método | Ruta relativa | Lista de la respuesta |
| --- | --- | --- |
| `get_seasons()` | `/seasons/` | `MRData.SeasonTable.Seasons` |
| `get_drivers(2024)` | `/2024/drivers/` | `MRData.DriverTable.Drivers` |
| `get_constructors(2024)` | `/2024/constructors/` | `MRData.ConstructorTable.Constructors` |
| `get_circuits(2024)` | `/2024/circuits/` | `MRData.CircuitTable.Circuits` |
| `get_race_schedule(2024)` | `/2024/races/` | `MRData.RaceTable.Races` |
| `get_race_results(2024)` | `/2024/results/` | `MRData.RaceTable.Races`, con `Results` por carrera |

El código utiliza el sufijo `.json` en estas rutas, una alternativa a la barra
final admitida por Jolpica. No consulta los endpoints alpha.

`get_race_results(2024, round_number=1)` limita la consulta a
`/2024/1/results/`. El cliente no necesita consultar previamente el calendario.

Fuentes oficiales por recurso: [temporadas](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/seasons.md),
[pilotos](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/drivers.md),
[constructores](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/constructors.md),
[circuitos](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/circuits.md),
[calendario](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/races.md)
y [resultados](https://github.com/jolpica/jolpica-f1/blob/main/docs/endpoints/results.md).

## Obtener resultados reales de 2024

Instala las dependencias desde la raíz del proyecto:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Este ejemplo solicita únicamente los resultados de la primera ronda y cierra
la sesión automáticamente. Con la página predeterminada de 100 resultados,
la consulta cabe en una petición si el total indicado por el servidor no
supera ese límite:

```python
from api.jolpica_client import JolpicaClient

with JolpicaClient() as client:
    races = client.get_race_results(2024, round_number=1)

for race in races:
    print(race.get("raceName"), race.get("date"))
    for result in race["Results"]:
        driver = result.get("Driver") or {}
        print(
            result.get("position"),
            driver.get("givenName"),
            driver.get("familyName"),
            result.get("points"),
        )
```

Una comprobación pequeña desde PowerShell, sin modificar el menú:

```powershell
@'
from api.jolpica_client import JolpicaClient
with JolpicaClient(max_retries=0, timeout=(5, 15)) as client:
    races = client.get_race_results(2024, round_number=1)
print([(race.get('raceName'), len(race['Results'])) for race in races])
'@ | .\.venv\Scripts\python.exe -
```

Para recuperar otros recursos, utiliza los métodos de la tabla. Para obtener
todos los resultados de 2024, omite `round_number`; el cliente pagina el
endpoint de temporada automáticamente. El número de peticiones depende del
total y del límite efectivo que devuelva el servidor. Reutiliza una misma
instancia para aprovechar la sesión y la caché.

## Configuración del cliente

```python
from api.jolpica_client import JolpicaClient

client = JolpicaClient(
    base_url="https://api.jolpi.ca/ergast/f1",
    timeout=(5.0, 30.0),
    page_size=100,
    max_retries=2,
    backoff_factor=1.0,
    max_retry_delay=60.0,
    cache_ttl=300.0,
)
```

| Parámetro | Comportamiento |
| --- | --- |
| `base_url` | Base HTTP; el valor predeterminado apunta a Jolpica. |
| `timeout` | Tiempo máximo de conexión y lectura, en segundos. |
| `page_size` | Tamaño solicitado por página, de 1 a 100. |
| `max_retries` | Entero de 0 a 10; reintentos adicionales por petición. 2 permite hasta 3 intentos. |
| `backoff_factor` | Base de la espera exponencial entre reintentos. |
| `max_retry_delay` | Espera máxima automática; no adelanta un reintento que deba esperar más. |
| `cache_ttl` | Duración de la caché en memoria, en segundos; 0 la desactiva. |
| `session` | Sesión opcional proporcionada por quien llama. |

El cliente configura `User-Agent: F1-Historical-Database/0.1.0` para identificar
la aplicación. La configuración se pasa al constructor: no carga `.env` ni
depende de la configuración MySQL. `F1_API_URL` en la plantilla de entorno no
se lee automáticamente en esta fase.

Si el cliente crea la sesión, `close()` y el gestor de contexto la cierran.
Si se proporciona `session=mi_sesion`, su cierre corresponde a quien la creó.

## Paginación y datos ausentes

Los métodos devuelven listas de diccionarios con los nombres y valores de la
API. No convierten automáticamente textos numéricos en enteros o decimales,
ni crean filas para información desconocida. Una colección vacía válida se
devuelve como `[]`. Los campos opcionales ausentes o nulos se conservan así.
Para leerlos, utiliza `.get()` y comprueba los objetos anidados antes de acceder.

Por ejemplo, un piloto puede carecer de `code` o `permanentNumber`; una carrera
puede no incluir hora, prácticas o sprint; un resultado puede no tener `Time`
o `FastestLap`. No se sustituye una puntuación ausente por cero.

El cliente valida el JSON y la estructura de la respuesta, incluidos los
metadatos `limit`, `offset` y `total`. Los datos estructuralmente inválidos
generan un error; no se interpretan como una colección vacía.

El [formato general oficial](https://github.com/jolpica/jolpica-f1/blob/main/docs/README.md)
documenta un máximo de 100 elementos por página. El cliente utiliza el límite
efectivo recibido, que puede diferir del solicitado, y termina cuando alcanza
el total sin hacer una petición vacía adicional.

En resultados, `total` cuenta resultados de pilotos, no carreras. Una carrera
puede aparecer en varias páginas: el cliente reúne sus listas `Results` por
temporada y ronda. Ese comportamiento se comprueba también en el
[código oficial de paginación](https://github.com/jolpica/jolpica-f1/blob/main/jolpica_api/ergastapi/pagination.py)
y en el [serializador de resultados](https://github.com/jolpica/jolpica-f1/blob/main/jolpica_api/ergastapi/serializers.py).

La caché conserva únicamente consultas completas que han terminado correctamente.
Tiene una duración predeterminada de 300 segundos y devuelve copias profundas,
por lo que modificar un resultado no modifica la copia guardada. No se persiste
en disco. Usa `client.clear_cache()` para vaciarla o `cache_ttl=0` para desactivarla.

## Límites y reintentos

Los [límites oficiales sin autenticación](https://github.com/jolpica/jolpica-f1/blob/main/docs/rate_limits.md)
son 4 peticiones por segundo y 500 por hora. Pueden cambiar. El limitador se
comparte entre clientes del mismo proceso y cuenta todos los intentos HTTP,
incluidos los reintentos. Aplica ventanas móviles para ambos límites.

La coordinación no se extiende a otros procesos o equipos que compartan la
misma IP. Si ejecutas varios, coordina su uso externamente y atiende las
respuestas 429 del servidor.

Se reintentan tiempos de espera, fallos temporales de conexión y los estados
HTTP 408, 429, 500, 502, 503 y 504. Los fallos SSL, otros errores HTTP y los
errores de JSON o estructura no se reintentan.

Ante `Retry-After`, se admite tanto un número de segundos como una fecha HTTP.
La espera respeta esa indicación y el limitador local. Si la cuota o el periodo
de espera exige más que `max_retry_delay`, se lanza `JolpicaRateLimitError` con
`retry_after` para que quien llama decida cuándo continuar. Si la respuesta
fue otro error HTTP temporal, se conserva `JolpicaHTTPError` con su código.
No se repite la petición antes de tiempo. Los intentos están acotados por
`max_retries` y el tiempo de espera de cada petición por `timeout`; no se define
un tiempo máximo global para una consulta completa con varias páginas.

## Errores y pruebas

| Excepción | Significado |
| --- | --- |
| `JolpicaError` | Base de los errores del cliente. |
| `JolpicaHTTPError` | Error HTTP; `status_code` permite examinar el estado. |
| `JolpicaRateLimitError` | Límite o espera pendiente; `retry_after` indica la demora. |
| `JolpicaTimeoutError` | Tiempo de espera agotado tras los intentos permitidos. |
| `JolpicaConnectionError` | Fallo de comunicación. |
| `JolpicaResponseError` | JSON o estructura de respuesta inválidos. |
| `ValueError` | Argumentos locales inválidos. |

Las pruebas usan respuestas simuladas y no requieren Internet, MySQL ni claves:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_jolpica_client.py tests/test_jolpica_rate_limiter.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

Cubren peticiones correctas, paginación, datos ausentes, errores HTTP, tiempos
de espera, respuestas 429 y JSON inválido. La comprobación de compatibilidad
real debe ejecutarse aparte mediante una consulta pequeña como la mostrada
arriba; no se hace una petición a la API al importar el módulo.

En la validación de esta fase pasaron **84 pruebas nuevas de API y limitación
de solicitudes**, y **192 pruebas en la suite completa**, sin conexiones reales
durante pytest.

## Resultado de la comprobación real

El 16 de noviembre de 2026 se hizo una única petición de datos, sin reintentos:

```text
GET https://api.jolpi.ca/ergast/f1/2024/1/results.json?limit=100&offset=0
HTTP 200
Temporada: 2024
Ronda: 1
Carrera: Bahrain Grand Prix
Fecha: 2024-03-02
Resultados: 20
Primera posición: max_verstappen
```

Se utilizó el cliente implementado con `max_retries=0` y `timeout=(5, 15)`.
El JSON pasó la validación del cliente. Esta comprobación verifica una respuesta
real de resultados; los otros endpoints y la paginación se validan mediante la
documentación oficial y pruebas simuladas, sin descargar toda la temporada.
