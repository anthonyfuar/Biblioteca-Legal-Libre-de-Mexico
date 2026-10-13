"""
catalogo.py — El modelo de la biblioteca (estructura única de carpetas, esquema del catálogo y del manifiesto, pistas)
y la herramienta `catalogo`.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from herramientas.comun import (
    ANTERIORES,
    BITACORA,
    CARPETA_MARKDOWN,
    CARPETA_PDF,
    DOCUMENTOS,
    FUENTES,
    Analizador,
    bitacora,
    bloqueo,
    cargar_catalogo,
    cargar_fuentes,
    cargar_manifiesto,
    guardar_catalogo,
    guardar_json,
    guardar_manifiesto,
    hoy,
    json_tambien_al_final,
    nombre_archivo,
    normalizar,
    relativa,
    salida,
    salir_con_error,
)

# ====================================================================================================
# La ÚNICA organización de biblioteca/ (sistema/referencia/estructura.md): carpetas válidas,
# rutas de cada documento, qué va en Abrogadas/, y el único lugar que mueve archivos (mover_documento).
# ====================================================================================================
FORMATOS = ("pdf", "doc", "docx")      # solo PDF o Word, nunca páginas web; en este orden se busca el archivo
CONSTITUCION = "Constitucion"
FEDERAL = "Federal"
CARPETA_ESTADOS = "Estados"
TRATADOS = "Tratados_internacionales"
ABROGADAS = "Abrogadas"
SUBGRUPOS_CONSTITUCION = ("Reformas",)
TEMAS_TRATADOS = ("Derechos_humanos", "Justicia_y_cooperacion", "Comercio")
MATERIAS = {           # materia federal: subcarpetas permitidas (expresiones regulares)
    "Administrativo_y_anticorrupcion": (),
    "Agrario": (),
    "Ambiental": (),
    "Amparo_y_Poder_Judicial": (),
    "Civil_y_familiar": (),
    "Electoral": (),
    "Energia": (),
    "Fiscal": (r"Resolucion_Miscelanea_Fiscal_\d{4}",),
    "Laboral_y_seguridad_social": (),
    "Mercantil_y_financiero": (),
    "Migracion_y_poblacion": (),
    "Penal_y_seguridad": (),
    "Propiedad_intelectual_y_consumidor": (),
    "Social_salud_y_educacion": (),
    "Telecomunicaciones": (),
    "Transito_y_transporte": (),
    "Transparencia_y_datos_personales": (),
    "Urbano_obras_y_bienes_nacionales": (),
}


def grupo_de_estado(entidad: str) -> str:
    """'Baja California Sur' -> 'Estados/Baja_California_Sur'."""
    return f"{CARPETA_ESTADOS}/{nombre_archivo(entidad)}"


def validar_grupo(grupo: str, entidad: str | None = None) -> str | None:
    """None si el grupo es parte de la estructura (y corresponde a la entidad); si no, el motivo."""
    partes = grupo.split("/")
    if entidad:
        esperado = grupo_de_estado(entidad)
        return None if grupo == esperado else f"un documento de {entidad} va en {esperado}"
    if partes[0] == CARPETA_ESTADOS:
        return "Estados/<Estado> es solo para documentos estatales (con --entidad)"
    if partes[0] == CONSTITUCION:
        if len(partes) == 1 or (len(partes) == 2 and partes[1] in SUBGRUPOS_CONSTITUCION):
            return None
        return f"en {CONSTITUCION}/ solo existe la subcarpeta {', '.join(SUBGRUPOS_CONSTITUCION)}"
    if partes[0] != FEDERAL or len(partes) < 2:
        return f"debe empezar con {CONSTITUCION}, {FEDERAL}/<Materia> o {CARPETA_ESTADOS}/<Estado>"
    if partes[1] == TRATADOS:
        if len(partes) == 3 and partes[2] in TEMAS_TRATADOS:
            return None
        return f"los tratados van en {FEDERAL}/{TRATADOS}/<{' | '.join(TEMAS_TRATADOS)}>"
    if partes[1] not in MATERIAS:
        return f"materia desconocida '{partes[1]}' (materias: {', '.join(MATERIAS)})"
    resto = partes[2:]
    if resto and resto[0] == ABROGADAS:
        resto = resto[1:]
    if not resto or (len(resto) == 1 and any(re.fullmatch(x, resto[0]) for x in MATERIAS[partes[1]])):
        return None
    permitidas = ", ".join(MATERIAS[partes[1]]) or "ninguna"
    return f"subcarpeta no permitida en {partes[1]} (solo {ABROGADAS}/ y: {permitidas})"


def grupo_segun_estado(grupo: str, estado: str) -> str:
    """En las materias federales, lo abrogado vive en Abrogadas/ dentro de su materia (conservando su
    subgrupo, si tiene); al volver a vigente regresa. Constitucion/, Estados/ y los tratados no usan
    esa subcarpeta."""
    partes = grupo.split("/")
    if partes[0] != FEDERAL or len(partes) < 2 or partes[1] == TRATADOS:
        return grupo
    if len(partes) > 2 and partes[2] == ABROGADAS:
        del partes[2]
    if estado == "abrogado":
        partes.insert(2, ABROGADAS)
    return "/".join(partes)


def problema_de_estructura(doc: dict) -> str | None:
    """None si el documento está donde le toca (grupo válido, Abrogadas/ según su estado y archivo =
    nombre oficial); si no, qué está mal y el comando que lo corrige."""
    motivo = validar_grupo(doc["grupo"], doc.get("entidad"))
    if motivo:
        return f"carpeta '{doc['grupo']}' fuera de la estructura ({motivo})"
    if grupo_segun_estado(doc["grupo"], doc["estado"]) != doc["grupo"]:
        return (f"está en {doc['grupo']} pero es '{doc['estado']}' (corrige con: python sistema/biblioteca.py "
                f"catalogo mover {doc['id']} --grupo \"{doc['grupo']}\")")
    if doc["archivo"] != nombre_archivo(doc["nombre"]):
        return (f"su archivo '{doc['archivo']}' no corresponde a su nombre (debe ser '{nombre_archivo(doc['nombre'])}'; "
                f"corrige con: python sistema/biblioteca.py catalogo mover {doc['id']} --grupo \"{doc['grupo']}\")")
    return None


def exigir_estructura(doc: dict) -> None:
    """Para las herramientas que escriben en biblioteca/: si el documento no está en la estructura, se
    detienen en vez de crear carpetas o archivos fuera de ella."""
    motivo = problema_de_estructura(doc)
    if motivo:
        salir_con_error(f"{doc['id']}: {motivo}. No se escribió nada en biblioteca/.")


def mover_documento(doc: dict, nuevo_grupo: str, catalogo: dict, manifiesto: dict,
                    nuevo_archivo: str | None = None) -> bool:
    """ÚNICO lugar que cambia de sitio los archivos de un documento: lo lleva a (nuevo_grupo,
    nuevo_archivo), moviendo a la vez su documento oficial (en PDF/) y su Markdown (en Markdown/), quita las carpetas
    que quedan vacías y corrige
    las rutas del manifiesto. Rechaza grupos fuera de la estructura. Se llama DENTRO del candado; quien
    llama guarda catálogo y manifiesto. El id no cambia."""
    nuevo_archivo = nuevo_archivo or doc["archivo"]
    if (nuevo_grupo, nuevo_archivo) == (doc["grupo"], doc["archivo"]):
        return False
    motivo = validar_grupo(nuevo_grupo, doc.get("entidad"))
    if motivo:
        salir_con_error(f"'{nuevo_grupo}' no es parte de la estructura de la biblioteca: {motivo}. "
                        "Carpetas válidas: python sistema/biblioteca.py catalogo grupos")
    choque = next((d for d in catalogo["documentos"] if d is not doc and d["grupo"] == nuevo_grupo
                   and d["archivo"] == nuevo_archivo), None)
    if choque:
        salir_con_error(f"Ya existe {choque['id']} con el archivo '{nuevo_archivo}' en {nuevo_grupo}.")
    viejo_pdf, viejo_md = ruta_pdf(doc), ruta_md(doc)
    formato = viejo_pdf.suffix.lstrip(".")          # un Word oficial sigue siendo .doc en su nuevo lugar
    doc["grupo"], doc["archivo"] = nuevo_grupo, nuevo_archivo
    for viejo, nuevo in ((viejo_pdf, ruta_pdf(doc, formato)), (viejo_md, ruta_md(doc))):
        if viejo.exists():
            nuevo.parent.mkdir(parents=True, exist_ok=True)
            viejo.replace(nuevo)
            for carpeta in viejo.parents:          # quita las carpetas que quedaron vacías (nunca PDF/ ni Markdown/)
                if (carpeta in (DOCUMENTOS, DOCUMENTOS / CARPETA_PDF, DOCUMENTOS / CARPETA_MARKDOWN)
                        or DOCUMENTOS not in carpeta.parents):
                    break
                try:
                    carpeta.rmdir()
                except OSError:
                    break
    registro = manifiesto.get(doc["id"])
    if registro:
        registro["archivo"] = ruta_relativa_pdf(doc, formato)
        if registro.get("markdown"):
            registro["markdown"]["archivo"] = ruta_relativa_md(doc)
    return True


def buscar_documento(catalogo: dict, doc_id: str) -> dict:
    """Devuelve el documento con ese id o termina con sugerencias parecidas."""
    for doc in catalogo["documentos"]:
        if doc["id"] == doc_id:
            return doc
    ids = [d["id"] for d in catalogo["documentos"]]
    parecidos = difflib.get_close_matches(doc_id, ids, n=5, cutoff=0.5)
    pista = f" ¿Quisiste decir: {', '.join(parecidos)}?" if parecidos else ""
    salir_con_error(f"No existe el id '{doc_id}' en el catálogo.{pista} "
                    f"Usa: python sistema/biblioteca.py catalogo listar --buscar \"texto\"")
    return {}  # inalcanzable


def ruta_pdf(doc: dict, formato: str | None = None) -> Path:
    """Dónde va el documento oficial: biblioteca/PDF/<grupo>/<archivo>.pdf (ver ESTRUCTURA). Si la fuente oficial lo
    publica solo en Word, va en el mismo lugar con su extensión real (.doc, .docx). Sin
    `formato`, devuelve el archivo que exista (o el .pdf si aún no hay)."""
    base = DOCUMENTOS.joinpath(CARPETA_PDF, *doc["grupo"].split("/"), doc["archivo"])
    if formato:
        return base.with_name(f"{base.name}.{formato}")
    for f in FORMATOS:
        ruta = base.with_name(f"{base.name}.{f}")
        if ruta.exists():
            return ruta
    return base.with_name(base.name + ".pdf")


def ruta_md(doc: dict) -> Path:
    """Su Markdown: biblioteca/Markdown/<grupo>/<archivo>.md (la misma carpeta y el mismo nombre que en PDF/)."""
    return DOCUMENTOS.joinpath(CARPETA_MARKDOWN, *doc["grupo"].split("/"), doc["archivo"] + ".md")


def ruta_relativa_pdf(doc: dict, formato: str | None = None) -> str:
    """Como se guarda en el manifiesto ('archivo'): relativa a biblioteca/, con '/' (empieza con PDF/)."""
    return ruta_pdf(doc, formato).relative_to(DOCUMENTOS).as_posix()


def ruta_relativa_md(doc: dict) -> str:
    """Como se guarda en el manifiesto ('markdown.archivo'): empieza con Markdown/."""
    return ruta_md(doc).relative_to(DOCUMENTOS).as_posix()


def pendiente_de_descarga(doc: dict, manifiesto: dict) -> bool:
    """Vigente o abrogado sin PDF guardado (salvo que se confirmó que no hay texto oficial en línea)."""
    return doc["estado"] in ("vigente", "abrogado") and not doc.get("sin_pdf") and doc["id"] not in manifiesto


# ====================================================================================================
# Esquema de biblioteca/catalogo.json y biblioteca/manifiesto.json (sistema/referencia/esquema.md): versiones, vocabularios y
# validadores de cada entrada; huella del documento oficial y si su Markdown está al día.
# ====================================================================================================
ESTADOS_JURIDICOS = ("vigente", "abrogado", "no_existe", "no_localizado")   # estado de un documento del catálogo
RESULTADOS_PISTA = ("ok", "falla", "sin_probar")                            # pistas.urls[].resultado
METODOS_MARKDOWN = ("markitdown", "ocr", "lector_word")


def huella_origen(registro: dict) -> str | None:
    """Huella SHA-256 del documento OFICIAL tal como se descargó. Si el PDF guardado se comprimió
    (biblioteca.py comprimir), su 'sha256' es el del archivo comprimido y el del oficial queda en
    compresion.sha256_original: así una nueva descarga idéntica sigue siendo SIN_CAMBIO y el Markdown sigue al día."""
    return (registro.get("compresion") or {}).get("sha256_original") or registro.get("sha256")


def markdown_al_dia(doc: dict, registro: dict) -> bool:
    """El Markdown existe y salió del documento oficial que está registrado."""
    md = registro.get("markdown") or {}
    return bool(md) and md.get("sha256_origen") == huella_origen(registro) and ruta_md(doc).exists()


def problemas_de_esquema_doc(doc: dict) -> list[str]:
    """Qué tiene mal una entrada del catálogo respecto al esquema (sistema/referencia/esquema.md). Vacío = bien."""
    p = []
    obligatorios: tuple[tuple[str, type], ...] = (("id", str), ("nombre", str), ("grupo", str), ("archivo", str),
                                                  ("estado", str), ("pistas", dict))
    for clave, tipo in obligatorios:
        if not isinstance(doc.get(clave), tipo):
            p.append(f"'{clave}' falta o no es {tipo.__name__}")
    if doc.get("estado") not in ESTADOS_JURIDICOS:
        p.append(f"estado '{doc.get('estado')}' no es uno de {', '.join(ESTADOS_JURIDICOS)}")
    opcionales: tuple[tuple[str, type], ...] = (("entidad", str), ("nota", str), ("aviso", str),
                                                ("nombres_anteriores", list), ("sin_pdf", bool))
    for clave, tipo in opcionales:
        if clave in doc and not isinstance(doc[clave], tipo):
            p.append(f"'{clave}' no es {tipo.__name__}")
    if "ambito" in doc:
        p.append("'ambito' ya no existe (se deduce de 'entidad')")
    if doc.get("nota") == "":
        p.append("'nota' vacía: se omite en vez de dejar \"\"")
    pistas = doc.get("pistas")
    if not isinstance(pistas, dict):
        pistas = {}
    urls = pistas.get("urls")
    for u in urls if isinstance(urls, list) else []:
        if not isinstance(u, dict) or not isinstance(u.get("url"), str):
            p.append("pista sin 'url'")
            continue
        if u.get("resultado") not in RESULTADOS_PISTA:
            p.append(f"pista {u['url'][:50]}: resultado '{u.get('resultado')}' no es uno de {', '.join(RESULTADOS_PISTA)}")
        if not _es_fecha_iso(u.get("fecha")):
            p.append(f"pista {u['url'][:50]}: fecha '{u.get('fecha')}' no es AAAA-MM-DD")
    return p


def problemas_de_esquema_registro(registro: dict) -> list[str]:
    """Qué tiene mal un registro del manifiesto respecto al esquema. Vacío = bien."""
    p = []
    obligatorios: tuple[tuple[str, type], ...] = (("archivo", str), ("formato", str), ("sha256", str), ("bytes", int),
                                                  ("url", str), ("fecha_descarga", str), ("ultima_verificacion", str))
    for clave, tipo in obligatorios:
        if not isinstance(registro.get(clave), tipo):
            p.append(f"'{clave}' falta o no es {tipo.__name__}")
    if registro.get("formato") not in FORMATOS:
        p.append(f"formato '{registro.get('formato')}' no es uno de {', '.join(FORMATOS)}")
    if not _es_sha256(registro.get("sha256")):
        p.append("'sha256' no es una huella SHA-256")
    for clave in ("fecha_descarga", "ultima_verificacion"):
        if not _es_fecha_iso(registro.get(clave)):
            p.append(f"'{clave}' no es AAAA-MM-DD")
    if registro.get("fecha_reforma") is not None and not _es_fecha_iso(registro.get("fecha_reforma")):
        p.append("'fecha_reforma' no es AAAA-MM-DD ni null")
    if "md5" in registro or "pdf" in registro or "nombre_confirmado" in registro:
        p.append("claves del esquema 1 (md5/pdf/nombre_confirmado): migra el manifiesto")
    md = registro.get("markdown")
    if md is not None:
        if not isinstance(md, dict):
            p.append("'markdown' no es objeto ni null")
        else:
            if not isinstance(md.get("archivo"), str) or not _es_sha256(md.get("sha256_origen")):
                p.append("'markdown' sin 'archivo' o sin 'sha256_origen'")
            if md.get("metodo") not in METODOS_MARKDOWN:
                p.append(f"markdown.metodo '{md.get('metodo')}' no es uno de {', '.join(METODOS_MARKDOWN)}")
            if not isinstance(md.get("caracteres"), int) or not _es_fecha_iso(md.get("fecha")):
                p.append("markdown.caracteres debe ser entero y markdown.fecha AAAA-MM-DD")
            if "formato" in md and (not isinstance(md["formato"], int) or isinstance(md["formato"], bool)):
                p.append("markdown.formato debe ser entero (versión del Markdown estructurado; ausente = 1, crudo)")
    comp = registro.get("compresion")
    if comp is not None and (not isinstance(comp, dict) or not _es_sha256(comp.get("sha256_original"))
                             or not _es_sha256(comp.get("sha256")) or not isinstance(comp.get("bytes_original"), int)
                             or not isinstance(comp.get("bytes"), int) or comp.get("nivel") not in (10, 30, 50, 80)):
        p.append("'compresion' incompleta (nivel, sha256_original, bytes_original, sha256, bytes)")
    return p


def _es_fecha_iso(valor) -> bool:
    if not isinstance(valor, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        return False
    try:
        datetime.strptime(valor, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _es_sha256(valor) -> bool:
    return isinstance(valor, str) and re.fullmatch(r"[0-9a-f]{64}", valor) is not None


# ====================================================================================================
# Las pistas de un documento del catálogo: URLs donde se encontró o buscó, con fecha y resultado. Las usan
# biblioteca.py catalogo (pista, agregar) y biblioteca.py descargar (al registrar una descarga).
# ====================================================================================================
def sin_esquema(url: str) -> str:
    return url.split("://", 1)[-1].rstrip("/").lower()


def registrar_pista(doc: dict, url: str, resultado: str, nota: str | None = None) -> bool:
    """Pone la URL al frente de las pistas del documento (sin duplicar). Usado también por biblioteca.py descargar.
    Devuelve False (y no toca nada) si esa URL ya era la primera pista con el mismo resultado: así una
    verificación SIN_CAMBIO no reescribe el catálogo en cada corrida."""
    if resultado not in RESULTADOS_PISTA:
        raise ValueError(f"resultado de pista inválido: {resultado}")
    pistas = doc.setdefault("pistas", {})
    urls = pistas.get("urls", [])
    if urls and sin_esquema(urls[0]["url"]) == sin_esquema(url) and urls[0].get("resultado") == resultado \
            and (nota is None or urls[0].get("nota") == nota):
        return False
    resto = [u for u in urls if sin_esquema(u["url"]) != sin_esquema(url)]
    entrada = {"url": url, "fecha": hoy(), "resultado": resultado}
    if nota:
        entrada["nota"] = nota
    pistas["urls"] = [entrada, *resto[:9]]    # se conservan como máximo 10 pistas
    return True


# ====================================================================================================
# Herramienta: catalogo
# ====================================================================================================
AYUDA_CATALOGO = """
biblioteca.py catalogo — Consultar y mantener el catálogo de documentos.

