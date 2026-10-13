# Estructura del proyecto y de la biblioteca

> Referencia del manual `AGENTS.md` (sección 3). Contenido: 1. el proyecto · 2. la estructura ÚNICA de la biblioteca
> (`biblioteca/`) · 3. temporales, limpieza y lo que queda al cerrar un proceso.

## 1. El proyecto

```
Biblioteca_Legal_Libre_de_Mexico/
├── biblioteca/           LA BIBLIOTECA: PDF/ y Markdown/ (el mismo árbol), catálogo y manifiesto (sección 2)
├── sistema/              todo lo que la mantiene al día
│   ├── biblioteca.py       la ÚNICA entrada: python sistema/biblioteca.py <herramienta> [opciones]
│   ├── herramientas/       el código, un módulo por proceso
│   │   ├── comun.py          rutas, consola, candado, bitácora, temporales (lo usan todos)
│   │   ├── instalar.py       PROCESO 1 (solo biblioteca estándar: corre antes de que exista el entorno)
│   │   ├── catalogo.py       estructura, esquema, pistas y la herramienta `catalogo`
│   │   ├── descargar.py      PROCESO 2: HTTP, lectura de PDF/Word/HTML, fechas, `buscar`, `descargar`, `word`
│   │   ├── convertir.py      PROCESO 3: Markdown estructurado y `convertir`
│   │   ├── comprimir.py      PROCESO 4
│   │   ├── publicar.py       PROCESO 5
│   │   └── verificar.py      integridad y limpieza, `sincronizar` (.agents/ → .claude/) y `pruebas`
│   ├── pruebas/            pruebas automáticas (python sistema/biblioteca.py pruebas), una por módulo
│   ├── fuentes.json        dónde buscar cada entidad: portal, navegación, patrones, trampas
│   ├── bitacora.jsonl      una línea por cada acción de las herramientas (permanente)
│   ├── CAMBIOS.md          qué cambió en cada publicación (lo escribe publicar --preparar)
│   ├── referencia/         esta carpeta: estructura, esquema, actualizar, comprimir
│   └── requirements.txt pyproject.toml
├── README.md LICENSE     para personas, y la licencia
├── AGENTS.md CLAUDE.md   el manual de los agentes y su entrada para Claude Code
├── .agents/ .claude/     originales de agentes y comandos, y sus copias generadas (sincronizar)
└── trabajo/              SOLO durante un proceso: archivos de inspección del agente
```

| Herramienta (`python sistema/biblioteca.py …`) | Módulo |
|---|---|
| `instalar` | `sistema/herramientas/instalar.py` |
| `catalogo` | `sistema/herramientas/catalogo.py` |
| `buscar`, `descargar`, `word` | `sistema/herramientas/descargar.py` |
| `convertir` | `sistema/herramientas/convertir.py` |
| `comprimir` | `sistema/herramientas/comprimir.py` |
| `publicar` | `sistema/herramientas/publicar.py` |
| `verificar`, `sincronizar`, `pruebas` | `sistema/herramientas/verificar.py` |

`requirements.txt` tiene una sección por proceso (`# [actualizar]`, `# [comprimir]`, `# [convertir]`,
`# [desarrollo]`); `instalar --para <proceso>` instala solo la suya.

## 2. Estructura de la biblioteca (la única)

### Carpetas

La biblioteca tiene **dos carpetas con el mismo árbol**: `PDF/`, con cada documento oficial, y `Markdown/`, con su
texto en Markdown. Un documento y su Markdown llevan la misma subcarpeta y el mismo nombre
(`PDF/Federal/Fiscal/Codigo_Fiscal_de_la_Federacion.pdf` y `Markdown/Federal/Fiscal/Codigo_Fiscal_de_la_Federacion.md`).
Los pocos documentos que la fuente oficial publica solo en Word van en `PDF/` tal cual, con su extensión (`.doc`,
`.docx`). **Solo PDF o Word: nunca una página web.**

```
biblioteca/
├── catalogo.json                         QUÉ documentos debe tener la biblioteca + pistas (lo escriben las herramientas)
├── manifiesto.json                       qué hay guardado: SHA-256, URL, fechas de cada documento
├── SHA256SUMS                            huella de cada archivo (la escribe publicar --preparar)
├── PDF/                                  los documentos oficiales (PDF, o Word si la fuente solo publica Word)
│   ├── Constitucion/                     la Constitución federal: está por encima de todo (art. 133)
│   │   └── Reformas/                     sus decretos de reforma
│   ├── Federal/
│   │   ├── Tratados_internacionales/
│   │   │   ├── Derechos_humanos/
│   │   │   ├── Justicia_y_cooperacion/
│   │   │   └── Comercio/
│   │   ├── Administrativo_y_anticorrupcion/
│   │   ├── Agrario/
│   │   ├── Ambiental/
│   │   ├── Amparo_y_Poder_Judicial/
│   │   ├── Civil_y_familiar/
│   │   ├── Electoral/
│   │   ├── Energia/
│   │   ├── Fiscal/
│   │   │   └── Resolucion_Miscelanea_Fiscal_<año>/   la RMF y sus anexos de ese año
│   │   ├── Laboral_y_seguridad_social/
│   │   ├── Mercantil_y_financiero/
│   │   ├── Migracion_y_poblacion/
│   │   ├── Penal_y_seguridad/
│   │   ├── Propiedad_intelectual_y_consumidor/
│   │   ├── Social_salud_y_educacion/
│   │   ├── Telecomunicaciones/
│   │   ├── Transito_y_transporte/
│   │   ├── Transparencia_y_datos_personales/
│   │   └── Urbano_obras_y_bienes_nacionales/
│   │       (cada materia puede tener Abrogadas/ con lo abrogado, y dentro su subgrupo si lo tenía)
│   └── Estados/
│       └── <Estado>/                     todo lo del estado, sin subcarpetas (32 carpetas)
└── Markdown/                             el texto de cada documento (.md): las MISMAS carpetas que PDF/
```

