"""
convertir.py — PROCESO 3, CONVERTIR: Markdown estructurado (posprocesado y validación) y la herramienta `convertir`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from herramientas.catalogo import huella_origen, markdown_al_dia, problema_de_estructura, ruta_md, ruta_pdf, ruta_relativa_md
from herramientas.comun import (
    ENTORNO,
    ENTRADA,
    TESSDATA,
    Analizador,
    bitacora,
    bloqueo,
    cargar_catalogo,
    cargar_manifiesto,
    correr_hijo,
    guardar_manifiesto,
    hoy,
    leer_json,
    matar_hijos,
    normalizar,
    reemplazar_archivo,
    relativa,
    salida,
    salir_con_error,
    sin_acentos,
    temporal,
)
from herramientas.descargar import MIN_CARACTERES_MD, formato_archivo

# ====================================================================================================
# Markdown: posprocesado del Markdown crudo (MarkItDown, OCR o lector de Word) a Markdown ESTRUCTURADO.
#
# Funciones puras: solo texto; sin entrada/salida ni dependencias de terceros (solo `herramientas/comun.py`).
# `biblioteca.py convertir` las usa para escribir el Markdown de la biblioteca; `estructurar` es idempotente, así que
# el mismo archivo puede reprocesarse sin que cambie. Regla número uno: ante la duda, NO quitar texto.
#
# Formato del Markdown estructurado (VERSION 2)
# ---------------------------------------------
# 1. Front-matter YAML (`front_matter`): id, nombre, entidad, grupo, estado, archivo, fuente, fecha_reforma,
#    sha256_origen, metodo, convertido, version y aviso. Lo lee `separar_front_matter` (solo reconoce como
#    front-matter propio un bloque `---` con `id` o `version`; cualquier otro bloque inicial es cuerpo).
# 2. Limpieza (`limpiar`):
#    - Números de página: las formas inequívocas ("12 de 345", "Página 12", "- 12 -", "## Página 12" del OCR) solo
#      si son el folio del documento (forma repetida 3+ veces), la marca del OCR o están junto a un salto de página
#      ("Página: 531" de una tesis es contenido). Un número suelto solo si está pegado a un corte de página, tiene el
#      estilo de folio del documento (todos con punto o todos sin él), forma secuencia en el sentido correcto con el
#      folio de una página vecina y continúa el folio inequívoco si lo hay: así se conservan "...del artículo" +
#      "160.", las etiquetas "23." ... "45." de una lista, los años y los números de partida de una tarifa.
#    - Cabeceras y pies que se repiten en cada página: líneas cortas repetidas 4+ veces y pegadas a los cortes
#      de página (`\x0c`, "## Página N", folios o, en los PDF sin `\x0c`, la línea en blanco que separa las
#      páginas), en su forma exacta (con mayúsculas y signos: "Guerrero;" no es la cabecera "GUERRERO."). Se quita
#      la repetición, no el dato: la copia de la primera página se conserva ("Publicación 2006/09/06"). No se
#      tocan las notas de reforma ("Párrafo reformado DOF ...", también numeradas al pie: "2 Fracción reformada
#      el ..."), ni los nombres de capítulo que se repiten, ni una línea suelta que termina en enlace o coma y cierra
#      la página (frase cortada). El umbral cuenta páginas distintas, no líneas. De un pie de varias líneas o con
#      frases completas se conserva la primera copia entera, y una copia que difiere (OCR, otro corte) se conserva
#      entera en vez de mutilarla; ninguna copia conservada puede partir una frase, y un bloque idéntico al que la
#      extracción solo intercaló cuerpo se conserva una sola vez.
#    - Dobles espacios y caracteres de control (se cambian por espacio: nunca pegan palabras). Guiones de fin
#      de línea unidos sin espacio (el guion se quita solo cuando la palabra unida ya existe en el documento:
#      "desa-rrollo" -> "desarrollo"; "político-administrativos" se conserva).
# 3. Párrafos (`unir_lineas`): las líneas cortadas a media frase por el PDF se unen; cada párrafo queda en
#    una sola línea y los bloques se separan con una línea en blanco. Tablas, listas, encabezados y bloques
#    de código no se tocan.
# 4. Encabezados (`encabezados`), jerarquía:
#      - Unidades: LIBRO > TÍTULO > CAPÍTULO > SECCIÓN/APARTADO. La unidad mayor presente en el documento
#        recibe `#`, la siguiente `##` y la siguiente `###`; si hay cuatro clases (Libro, Título, Capítulo y
#        Sección), las dos menores comparten `###` para que los artículos queden siempre en el nivel 4.
#        El rótulo se conserva tal cual y, si el nombre de la unidad va en la línea siguiente, se incorpora:
#        `## TÍTULO PRIMERO. DISPOSICIONES GENERALES`. Una serie "APARTADO A, B, ..." que empieza después de un
#        artículo es subdivisión de ese artículo (como el 123 constitucional) y queda como párrafo.
#      - TRANSITORIOS (y "ARTÍCULOS TRANSITORIOS", "T R A N S I T O R I O S"): siempre `##`.
#      - Artículos: siempre `#### Artículo N` (N tal como viene: 12, 12 Bis, 12-A, 1.2, 1o., Primero, Único);
#        el texto que seguía en la misma línea pasa al párrafo siguiente. Solo se convierten si el documento
#        tiene 3 o más artículos numerados en orden creciente (en su mayoría); los ordinales ("Artículo
#        Primero") solo después de TRANSITORIOS o cuando el documento no tiene artículos numerados.
#        Nunca se convierten las menciones dentro de un párrafo ("...conforme al artículo 5 de..."), los
#        apéndices de historial ("ARTÍCULO 492.- fue reformado por Decreto...") ni las líneas de un índice
#        ("Artículo 5 ....... 12", tiradas de rótulos sin texto, o índices con título "ARTÍCULO 1. Objeto"
#        confirmados porque después la numeración reinicia).
# 5. Validación (`validar`, y `estructurar` la incluye en `informe["validacion"]`): las palabras del resultado
#    (con acentos) son las del crudo menos las de las líneas quitadas, ajustadas por los guiones unidos; los
#    números con signos ("5/DEC-12", "171-1") y el orden (bigramas) se conservan; y una revisión independiente
#    de lo quitado marca como sospechosa toda cabecera que termina en ";", que es nota de reforma, un número
#    suelto pegado a otro número quitado, o una variante rara que continuaba la frase anterior.
# ====================================================================================================
VERSION = 2
AVISO = ("Conversión automática del documento oficial; ante cualquier diferencia prevalece el PDF publicado "
         "en la fuente oficial")
CLAVES_FRONT_MATTER = ("id", "nombre", "entidad", "grupo", "estado", "archivo", "fuente", "fecha_reforma",
                       "sha256_origen", "metodo", "convertido", "version", "aviso")

# ---------------------------------------------------------------------------------------------------
# Front-matter YAML (escritor y lector mínimos, sin PyYAML)
# ---------------------------------------------------------------------------------------------------
_RESERVADAS_YAML = frozenset({"null", "~", "true", "false", "yes", "no", "on", "off", "y", "n"})
_RX_NUMERO_YAML = re.compile(r"[-+.]?\d[\d_.,:eE+\-]*")


def _necesita_comillas(texto: str) -> bool:
    if texto == "" or texto != texto.strip():
        return True
    if texto.lower() in _RESERVADAS_YAML or _RX_NUMERO_YAML.fullmatch(texto) or texto[0].isdigit():
        return True
    if texto[0] in "-?:,[]{}#&*!|>'\"%@`" or texto.endswith(":"):
        return True
    return ": " in texto or " #" in texto or "\n" in texto or "\t" in texto


def _yaml_valor(valor) -> str:
    if valor is None:
        return "null"
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, int):
        return str(valor)
    texto = str(valor)
    if not _necesita_comillas(texto):
        return texto
    texto = texto.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{texto}"'


def _yaml_leer(valor: str):
    valor = valor.strip()
    if valor in ("null", "~", ""):
        return None
    if len(valor) >= 2 and valor[0] == '"' and valor[-1] == '"':
        cuerpo = valor[1:-1]
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), cuerpo)
    if len(valor) >= 2 and valor[0] == "'" and valor[-1] == "'":
        return valor[1:-1].replace("''", "'")
    if re.fullmatch(r"-?\d+", valor):
        return int(valor)
    if valor == "true":
        return True
    if valor == "false":
        return False
    return valor


def front_matter(meta: dict) -> str:
    """Bloque YAML entre `---` con las claves de CLAVES_FRONT_MATTER (las que falten van en null)."""
    datos = dict(meta)
    datos.setdefault("version", VERSION)
    datos.setdefault("aviso", AVISO)
    lineas = ["---"]
    for clave in CLAVES_FRONT_MATTER:
        lineas.append(f"{clave}: {_yaml_valor(datos.get(clave))}")
    lineas.append("---")
    return "\n".join(lineas) + "\n"


_RX_FRONT_MATTER = re.compile(r"---[ \t]*\r?\n(.*?)(?:^|\r?\n)---[ \t]*(?:\r?\n|\Z)", re.S)
_RX_CLAVE_YAML = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*:(?:\s|$)")


def separar_front_matter(texto: str) -> tuple[dict | None, str]:
    """(front-matter como dict, cuerpo). Solo es front-matter un bloque `---` inicial de líneas `clave: valor` que
    trae `id` o `version` (el nuestro); cualquier otro bloque ("---\\nNota: ...\\n---") es cuerpo: (None, texto)."""
    if not texto.startswith("---"):
        return None, texto
    m = _RX_FRONT_MATTER.match(texto)
    if not m:
        return None, texto
    meta: dict = {}
    for linea in m.group(1).split("\n"):
        linea = linea.rstrip("\r")
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        if not _RX_CLAVE_YAML.match(linea):
            return None, texto
        clave, _, valor = linea.partition(":")
        meta[clave.strip()] = _yaml_leer(valor)
    if "id" not in meta and "version" not in meta:
        return None, texto
    return meta, texto[m.end():]


# ---------------------------------------------------------------------------------------------------
# Clasificación de líneas (compartida por limpiar, unir_lineas y encabezados)
# ---------------------------------------------------------------------------------------------------
_L = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
_ORD = (r"(?:(?:D[ÉE]CIM[OA]|VIG[ÉE]SIM[OA]|TRIG[ÉE]SIM[OA]|CUADRAG[ÉE]SIM[OA]|QUINCUAG[ÉE]SIM[OA]|SEXAG[ÉE]SIM[OA]"
        r"|SEPTUAG[ÉE]SIM[OA]|OCTOG[ÉE]SIM[OA]|NONAG[ÉE]SIM[OA])\s*)?"
        r"(?:PRIMER[OA]|SEGUND[OA]|TERCER[OA]|CUART[OA]|QUINT[OA]|SEXT[OA]|S[ÉE]PTIM[OA]|OCTAV[OA]|NOVEN[OA]"
        r"|D[ÉE]CIM[OA]|UND[ÉE]CIM[OA]|DUOD[ÉE]CIM[OA]|VIG[ÉE]SIM[OA]|TRIG[ÉE]SIM[OA]|CUADRAG[ÉE]SIM[OA]"
        r"|QUINCUAG[ÉE]SIM[OA]|SEXAG[ÉE]SIM[OA]|SEPTUAG[ÉE]SIM[OA]|OCTOG[ÉE]SIM[OA]|NONAG[ÉE]SIM[OA]|CENT[ÉE]SIM[OA]"
        r"|[ÚU]NIC[OA])")
_ORD_I = f"(?i:{_ORD}(?![{_L}]))"
_LATINO = (r"(?i:BIS|TER|QU[ÁA]TER|QUARTER|QUINQUIES|QUINTUS|SEXIES|SEXTIES|SEXTUS|SEPTIES|SEPTIMUS|OCTIES|OCTAVUS|NONIES"
           r"|NONUS|DECIES|DECIMUS|UNDECIES|DUODECIES|TERDECIES|[A-Z]{3,9}DECIES|[A-Z]{0,4}VICIES)")
def _patron_num_articulo(e: str, decimal: str = rf"\d{{1,4}}(?!\d)|[0-9A-Z]{{1,3}}(?![{_L}\d])") -> str:
    """Número de artículo con sus sufijos (grupo `num`); `e` es la clase de espacio permitida entre sus partes: dentro de
    una línea `[ \t]`, y para buscar menciones cortadas en un párrafo también un salto de línea simple."""
    sufijos = (rf"(?:{e}*[-–]?{e}*{_LATINO}(?![{_L}])(?:{e}*-?{e}*\d{{1,2}}(?!\d))?"   # 12 Bis, 12 Bis 1, 5 BIS-1
               rf"|[-–][A-ZÑ]{{1,3}}(?=[\s.:;,\-–]|$)"                                 # 12-A, 15-III, 158-Ñ
               rf"|[-–]{e}+[A-ZÑ]{{1,3}}(?=[.:;,\-–]|$)"                                # 1691- A.- (no '5o.- A ninguna')
               rf"|-\d{{1,3}}(?!\d)"                                                    # 9-1, 31-A-1, 195-Z-11
               rf"|\.[-–][A-ZÑ]{{1,3}}(?=[.:;,\-–]|$)"                                  # 4o.-A.- (no '5o.- A ninguna')
               rf"|\.\d{{1,3}}(?!\d)"                                                  # 176-Bis.1
               rf"|{e}*(?<![{_L}])(?:[a-z]\)?(?=[.:\-–])|[b-df-np-tvwxz](?=$))"       # 228 b.-, 88b.-, 69 a)., 272 b
               rf"|(?<![º°.]){e}+(?![YOEU](?:\s|$))[A-ZÑ](?=[\s.:;\-–]|$)"            # 12 A (no 'Y 16')
               rf"|(?<=[º°.]){e}+(?![YOEU](?:\s|$))[A-ZÑ](?=[.:;\-–]|$))*")          # 4o. A.- (no '5o. A ninguna')
    return (rf"(?P<num>\d{{1,4}}(?:,\d{{3}})*(?:\.(?:{decimal}))*(?:-\d{{1,3}}(?!\d))?"   # 1,449 · 1.2 · 2.A.1 · 9-1
            rf"(?:[A-Z]{{1,2}}(?![{_L}])|{e}*(?:[oaº°]\.?|ro\.?|er\.?|do\.?)(?![{_L})]))?{sufijos}"
            rf"|{_ORD_I}(?:{e}+{_LATINO})?)")


_NUM_ART = _patron_num_articulo("[ \t]")
_RX_ARTICULO = re.compile(
    rf"^(?P<kw>Art[íi]culo|ART[ÍI]CULO|Articulo|ARTICULO|Art\.?|ART\.?)[ \t]*{_NUM_ART}"
    rf"(?P<sep>\s*(?:[.:\-–—]+\s*)*)(?P<resto>.*)$")
_DECIMAL_FICHA = rf"[0-9A-Z]{{1,3}}(?![{_L}])"     # en una ficha suelta el decimal sigue siendo corto: '0.0032' se valida
# una ficha con la forma de un número de artículo ('17-A', '7.35'); sin f-string anidada (el módulo importa en Python 3.9)
_RX_NUM_ARTICULO_SUELTO = re.compile("^(?:" + _patron_num_articulo("[ \t]", _DECIMAL_FICHA) + ")$")
_RX_UNIDAD = re.compile(
    rf"^(?P<kw>LIBRO|Libro|T[ÍI]TULO|T[ií]tulo|CAP[ÍI]TULO|Cap[ií]tulo|SECCI[ÓO]N|Secci[óo]n|APARTADO|Apartado)\s+"
    rf"(?P<num>{_ORD_I}|[IVXLC]{{1,8}}(?![{_L}])|\d{{1,3}}(?:\s*(?:[oaº°]|ro|ra|er|do|da|to|ta|vo|va|mo|ma|no|na)\.?)?(?!\d)"
    rf"|[A-Z](?![{_L}]))"
    rf"(?P<suf>(?:\s*[-–]?\s*{_LATINO}(?![{_L}]))?(?:\s*[-–]\s*[A-Z](?![{_L}]))?)"
    rf"(?P<sep>\s*[.:\-–—]*)\s*(?P<resto>.*)$")
_RX_TRANSITORIOS = re.compile(
    r"^(?:(?:ART[ÍI]CULOS?|Art[íi]culos?|DISPOSICIONES|Disposiciones)\s+)?T\s?R\s?A\s?N\s?S\s?I\s?T\s?O\s?R\s?I\s?[OAoa]\s?[Ss]?"
    r"(?:\s*[.:])?(?P<cola>\s+(?:DEL?|DE L[OA]S|A LA|AL|POR)\s+.{3,100})?\s*$", re.IGNORECASE)
_RX_MD_ENCABEZADO = re.compile(r"^#{1,6}\s+\S")
_RX_LISTA = re.compile(r"^(?:[-*+]|\d{1,3}[.)]-?|[a-zA-Z][.)]-?|[IVXLCivxlc]{1,8}[.)]-?|\d{1,2}\.\d{1,2}(?:\.\d{1,3})*\.?)\s+\S")
_RX_LISTA_MINUSCULA = re.compile(            # como _RX_LISTA, pero sin letras mayúsculas ('H. CONGRESO' no es lista)
    r"^(?:[-*+]|\d{1,3}[.)]-?|[a-z][.)]-?|[IVXLCivxlc]{1,8}[.)]-?|\d{1,2}\.\d{1,2}(?:\.\d{1,3})*\.?)\s+\S")
_RX_ETIQUETA = re.compile(r"^(?:(?=[IVXLC])C{0,3}(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})|[a-zA-Z]|\d{1,3})(?:[.)]-?)?$")
_RX_REGLA_HORIZONTAL = re.compile(r"^(?:-{3,}|={3,}|\*{3,}|_{3,})$")
_VERBOS_REFORMA = r"reformad[oa]s?|adicionad[oa]s?|derogad[oa]s?|recorrid[oa]s?|reubicad[oa]s?|republicad[oa]s?|modificad[oa]s?"
_RX_ANOTACION = re.compile(
    r"^[\(\[]?\s*(?:"
    rf"(?i:{_VERBOS_REFORMA}|REFORMA\b|FE DE ERRATAS|F\.\s*DE\s*E\.?|N\.\s*DE\s*E\.?)"
    r"|(?:P[áa]rrafos?|Fracci[óo]n(?:es)?|Incisos?|Art[íi]culos?|Apartados?|Cap[íi]tulos?|T[íi]tulos?|Secci[óo]n(?:es)?|Libros?"
    r"|Denominaci[óo]n|Numeral(?:es)?|Rubros?|Tablas?|Ep[íi]grafes?|Transitorios?|Anexos?)\b[^.;:]{0,40}?"
    rf"\b(?i:{_VERBOS_REFORMA}|fe de erratas)\b"
    r")")
_RX_NOTA_AL_PIE = re.compile(r"^\d{1,4}\s+")                        # '2 Fracción reformada el 22/ago/2015.'
_RX_HISTORIAL = re.compile(rf"(?i)^(?:fue|fueron|ha sido|han sido)\s+(?:{_VERBOS_REFORMA}|suspendid[oa]s?)\b")
_RX_INDICE_RESTO = re.compile(r"(?:\.\s*){3,}\d*\s*$|^…+\s*\d*$|^\d{1,4}$")

_TERMINADORES = frozenset(".:;)]\"»—”…?!'’")
_SIGNOS_CONTINUAN = frozenset(",;.%°º-–—")
_PALABRAS_ENLACE = frozenset({
    "de", "del", "la", "las", "los", "el", "y", "e", "o", "u", "a", "al", "en", "con", "por", "para", "que",
    "su", "sus", "se", "lo", "un", "una", "unos", "unas", "no", "ni", "sin", "sobre", "ante", "bajo", "contra",
    "desde", "durante", "entre", "hacia", "hasta", "mediante", "según", "segun", "tras", "cuyo", "cuya", "cuyos",
    "cuyas", "como", "más", "mas", "pero", "si", "cuando", "donde", "les", "le", "nos", "me", "te", "esta", "este",
    "esa", "ese", "aquel", "aquella", "cada", "toda", "todo", "todos", "todas", "cual", "cuales", "quien", "quienes",
    "mismo", "misma", "dicho", "dicha", "dichos", "dichas",
})
_PALABRAS_VACIAS_NOMBRE = frozenset({"de", "del", "la", "las", "los", "el", "y", "e", "o", "u", "a", "al", "en",
                                     "con", "por", "para", "su", "sus", "que", "no", "ni", "sin", "sobre"})

_RANGO_UNIDAD = {"LIBRO": 1, "TITULO": 2, "CAPITULO": 3, "SECCION": 4, "APARTADO": 4}


def _clase_unidad(kw: str) -> str:
    return kw.upper().replace("Í", "I").replace("Ó", "O")


def _es_anotacion(linea: str) -> bool:
    """Nota de reforma: '(REFORMADO, P.O. 23 DE MARZO DE 1997)', 'Párrafo reformado DOF 10-06-2011', 'N. DE E. ...'.
    Se reconoce por cómo empieza (no por su longitud) para que la clasificación no cambie al reprocesar."""
    return bool(_RX_ANOTACION.match(linea))


def _articulo(linea: str) -> dict | None:
    """Si la línea empieza con un rótulo de artículo devuelve {num, etiqueta, ordinal, n, resto, sep, indice}; si no,
    None. Las menciones ('Artículo 5 de la Ley...', 'Artículo 5, fracción II') no cuentan."""
    m = _RX_ARTICULO.match(linea)
    if not m:
        return None
    num = re.sub(r"\s+", " ", m.group("num").strip())
    sep, resto = m.group("sep"), m.group("resto").strip()
    con_sep = bool(sep.strip())
    if resto and not con_sep and not (resto[0].isupper() or resto[0].isdigit() or resto[0] in "(\"“«¿¡["):
        return None                                      # mención: 'Artículo 5 de la Ley...', 'Artículo 5, fracción II'
    if resto and not con_sep and re.match(r"[YOEUyoeu]\s", resto):
        return None                                      # 'ARTÍCULO 15 BIS Y SE ADICIONAN...': referencia de un decreto
    if resto and not con_sep and resto[0] == "(" and not (len(resto) > 1 and (resto[1].isupper() or resto[1].isdigit())):
        return None
    ordinal = not num[0].isdigit()
    if ordinal and resto and not con_sep:
        return None                                      # 'ARTÍCULO SEGUNDO TRANSITORIO DEL DECRETO...': referencia
    m_n = re.match(r"\d+", num)
    n = int(m_n.group()) if m_n else None
    etiqueta = re.sub(r"\s*[-–]\s*", "-", num)
    etiqueta = re.sub(rf"(?<![{_L}]){_LATINO}(?![{_L}])", lambda m: m.group().capitalize(), etiqueta)
    etiqueta = " ".join(p.capitalize() if re.fullmatch(_ORD_I, p) else p for p in etiqueta.split(" "))
    return {"num": num, "etiqueta": "Artículo " + etiqueta, "ordinal": ordinal, "n": n, "resto": resto, "sep": con_sep,
            "indice": bool(resto) and (bool(_RX_INDICE_RESTO.search(resto)) or (not con_sep and resto[0].isdigit()))}


def _serie_apartado(num: str) -> str:
    """'A', 'I', '1', '1o.' y 'PRIMERO/A' empiezan una serie de apartados ('inicio'); 'ÚNICO/A' va solo ('unico')."""
    n = num.upper().replace("Ú", "U").rstrip(".")
    if n.startswith("UNIC"):
        return "unico"
    if n in ("A", "I", "1") or n.startswith("PRIMER") or re.fullmatch(r"1\s*(?:[OAº°]|RO|RA|ER)", n):
        return "inicio"
    return ""


_RX_RESTO_MENCION = re.compile(
    r"(?i:(?:DEL?|AL|DE\s+(?:ESTE|ESTA|LA|LAS|LOS))\s+(?:T[ÍI]TULO|CAP[ÍI]TULO|LIBRO|SECCI[ÓO]N|APARTADO|C[ÓO]DIGO|LEY"
    r"|REGLAMENTO|ART[ÍI]CULO|PRESENTE|DECRETO))\b")


def _unidad(linea: str) -> dict | None:
    """Si la línea es un rótulo de unidad (LIBRO/TÍTULO/CAPÍTULO/SECCIÓN/APARTADO) devuelve {clase, resto, indice, serie}."""
    if len(linea) > 160:
        return None
    m = _RX_UNIDAD.match(linea)
    if not m:
        return None
    kw, num, resto = m.group("kw"), m.group("num"), m.group("resto").strip()
    clase = _clase_unidad(kw)
    if clase == "APARTADO" and not kw.isupper():
        return None
    if re.fullmatch(r"[A-Z]", num) and num not in "IVXLC" and clase not in ("SECCION", "APARTADO"):
        return None                                      # 'TÍTULO A ...' no es numeración por letra
    indice = bool(resto) and (bool(_RX_INDICE_RESTO.search(resto)) or bool(re.search(r"\s\d{1,4}$", resto)))
    if resto and not indice and (len(resto) > 130 or not (resto[0].isupper() or resto[0] in "(\"“«¿")):
        return None
    mencion = bool(resto) and not m.group("sep").strip() and bool(_RX_RESTO_MENCION.match(resto))
    return {"clase": clase, "resto": resto, "indice": indice, "serie": _serie_apartado(num), "mencion": mencion}


def _transitorios(linea: str) -> bool:
    if len(linea) > 130 or not linea[:1].isupper():
        return False
    m = _RX_TRANSITORIOS.match(linea)
    if not m:
        return False
    cola = m.group("cola")
    if cola:
        letras = [c for c in cola if c.isalpha()]
        return bool(letras) and sum(c.isupper() for c in letras) / len(letras) >= 0.7
    return True


def _es_tabla(linea: str) -> bool:
    return linea.startswith("|")


def _es_lista(linea: str) -> bool:
    return bool(_RX_LISTA.match(linea))


def _es_fija(linea: str) -> bool:
    """Líneas que ni se unen con la anterior ni acumulan la siguiente (unir_lineas)."""
    if _es_tabla(linea) or _RX_MD_ENCABEZADO.match(linea) or linea.startswith("```") or _RX_REGLA_HORIZONTAL.match(linea):
        return True
    if _RX_ETIQUETA.match(linea) or _es_anotacion(linea) or _transitorios(linea) or _unidad(linea):
        return True
    art = _articulo(linea)
    return art is not None and (not art["resto"] or art["indice"])


def _es_titulo(resto: str) -> bool:
    """Resto de un rótulo que es un título y no texto: 'Objeto', 'Supletoriedad' (corto, sin puntuación final y sin
    nota de reforma: 'Derogado', 'Se deroga', '(DEROGADO, P.O. ...)' son artículos del cuerpo)."""
    return (0 < len(resto) <= 130 and not resto.endswith((".", ";", ":"))
            and not re.search(r"(?i)\b(?:derogad|se deroga|reformad|adicionad)", resto))


def _es_indice(linea: str) -> bool:
    """Línea de un índice: rótulo con puntos de guía o número de página ('Artículo 5 ....... 12')."""
    d = _articulo(linea) or _unidad(linea)
    return d is not None and d["indice"]


def _inicia_bloque(linea: str) -> bool:
    """Líneas que nunca se pegan a la anterior."""
    return _es_fija(linea) or _es_lista(linea) or bool(_articulo(linea))


# ---------------------------------------------------------------------------------------------------
# limpiar: números de página, cabeceras repetidas, espacios y guiones
# ---------------------------------------------------------------------------------------------------
_RX_PAGINA = re.compile(
    r"^(?:[-–—]\s*)?(?:P\s*[áÁaA]\s*[gG]\s*(?:[iI]\s*[nN]\s*[aA])?\s*\.?|Hoja|HOJA|Page|PAGE)\s*(?:No\.?|N[°º]\.?|:)?\s*"
    r"\d{1,4}(?:\s*(?:de|DE|of|/)\s*\d{1,4})?\s*\.?(?:\s*[-–—])?$")
_RX_N_DE_M = re.compile(r"^\d{1,4}\s+(?:de|DE|of)\s+\d{1,4}$")
_RX_GUIONES = re.compile(r"^[-–—]\s*\d{1,4}\s*[-–—]$")
_RX_MD_PAGINA = re.compile(r"^#{1,6}\s*P[áa]gina\s+\d{1,5}\s*$")
_RX_NUMERO_SOLO = re.compile(r"^\d{1,4}\.?$")
_RX_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
_RX_ESPACIOS = re.compile(r"[ \t\xa0\u2007\u202f]+")
_RX_GUION_FINAL = re.compile(rf"([{_L}]+)-$")
_RX_DIGITOS = re.compile(r"\d+")
_RX_FORMA_CABECERA = re.compile(r"[\d\s]+")
_RX_SALTO_PAGINA_EN_LINEA = re.compile(r"(?<![\n\x0c])\x0c")


def es_numero_pagina(linea: str) -> bool:
    """Formas inequívocas de número de página ('12 de 345', 'Página 12', 'Pág. 12', '- 12 -', '## Página 12').
    Los números sueltos ('12') se evalúan aparte: solo junto a un corte de página y en secuencia."""
    s = linea.strip()
    return bool(_RX_N_DE_M.match(s) or _RX_PAGINA.match(s) or _RX_GUIONES.match(s) or _RX_MD_PAGINA.match(s))


@dataclass
class _Analisis:
    lineas: list[str]
    quitar: dict[int, str] = field(default_factory=dict)     # índice -> motivo
    cabeceras: Counter = field(default_factory=Counter)      # texto -> veces quitado
    paginas: int = 1
    inicio_pagina: set[int] = field(default_factory=set)     # índices de las líneas que empiezan página (\x0c)


def _normalizar_lineas(texto: str) -> tuple[list[str], set[int]]:
    """Líneas sin caracteres de control ni espacios dobles; el conjunto son los índices que empiezan página (\\x0c).
    Un \\x0c a media línea parte la línea (nunca pega 'de' con 'PÁGINA'); los controles se vuelven espacio."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    texto = _RX_SALTO_PAGINA_EN_LINEA.sub("\n\x0c", texto)
    lineas, inicio_pagina, en_fence = [], set(), False
    for i, linea in enumerate(texto.split("\n")):
        if "\x0c" in linea:
            inicio_pagina.add(i)
            linea = linea.replace("\x0c", " ")
        linea = _RX_CONTROL.sub(" ", linea)
        if linea.startswith("```"):
            en_fence = not en_fence
            lineas.append(linea.rstrip())
            continue
        if en_fence:
            lineas.append(linea.rstrip())
            continue
        lineas.append(_RX_ESPACIOS.sub(" ", linea).strip())
    return lineas, inicio_pagina


