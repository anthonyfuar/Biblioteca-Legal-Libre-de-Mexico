# Esquema de los datos públicos: catálogo, manifiesto y Markdown

> Interfaz pública de la biblioteca (otros proyectos la leen). Contenido: 1. catálogo y manifiesto (esquema 3) ·
> 2. Markdown estructurado (formato 2) · 3. ids heredados de la versión 1.

## 1. Catálogo y manifiesto (esquema 3)

Los dos archivos son la interfaz pública de la biblioteca: otros proyectos los leen para saber qué documentos hay,
dónde están y si cambiaron. Por eso su esquema tiene **versión** (`sistema/herramientas/comun.py`) y solo cambia con una
migración documentada aquí. Los escriben únicamente las herramientas (`python sistema/biblioteca.py …`, nunca a mano);
`verificar` comprueba cada entrada contra este esquema y reporta como ERROR lo que no cumpla.

Convenciones comunes: codificación UTF-8, finales de línea `\n`, fechas `AAAA-MM-DD`, huellas **SHA-256** en
hexadecimal de 64 caracteres, rutas relativas con `/`. Los campos opcionales **se omiten** cuando no aplican (nunca
se guardan vacíos o en `null`, salvo donde se indica).

### `biblioteca/catalogo.json` — qué documentos debe tener la biblioteca

```json
{
  "descripcion": "...",
  "version": 3,
  "actualizado": "2026-10-06",
  "documentos": [ { ...documento... } ]
}
```

| Campo del documento | Tipo | Siempre | Significado |
|---|---|---|---|
| `id` | texto | sí | Identificador **permanente y opaco**: `federal__<archivo en minúsculas>` o `<estado>__<archivo en minúsculas>` al crearse; nunca cambia después, ni al renombrar ni al mover (por eso puede no coincidir con el `archivo` actual). Es la clave del manifiesto |
| `nombre` | texto | sí | Nombre oficial vigente (con acentos) |
| `grupo` | texto | sí | Subcarpeta, la misma dentro de `biblioteca/PDF/` (el documento oficial) y de `biblioteca/Markdown/` (su Markdown): `Constitucion`, `Federal/<Materia>[/Abrogadas]`, `Federal/Tratados_internacionales/<Tema>`, `Estados/<Estado>`; ver `estructura.md` |
| `archivo` | texto | sí | Nombre de archivo sin extensión = `nombre` sin acentos y con `_` |
| `estado` | `vigente` · `abrogado` · `no_existe` · `no_localizado` | sí | Situación jurídica o de búsqueda |
| `entidad` | texto | solo estatales | Nombre del estado tal como está en `fuentes.json` (con acentos). Ausente = federal |
| `nota` | texto | no | Justificación del estado o del último cambio, con fuente; `marcar`, `renombrar` y `retirar-pdf` le añaden `(verificado AAAA-MM-DD)` |
| `nombres_anteriores` | lista de textos | no | Nombres que tuvo antes de cada `renombrar` |
| `sin_pdf` | `true` | no | Solo en `abrogado`: se buscó y su texto ya no está en ninguna fuente oficial en línea (deja de ser pendiente) |
| `aviso` | texto | no | Hallazgo de una investigación por resolver (abrogada, renombrada...); `marcar`/`renombrar` lo quitan |
| `pistas.urls` | lista | sí (puede estar vacía) | Hasta 10 URL donde se encontró o buscó el documento, la más reciente primero: `{url, fecha, resultado, nota?}` con `resultado` ∈ `ok` · `falla` · `sin_probar`. **No son la fuente del enlace vigente**: el agente consulta el listado del portal; una verificación `SIN_CAMBIO` no las toca |
| `pistas.indice_diputados` | texto | no | Nombre con el que el documento aparece en el índice de la Cámara de Diputados (`buscar diputados`) |

Lo federal/estatal se deduce de `entidad` (no hay campo `ambito`).

### `biblioteca/manifiesto.json` — qué hay guardado

```json
{
  "descripcion": "...",
  "version": 3,
  "actualizado": "2026-10-06",
  "documentos": { "<id>": { ...registro... } }
}
```

| Campo del registro | Tipo | Siempre | Significado |
|---|---|---|---|
| `archivo` | texto | sí | Ruta relativa a `biblioteca/` del documento oficial: `PDF/<grupo>/<archivo>.<formato>` |
| `formato` | `pdf` · `doc` · `docx` | sí | Formato real del archivo oficial (los pocos Word van en `PDF/` con su extensión real). Nunca una página web |
| `sha256` | hex 64 | sí | Huella del archivo tal como está en disco |
| `bytes` | entero | sí | Tamaño en bytes del archivo en disco |
| `url` | texto | sí | URL oficial de la que se descargó |
| `fecha_reforma` | `AAAA-MM-DD` o `null` | sí | Última reforma leída en la portada (o indicada con `--reforma`); `null` si la portada no la trae |
| `fecha_descarga` | `AAAA-MM-DD` | sí | Cuándo se guardó esta versión |
| `ultima_verificacion` | `AAAA-MM-DD` | sí | Última vez que se comprobó contra la fuente (aunque no cambiara) |
| `markdown` | objeto o `null` | sí | `{archivo, sha256_origen, metodo, caracteres, fecha, formato}`: su ruta (`Markdown/<grupo>/<archivo>.md`), la huella del documento **oficial** del que salió, `metodo` ∈ `markitdown` · `ocr` · `lector_word`, caracteres del Markdown final, fecha en que se generó y `formato` = versión del Markdown estructurado (hoy **2**: front-matter y encabezados, sección 2; ausente = 1, el texto crudo de MarkItDown). Si `sha256_origen` no coincide con la huella del oficial, el Markdown está desactualizado; si `formato` es menor que el vigente, `convertir --reestructurar` lo vuelve a formatear sin reconvertir |
| `compresion` | objeto | solo si se comprimió | `{nivel, sha256_original, bytes_original, sha256, bytes, fecha, verificacion, detalle}`: huella y tamaño del PDF **oficial** antes de comprimir, los del comprimido (= `sha256`/`bytes` del registro) y el resultado de la verificación |
| `alarma_confirmada` | texto | no | Texto de la alarma de nombre que el agente confirmó con `--forzar` para esta URL (no se vuelve a pedir mientras sea idéntica) |

**Huella del oficial**: `compresion.sha256_original` si existe, si no `sha256`. Con ella se compara cada nueva descarga
(`SIN_CAMBIO` si coincide) y se decide si el Markdown está al día.

### Para quien consume la biblioteca
- Resuelve cada documento por `id` (manifiesto → `archivo` y `markdown.archivo`); no supongas la extensión `.pdf`.
- Detecta cambios por `sha256` (o por `compresion.sha256_original` si quieres la huella del oficial), no por fecha.
- `fecha_reforma` puede ser `null` (la portada no siempre la trae).
- El front-matter de cada Markdown repite `id`, `sha256_origen` y `fuente`: un `.md` suelto se puede volver a asociar a
  su documento sin el manifiesto.