El catálogo (catalogo.json) dice QUÉ documentos debe tener la biblioteca y
guarda PISTAS para encontrarlos (URLs conocidas, nombre en el índice oficial).
El manifiesto (biblioteca/manifiesto.json) dice QUÉ se tiene ya descargado.
Esta herramienta cruza ambos para que el agente sepa qué hacer.

USO:
  python sistema/biblioteca.py catalogo resumen
      Totales: documentos, descargados, convertidos, pendientes, por grupo.

  python sistema/biblioteca.py catalogo listar [--grupo G] [--entidad E] [--estado S]
                                    [--buscar TEXTO] [--pendientes]
                                    [--sin-verificar DIAS] [--con-aviso] [--limite N] [--solo-ids]
      Lista documentos con filtros. --pendientes = nunca descargados.
      --sin-verificar 30 = no revisados en los últimos 30 días.
      --con-aviso = con un hallazgo de investigación por resolver (abrogada, renombrada...).
      --solo-ids imprime únicamente los ids (uno por línea), para usarlos en un ciclo de shell.

  python sistema/biblioteca.py catalogo ver <id>
      Ficha completa: pistas, URLs conocidas, estado de descarga y conversión.

  python sistema/biblioteca.py catalogo fuentes <entidad|federal> [--resumen | --seccion <nombre>]
      Dónde y cómo buscar en esa entidad (portal oficial, patrones, trampas). El mapa completo pesa
      decenas de KB: --resumen muestra el portal y el tamaño de cada sección; --seccion trae solo una
      (patrones_url, particularidades, notas_de_agentes...).

  python sistema/biblioteca.py catalogo grupos
      Muestra la ESTRUCTURA de la biblioteca (la única válida) con cuántos documentos hay en cada
      carpeta: Constitucion/[Reformas], Federal/Tratados_internacionales/<tema>, Federal/<Materia>
      [/Abrogadas] y Estados/<Estado>. Úsala para saber qué poner en --grupo.

  python sistema/biblioteca.py catalogo marcar <id> --estado ESTADO --nota "texto con fuente" [--sin-pdf]
      Cambia el estado de un documento (y resuelve su AVISO, si tenía). ESTADOS válidos:
        vigente        se debe mantener actualizado (normal)
        abrogado       la ley ya no está vigente; se conserva la última versión
                       (--sin-pdf: se buscó y su texto ya no está en ninguna fuente oficial)
        no_existe      no existe como documento separado (nota obligatoria)
        no_localizado  se buscó y no se encontró fuente oficial (nota obligatoria)

  python sistema/biblioteca.py catalogo agregar --nombre "Nombre oficial" --grupo "Federal/<Materia>" [--url URL]
                                                     [--nota "..."]
  python sistema/biblioteca.py catalogo agregar --nombre "Nombre oficial" --entidad "Estado" [--url URL] [--nota "..."]
      Agrega un documento nuevo al catálogo (p. ej. una ley recién expedida). El grupo debe ser una
      carpeta de la estructura (biblioteca.py catalogo grupos); los estatales van solos a Estados/<Estado>.
      El archivo toma el nombre oficial y el id es <estado>__<archivo> o federal__<archivo>.

  python sistema/biblioteca.py catalogo renombrar <id> --nombre "Nombre oficial nuevo" --nota "decreto y fuente"
      La ley sigue vigente pero cambió de nombre por decreto, o el nombre del catálogo era
      inexacto y hay que dejar el nombre oficial de la portada (el id no cambia; el archivo, su
      PDF y su Markdown toman el nombre nuevo).

  python sistema/biblioteca.py catalogo pista <id> --url URL [--nota "..."] [--quitar]
      Registra una URL como pista (biblioteca.py descargar lo hace solo cuando tiene éxito): va al frente y
      queda "sin_probar". Si la URL ya era pista, solo le pone la --nota (no la mueve ni cambia su
      resultado). Con --quitar la elimina (p. ej. resultó ser de OTRO documento).

  python sistema/biblioteca.py catalogo mover <id> --grupo "Federal/<Materia>"   |   mover --mapa lista.txt
      Cambia un documento de carpeta: mueve su PDF y su Markdown y corrige catálogo y manifiesto (el id
      no cambia). En las materias federales, lo abrogado va solo a <materia>/Abrogadas/ (también al
      marcarlo abrogado con 'marcar').

  python sistema/biblioteca.py catalogo retirar-pdf <id> --nota "por qué"
      Quita un PDF guardado por error (era de otra ley o de una versión equivocada y no hay otra
      fuente). El documento vuelve a estar pendiente (la copia que queda en anteriores/, en la carpeta temporal, se
      borra al cerrar el proceso con biblioteca.py verificar --limpiar).

  python sistema/biblioteca.py catalogo eliminar <id> --nota "por qué"
      Borra del catálogo una entrada que nunca debió existir (duplicada o creada por error). Solo si no
      tiene archivo guardado (si lo tiene, primero retirar-pdf). Queda en la bitácora.

  python sistema/biblioteca.py catalogo nota-fuente <entidad|federal> "lo que aprendiste"
      Deja escrito en el mapa de fuentes algo nuevo de un portal (cambió de ruta,
      bloquea descargas, ahora publica en otro sitio...). El próximo agente lo verá.

  python sistema/biblioteca.py catalogo reporte [--desde "AAAA-MM-DD HH:MM"] [--entidad E] [--grupo G]
      Con --entidad/--grupo solo cuenta lo de ese alcance (varios agentes trabajan en paralelo).
      Resumen de lo hecho (nuevos, actualizados, dudosos, errores, conversiones)
      a partir de la bitácora. Úsalo para el informe final al usuario.