def _clave_cabecera(linea: str) -> str:
    return normalizar(_RX_DIGITOS.sub("#", linea))


def _forma_cabecera(linea: str) -> str:
    """Forma exacta de una cabecera: sin folio, espacios ni acentos, pero con mayúsculas y signos ('Guerrero;' != 'GUERRERO.')."""
    return sin_acentos(_RX_FORMA_CABECERA.sub("", linea))


def _analizar(texto: str) -> _Analisis:
    """Decide qué líneas se quitan (números de página y cabeceras repetidas) sin modificar las demás."""
    lineas, inicio_pagina = _normalizar_lineas(texto)
    an = _Analisis(lineas=lineas, inicio_pagina=inicio_pagina)
    n = len(lineas)
    en_fence = [False] * n
    dentro = False
    for i, s in enumerate(lineas):
        if s.startswith("```"):
            dentro = not dentro
            en_fence[i] = True
        else:
            en_fence[i] = dentro

    # 1. Números de página inequívocos (en cualquier parte) y marcas de página del OCR.
    separador = [False] * n
    # Solo si es el folio del documento (la misma forma, con los dígitos cambiados, aparece 3+ veces), la marca del OCR
    # o está pegada a un salto de página: 'Página: 531' en la ficha de una tesis o 'Página 31' + 'DICE:' de una fe de
    # erratas son contenido.
    formas_pagina = Counter(_RX_DIGITOS.sub("#", s) for i, s in enumerate(lineas)
                            if s and not en_fence[i] and es_numero_pagina(s))
    for i, s in enumerate(lineas):
        if s and not en_fence[i] and es_numero_pagina(s) and (
                formas_pagina[_RX_DIGITOS.sub("#", s)] >= 3 or _RX_MD_PAGINA.match(s)
                or any(j in inicio_pagina for j in (i - 1, i, i + 1))):
            an.quitar[i] = "numeros_pagina"
            separador[i] = True

    # 2. Documentos sin \x0c donde el salto de página es la única línea en blanco (texto sin párrafos separados).
    blancas = sum(1 for s in lineas if not s)
    blanco_es_salto = n >= 40 and not inicio_pagina and blancas / n < 0.08
    if blanco_es_salto:
        for i, s in enumerate(lineas):
            if not s:
                separador[i] = True
    an.paginas = max(len(inicio_pagina) + 1, sum(separador), (blancas + 1) if blanco_es_salto else 1)

    def junto_a_separador(i: int) -> bool:
        """La línea i está pegada a un corte de página (solo líneas vacías o quitadas de por medio)."""
        if i in inicio_pagina:
            return True
        j = i - 1
        while j >= 0 and (not lineas[j] or j in an.quitar):
            if separador[j] or j in inicio_pagina:
                return True
            j -= 1
        if j < 0:
            return True
        j = i + 1
        while j < n:
            if j in inicio_pagina or separador[j]:
                return True
            if lineas[j] and j not in an.quitar:
                return False
            j += 1
        return True

    # 3. Números sueltos: candidatos pegados a un corte de página detectado ANTES (no a otro número ya quitado: una
    #    lista '23.'...'45.' se arrastraría), con el estilo de folio del documento (todos con punto o todos sin él) y
    #    en secuencia confirmada por otro folio de una página vecina. El número de un artículo citado que cae solo al
    #    pie ('...del artículo' + '160.') o la etiqueta '2.' de un párrafo numerado no tienen esa compañía.
    if an.paginas >= 4:
        candidatos = [i for i, s in enumerate(lineas)
                      if s and not en_fence[i] and i not in an.quitar and _RX_NUMERO_SOLO.match(s) and junto_a_separador(i)]
        con_punto = sum(1 for i in candidatos if lineas[i].endswith("."))
        estilo_punto = con_punto * 2 > len(candidatos)
        candidatos = [i for i in candidatos if lineas[i].endswith(".") == estilo_punto]
        valores = [int(lineas[i].rstrip(".")) for i in candidatos]
        folio_previo: list[int | None] = []       # valor del último folio inequívoco (paso 1) antes de cada candidato
        ultimo: int | None = None
        for i in range(n):
            if an.quitar.get(i) == "numeros_pagina" and (cifra := _RX_DIGITOS.search(lineas[i])):
                ultimo = int(cifra.group())
            if len(folio_previo) < len(candidatos) and candidatos[len(folio_previo)] == i:
                folio_previo.append(ultimo)
        pagina_de: list[int] = []
        corte, siguiente = 0, 0
        for i in range(n):
            if i in inicio_pagina or separador[i]:
                corte += 1
            if siguiente < len(candidatos) and candidatos[siguiente] == i:
                pagina_de.append(corte)
                siguiente += 1

        def vecino(pos: int, distancia: int, *saltos: int) -> bool:
            """Otro candidato, a lo más `distancia` lugares antes o después, en otra página y con valor v+salto si está
            después o v-salto si está antes (el folio siguiente nunca queda en una página anterior)."""
            return any(0 <= m < len(candidatos) and pagina_de[m] != pagina_de[pos]
                       and (valores[m] - valores[pos]) * (1 if m > pos else -1) in saltos
                       for d in range(1, distancia + 1) for m in (pos - d, pos + d))

        anterior = None
        pagina_anterior = 0
        for pos, i in enumerate(candidatos):
            v = valores[pos]
            if anterior is None:                      # el primer folio necesita compañía; después basta la secuencia
                # Tras un folio inequívoco ('157 de 166') el suelto lo continúa o trae dos confirmaciones: '38' | '39' de
                # la columna de un índice no son folios.
                seguro = vecino(pos, 1, 1) and vecino(pos, 2, 2)
                previo = folio_previo[pos]
                acepta = (vecino(pos, 2, -2, -1, 1, 2) and (v <= an.paginas + 30 or seguro)
                          and (previo is None or 0 <= v - previo <= 10 or seguro))
            else:                                     # el mismo valor solo en la página contigua; reinicio con dos confirmaciones
                acepta = (1 <= v - anterior <= 10 or (v == anterior and pagina_de[pos] - pagina_anterior <= 1)
                          or (v < anterior and vecino(pos, 1, 1) and vecino(pos, 2, 2)))
            if acepta:
                an.quitar[i] = "numeros_pagina"
                separador[i] = True
                anterior = v
                pagina_anterior = pagina_de[pos]

    # 4. Cabeceras y pies repetidos: líneas cortas repetidas 4+ veces, pegadas a los cortes de página.
    def es_candidata(s: str) -> bool:
        if not s or len(s) > 160 or not any(c.isalpha() for c in s):
            return False
        # 'H. CONGRESO DEL ESTADO' o 'C. GOBERNADOR' parecen punto de lista ('H.') pero son cabeceras típicas.
        if _es_tabla(s) or _RX_MD_ENCABEZADO.match(s) or _RX_LISTA_MINUSCULA.match(s) or _RX_ETIQUETA.match(s):
            return False
        if _es_anotacion(s) or _es_anotacion(_RX_NOTA_AL_PIE.sub("", s, count=1)):
            return False                                 # nota de reforma, también numerada al pie ('2 Fracción reformada...')
        return not (_transitorios(s) or _unidad(s) or _articulo(s))

    claves = {}
    for i, s in enumerate(lineas):
        if i not in an.quitar and not en_fence[i] and es_candidata(s):
            claves[i] = _clave_cabecera(s)
    conteo = Counter(claves.values())
    candidatas = {i: k for i, k in claves.items() if conteo[k] >= 4}
    if candidatas and an.paginas >= 4:
        # Tiradas: una nueva en cada corte de página y en cada línea de cuerpo (ni vacía, ni candidata, ni quitada).
        bloque, actual = [0] * n, 0
        for i, s in enumerate(lineas):
            if i in inicio_pagina or separador[i] or (s and i not in candidatas and i not in an.quitar):
                actual += 1
            bloque[i] = actual

        def transparente(j: int) -> bool:
            """Líneas que no rompen la tirada de cabecera: vacías, otras candidatas, quitadas o etiquetas ('I.', 'A')."""
            return not lineas[j] or j in candidatas or separador[j] or j in an.quitar or bool(_RX_ETIQUETA.match(lineas[j]))

        def pegada(i: int) -> bool:
            if i in inicio_pagina:
                return True
            j = i - 1
            while j >= 0 and transparente(j):
                if separador[j] or j in inicio_pagina or j in an.quitar:
                    return True
                j -= 1
            if j < 0:
                return True
            j = i + 1
            while j < n:
                if j in inicio_pagina or separador[j] or j in an.quitar:
                    return True
                if not transparente(j):
                    return False
                j += 1
            return True

        pagina_de_linea, corte_ = [0] * n, 0              # mismo contador de cortes que el paso 3
        for i in range(n):
            if i in inicio_pagina or separador[i]:
                corte_ += 1
            pagina_de_linea[i] = corte_
        paginas_con: dict[str, set[int]] = {}            # clave -> páginas DISTINTAS donde va pegada a un corte
        pegadas: Counter[str] = Counter()
        posiciones: dict[str, list[int]] = {}
        formas: dict[str, Counter] = {}                  # clave -> forma exacta -> veces
        for i, k in candidatas.items():
            posiciones.setdefault(k, []).append(i)
            formas.setdefault(k, Counter())[_forma_cabecera(lineas[i])] += 1
            if pegada(i):
                pegadas[k] += 1
                paginas_con.setdefault(k, set()).add(pagina_de_linea[i])
        cabeceras = {k for k, total in conteo.items() if k in pegadas
                     and pegadas[k] >= 4 and pegadas[k] / total >= 0.6 and len(paginas_con[k]) >= max(4, 0.4 * an.paginas)}

        def contigua(i: int, paso: int) -> int | None:
            """Índice de la línea no vacía que sigue (paso 1) o precede (paso -1) a i dentro de su misma página;
            None si hay un corte de página o el borde del documento."""
            j = i
            while True:
                if paso < 0 and j in inicio_pagina:
                    return None
                j += paso
                if not 0 <= j < n or separador[j] or (paso > 0 and j in inicio_pagina):
                    return None
                if lineas[j]:
                    return j

        def clave_contigua(i: int, paso: int) -> str:
            """Clave de la cabecera contigua a i en esa dirección; '' si no hay línea o no es cabecera."""
            j = contigua(i, paso)
            k = candidatas.get(j) if j is not None else None
            return k if k in cabeceras else ""

        # Pies de varias líneas: cada línea va casi siempre junto a la misma otra cabecera (su vecina habitual). Una
        # copia donde falta esa vecina (OCR, otro corte de línea o de página) es DISTINTA y se conserva entera, con
        # las vecinas habituales que sí tiene: quitarle solo las líneas que coinciden la dejaría mutilada.
        vecinas: dict[tuple[str, int], str] = {}
        for k in cabeceras:
            pegadas_k = [i for i in posiciones[k] if pegada(i)]
            for paso in (1, -1):
                clave, veces = Counter(clave_contigua(i, paso) for i in pegadas_k).most_common(1)[0]
                if clave and veces >= 0.8 * len(pegadas_k):
                    vecinas[(k, paso)] = clave
        # Una línea SUELTA que termina en palabra de enlace o coma ("..., también de la") es una frase cortada por el
        # salto de página, no una cabecera; en un pie de varias líneas sí puede pasar ("Coordinación de Registro
        # Parlamentario y" + "Actualización Legislativa."), y ahí la vecina habitual lo confirma.
        def abre_pagina(k: str) -> bool:
            """La línea abre la página en la mayoría de sus copias (cabecera), no la cierra (frase cortada por el salto)."""
            return sum(contigua(i, -1) is None for i in posiciones[k]) * 2 > len(posiciones[k])

        def siguiente_fija(k: str) -> bool:
            """La sigue casi siempre la misma línea no candidata ('ARTÍCULO 123 CONSTITUCIONAL'): quitarla sola la dejaría
            huérfana."""
            sig = Counter(lineas[j] for j in (contigua(i, 1) for i in posiciones[k]) if j is not None)
            return bool(sig) and sig.most_common(1)[0][1] >= 0.8 * len(posiciones[k])

        sueltas_cortadas = {k for k in cabeceras if (k, 1) not in vecinas and (k, -1) not in vecinas
                            and (lineas[posiciones[k][0]].endswith(",")
                                 or _ultima_palabra(lineas[posiciones[k][0]]) in _PALABRAS_ENLACE)
                            and (not abre_pagina(k) or siguiente_fija(k))}
        cabeceras -= sueltas_cortadas
        conservar: set[int] = set()
        habitual = {k: formas[k].most_common(1)[0][0] for k in posiciones}
        fisica, corte_f = [0] * n, 0                      # página física: entre \x0c (sin \x0c, entre folios)
        for i in range(n):
            if i in inicio_pagina or (not inicio_pagina and an.quitar.get(i) == "numeros_pagina"):
                corte_f += 1
            fisica[i] = corte_f

        def copia(i: int) -> set[int]:
            """i y las vecinas habituales contiguas que sí tiene."""
            vistos, pila = {i}, [i]
            while pila:
                x = pila.pop()
                for paso in (1, -1):
                    j = contigua(x, paso)
                    if (j is not None and j not in vistos and (candidatas[x], paso) in vecinas
                            and candidatas.get(j) == vecinas[(candidatas[x], paso)]):
                        vistos.add(j)
                        pila.append(j)
            return vistos

        def intercalada(c: set[int]) -> bool:
            """2+ líneas con la forma habitual exacta y cada vecina habitual que les falta está, con su forma habitual, en
            la misma página física: es la cabecera de siempre con cuerpo intercalado ('XV.-', 'predio.'), no una variante."""
            if len(c) < 2 or any(_forma_cabecera(lineas[j]) != habitual[candidatas[j]] for j in c):
                return False
            for j in c:
                for paso in (1, -1):
                    v = vecinas.get((candidatas[j], paso))
                    cj = contigua(j, paso)
                    if v and (candidatas.get(cj) if cj is not None else None) != v and not any(
                            fisica[m] == fisica[j] and _forma_cabecera(lineas[m]) == habitual[v] for m in posiciones[v]):
                        return False
            return True

        def parte_frase(i: int) -> bool:
            """La copia queda entre dos mitades de una frase (un pie al final de su página): sin ella, el cuerpo
            anterior y el siguiente se unirían. Solo se saltan las cabeceras (no todas las candidatas)."""
            def es_cab(x: int) -> bool:
                return not lineas[x] or x in an.quitar or candidatas.get(x) in cabeceras
            j = i - 1
            while j >= 0 and es_cab(j):
                j -= 1
            m = i + 1
            while m < n and es_cab(m):
                m += 1
            return j >= 0 and m < n and _puede_unir(lineas[j], lineas[m], False)

        repetidas: dict[tuple[str, ...], set[int]] = {}
        for i in sorted(i for k in cabeceras for i in posiciones[k] if pegada(i)
                        and any(clave_contigua(i, paso) != vecinas[(k, paso)] for paso in (1, -1) if (k, paso) in vecinas)):
            if i in conservar or any(i in c for c in repetidas.values()):
                continue
            c = copia(i)
            if not intercalada(c):
                conservar.update(c)                       # variante (OCR, otro corte): se conserva entera, como antes
                continue
            t = tuple(lineas[j] for j in sorted(c))
            if t not in repetidas or (parte_frase(min(repetidas[t])) and not parte_frase(min(c))):
                repetidas[t] = c
        primer_corte_ = min([j for j in range(n) if j in inicio_pagina or separador[j]] or [n])

        def queda(k: str) -> bool:
            return any(not pegada(m) or m in conservar or m < primer_corte_
                       for m in posiciones[k] if _forma_cabecera(lineas[m]) == habitual[k])
        for c in sorted(repetidas.values(), key=min):     # se quita la repetición, no el dato
            if not all(queda(candidatas[j]) for j in c):
                conservar.update(c)
        # De un pie con frases completas (varias líneas o termina en punto) se conserva la primera copia ENTERA que no
        # parta una frase: el aviso legal del SAT o la nota de editor deben quedar una vez en el documento.
        con_frases = {k for k in cabeceras
                      if (k, 1) in vecinas or (k, -1) in vecinas or lineas[posiciones[k][0]].endswith(".")}
        primeras = [i for k in con_frases for i in posiciones[k] if pegada(i)]
        if primeras:                              # la primera copia que no parte una frase (si todas la parten, la primera)
            primer_bloque = bloque[next((i for i in sorted(primeras) if not parte_frase(i)), min(primeras))]
            conservar.update(i for k in cabeceras for i in posiciones[k] if pegada(i) and bloque[i] == primer_bloque)

        # La copia de la PRIMERA página de cada cabecera se conserva (si no queda otra copia): se quita la repetición,
        # no el dato. Las cabeceras traen metadatos que no están en otro lado ("Publicación 2006/09/06", "Vigencia
        # 2006/10/01", "Fecha última reforma: ..."), y en la primera página no hay una frase anterior a la que se pegue.
        primer_corte = min([j for j in range(n) if j in inicio_pagina or separador[j]] or [n])
        for k in cabeceras:
            forma_habitual = formas[k].most_common(1)[0][0]
            habituales = [i for i in posiciones[k] if _forma_cabecera(lineas[i]) == forma_habitual]
            queda_copia = any(not pegada(i) or i in conservar for i in habituales)
            ultimo_bloque = None
            for i in posiciones[k]:
                if not pegada(i) or bloque[i] == ultimo_bloque or i in conservar:
                    continue                      # misma tirada: la segunda copia es contenido (p. ej. el título)
                forma = _forma_cabecera(lineas[i])
                if forma != forma_habitual and formas[k][forma] < 4:
                    continue                      # 'Guerrero;' comparte clave con la cabecera 'GUERRERO.' pero no es ella
                if not queda_copia and i < primer_corte and forma == forma_habitual and not parte_frase(i):
                    queda_copia = True            # la de la primera página se queda (una sola vez)
                    ultimo_bloque = bloque[i]
                    continue
                an.quitar[i] = "cabeceras_repetidas"
                an.cabeceras[lineas[i]] += 1
                ultimo_bloque = bloque[i]
    return an


