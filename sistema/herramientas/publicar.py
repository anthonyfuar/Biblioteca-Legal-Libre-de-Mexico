"""
publicar.py — PROCESO 5, PUBLICAR: SHA256SUMS, CAMBIOS.md, commit y push (solo con autorización) y la herramienta `publicar`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from herramientas.comun import (
    BASURA_SO,
    BITACORA,
    DOCUMENTOS,
    ENTRADA,
    RAIZ,
    SISTEMA,
    Analizador,
    ahora,
    bitacora,
    bloqueo,
    cargar_catalogo,
    correr_hijo,
    guardar_bytes,
    hoy,
    relativa,
    salida,
    salir_con_error,
    sha256_archivo,
    temporal,
)

# ====================================================================================================
# Publicación de la biblioteca (PROCESO 5, PUBLICAR): las funciones puras o de archivo que usan biblioteca.py publicar y
# biblioteca.py verificar. Solo biblioteca estándar y sin estado.
#
#   - archivos_de / sumas / escribir_sumas / comprobar_sumas / leer_sumas: biblioteca/SHA256SUMS en el formato de
#     `sha256sum` ("<sha256 en hexadecimal>  <ruta relativa a biblioteca/ con />", una línea por archivo, ordenadas
#     por ruta, saltos \n, sin BOM) con TODO lo que hay en biblioteca/ (PDF o Word oficiales, Markdown,
#     manifiesto y catálogo); nunca el propio SHA256SUMS, los archivos que deja el sistema operativo (.DS_Store,
#     Thumbs.db) ni los temporales que git no publica (*.tmp, *.pyc, __pycache__/). Quien descarga la biblioteca
#     comprueba que no le falta ni le cambió nada con `sha256sum -c SHA256SUMS` (en macOS, `shasum -a 256 -c`).
#   - ultima_publicacion / entradas / entrada_cambios / insertar_entrada / mensaje_commit: CAMBIOS.md lleva una
#     entrada por publicación, la más reciente primero: "## AAAA-MM-DD — N nuevos, M actualizados..." seguida del
#     marcador invisible `<!-- publicacion: AAAA-MM-DD HH:MM:SS -->`. La entrada siguiente se arma con los eventos
#     de bitacora.jsonl POSTERIORES a ese marcador y hasta el momento de la nueva publicación (descargas
#     NUEVO/ACTUALIZADO, cambios del catálogo, conversiones, compresiones y migraciones). ultima_preparacion lee lo
#     mismo de la bitácora (evento publicar/preparar), por si CAMBIOS.md se editó a mano.
#   - archivos_grandes: lo que pasa del límite por archivo de GitHub (bloquea 100 MiB; aquí 95 de margen).
# ====================================================================================================
ARCHIVO_SUMAS = "SHA256SUMS"
LIMITE_ARCHIVO_MIB = 95          # GitHub bloquea los archivos de 100 MiB o más
ENCABEZADO_CAMBIOS = "# Cambios de la Biblioteca Legal Libre de México"
EXPLICACION_CAMBIOS = ("Cada entrada es una publicación (`python sistema/biblioteca.py publicar`): lo que cambió en la "
                       "biblioteca desde la publicación anterior, según `bitacora.jsonl`. La más reciente va primero.")

# Carpetas que nunca van al repositorio ni se recorren: las de git, el entorno y las cachés
_NO_RECORRER = {".git", ".venv", "trabajo", "__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache"}
_NO_PUBLICADOS = (".tmp", ".pyc")          # .gitignore los excluye: no se listan en SHA256SUMS
# Encabezado de entrada. Tolera lo que una persona puede cambiar a mano: espacios de más, guion largo, corto o normal,
# resumen ausente o espacio final. Sin esa tolerancia, una entrada "irreconocible" haría volcar toda la bitácora otra vez.
_ENTRADA = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\b(?:\s*[—–-]\s*(.*?))?\s*$")
_MARCADOR = re.compile(r"<!--\s*publicacion:\s*(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?)\s*-->")
_LINEA_SUMA = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")
_FECHA_DMA = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_LARGO_NOTA = 160


# ---------------------------------------------------------------------------
# SHA256SUMS
# ---------------------------------------------------------------------------
def archivos_de(documentos: Path) -> list[tuple[str, Path]]:
    """[(ruta relativa con /, ruta)] de cada archivo de biblioteca/, ordenados por ruta relativa.
    Sin SHA256SUMS (el de la raíz de biblioteca/), sin basura del sistema, sin enlaces simbólicos y sin lo que git
    no publica (*.tmp, *.pyc, __pycache__/: .gitignore los excluye y el clon fallaría `sha256sum -c`)."""
    encontrados: list[tuple[str, Path]] = []
    for carpeta, subcarpetas, archivos in os.walk(documentos):
        subcarpetas[:] = sorted(s for s in subcarpetas if s != "__pycache__")
        for nombre in archivos:
            if nombre in BASURA_SO or nombre.lower().endswith(_NO_PUBLICADOS):
                continue
            if nombre == ARCHIVO_SUMAS and Path(carpeta) == documentos:
                continue
            ruta = Path(carpeta) / nombre
            if ruta.is_symlink() or not ruta.is_file():
                continue
            encontrados.append((ruta.relative_to(documentos).as_posix(), ruta))
    return sorted(encontrados)


def sumas(documentos: Path) -> list[tuple[str, str]]:
    """[(sha256, ruta relativa a biblioteca/)] de todos los archivos de la biblioteca, ordenados por ruta."""
    return [(sha256_archivo(ruta), relativa) for relativa, ruta in archivos_de(documentos)]


def texto_sumas(lista: list[tuple[str, str]]) -> str:
    """El contenido de SHA256SUMS: '<sha256>  <ruta>' por línea, con \\n, ordenado por ruta."""
    return "".join(f"{suma}  {ruta}\n" for suma, ruta in sorted(lista, key=lambda par: par[1]))


def escribir_sumas(documentos: Path, calculadas: list[tuple[str, str]] | None = None) -> Path:
    """Escribe (atómicamente, sin BOM) biblioteca/SHA256SUMS y devuelve su ruta. `calculadas` evita volver a
    hashear si ya se llamó a sumas()."""
    if not documentos.is_dir():
        raise FileNotFoundError(f"no existe la carpeta de la biblioteca: {documentos}")
    lista = sumas(documentos) if calculadas is None else calculadas
    destino = documentos / ARCHIVO_SUMAS
    guardar_bytes(destino, texto_sumas(lista).encode("utf-8"))
    return destino


def leer_sumas(archivo: Path) -> tuple[dict[str, str], int]:
    """({ruta: sha256 en minúsculas}, líneas que no tienen el formato) de un SHA256SUMS. Acepta el separador
    de texto ('  ') y el binario (' *') de sha256sum."""
    listadas: dict[str, str] = {}
    invalidas = 0
    for linea in archivo.read_text(encoding="utf-8-sig").splitlines():
        if not linea.strip():
            continue
        m = _LINEA_SUMA.match(linea)
        if m:
            listadas[m.group(2)] = m.group(1).lower()
        else:
            invalidas += 1
    return listadas, invalidas


def comprobar_sumas(documentos: Path, calculadas: list[tuple[str, str]] | None = None) -> dict:
    """Compara biblioteca/SHA256SUMS con lo que hay en disco. Devuelve:
      existe: hay SHA256SUMS · ok: está al día · faltantes: archivos en disco que no están listados ·
      sobrantes: listados que ya no existen · distintas: listados con otra huella · lineas_invalidas ·
      listadas / en_disco: cuántos hay en cada lado.
    Si no existe, ok=False con las listas vacías (no hay con qué comparar)."""
    archivo = documentos / ARCHIVO_SUMAS
    resultado: dict = {"archivo": archivo.as_posix(), "existe": archivo.is_file(), "ok": False, "faltantes": [],
                       "sobrantes": [], "distintas": [], "lineas_invalidas": 0, "listadas": 0, "en_disco": 0}
    if not resultado["existe"]:
        return resultado
    listadas, invalidas = leer_sumas(archivo)
    en_disco = {ruta: suma for suma, ruta in (sumas(documentos) if calculadas is None else calculadas)}
    resultado.update({
        "faltantes": sorted(set(en_disco) - set(listadas)),
        "sobrantes": sorted(set(listadas) - set(en_disco)),
        "distintas": sorted(r for r, suma in listadas.items() if r in en_disco and en_disco[r] != suma),
        "lineas_invalidas": invalidas, "listadas": len(listadas), "en_disco": len(en_disco),
    })
    resultado["ok"] = not (resultado["faltantes"] or resultado["sobrantes"] or resultado["distintas"] or invalidas)
    return resultado


# ---------------------------------------------------------------------------
# CAMBIOS.md
# ---------------------------------------------------------------------------
def entradas_texto(texto: str) -> list[tuple[str, str]]:
    """[(fecha, texto)] de cada entrada de un CAMBIOS.md dado como texto, en el orden del archivo. La fecha es la
    del marcador `<!-- publicacion: ... -->` (con hora) o, si la entrada no lo tiene, la del encabezado
    '## AAAA-MM-DD'."""
    resultado: list[tuple[str, list[str]]] = []
    for linea in texto.splitlines():
        m = _ENTRADA.match(linea)
        if m:
            resultado.append((m.group(1), [linea]))
            continue
        if not resultado:
            continue              # el encabezado del archivo y su explicación
        fecha, lineas = resultado[-1]
        marcador = _MARCADOR.search(linea)
        if marcador and marcador.group(1) >= fecha:
            resultado[-1] = (marcador.group(1), lineas)
        lineas.append(linea)
    return [(fecha, "\n".join(lineas).rstrip("\n") + "\n") for fecha, lineas in resultado]


def entradas(cambios: Path) -> list[tuple[str, str]]:
    """[(fecha, texto)] de cada entrada de CAMBIOS.md (ver entradas_texto); [] si no existe."""
    return entradas_texto(cambios.read_text(encoding="utf-8")) if cambios.is_file() else []


def ultima_publicacion(cambios: Path) -> str | None:
    """Fecha ('AAAA-MM-DD HH:MM:SS', o 'AAAA-MM-DD' si la entrada no tiene marcador) de la última publicación
    registrada en CAMBIOS.md; None si no hay archivo o no tiene entradas."""
    fechas = [fecha for fecha, _ in entradas(cambios)]
    return max(fechas) if fechas else None


def ultima_entrada(cambios: Path) -> tuple[str, str] | None:
    """(fecha, texto) de la entrada más reciente de CAMBIOS.md, o None."""
    lista = entradas(cambios)
    return max(lista, key=lambda par: par[0]) if lista else None


def cambios_sin_entradas_reconocibles(cambios: Path) -> bool:
    """True si CAMBIOS.md existe y tiene texto más allá de su encabezado pero ninguna entrada '## AAAA-MM-DD':
    alguien lo editó a mano. biblioteca.py publicar lo avisa (la ventana de la bitácora se calcula entonces con
    ultima_preparacion)."""
    if not cambios.is_file() or entradas(cambios):
        return False
    lineas = [linea.strip() for linea in cambios.read_text(encoding="utf-8").splitlines()]
    return any(linea and linea not in (ENCABEZADO_CAMBIOS, EXPLICACION_CAMBIOS) for linea in lineas)


def leer_bitacora(bitacora: Path, desde: str | None = None, hasta: str | None = None) -> list[dict]:
    """Eventos de bitacora.jsonl con fecha en (desde, hasta]: posteriores a `desde` y no posteriores a
    `hasta` (None = sin ese límite). Las líneas que no son JSON se omiten. Las fechas se comparan como texto
    ('AAAA-MM-DD HH:MM:SS'); un `desde` sin hora incluye ese día completo y un `hasta` sin hora también
    (se le añade 23:59:59)."""
    eventos: list[dict] = []
    if not bitacora.is_file():
        return eventos
    if hasta and len(hasta) == 10:
        hasta += " 23:59:59"
    with open(bitacora, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                evento = json.loads(linea)
            except ValueError:
                continue
            if not isinstance(evento, dict):
                continue
            fecha = str(evento.get("fecha", ""))
            if (desde and fecha <= desde) or (hasta and fecha > hasta):
                continue
            eventos.append(evento)
    return eventos


def ultima_preparacion(bitacora: Path) -> str | None:
    """Momento ('AAAA-MM-DD HH:MM:SS') de la última preparación registrada en la bitácora (evento publicar/preparar,
    campo `publicacion`, el mismo que lleva el marcador de CAMBIOS.md); None si no hay ninguna. biblioteca.py publicar toma el
    MÁS RECIENTE entre esto y ultima_publicacion(CAMBIOS.md): CAMBIOS.md lo puede editar una persona (si borra el
    marcador o una entrada, la bitácora entera volvería a volcarse), mientras que la bitácora es el registro propio
    de la herramienta."""
    momentos = [str(e.get("publicacion")) for e in leer_bitacora(bitacora)
                if e.get("herramienta") == "publicar" and e.get("accion") == "preparar" and e.get("publicacion")]
    return max(momentos) if momentos else None


def _n(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _recortar(texto: object, maximo: int = _LARGO_NOTA) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def _quien(doc_id: str, nombres: dict[str, str] | None, nombre_evento: object = None) -> str:
    """'Nombre oficial (`id`)' si se conoce el nombre; si no, '`id`'."""
    nombre = (nombres or {}).get(doc_id) or (str(nombre_evento) if nombre_evento else "")
    return f"{nombre} (`{doc_id}`)" if nombre else f"`{doc_id}`"


def _fecha_iso(fecha: object) -> str | None:
    """Las bitácoras viejas guardaban la reforma como DD-MM-AAAA; hoy es ISO. Para mostrar, siempre ISO."""
    if not fecha:
        return None
    m = _FECHA_DMA.match(str(fecha))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else str(fecha)


_ETIQUETAS = (("abrogados", "abrogado", "abrogados"),
              ("agregados", "agregado al catálogo", "agregados al catálogo"),
              ("eliminados", "eliminado del catálogo", "eliminados del catálogo"),
              ("renombrados", "renombrado", "renombrados"),
              ("movidos", "movido de carpeta", "movidos de carpeta"),
              ("otros", "otro cambio del catálogo", "otros cambios del catálogo"),
              ("convertidos", "convertido a Markdown", "convertidos a Markdown"),
              ("reestructurados", "Markdown con formato nuevo", "Markdown con formato nuevo"),
              ("comprimidos", "PDF comprimido", "PDF comprimidos"),
              ("migraciones", "tarea de mantenimiento", "tareas de mantenimiento"))


def entrada_cambios(bitacora: Path, desde: str | None, hoy: str,
                    nombres: dict[str, str] | None = None) -> tuple[str, dict]:
    """La entrada de CAMBIOS.md (texto Markdown) y sus conteos con lo que registró la bitácora en (desde, hoy]:
    después de `desde` (None = desde el inicio) y hasta `hoy`, el momento de la publicación ('AAAA-MM-DD' o con
    hora): el encabezado lleva el día y el marcador el valor completo, y es el `desde` de la entrada siguiente,
    así ningún evento entra en dos entradas ni se pierde. `nombres` (id -> nombre oficial) hace legibles las
    listas. Sin cambios, el texto es '' (los conteos quedan en 0)."""
    descargas: dict[str, dict] = {}       # id -> {"nuevo": bool, "reforma": str | None}
    abrogados: list[dict] = []
    otros: list[dict] = []
    agregados: list[dict] = []
    eliminados: list[dict] = []
    renombrados: list[dict] = []
    movidos: list[dict] = []
    comprimidos: list[dict] = []
    migraciones: list[dict] = []
    convertidos: Counter[str] = Counter()
    reestructurados: dict[str, int] = {}          # id -> formato (biblioteca.py convertir --reestructurar no reconvierte nada)
    eventos = leer_bitacora(bitacora, desde, hasta=hoy)
    # Una migración de ids (evento "migracion" con "mapa": id viejo -> id nuevo) deja eventos anteriores con el id
    # viejo: se muestran con el id actual, que es el que está en el catálogo.
    mapa_ids: dict[str, str] = {}
    for e in eventos:
        if e.get("herramienta") == "migracion" and isinstance(e.get("mapa"), dict):
            mapa_ids.update({str(k): str(v) for k, v in e["mapa"].items()})
    for e in eventos:
        herramienta, accion, resultado = e.get("herramienta"), e.get("accion"), e.get("resultado")
        doc_id = str(e.get("id") or "")
        doc_id = mapa_ids.get(doc_id, doc_id)
        if herramienta == "descargar" and resultado in ("NUEVO", "ACTUALIZADO"):
            d = descargas.setdefault(doc_id, {"nuevo": False, "reforma": None})
            d["nuevo"] = d["nuevo"] or resultado == "NUEVO"
            d["reforma"] = _fecha_iso(e.get("fecha_reforma")) or d["reforma"]
        elif herramienta == "catalogo":
            if accion == "marcar":
                de, a = e.get("de"), e.get("a")
                if a == "abrogado" and de != "abrogado":
                    abrogados.append({**e, "id": doc_id})
                elif de != a:
                    otros.append({**e, "id": doc_id})
            elif accion == "agregar":
                agregados.append({**e, "id": doc_id})
            elif accion == "eliminar":
                eliminados.append({**e, "id": doc_id})
            elif accion == "renombrar":
                renombrados.append({**e, "id": doc_id})
            elif accion == "mover":
                movidos.append({**e, "id": doc_id})
            elif accion == "retirar-pdf":
                otros.append({**e, "id": doc_id})
        elif herramienta == "convertir" and resultado == "OK" and accion == "reestructurar":
            reestructurados[doc_id] = int(e.get("formato") or 0)
        elif herramienta == "convertir" and resultado == "OK":
            convertidos[str(e.get("metodo") or "sin método")] += 1
        elif herramienta == "comprimir" and resultado == "OK":
            comprimidos.append({**e, "id": doc_id})
        elif herramienta == "migracion":
            migraciones.append(e)
    nuevos = sorted(i for i, d in descargas.items() if d["nuevo"])
    actualizados = sorted(i for i, d in descargas.items() if not d["nuevo"])
    conteos = {"nuevos": len(nuevos), "actualizados": len(actualizados), "abrogados": len(abrogados),
               "agregados": len(agregados), "eliminados": len(eliminados), "renombrados": len(renombrados),
               "movidos": len(movidos), "otros": len(otros), "convertidos": sum(convertidos.values()),
               "reestructurados": len(reestructurados),
               "comprimidos": len(comprimidos), "migraciones": len(migraciones)}
    conteos["total"] = sum(conteos.values())
    if conteos["total"] == 0:
        return "", conteos

    def reforma(doc_id: str) -> str:
        fecha = descargas[doc_id]["reforma"]
        return f" — reforma {fecha}" if fecha else ""

    def nota(e: dict) -> str:
        texto = _recortar(e.get("nota"))
        return f" — {texto}" if texto else ""

    secciones: list[tuple[str, list[str]]] = []
    if nuevos:
        secciones.append((f"Documentos nuevos ({len(nuevos)})",
                          [f"- {_quien(i, nombres)}{reforma(i)}" for i in nuevos]))
    if actualizados:
        secciones.append((f"Documentos actualizados ({len(actualizados)})",
                          [f"- {_quien(i, nombres)}{reforma(i)}" for i in actualizados]))
    if abrogados:
        secciones.append((f"Abrogados ({len(abrogados)})",
                          [f"- {_quien(str(e.get('id')), nombres)}{nota(e)}" for e in abrogados]))
    if agregados:
        secciones.append((f"Agregados al catálogo ({len(agregados)})",
                          [f"- {_quien(str(e.get('id')), nombres, e.get('nombre'))}{nota(e)}" for e in agregados]))
    if eliminados:
        secciones.append((f"Eliminados del catálogo ({len(eliminados)})",
                          [f"- {_quien(str(e.get('id')), None, e.get('nombre'))}{nota(e)}" for e in eliminados]))
    if renombrados:
        secciones.append((f"Renombrados ({len(renombrados)})",
                          [f"- `{e.get('id')}`: «{_recortar(e.get('de'))}» → «{_recortar(e.get('a'))}»{nota(e)}"
                           for e in renombrados]))
    if movidos:
        secciones.append((f"Movidos de carpeta ({len(movidos)})",
                          [f"- `{e.get('id')}`: {e.get('de')} → {e.get('a')}{nota(e)}" for e in movidos]))
    if otros:
        lineas_otros = []
        for e in otros:
            if e.get("accion") == "retirar-pdf":
                lineas_otros.append(f"- `{e.get('id')}`: PDF retirado de la biblioteca{nota(e)}")
            else:
                lineas_otros.append(f"- `{e.get('id')}`: {e.get('de')} → {e.get('a')}{nota(e)}")
        secciones.append((f"Otros cambios del catálogo ({len(otros)})", lineas_otros))
    if convertidos:
        secciones.append((f"Convertidos a Markdown ({sum(convertidos.values())})",
                          [f"- {metodo}: {k}" for metodo, k in sorted(convertidos.items())]))
    if reestructurados:
        por_formato = Counter(reestructurados.values())
        secciones.append((f"Markdown con formato nuevo ({len(reestructurados)})",
                          [f"- formato {f}: {k} documento(s), sin reconvertir el original"
                           for f, k in sorted(por_formato.items())]))
    if comprimidos:
        lineas_comp = []
        for e in comprimidos:
            mb = e.get("mb") if isinstance(e.get("mb"), list) and len(e.get("mb") or []) == 2 else None
            peso = f": {mb[0]} → {mb[1]} MB" if mb else ""
            lineas_comp.append(f"- `{e.get('id')}`{peso} (nivel {e.get('nivel')})")
        secciones.append((f"PDF comprimidos ({len(comprimidos)})", lineas_comp))
    if migraciones:
        secciones.append((f"Mantenimiento ({len(migraciones)})",
                          [f"- {e.get('accion') or 'migración'}: {_recortar(e.get('detalle'), 300)}" for e in migraciones]))

    partes = [_n(conteos["nuevos"], "nuevo", "nuevos"), _n(conteos["actualizados"], "actualizado", "actualizados")]
    partes += [_n(conteos[clave], singular, plural) for clave, singular, plural in _ETIQUETAS if conteos[clave]]
    origen = f"desde la publicación anterior ({desde})" if desde else "desde el inicio de la bitácora (primera publicación)"
    lineas = [f"## {hoy[:10]} — {', '.join(partes)}", f"<!-- publicacion: {hoy} -->", "",
              f"Cambios registrados en la bitácora {origen} hasta {hoy}.", ""]
    for titulo, elementos in secciones:
        lineas += [f"### {titulo}", *elementos, ""]
    return "\n".join(lineas).rstrip("\n") + "\n", conteos


def insertar_entrada(cambios: Path, texto: str) -> None:
    """Pone `texto` como primera entrada de CAMBIOS.md (lo crea con su encabezado si no existe). Va antes de la
    primera entrada reconocible; si no hay ninguna, justo después de la explicación del encabezado (o al final)."""
    if cambios.is_file():
        lineas = cambios.read_text(encoding="utf-8").splitlines()
    else:
        lineas = [ENCABEZADO_CAMBIOS, "", EXPLICACION_CAMBIOS]
    posicion = next((i for i, linea in enumerate(lineas) if _ENTRADA.match(linea)), None)
    if posicion is None:
        tras_explicacion = next((i + 1 for i, linea in enumerate(lineas) if linea.strip() == EXPLICACION_CAMBIOS), None)
        posicion = tras_explicacion if tras_explicacion is not None else len(lineas)
    antes, despues = lineas[:posicion], lineas[posicion:]
    while antes and not antes[-1].strip():
        antes.pop()
    while despues and not despues[0].strip():
        despues.pop(0)
    nuevo = [*antes, "", texto.rstrip("\n"), "", *despues]
    guardar_bytes(cambios, ("\n".join(nuevo).rstrip("\n") + "\n").encode("utf-8"))


def _conteos_del_encabezado(linea: str) -> tuple[str, int, int] | None:
    """(fecha, nuevos, actualizados) de un encabezado '## AAAA-MM-DD — N nuevos, M actualizados...'; None si no lo es."""
    m = _ENTRADA.match(linea)
    if not m:
        return None
    resumen = m.group(2) or ""
    nuevos = re.search(r"(\d+) nuevos?\b", resumen)
    actualizados = re.search(r"(\d+) actualizados?\b", resumen)
    if not (nuevos and actualizados):
        return None
    return m.group(1), int(nuevos.group(1)), int(actualizados.group(1))


def _asunto(fecha: str, nuevos: int, actualizados: int) -> str:
    return f"Biblioteca {fecha}: {_n(nuevos, 'nuevo', 'nuevos')}, {_n(actualizados, 'actualizado', 'actualizados')}"


def mensaje_commit(texto_entrada: str, maximo_por_lista: int = 40) -> tuple[str, str]:
    """(asunto, cuerpo) del commit a partir de una entrada de CAMBIOS.md. Asunto de 72 caracteres o menos:
    'Biblioteca AAAA-MM-DD: N nuevos, M actualizados'. Cuerpo: las listas de la entrada, cada una recortada a
    `maximo_por_lista` elementos (el resto queda en CAMBIOS.md). Sin la línea de coautoría: la añade biblioteca.py publicar."""
    lineas = texto_entrada.splitlines()
    m = _ENTRADA.match(lineas[0]) if lineas else None
    conteos = _conteos_del_encabezado(lineas[0]) if lineas else None
    if conteos:
        asunto = _asunto(*conteos)
    elif m:
        asunto = _recortar(f"Biblioteca {m.group(1)}: {m.group(2) or ''}".rstrip(": "), 72)
    else:
        asunto = "Publicación de la biblioteca"
    cuerpo: list[str] = []
    en_lista, omitidos = 0, 0
    for linea in lineas[1:]:
        if _MARCADOR.search(linea):
            continue
        if linea.startswith("### "):
            if omitidos:
                cuerpo.append(f"  … y {omitidos} más (ver CAMBIOS.md)")
            en_lista, omitidos = 0, 0
            cuerpo.append(linea[4:] + ":")
        elif linea.startswith("- "):
            en_lista += 1
            if en_lista <= maximo_por_lista:
                cuerpo.append(linea)
            else:
                omitidos += 1
        elif linea.strip() or (cuerpo and cuerpo[-1].strip()):
            cuerpo.append(linea)
    if omitidos:
        cuerpo.append(f"  … y {omitidos} más (ver CAMBIOS.md)")
    return asunto, "\n".join(cuerpo).strip()


def mensaje_commit_varias(pendientes: list[str], maximo_por_lista: int = 40) -> tuple[str, str]:
    """(asunto, cuerpo) para un commit que confirma VARIAS entradas de CAMBIOS.md a la vez (se preparó más de una
    vez sin hacer commit). Con una sola, lo mismo que mensaje_commit. Con varias: el asunto suma los nuevos y
    actualizados de todas, con la fecha de la más reciente, y el cuerpo concatena el de cada una (la más reciente
    primero), encabezado por su fecha y resumen."""
    if not pendientes:
        return "Publicación de la biblioteca", ""
    if len(pendientes) == 1:
        return mensaje_commit(pendientes[0], maximo_por_lista)
    ordenadas = sorted(pendientes, key=lambda t: (t.splitlines() or [""])[0], reverse=True)
    nuevos = actualizados = 0
    fecha = ""
    partes: list[str] = []
    for texto in ordenadas:
        primera = (texto.splitlines() or [""])[0]
        conteos = _conteos_del_encabezado(primera)
        if conteos:
            fecha = fecha or conteos[0]
            nuevos += conteos[1]
            actualizados += conteos[2]
        m = _ENTRADA.match(primera)
        titulo = f"{m.group(1)} — {m.group(2) or ''}".rstrip(" —") if m else "Entrada"
        _, cuerpo = mensaje_commit(texto, maximo_por_lista)
        partes.append(f"[{titulo}]\n{cuerpo}".rstrip())
    asunto = _asunto(fecha, nuevos, actualizados) if fecha else "Publicación de la biblioteca"
    return asunto, "\n\n".join(partes)


# ---------------------------------------------------------------------------
# Archivos que GitHub rechazaría
# ---------------------------------------------------------------------------
def archivos_grandes(raiz: Path, limite_mib: int = LIMITE_ARCHIVO_MIB) -> list[Path]:
    """Archivos del proyecto de más de `limite_mib` MiB (GitHub bloquea 100 MiB por archivo). No entra en .git/,
    trabajo/ ni las cachés: nada de eso se publica."""
    limite = limite_mib * 1024 * 1024
    grandes: list[Path] = []
    for carpeta, subcarpetas, archivos in os.walk(raiz):
        subcarpetas[:] = sorted(s for s in subcarpetas if s not in _NO_RECORRER)
        for nombre in archivos:
            ruta = Path(carpeta) / nombre
            try:
                if not ruta.is_symlink() and ruta.stat().st_size > limite:
                    grandes.append(ruta)
            except OSError:
                continue
    return sorted(grandes)


# ====================================================================================================
# Herramienta: publicar
# ====================================================================================================
AYUDA_PUBLICAR = """
biblioteca.py publicar — Deja la biblioteca lista para publicarla en GitHub y la sube SOLO cuando el usuario lo pide.

