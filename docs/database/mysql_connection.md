# Conexión MySQL — fase 3

## Estado del repositorio

La conexión está implementada con `mysql-connector-python` y la configuración
con `python-dotenv`. El diagnóstico solo ejecuta consultas de lectura. No crea
bases ni tablas, no ejecuta `schema.sql` y no importa datos de Jolpica.

**Limitación del esquema:** actualmente `sql/schema.sql` contiene únicamente un
comentario. Los documentos de diseño proponen 11 entidades pendientes de
aprobación; no contienen una lista aprobada de seis tablas. Se han conservado
estos archivos sin cambios. No es posible afirmar que existen las seis tablas
esperadas hasta disponer de sus nombres y comprobarlos contra MySQL.

## Configuración local

Desde la raíz del proyecto, en PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
```

La plantilla contiene:

```dotenv
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=f1_historical_database
```

Sustituye `your_password` por tu contraseña local; nunca la escribas en código,
pruebas o documentación. Si contiene `#` o espacios, utiliza las comillas
adecuadas para el formato dotenv. Una cuenta configurada expresamente sin
contraseña admite `DB_PASSWORD=`; una variable ausente o el valor de ejemplo
producen un error de configuración.

`load_config()` busca `.env` junto a `config.py`, independientemente del
directorio de trabajo. Las variables del entorno tienen prioridad sobre el
archivo. No se carga `.env.example`. La carga ocurre al solicitar configuración,
sin abrir conexiones durante la importación de módulos. El puerto debe ser un
entero de 1 a 65535; host, usuario y base no pueden estar vacíos.

Si no se especifican host, puerto, usuario o base, se usan los valores de la
plantilla. La contraseña no tiene valor predeterminado y se excluye de `repr()`.

`.gitignore` excluye `.env` y `.env.*`, con una excepción para `.env.example`.
Comprueba la protección antes de añadir archivos a Git:

```powershell
git check-ignore .env .env.local
git ls-files .env '.env.*'
```

El segundo comando solo debe mostrar `.env.example`. No fuerces la inclusión
de los archivos locales con `git add -f`.

## Diagnóstico de conexión

```powershell
.\.venv\Scripts\python.exe -m database.diagnostic
$LASTEXITCODE
```

El diagnóstico abre una conexión a `DB_NAME` con un tiempo máximo de conexión
de cinco segundos. Ejecuta `SELECT 1`, comprueba `SELECT DATABASE()` y consulta
las tablas base visibles en `information_schema.TABLES` mediante parámetros
`%s`. Las vistas no se cuentan como tablas base. No muestra la contraseña ni
mensajes SQL del servidor.

Sin los nombres aprobados muestra el inventario, pero devuelve **1**, incluso
si encuentra seis tablas: contar tablas no verifica su identidad. Cuando estén
confirmados los nombres, ejecuta el comando siguiente, sustituyendo cada nombre
de ejemplo por el real. Estos nombres son marcadores, no un diseño propuesto:

```powershell
.\.venv\Scripts\python.exe -m database.diagnostic --expected-tables tabla_1 tabla_2 tabla_3 tabla_4 tabla_5 tabla_6
```

El código de salida **0** requiere que `SELECT 1` sea correcto, que la base
seleccionada sea `DB_NAME`, que haya exactamente seis tablas base visibles y
que existan los seis nombres indicados. Un error de configuración, conexión,
consulta o cierre también devuelve **1**. Los errores de sintaxis de argumentos
devuelven **2**, como es habitual en `argparse`.

Para una comprobación independiente, si tienes instalado el cliente MySQL:

```powershell
mysql --host=localhost --port=3306 --user=root --password --database=f1_historical_database --execute="SELECT 1; SELECT DATABASE(); SHOW FULL TABLES;"
```

`--password` solicita la contraseña de forma interactiva: no la añadas al comando.
Adapta host, puerto, usuario y base a tu configuración.

## Uso desde Python

```python
from database.connection import connection, cursor, transaction

# Lectura con cierre automático de cursor y conexión.
with connection() as db, cursor(db) as query:
    query.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                  "WHERE SCHEMA_NAME = %s", ("f1_historical_database",))
    print(query.fetchone())

# Plantilla de transacción; no ejecuta cambios ni depende de tablas de dominio.
with transaction() as db, cursor(db) as query:
    query.execute("SELECT %s", (1,))
    result = query.fetchone()
```