def _unir_guiones(lineas: list[str]) -> tuple[list[str], int, int]:
    """Une 'desa-' + 'rrollo' (sin guion solo si 'desarrollo' ya aparece en el documento; si no, 'desa-rrollo')."""
    vocabulario = None
    salida: list[str] = []
    unidos = conservados = 0
    n = len(lineas)
    i = 0
    while i < n:
        s = lineas[i]
        i += 1
        while True:
            m = _RX_GUION_FINAL.search(s) if s and not s.startswith(("|", "```")) else None
            if not m:
                break
            j = i
            while j < n and not lineas[j]:
                j += 1
            if j >= n or not lineas[j][:1].isalpha() or lineas[j].startswith("|") or _inicia_bloque(lineas[j]) \
                    or (j > i and not lineas[j][0].islower()):
                break
            sig = lineas[j]
            m_b = re.match(rf"[{_L}]+", sig)
            a, b = m.group(1), m_b.group() if m_b else ""
            if vocabulario is None:
                vocabulario = set(normalizar("\n".join(lineas)).split())
            if b and sig[0].islower() and normalizar(a + b) in vocabulario:
                s = s[:-1] + sig
                unidos += 1
            else:
                s = s + sig
                conservados += 1
            i = j + 1
        salida.append(s)
    return salida, unidos, conservados