"""


def _dias_desde(fecha: str | None) -> int | None:
    if not fecha:
        return None
    try:
        return (datetime.now() - datetime.strptime(fecha[:10], "%Y-%m-%d")).days
    except ValueError:
        return None


def _situacion(doc: dict, manifiesto: dict) -> str:
    reg = manifiesto.get(doc["id"])
    if doc["estado"] in ("no_existe", "no_localizado"):
        return doc["estado"]
    if not reg and doc.get("sin_pdf"):
        return f"{doc['estado']}, sin texto oficial disponible en línea (confirmado)"
    if not reg:
        return "PENDIENTE (nunca descargado)"
    if not markdown_al_dia(doc, reg):
        return "descargado, falta Markdown"
    return "completo"


def cmd_resumen(args) -> None:
    cat, man = cargar_catalogo(), cargar_manifiesto()
    docs = cat["documentos"]
    por_estado: dict[str, int] = {}
    for d in docs:
        por_estado[d["estado"]] = por_estado.get(d["estado"], 0) + 1
    activos = [d for d in docs if d["estado"] in ("vigente", "abrogado")]
    descargados = [d for d in activos if d["id"] in man]
    sin_texto = [d for d in activos if d.get("sin_pdf") and d["id"] not in man]
    con_md = [d for d in descargados if markdown_al_dia(d, man[d["id"]])]
    viejos = [d for d in descargados if (_dias_desde(man[d["id"]].get("ultima_verificacion")) or 0) > 30]
    print("=== RESUMEN DEL CATÁLOGO ===")
    print(f"Documentos en catálogo: {len(docs)}  " +
          "  ".join(f"{k}={v}" for k, v in sorted(por_estado.items())))
    print(f"Activos (vigente/abrogado): {len(activos)}")
    print(f"  descargados:               {len(descargados)}")
    print(f"  con Markdown al día:       {len(con_md)}")
    print(f"  PENDIENTES de descargar:   {len(activos) - len(descargados) - len(sin_texto)}")
    if sin_texto:
        print(f"  sin texto oficial en línea: {len(sin_texto)} (confirmado; no cuentan como pendientes)")
    print(f"  sin verificar hace >30 d:  {len(viejos)}")
    avisos = [d for d in docs if d.get("aviso")]
    if avisos:
        print(f"Con AVISO por resolver:      {len(avisos)}  (python sistema/biblioteca.py catalogo listar --con-aviso)")
    print("\nPor grupo (descargados / activos):")
    grupos: dict[str, list[int]] = {}
    for d in activos:
        g = grupos.setdefault(d["grupo"], [0, 0, 0])
        g[0] += 1
        g[1] += d["id"] in man
        g[2] += bool(d.get("sin_pdf")) and d["id"] not in man
    for grupo, (a, dsc, sin) in sorted(grupos.items()):
        faltan = a - dsc - sin
        marca = (f"   <- faltan {faltan}" if faltan else "") + (f"   ({sin} sin texto en línea)" if sin else "")
        print(f"  {grupo:60} {dsc:>4}/{a:<4}{marca}")
    salida.poner(documentos=len(docs), por_estado=por_estado, activos=len(activos), descargados=len(descargados),
                 markdown_al_dia=len(con_md), pendientes=len(activos) - len(descargados) - len(sin_texto),
                 sin_texto_oficial=len(sin_texto), sin_verificar_30_dias=len(viejos), con_aviso=len(avisos),
                 por_grupo={g: {"activos": a, "descargados": dsc, "sin_texto_oficial": sin}
                            for g, (a, dsc, sin) in sorted(grupos.items())})


def _coincide(doc, texto):
    t = normalizar(texto)
    return all(p in normalizar(doc["nombre"] + " " + doc["id"]) for p in t.split())


def cmd_listar(args):
    cat, man = cargar_catalogo(), cargar_manifiesto()
    docs = cat["documentos"]
    if args.grupo:
        docs = [d for d in docs if normalizar(args.grupo) in normalizar(d["grupo"])]
    if args.entidad:
        docs = [d for d in docs if normalizar(args.entidad) == normalizar(d.get("entidad") or "federal")]
    if args.estado:
        docs = [d for d in docs if d["estado"] == args.estado]
    if args.buscar:
        docs = [d for d in docs if _coincide(d, args.buscar)]
    if args.pendientes:
        docs = [d for d in docs if pendiente_de_descarga(d, man)]
    if args.sin_verificar is not None:
        docs = [d for d in docs if d["estado"] in ("vigente", "abrogado") and
                (_dias_desde((man.get(d["id"]) or {}).get("ultima_verificacion")) or 10**6) > args.sin_verificar]
    if args.con_aviso:
        docs = [d for d in docs if d.get("aviso")]
    total = len(docs)
    salida.poner(total=total, documentos=[{"id": d["id"], "nombre": d["nombre"], "estado": d["estado"], "grupo": d["grupo"],
                                           "entidad": d.get("entidad"), "situacion": _situacion(d, man),
                                           "verificado": (man.get(d["id"]) or {}).get("ultima_verificacion"),
                                           "aviso": d.get("aviso")} for d in docs])   # con --json van todos, sin --limite
    if args.solo_ids:
        for d in docs:
            print(d["id"])
        return
    for d in docs[: args.limite]:
        reg = man.get(d["id"]) or {}
        estado = "" if d["estado"] == "vigente" else f" ({d['estado']})"
        print(f"{d['id']}\n    {d['nombre']}{estado}  [{d['grupo']}]\n"
              f"    situación: {_situacion(d, man)} | verificado: {reg.get('ultima_verificacion', 'nunca')}"
              + ("  | TIENE AVISO (ver ficha)" if d.get("aviso") else ""))
    if total > args.limite:
        print(f"\n... {total - args.limite} más (usa --limite para ver más).")
    print(f"\nTotal: {total}")


def cmd_ver(args):
    cat, man = cargar_catalogo(), cargar_manifiesto()
    doc = buscar_documento(cat, args.id)
    reg = man.get(doc["id"])
    print(f"ID:        {doc['id']}")
    print(f"Nombre:    {doc['nombre']}")
    print("Ámbito:    " + (f"estatal  | Entidad: {doc['entidad']}" if doc.get("entidad") else "federal"))
    print(f"Grupo:     {doc['grupo']}")
    print(f"Estado:    {doc['estado']}" + (f"  | Nota: {doc['nota']}" if doc.get("nota") else ""))
    if doc.get("aviso"):
        print(f"AVISO:     {doc['aviso']}")
    print(f"Documento: {relativa(ruta_pdf(doc))}")
    print(f"Markdown:  {relativa(ruta_md(doc))}")
    print(f"Situación: {_situacion(doc, man)}")
    pistas = doc.get("pistas", {})
    if pistas.get("indice_diputados"):
        print(f"\nÍndice de Diputados: buscar por nombre \"{pistas['indice_diputados']}\""
              f"  ->  python sistema/biblioteca.py buscar diputados \"{pistas['indice_diputados']}\"")
    if pistas.get("urls"):
        print("\nURLs conocidas (punto de partida, en orden de preferencia; una URL vieja puede seguir "
              "respondiendo con una versión ANTERIOR: si el portal tiene listado o índice, manda el portal):")
        for u in pistas["urls"]:
            print(f"  - {u['url']}  (visto: {u.get('fecha', '?')}, resultado: {u.get('resultado', '?')})")
            if u.get("nota"):
                print(f"      nota: {u['nota']}")
    else:
        print("\nURLs conocidas: ninguna -> hay que buscarla (ver 'fuentes').")
    if reg:
        print("\nÚltima descarga registrada:")
        for k in ("archivo", "formato", "url", "sha256", "bytes", "fecha_reforma", "fecha_descarga", "ultima_verificacion"):
            print(f"  {k:20} {reg.get(k)}")
        md = reg.get("markdown") or {}
        print(f"  {'markdown':20} " + (f"{md.get('metodo', '—')}, {md.get('caracteres', 0):,} caracteres, {md.get('fecha', '')}"
                                       if md else "— (aún no se convierte)"))
    entidad = doc.get("entidad") or "federal"
    print(f"\nDónde buscar: python sistema/biblioteca.py catalogo fuentes \"{entidad}\"")
    salida.poner(documento=doc, registro=reg, situacion=_situacion(doc, man), archivo=relativa(ruta_pdf(doc)),
                 markdown=relativa(ruta_md(doc)), fuentes=entidad)


def cmd_fuentes(args):
    fuentes = cargar_fuentes()
    if not fuentes:
        salir_con_error(f"No existe {FUENTES}")
    clave = normalizar(args.entidad)
    if clave == "federal":
        bloque = fuentes.get("federal")
    else:
        bloque = next((v for k, v in fuentes.get("entidades", {}).items() if normalizar(k) == clave), None)
    if not bloque:
        disponibles = ", ".join(sorted(fuentes.get("entidades", {}))) + ", federal"
        salir_con_error(f"Sin mapa de fuentes para '{args.entidad}'. Disponibles: {disponibles}")
    if args.seccion:
        clave_sec = normalizar(args.seccion).replace(" ", "_")
        sec = {k: v for k, v in bloque.items() if normalizar(k).replace(" ", "_") == clave_sec}
        if not sec:
            salir_con_error(f"Sin sección '{args.seccion}'. Hay: {', '.join(bloque)}")
        _imprimir(sec)
        salida.poner(entidad=args.entidad, fuentes=sec)
    elif args.resumen:
        for k, v in bloque.items():
            tam = len(v) if isinstance(v, (list, dict)) else len(str(v))
            print(f"  {k}: {tam} {'elementos' if isinstance(v, (list, dict)) else 'caracteres'}")
        portal = bloque.get("portal_oficial") or {}
        if portal:
            print("\nPortal oficial:")
            _imprimir({k: portal.get(k) for k in ("nombre", "url", "funciona_hoy")})
        print(f"\nVe cada sección con: python sistema/biblioteca.py catalogo fuentes \"{args.entidad}\" --seccion <nombre>")
        salida.poner(entidad=args.entidad, portal_oficial=portal,
                     secciones={k: (len(v) if isinstance(v, (list, dict)) else len(str(v))) for k, v in bloque.items()})
    else:
        _imprimir(bloque)
        salida.poner(entidad=args.entidad, fuentes=bloque)


def _imprimir(valor, sangria=0):
    pre = "  " * sangria
    if isinstance(valor, dict):
        for k, v in valor.items():
            if isinstance(v, (dict, list)) and v:
                print(f"{pre}{k}:")
                _imprimir(v, sangria + 1)
            elif v not in (None, "", [], {}):
                print(f"{pre}{k}: {v}")
    elif isinstance(valor, list):
        for v in valor:
            if isinstance(v, (dict, list)):
                print(f"{pre}-")
                _imprimir(v, sangria + 1)
            else:
                print(f"{pre}- {v}")
    else:
        print(f"{pre}{valor}")


def cmd_grupos(args):
    """La estructura de la biblioteca (la única válida) con cuántos documentos hay en cada carpeta."""
    cat = cargar_catalogo()
    cuenta = Counter(d["grupo"] for d in cat["documentos"])
    en_uso = set(cuenta)

    def fila(grupo, sangria=0, nota=""):
        print(f"{'  ' * sangria}{grupo.split('/')[-1] + '/':{58 - 2 * sangria}} {cuenta.get(grupo, 0):4d}  {nota}")
        en_uso.discard(grupo)

    print("ESTRUCTURA DE biblioteca/PDF/ y biblioteca/Markdown/ (las mismas carpetas en las dos). Usa estos valores "
          "en --grupo.\n")
    fila(CONSTITUCION, 0, CONSTITUCION)
    for s in SUBGRUPOS_CONSTITUCION:
        fila(f"{CONSTITUCION}/{s}", 1, f"{CONSTITUCION}/{s}")
    print(f"{FEDERAL}/")
    print(f"  {TRATADOS}/")
    for t in TEMAS_TRATADOS:
        fila(f"{FEDERAL}/{TRATADOS}/{t}", 2, f"{FEDERAL}/{TRATADOS}/{t}")
    for m, subcarpetas in MATERIAS.items():
        g = f"{FEDERAL}/{m}"
        fila(g, 1, g)
        hijos = sorted(x for x in en_uso if x.startswith(g + "/"))
        for x in hijos:
            fila(x, x.count("/"), x)
        for patron in subcarpetas:
            print(f"{'':6}(subcarpeta permitida: {patron})")
    print(f"{CARPETA_ESTADOS}/")
    for e in cargar_fuentes().get("entidades", {}):
        g = grupo_de_estado(e)
        fila(g, 1, f"--entidad \"{e}\"")
    print("\nLo abrogado de cada materia federal va en <Materia>/Abrogadas/ (biblioteca.py catalogo marcar lo mueve solo).")
    if en_uso:
        print("\nERROR: grupos FUERA de la estructura (corrígelos con biblioteca.py catalogo mover): "
              + ", ".join(sorted(en_uso)))
    salida.poner(grupos=dict(sorted(cuenta.items())), fuera_de_estructura=sorted(en_uso))


def cmd_marcar(args):
    if args.estado not in ESTADOS_JURIDICOS:
        salir_con_error(f"Estado inválido. Usa uno de: {', '.join(ESTADOS_JURIDICOS)}")
    if args.estado in ("no_existe", "no_localizado", "abrogado") and not args.nota:
        salir_con_error("Ese estado requiere --nota con la justificación y la fuente.")
    if args.sin_pdf and args.estado != "abrogado":
        salir_con_error("--sin-pdf solo aplica a documentos abrogados cuyo texto ya no está en línea "
                              "(un no_existe ya no cuenta como pendiente por sí solo: repite sin --sin-pdf).")
    with bloqueo():
        cat, man = cargar_catalogo(), cargar_manifiesto()
        doc = buscar_documento(cat, args.id)
        anterior = doc["estado"]
        doc["estado"] = args.estado
        # en las materias federales lo abrogado vive en <materia>/Abrogadas/ (y vuelve si revive)
        grupo_antes, movido = doc["grupo"], False
        try:
            movido = mover_documento(doc, grupo_segun_estado(doc["grupo"], args.estado), cat, man)
            if movido:
                print(f"  Movido de {grupo_antes} a {doc['grupo']}")
            if args.nota:
                doc["nota"] = f"{args.nota} (verificado {hoy()})"
            if args.sin_pdf:
                doc["sin_pdf"] = True        # se buscó y no hay texto oficial en línea: deja de ser pendiente
            else:
                doc.pop("sin_pdf", None)
            aviso = doc.pop("aviso", None)   # marcar con nota = el aviso de la investigación quedó resuelto
        finally:                             # archivos ya movidos: catálogo y manifiesto se guardan pase lo que pase
            if movido:
                guardar_manifiesto(man)
            guardar_catalogo(cat)
    bitacora({"herramienta": "catalogo", "accion": "marcar", "id": doc["id"],
                    "de": anterior, "a": args.estado, "nota": args.nota, "aviso_resuelto": aviso})
    cambio = f"{anterior} -> {args.estado}" if anterior != args.estado else f"sigue {args.estado} (nota actualizada)"
    print(f"OK: {doc['id']}: {cambio}" + (" (aviso resuelto)" if aviso else ""))
    salida.poner(id=doc["id"], de=anterior, a=args.estado, grupo=doc["grupo"], movido=movido, aviso_resuelto=bool(aviso))
    if args.estado == "abrogado" and doc["id"] not in cargar_manifiesto() and not args.sin_pdf:
        print("  Aún no hay PDF guardado: busca su última versión y guárdala con biblioteca.py descargar, o repite con "
              "--sin-pdf si su texto ya no está en línea.")


def cmd_agregar(args):
    with bloqueo():
        _agregar(args)


def _agregar(args):
    cat = cargar_catalogo()
    archivo = nombre_archivo(args.nombre)
    if args.entidad:            # estatal: su carpeta es siempre Estados/<Estado>
        entidades = {normalizar(e): e for e in cargar_fuentes().get("entidades", {})}
        if normalizar(args.entidad) not in entidades:
            salir_con_error(f"Entidad desconocida '{args.entidad}'. Usa el nombre de uno de los 32 estados "
                                  "(python sistema/biblioteca.py catalogo grupos).")
        args.entidad = entidades[normalizar(args.entidad)]
        args.grupo = args.grupo or grupo_de_estado(args.entidad)
    elif not args.grupo:
        salir_con_error('Indica --grupo con la carpeta de la estructura (p. ej. "Federal/Fiscal"). '
                              "Carpetas válidas: python sistema/biblioteca.py catalogo grupos")
    args.grupo = args.grupo.strip().strip("/")
    motivo = validar_grupo(args.grupo, args.entidad)
    if motivo:
        salir_con_error(f"'{args.grupo}' no es parte de la estructura de la biblioteca: {motivo}. "
                              "Carpetas válidas: python sistema/biblioteca.py catalogo grupos")
    # id permanente: <estado>__<archivo> o federal__<archivo> (no depende de la carpeta, que puede cambiar)
    base = re.sub(r"[^a-z0-9_]", "", ((nombre_archivo(args.entidad) if args.entidad else "federal")
                                      + "__" + archivo).lower())
    if base in {d["id"] for d in cat["documentos"]}:
        salir_con_error(f"Ya existe '{base}'. Revisa con: python sistema/biblioteca.py catalogo ver {base}")
    igual = next((d for d in cat["documentos"] if d["archivo"] == archivo
                  and (d.get("entidad") or None) == (args.entidad or None)), None)
    if igual:
        salir_con_error(f"Ya existe ese documento: {igual['id']} (en {igual['grupo']}). Revisa con: "
                              f"python sistema/biblioteca.py catalogo ver {igual['id']}")
    doc = {"id": base, "nombre": args.nombre, "grupo": args.grupo, "archivo": archivo, "estado": "vigente",
           "pistas": {"urls": []}}
    if args.entidad:
        doc["entidad"] = args.entidad
    if args.nota:
        doc["nota"] = args.nota
    if args.url:
        doc["pistas"]["urls"].append({"url": args.url, "fecha": hoy(), "resultado": "sin_probar"})
        if not args.entidad and "diputados.gob.mx/leyesbiblio/" in args.url.lower():
            # federal del índice de Diputados: biblioteca.py buscar diputados "<nombre>" dará siempre la URL vigente
            doc["pistas"]["indice_diputados"] = args.nombre
    cat["documentos"].append(doc)
    guardar_catalogo(cat)
    bitacora({"herramienta": "catalogo", "accion": "agregar", "id": base, "nombre": args.nombre,
                    "url": args.url, "nota": args.nota})
    print(f"OK: agregado '{base}'. Descárgalo con: python sistema/biblioteca.py descargar {base} <URL>")
    salida.poner(id=base, nombre=args.nombre, grupo=args.grupo, entidad=args.entidad)


def cmd_mover(args):
    """Cambia documentos de carpeta (grupo): mueve su PDF y su Markdown y corrige catálogo y manifiesto.
    El id no cambia. Con --mapa se mueven muchos de una vez (líneas 'id grupo')."""
    pares = []
    if args.mapa:
        if not Path(args.mapa).is_file():
            salir_con_error(f"No existe el archivo {args.mapa}")
        for linea in Path(args.mapa).read_text(encoding="utf-8-sig").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                partes = linea.split(None, 1)
                if len(partes) != 2:
                    salir_con_error(f"Línea inválida en {args.mapa} (debe ser 'id grupo'): {linea[:80]}")
                pares.append((partes[0], partes[1].strip().strip("/")))
    if args.id:
        if not args.grupo:
            salir_con_error("Indica --grupo \"Federal/<Materia>\" (o usa --mapa archivo)")
        pares.append((args.id, args.grupo.strip().strip("/")))
    if not pares:
        salir_con_error("Indica un id con --grupo, o --mapa archivo")
    movidos = 0
    with bloqueo():
        cat, man = cargar_catalogo(), cargar_manifiesto()
        try:            # aunque uno falle, lo ya movido queda registrado (archivos y catálogo coinciden)
            for doc_id, grupo in pares:
                doc = buscar_documento(cat, doc_id)
                destino = grupo_segun_estado(grupo, doc["estado"])
                antes, archivo_antes = doc["grupo"], doc["archivo"]
                # también deja el archivo con el nombre oficial (corrige nombres de archivo viejos)
                if mover_documento(doc, destino, cat, man, nombre_archivo(doc["nombre"])):
                    movidos += 1
                    bitacora({"herramienta": "catalogo", "accion": "mover", "id": doc_id, "de": antes,
                                    "a": destino, "nota": args.nota})
                    if not args.mapa:
                        print(f"OK: {doc_id}: {antes} -> {destino}"
                              + (f" (archivo: {archivo_antes} -> {doc['archivo']})" if doc["archivo"] != archivo_antes else ""))
        finally:
            guardar_catalogo(cat)
            guardar_manifiesto(man)
    print(f"OK: {movidos} documento(s) movido(s) de carpeta ({len(pares) - movidos} ya estaban en su lugar).")
    salida.poner(movidos=movidos, total=len(pares))


def cmd_renombrar(args):
    """La ley sigue vigente pero cambió de nombre oficial por decreto (p. ej. '... Vida Libre de
    Violencia' -> '... Vida Libre de Violencias'), o el nombre del catálogo era inexacto y se corrige
    al de la portada. El id no cambia (así no se rompen referencias).
    El archivo también toma el nombre nuevo: si ya se descargó, se renombran su PDF, su Markdown y
    la ficha del manifiesto."""
    with bloqueo():
        cat, man = cargar_catalogo(), cargar_manifiesto()
        doc = buscar_documento(cat, args.id)
        anterior = doc["nombre"]
        doc["nombre"] = args.nombre
        doc.setdefault("nombres_anteriores", []).append(anterior)
        movido = False
        try:
            # el archivo sigue siempre al nombre oficial (el documento en PDF/ y su Markdown en Markdown/)
            movido = mover_documento(doc, doc["grupo"], cat, man, nombre_archivo(args.nombre))
            if doc.get("pistas", {}).get("indice_diputados"):
                doc["pistas"]["indice_diputados"] = args.nombre
            doc["nota"] = f"Antes: '{anterior}'. {args.nota} (verificado {hoy()})"
            aviso = doc.pop("aviso", None)
        finally:
            if movido:
                guardar_manifiesto(man)
            guardar_catalogo(cat)
    bitacora({"herramienta": "catalogo", "accion": "renombrar", "id": doc["id"],
                    "de": anterior, "a": args.nombre, "nota": args.nota, "aviso_resuelto": aviso})
    print(f"OK: {doc['id']}: '{anterior}' -> '{args.nombre}' (archivo: {doc['archivo']})")
    print(f"  El id NO cambia: sigue siendo {doc['id']} (úsalo así en biblioteca.py descargar y demás comandos).")
    salida.poner(id=doc["id"], de=anterior, a=args.nombre, archivo=doc["archivo"], movido=movido)


def cmd_pista(args):
    with bloqueo():
        cat = cargar_catalogo()
        doc = buscar_documento(cat, args.id)
        if args.quitar:
            urls = doc.get("pistas", {}).get("urls", [])
            quedan = [u for u in urls if sin_esquema(u["url"]) != sin_esquema(args.url)]
            if len(quedan) == len(urls):
                salir_con_error(f"{doc['id']} no tiene la pista {args.url}")
            doc["pistas"]["urls"] = quedan
        else:
            urls = doc.setdefault("pistas", {}).setdefault("urls", [])
            existente = next((u for u in urls if sin_esquema(u["url"]) == sin_esquema(args.url)), None)
            if existente is not None:
                # ya conocida: solo se le pone la nota; su lugar y su resultado los deciden las descargas
                if args.nota:
                    existente["nota"] = args.nota
            else:
                registrar_pista(doc, args.url, "sin_probar", args.nota)
        guardar_catalogo(cat)
    bitacora({"herramienta": "catalogo", "accion": "quitar-pista" if args.quitar else "pista",
                    "id": doc["id"], "a": args.url, "nota": args.nota})
    print(f"OK: pista {'quitada' if args.quitar else 'registrada'} para {doc['id']}")
    salida.poner(id=doc["id"], url=args.url, accion="quitar-pista" if args.quitar else "pista")


def cmd_retirar_pdf(args):
    """Quita de la biblioteca un PDF guardado por error (era de otra ley o de una versión equivocada y no
    hay otra fuente). El PDF se conserva en anteriores/ (carpeta temporal) y el documento vuelve a quedar pendiente
    (o márcalo después con `marcar`). Su Markdown, si existe, se borra porque ya no corresponde."""
    with bloqueo():
        cat, man = cargar_catalogo(), cargar_manifiesto()
        doc = buscar_documento(cat, args.id)
        pdf, md = ruta_pdf(doc), ruta_md(doc)
        if doc["id"] not in man and not pdf.exists():
            salir_con_error(f"{doc['id']} no tiene PDF guardado.")
        respaldo = ANTERIORES / f"{doc['id']}.retirado{pdf.suffix}"
        if pdf.exists():
            respaldo.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf), str(respaldo))   # puede ser otro disco: no basta un rename
        md.unlink(missing_ok=True)
        man.pop(doc["id"], None)
        guardar_manifiesto(man)
        doc["nota"] = f"PDF retirado: {args.nota} (verificado {hoy()})"
        guardar_catalogo(cat)
    bitacora({"herramienta": "catalogo", "accion": "retirar-pdf", "id": doc["id"], "nota": args.nota})
    print(f"OK: PDF de {doc['id']} retirado (copia en {relativa(respaldo)}). Vuelve a descargarlo desde la "
          "fuente correcta o márcalo con biblioteca.py catalogo marcar.")
    salida.poner(id=doc["id"], respaldo=relativa(respaldo))


def cmd_eliminar(args):
    """Borra del catálogo una entrada que nunca debió existir (duplicada o creada por error). Solo si no
    tiene archivo guardado ni registro en el manifiesto: si lo tiene, primero `retirar-pdf`."""
    with bloqueo():
        cat, man = cargar_catalogo(), cargar_manifiesto()
        doc = buscar_documento(cat, args.id)
        if doc["id"] in man or ruta_pdf(doc).exists() or ruta_md(doc).exists():
            salir_con_error(f"{doc['id']} tiene archivos guardados: primero python sistema/biblioteca.py catalogo "
                                  f"retirar-pdf {doc['id']} --nota \"...\"")
        cat["documentos"] = [d for d in cat["documentos"] if d["id"] != doc["id"]]
        guardar_catalogo(cat)
    bitacora({"herramienta": "catalogo", "accion": "eliminar", "id": doc["id"], "nombre": doc["nombre"],
                    "grupo": doc["grupo"], "estado_previo": doc["estado"], "nota": args.nota})
    print(f"OK: '{doc['id']}' eliminado del catálogo ({doc['nombre']}).")
    salida.poner(id=doc["id"], nombre=doc["nombre"], grupo=doc["grupo"])


def cmd_nota_fuente(args):
    """Deja por escrito algo aprendido de un portal (p. ej. 'cambió la ruta de las leyes a /x/y').
    Así el siguiente agente no tiene que redescubrirlo."""
    with bloqueo():
        fuentes = cargar_fuentes()
        clave = normalizar(args.entidad)
        if clave == "federal":
            bloque = fuentes.setdefault("federal", {})
        else:
            entidades = fuentes.setdefault("entidades", {})
            nombre = next((k for k in entidades if normalizar(k) == clave), None)
            if not nombre:
                salir_con_error(f"Entidad desconocida '{args.entidad}'. Revisa: "
                                "python sistema/biblioteca.py catalogo grupos")
            bloque = entidades[nombre]
        texto = re.sub(r"^\s*\d{4}-\d{2}-\d{2}\s*:\s*", "", args.texto.strip())   # la fecha la pone la herramienta
        bloque.setdefault("notas_de_agentes", []).insert(0, f"{hoy()}: {texto}")
        fuentes["actualizado"] = hoy()
        guardar_json(FUENTES, fuentes)
    bitacora({"herramienta": "catalogo", "accion": "nota-fuente", "entidad": args.entidad, "texto": args.texto})
    print(f"OK: nota registrada en el mapa de fuentes de {args.entidad}")
    salida.poner(entidad=args.entidad, texto=texto)


def cmd_reporte(args):
    """Resume lo que hicieron las herramientas desde una fecha (para el informe final)."""
    if not BITACORA.exists():
        print("La bitácora está vacía.")
        salida.poner(revisados=0, conteo={})
        return
    desde = args.desde or hoy()
    ambito = {}          # id -> (entidad o 'federal', grupo), para filtrar por alcance
    if args.entidad or args.grupo:
        ambito = {d["id"]: (normalizar(d.get("entidad") or "federal"), normalizar(d["grupo"]))
                  for d in cargar_catalogo()["documentos"]}
    eventos = []
    with open(BITACORA, encoding="utf-8") as f:
        for linea in f:
            try:
                e = json.loads(linea)
            except ValueError:
                continue
            if e.get("fecha", "") < desde:
                continue
            if ambito:
                entidad, grupo = ambito.get(e.get("id"), (normalizar(e.get("entidad") or ""), ""))
                if args.entidad and entidad != normalizar(args.entidad):
                    continue
                if args.grupo and normalizar(args.grupo) not in grupo:
                    continue
            eventos.append(e)
    descargas = [e for e in eventos if e.get("herramienta") == "descargar"]
    exitos = ("NUEVO", "ACTUALIZADO", "SIN_CAMBIO", "SIN_CAMBIO_REAL")
    ultimo = {}
    for e in descargas:
        # Resultado final de cada documento: el último ÉXITO si hubo alguno (los intentos
        # descartados antes o después no cambian lo que quedó en la biblioteca); si no, el último intento.
        previo = ultimo.get(e["id"])
        if previo and previo["resultado"] in exitos and e["resultado"] not in exitos:
            continue
        if previo and previo["resultado"] in ("NUEVO", "ACTUALIZADO") and e["resultado"] in ("SIN_CAMBIO", "SIN_CAMBIO_REAL"):
            continue        # volver a verificar lo recién guardado no cambia lo que pasó en la corrida
        ultimo[e["id"]] = e
    conteo = {}
    for e in ultimo.values():
        conteo[e["resultado"]] = conteo.get(e["resultado"], 0) + 1
    conv = [e for e in eventos if e.get("herramienta") == "convertir"]
    alcance = (f" | entidad: {args.entidad}" if args.entidad else "") + (f" | grupo: {args.grupo}" if args.grupo else "")
    print(f"=== REPORTE DESDE {desde}{alcance} ===")
    print(f"Documentos revisados: {len(ultimo)}  " + "  ".join(f"{k}={v}" for k, v in sorted(conteo.items())))
    for tipo in ("ACTUALIZADO", "NUEVO", "DUDOSO", "ERROR"):
        ids = [e for e in ultimo.values() if e["resultado"] == tipo]
        if ids:
            print(f"\n{tipo} ({len(ids)}):")
            for e in ids:
                extra = e.get("fecha_reforma") if tipo in ("ACTUALIZADO", "NUEVO") else e.get("detalle", "")[:120]
                print(f"  - {e['id']}  {extra or ''}")
    if conv:
        ok = sum(1 for e in conv if e.get("resultado") == "OK")
        print(f"\nConversiones a Markdown: {ok} OK, {len(conv) - ok} con falla")
    salida.poner(desde=desde, entidad=args.entidad, grupo=args.grupo, revisados=len(ultimo), conteo=conteo,
                 documentos={tipo: [{"id": e["id"], "fecha_reforma": e.get("fecha_reforma"), "detalle": e.get("detalle")}
                                    for e in ultimo.values() if e["resultado"] == tipo]
                             for tipo in ("NUEVO", "ACTUALIZADO", "SIN_CAMBIO", "SIN_CAMBIO_REAL", "DUDOSO", "ERROR")},
                 conversiones={"ok": sum(1 for e in conv if e.get("resultado") == "OK"),
                               "fallas": sum(1 for e in conv if e.get("resultado") != "OK")},
                 cambios_catalogo=[{k: e.get(k) for k in ("accion", "id", "entidad", "de", "a", "nombre", "nota", "texto")}
                                   for e in eventos if e.get("herramienta") == "catalogo"])
    otros = [e for e in eventos if e.get("herramienta") == "catalogo"]
    if otros:
        print(f"\nCambios al catálogo/fuentes ({len(otros)}):")
        for e in otros:
            detalle = e.get("a") or (f"'{e['nombre']}'" if e.get("accion") == "agregar" and e.get("nombre") else "")
            print(f"  - {e.get('accion')}: {e.get('id') or e.get('entidad')} {detalle} "
                  f"{e.get('nota') or e.get('texto') or ''}"[:200])


def main():
    p = Analizador(description="Consultar y mantener el catálogo de documentos.",
                         formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_CATALOGO)
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("resumen").set_defaults(fn=cmd_resumen)
    li = sub.add_parser("listar")
    li.add_argument("--grupo")
    li.add_argument("--entidad")
    li.add_argument("--estado", choices=ESTADOS_JURIDICOS)
    li.add_argument("--buscar")
    li.add_argument("--pendientes", action="store_true")
    li.add_argument("--sin-verificar", type=int, dest="sin_verificar")
    li.add_argument("--con-aviso", action="store_true", dest="con_aviso",
                    help="solo documentos con un AVISO de investigación por resolver")
    li.add_argument("--limite", type=int, default=60)
    li.add_argument("--solo-ids", action="store_true", dest="solo_ids",
                    help="imprime solo los ids, uno por línea (para usarlos en un ciclo)")
    li.set_defaults(fn=cmd_listar)
    v = sub.add_parser("ver")
    v.add_argument("id")
    v.set_defaults(fn=cmd_ver)
    f = sub.add_parser("fuentes")
    f.add_argument("entidad")
    f.add_argument("--resumen", action="store_true",
                   help="solo el portal oficial y los nombres de las secciones (con su tamaño)")
    f.add_argument("--seccion", help="solo esa sección (p. ej. patrones_url, particularidades, notas_de_agentes)")
    f.set_defaults(fn=cmd_fuentes)
    sub.add_parser("grupos").set_defaults(fn=cmd_grupos)
    m = sub.add_parser("marcar")
    m.add_argument("id")
    m.add_argument("--estado", required=True)
    m.add_argument("--nota")
    m.add_argument("--sin-pdf", action="store_true", dest="sin_pdf",
                   help="abrogado cuyo texto ya no está en ninguna fuente oficial en línea (deja de ser pendiente)")
    m.set_defaults(fn=cmd_marcar)
    a = sub.add_parser("agregar")
    a.add_argument("--nombre", required=True)
    a.add_argument("--grupo", help='carpeta de la estructura (federales); los estatales van solos a Estados/<Estado>')
    a.add_argument("--entidad")
    a.add_argument("--url")
    a.add_argument("--nota")
    a.set_defaults(fn=cmd_agregar)
    mv = sub.add_parser("mover")
    mv.add_argument("id", nargs="?")
    mv.add_argument("--grupo", help='carpeta nueva, p. ej. "Federal/Fiscal"')
    mv.add_argument("--mapa", help="archivo con líneas 'id grupo' para mover muchos de una vez")
    mv.add_argument("--nota")
    mv.set_defaults(fn=cmd_mover)

    rn = sub.add_parser("renombrar")
    rn.add_argument("id")
    rn.add_argument("--nombre", required=True, help="nombre oficial nuevo, tal como aparece en la fuente")
    rn.add_argument("--nota", required=True, help="qué decreto lo cambió y la fuente")
    rn.set_defaults(fn=cmd_renombrar)
    pi = sub.add_parser("pista")
    pi.add_argument("id")
    pi.add_argument("--url", required=True)
    pi.add_argument("--nota")
    pi.add_argument("--quitar", action="store_true",
                    help="quitar esa URL de las pistas (p. ej. resultó ser de OTRO documento)")
    pi.set_defaults(fn=cmd_pista)
    rp = sub.add_parser("retirar-pdf")
    rp.add_argument("id")
    rp.add_argument("--nota", required=True, help="por qué se retira (p. ej. era la ley anterior)")
    rp.set_defaults(fn=cmd_retirar_pdf)

    el = sub.add_parser("eliminar")
    el.add_argument("id")
    el.add_argument("--nota", required=True, help="por qué se elimina (p. ej. duplicado de <id>)")
    el.set_defaults(fn=cmd_eliminar)
    nf = sub.add_parser("nota-fuente")
    nf.add_argument("entidad")
    nf.add_argument("texto")
    nf.set_defaults(fn=cmd_nota_fuente)
    r = sub.add_parser("reporte")
    r.add_argument("--desde", help="AAAA-MM-DD o 'AAAA-MM-DD HH:MM' (por defecto: hoy)")
    r.add_argument("--entidad", help="solo los documentos de esa entidad (o 'federal')")
    r.add_argument("--grupo", help="solo los documentos de ese grupo (basta una parte del nombre)")
    r.set_defaults(fn=cmd_reporte)
    json_tambien_al_final(sub)
    args = p.parse_args()
    salida.iniciar(args.json, "catalogo")
    args.fn(args)