ES UN PROCESO APARTE (PROCESO 5, PUBLICAR). Un solo repositorio con todo (herramientas + biblioteca/), rama
`main`, sin Git LFS. Cuatro pasos, uno por opción; cada uno comprueba lo que dejó el anterior:

  --revisar    NO escribe nada. Muestra qué haría: el resultado de biblioteca.py verificar --final, los archivos de más
               de 95 MiB (GitHub bloquea 100 MiB por archivo), si biblioteca/SHA256SUMS está al día, lo que
               entraría en CAMBIOS.md desde la última publicación, el tamaño del repositorio y si hay remoto.
               Sale 0 solo cuando la biblioteca está lista para --commit (integridad OK, nada grande, SHA256SUMS
               al día y ninguna entrada pendiente); si falta --preparar, sale 1 y lo dice.
  --preparar   Exige INTEGRIDAD: OK del verificador (si no, sale con 1 y lista los errores). Genera
               biblioteca/SHA256SUMS (formato de sha256sum: "<sha256>  <ruta>" por cada archivo de biblioteca/,
               ordenadas por ruta) y añade a CAMBIOS.md la entrada de esta publicación: fecha, conteos y listas
               de nuevos, actualizados, abrogados, agregados, eliminados, renombrados, convertidos y
               comprimidos desde la entrada anterior, tomados de bitacora.jsonl (la ventana va de la
               última preparación —la más reciente entre el marcador de CAMBIOS.md y el evento de la bitácora—
               hasta el momento de esta; la herramienta espera a que el reloj pase ese segundo antes de leer la
               bitácora, así ningún evento entra en dos entradas ni se pierde). Si no hubo cambios desde la
               última entrada, lo dice y no añade nada (SHA256SUMS se pone al día de todos modos).
  --commit     Exige --preparar hecho (SHA256SUMS al día y verificador sin errores) y que haya cambios en git
               aparte de la bitácora. Hace `git add -A` y `git commit` con el mensaje generado de las entradas
               de CAMBIOS.md que aún no están en HEAD (asunto "Biblioteca AAAA-MM-DD: N nuevos, M actualizados"
               y las listas en el cuerpo). Nunca hace commit si git status muestra archivos de trabajo/ o .venv/
               (deben estar ignorados en .gitignore), ni si
               SHA256SUMS lista un archivo que .gitignore excluye. Desde la comprobación de SHA256SUMS hasta que
               termina el commit tiene tomado el candado: ninguna otra herramienta reemplaza un
               documento entre ambos pasos.
  --push       SOLO SI EL USUARIO LO PIDIÓ EXPLÍCITAMENTE EN LA CONVERSACIÓN. Sube `main` a `origin`
               (git push -u origin main) y comprueba en el remoto que main quedó igual. Sin remoto, explica cómo
               crear el repositorio VACÍO en GitHub y repetir con --remoto URL (que añade `origin`, o lo reemplaza
               si la URL cambió: el remoto nuevo empieza de cero, como primera subida). GitHub rechaza un push de
               2 GB o más: si lo que falta subir pasa de 1 GiB, lo sube POR BLOQUES de commits de hasta 1 GiB, del
               más viejo al más nuevo (un commit que solo pase de 1 GiB va en su propio bloque). Lo que ya subió se
               queda en GitHub: si un bloque falla, repetir --push sigue desde ese bloque. Un solo commit de más de
               1.8 GiB no se puede subir: explica cómo rehacerlo en partes y NO lo hace solo. Cada push tiene un
               tope de tiempo proporcional a lo que sube (una hora como mínimo; 150 KiB/s, es decir 1.2 Mbps
               sostenidos): si se vence, se cancela, queda en la bitácora y ese bloque no se reanuda (git no
               reanuda subidas).