def _compactar_blancas(lineas: list[str]) -> list[str]:
    salida: list[str] = []
    for s in lineas:
        if not s and (not salida or not salida[-1]):
            continue
        salida.append(s)
    while salida and not salida[-1]:
        salida.pop()
    return salida


def _limpiar(texto: str) -> tuple[str, dict, _Analisis, list[str]]:
    """Como `limpiar`, devolviendo además el análisis y las líneas conservadas (con los guiones ya unidos)."""
    an = _analizar(texto)
    conservadas = [s for i, s in enumerate(an.lineas) if i not in an.quitar]
    conservadas, unidos, conservados = _unir_guiones(conservadas)
    motivos = Counter(an.quitar.values())
    informe = {
        "numeros_pagina": motivos.get("numeros_pagina", 0),
        "cabeceras_repetidas": dict(an.cabeceras.most_common()),
        "lineas_cabeceras": motivos.get("cabeceras_repetidas", 0),
        "guiones_unidos": unidos,
        "guiones_conservados": conservados,
        "paginas": an.paginas,
    }
    compactas = _compactar_blancas(conservadas)
    return "\n".join(compactas) + ("\n" if compactas else ""), informe, an, conservadas


def limpiar(texto: str) -> tuple[str, dict]:
    """Quita números de página, cabeceras/pies repetidos, dobles espacios y caracteres de control; une los guiones
    de fin de línea. Devuelve (texto, informe)."""
    limpio, informe, _, _ = _limpiar(texto)
    return limpio, informe


# ---------------------------------------------------------------------------------------------------
# unir_lineas: párrafos en una sola línea
# ---------------------------------------------------------------------------------------------------
_RX_ULTIMA_PALABRA = re.compile(rf"([{_L}]+),?$")


def _ultima_palabra(linea: str) -> str:
    m = _RX_ULTIMA_PALABRA.search(linea[-64:])
    return m.group(1).lower() if m else ""


def _puede_unir(acumulada: str, siguiente: str, hubo_blanco: bool) -> bool:
    if not any(c.isalpha() for c in acumulada[-64:]) and not any(c.isalpha() for c in acumulada):
        return False                       # una línea sin letras ('- 2', '12') no es el inicio de un párrafo
    ultimo = acumulada[-1]
    if ultimo in _TERMINADORES:
        return False
    primero = siguiente[0]
    enlace = _ultima_palabra(acumulada) in _PALABRAS_ENLACE and primero.isalpha()
    if _inicia_bloque(siguiente):
        if hubo_blanco:
            return False
        art = _articulo(siguiente)         # '...fracción IV del\nARTÍCULO 15 BIS SE REFORMA': mención cortada, no rótulo
        if art is not None:
            return bool(art["resto"]) and not art["sep"] and enlace
        u = _unidad(siguiente)             # '...LA DENOMINACIÓN DEL\nCAPÍTULO V DEL TÍTULO NOVENO, PUBLICADO EN EL'
        return u is not None and u["mencion"] and (enlace or (ultimo == "," and primero.isalpha()))
    if ultimo == "-" and not hubo_blanco and _RX_GUION_FINAL.search(acumulada[-64:]) and primero.isalpha():
        return True
    if hubo_blanco:
        return primero.islower() or enlace
    if primero.islower() or primero.isdigit() or enlace:
        return True
    if primero in "([":
        return len(siguiente) > 1 and (siguiente[1].islower() or siguiente[1].isdigit())
    if primero in _SIGNOS_CONTINUAN:
        return True
    return ultimo == "," and primero.isalpha()


def _pegar(a: str, b: str) -> str:
    if _RX_GUION_FINAL.search(a[-64:]) and b[:1].isalpha():
        return a + b
    return a + " " + b