`python sistema/biblioteca.py catalogo grupos` muestra este árbol (el de `PDF/` y `Markdown/`) con cuántos documentos hay en
cada carpeta y el valor exacto que se usa en `--grupo`.

### Reglas

| Regla | Detalle |
|---|---|
| **Cada materia junta todo lo suyo** | Códigos, leyes y reglamentos van en su materia: el Código Penal Federal en `Penal_y_seguridad/`, el Código Fiscal en `Fiscal/`, el Código de Comercio en `Mercantil_y_financiero/`. No hay carpeta de "códigos" |
| **Lo abrogado va aparte** | Federal: `<Materia>/Abrogadas/` (conserva su subgrupo: `Fiscal/Abrogadas/Resolucion_Miscelanea_Fiscal_2026/`). `catalogo marcar --estado abrogado` lo mueve solo, y si vuelve a `vigente`, lo regresa. La Constitución, los tratados y los estados no usan `Abrogadas/` |
| **Estados sin subcarpetas** | `Estados/<Estado>/`, con el nombre del estado sin acentos y con `_` (`Estados/Ciudad_de_Mexico/`). Un documento con `entidad` solo puede estar en la carpeta de su estado |
| **Nombre del archivo = nombre oficial** | `Código Fiscal de la Federación` → `Codigo_Fiscal_de_la_Federacion.pdf` / `.md` (sin acentos, sin signos, palabras unidas con `_`). Si el nombre oficial cambia (`renombrar`), el archivo cambia con él |
| **Sin carpetas nuevas** | Solo existen las materias y subcarpetas de la lista. Una materia o subcarpeta nueva es una decisión del usuario: se agrega en `sistema/herramientas/catalogo.py` (`MATERIAS`, `TEMAS_TRATADOS`, `SUBGRUPOS_CONSTITUCION`) y en este documento |
| **El id no indica la carpeta** | El id es permanente (el manifiesto y la bitácora lo usan) y no cambia al mover o renombrar. Todos los ids son `<estado>__<archivo>` o `federal__<archivo>` (los federales heredados con otro prefijo se migraron el 2026-10-06: tabla viejo → nuevo en `esquema.md`, sección 3); el prefijo tampoco indica la carpeta |

### Qué hace cada proceso con la estructura

| Proceso | Qué pasa |
|---|---|
| **Agregar** (`catalogo agregar`) | Federal: `--grupo` obligatorio y debe ser una carpeta de la estructura. Estatal: basta `--entidad` (uno de los 32 estados) y va solo a `Estados/<Estado>`. Rechaza duplicados |
| **Descargar** (`descargar`) | Guarda el PDF en `PDF/<grupo>/<archivo>.pdf` (o el Word oficial con su extensión; una página web, nunca). Si el documento no está en la estructura, se detiene sin escribir nada |
| **Convertir** (`convertir`) | Escribe el Markdown en `Markdown/<grupo>/<archivo>.md`: la misma carpeta y el mismo nombre que su documento en `PDF/`. Omite (y lo dice) un documento fuera de la estructura |
| **Comprimir** (`comprimir`) | Reemplaza el PDF en su mismo lugar dentro de `PDF/` (no crea otras carpetas). Omite un documento fuera de la estructura |
| **Mover** (`catalogo mover <id> --grupo "..."` o `--mapa lista.txt`) | Mueve a la vez el documento (en `PDF/`) y su Markdown (en `Markdown/`), corrige catálogo y manifiesto, deja el archivo con su nombre oficial y borra las carpetas que quedan vacías. Rechaza carpetas fuera de la estructura |
| **Marcar** (`catalogo marcar`) | Abrogado ↔ vigente mueve el documento a/desde `Abrogadas/` |
| **Renombrar** (`catalogo renombrar`) | Cambia el nombre del documento y de su Markdown al nuevo nombre oficial, en sus mismas carpetas |
| **Retirar** (`catalogo retirar-pdf`) | Quita el documento (con una copia en `anteriores/`, en la carpeta temporal, hasta el cierre) y su Markdown |
| **Verificar** (`verificar`) | ERROR si una carpeta no es de la estructura, si un abrogado no está en `Abrogadas/` (o al revés), si el archivo no corresponde al nombre, si el manifiesto apunta a otra ruta, o si hay documentos, Markdown u otros archivos sueltos o fuera de su carpeta (un Markdown en `PDF/`, un documento en `Markdown/` o cualquier archivo fuera de las dos). Cada error trae el comando que lo corrige |