USO:
  python sistema/biblioteca.py publicar --revisar
  python sistema/biblioteca.py publicar --preparar
  python sistema/biblioteca.py publicar --commit
  python sistema/biblioteca.py publicar --push [--remoto https://github.com/<usuario>/<repositorio>.git]
  --json    salida en JSON por stdout (el texto va a stderr), en todos los modos

Cada paso queda en la bitácora (herramienta "publicar", acción preparar/commit/push). git se ejecuta desde la
carpeta del proyecto y nunca con shell; git no pide credenciales en la terminal (GIT_TERMINAL_PROMPT=0): la
autenticación la configura el usuario (gh auth login, gestor de credenciales o llaves SSH). Una URL de --remoto
con usuario:token se rechaza, y si git repite una URL así en un error, se tapa antes de mostrarla o registrarla.

CÓDIGOS DE SALIDA: 0 = hecho (con --revisar: lista para --commit) · 1 = error, o no está lista (el texto dice
qué falta) · 64 = uso incorrecto
"""


CAMBIOS = SISTEMA / "CAMBIOS.md"
RAMA = "main"
REMOTO = "origin"
LIMITE_PUSH_BYTES = int(1.8 * 1024 ** 3)        # GitHub rechaza un push de 2 GB o más: ningún push pasa de aquí
BLOQUE_PUSH_BYTES = 1024 ** 3                   # lo que pasa de esto sube en varios push (menos que repetir si la red falla)
VELOCIDAD_MINIMA = 150 * 1024                   # bytes/s (1.2 Mbps) que el push debe sostener para no vencer su tope
TOPE_PUSH_MINIMO = 3600.0                       # segundos: el tope nunca baja de una hora
COAUTORIA = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
# Lo que NUNCA debe aparecer en git status (si aparece, .gitignore está roto) y la única excepción
_NUNCA_EN_GIT = ("trabajo/", ".venv/")
_URL_REMOTO = re.compile(r"^(https://|ssh://|git@)\S+$")
# Credenciales dentro de una URL: `esquema://usuario:token@host` (o `esquema://token@host`) y la forma scp
# `usuario@host:ruta` (usuario o token: letras, dígitos, `_`, `.`, `+`, `-`). `git@` es el usuario convencional de
# SSH, no un secreto: se conserva.
_CRED_URL = re.compile(r"(://)([^/@\s]+)@")
_CRED_SCP = re.compile(r"(?<![\w./:@+-])([\w.+-]+)@(?=[\w.-]+:)")

CREAR_REPOSITORIO = f"""No hay remoto '{REMOTO}'. Para publicar por primera vez:
  1. El USUARIO crea un repositorio VACÍO en GitHub (https://github.com/new): sin README, sin .gitignore y sin
     licencia (el proyecto ya los tiene). Público, para que la biblioteca sea libre.
  2. Se vuelve a ejecutar con la URL que da GitHub:
       python sistema/biblioteca.py publicar --push --remoto https://github.com/<usuario>/<repositorio>.git
     (o la URL SSH, git@github.com:<usuario>/<repositorio>.git, si el usuario usa llaves SSH)
  3. La autenticación la hace el usuario, nunca el agente: `gh auth login` (GitHub CLI), un token en el gestor
     de credenciales de git o sus llaves SSH. La URL nunca lleva usuario:token."""

ESTRATEGIA_BLOQUES = """ESTRATEGIA POR BLOQUES (no se hace sola: el usuario decide):
  GitHub rechaza un push de 2 GB o más. publicar --push ya sube los commits por bloques, pero un bloque nunca parte
  un commit: si UN commit pesa más de 1.8 GiB hay que rehacerlo en partes ANTES de subir, p. ej. un commit con las
  herramientas y lo federal (PDF/Federal y Markdown/Federal) y luego uno por cada bloque de estados (menos de 1 GiB
  cada uno), y después
  repetir publicar --push. Pídele al agente que lo planee contigo; nunca uses Git LFS en este proyecto."""


# ---------------------------------------------------------------------------
# git (siempre con la raíz como parámetro: las pruebas usan un repositorio temporal)
# ---------------------------------------------------------------------------
def ocultar_credenciales(texto: str) -> str:
    """Tapa las credenciales de cualquier URL que haya en `texto` (una URL sola o un mensaje de git que la repite):
    'https://usuario:token@github.com/x' -> 'https://github.com/x', 'token@github.com:u/r.git' -> 'github.com:u/r.git';
    `git@` (el usuario de SSH) se conserva. Nunca se muestra ni se registra una URL sin pasar por aquí."""
    texto = _CRED_URL.sub(lambda m: m.group(1) + ("git@" if m.group(2) == "git" else ""), texto)
    return _CRED_SCP.sub(lambda m: "git@" if m.group(1) == "git" else "", texto)


def lleva_credenciales(url: str) -> bool:
    return ocultar_credenciales(url) != url


def _git(raiz: Path, *args: str, timeout: float = 60, fallar: bool = True,
         propagar_tiempo: bool = False) -> subprocess.CompletedProcess:
    """git <args> en `raiz`, sin shell y sin preguntar credenciales en la terminal. Con `fallar`, un código
    distinto de 0 termina la herramienta con el error de git (con las credenciales de cualquier URL tapadas).
    Con `propagar_tiempo`, un tope vencido se devuelve al que llama (subprocess.TimeoutExpired) en vez de terminar."""
    entorno = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    try:
        r = correr_hijo(["git", *args], timeout=timeout, cwd=str(raiz), env=entorno)
    except subprocess.TimeoutExpired:
        if propagar_tiempo:
            raise
        salir_con_error(f"git {args[0]} tardó más de {timeout:.0f} s y se canceló")
    except OSError as e:           # no se pudo lanzar (FileNotFoundError, permisos, Windows sin consola...)
        if not shutil.which("git"):
            salir_con_error("git no está instalado o no está en el PATH: instálalo (https://git-scm.com) y repite")
        salir_con_error(f"no se pudo ejecutar git {args[0]}: {ocultar_credenciales(str(e))}")
    if fallar and r.returncode != 0:
        detalle = ocultar_credenciales((r.stderr or r.stdout or "").strip()[:600])
        comando = ocultar_credenciales(" ".join(args))
        salir_con_error(f"git {comando} falló (código {r.returncode}): {detalle}")
    return r


def _mib(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GiB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MiB"
    return f"{n / 1024:.0f} KiB"


def cambios_git(raiz: Path) -> list[str]:
    """Rutas con cambios según `git status --porcelain -z` (modificadas, borradas, nuevas sin seguimiento,
    renombradas o copiadas —en el índice o en el árbol de trabajo—: solo la ruta nueva)."""
    partes = _git(raiz, "status", "--porcelain", "-z", "--untracked-files=all", timeout=300).stdout.split("\0")
    rutas: list[str] = []
    i = 0
    while i < len(partes):
        entrada = partes[i]
        i += 1
        if len(entrada) < 4:
            continue
        rutas.append(entrada[3:])
        if "R" in entrada[:2] or "C" in entrada[:2]:       # la siguiente parte es la ruta de origen
            i += 1
    return rutas


def _permitidos() -> set[str]:
    """La bitácora, como la muestra git status (ruta desde la raíz del repositorio)."""
    return {BITACORA.relative_to(RAIZ).as_posix()}


def cambios_prohibidos(cambios: list[str]) -> list[str]:
    """Las rutas de `cambios` que nunca deben publicarse (trabajo/ del agente y un .venv/ creado a mano)."""
    return [r for r in cambios if r.startswith(_NUNCA_EN_GIT) and r not in _permitidos()]


def cambios_pendientes(cambios: list[str]) -> list[str]:
    """Las rutas de `cambios` que de verdad hay que confirmar: todas menos la bitácora (que cambia sola con cada
    push y entra con la publicación siguiente)."""
    return [r for r in cambios if r not in _permitidos()]


def rutas_ignoradas(raiz: Path, rutas: list[str]) -> list[str]:
    """Las `rutas` (relativas a la raíz del repositorio) que .gitignore excluye y `git add -A` no añadiría
    (git check-ignore --stdin -z; las ya rastreadas no cuentan). Vacía si no hay ninguna."""
    if not rutas:
        return []
    archivo = temporal() / "check_ignore.txt"
    archivo.write_bytes("".join(f"{r}\0" for r in rutas).encode("utf-8"))
    entorno = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    with open(archivo, "rb") as entrada:
        try:
            r = correr_hijo(["git", "check-ignore", "--stdin", "-z"], timeout=300, cwd=str(raiz), env=entorno, stdin=entrada)
        except (OSError, subprocess.TimeoutExpired) as e:
            salir_con_error(f"no se pudo ejecutar git check-ignore: {e}")
    if r.returncode not in (0, 1):          # 0 = alguna ignorada, 1 = ninguna
        salir_con_error(f"git check-ignore falló (código {r.returncode}): {(r.stderr or '').strip()[:300]}")
    return [x for x in r.stdout.split("\0") if x]


def tamano_git(raiz: Path) -> dict:
    """Tamaño de los objetos del repositorio según `git count-objects -vH` (sueltos + empaquetados)."""
    valores: dict[str, str] = {}
    for linea in _git(raiz, "count-objects", "-v", fallar=False).stdout.splitlines():
        clave, _, valor = linea.partition(":")
        valores[clave.strip()] = valor.strip()

    def kib(clave: str) -> int:
        return int(valores.get(clave) or 0) if (valores.get(clave) or "0").isdigit() else 0

    sueltos, empaquetados = kib("size") * 1024, kib("size-pack") * 1024
    legible: dict[str, str] = {}                 # las mismas cifras como las muestra `git count-objects -vH`
    texto = _git(raiz, "count-objects", "-vH", fallar=False).stdout.strip()
    for linea in texto.splitlines():
        clave, _, valor = linea.partition(":")
        legible[clave.strip()] = valor.strip()
    return {"bytes": sueltos + empaquetados, "sueltos": legible.get("size") or _mib(sueltos),
            "empaquetados": legible.get("size-pack") or _mib(empaquetados),
            "objetos": kib("count") + kib("in-pack"), "texto": texto}


def objetos_por_subir(raiz: Path, rango: str) -> int | None:
    """Bytes en disco de los objetos que un push de `rango` enviaría (git rev-list --disk-usage, git 2.31+);
    None si este git no lo sabe calcular."""
    r = _git(raiz, "rev-list", "--disk-usage", "--objects", rango, timeout=600, fallar=False)
    texto = r.stdout.strip()
    return int(texto) if r.returncode == 0 and texto.isdigit() else None


def commits_por_subir(raiz: Path, base: str | None) -> list[str]:
    """Los commits de la línea principal de main que el remoto aún no tiene (los posteriores a `base`, el origin/main;
    todos si es None), del más viejo al más nuevo. Solo la línea principal (--first-parent): cada uno desciende del
    anterior, así cada bloque avanza main en el remoto sin forzar nada."""
    rango = RAMA if base is None else f"{base}..{RAMA}"
    return _git(raiz, "rev-list", "--first-parent", "--reverse", rango, timeout=300).stdout.split()


def pesos_de_commits(raiz: Path, commits: list[str], base: str | None) -> list[tuple[str, int]] | None:
    """[(commit, bytes)]: lo que cada commit de `commits` (línea principal, del más viejo al más nuevo) sube además de lo
    que el remoto ya tendría tras el anterior: sus objetos menos los del commit anterior (o los de `base` para el
    primero; si es None, todos). Así un bloque pesa la suma de sus commits. None si este git no sabe medirlo
    (git rev-list --disk-usage, git 2.31+)."""
    pesos: list[tuple[str, int]] = []
    anterior = base
    for commit in commits:
        excluir = ["--not", anterior] if anterior else []
        r = _git(raiz, "rev-list", "--disk-usage", "--objects", commit, *excluir, timeout=600, fallar=False)
        texto = r.stdout.strip()
        if r.returncode != 0 or not texto.isdigit():
            return None
        pesos.append((commit, int(texto)))
        anterior = commit
    return pesos


def agrupar_en_bloques(pesos: list[tuple[str, int]], tamano: int) -> list[tuple[str, int, int]]:
    """Agrupa los commits consecutivos de `pesos` ([(commit, bytes)], del más viejo al más nuevo) en bloques de hasta
    `tamano` bytes: [(último commit del bloque, bytes del bloque, commits del bloque)]. Un commit que solo pasa de
    `tamano` va en su propio bloque: quien llama comprueba que quepa en el límite de GitHub."""
    bloques: list[tuple[str, int, int]] = []
    ultimo, suma, cuantos = "", 0, 0
    for commit, peso in pesos:
        if cuantos and suma + peso > tamano:
            bloques.append((ultimo, suma, cuantos))
            suma, cuantos = 0, 0
        ultimo, suma, cuantos = commit, suma + peso, cuantos + 1
    if cuantos:
        bloques.append((ultimo, suma, cuantos))
    return bloques


def estado_git(raiz: Path) -> dict:
    """Todo lo que biblioteca.py publicar necesita saber del repositorio de `raiz` (sin tocarlo)."""
    r = _git(raiz, "rev-parse", "--show-toplevel", fallar=False)
    if r.returncode != 0:
        return {"repositorio": False}
    try:
        mismo = Path(r.stdout.strip()).resolve() == raiz.resolve()
    except OSError:
        mismo = False
    if not mismo:
        return {"repositorio": False, "repositorio_padre": r.stdout.strip()}
    rama = _git(raiz, "symbolic-ref", "--short", "-q", "HEAD", fallar=False).stdout.strip() or "(separada)"
    r = _git(raiz, "rev-list", "--count", "HEAD", fallar=False)
    commits = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else 0
    r = _git(raiz, "remote", "get-url", REMOTO, fallar=False)
    remoto = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    origen = _git(raiz, "rev-parse", "--verify", "-q", f"refs/remotes/{REMOTO}/{RAMA}", fallar=False).returncode == 0
    adelante = None
    if origen:
        r = _git(raiz, "rev-list", "--count", f"{REMOTO}/{RAMA}..{RAMA}", fallar=False)
        adelante = int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None
    return {"repositorio": True, "rama": rama, "commits": commits, "cambios": cambios_git(raiz),
            "remoto": ocultar_credenciales(remoto) if remoto else None, "origen_main": origen,
            "adelante": adelante, "tamano": tamano_git(raiz)}


def hacer_commit(raiz: Path, asunto: str, cuerpo: str) -> tuple[str, str]:
    """`git add -A` y `git commit` en `raiz` con asunto, cuerpo y la línea de coautoría. El mensaje va en un
    archivo temporal (`git commit -F`): con -m, un cuerpo largo rebasa el tope de la línea de comandos de Windows.
    Devuelve (sha corto, resumen de archivos cambiados). No toca la bitácora (eso lo hace cmd_commit)."""
    _git(raiz, "add", "-A", timeout=900)
    archivo = temporal() / "commit.txt"
    archivo.write_text(f"{asunto}\n\n{cuerpo or '(sin detalle)'}\n\n{COAUTORIA}\n", encoding="utf-8")
    r = _git(raiz, "commit", "-q", "-F", str(archivo), timeout=900, fallar=False)
    if r.returncode != 0:
        detalle = ocultar_credenciales((r.stderr or r.stdout or "").strip())
        pista = ""
        if "Please tell me who you are" in detalle or "user.name" in detalle or "user.email" in detalle:
            pista = (" → git no sabe quién eres: el usuario debe ejecutar `git config --global user.name \"Su Nombre\"` y "
                     "`git config --global user.email \"su@correo\"` y repetir --commit")
        salir_con_error(f"git commit falló: {detalle[:600]}{pista}")
    sha = _git(raiz, "rev-parse", "--short", "HEAD").stdout.strip()
    resumen = _git(raiz, "show", "--shortstat", "--format=", "HEAD", fallar=False).stdout.strip()
    return sha, resumen


def entradas_pendientes(raiz: Path, cambios: Path) -> list[str]:
    """Textos de las entradas de CAMBIOS.md que todavía no están en HEAD (todas si no hay HEAD o HEAD no tiene el
    archivo). Se comparan por su fecha/marcador: corregir una errata en una entrada ya confirmada no la vuelve
    pendiente."""
    actuales = entradas(cambios)
    if not actuales:
        return []
    r = _git(raiz, "show", f"HEAD:{cambios.relative_to(raiz).as_posix()}", fallar=False)
    confirmadas = {fecha for fecha, _ in entradas_texto(r.stdout)} if r.returncode == 0 else set()
    return [texto for fecha, texto in actuales if fecha not in confirmadas]


# ---------------------------------------------------------------------------
# Comprobaciones compartidas
# ---------------------------------------------------------------------------
def _ultimo_json(texto: str) -> dict | None:
    for linea in reversed(texto.splitlines()):
        linea = linea.strip()
        if linea.startswith("{"):
            try:
                datos = json.loads(linea)
            except ValueError:
                continue
            if isinstance(datos, dict):
                return datos
    return None


def verificador() -> dict:
    """Ejecuta `biblioteca.py verificar --final --json` y devuelve su JSON (integridad, errores, avisos...)."""
    cmd = [sys.executable, str(ENTRADA), "verificar", "--final", "--json"]
    try:
        r = correr_hijo(cmd, timeout=900, cwd=str(RAIZ))
    except subprocess.TimeoutExpired:
        salir_con_error("biblioteca.py verificar tardó más de 15 minutos y se canceló")
    except OSError as e:
        salir_con_error(f"no se pudo ejecutar biblioteca.py verificar: {e}")
    datos = _ultimo_json(r.stdout)
    if datos is None:
        salir_con_error("biblioteca.py verificar no devolvió su JSON (¿falló al arrancar?): "
                        + (r.stderr or r.stdout or "").strip()[-800:])
    datos["pendientes"] = len(datos.get("pendientes") or [])       # la lista puede tener cientos de líneas
    return datos


def _resumen_verificador(v: dict) -> str:
    disco = v.get("en_disco") or {}
    return (f"INTEGRIDAD {'OK' if v.get('integridad') else 'CON ERRORES'} · catálogo {v.get('catalogo', '?')} · "
            f"en disco {sum(disco.values()) if isinstance(disco, dict) else '?'} · Markdown {v.get('markdown', '?')} · "
            f"{len(v.get('errores') or [])} errores · {len(v.get('avisos') or [])} avisos · "
            f"{v.get('pendientes', 0)} pendientes")


def _mostrar_errores(v: dict) -> None:
    errores = v.get("errores") or []
    for error in errores[:20]:
        print(f"  - {error}")
    if len(errores) > 20:
        print(f"  ... y {len(errores) - 20} más (python sistema/biblioteca.py verificar --final)")


def _exigir_integridad(v: dict) -> None:
    if not v.get("integridad"):
        print(f"Verificador: {_resumen_verificador(v)}")
        _mostrar_errores(v)
        salir_con_error("el verificador encontró errores: corrígelos (AGENTS.md, sección 8, fase 3) y repite")


def _exigir_sin_grandes(grandes: list[Path]) -> None:
    if grandes:
        for ruta in grandes[:10]:
            print(f"  - {relativa(ruta)} ({_mib(ruta.stat().st_size)})")
        salir_con_error(f"{len(grandes)} archivo(s) de más de {LIMITE_ARCHIVO_MIB} MiB: GitHub los rechazaría. Comprime los "
                        "PDF (proceso COMPRIMIR: python sistema/biblioteca.py comprimir --listar) o saca del proyecto "
                        "lo que no sea de la biblioteca")


def _exigir_repositorio(git: dict) -> None:
    if git.get("repositorio_padre"):
        salir_con_error(f"el repositorio git es una carpeta superior ({git['repositorio_padre']}): la biblioteca debe "
                        "ser su propio repositorio (git init -b main en la carpeta del proyecto)")
    if not git.get("repositorio"):
        salir_con_error("la carpeta del proyecto no es un repositorio git. El usuario (o el agente, si lo pide) ejecuta "
                        "en la carpeta del proyecto: git init -b main   y repite")
    if git.get("rama") != RAMA:
        salir_con_error(f"estás en la rama '{git.get('rama')}'; la biblioteca se publica desde '{RAMA}': git switch {RAMA}")


def _nombres() -> dict[str, str]:
    return {d["id"]: d["nombre"] for d in cargar_catalogo()["documentos"]}


def _momento() -> str:
    """El momento de esta publicación. Espera a que el reloj pase ese segundo antes de devolverlo: así todo evento
    que otra herramienta registre después lleva una fecha posterior y cae en la publicación siguiente."""
    momento = ahora()
    while ahora() <= momento:
        time.sleep(0.02)
    time.sleep(0.05)
    return momento


def _ventana() -> tuple[str | None, list[str]]:
    """(desde, avisos): la última publicación según CAMBIOS.md (marcador de su última entrada) y según la bitácora
    (evento publicar/preparar), tomando la MÁS RECIENTE. CAMBIOS.md lo puede editar una persona —si borra el marcador
    o una entrada, la bitácora entera volvería a volcarse—; la bitácora es el registro propio de la herramienta."""
    en_cambios = ultima_publicacion(CAMBIOS)
    en_bitacora = ultima_preparacion(BITACORA)
    candidatos = [x for x in (en_cambios, en_bitacora) if x]
    desde = max(candidatos) if candidatos else None
    avisos = []
    if cambios_sin_entradas_reconocibles(CAMBIOS):
        avisos.append("CAMBIOS.md tiene contenido pero ninguna entrada reconocible ('## AAAA-MM-DD — ...'): ¿se editó a "
                      "mano? La ventana de cambios se toma de la bitácora y la entrada nueva irá tras el encabezado")
    if en_bitacora and (not en_cambios or en_bitacora > en_cambios):
        avisos.append(f"la bitácora registra una preparación ({en_bitacora}) más reciente que la última entrada de "
                      f"CAMBIOS.md ({en_cambios or 'ninguna'}): se toma la de la bitácora")
    return desde, avisos


def _estado_sumas(calculadas: list[tuple[str, str]] | None = None) -> dict:
    if not DOCUMENTOS.is_dir():
        return {"archivo": (DOCUMENTOS / ARCHIVO_SUMAS).as_posix(), "existe": False, "ok": False, "faltantes": [],
                "sobrantes": [], "distintas": [], "lineas_invalidas": 0, "listadas": 0, "en_disco": 0}
    return comprobar_sumas(DOCUMENTOS, calculadas)


def _describir_sumas(s: dict) -> str:
    if not s["existe"]:
        return "no existe (lo genera --preparar)"
    if s["ok"]:
        return f"al día ({s['listadas']:,} archivos)"
    partes = []
    if s["faltantes"]:
        partes.append(f"{len(s['faltantes'])} archivo(s) sin listar")
    if s["sobrantes"]:
        partes.append(f"{len(s['sobrantes'])} listado(s) que ya no existen")
    if s["distintas"]:
        partes.append(f"{len(s['distintas'])} con otra huella")
    if s["lineas_invalidas"]:
        partes.append(f"{s['lineas_invalidas']} línea(s) mal formadas")
    return "DESACTUALIZADO: " + ", ".join(partes) + " (lo regenera --preparar)"


def _describir_git(git: dict) -> list[str]:
    if git.get("repositorio_padre"):
        return [f"Repositorio git: ES UNA CARPETA SUPERIOR ({git['repositorio_padre']}); la biblioteca debe ser su propio "
                "repositorio"]
    if not git.get("repositorio"):
        return ["Repositorio git: no existe todavía (git init -b main en la carpeta del proyecto)", "Remoto origin: ninguno"]
    t = git["tamano"]
    pendientes = cambios_pendientes(git["cambios"])
    solo_bitacora = " (solo la bitácora del push anterior)" if git["cambios"] and not pendientes else ""
    lineas = [f"Repositorio git: rama {git['rama']} · {git['commits']} commit(s) · {len(pendientes)} cambio(s) sin "
              f"commit{solo_bitacora} · objetos: {t['sueltos']} sueltos + {t['empaquetados']} empaquetados "
              f"({t['objetos']} objetos)"]
    prohibidos = cambios_prohibidos(git["cambios"])
    if prohibidos:
        lineas.append(f"  ¡OJO! git status muestra {len(prohibidos)} archivo(s) que nunca deben publicarse (revisa .gitignore): "
                      + ", ".join(prohibidos[:5]))
    if git["remoto"]:
        subida = ("nunca se ha subido" if not git["origen_main"]
                  else f"{git['adelante']} commit(s) por subir" if git["adelante"] else "al día con origin/main")
        lineas.append(f"Remoto origin: {git['remoto']} · {subida}")
    else:
        lineas.append("Remoto origin: ninguno (la primera subida necesita --push --remoto URL, solo si el usuario lo pide)")
    return lineas


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------
def cmd_revisar() -> None:
    print("=== PUBLICAR: REVISIÓN (no se escribe nada) ===")
    v = verificador()
    print(f"Verificador (biblioteca.py verificar --final): {_resumen_verificador(v)}")
    _mostrar_errores(v)
    grandes = archivos_grandes(RAIZ)
    print(f"Archivos de más de {LIMITE_ARCHIVO_MIB} MiB (GitHub bloquea 100 MiB): "
          + (", ".join(f"{relativa(g)} ({_mib(g.stat().st_size)})" for g in grandes) if grandes else "ninguno"))
    s = _estado_sumas()
    print(f"biblioteca/{ARCHIVO_SUMAS}: {_describir_sumas(s)}")
    desde, avisos = _ventana()
    print("CAMBIOS.md: " + (f"última publicación {desde}" if desde
                            else "sin entradas (esta sería la primera publicación: entra toda la bitácora)"))
    for aviso in avisos:
        print(f"  AVISO: {aviso}")
    momento = _momento()
    texto, conteos = entrada_cambios(BITACORA, desde, momento, nombres=_nombres())
    if texto:
        lineas = texto.splitlines()
        print(f"Entraría en CAMBIOS.md ({len(lineas)} líneas):")
        for linea in lineas[:30]:
            print(f"  {linea}")
        if len(lineas) > 30:
            print(f"  ... ({len(lineas) - 30} líneas más; completo con --json)")
    else:
        print("Entraría en CAMBIOS.md: nada (sin cambios en la bitácora desde la última publicación)")
    git = estado_git(RAIZ)
    for linea in _describir_git(git):
        print(linea)
    pendientes = cambios_pendientes(git.get("cambios") or [])
    listo = bool(v.get("integridad")) and not grandes and bool(s["ok"]) and not texto
    if not v.get("integridad"):
        siguiente = "corrige los errores del verificador y repite --revisar"
    elif grandes:
        siguiente = "comprime los archivos grandes (proceso COMPRIMIR) y repite --revisar"
    elif not s["ok"] or texto:
        siguiente = "python sistema/biblioteca.py publicar --preparar"
    elif not git.get("repositorio") or pendientes:
        siguiente = "python sistema/biblioteca.py publicar --commit"
    elif not git.get("remoto"):
        siguiente = ("python sistema/biblioteca.py publicar --push --remoto <URL del repositorio vacío> "
                     "(solo si el usuario lo pide)")
    elif not git.get("origen_main") or git.get("adelante"):
        siguiente = "python sistema/biblioteca.py publicar --push (solo si el usuario lo pide)"
    else:
        siguiente = "nada: todo está publicado"
    print(f"\nLISTO PARA PUBLICAR: {'SÍ' if listo else 'NO'} · siguiente paso: {siguiente}")
    salida.poner(verificador=v, grandes=[relativa(g) for g in grandes], sumas=s, ultima_publicacion=desde, avisos=avisos,
                 cambios={"conteos": conteos, "texto": texto}, git=git, listo=listo, siguiente=siguiente)
    sys.exit(0 if listo else 1)


def cmd_preparar() -> None:
    print("=== PUBLICAR: PREPARAR ===")
    v = verificador()
    _exigir_integridad(v)
    print(f"Verificador: {_resumen_verificador(v)}")
    _exigir_sin_grandes(archivos_grandes(RAIZ))
    if not DOCUMENTOS.is_dir():
        salir_con_error("no existe biblioteca/: no hay biblioteca que publicar (primero el proceso ACTUALIZAR)")
    desde, avisos = _ventana()
    for aviso in avisos:
        print(f"AVISO: {aviso}")
    momento = _momento()           # todo evento posterior lleva fecha > momento: cae en la publicación siguiente
    texto, conteos = entrada_cambios(BITACORA, desde, momento, nombres=_nombres())
    with bloqueo():            # nadie reemplaza un documento mientras se calculan las huellas
        calculadas = sumas(DOCUMENTOS)
        estado = comprobar_sumas(DOCUMENTOS, calculadas)
        reescrito = not estado["ok"]
        if reescrito:
            escribir_sumas(DOCUMENTOS, calculadas)
    print(f"biblioteca/{ARCHIVO_SUMAS}: {'generado' if reescrito else 'ya estaba al día'} ({len(calculadas):,} archivos)")
    if texto:
        insertar_entrada(CAMBIOS, texto)
        print(f"CAMBIOS.md: entrada añadida ({texto.splitlines()[0][3:]})")
        for linea in texto.splitlines()[:30]:
            print(f"  {linea}")
        if len(texto.splitlines()) > 30:
            print(f"  ... ({len(texto.splitlines()) - 30} líneas más en CAMBIOS.md)")
    else:
        print("CAMBIOS.md: sin cambios en la bitácora desde la última publicación"
              + (f" ({desde})" if desde else "") + ": no se añade entrada")
    bitacora({"herramienta": "publicar", "accion": "preparar", "desde": desde, "publicacion": momento,
              "entrada": bool(texto), "conteos": conteos, "sumas": {"archivos": len(calculadas), "reescrito": reescrito}})
    if texto or reescrito:
        print("\nSiguiente paso: python sistema/biblioteca.py publicar --commit")
    else:
        print("\nNada que preparar: SHA256SUMS y CAMBIOS.md ya estaban al día.")
    salida.poner(verificador=v, sumas={"archivo": relativa(DOCUMENTOS / ARCHIVO_SUMAS), "archivos": len(calculadas),
                                       "reescrito": reescrito}, avisos=avisos,
                 cambios={"desde": desde, "publicacion": momento, "entrada": bool(texto), "conteos": conteos, "texto": texto})


def cmd_commit() -> None:
    print("=== PUBLICAR: COMMIT ===")
    _exigir_repositorio(estado_git(RAIZ))
    v = verificador()              # subproceso que también usa el candado: va ANTES de tomarlo
    _exigir_integridad(v)
    _exigir_sin_grandes(archivos_grandes(RAIZ))
    with bloqueo():                # desde comprobar SHA256SUMS hasta que termina el commit nadie reemplaza un documento
        s = _estado_sumas()
        if not s["ok"]:
            salir_con_error(f"biblioteca/{ARCHIVO_SUMAS} {_describir_sumas(s)}: ejecuta "
                            "python sistema/biblioteca.py publicar --preparar")
        git = estado_git(RAIZ)
        cambios = git["cambios"]
        pendientes = cambios_pendientes(cambios)
        if not pendientes:
            salir_con_error("no hay cambios que confirmar: el último commit ya lo tiene todo"
                            + (" (solo la bitácora del push anterior; entrará con la próxima publicación)" if cambios
                               else " (git status limpio)"))
        prohibidos = cambios_prohibidos(cambios)
        if prohibidos:
            for ruta in prohibidos[:10]:
                print(f"  - {ruta}")
            salir_con_error(f"git status muestra {len(prohibidos)} archivo(s) que nunca deben publicarse. Revisa .gitignore: "
                            "debe tener `trabajo/` y `.venv/`")
        listadas, _invalidas = leer_sumas(DOCUMENTOS / ARCHIVO_SUMAS)
        ignoradas = rutas_ignoradas(RAIZ, [f"{DOCUMENTOS.name}/{ruta}" for ruta in listadas])
        if ignoradas:
            for ruta in ignoradas[:10]:
                print(f"  - {ruta}")
            salir_con_error(f"{ARCHIVO_SUMAS} lista {len(ignoradas)} archivo(s) que .gitignore excluye (el clon fallaría "
                            "`sha256sum -c`): ejecuta python sistema/biblioteca.py verificar --limpiar y repite --preparar")
        entradas_nuevas = entradas_pendientes(RAIZ, CAMBIOS)
        if entradas_nuevas and CAMBIOS.relative_to(RAIZ).as_posix() in cambios:
            asunto, cuerpo = mensaje_commit_varias(entradas_nuevas)
        else:         # la entrada ya se confirmó antes: solo cambian herramientas, documentación o la bitácora
            asunto = f"Biblioteca {hoy()}: sin cambios en los documentos"
            cuerpo = ("Cambios en las herramientas, la documentación o la bitácora del proyecto; biblioteca/ no cambió "
                      "desde la publicación anterior.")
        print(f"Archivos con cambios: {len(pendientes)}")
        if len(entradas_nuevas) > 1:
            print(f"Entradas de CAMBIOS.md por confirmar: {len(entradas_nuevas)}")
        print(f"Mensaje: {asunto}")
        bitacora({"herramienta": "publicar", "accion": "commit", "asunto": asunto, "cambios": len(pendientes),
                  "entradas": len(entradas_nuevas)})
        sha, resumen = hacer_commit(RAIZ, asunto, cuerpo)
    print(f"Commit {sha}: {resumen or 'hecho'}")
    print("\nSiguiente paso: PREGUNTA al usuario si autoriza subirlo; solo con su sí: "
          "python sistema/biblioteca.py publicar --push"
          + ("" if git["remoto"] else " --remoto <URL del repositorio vacío>"))
    salida.poner(commit=sha, asunto=asunto, cuerpo=cuerpo, archivos=len(pendientes), entradas=len(entradas_nuevas),
                 resumen=resumen, remoto=git["remoto"])


def _pista_push(detalle: str) -> str:
    bajo = detalle.lower()
    if "terminal prompts disabled" in bajo or "authentication failed" in bajo or "could not read username" in bajo:
        return ("No hay credenciales para GitHub: el usuario (nunca el agente) ejecuta `gh auth login`, guarda un "
                "token en el gestor de credenciales de git o usa la URL SSH del repositorio.")
    if "fetch first" in bajo or "non-fast-forward" in bajo or "rejected" in bajo:
        return ("El remoto tiene commits que aquí no están (¿el repositorio no estaba vacío, o alguien subió "
                "antes?). Revisa con `git fetch origin` y `git log origin/main`; no se fuerza nunca.")
    if "repository not found" in bajo:
        return "GitHub no encuentra el repositorio: revisa la URL (--remoto) y que el usuario tenga acceso."
    if "exceeds github's file size limit" in bajo or "gh001" in bajo:
        return "Un archivo pasa de 100 MiB: comprímelo (proceso COMPRIMIR) y vuelve a hacer commit."
    if "pack exceeds maximum allowed size" in bajo or "gh008" in bajo:
        return ESTRATEGIA_BLOQUES
    return ""


def cmd_push(remoto_nuevo: str | None) -> None:
    print("=== PUBLICAR: PUSH (autorizado por el usuario) ===")
    git = estado_git(RAIZ)
    _exigir_repositorio(git)
    if git["commits"] == 0:
        salir_con_error("no hay ningún commit: ejecuta --preparar y --commit antes de subir")
    pendientes = cambios_pendientes(git["cambios"])
    if pendientes:
        print(f"AVISO: {len(pendientes)} cambio(s) sin commit no se subirán (si deben publicarse: --commit y repite)")
    if remoto_nuevo:
        if lleva_credenciales(remoto_nuevo):
            salir_con_error("la URL de --remoto lleva credenciales (usuario:token@): no se acepta, para que no queden en "
                            ".git/config ni en la bitácora. La autenticación la configura el usuario: `gh auth login`, el "
                            "gestor de credenciales de git o llaves SSH. Repite con la URL sin credenciales")
        if not _URL_REMOTO.match(remoto_nuevo):
            salir_con_error("--remoto no parece una URL de git: debe ser https://..., ssh://... o "
                            "git@github.com:<usuario>/<repositorio>.git")
        actual = _git(RAIZ, "remote", "get-url", REMOTO, fallar=False)
        if actual.returncode != 0:
            _git(RAIZ, "remote", "add", REMOTO, remoto_nuevo)
            print(f"Remoto {REMOTO} añadido: {ocultar_credenciales(remoto_nuevo)}")
        elif actual.stdout.strip() != remoto_nuevo:
            # remove borra también refs/remotes/origin/*: el remoto nuevo empieza de cero (primera subida, con su
            # comprobación de tamaño), en vez de heredar el origin/main del remoto anterior
            _git(RAIZ, "remote", "remove", REMOTO)
            _git(RAIZ, "remote", "add", REMOTO, remoto_nuevo)
            print(f"Remoto {REMOTO} reemplazado: {ocultar_credenciales(remoto_nuevo)}")
        git = estado_git(RAIZ)
    if not git["remoto"]:
        print(CREAR_REPOSITORIO)
        salir_con_error(f"no hay remoto '{REMOTO}': crea el repositorio vacío y repite con --push --remoto URL")
    primera = not git["origen_main"]
    base = None if primera else f"{REMOTO}/{RAMA}"
    rango = RAMA if primera else f"{REMOTO}/{RAMA}..{RAMA}"
    estimado = objetos_por_subir(RAIZ, rango)
    total = git["tamano"]["bytes"]
    por_subir = estimado if estimado is not None else total
    cuantos = "primera subida" if primera else f"{git['adelante']} commit(s) nuevos"
    print(f"Remoto: {git['remoto']} · {cuantos} · por subir ≈ {_mib(por_subir)} (repositorio: {_mib(total)}; "
          "GitHub rechaza un push de 2 GB o más)")
    bloques = _planear_bloques(commits_por_subir(RAIZ, base), base, por_subir)
    n = len(bloques)
    if n > 1:
        print(f"Se sube POR BLOQUES: {n} push de hasta {_mib(min(BLOQUE_PUSH_BYTES, LIMITE_PUSH_BYTES))}, del commit más "
              "viejo al más nuevo. Lo que suba se queda en GitHub; si un bloque falla, repetir --push sigue desde él:")
        for i, (commit, peso, k) in enumerate(bloques, 1):
            print(f"  bloque {i}/{n}: {_n(k, 'commit', 'commits')} hasta {commit[:7]} · ≈ {_mib(peso)}")
    topes: list[float] = []
    for i, (commit, peso, _k) in enumerate(bloques, 1):
        final = i == n
        destino = ["-u", REMOTO, RAMA] if final else [REMOTO, f"{commit}:refs/heads/{RAMA}"]
        mostrado = " ".join(destino) if final else f"{REMOTO} {commit[:7]}:refs/heads/{RAMA}"
        marca = {"bloque": f"{i}/{n}"} if n > 1 else {}
        corto = _git(RAIZ, "rev-parse", "--short", commit).stdout.strip()
        tope = max(TOPE_PUSH_MINIMO, peso / VELOCIDAD_MINIMA)
        topes.append(tope)
        print(f"{'bloque ' + str(i) + '/' + str(n) + ': ' if n > 1 else ''}git push {mostrado} ... (≈ {_mib(peso)}; "
              f"tope: {tope / 60:.0f} min, es decir al menos {VELOCIDAD_MINIMA * 8 / 1_000_000:.1f} Mbps sostenidos)",
              flush=True)
        ya_subidos = f" Ya están en el remoto {i - 1} de {n} bloques: repite --push y sigue desde el bloque {i}." if i > 1 else ""
        try:
            # pack.useSparse=false: el cálculo rápido de git (sparse) vuelve a enviar los archivos que un commit solo
            # cambió de carpeta aunque el remoto ya los tenga; sin él, el push manda lo que midió pesos_de_commits
            r = _git(RAIZ, "-c", "pack.useSparse=false", "push", *destino, timeout=tope, fallar=False,
                     propagar_tiempo=True)
        except subprocess.TimeoutExpired:
            bitacora({"herramienta": "publicar", "accion": "push", "remoto": git["remoto"], "rama": RAMA, **marca,
                      "resultado": "ERROR", "detalle": f"cancelado: tardó más de {tope:.0f} s"})
            perdido = "este bloque no quedó en el remoto" if n > 1 else "nada quedó en el remoto"
            salir_con_error(f"git push se canceló: tardó más de {tope:.0f} s. git no reanuda subidas, así que {perdido}; "
                            f"repite con una conexión mejor.{ya_subidos}")
        detalle = ocultar_credenciales(((r.stderr or "") + "\n" + (r.stdout or "")).strip())
        if r.returncode != 0:
            bitacora({"herramienta": "publicar", "accion": "push", "remoto": git["remoto"], "rama": RAMA, **marca,
                      "resultado": "ERROR", "detalle": detalle[:400]})
            print(detalle)
            pista = _pista_push(detalle)
            if pista:
                print(pista)
            salir_con_error(f"git push falló (ver arriba).{ya_subidos}")
        if detalle:
            print(detalle)
        bitacora({"herramienta": "publicar", "accion": "push", "remoto": git["remoto"], "rama": RAMA, **marca,
                  "commit": corto, "resultado": "OK", "bytes_estimados": peso, "primera": primera and i == 1})
    sha = _git(RAIZ, "rev-parse", RAMA).stdout.strip()
    r = _git(RAIZ, "ls-remote", REMOTO, f"refs/heads/{RAMA}", timeout=300, fallar=False)
    en_remoto = r.stdout.split()[0] if r.returncode == 0 and r.stdout.split() else None
    if en_remoto and en_remoto != sha:
        salir_con_error(f"después del push, {RAMA} en el remoto es {en_remoto[:7]} y aquí {sha[:7]}: alguien más subió algo "
                        f"al mismo tiempo. Revisa con `git fetch {REMOTO}` y `git log {REMOTO}/{RAMA}`; no se fuerza nunca")
    estado = _git(RAIZ, "status", "-sb", fallar=False).stdout.strip().splitlines()
    ultimo = _git(RAIZ, "log", f"{REMOTO}/{RAMA}", "-1", "--format=%h %ci %s", fallar=False).stdout.strip()
    print(f"Estado: {estado[0] if estado else '?'}")
    print(f"{REMOTO}/{RAMA}: {ultimo or '?'}")
    print(f"Comprobado en el remoto: {RAMA} = {sha[:7]}" if en_remoto else
          "AVISO: no se pudo consultar el remoto para comprobarlo (git ls-remote): "
          + ocultar_credenciales((r.stderr or "").strip()[:200]))
    print(f"\nPublicado: {git['remoto']} (rama {RAMA}, commit {sha[:7]}{f', en {n} bloques' if n > 1 else ''}). La "
          "bitácora registró el push; ese registro entra en la próxima publicación.")
    salida.poner(remoto=git["remoto"], rama=RAMA, commit=sha[:7], primera=primera, bytes_estimados=por_subir,
                 tope_seg=sum(topes), bloques=[{"commit": c[:7], "bytes": p, "commits": k} for c, p, k in bloques],
                 comprobado=bool(en_remoto), origen_main=ultimo, estado=estado[0] if estado else None)


def _planear_bloques(commits: list[str], base: str | None, por_subir: int) -> list[tuple[str, int, int]]:
    """Los push que hacen falta: [(commit hasta el que sube, bytes, commits)], del más viejo al más nuevo. Uno solo si
    todo cabe en un bloque (o no hay más de un commit); si no, bloques de commits (agrupar_en_bloques). Termina con
    error, sin subir nada, si un bloque —un commit solo— pasa del límite de GitHub o si este git no sabe medirlos."""
    tamano = min(BLOQUE_PUSH_BYTES, LIMITE_PUSH_BYTES)
    if por_subir <= tamano or len(commits) <= 1:
        bloques = [(commits[-1] if commits else RAMA, por_subir, len(commits))]
    else:
        pesos = pesos_de_commits(RAIZ, commits, base)
        if pesos is None:
            salir_con_error(f"lo que se subiría pesa {_mib(por_subir)} y hay que subirlo por bloques, pero este git no sabe "
                            "medir cada commit (git rev-list --disk-usage, git 2.31 o más nuevo): actualiza git y repite")
        bloques = agrupar_en_bloques(pesos, tamano)
    for commit, peso, _k in bloques:
        if peso > LIMITE_PUSH_BYTES:
            print(ESTRATEGIA_BLOQUES)
            salir_con_error(f"el commit {commit[:7]} trae {_mib(peso)}, más de 1.8 GiB: GitHub lo rechazaría aunque subiera "
                            "solo. Hay que rehacerlo en partes (arriba); no se hace solo")
    return bloques


def main():
    p = Analizador(description="Preparar la biblioteca para GitHub y subirla solo cuando el usuario lo pide.",
                   formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_PUBLICAR)
    modo = p.add_mutually_exclusive_group(required=True)
    modo.add_argument("--revisar", action="store_true", help="qué haría (no escribe nada)")
    modo.add_argument("--preparar", action="store_true",
                      help="exige INTEGRIDAD OK; escribe SHA256SUMS y la entrada de CAMBIOS.md")
    modo.add_argument("--commit", action="store_true", help="git add -A y git commit con el mensaje de CAMBIOS.md")
    modo.add_argument("--push", action="store_true",
                      help="SOLO si el usuario lo pidió explícitamente: git push -u origin main")
    p.add_argument("--remoto", metavar="URL", help="con --push: añade o reemplaza el remoto origin (repositorio vacío de GitHub)")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    args = p.parse_args()
    salida.iniciar(args.json, "publicar")
    if args.remoto and not args.push:
        p.error("--remoto solo tiene sentido con --push")
    if args.revisar:
        salida.poner(modo="revisar")
        cmd_revisar()
    elif args.preparar:
        salida.poner(modo="preparar")
        cmd_preparar()
    elif args.commit:
        salida.poner(modo="commit")
        cmd_commit()
    else:
        salida.poner(modo="push")
        cmd_push(args.remoto)