def _unir_lineas(texto: str) -> tuple[str, int]:
    salida: list[str] = []
    unidas = 0
    acumulada: str | None = None
    hubo_blanco = False
    en_fence = False

    def cerrar() -> None:
        nonlocal acumulada, hubo_blanco
        if acumulada is not None:
            salida.append(acumulada)
            acumulada = None
        if hubo_blanco:
            salida.append("")
            hubo_blanco = False

    def agregar_fija(s: str, blanco: bool = False) -> None:
        # Filas de tabla, puntos de lista y líneas de índice van seguidos (salvo que el original los separara).
        previa = salida[-1] if salida else ""
        seguidas = ((_es_tabla(s) and _es_tabla(previa)) or (_es_lista(s) and _es_lista(previa))
                    or (_es_indice(s) and _es_indice(previa)))
        if previa and (blanco or not seguidas):
            salida.append("")
        salida.append(s)

    for linea in texto.split("\n"):
        s = linea.rstrip()
        if s.startswith("```"):
            cerrar()
            if en_fence:
                salida.append(s)
            else:
                agregar_fija(s)
            en_fence = not en_fence
            continue
        if en_fence:
            cerrar()
            salida.append(s)
            continue
        if not s:
            if acumulada is not None or (salida and salida[-1]):
                hubo_blanco = True
            continue
        if acumulada is not None and _puede_unir(acumulada, s, hubo_blanco):
            acumulada = _pegar(acumulada, s)
            unidas += 1
            hubo_blanco = False
            continue
        if acumulada is not None:
            salida.append(acumulada)
            acumulada = None
        blanco, hubo_blanco = hubo_blanco, False
        if _es_fija(s):
            agregar_fija(s, blanco)
        elif _es_lista(s) and salida and salida[-1] and _es_lista(salida[-1]):
            if blanco:
                salida.append("")
            acumulada = s
        else:
            if salida and salida[-1]:
                salida.append("")
            acumulada = s
    cerrar()
    salida = _compactar_blancas(salida)
    return "\n".join(salida) + ("\n" if salida else ""), unidas


def unir_lineas(texto: str) -> str:
    """Une las líneas cortadas de un mismo párrafo (conservador: ante la duda, no une)."""
    return _unir_lineas(texto)[0]


# ---------------------------------------------------------------------------------------------------
# encabezados: unidades, transitorios y artículos
# ---------------------------------------------------------------------------------------------------
def _es_nombre(linea: str) -> bool:
    """Nombre de una unidad en su propia línea ('DISPOSICIONES GENERALES', 'De los Derechos Humanos')."""
    if not linea or len(linea) > 100 or linea[0] in "([|#" or linea.endswith((";", ",")):
        return False
    if _es_anotacion(linea) or _es_lista(linea) or re.search(r"(?i)\b(?:derogad|se deroga|reformad|adicionad)", linea):
        return False
    letras = [c for c in linea if c.isalpha()]
    if len(letras) < 2:
        return False
    if sum(c.isupper() for c in letras) / len(letras) >= 0.7:
        return True
    palabras = linea.rstrip(".").split()
    if len(palabras) > 14:
        return False
    if palabras[0] in ("De", "Del", "Disposiciones"):
        return True
    return all(p[0].isupper() for p in palabras if p[0].isalpha() and p.lower() not in _PALABRAS_VACIAS_NOMBRE and len(p) >= 4)


def _clasificar(lineas: list[str]) -> list[tuple | None]:
    """Por línea: ('unidad', dict) · ('articulo', dict) · ('transitorios',) · ('..._previo', ...) para los encabezados
    de un reprocesado (solo cuentan, no se tocan) · None."""
    n = len(lineas)
    clase: list[tuple | None] = [None] * n
    en_fence = False
    for i, s in enumerate(lineas):
        if s.startswith("```"):
            en_fence = not en_fence
            continue
        if en_fence or not s or _es_tabla(s):
            continue
        if _RX_MD_ENCABEZADO.match(s):
            previo = s.lstrip("#").strip()
            if _transitorios(previo):
                clase[i] = ("transitorios_previo",)
            else:
                a = _articulo(previo)
                if a:
                    clase[i] = ("articulo_previo", a)
                elif _unidad(previo):
                    clase[i] = ("unidad_previo",)
            continue
        if _transitorios(s):
            clase[i] = ("transitorios",)
            continue
        u = _unidad(s)
        if u:
            clase[i] = ("unidad", u)
            continue
        a = _articulo(s)
        if a and not (a["resto"] and _RX_HISTORIAL.match(a["resto"])):   # 'ARTÍCULO 492.- fue reformado por...': historial
            clase[i] = ("articulo", a)
    return clase


_RX_NOTA_REFORMA_RESTO = re.compile(r"(?i)\b(?:derogad|se deroga|reformad|adicionad)")   # 'Derogado' es cuerpo, no título
_RX_GUIA = re.compile(r"(?:[.…·]\s*){5,}\d{0,4}")
_RX_GUIA_FINAL = re.compile(r"(?:[.…·]\s*){5,}\d{0,4}$")


def _es_guia(s: str) -> bool:
    """Guía de puntos con la página de un índice en su propia línea ('.......... 4') o al final de un nombre de unidad
    ('DISPOSICIONES GENERALES ........ 6'). Sin '_' para no confundir los separadores '_____' del cuerpo."""
    return bool(_RX_GUIA.fullmatch(s) or (len(s) <= 160 and _RX_GUIA_FINAL.search(s)
                                          and not (_articulo(s) or _unidad(s) or _es_lista(s) or _es_tabla(s))))


def _seguida_de_guia(lineas: list[str], i: int) -> bool:
    """La línea siguiente a la unidad i (o la siguiente a su nombre) es una guía de índice."""
    vistas = 0
    for j in range(i + 1, min(i + 8, len(lineas))):
        s = lineas[j]
        if not s:
            continue
        if _es_guia(s):
            return True
        vistas += 1
        if vistas >= 2 or not _es_nombre(s):
            return False
    return False


def _detectar_indice(lineas: list[str], clase: list[tuple | None]) -> set[int]:
    """Líneas de índice: tiradas de 8+ rótulos sin texto (solo líneas vacías o nombres cortos de por medio). Un rótulo
    con título ('ARTÍCULO 1. Objeto': resto sin puntuación final, sin puntos de guía ni página) no cierra la tirada,
    pero la vuelve CANDIDATA: se confirma solo si después la numeración reinicia (el cuerpo vuelve al artículo 1)."""
    indice: set[int] = set()
    tirada: list[int] = []
    con_titulos = False
    candidatas: list[list[int]] = []
    nombre_de_unidad = False

    def parece_nombre(s: str) -> bool:      # nombre de unidad en su línea: 'Principios y directrices que rigen...'
        return len(s) <= 160 and s[0].isupper() and not s.endswith((".", ";", ":", ","))

    def con_titulo(i: int) -> bool:
        c = clase[i]
        return bool(c and c[0] == "articulo" and c[1]["resto"] and not c[1]["indice"])

    def sigue_rotulo(i: int) -> bool:
        j = i + 1
        while j < len(lineas) and not lineas[j]:
            j += 1
        return j < len(lineas) and bool(clase[j]) and clase[j][0] in ("articulo", "unidad", "transitorios")  # type: ignore[index]

    def cerrar_tirada() -> None:
        nonlocal con_titulos
        if len(tirada) >= 8:
            if con_titulos:
                candidatas.append(list(tirada))
            else:
                indice.update(tirada)
        tirada.clear()
        con_titulos = False

    for i, s in enumerate(lineas):
        c = clase[i]
        if c and c[0] == "articulo":
            if c[1]["indice"]:
                indice.add(i)
                tirada.append(i)
            elif not c[1]["resto"]:
                tirada.append(i)
            elif _es_titulo(c[1]["resto"]) or (len(c[1]["resto"]) <= 160 and sigue_rotulo(i)
                                                and not _RX_NOTA_REFORMA_RESTO.search(c[1]["resto"])):
                tirada.append(i)
                con_titulos = True
            else:
                cerrar_tirada()
        elif c and c[0] == "unidad":
            if c[1]["indice"] or _seguida_de_guia(lineas, i):
                indice.add(i)
            tirada.append(i)
        elif c and c[0] == "transitorios":
            tirada.append(i)
        elif (s and not (c and c[0].endswith("_previo")) and not _es_nombre(s)
              and not (nombre_de_unidad and con_titulos and parece_nombre(s))):
            cerrar_tirada()
        if s:
            nombre_de_unidad = bool(c and c[0] == "unidad" and not c[1]["resto"])
    cerrar_tirada()

    # Confirmación de las candidatas (de la última a la primera, para que un índice partido en varias tiradas se confirme
    # entero): 8+ artículos numerados en orden creciente y el PRIMER rótulo numerado que sigue fuera de los índices
    # REINICIA la numeración (vuelve al primer número o antes) sin que medie TRANSITORIOS. Un bloque de artículos
    # derogados del cuerpo no lo cumple: lo sigue el siguiente artículo con texto. Los encabezados de un reprocesado
    # cuentan como rótulos (idempotencia).
    secuencia = [(i, c[1]["n"]) for i, c in enumerate(clase)
                 if c and c[0] in ("articulo", "articulo_previo") and not c[1]["ordinal"] and c[1]["n"] is not None]

    def es_transitorios(j: int) -> bool:
        c = clase[j]
        return bool(c and c[0] in ("transitorios", "transitorios_previo") and j not in indice)

    for cand in reversed(candidatas):
        numerados = [(i, clase[i][1]["n"]) for i in cand                       # type: ignore[index]
                     if clase[i] and clase[i][0] == "articulo" and not clase[i][1]["ordinal"]]   # type: ignore[index]
        # Si la numeración cae dentro de la tirada ('ARTÍCULO 10. Vigencia' y luego 'ARTÍCULO 1. Objeto' del cuerpo con
        # el texto en la línea siguiente), ahí termina el índice; las unidades justo antes de la caída son del cuerpo.
        caida = next((k for k in range(1, len(numerados)) if numerados[k][1] < numerados[k - 1][1]), None)
        if caida is not None:
            fin = cand.index(numerados[caida][0])
            while fin > 0 and clase[cand[fin - 1]] and clase[cand[fin - 1]][0] in ("unidad", "transitorios"):  # type: ignore[index]
                fin -= 1
            cand, numerados = cand[:fin], numerados[:caida]
        nums = [n_ for _, n_ in numerados]
        crecientes = sum(1 for a, b in zip(nums, nums[1:]) if b >= a)  # noqa: B905, RUF007 - sin pairwise ni strict (3.10+)
        reinicio = next(((i, n_) for i, n_ in secuencia if cand and i > cand[-1] and i not in indice), None)
        if (len(nums) >= 8 and crecientes >= 0.9 * (len(nums) - 1) and reinicio is not None
                and reinicio[1] <= nums[0] < nums[-1] and not any(es_transitorios(j) for j in range(cand[-1] + 1, reinicio[0]))):
            indice.update(cand)
            continue
        sub: list[int] = []                               # no confirmada: la regla original (subtiradas sin títulos)
        for i in cand:
            if con_titulo(i):
                if len(sub) >= 8:
                    indice.update(sub)
                sub = []
            else:
                sub.append(i)
        if len(sub) >= 8:
            indice.update(sub)
    return indice


def _encabezados(texto: str) -> tuple[str, dict]:
    lineas = texto.split("\n")
    n = len(lineas)
    clase = _clasificar(lineas)
    indice = _detectar_indice(lineas, clase)

    # "APARTADO A, B, ..." que empieza después de un artículo es subdivisión suya (como el 123 constitucional): la serie
    # entera queda como párrafos. Solo es unidad la serie que empieza tras una unidad o al principio.
    ultimo_articulo = False
    apartado_unidad = True
    for i, c in enumerate(clase):
        if not c or i in indice:
            continue
        if c[0] in ("articulo", "articulo_previo"):
            ultimo_articulo = True
        elif c[0] == "unidad" and c[1]["clase"] == "APARTADO":
            if c[1]["serie"] == "inicio":
                apartado_unidad = not ultimo_articulo
            if not apartado_unidad and c[1]["serie"] != "unico":
                clase[i] = None
                continue
            ultimo_articulo = False
        else:
            ultimo_articulo = False

    # Coherencia de los artículos numerados: 3+ y en orden creciente (en su mayoría).
    numerados = [(c[1]["n"], i) for i, c in enumerate(clase)
                 if c and c[0] == "articulo" and not c[1]["ordinal"] and i not in indice]
    crecientes = sum(1 for (a, _), (b, _) in zip(numerados, numerados[1:]) if b >= a)  # noqa: B905, RUF007 - sin pairwise (3.10+)
    convertir_numerados = len(numerados) >= 3 and crecientes >= 0.7 * (len(numerados) - 1)
    sospechosos = len(numerados) >= 3 and not convertir_numerados
    ordinales = [i for i, c in enumerate(clase) if c and c[0] == "articulo" and c[1]["ordinal"] and i not in indice]
    primer_transitorio = next((i for i, c in enumerate(clase) if c and c[0] in ("transitorios", "transitorios_previo")
                               and i not in indice), None)
    hay_numerados = bool(numerados) or any(c and c[0] == "articulo_previo" and not c[1]["ordinal"] for c in clase)
    ordinales_libres = not hay_numerados and len(ordinales) >= 3

    # Niveles de las unidades presentes (fuera de los índices).
    clases_presentes = sorted({c[1]["clase"] for i, c in enumerate(clase) if c and c[0] == "unidad" and i not in indice},
                              key=lambda c: _RANGO_UNIDAD[c])
    rangos = sorted({_RANGO_UNIDAD[c] for c in clases_presentes})
    niveles = {c: min(rangos.index(_RANGO_UNIDAD[c]) + 1, 3) for c in clases_presentes}

    salida: list[str] = []
    conteo: Counter[int] = Counter()
    consumidas: set[int] = set()
    unidades: Counter[str] = Counter()
    informe: dict[str, Any] = {"articulos": 0, "transitorios": 0, "indice_lineas": len(indice),
                               "articulos_sospechosos": sospechosos, "niveles": niveles}

    serie = [(i, c[1]["n"]) for i, c in enumerate(clase)
             if c and c[0] in ("articulo", "articulo_previo") and not c[1]["ordinal"] and c[1]["n"] is not None
             and i not in indice]
    pos_serie = {i: k for k, (i, _) in enumerate(serie)}

    def mencion_fuera_de_serie(i: int) -> bool:
        """'...que establece el' + 'Artículo 292.': rótulo sin texto, tras palabra de enlace y fuera de la numeración."""
        j = i - 1
        while j >= 0 and not lineas[j]:
            j -= 1
        previa = lineas[j] if j >= 0 else ""
        if (not previa or not previa[-1].isalpha() or _RX_MD_ENCABEZADO.match(previa)
                or _ultima_palabra(previa) not in _PALABRAS_ENLACE):
            return False
        k = pos_serie[i]
        if k == 0 or k + 1 >= len(serie):
            return False
        return not (serie[k - 1][1] <= clase[i][1]["n"] <= serie[k + 1][1])   # type: ignore[index]

    def encabezado(nivel: int, texto_enc: str) -> None:
        if salida and salida[-1]:
            salida.append("")
        salida.append("#" * nivel + " " + texto_enc)
        salida.append("")
        conteo[nivel] += 1

    for i, s in enumerate(lineas):
        if i in consumidas:
            continue
        c = clase[i]
        if not c or i in indice or c[0].endswith("_previo"):
            salida.append(s)
            continue
        if c[0] == "transitorios":
            encabezado(2, s)
            informe["transitorios"] += 1
            continue
        if c[0] == "unidad":
            u = c[1]
            texto_enc = s
            resto = u["resto"]
            letra = bool(re.fullmatch(r"[A-ZÑ]", resto))      # 'SECCIÓN QUINTA A' + 'DE LA COMISIÓN...'
            corta = bool(resto) and not u["indice"] and (     # 'Sección Segunda De' + 'la Reclamación'
                letra or resto.endswith(",") or _ultima_palabra(resto) in _PALABRAS_ENLACE)
            if not resto or corta:
                nombres: list[str] = []
                j = i + 1
                while j < n and len(nombres) < 2:
                    if not lineas[j]:
                        j += 1
                        continue
                    if j in indice or clase[j] or not _es_nombre(lineas[j]):
                        break
                    nombres.append(lineas[j])
                    consumidas.add(j)
                    if lineas[j].endswith("."):
                        break
                    j += 1
                if nombres:
                    sep = " " if s.endswith((".", ":", "-", "–", "—")) or (corta and not letra) else ". "
                    texto_enc = s + sep + " ".join(nombres)
            encabezado(niveles[u["clase"]], texto_enc)
            unidades[u["clase"].lower()] += 1
            continue
        a = c[1]
        if not a["ordinal"] and not a["resto"] and a["sep"] and a["n"] is not None and mencion_fuera_de_serie(i):
            salida.append(s)                              # mención cortada: queda como párrafo, sin encabezado
            continue
        convertir = (not a["ordinal"] and convertir_numerados) or (
            a["ordinal"] and ((primer_transitorio is not None and i > primer_transitorio) or ordinales_libres))
        if not convertir:
            salida.append(s)
            continue
        while True:
            encabezado(4, a["etiqueta"])
            informe["articulos"] += 1
            if not a["resto"]:
                break
            repetido = _articulo(a["resto"])              # rótulo duplicado por el PDF: 'ARTÍCULO ÚNICO: ARTÍCULO ÚNICO: ...'
            if repetido and not repetido["indice"] and repetido["ordinal"] == a["ordinal"]:
                a = repetido
                continue
            salida.append(a["resto"])
            break
    salida = _compactar_blancas(salida)
    informe["encabezados"] = {nivel: conteo.get(nivel, 0) for nivel in (1, 2, 3, 4)}
    informe["unidades"] = dict(unidades)
    return "\n".join(salida) + ("\n" if salida else ""), informe