Nunca muevas, copies ni renombres a mano archivos de `biblioteca/`: usa `catalogo mover` o `renombrar`.

## 3. Temporales, limpieza y lo que queda al cerrar

### Lo que no es el proyecto vive fuera de la carpeta

| Qué | Dónde | Se borra |
|---|---|---|
| Entorno de Python (`entorno/`), idioma español del OCR (`tessdata/`), `entorno.json` y el archivo del candado | Carpeta de datos del usuario: `~/Library/Caches/biblioteca_legal_libre/<huella>/` (macOS), `%LOCALAPPDATA%\biblioteca_legal_libre\<huella>\` (Windows), `~/.cache/biblioteca_legal_libre/<huella>/` (Linux) | Nunca solo: se reutiliza entre procesos. `instalar --quitar` lo borra a mano |
| Carpeta de cada ejecución (`ejecucion_…`), caché del índice de Diputados (`cache/`) y copia del documento reemplazado (`anteriores/`) | Carpeta temporal del sistema: `<temporal>/biblioteca_legal_libre/<huella>/` (`%TEMP%` en Windows, `$TMPDIR` o `/tmp` en macOS/Linux) | Al terminar cada ejecución; la caché a las 6 horas; todo, con `verificar --limpiar` |

`<huella>` son 10 caracteres que identifican a esta copia del proyecto (dos copias no se pisan).

- La carpeta de cada ejecución se borra sola al terminar, aunque la herramienta falle o se interrumpa con Ctrl+C o
  Ctrl+Break. **Si un proceso muere de golpe** (se cierra la terminal o lo matan), su carpeta se borra en cuanto
  cualquier herramienta se vuelve a ejecutar y detecta que ese proceso ya no existe; como red de seguridad, también
  pasadas 12 horas.
- Los procesos hijos (el `curl` de respaldo, las conversiones) se detienen junto con la herramienta, incluso si a
  esta la matan a la fuerza (en Windows van en un *Job Object* que el sistema cierra): no quedan procesos sueltos.
- **Dentro del proyecto** las herramientas no dejan nada: no generan `__pycache__` (`biblioteca.py` lo desactiva),
  `pruebas` deja las cachés de pytest, ruff y mypy en la carpeta temporal, y el único resto posible es el `*.tmp`
  momentáneo con que se guardan de forma atómica el catálogo, `fuentes.json`, el manifiesto y cada documento (existe
  milisegundos).
- **Archivos de trabajo del agente** (listados, pruebas): van en `trabajo/`, nunca fuera del proyecto. Los
  subagentes los nombran con el prefijo de su bloque (`trabajo/<bloque>_...`).

### Al cerrar cada proceso

El agente principal ejecuta `python sistema/biblioteca.py verificar --limpiar`, que borra `trabajo/`, `anteriores/`, restos
`*.tmp` y `*.pyc`, `__pycache__`, las carpetas vacías de `biblioteca/`, la caché y la carpeta temporal del sistema de
este proyecto (sin tocar la de las herramientas que sigan trabajando), y lo que el sistema operativo o las herramientas
de desarrollo crean por su cuenta (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `.ruff_cache/`, `.mypy_cache/`,
`.pytest_cache/`). Si algo no se puede borrar (porque otro programa lo tiene abierto), lo avisa. Los subagentes no
limpian a la mitad, porque borrarían lo de los demás.

### Control (`verificar`)

- **ERROR** por cualquier temporal que quede en el proyecto (los `*.tmp`/`*.pyc` de menos de 10 minutos no, por si
  son una escritura en curso; `--limpiar` sí los borra).
- Los archivos de `trabajo/` solo son **AVISO** mientras varios agentes trabajan en paralelo; con `--final`
  o `--limpiar` cuentan como ERROR.
- **AVISO** por cualquier archivo o carpeta de la raíz o de `sistema/` que no sea parte del proyecto (el árbol de la
  sección 1; las carpetas de los editores, `.vscode/`, `.idea/` y `.cursor/`, y un `.venv/` propio no se avisan y git
  las ignora). Los avisos no se borran solos, porque podrían ser de una persona: si los creó un agente, se borran y se
  repite.
- **ERROR** por un enlace simbólico dentro de `biblioteca/` (git lo publicaría tal cual) y por un original de
  `.agents/` que falta o está mal formado (no se pueden generar sus copias de `.claude/`).

**Lo único que queda en la carpeta al cerrar** (todo lo demás se borra, siempre, aunque el proceso falle o se
cancele): los archivos del árbol de la sección 1, sin `trabajo/`, y `biblioteca/` sin carpetas vacías. El agente
principal no da por terminado un proceso hasta que `verificar` diga 0 temporales, 0 avisos e `INTEGRIDAD: OK`.