- Los textos oficiales no están sujetos a derechos de autor; el resto del proyecto, a `LICENSE`.

### Historial
- **v3 (2026-10-08)**: la carpeta de la biblioteca pasa a llamarse `biblioteca/` (antes `Documentos/`), con sus
  carpetas `PDF/` y `Markdown/`, y un solo catálogo, `biblioteca/catalogo.json` (antes también estaba en la raíz del
  proyecto); el campo `archivo` del front-matter de cada Markdown sigue al del manifiesto; `formato` admite solo `pdf`,
  `doc` y `docx` (la biblioteca guarda solo PDF o Word, nunca páginas web).
- **v2 (2026-10-06)**: SHA-256 en lugar de MD5 (`md5` → `sha256`, `md5_original` → `sha256_original`,
  `md5_pdf_origen` → `sha256_origen`); `fecha_reforma` en `AAAA-MM-DD` (antes `DD-MM-AAAA`); `pdf` → `archivo` +
  `formato`; `nombre_confirmado` → `alarma_confirmada`; manifiesto con `descripcion`/`version`/`actualizado`/`documentos`
  (antes el archivo era directamente el diccionario por id); catálogo sin `ambito`, sin `nota` vacía, y
  `pistas.urls[].resultado` restringido a `ok`/`falla`/`sin_probar` (el detalle va en `nota`).
  El mismo día, sin cambiar el esquema, los 188 ids federales heredados (`leyes_federales_*__…`,
  `constitucion_y_reformas__…`, `tratados_internacionales__…`, etc.) pasaron a `federal__<archivo>`: tabla completa
  viejo → nuevo en la sección 3.
- **v2, mismo día**: `markdown.formato` (entero) en cada registro con Markdown, al introducir el Markdown estructurado
  (formato 2).
- **v1**: esquema original (hasta 2026-10-06).

## 2. Markdown estructurado (formato 2)

**Regla número uno: no se pierde ni se altera una sola palabra del texto oficial.** El posprocesado solo cambia la
presentación (encabezados, párrafos, ruido de paginación). Antes de aceptar un resultado, la validación comprueba,
contra el texto crudo SIN normalizar: que las palabras (con acentos y ñ) son las del crudo menos las de las líneas
quitadas (números de página y cabeceras repetidas); que los números con signos ("5/DEC-12", "171-1", "09-04-2026")
no cambian; que el orden de las palabras se conserva (pares consecutivos); y, por separado, que ninguna línea quitada
parece contenido (una "cabecera" que termina en ";" o es nota de reforma, un número suelto junto a otro número
quitado, una variante rara que continuaba la frase anterior). Si algo no cuadra, se guarda el texto crudo
(`markdown.formato: 1`) y la herramienta dice por qué. La validación va en el informe de `estructurar`
(`informe["validacion"]`); `validar(crudo, resultado)` hace lo mismo desde fuera.

### 1. Front-matter