def encabezados(texto: str) -> str:
    """Convierte en encabezados las unidades jurídicas, los transitorios y los artículos (ver la cabecera del módulo)."""
    return _encabezados(texto)[0]


# ---------------------------------------------------------------------------------------------------
# validación: palabras, números, orden y revisión independiente de lo quitado
# ---------------------------------------------------------------------------------------------------
_RX_PALABRA = re.compile(r"\w+")
_RX_CON_SIGNOS = re.compile(r"[^\W_]+(?:[.,/-][^\W_]+)+")          # '5/DEC-12', '171-1', '2.1.1', '09-04-2026'

_RX_ROTULO_PEGADO = re.compile(                         # 'Artículo12', 'ARTÍCULOSEGUNDO': rótulo pegado por el PDF
    r"^(art[íi]culo)(\d.*|(?:primer|segund|tercer|cuart|quint|sext|s[ée]ptim|octav|noven|d[ée]cim|[úu]nic).*)$")
_RX_FUSION_PAGINA = re.compile(r"[^\s\x0c]\x0c[^\s\x0c]")
_EQUIVALENTES = {"art": "artículo", "articulo": "artículo"}


def _tokens_lista(texto: str) -> list[str]:
    """Palabras en orden, con acentos y en minúsculas; 'Art.'/'ARTICULO' valen 'artículo' y el rótulo pegado por el
    PDF ('Artículo12') se parte, que es lo único que el posprocesado normaliza."""
    salida: list[str] = []
    for t in _RX_PALABRA.findall(texto.casefold()):
        m = _RX_ROTULO_PEGADO.match(t)
        if m:
            salida.append("artículo")
            salida.append(m.group(2))
        else:
            salida.append(_EQUIVALENTES.get(t, t))
    return salida


def _tokens(texto: str) -> Counter:
    return Counter(_tokens_lista(texto))


_RX_SIGNO_MINUSCULA = re.compile(r"[.,/-][a-záéíóúüñ]")
_RX_ANTES_ARTICULO = re.compile(r"(?i)\bart(?:[íi]culos?|\.)?\s*$")


def _numeros_con_signos(texto: str) -> list[str]:
    """Fichas con dígitos unidas por signos ('5/DEC-12', '09-04-2026', '2,595,560'), que el posprocesado no debe partir ni
    cambiar. Se omite el rótulo de artículo al inicio de línea (es lo único que se normaliza: 'Art.181.-' -> 'Artículo
    181', 'Artículo 34.El' -> rótulo + párrafo), las fichas con forma de número de artículo ('17-A', '7.35': una mención
    cortada 'artículo' + salto + '17-A' se une o no según la línea siguiente) y las que llevan minúscula tras el signo."""
    fichas: list[str] = []
    previa = ""                                   # última línea no vacía ('artículo' + salto + '17-A')
    for linea in texto.split("\n"):
        linea = linea.strip()
        if _RX_MD_ENCABEZADO.match(linea):
            if _articulo(linea.lstrip("#").strip()):
                previa = linea
                continue
        else:
            m = _RX_ARTICULO.match(linea)
            if m and _articulo(linea):
                linea = linea[m.end("num"):]
        for m in _RX_CON_SIGNOS.finditer(linea):
            f = m.group()
            if not any(c.isdigit() for c in f) or _RX_SIGNO_MINUSCULA.search(f):
                continue
            antes = linea[:m.start()] if m.start() else previa
            if _RX_NUM_ARTICULO_SUELTO.match(f) and _RX_ANTES_ARTICULO.search(antes[-40:]):
                continue                          # mención de artículo cortada: la une o no la línea siguiente
            fichas.append(f.casefold())
        if linea:
            previa = linea
    return fichas


def _bigramas(tokens: list[str]) -> Counter:
    return Counter(zip(tokens, tokens[1:]))  # noqa: B905, RUF007 - sin pairwise ni strict (3.10+)


def _revisar_quitadas(an: _Analisis) -> list[dict]:
    """Revisión independiente de lo que `_analizar` decidió quitar: lo que parece contenido se marca sospechoso."""
    lineas = an.lineas
    formas: Counter = Counter(_forma_cabecera(s) for s in lineas if s)
    sospechosas = []
    for i in sorted(an.quitar):
        s = lineas[i]
        razon = ""
        if an.quitar[i] == "cabeceras_repetidas":
            if s.endswith(";"):
                razon = "termina en ';'"
            elif _es_anotacion(_RX_NOTA_AL_PIE.sub("", s, count=1)):
                razon = "nota de reforma"
            elif formas[_forma_cabecera(s)] < 4:
                j, blanco = i - 1, False
                while j >= 0 and (not lineas[j] or j in an.quitar):
                    blanco = blanco or not lineas[j]
                    j -= 1
                if j >= 0 and _puede_unir(lineas[j], s, blanco):
                    razon = "variante rara que continúa la frase anterior"
        elif _RX_NUMERO_SOLO.match(s):
            for paso in (-1, 1):
                j = i + paso
                while 0 <= j < len(lineas) and not lineas[j]:
                    j += paso
                if (0 <= j < len(lineas) and j in an.quitar and _RX_NUMERO_SOLO.match(lineas[j])
                        and not any(k in an.inicio_pagina for k in range(min(i, j) + 1, max(i, j) + 1))):
                    razon = "número suelto junto a otro número quitado"      # con un corte de página en medio es una página vacía
        if razon:
            sospechosas.append({"linea": i + 1, "texto": s, "motivo": razon})
    return sospechosas


def _comparar(cuerpo_crudo: str, conservadas: list[str], an: _Analisis, cuerpo_final: str) -> dict:
    """Compara el crudo (sin normalizar) con el resultado: palabras, números con signos y orden; más la revisión
    independiente de lo quitado. `conservadas` son las líneas que sobrevivieron a `limpiar`, con los guiones unidos."""
    texto_conservado = "\n".join(conservadas)
    esperado = _tokens(cuerpo_crudo)                      # del crudo tal cual: un \x0c que pegara palabras se notaría
    esperado.update(_tokens(texto_conservado))            # ... más lo conservado (guiones ya unidos) ...
    esperado.subtract(_tokens("\n".join(an.lineas)))      # ... menos todas las líneas analizadas = crudo - quitadas
    obtenido = _tokens(cuerpo_final)
    perdidas = +(esperado - obtenido)
    nuevas = +(obtenido - esperado)
    num_esperados = Counter(_numeros_con_signos(texto_conservado))   # el crudo sin normalizar ya se cotejó palabra a palabra
    num_obtenidos = Counter(_numeros_con_signos(cuerpo_final))
    num_perdidos = +(num_esperados - num_obtenidos)
    num_nuevos = +(num_obtenidos - num_esperados)
    bg_esperados = _bigramas(_tokens_lista(texto_conservado))
    bg_obtenidos = _bigramas(_tokens_lista(cuerpo_final))
    bigramas_distintos = sum((bg_esperados - bg_obtenidos).values()) + sum((bg_obtenidos - bg_esperados).values())
    sospechosas = _revisar_quitadas(an)
    total = sum(len(_RX_PALABRA.findall(s)) for s in an.lineas)
    quitadas = sum(len(_RX_PALABRA.findall(an.lineas[i])) for i in an.quitar)
    return {
        "ok": (not perdidas and not nuevas and not num_perdidos and not num_nuevos and bigramas_distintos == 0
               and not sospechosas),
        "palabras_perdidas": [p for p, _ in perdidas.most_common(20)],
        "palabras_nuevas": [p for p, _ in nuevas.most_common(20)],
        "total_perdidas": sum(perdidas.values()),
        "total_nuevas": sum(nuevas.values()),
        "numeros_perdidos": [p for p, _ in num_perdidos.most_common(20)],
        "numeros_nuevos": [p for p, _ in num_nuevos.most_common(20)],
        "bigramas_distintos": bigramas_distintos,
        "lineas_sospechosas": sospechosas[:50],
        "lineas_quitadas": len(an.quitar),
        "fraccion_quitada": round(quitadas / total, 4) if total else 0.0,
        "fusiones_pagina": len(_RX_FUSION_PAGINA.findall(cuerpo_crudo)),
    }


def estructurar(texto_crudo: str, meta: dict) -> tuple[str, dict]:
    """Markdown crudo -> Markdown estructurado con front-matter. Idempotente. Devuelve (markdown, informe); el informe
    trae en "validacion" lo mismo que `validar(crudo, markdown)` sin volver a calcular nada."""
    _, cuerpo = separar_front_matter(texto_crudo)
    palabras_antes = len(_RX_PALABRA.findall(cuerpo))
    limpio, inf_limpiar, an, conservadas = _limpiar(cuerpo)
    unido, lineas_unidas = _unir_lineas(limpio)
    con_encabezados, inf_enc = _encabezados(unido)
    cuerpo_final = con_encabezados.strip("\n")
    markdown = front_matter(meta) + ("\n" + cuerpo_final + "\n" if cuerpo_final else "")
    informe = {
        "encabezados": inf_enc["encabezados"],
        "articulos": inf_enc["articulos"],
        "unidades": inf_enc["unidades"],
        "transitorios": inf_enc["transitorios"],
        "niveles": inf_enc["niveles"],
        "articulos_sospechosos": inf_enc["articulos_sospechosos"],
        "indice_lineas": inf_enc["indice_lineas"],
        "lineas_quitadas": {"numeros_pagina": inf_limpiar["numeros_pagina"],
                            "cabeceras_repetidas": inf_limpiar["lineas_cabeceras"]},
        "cabeceras_repetidas": inf_limpiar["cabeceras_repetidas"],
        "guiones_unidos": inf_limpiar["guiones_unidos"],
        "guiones_conservados": inf_limpiar["guiones_conservados"],
        "paginas": inf_limpiar["paginas"],
        "lineas_unidas": lineas_unidas,
        "palabras_antes": palabras_antes,
        "palabras_despues": len(_RX_PALABRA.findall(cuerpo_final)),
        "validacion": _comparar(cuerpo, conservadas, an, cuerpo_final),
    }
    return markdown, informe


def validar(crudo: str, resultado: str) -> dict:
    """Comprueba que el resultado conserva el texto del crudo salvo las líneas quitadas (números de página y cabeceras
    repetidas): palabras con acentos, números con signos y orden; y revisa de forma independiente lo quitado
    (`lineas_sospechosas`). `ok` solo si todo cuadra. Es lo mismo que `estructurar(...)[1]["validacion"]`."""
    _, cuerpo_crudo = separar_front_matter(crudo)
    _, cuerpo_res = separar_front_matter(resultado)
    _, _, an, conservadas = _limpiar(cuerpo_crudo)
    return _comparar(cuerpo_crudo, conservadas, an, cuerpo_res.strip("\n"))