`transaction()` abre una conexión independiente, inicia una transacción,
confirma al terminar y revierte si falla el bloque o el `commit`, también ante
`KeyboardInterrupt`. Siempre intenta cerrar la conexión. Si falla el rollback,
se conserva el error principal y se añade una nota sobre el resultado incierto.
Un fallo de cierre tampoco sustituye un error previo. `connection()` por sí
solo no confirma cambios; `create_connection()` permite el uso manual y obliga
a quien llama a cerrar el recurso.

Usa `transaction()` para cambios en tablas transaccionales como InnoDB. No
ejecutes DDL (`CREATE`, `ALTER`, `DROP`, etc.) dentro del bloque: MySQL puede
hacer un commit implícito. Cada transacción posee su conexión; no implementa
transacciones anidadas ni puntos de guardado.

Pasa siempre los valores como segundo argumento de `execute()`, en tuplas o
diccionarios. No construyas SQL con f-strings, concatenación ni interpolación
de valores. Los parámetros no representan identificadores SQL; para este
diagnóstico, los nombres de base se comparan como datos en `information_schema`.

## Ejecutar el esquema manualmente en MySQL Workbench

El `schema.sql` actual es un comentario y ejecutarlo **no crea nada**. Primero
debe estar disponible el script aprobado; no se ha generado ni modificado en
esta fase.

Cuando dispongas del script aprobado:

1. Abre MySQL Workbench y conecta con el servidor configurado en `.env`.
2. Usa **File → Open SQL Script** para abrir `sql/schema.sql`.
3. Revisa el script completo antes de ejecutarlo. Comprueba la base de destino
   y que no contenga instrucciones que borren, sustituyan o alteren tablas
   existentes. Si las tablas ya existen, omite su creación.
4. Si el script incluye `CREATE DATABASE` y `USE`, comprueba que señalan la base
   aprobada. Si no los incluye, selecciona con doble clic una base ya existente
   en **SCHEMAS**. La conexión Python no creará una base que falte.
5. Ejecuta únicamente las sentencias aprobadas y necesarias mediante el botón
   del rayo. Revisa **Action Output**, actualiza **SCHEMAS** y comprueba las tablas.
6. Repite el diagnóstico Python con los seis nombres confirmados.

## Errores y pruebas

| Resultado | Qué revisar |
| --- | --- |
| Falta `DB_PASSWORD` | Crear `.env` o definir la variable en el entorno. |
| Contraseña de ejemplo | Sustituir `your_password` localmente. |
| Puerto inválido | Definir `DB_PORT` como entero de 1 a 65535. |
| MySQL 1045 | Usuario, contraseña y autorización para conectar desde ese host. |
| MySQL 1044 | Permisos del usuario sobre la base. |
| MySQL 1049 | La base no existe; revisar `DB_NAME` y el esquema aprobado. |
| MySQL 2002 / 2003 | Servicio MySQL, dirección, puerto y acceso de red. |
| MySQL 2005 | Resolución del nombre del servidor. |
| MySQL 2006 / 2013 | Conexión perdida; disponibilidad del servicio y red. |
| Tablas ausentes o recuento distinto de seis | Esquema aprobado y permisos: el inventario solo incluye tablas visibles. |

Pruebas automatizadas sin conexión real ni credenciales:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Las pruebas aíslan las variables de entorno y simulan el conector. Cubren carga
de dotenv, validación, prioridad del entorno, cierre de recursos, commits,
rollbacks, errores de conexión y comprobación de consultas parametrizadas.

En la verificación local de esta fase se encontraron las dependencias
instaladas y el servicio `MySQL80` en ejecución, pero no `.env` ni variables
`DB_*`. El diagnóstico terminó con código **1** y el mensaje
`Falta DB_PASSWORD en el entorno o en .env.` No se intentó autenticar con una
contraseña inventada. Por tanto, `SELECT 1`, la existencia de la base y las seis
tablas quedan pendientes de verificación real.

Referencias: [argumentos de Connector/Python](https://dev.mysql.com/doc/connector-python/en/connector-python-connectargs.html),
[commit y rollback](https://dev.mysql.com/doc/connector-python/en/connector-python-example-cursor-transaction.html)
y [carga de python-dotenv](https://bbc2.github.io/python-dotenv/reference/).
