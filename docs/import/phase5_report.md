# Informe de validación — fase 5

## Fuente real consultada

El 16 de noviembre de 2026, a las `15:20:16 UTC`, se consultó la API Jolpica
mediante el cliente del proyecto, con `max_retries=0` y `timeout=(5, 20)`.
Se hicieron exactamente **6 peticiones de datos**, todas con respuesta
**HTTP 200**:

| Recurso | `limit` | `offset` |
| --- | ---: | ---: |
| `/ergast/f1/2024/races.json` | 100 | 0 |
| `/ergast/f1/2024/results.json` | 100 | 0 |
| `/ergast/f1/2024/results.json` | 100 | 100 |
| `/ergast/f1/2024/results.json` | 100 | 200 |
| `/ergast/f1/2024/results.json` | 100 | 300 |
| `/ergast/f1/2024/results.json` | 100 | 400 |

La respuesta original quedó en `data/cache/phase5_2024_source.json`, excluida de
Git. La fecha exacta de recuperación registrada es
`2026-11-16T15:20:16.638413+00:00`. El archivo permite repetir validaciones
locales sin consultar otra vez la API.

## Cantidades observadas

Estos números se calcularon desde las respuestas recibidas y no son constantes
del importador:

| Entidad | Registros distintos en la fuente |
| --- | ---: |
| `SEASON` | 1, correspondiente a 2024 |
| `DRIVER` | 24 |
| `CONSTRUCTOR` | 10 |
| `CIRCUIT` | 24 |
| `RACE` | 24 |
| `RESULT` | **479** |

Calendario y resultados contienen las mismas rondas, del **1 al 24**, y todos
los objetos de carrera identifican la temporada **2024**. No se encontraron
identidades duplicadas `(round, driverId)`.

La ronda **3** tiene **19 resultados**; cada una de las otras 23 rondas tiene
20. No se añadió un resultado artificial para alcanzar 480.

La suma de puntos publicados en estos resultados es exactamente
`Decimal("2443")`. Es una suma de resultados de grandes premios; **no incluye
sprint** y no pretende reemplazar una clasificación oficial de campeonato.
Todos los valores `points` recibidos son cadenas numéricas y ninguno es nulo.

## Campos opcionales

- `Time` falta en 47 resultados. Las 432 entradas presentes contienen `millis`
  y `time`.
- `FastestLap` falta en 17 resultados.
- De los 462 objetos `FastestLap` presentes, 28 no incluyen `AverageSpeed`.
- No se observaron valores nulos explícitos en los campos principales de
  resultado comprobados.
- No se observaron metadatos contradictorios para distintas apariciones de
  un mismo piloto, constructor o circuito.
- Los 24 objetos de calendario incluyen fecha, hora, URL y circuito. Las
  horas recibidas terminan en `Z`.

Estas observaciones describen esta respuesta concreta. El importador sigue
necesitando manejar datos opcionales ausentes o nulos en futuras consultas.

## Ejecución del importador sobre la fuente capturada

Se ejecutó `ImportService.import_2024(dry_run=True)` usando las respuestas
reales capturadas, sin repetir las peticiones HTTP. La normalización y la
validación completa de rondas y unicidad terminaron correctamente. Se
conservaron las 47 duraciones desconocidas como `None`, sin convertir los
déficits de tiempo en duraciones totales.

El informe serializado está en `data/cache/phase5_dry_run_report.json`, con
`success=true` y `status="dry_run"`. Las validaciones MySQL permanecen en
`null` y los contadores confirmados en cero.

## Pruebas automatizadas

Pasaron **134 pruebas nuevas**: 52 del servicio y 82 del repositorio. La suite
completa terminó con **326 pruebas aprobadas**. Las nuevas pruebas cubren
upserts, PK internas, NULL, precisión decimal, validación del esquema,
paginación con HTTP simulado, rollback, interrupción, reinicio e idempotencia.

## Situación de MySQL

El servicio Windows `MySQL80` se encontró en ejecución. Sin embargo:

1. No existe un archivo `.env` local ni están definidas las variables `DB_*`.
2. `sql/schema.sql` contiene únicamente un comentario.
3. La documentación del repositorio describe una propuesta de 11 entidades,
   diferente de las seis tablas solicitadas para este importador.
4. No está disponible el DDL aprobado de esas seis tablas ni su mapeo de
   columnas y claves.

No se intentó autenticar con credenciales inventadas y no se realizó ninguna
escritura en MySQL. Por tanto, las cantidades anteriores son recuentos de
fuente, **no filas insertadas**.

Se ejecutó el comando de importación sin un mapeo y terminó con código **1**,
antes de consultar la API o abrir MySQL, indicando que falta `--mapping`.
Su informe está en `data/cache/phase5_blocked_import_report.json`. El
diagnóstico MySQL también terminó con código **1** y el mensaje preciso
`Falta DB_PASSWORD en el entorno o en .env.`

Los contadores de filas omitidas y fallidas son también cero: no se intentó
guardar ninguna fila. El error registrado es de configuración, no un fallo
de una fila de Fórmula 1.

| Tabla | Insertadas realmente | Actualizadas realmente |
| --- | ---: | ---: |
| `SEASON` | 0 | 0 |
| `DRIVER` | 0 | 0 |
| `CONSTRUCTOR` | 0 | 0 |
| `CIRCUIT` | 0 | 0 |
| `RACE` | 0 | 0 |
| `RESULT` | 0 | 0 |

## Validaciones reales pendientes

La fuente y su paginación se han comprobado con datos reales. Para acreditar
una importación real todavía se necesitan las credenciales y el DDL aprobado.
Con ellos se debe:

1. Cargar el mapeo y superar la comprobación de compatibilidad del esquema.
2. Confirmar la primera importación y contrastar los recuentos finales contra
   la fuente recibida en esa ejecución.
3. Verificar las claves internas resueltas, las relaciones entre las seis
   tablas y los valores decimales almacenados.
4. Confirmar que ninguna carrera de la importación pertenece a otra temporada
   ni existe un resultado duplicado.
5. Ejecutar nuevamente la misma importación y comprobar que no aumenta el
   número de registros.
6. Comprobar con MySQL real la reversión y recuperación ante una interrupción.

Los tests con mocks pueden validar el comportamiento del código ante esos
casos, pero no sustituyen estas comprobaciones en el servidor de destino.
La [guía de ejecución](import_2024.md) contiene los comandos de validación,
configuración y reimportación.