# ====================================================================================================
# Herramienta: convertir
# ====================================================================================================
AYUDA_CONVERTIR = """
biblioteca.py convertir — Convierte a Markdown los documentos de la biblioteca (PDF, y los pocos que la fuente
oficial publica solo en Word).

ES UN PROCESO APARTE de la actualización: solo se ejecuta cuando el usuario lo pide
o acepta ("¿deseas convertir los N PDFs a Markdown?"). Nunca lo lances por tu cuenta.

Usa MarkItDown de Microsoft. Si un PDF es escaneado (imagen, sin texto), usa
OCR en español (Tesseract). Un Word oficial (.doc/.docx) se lee primero con MarkItDown y, si no puede
(MarkItDown no lee el .doc de Word 97-2003), con el lector de Word propio (biblioteca.py word). Solo convierte lo
que hace falta: documentos nuevos o actualizados, o sin Markdown.

El texto crudo se ESTRUCTURA antes de guardarse (herramientas/convertir.py, formato 2): front-matter YAML con los datos
del documento, encabezados por libro/título/capítulo y "#### Artículo N", líneas cortadas unidas, sin números de
página ni cabeceras repetidas; tablas intactas. Antes de aceptar el resultado se comprueba que no se perdió ni una
palabra del texto crudo (si se perdiera, se guarda el crudo con formato 1 y se avisa). El manifiesto anota
markdown.formato; cuando el formato vigente es más nuevo que el guardado, --reestructurar vuelve a aplicar el
formato al Markdown existente sin reconvertir el documento (es idempotente: reaplicarlo no cambia nada).

Lecciones aprendidas que esta herramienta ya aplica:
  - MarkItDown con enable_plugins=False (sin esto carga modelos pesados).
  - Un PDF por subproceso con límite de tiempo: un PDF gigante no atora el lote
    ni consume toda la memoria.
  - Máximo 5 conversiones simultáneas (con más, la computadora puede colapsar).
  - Si se agota el tiempo, se reintenta con más tiempo: un PDF grande NO es
    necesariamente escaneado.
  - Menos de 200 caracteres extraídos, o menos de 300 por página en un PDF de 3 páginas o más
    (escaneado o mixto: casi todo son imágenes) = OCR en español. En un PDF mixto solo se pasan por
    OCR las páginas sin texto; las demás conservan el suyo.

USO:
  python sistema/biblioteca.py convertir --listar           SOLO cuenta y lista lo pendiente (no convierte nada)
  python sistema/biblioteca.py convertir                    convierte todo lo pendiente
  python sistema/biblioteca.py convertir --id ID [ID ...]   solo esos documentos
  python sistema/biblioteca.py convertir --grupo G / --entidad E   solo ese grupo (basta una parte del nombre) o esa
                                                        entidad (--entidad federal = los federales)
  python sistema/biblioteca.py convertir --forzar           reconvertir aunque esté al día (toda la biblioteca, o
                                                 lo que indiquen --id / --grupo / --entidad)
  python sistema/biblioteca.py convertir --procesos 3       conversiones simultáneas (1 a 5; por defecto 3)
  python sistema/biblioteca.py convertir --reestructurar    reaplica el formato vigente a los Markdown con formato anterior
                                                 (acepta --id / --grupo / --entidad; con --forzar, a todos)

"Pendiente" = documento descargado cuyo Markdown no existe o se hizo de una versión anterior
(se compara la huella SHA-256 del documento con la que quedó anotada al convertir). Cada Markdown se
escribe primero en la carpeta temporal y se coloca en su sitio dentro del candado, releyendo el catálogo:
si otro agente movió o retiró el documento mientras se convertía, no queda un .md huérfano.

CÓDIGOS DE SALIDA: 0 = todo bien (o nada pendiente) · 1 = hubo fallas (se listan).
"""


TIEMPO_NORMAL = 180        # segundos por PDF
TIEMPO_EXTENDIDO = 1500    # segundo intento para PDFs muy grandes
TIEMPO_ESTRUCTURAR = 900   # reaplicar el formato a un Markdown ya convertido (menos de 5 s en los más grandes)
MAX_PROCESOS = 5
MIN_CARACTERES_POR_PAGINA = 300   # una página de ley trae ~2,500; los PDF escaneados o mixtos, casi nada


# ---------------------------------------------------------------------------
# Modo interno: lo ejecuta un subproceso para convertir UN archivo
# ---------------------------------------------------------------------------
def _paginas(pdf: str) -> int:
    """Número de páginas del PDF (0 si no se puede saber)."""
    try:
        import pypdfium2 as pdfium
        documento = pdfium.PdfDocument(pdf)
        try:
            return len(documento)
        finally:
            documento.close()
    except Exception:  # noqa: BLE001 - sin el dato, solo cuenta el mínimo de caracteres
        return 0


def _escribir(md: str, texto: str, metodo: str) -> None:
    texto = texto.strip()
    os.makedirs(os.path.dirname(md), exist_ok=True)
    with open(md, "w", encoding="utf-8", newline="\n") as f:      # siempre \n, también en Windows
        f.write(texto + "\n")
    print(f"OK\t{len(texto)}\t{metodo}")


def _interno_word(archivo: str, md: str, markitdown) -> None:
    """Word oficial: MarkItDown primero; si falla o casi no saca texto, el lector propio (`descargar.a_markdown`)."""
    texto = ""
    if markitdown is not None:
        try:
            texto = (markitdown(enable_plugins=False).convert(archivo).text_content or "").strip()
        except Exception:  # noqa: BLE001 - p. ej. el .doc de Word 97-2003: MarkItDown no lo soporta
            texto = ""
    if len(texto) >= MIN_CARACTERES_MD:
        return _escribir(md, texto, "markitdown")
    from herramientas import descargar as word
    try:
        texto = word.a_markdown(Path(archivo))
    except word.ErrorWord as e:
        print(f"FALLA\t{e}")
        sys.exit(1)
    if len(texto.strip()) < MIN_CARACTERES_MD:
        print(f"FALLA\tel Word casi no tiene texto ({len(texto.strip())} caracteres)")
        sys.exit(1)
    _escribir(md, texto, "lector_word")


def _interno_markitdown(pdf: str, md: str, url: str = "") -> None:
    import warnings
    warnings.filterwarnings("ignore")
    formato = formato_archivo(Path(pdf))
    try:
        from markitdown import MarkItDown
    except ImportError as e:
        if isinstance(e, ModuleNotFoundError) and (e.name or "").split(".")[0] == "markitdown":
            print("FALLA\tmarkitdown no está instalado: ejecuta python sistema/biblioteca.py instalar")
        elif "DLL load failed" in str(e) or "demasiado largo" in str(e) or "too long" in str(e).lower():
            print(f"FALLA\tmarkitdown está instalado pero no se pudo cargar ({str(e)[:150]}): la ruta de la "
                  "carpeta es demasiado larga para Windows; mueve la biblioteca a una ruta más corta (p. ej. C:\\Biblioteca)")
        else:
            print(f"FALLA\tno se pudo cargar markitdown: {type(e).__name__}: {str(e)[:200]}")
        if formato in ("doc", "docx"):           # el Word todavía se puede leer con el lector propio
            return _interno_word(pdf, md, None)
        sys.exit(1)
    if formato in ("doc", "docx"):
        return _interno_word(pdf, md, MarkItDown)
    texto = (MarkItDown(enable_plugins=False).convert(pdf).text_content or "").strip()
    paginas = _paginas(pdf)
    if len(texto) < MIN_CARACTERES_MD or (paginas >= 3 and len(texto) / paginas < MIN_CARACTERES_POR_PAGINA):
        print(f"VACIO\t{len(texto)}")      # escaneado, o mixto con casi todo en imágenes
        return
    _escribir(md, texto, "markitdown")


def _entorno() -> dict:
    """Lo que dejó anotado biblioteca.py instalar (ruta de Tesseract y carpeta del idioma español)."""
    return leer_json(ENTORNO, {})