Cada archivo empieza con un bloque YAML entre `---`, con estas claves en este orden: `id`, `nombre`, `entidad` (`null`
si es federal), `grupo`, `estado`, `archivo` (ruta del documento oficial dentro de `biblioteca/`, con `PDF/` al principio), `fuente` (URL
oficial), `fecha_reforma` (`AAAA-MM-DD` o `null`), `sha256_origen` (huella del documento oficial del que salió),
`metodo` (`markitdown`, `ocr` o `lector_word`), `convertido` (`AAAA-MM-DD`), `version` (2) y `aviso` ("Conversión
automática del documento oficial; ante cualquier diferencia prevalece el PDF publicado en la fuente oficial"). Los
valores con comillas, dos puntos u otros caracteres especiales van entre comillas dobles; las fechas y la huella,
siempre entre comillas. Se escribe y se lee sin PyYAML (lector mínimo `clave: valor`), pero es YAML válido.

### 2. Limpieza

- **Números de página**: las formas inequívocas ("12 de 345", "Página 12", "Pág. 12", "- 12 -", "## Página 12" del
  OCR) se quitan solo si son el folio del documento (la misma forma se repite 3 o más veces), la marca del OCR o están
  junto a un salto de página: "Página: 531" en la ficha de una tesis o "Página 31" + "DICE:" de una fe de erratas son
  contenido. Un número suelto solo se quita si está pegado a un corte de página, tiene el estilo de folio del
  documento (todos con punto o todos sin él), forma secuencia en el sentido correcto con el folio de una página vecina
  y, si el documento ya tiene un folio inequívoco, lo continúa. Así se conservan "…del artículo" + "160." (queda como
  párrafo propio), las etiquetas "23." … "45." de una lista, los años ("…del ejercicio fiscal" + "2025."), los números
  de partida de una tarifa y las columnas de montos.
- **Cabeceras y pies de página repetidos** (nombre de la ley en cada página, "Secretaría General", "Última Reforma
  DOF …", "H. Congreso del Estado…"): líneas cortas (≤ 160 caracteres) repetidas 4 o más veces **y pegadas a un corte
  de página** (`\x0c`, "## Página N", folios o, en los PDF sin `\x0c`, la línea en blanco que separa las páginas) en
  al menos el 60 % de sus apariciones y en al menos el 40 % de las páginas, comparadas en su forma exacta (con
  mayúsculas y signos: "Guerrero;" al final de una fracción no es la cabecera "GUERRERO."). **Se quita la
  repetición, no el dato**: la copia de la primera página se conserva (los metadatos de Morelos "Publicación
  2006/09/06", "Vigencia 2006/10/01" quedan una vez); de un pie de varias líneas o con frases completas (el aviso
  del SAT en la RMF, la "NOTA DE EDITOR" de Guanajuato) se conserva la primera copia entera, y una copia distinta
  (OCR, otro corte de línea) se conserva entera en vez de mutilarla. Ninguna copia conservada puede partir una frase
  (si el texto de antes y el de después se unirían, se elige otra copia o ninguna). Un bloque idéntico a la cabecera
  habitual al que la extracción solo intercaló cuerpo ("XV.-", "predio.") es repetición: se conserva una sola vez. Una
  cabecera que no está en la primera página se quita entera (conservarla la dejaría en medio de una frase). Nunca se quitan encabezados jurídicos, notas de
  reforma ("(REFORMADO, P.O. …)", "Párrafo reformado DOF …", también numeradas al pie: "2 Fracción reformada el
  …"), listas, etiquetas ("I.", "a)"), filas de tabla, una línea suelta que termina en palabra de enlace o coma y
  cierra la página (frase cortada por el salto; si la abre, como "LEY NÚMERO 450 DE VÍCTIMAS DEL ESTADO LIBRE Y", es
  cabecera) ni nada en documentos de menos de 4 páginas. Para ser cabecera, la línea debe ir pegada a un corte en el
  40 % de las páginas DISTINTAS (no de las líneas: una columna de índice "DEL 1 AL 14" no es cabecera).
- **Guiones de fin de línea**: "desa-" + "rrollo" se une sin espacio; el guion se quita solo si la palabra unida ya
  existe en el documento ("desarrollo"); si no, se conserva ("político-administrativos").
- Dobles espacios, espacios finales y caracteres de control (se cambian por espacio: nunca pegan palabras; un
  `\x0c` a media línea parte la línea).

### 3. Párrafos

Las líneas cortadas a media frase por el PDF se unen: una línea que no termina en `. : ; ) ] " » — ? !` seguida de
una que empieza en minúscula, dígito, "(" + minúscula o signo; también si termina en palabra de enlace (de, la,
del, y, que, al…) o en coma, aunque la siguiente empiece en mayúscula. Nunca se une hacia o desde encabezados,
rótulos de artículo, listas ("- ", "1.", "I.-", "a)"), tablas, notas de reforma ni bloques de código. Cada
párrafo queda en una sola línea; los bloques se separan con una línea en blanco; tablas, listas e índices van
seguidos. Lo que queda sin unir (unas 5 líneas de cada 100 cortadas) es lo que la regla no puede decidir sin
riesgo: una línea que termina en palabra normal seguida de una que empieza en mayúscula.

### 4. Encabezados

| Unidad | Encabezado | Regla |
|---|---|---|
| LIBRO > TÍTULO > CAPÍTULO > SECCIÓN / APARTADO | `#`, `##`, `###` | La unidad mayor presente en el documento recibe `#`, la siguiente `##`, la siguiente `###`; si hay las cuatro clases, Capítulo y Sección comparten `###` para que los artículos queden siempre en el nivel 4. El rótulo se conserva tal cual; si el nombre va en la línea siguiente se incorpora: `## CAPÍTULO I. De los Derechos Humanos y sus Garantías` |
| TRANSITORIOS (también "ARTÍCULOS TRANSITORIOS", "T R A N S I T O R I O S", "DISPOSICIONES TRANSITORIAS") | `##` | Siempre nivel 2 |
| Artículo | `#### Artículo N` | N tal como viene (12, 12 Bis, 76 Quaterdecies, 228 b, 12-A, 17-H Bis, 1.2 y 7.1000 del Estado de México y del T-MEC, 1o., 1°., Primero, Único); "Art.", "ARTICULO" y "Artículo12" se normalizan a "Artículo". El texto que seguía en la misma línea ("Artículo 5.- Las disposiciones…") pasa al párrafo siguiente; en "Artículo 5o. A ninguna persona…" la "A" es preposición, no sufijo |

Condiciones para convertir artículos: el documento tiene al menos 3 artículos numerados y su numeración es no
decreciente en al menos el 70 % de los pasos; los ordinales ("Artículo Primero/Único") solo después del primer
TRANSITORIOS o cuando el documento no tiene artículos numerados (Leyes de Ingresos, decretos). Nunca se convierten
las menciones dentro de un párrafo ("…conforme al Artículo 5 de la Ley…", "ARTÍCULO 15 BIS Y SE ADICIONAN…"), los
apéndices de historial ("ARTÍCULO 492.- fue reformado por Decreto No. 362…", Baja California) ni las líneas de un
índice (puntos de guía con número de página, también en la línea siguiente al rótulo como en Sonora y Nayarit;
tiradas de 8 o más rótulos sin texto; o rótulos con título "ARTÍCULO 1. Objeto", aunque los separen los nombres de
capítulo, confirmados como índice porque después la numeración reinicia, como en Yucatán): quedan como líneas
seguidas sin encabezado. Tampoco las menciones cortadas por el salto de línea ("…que establece el" + "Artículo 292.",
"…LA DENOMINACIÓN DEL" + "CAPÍTULO V DEL TÍTULO NOVENO…"): el número fuera de la serie de artículos delata la
mención y se une a su frase. Si el nombre de una unidad quedó cortado ("SECCIÓN QUINTA A" + "DE LA COMISIÓN…",
"Sección Segunda De" + "la Reclamación"), se completa con la línea siguiente. Una serie "APARTADO A, B, …" que empieza después de un artículo es subdivisión de ese artículo
(como el 123 constitucional) y queda como párrafo; solo es unidad (`###`) si no hay un artículo antes. Un rótulo duplicado por el PDF ("ARTÍCULO ÚNICO: ARTÍCULO ÚNICO: Se reforman…") produce dos
encabezados seguidos (fiel al original).

### 5. Lo que queda fuera (a propósito)

- Documentos sin artículos al inicio de línea (la RMF y sus anexos, que son reglas; tratados cuyos artículos van
  en medio de la línea; decretos con un solo "ARTÍCULO ÚNICO") no reciben encabezados de artículo.
- Transitorios escritos "PRIMERO.-" sin la palabra Artículo quedan como párrafos.
- Una referencia cortada por el salto de página ("…del artículo" + "160.") no se reúne con su frase: queda como
  párrafo propio (sin perder el número).
- Cabeceras garabateadas por el OCR, distintas en cada página, y las que cambian por capítulo no se detectan.
- Un Markdown crudo de una sola línea (extracción rota de MarkItDown) no se puede estructurar: hay que reconvertir
  el documento (`convertir --forzar --id <id>`) y, si MarkItDown repite el resultado, revisarlo a mano.

## 3. Ids heredados (v1) de los documentos federales

Los `id` de `catalogo.json` son **permanentes y opacos**: identifican a cada documento para siempre (son la clave de
`biblioteca/manifiesto.json` y los usa `bitacora.jsonl`), no indican su carpeta y no cambian al renombrar
(`catalogo renombrar`) ni al mover (`catalogo mover`) el documento. El esquema vigente es **un solo prefijo por
ámbito**: `<estado>__<archivo>` para los estatales (p. ej. `jalisco__codigo_civil_del_estado_de_jalisco`) y
`federal__<archivo>` para los federales, siempre sobre el campo `archivo` en minúsculas y sin más caracteres que
`a-z`, `0-9` y `_` (la regla de `catalogo agregar`: `re.sub(r"[^a-z0-9_]", "", ("federal__" + archivo).lower())`).

Los 188 documentos federales de la primera versión del catálogo (v1) nacieron con prefijos de un esquema anterior,
tomados del grupo temático que tenían entonces: `leyes_federales_<materia>__…`, `constitucion_y_reformas__…`,
`tratados_internacionales__…`, `codigos_federales_y_nacionales__…` y `transito_movilidad_y_autotransporte_federal__…`.
**El 2026-10-06 esos 188 ids pasaron a `federal__<archivo>`**, calculados con la regla anterior sobre el `archivo`
**actual** de cada documento. Es la única excepción, hecha una sola vez, a la permanencia de los ids; se hizo para que
todo el catálogo siga un solo esquema. Lo que **no** cambió:

- La versión del esquema (entonces v2) y el formato de `catalogo.json` y del manifiesto (sección 1).
- Los archivos de la biblioteca (entonces `Documentos/PDF/` y `Documentos/Markdown/`): su ruta no lleva el id, así
  que nada se movió.
- Las huellas SHA-256, URLs y fechas de cada registro del manifiesto: solo se renombró su clave.
- Las líneas anteriores de la bitácora: el historial no se reescribe. Para leerlas, traduce el id con la tabla de abajo.
  El cambio quedó registrado como un evento más (`"herramienta": "migracion", "accion": "ids_federales"`, con el mapa completo).

Si consumes la biblioteca y guardaste ids de la v1, tradúcelos con esta tabla. De los 188, 184 tenían registro en el
manifiesto y 4 no (nunca descargados o abrogados sin PDF en línea).
En 5 casos el documento se había renombrado antes de la migración (`nombres_anteriores`), por lo que el sufijo
nuevo refleja el nombre oficial vigente y no el que tenía el id viejo:

- `leyes_federales_constitucional_amparo_y_administrativo__ley_reglamentaria_de_las_fracciones_i_y_ii_del_articulo_105_constitucional` → `federal__ley_reglamentaria_de_las_fracciones_i_y_ii_del_articulo_105_de_la_constitucion_politica_de_los_estados_unidos_mexicanos`
- `leyes_federales_familia_salud_y_educacion__ley_general_para_la_igualdad_entre_mujeres_y_hombres` → `federal__ley_general_para_la_igualdad_sustantiva_entre_mujeres_y_hombres`
- `leyes_federales_penal_y_seguridad__ley_general_de_acceso_de_las_mujeres_a_una_vida_libre_de_violencia` → `federal__ley_general_de_acceso_de_las_mujeres_a_una_vida_libre_de_violencias`
- `transito_movilidad_y_autotransporte_federal__reglamento_de_la_ley_de_caminos_puentes_y_autotransporte_federal` → `federal__reglamento_de_autotransporte_federal_y_servicios_auxiliares`
- `tratados_internacionales__convencion_de_la_haya_sobre_apostilla` → `federal__convenio_por_el_que_se_suprime_la_exigencia_de_legalizacion_de_los_documentos_publicos_extranjeros_convencion_de_la_apostilla_la_haya_5_de_octubre_de_1961`

### Tabla completa (ordenada por el id anterior)

| Id anterior (v1) | Id vigente |
|---|---|
| `codigos_federales_y_nacionales__codigo_civil_federal` | `federal__codigo_civil_federal` |
| `codigos_federales_y_nacionales__codigo_de_comercio` | `federal__codigo_de_comercio` |
| `codigos_federales_y_nacionales__codigo_de_justicia_militar` | `federal__codigo_de_justicia_militar` |
| `codigos_federales_y_nacionales__codigo_federal_de_procedimientos_civiles` | `federal__codigo_federal_de_procedimientos_civiles` |
| `codigos_federales_y_nacionales__codigo_federal_de_procedimientos_penales` | `federal__codigo_federal_de_procedimientos_penales` |
| `codigos_federales_y_nacionales__codigo_fiscal_de_la_federacion` | `federal__codigo_fiscal_de_la_federacion` |
| `codigos_federales_y_nacionales__codigo_militar_de_procedimientos_penales` | `federal__codigo_militar_de_procedimientos_penales` |
| `codigos_federales_y_nacionales__codigo_nacional_de_procedimientos_civiles_y_familiares` | `federal__codigo_nacional_de_procedimientos_civiles_y_familiares` |
| `codigos_federales_y_nacionales__codigo_nacional_de_procedimientos_penales` | `federal__codigo_nacional_de_procedimientos_penales` |
| `codigos_federales_y_nacionales__codigo_penal_federal` | `federal__codigo_penal_federal` |
| `constitucion_y_reformas__constitucion_politica_de_los_estados_unidos_mexicanos` | `federal__constitucion_politica_de_los_estados_unidos_mexicanos` |
| `constitucion_y_reformas__decreto_de_reforma_a_la_ley_de_amparo_dof_14_jun_2024` | `federal__decreto_de_reforma_a_la_ley_de_amparo_dof_14_jun_2024` |
| `constitucion_y_reformas__decreto_de_reforma_al_articulo_2o_en_materia_indigena_dof_30_sep_2024` | `federal__decreto_de_reforma_al_articulo_2o_en_materia_indigena_dof_30_sep_2024` |
| `constitucion_y_reformas__decreto_de_reforma_de_la_guardia_nacional_dof_30_sep_2024` | `federal__decreto_de_reforma_de_la_guardia_nacional_dof_30_sep_2024` |
| `constitucion_y_reformas__decreto_de_reforma_del_poder_judicial_dof_15_sep_2024` | `federal__decreto_de_reforma_del_poder_judicial_dof_15_sep_2024` |
| `constitucion_y_reformas__decreto_de_reforma_en_materia_de_acceso_a_la_informacion_dof_20_dic_2024` | `federal__decreto_de_reforma_en_materia_de_acceso_a_la_informacion_dof_20_dic_2024` |
| `constitucion_y_reformas__decreto_de_reforma_en_materia_de_bienestar_animal` | `federal__decreto_de_reforma_en_materia_de_bienestar_animal` |
| `constitucion_y_reformas__decreto_de_reforma_en_materia_de_igualdad_sustantiva_dof_15_ene_2026` | `federal__decreto_de_reforma_en_materia_de_igualdad_sustantiva_dof_15_ene_2026` |
| `constitucion_y_reformas__decreto_de_reforma_en_materia_de_jornada_laboral_de_40_horas` | `federal__decreto_de_reforma_en_materia_de_jornada_laboral_de_40_horas` |
| `constitucion_y_reformas__decreto_de_reforma_en_materia_de_maiz_nativo` | `federal__decreto_de_reforma_en_materia_de_maiz_nativo` |
| `constitucion_y_reformas__decretos_de_reformas_constitucionales_cronologico` | `federal__decretos_de_reformas_constitucionales_cronologico` |
| `leyes_federales_agrario__ley_agraria` | `federal__ley_agraria` |
| `leyes_federales_agrario__ley_organica_de_los_tribunales_agrarios` | `federal__ley_organica_de_los_tribunales_agrarios` |
| `leyes_federales_ambiental__ley_de_aguas_nacionales` | `federal__ley_de_aguas_nacionales` |
| `leyes_federales_ambiental__ley_federal_de_responsabilidad_ambiental` | `federal__ley_federal_de_responsabilidad_ambiental` |
| `leyes_federales_ambiental__ley_general_de_cambio_climatico` | `federal__ley_general_de_cambio_climatico` |
| `leyes_federales_ambiental__ley_general_de_desarrollo_forestal_sustentable` | `federal__ley_general_de_desarrollo_forestal_sustentable` |
| `leyes_federales_ambiental__ley_general_de_pesca_y_acuacultura_sustentables` | `federal__ley_general_de_pesca_y_acuacultura_sustentables` |
| `leyes_federales_ambiental__ley_general_de_vida_silvestre` | `federal__ley_general_de_vida_silvestre` |
| `leyes_federales_ambiental__ley_general_del_equilibrio_ecologico_y_la_proteccion_al_ambiente` | `federal__ley_general_del_equilibrio_ecologico_y_la_proteccion_al_ambiente` |
| `leyes_federales_ambiental__ley_general_para_la_prevencion_y_gestion_integral_de_los_residuos` | `federal__ley_general_para_la_prevencion_y_gestion_integral_de_los_residuos` |
| `leyes_federales_asentamientos_y_desarrollo_urbano__ley_de_adquisiciones_arrendamientos_y_servicios_del_sector_publico` | `federal__ley_de_adquisiciones_arrendamientos_y_servicios_del_sector_publico` |
| `leyes_federales_asentamientos_y_desarrollo_urbano__ley_de_obras_publicas_y_servicios_relacionados_con_las_mismas` | `federal__ley_de_obras_publicas_y_servicios_relacionados_con_las_mismas` |
| `leyes_federales_asentamientos_y_desarrollo_urbano__ley_de_vivienda` | `federal__ley_de_vivienda` |
| `leyes_federales_asentamientos_y_desarrollo_urbano__ley_general_de_asentamientos_humanos_ordenamiento_territorial_y_desarrollo_urbano` | `federal__ley_general_de_asentamientos_humanos_ordenamiento_territorial_y_desarrollo_urbano` |
| `leyes_federales_asentamientos_y_desarrollo_urbano__ley_general_de_bienes_nacionales` | `federal__ley_general_de_bienes_nacionales` |
| `leyes_federales_civil_mercantil_y_financiero__ley_de_concursos_mercantiles` | `federal__ley_de_concursos_mercantiles` |
| `leyes_federales_civil_mercantil_y_financiero__ley_de_instituciones_de_credito` | `federal__ley_de_instituciones_de_credito` |
| `leyes_federales_civil_mercantil_y_financiero__ley_de_instituciones_de_seguros_y_de_fianzas` | `federal__ley_de_instituciones_de_seguros_y_de_fianzas` |
| `leyes_federales_civil_mercantil_y_financiero__ley_de_inversion_extranjera` | `federal__ley_de_inversion_extranjera` |
| `leyes_federales_civil_mercantil_y_financiero__ley_de_proteccion_y_defensa_al_usuario_de_servicios_financieros` | `federal__ley_de_proteccion_y_defensa_al_usuario_de_servicios_financieros` |
| `leyes_federales_civil_mercantil_y_financiero__ley_del_mercado_de_valores` | `federal__ley_del_mercado_de_valores` |
| `leyes_federales_civil_mercantil_y_financiero__ley_general_de_organizaciones_y_actividades_auxiliares_del_credito` | `federal__ley_general_de_organizaciones_y_actividades_auxiliares_del_credito` |
| `leyes_federales_civil_mercantil_y_financiero__ley_general_de_sociedades_cooperativas` | `federal__ley_general_de_sociedades_cooperativas` |
| `leyes_federales_civil_mercantil_y_financiero__ley_general_de_sociedades_mercantiles` | `federal__ley_general_de_sociedades_mercantiles` |
| `leyes_federales_civil_mercantil_y_financiero__ley_general_de_titulos_y_operaciones_de_credito` | `federal__ley_general_de_titulos_y_operaciones_de_credito` |
| `leyes_federales_civil_mercantil_y_financiero__ley_para_la_transparencia_y_ordenamiento_de_los_servicios_financieros` | `federal__ley_para_la_transparencia_y_ordenamiento_de_los_servicios_financieros` |
| `leyes_federales_civil_mercantil_y_financiero__ley_para_regular_las_instituciones_de_tecnologia_financiera` | `federal__ley_para_regular_las_instituciones_de_tecnologia_financiera` |
| `leyes_federales_civil_mercantil_y_financiero__ley_para_regular_las_sociedades_de_informacion_crediticia` | `federal__ley_para_regular_las_sociedades_de_informacion_crediticia` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_de_amparo` | `federal__ley_de_amparo` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_de_la_fiscalia_general_de_la_republica` | `federal__ley_de_la_fiscalia_general_de_la_republica` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_federal_de_las_entidades_paraestatales` | `federal__ley_federal_de_las_entidades_paraestatales` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_federal_de_procedimiento_administrativo` | `federal__ley_federal_de_procedimiento_administrativo` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_federal_de_procedimiento_contencioso_administrativo` | `federal__ley_federal_de_procedimiento_contencioso_administrativo` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_federal_de_responsabilidades_de_los_servidores_publicos` | `federal__ley_federal_de_responsabilidades_de_los_servidores_publicos` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_general_de_responsabilidades_administrativas` | `federal__ley_general_de_responsabilidades_administrativas` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_general_del_sistema_nacional_anticorrupcion` | `federal__ley_general_del_sistema_nacional_anticorrupcion` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_organica_de_la_administracion_publica_federal` | `federal__ley_organica_de_la_administracion_publica_federal` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_organica_de_la_fiscalia_general_de_la_republica` | `federal__ley_organica_de_la_fiscalia_general_de_la_republica` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_organica_del_poder_judicial_de_la_federacion` | `federal__ley_organica_del_poder_judicial_de_la_federacion` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_organica_del_tribunal_federal_de_justicia_administrativa` | `federal__ley_organica_del_tribunal_federal_de_justicia_administrativa` |
| `leyes_federales_constitucional_amparo_y_administrativo__ley_reglamentaria_de_las_fracciones_i_y_ii_del_articulo_105_constitucional` | `federal__ley_reglamentaria_de_las_fracciones_i_y_ii_del_articulo_105_de_la_constitucion_politica_de_los_estados_unidos_mexicanos` |
| `leyes_federales_datos_personales_y_transparencia__ley_federal_de_proteccion_de_datos_personales_en_posesion_de_los_particulares` | `federal__ley_federal_de_proteccion_de_datos_personales_en_posesion_de_los_particulares` |
| `leyes_federales_datos_personales_y_transparencia__ley_federal_de_transparencia_y_acceso_a_la_informacion_publica` | `federal__ley_federal_de_transparencia_y_acceso_a_la_informacion_publica` |
| `leyes_federales_datos_personales_y_transparencia__ley_general_de_archivos` | `federal__ley_general_de_archivos` |
| `leyes_federales_datos_personales_y_transparencia__ley_general_de_proteccion_de_datos_personales_en_posesion_de_sujetos_obligados` | `federal__ley_general_de_proteccion_de_datos_personales_en_posesion_de_sujetos_obligados` |
| `leyes_federales_datos_personales_y_transparencia__ley_general_de_transparencia_y_acceso_a_la_informacion_publica` | `federal__ley_general_de_transparencia_y_acceso_a_la_informacion_publica` |
| `leyes_federales_electoral__ley_general_de_instituciones_y_procedimientos_electorales` | `federal__ley_general_de_instituciones_y_procedimientos_electorales` |
| `leyes_federales_electoral__ley_general_de_partidos_politicos` | `federal__ley_general_de_partidos_politicos` |
| `leyes_federales_electoral__ley_general_del_sistema_de_medios_de_impugnacion_en_materia_electoral` | `federal__ley_general_del_sistema_de_medios_de_impugnacion_en_materia_electoral` |
| `leyes_federales_energia__ley_de_biocombustibles` | `federal__ley_de_biocombustibles` |
| `leyes_federales_energia__ley_de_energia_geotermica` | `federal__ley_de_energia_geotermica` |
| `leyes_federales_energia__ley_de_geotermia` | `federal__ley_de_geotermia` |
| `leyes_federales_energia__ley_de_hidrocarburos` | `federal__ley_de_hidrocarburos` |
| `leyes_federales_energia__ley_de_la_comision_nacional_de_energia` | `federal__ley_de_la_comision_nacional_de_energia` |
| `leyes_federales_energia__ley_de_la_empresa_publica_del_estado_comision_federal_de_electricidad` | `federal__ley_de_la_empresa_publica_del_estado_comision_federal_de_electricidad` |
| `leyes_federales_energia__ley_de_la_empresa_publica_del_estado_petroleos_mexicanos` | `federal__ley_de_la_empresa_publica_del_estado_petroleos_mexicanos` |
| `leyes_federales_energia__ley_de_la_industria_electrica` | `federal__ley_de_la_industria_electrica` |
| `leyes_federales_energia__ley_de_los_organos_reguladores_coordinados_en_materia_energetica` | `federal__ley_de_los_organos_reguladores_coordinados_en_materia_energetica` |
| `leyes_federales_energia__ley_de_planeacion_y_transicion_energetica` | `federal__ley_de_planeacion_y_transicion_energetica` |
| `leyes_federales_energia__ley_de_transicion_energetica` | `federal__ley_de_transicion_energetica` |
| `leyes_federales_energia__ley_del_sector_electrico` | `federal__ley_del_sector_electrico` |
| `leyes_federales_energia__ley_del_sector_hidrocarburos` | `federal__ley_del_sector_hidrocarburos` |
| `leyes_federales_energia__ley_para_el_aprovechamiento_sustentable_de_la_energia` | `federal__ley_para_el_aprovechamiento_sustentable_de_la_energia` |
| `leyes_federales_energia__ley_reglamentaria_del_articulo_27_constitucional_en_el_ramo_del_petroleo` | `federal__ley_reglamentaria_del_articulo_27_constitucional_en_el_ramo_del_petroleo` |
| `leyes_federales_familia_salud_y_educacion__ley_de_los_derechos_de_las_personas_adultas_mayores` | `federal__ley_de_los_derechos_de_las_personas_adultas_mayores` |
| `leyes_federales_familia_salud_y_educacion__ley_federal_para_prevenir_y_eliminar_la_discriminacion` | `federal__ley_federal_para_prevenir_y_eliminar_la_discriminacion` |
| `leyes_federales_familia_salud_y_educacion__ley_general_de_cultura_fisica_y_deporte` | `federal__ley_general_de_cultura_fisica_y_deporte` |
| `leyes_federales_familia_salud_y_educacion__ley_general_de_cultura_y_derechos_culturales` | `federal__ley_general_de_cultura_y_derechos_culturales` |
| `leyes_federales_familia_salud_y_educacion__ley_general_de_educacion` | `federal__ley_general_de_educacion` |
| `leyes_federales_familia_salud_y_educacion__ley_general_de_educacion_superior` | `federal__ley_general_de_educacion_superior` |
| `leyes_federales_familia_salud_y_educacion__ley_general_de_los_derechos_de_ninas_ninos_y_adolescentes` | `federal__ley_general_de_los_derechos_de_ninas_ninos_y_adolescentes` |
| `leyes_federales_familia_salud_y_educacion__ley_general_de_salud` | `federal__ley_general_de_salud` |
| `leyes_federales_familia_salud_y_educacion__ley_general_del_deporte` | `federal__ley_general_del_deporte` |
| `leyes_federales_familia_salud_y_educacion__ley_general_del_servicio_profesional_docente` | `federal__ley_general_del_servicio_profesional_docente` |
| `leyes_federales_familia_salud_y_educacion__ley_general_del_sistema_para_la_carrera_de_las_maestras_y_los_maestros` | `federal__ley_general_del_sistema_para_la_carrera_de_las_maestras_y_los_maestros` |
| `leyes_federales_familia_salud_y_educacion__ley_general_para_la_igualdad_entre_mujeres_y_hombres` | `federal__ley_general_para_la_igualdad_sustantiva_entre_mujeres_y_hombres` |
| `leyes_federales_familia_salud_y_educacion__ley_general_para_la_inclusion_de_las_personas_con_discapacidad` | `federal__ley_general_para_la_inclusion_de_las_personas_con_discapacidad` |
| `leyes_federales_fiscal__ley_aduanera` | `federal__ley_aduanera` |
| `leyes_federales_fiscal__ley_de_comercio_exterior` | `federal__ley_de_comercio_exterior` |
| `leyes_federales_fiscal__ley_de_coordinacion_fiscal` | `federal__ley_de_coordinacion_fiscal` |
| `leyes_federales_fiscal__ley_de_ingresos_de_la_federacion` | `federal__ley_de_ingresos_de_la_federacion` |
| `leyes_federales_fiscal__ley_del_impuesto_al_valor_agregado` | `federal__ley_del_impuesto_al_valor_agregado` |
| `leyes_federales_fiscal__ley_del_impuesto_especial_sobre_produccion_y_servicios` | `federal__ley_del_impuesto_especial_sobre_produccion_y_servicios` |
| `leyes_federales_fiscal__ley_del_impuesto_sobre_la_renta` | `federal__ley_del_impuesto_sobre_la_renta` |
| `leyes_federales_fiscal__ley_federal_de_derechos` | `federal__ley_federal_de_derechos` |
| `leyes_federales_fiscal__ley_federal_de_presupuesto_y_responsabilidad_hacendaria` | `federal__ley_federal_de_presupuesto_y_responsabilidad_hacendaria` |
| `leyes_federales_fiscal__ley_federal_para_la_prevencion_e_identificacion_de_operaciones_con_recursos_de_procedencia_ilicita` | `federal__ley_federal_para_la_prevencion_e_identificacion_de_operaciones_con_recursos_de_procedencia_ilicita` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal` | `federal__resolucion_miscelanea_fiscal` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_1` | `federal__resolucion_miscelanea_fiscal_2026_anexo_1` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_15` | `federal__resolucion_miscelanea_fiscal_2026_anexo_15` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_18` | `federal__resolucion_miscelanea_fiscal_2026_anexo_18` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_2` | `federal__resolucion_miscelanea_fiscal_2026_anexo_2` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_24` | `federal__resolucion_miscelanea_fiscal_2026_anexo_24` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_25` | `federal__resolucion_miscelanea_fiscal_2026_anexo_25` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_3` | `federal__resolucion_miscelanea_fiscal_2026_anexo_3` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_30` | `federal__resolucion_miscelanea_fiscal_2026_anexo_30` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_4` | `federal__resolucion_miscelanea_fiscal_2026_anexo_4` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_5` | `federal__resolucion_miscelanea_fiscal_2026_anexo_5` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_6` | `federal__resolucion_miscelanea_fiscal_2026_anexo_6` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_7` | `federal__resolucion_miscelanea_fiscal_2026_anexo_7` |
| `leyes_federales_fiscal__resolucion_miscelanea_fiscal_2026_anexo_8` | `federal__resolucion_miscelanea_fiscal_2026_anexo_8` |
| `leyes_federales_laboral_y_seguridad_social__ley_de_los_sistemas_de_ahorro_para_el_retiro` | `federal__ley_de_los_sistemas_de_ahorro_para_el_retiro` |
| `leyes_federales_laboral_y_seguridad_social__ley_del_instituto_de_seguridad_y_servicios_sociales_de_los_trabajadores_del_estado` | `federal__ley_del_instituto_de_seguridad_y_servicios_sociales_de_los_trabajadores_del_estado` |
| `leyes_federales_laboral_y_seguridad_social__ley_del_instituto_del_fondo_nacional_de_la_vivienda_para_los_trabajadores` | `federal__ley_del_instituto_del_fondo_nacional_de_la_vivienda_para_los_trabajadores` |
| `leyes_federales_laboral_y_seguridad_social__ley_del_instituto_del_fondo_nacional_para_el_consumo_de_los_trabajadores` | `federal__ley_del_instituto_del_fondo_nacional_para_el_consumo_de_los_trabajadores` |
| `leyes_federales_laboral_y_seguridad_social__ley_del_seguro_social` | `federal__ley_del_seguro_social` |
| `leyes_federales_laboral_y_seguridad_social__ley_federal_de_los_trabajadores_al_servicio_del_estado` | `federal__ley_federal_de_los_trabajadores_al_servicio_del_estado` |
| `leyes_federales_laboral_y_seguridad_social__ley_federal_del_trabajo` | `federal__ley_federal_del_trabajo` |
| `leyes_federales_migracion_y_nacionalidad__ley_de_migracion` | `federal__ley_de_migracion` |
| `leyes_federales_migracion_y_nacionalidad__ley_de_nacionalidad` | `federal__ley_de_nacionalidad` |
| `leyes_federales_migracion_y_nacionalidad__ley_general_de_poblacion` | `federal__ley_general_de_poblacion` |
| `leyes_federales_migracion_y_nacionalidad__ley_sobre_refugiados_proteccion_complementaria_y_asilo_politico` | `federal__ley_sobre_refugiados_proteccion_complementaria_y_asilo_politico` |
| `leyes_federales_penal_y_seguridad__ley_de_la_guardia_nacional` | `federal__ley_de_la_guardia_nacional` |
| `leyes_federales_penal_y_seguridad__ley_de_seguridad_nacional` | `federal__ley_de_seguridad_nacional` |
| `leyes_federales_penal_y_seguridad__ley_federal_contra_la_delincuencia_organizada` | `federal__ley_federal_contra_la_delincuencia_organizada` |
| `leyes_federales_penal_y_seguridad__ley_federal_de_armas_de_fuego_y_explosivos` | `federal__ley_federal_de_armas_de_fuego_y_explosivos` |
| `leyes_federales_penal_y_seguridad__ley_federal_de_extincion_de_dominio` | `federal__ley_federal_de_extincion_de_dominio` |
| `leyes_federales_penal_y_seguridad__ley_federal_para_prevenir_y_sancionar_la_tortura` | `federal__ley_federal_para_prevenir_y_sancionar_la_tortura` |
| `leyes_federales_penal_y_seguridad__ley_general_de_acceso_de_las_mujeres_a_una_vida_libre_de_violencia` | `federal__ley_general_de_acceso_de_las_mujeres_a_una_vida_libre_de_violencias` |
| `leyes_federales_penal_y_seguridad__ley_general_de_victimas` | `federal__ley_general_de_victimas` |
| `leyes_federales_penal_y_seguridad__ley_general_del_sistema_nacional_de_seguridad_publica` | `federal__ley_general_del_sistema_nacional_de_seguridad_publica` |
| `leyes_federales_penal_y_seguridad__ley_general_en_materia_de_delitos_electorales` | `federal__ley_general_en_materia_de_delitos_electorales` |
| `leyes_federales_penal_y_seguridad__ley_general_en_materia_de_desaparicion_forzada_de_personas` | `federal__ley_general_en_materia_de_desaparicion_forzada_de_personas` |
| `leyes_federales_penal_y_seguridad__ley_general_para_prevenir_investigar_y_sancionar_la_tortura` | `federal__ley_general_para_prevenir_investigar_y_sancionar_la_tortura` |
| `leyes_federales_penal_y_seguridad__ley_general_para_prevenir_sancionar_y_erradicar_los_delitos_en_materia_de_trata_de_personas` | `federal__ley_general_para_prevenir_sancionar_y_erradicar_los_delitos_en_materia_de_trata_de_personas` |
| `leyes_federales_penal_y_seguridad__ley_general_para_prevenir_y_sancionar_los_delitos_en_materia_de_secuestro` | `federal__ley_general_para_prevenir_y_sancionar_los_delitos_en_materia_de_secuestro` |
| `leyes_federales_penal_y_seguridad__ley_nacional_de_ejecucion_penal` | `federal__ley_nacional_de_ejecucion_penal` |
| `leyes_federales_penal_y_seguridad__ley_nacional_de_extincion_de_dominio` | `federal__ley_nacional_de_extincion_de_dominio` |
| `leyes_federales_penal_y_seguridad__ley_nacional_de_mecanismos_alternativos_de_solucion_de_controversias_en_materia_penal` | `federal__ley_nacional_de_mecanismos_alternativos_de_solucion_de_controversias_en_materia_penal` |
| `leyes_federales_penal_y_seguridad__ley_nacional_del_registro_de_detenciones` | `federal__ley_nacional_del_registro_de_detenciones` |
| `leyes_federales_penal_y_seguridad__ley_nacional_del_sistema_integral_de_justicia_penal_para_adolescentes` | `federal__ley_nacional_del_sistema_integral_de_justicia_penal_para_adolescentes` |
| `leyes_federales_penal_y_seguridad__ley_nacional_sobre_el_uso_de_la_fuerza` | `federal__ley_nacional_sobre_el_uso_de_la_fuerza` |
| `leyes_federales_propiedad_intelectual_y_consumidor__ley_federal_de_competencia_economica` | `federal__ley_federal_de_competencia_economica` |
| `leyes_federales_propiedad_intelectual_y_consumidor__ley_federal_de_proteccion_a_la_propiedad_industrial` | `federal__ley_federal_de_proteccion_a_la_propiedad_industrial` |
| `leyes_federales_propiedad_intelectual_y_consumidor__ley_federal_de_proteccion_al_consumidor` | `federal__ley_federal_de_proteccion_al_consumidor` |
| `leyes_federales_propiedad_intelectual_y_consumidor__ley_federal_del_derecho_de_autor` | `federal__ley_federal_del_derecho_de_autor` |
| `leyes_federales_telecomunicaciones_y_medios__ley_del_sistema_publico_de_radiodifusion_del_estado_mexicano` | `federal__ley_del_sistema_publico_de_radiodifusion_del_estado_mexicano` |
| `leyes_federales_telecomunicaciones_y_medios__ley_en_materia_de_telecomunicaciones_y_radiodifusion` | `federal__ley_en_materia_de_telecomunicaciones_y_radiodifusion` |
| `leyes_federales_telecomunicaciones_y_medios__ley_federal_de_telecomunicaciones_y_radiodifusion` | `federal__ley_federal_de_telecomunicaciones_y_radiodifusion` |
| `transito_movilidad_y_autotransporte_federal__ley_de_caminos_puentes_y_autotransporte_federal` | `federal__ley_de_caminos_puentes_y_autotransporte_federal` |
| `transito_movilidad_y_autotransporte_federal__ley_general_de_movilidad_y_seguridad_vial` | `federal__ley_general_de_movilidad_y_seguridad_vial` |
| `transito_movilidad_y_autotransporte_federal__reglamento_de_la_ley_de_caminos_puentes_y_autotransporte_federal` | `federal__reglamento_de_autotransporte_federal_y_servicios_auxiliares` |
| `transito_movilidad_y_autotransporte_federal__reglamento_de_transito_en_carreteras_y_puentes_de_jurisdiccion_federal` | `federal__reglamento_de_transito_en_carreteras_y_puentes_de_jurisdiccion_federal` |
| `transito_movilidad_y_autotransporte_federal__reglamento_del_servicio_de_autotransporte_de_carga_federal` | `federal__reglamento_del_servicio_de_autotransporte_de_carga_federal` |
| `transito_movilidad_y_autotransporte_federal__reglamento_del_servicio_de_autotransporte_de_turismo` | `federal__reglamento_del_servicio_de_autotransporte_de_turismo` |
| `transito_movilidad_y_autotransporte_federal__reglamento_del_servicio_de_autotransporte_federal_de_pasajeros` | `federal__reglamento_del_servicio_de_autotransporte_federal_de_pasajeros` |
| `transito_movilidad_y_autotransporte_federal__reglamento_sobre_el_peso_dimensiones_y_capacidad_de_los_vehiculos_de_autotransporte` | `federal__reglamento_sobre_el_peso_dimensiones_y_capacidad_de_los_vehiculos_de_autotransporte` |
| `tratados_internacionales__convencion_americana_sobre_derechos_humanos` | `federal__convencion_americana_sobre_derechos_humanos` |
| `tratados_internacionales__convencion_contra_la_tortura_y_otros_tratos_o_penas_crueles_inhumanos_o_degradantes` | `federal__convencion_contra_la_tortura_y_otros_tratos_o_penas_crueles_inhumanos_o_degradantes` |
| `tratados_internacionales__convencion_de_la_haya_sobre_apostilla` | `federal__convenio_por_el_que_se_suprime_la_exigencia_de_legalizacion_de_los_documentos_publicos_extranjeros_convencion_de_la_apostilla_la_haya_5_de_octubre_de_1961` |
| `tratados_internacionales__convencion_de_la_haya_sobre_los_aspectos_civiles_de_la_sustraccion_internacional_de_menores` | `federal__convencion_de_la_haya_sobre_los_aspectos_civiles_de_la_sustraccion_internacional_de_menores` |
| `tratados_internacionales__convencion_de_las_naciones_unidas_contra_la_corrupcion` | `federal__convencion_de_las_naciones_unidas_contra_la_corrupcion` |
| `tratados_internacionales__convencion_de_las_naciones_unidas_contra_la_delincuencia_organizada_transnacional_palermo` | `federal__convencion_de_las_naciones_unidas_contra_la_delincuencia_organizada_transnacional_palermo` |
| `tratados_internacionales__convencion_de_nueva_york_sobre_reconocimiento_y_ejecucion_de_sentencias_arbitrales_extranjeras` | `federal__convencion_de_nueva_york_sobre_reconocimiento_y_ejecucion_de_sentencias_arbitrales_extranjeras` |
| `tratados_internacionales__convencion_de_viena_sobre_el_derecho_de_los_tratados` | `federal__convencion_de_viena_sobre_el_derecho_de_los_tratados` |
| `tratados_internacionales__convencion_interamericana_para_prevenir_sancionar_y_erradicar_la_violencia_contra_la_mujer_belem_do_para` | `federal__convencion_interamericana_para_prevenir_sancionar_y_erradicar_la_violencia_contra_la_mujer_belem_do_para` |
| `tratados_internacionales__convencion_interamericana_para_prevenir_y_sancionar_la_tortura` | `federal__convencion_interamericana_para_prevenir_y_sancionar_la_tortura` |
| `tratados_internacionales__convencion_interamericana_sobre_restitucion_internacional_de_menores` | `federal__convencion_interamericana_sobre_restitucion_internacional_de_menores` |
| `tratados_internacionales__convencion_sobre_la_eliminacion_de_todas_las_formas_de_discriminacion_contra_la_mujer_cedaw` | `federal__convencion_sobre_la_eliminacion_de_todas_las_formas_de_discriminacion_contra_la_mujer_cedaw` |
| `tratados_internacionales__convencion_sobre_los_derechos_de_las_personas_con_discapacidad` | `federal__convencion_sobre_los_derechos_de_las_personas_con_discapacidad` |
| `tratados_internacionales__convencion_sobre_los_derechos_del_nino` | `federal__convencion_sobre_los_derechos_del_nino` |
| `tratados_internacionales__convenio_169_de_la_oit_sobre_pueblos_indigenas_y_tribales` | `federal__convenio_169_de_la_oit_sobre_pueblos_indigenas_y_tribales` |
| `tratados_internacionales__estatuto_de_roma_de_la_corte_penal_internacional` | `federal__estatuto_de_roma_de_la_corte_penal_internacional` |
| `tratados_internacionales__pacto_internacional_de_derechos_civiles_y_politicos` | `federal__pacto_internacional_de_derechos_civiles_y_politicos` |
| `tratados_internacionales__pacto_internacional_de_derechos_economicos_sociales_y_culturales` | `federal__pacto_internacional_de_derechos_economicos_sociales_y_culturales` |
| `tratados_internacionales__tratado_entre_mexico_estados_unidos_y_canada_t_mec` | `federal__tratado_entre_mexico_estados_unidos_y_canada_t_mec` |
| `tratados_internacionales__tratado_entre_mexico_estados_unidos_y_canada_t_mec_tomo_ii_capitulos_14_a_34_y_acuerdos_paralelos` | `federal__tratado_entre_mexico_estados_unidos_y_canada_t_mec_tomo_ii_capitulos_14_a_34_y_acuerdos_paralelos` |