def ruta_tesseract() -> str | None:
    """Tesseract según biblioteca.py instalar, en el PATH o en las rutas típicas de Windows/macOS."""
    for candidato in (_entorno().get("tesseract"), shutil.which("tesseract"),
                      r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                      "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if candidato and os.path.isfile(candidato):
            return candidato
    return None


def usar_tessdata_local() -> None:
    """Si el español se descargó en la carpeta de datos (instalar), Tesseract debe buscarlo ahí.
    Se usa la variable TESSDATA_PREFIX y no '--tessdata-dir': en Windows las comillas de una ruta
    con espacios llegan literales a Tesseract y falla."""
    local = _entorno().get("tessdata") or str(TESSDATA)
    if os.path.isfile(os.path.join(local, "spa.traineddata")):
        os.environ["TESSDATA_PREFIX"] = local


def _interno_ocr(pdf: str, md: str) -> None:
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError:
        print("FALLA\tfaltan pypdfium2/pytesseract: ejecuta python sistema/biblioteca.py instalar")
        sys.exit(1)
    tesseract = ruta_tesseract()
    if not tesseract:
        print("FALLA\tTesseract no está instalado: ejecuta python sistema/biblioteca.py instalar")
        sys.exit(1)
    pytesseract.pytesseract.tesseract_cmd = tesseract
    usar_tessdata_local()
    if "spa" not in pytesseract.get_languages(config=""):
        print("FALLA\tTesseract no tiene el idioma español 'spa': ejecuta python sistema/biblioteca.py instalar")
        sys.exit(1)
    documento = pdfium.PdfDocument(pdf)
    partes = []
    for i in range(len(documento)):
        pagina = documento[i]
        try:                                                # PDF mixto: si la página trae texto, se conserva
            propio = (pagina.get_textpage().get_text_range() or "").strip()
        except Exception:  # noqa: BLE001
            propio = ""
        if len(propio) >= MIN_CARACTERES_POR_PAGINA:
            texto_pagina = propio
        else:
            imagen = pagina.render(scale=300 / 72).to_pil()   # 300 DPI
            texto_pagina = pytesseract.image_to_string(imagen, lang="spa").strip()
        partes.append(f"## Página {i + 1}\n\n{texto_pagina}\n")
        pagina.close()
    documento.close()
    _escribir(md, "\n".join(partes), "ocr")


# ---------------------------------------------------------------------------
# Markdown estructurado (herramientas/convertir.py): front-matter, encabezados, párrafos
# ---------------------------------------------------------------------------
def meta_markdown(doc: dict, registro: dict, metodo: str, fecha: str) -> dict:
    """Los datos del front-matter: del catálogo (identidad) y del manifiesto (archivo oficial, URL, reforma, huella)."""
    return {"id": doc["id"], "nombre": doc.get("nombre"), "entidad": doc.get("entidad"), "grupo": doc.get("grupo"),
            "estado": doc.get("estado"), "archivo": registro.get("archivo"), "fuente": registro.get("url"),
            "fecha_reforma": registro.get("fecha_reforma"), "sha256_origen": huella_origen(registro),
            "metodo": metodo, "convertido": fecha}


def estructurar_texto(crudo: str, meta: dict) -> tuple[str, dict, dict]:
    """(Markdown estructurado, informe, validación) de un texto crudo o ya estructurado. La validación dice si el
    resultado conserva todas las palabras del crudo (`ok`); quien llama decide si lo acepta."""
    texto, informe = estructurar(crudo, meta)
    return texto, informe, informe.pop("validacion")       # = validar(crudo, texto), sin recalcular


def motivo_rechazo(validacion: dict) -> str:
    """Por qué la validación no aceptó el Markdown estructurado, en una línea."""
    partes = []
    if validacion.get("total_perdidas"):
        partes.append(f"perdería {validacion['total_perdidas']} palabra(s) "
                      f"({', '.join(map(str, validacion.get('palabras_perdidas', [])[:5]))})")
    if validacion.get("total_nuevas"):
        partes.append(f"añadiría {validacion['total_nuevas']} palabra(s)")
    if validacion.get("numeros_perdidos") or validacion.get("numeros_nuevos"):
        partes.append(f"cambiaría números ({', '.join(map(str, (validacion.get('numeros_perdidos') or [])[:3]))})")
    if validacion.get("bigramas_distintos"):
        partes.append(f"cambiaría el orden ({validacion['bigramas_distintos']} pares de palabras)")
    if validacion.get("lineas_sospechosas"):
        s = validacion["lineas_sospechosas"][0]
        partes.append(f"quitaría {len(validacion['lineas_sospechosas'])} línea(s) que parecen contenido "
                      f"(p. ej. línea {s.get('linea')}: {s.get('motivo')})")
    return "; ".join(partes) or "la validación no lo aceptó"


def _estructurar_en_sitio(ruta: Path, meta: dict) -> dict:
    """Reemplaza el Markdown de `ruta` por su versión estructurada si la validación lo permite. Devuelve
    {formato, caracteres, articulos, aviso?}: con formato 1 (crudo) y un aviso cuando se descartó."""
    crudo = ruta.read_text(encoding="utf-8")
    texto, informe, validacion = estructurar_texto(crudo, meta)
    if not validacion["ok"]:
        return {"formato": 1, "caracteres": len(crudo), "articulos": 0,
                "aviso": "se guardó el texto crudo: " + motivo_rechazo(validacion)}
    with open(ruta, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto if texto.endswith("\n") else texto + "\n")
    return {"formato": VERSION, "caracteres": len(texto), "articulos": informe.get("articulos", 0)}


def _interno_estructurar(origen: str, destino: str, meta_json: str) -> None:
    """Subproceso de --reestructurar: lee el Markdown existente, lo estructura con los datos de `meta_json` y lo
    escribe en `destino` (carpeta temporal). Imprime OK<tab>caracteres<tab>articulos<tab>formato o FALLA<tab>motivo."""
    with open(meta_json, encoding="utf-8") as f:
        meta = json.load(f)
    shutil.copyfile(origen, destino)
    r = _estructurar_en_sitio(Path(destino), meta)
    if r.get("aviso"):
        print(f"FALLA\t{r['aviso']}")
        sys.exit(1)
    print(f"OK\t{r['caracteres']}\t{r['articulos']}\t{r['formato']}")


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
def _subproceso(modo: str, pdf, md, tiempo, url: str = ""):
    # registrado en comun: si biblioteca.py convertir se interrumpe, este subproceso también se detiene
    r = correr_hijo([sys.executable, str(ENTRADA), "convertir", modo, str(pdf), str(md), url], timeout=tiempo)
    salida = (r.stdout or "").strip().splitlines()
    if salida:
        return salida[-1]
    errores = (r.stderr or "").strip().splitlines()
    return "FALLA\t" + (errores[-1][:200] if errores else "el subproceso no produjo salida")


def convertir_uno(doc: dict, registro: dict) -> dict:
    """Convierte en un subproceso, estructura el texto (formato 2) y deja el Markdown en la carpeta temporal de
    esta ejecución ('temporal' en el resultado); quien llama lo coloca en su sitio dentro del candado."""
    pdf, md = ruta_pdf(doc), temporal() / f"{doc['id']}.md"
    url = registro.get("url") or ""
    try:
        estado = _subproceso("--interno-markitdown", pdf, md, TIEMPO_NORMAL, url)
    except subprocess.TimeoutExpired:
        try:
            estado = _subproceso("--interno-markitdown", pdf, md, TIEMPO_EXTENDIDO, url)
        except subprocess.TimeoutExpired:
            return {"id": doc["id"], "resultado": "FALLA", "detalle": "tiempo agotado incluso con reintento"}
    if estado.startswith("VACIO"):
        # PDF escaneado -> OCR (unos segundos por página)
        try:
            estado = _subproceso("--interno-ocr", pdf, md, 3 * 3600)
        except subprocess.TimeoutExpired:
            return {"id": doc["id"], "resultado": "FALLA", "detalle": "OCR agotó el tiempo"}
        if not estado.startswith("OK"):
            return {"id": doc["id"], "resultado": "FALLA", "detalle": "PDF escaneado y OCR no disponible: " + estado}
    if not estado.startswith("OK"):
        return {"id": doc["id"], "resultado": "FALLA", "detalle": estado}
    partes = estado.split("\t")
    metodo = partes[2] if len(partes) > 2 else "markitdown"
    res = {"id": doc["id"], "resultado": "OK", "metodo": metodo, "caracteres": int(partes[1]), "temporal": md}
    res.update(_estructurar_en_sitio(md, meta_markdown(doc, registro, metodo, hoy())))
    return res


def reestructurar_uno(doc: dict, registro: dict) -> dict:
    """Reaplica el formato vigente al Markdown ya guardado, en un subproceso, y deja el resultado en la carpeta
    temporal ('temporal'); quien llama lo coloca en su sitio dentro del candado."""
    md_info = registro.get("markdown") or {}
    origen, destino = ruta_md(doc), temporal() / f"{doc['id']}.md"
    meta_json = temporal() / f"{doc['id']}.meta.json"
    with open(meta_json, "w", encoding="utf-8") as f:
        # reaplicar el formato no cambia la fecha de conversión: así es idempotente cualquier día
        json.dump(meta_markdown(doc, registro, md_info.get("metodo", "markitdown"), md_info.get("fecha") or hoy()), f,
                  ensure_ascii=False)
    try:
        r = correr_hijo([sys.executable, str(ENTRADA), "convertir", "--interno-estructurar",
                         str(origen), str(destino), str(meta_json)], timeout=TIEMPO_ESTRUCTURAR)
    except subprocess.TimeoutExpired:
        return {"id": doc["id"], "resultado": "FALLA", "detalle": f"estructurar tardó más de {TIEMPO_ESTRUCTURAR} s"}
    lineas = (r.stdout or "").strip().splitlines()
    estado = lineas[-1] if lineas else "FALLA\t" + ((r.stderr or "").strip().splitlines() or ["sin salida"])[-1][:200]
    if not estado.startswith("OK"):
        return {"id": doc["id"], "resultado": "FALLA", "detalle": estado.split("\t", 1)[-1]}
    partes = estado.split("\t")
    return {"id": doc["id"], "resultado": "OK", "metodo": md_info.get("metodo", "markitdown"), "caracteres": int(partes[1]),
            "articulos": int(partes[2]), "formato": int(partes[3]), "temporal": destino}


def main():
    if len(sys.argv) in (4, 5) and sys.argv[1] == "--interno-markitdown":
        return _interno_markitdown(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) == 5 else "")
    if len(sys.argv) in (4, 5) and sys.argv[1] == "--interno-ocr":
        return _interno_ocr(sys.argv[2], sys.argv[3])
    if len(sys.argv) == 5 and sys.argv[1] == "--interno-estructurar":
        return _interno_estructurar(sys.argv[2], sys.argv[3], sys.argv[4])

    p = Analizador(description="Convertir PDF a Markdown (MarkItDown + OCR en español).",
                         formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_CONVERTIR)
    p.add_argument("--listar", action="store_true", help="solo mostrar cuántos y cuáles están pendientes")
    p.add_argument("--id", nargs="+", help="solo estos documentos")
    p.add_argument("--grupo", help="solo este grupo (ver: python sistema/biblioteca.py catalogo grupos)")
    p.add_argument("--entidad", help="solo esta entidad federativa")
    p.add_argument("--forzar", action="store_true", help="reconvertir aunque el Markdown esté al día")
    p.add_argument("--procesos", type=int, default=3, help="conversiones simultáneas (1-5)")
    p.add_argument("--reestructurar", action="store_true",
                   help="reaplicar el formato vigente del Markdown a los que tienen formato anterior (sin reconvertir)")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    args = p.parse_args()
    salida.iniciar(args.json, "convertir")
    procesos = max(1, min(MAX_PROCESOS, args.procesos))

    catalogo = cargar_catalogo()
    manifiesto = cargar_manifiesto()
    por_id = {d["id"]: d for d in catalogo["documentos"]}
    ids = args.id if args.id else list(manifiesto)

    trabajo, coinciden, formato_viejo = [], 0, []
    for doc_id in ids:
        doc = por_id.get(doc_id)
        registro = manifiesto.get(doc_id)
        if not doc or not registro:
            print(f"OMITIDO {doc_id}: no está descargado (primero hay que actualizarlo)")
            continue
        # mismos filtros que biblioteca.py catalogo listar: --grupo por "contiene"; --entidad federal = sin entidad
        if args.grupo and normalizar(args.grupo) not in normalizar(doc.get("grupo", "")):
            continue
        if args.entidad and normalizar(doc.get("entidad") or "federal") != normalizar(args.entidad):
            continue
        coinciden += 1
        problema = problema_de_estructura(doc)   # el Markdown solo se escribe dentro de la estructura
        if problema:
            print(f"OMITIDO {doc_id}: {problema}")
            continue
        if not ruta_pdf(doc).exists():
            print(f"OMITIDO {doc_id}: falta su documento en {relativa(ruta_pdf(doc))}")
            continue
        al_dia = markdown_al_dia(doc, registro)
        if al_dia and (registro.get("markdown") or {}).get("formato", 1) < VERSION:
            formato_viejo.append(doc)
        if args.reestructurar:
            if al_dia and (args.forzar or doc in formato_viejo):
                trabajo.append(doc)
            continue
        if al_dia and not args.forzar:
            continue
        trabajo.append(doc)

    if (args.grupo or args.entidad) and not coinciden:
        salir_con_error("Ningún documento descargado coincide con ese filtro (--grupo/--entidad). Revisa el "
                              "nombre con: python sistema/biblioteca.py catalogo grupos")
    def motivo_de(d: dict) -> str:
        if args.reestructurar:
            return "formato anterior"
        return "desactualizado" if manifiesto[d["id"]].get("markdown") else "sin Markdown"

    salida.poner(pendientes=[{"id": d["id"], "motivo": motivo_de(d)} for d in trabajo],
                 formato_anterior=[d["id"] for d in formato_viejo], formato_vigente=VERSION)
    nota_formato = (f"\nCon formato anterior del Markdown: {len(formato_viejo)} (al día en contenido; reaplica el formato "
                    f"{VERSION} con: python sistema/biblioteca.py convertir --reestructurar)" if formato_viejo else "")
    if not trabajo:
        if args.reestructurar:
            print("PENDIENTES: 0 — Nada que reestructurar: todos los Markdown tienen el formato vigente.")
        else:
            print("PENDIENTES: 0 — Nada que convertir: todos los Markdown están al día." + nota_formato)
        return
    if args.listar:
        if args.reestructurar:
            print(f"PENDIENTES: {len(trabajo)} Markdown con formato anterior (se reaplica el formato sin reconvertir)")
        else:
            nuevos = sum(1 for d in trabajo if not (manifiesto[d["id"]].get("markdown")))
            print(f"PENDIENTES: {len(trabajo)} documento(s) sin Markdown al día "
                  f"({nuevos} sin Markdown, {len(trabajo) - nuevos} con Markdown de una versión anterior)")
        for d in trabajo:
            print(f"  {d['id']}\t{motivo_de(d)}")
        if not args.reestructurar:
            print(nota_formato.lstrip("\n") if nota_formato else "")
        return
    verbo = "Reestructurando" if args.reestructurar else "Convirtiendo"
    print(f"{verbo} {len(trabajo)} documento(s) con {procesos} proceso(s) simultáneo(s)...")
    fallas = 0
    ejecutor = ThreadPoolExecutor(max_workers=procesos)
    tarea = reestructurar_uno if args.reestructurar else convertir_uno
    try:
        futuros = {ejecutor.submit(tarea, d, manifiesto[d["id"]]): d for d in trabajo}
        for n, futuro in enumerate(as_completed(futuros), 1):
            doc = futuros[futuro]
            res = futuro.result()
            if res["resultado"] == "OK":
                # se guarda al avanzar (una interrupción no pierde trabajo), dentro del candado y releyendo
                # catálogo y manifiesto: si otro agente movió, renombró o retiró el documento mientras se
                # convertía, el Markdown va a la ruta vigente o se descarta (nunca queda huérfano)
                with bloqueo():
                    actual = cargar_manifiesto()
                    doc_actual = next((d for d in cargar_catalogo()["documentos"] if d["id"] == doc["id"]), None)
                    if doc_actual is None or doc["id"] not in actual:
                        Path(res["temporal"]).unlink(missing_ok=True)
                        res = {"id": doc["id"], "resultado": "FALLA",
                               "detalle": "el documento se retiró del catálogo o del manifiesto durante la conversión"}
                    else:
                        reemplazar_archivo(res["temporal"], ruta_md(doc_actual))
                        actual[doc["id"]]["markdown"] = {
                            "archivo": ruta_relativa_md(doc_actual),
                            # a propósito la huella del manifiesto LEÍDO AL EMPEZAR: si el PDF se actualizó mientras
                            # tanto, este Markdown queda marcado como desactualizado y se vuelve a ofrecer
                            "sha256_origen": huella_origen(manifiesto[doc["id"]]),
                            "metodo": res["metodo"], "caracteres": res["caracteres"],
                            # --reestructurar no reconvierte: conserva la fecha de conversión (la del front-matter)
                            "fecha": (args.reestructurar and (manifiesto[doc["id"]].get("markdown") or {}).get("fecha"))
                            or hoy(),
                            "formato": res.get("formato", 1),
                        }
                        guardar_manifiesto(actual)
            if res["resultado"] == "OK":
                detalle = f"{res['metodo']}, {res['caracteres']:,} caracteres, formato {res.get('formato', 1)}"
                if res.get("articulos"):
                    detalle += f", {res['articulos']:,} artículos"
                print(f"[{n}/{len(trabajo)}] OK    {doc['id']} ({detalle})"
                      + (f"\n        AVISO: {res['aviso']}" if res.get("aviso") else ""))
            else:
                fallas += 1
                print(f"[{n}/{len(trabajo)}] FALLA {doc['id']}: {res['detalle']}")
            res.pop("temporal", None)
            bitacora({"herramienta": "convertir", "id": doc["id"], **({"accion": "reestructurar"} if args.reestructurar else {}),
                      **res})
            salida.agregar("resultados", res)
    except (KeyboardInterrupt, SystemExit):   # Ctrl+C, o Ctrl+Break/cierre de consola (SystemExit de comun)
        # se detienen las conversiones en curso (si no, seguirían solas en segundo plano)
        matar_hijos()                  # activa _DETENER: los hilos ya no lanzan conversiones
        ejecutor.shutdown(wait=False, cancel_futures=True)
        print("\nInterrumpido: lo que ya se había convertido quedó guardado; el resto sigue pendiente.")
        sys.exit(130)
    except BaseException:                    # cualquier otro error: no dejar hilos lanzando subprocesos
        matar_hijos()
        ejecutor.shutdown(wait=False, cancel_futures=True)
        raise
    ejecutor.shutdown(wait=True)
    hechos = "reestructurados" if args.reestructurar else "convertidos"
    print(f"\nTerminado: {len(trabajo) - fallas} {hechos}, {fallas} con falla.")
    salida.poner(convertidos=len(trabajo) - fallas, fallas=fallas)
    sys.exit(1 if fallas else 0)
