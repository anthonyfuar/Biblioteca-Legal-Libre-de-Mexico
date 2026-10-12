"""
verificar.py — Integridad y limpieza (`verificar`), copias de .claude/ desde .agents/ (`sincronizar`) y pruebas del
código (`pruebas`).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from herramientas.catalogo import (
    FORMATOS,
    markdown_al_dia,
    pendiente_de_descarga,
    problema_de_estructura,
    problemas_de_esquema_doc,
    problemas_de_esquema_registro,
    ruta_md,
    ruta_pdf,
    ruta_relativa_md,
    ruta_relativa_pdf,
)
from herramientas.comun import (
    CACHE,
    CARPETA_MARKDOWN,
    CARPETA_PDF,
    DOCUMENTOS,
    LIMITE_GITHUB_MB,
    RAIZ,
    TEMP_PROYECTO,
    TRABAJO,
    UMBRAL_COMPRIMIR_MB,
    Analizador,
    bloqueo,
    borrar_huerfanos,
    cargar_catalogo,
    cargar_fuentes,
    cargar_manifiesto,
    contar_archivos,
    inesperados,
    limpiar_temporales,
    relativa,
    salida,
    salir_con_error,
    sha256_archivo,
    temporales_en_proyecto,
)
from herramientas.descargar import MIN_CARACTERES_MD, FaltaDependencia, documento_valido
from herramientas.publicar import archivos_de, comprobar_sumas

# ====================================================================================================
# Herramienta: sincronizar_editores
# ====================================================================================================
AYUDA_SINCRONIZAR = """
biblioteca.py sincronizar — Copia los agentes y comandos originales de .agents/ al formato de Claude Code.

Los ORIGINALES viven en la carpeta estándar .agents/ (la pueden leer directamente otros agentes de IA que sigan
ese estándar):

  .agents/skills/<comando>/SKILL.md  comandos: instalar, actualizar, convertir, comprimir, publicar
  .agents/agents/<agente>.md         agentes: instalador, actualizador, convertidor, compresor, publicador

Claude Code usa su propia carpeta, así que esta herramienta genera copias (con la marca "GENERADO"; no las edites):

  .claude/agents/<agente>.md          subagentes de Claude Code
  .claude/skills/<comando>/SKILL.md   comandos /instalar, /actualizar, /convertir, /comprimir, /publicar

USO:
  python biblioteca.py sincronizar             regenera las copias
  python biblioteca.py sincronizar --revisar   solo revisa que estén al día (código 1 si no)

Cuándo usarlo: después de editar cualquier archivo de .agents/ (biblioteca.py verificar lo comprueba). Solo biblioteca estándar.
"""


AGENTES = RAIZ / ".agents" / "agents"
COMANDOS = RAIZ / ".agents" / "skills"
MARCA = "GENERADO por biblioteca.py sincronizar"
ORIGINAL = "<!-- ORIGINAL (fuente única)."

# Pista de argumentos que Claude Code muestra al escribir /comando (el estándar .agents no la define)
PISTAS_ARGUMENTOS = {
    "actualizar": "[todo | federal | pendientes | <estado> | <nombre de ley>]",
    "convertir": "[todo | <estado> | <grupo> | <id> ...]",
    "comprimir": "[10 | 30 | 50 | 80] [<id> ...]",
    "instalar": "[completo | sin-ocr]",
    "publicar": "[<URL del repositorio>]",
}


# ---------------------------------------------------------------------------
# Lectura de los originales
# ---------------------------------------------------------------------------
class OriginalInvalido(Exception):
    """Un original de .agents/ falta o tiene mal el encabezado: no se pueden generar sus copias."""


def leer(ruta: Path, nombre_esperado: str) -> tuple[dict, str]:
    """Separa el encabezado (--- clave: valor ---) del cuerpo."""
    texto = ruta.read_text(encoding="utf-8").replace("\r\n", "\n")
    fin = texto.find("\n---\n", 4)
    if not texto.startswith("---\n") or fin < 0:
        raise OriginalInvalido(f"{ruta} debe empezar con un encabezado entre líneas '---' (name, description)")
    campos = {}
    for linea in texto[4:fin].splitlines():
        clave, _, valor = linea.partition(":")
        valor = valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] == '"':
            valor = valor[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        campos[clave.strip()] = valor
    for requerido in ("name", "description"):
        if not campos.get(requerido):
            raise OriginalInvalido(f"a {ruta} le falta '{requerido}' en el encabezado")
    if campos["name"] != nombre_esperado:
        raise OriginalInvalido(f"en {ruta} el name '{campos['name']}' debe ser '{nombre_esperado}'")
    cuerpo = texto[fin + 5:].lstrip("\n")
    if cuerpo.startswith(ORIGINAL):                 # la nota de "original" no se copia
        cuerpo = cuerpo.split("\n", 1)[1].lstrip("\n") if "\n" in cuerpo else ""
    return campos, cuerpo


def yaml_texto(valor: str) -> str:
    return '"' + valor.replace("\\", "\\\\").replace('"', '\\"') + '"'


def origen_de(ruta: Path) -> str:
    return ruta.relative_to(RAIZ).as_posix()


def markdown(encabezado: list[tuple[str, str]], origen: str, cuerpo: str) -> str:
    lineas = ["---"] + [f"{k}: {v}" for k, v in encabezado] + ["---"]
    aviso = f"<!-- {MARCA} desde {origen}. No lo edites aquí: edita {origen} y vuelve a generar. -->"
    return "\n".join(lineas) + "\n" + aviso + "\n\n" + cuerpo.rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Qué copias produce cada original
# ---------------------------------------------------------------------------
def copias_de_agente(ruta: Path) -> dict[str, str]:
    campos, cuerpo = leer(ruta, ruta.stem)
    n, d = campos["name"], yaml_texto(campos["description"])
    origen = origen_de(ruta)
    return {f".claude/agents/{n}.md": markdown([("name", n), ("description", d)], origen, cuerpo)}


def copias_de_comando(ruta: Path) -> dict[str, str]:
    campos, cuerpo = leer(ruta, ruta.parent.name)
    n, d = campos["name"], yaml_texto(campos["description"])
    origen = origen_de(ruta)
    pista = PISTAS_ARGUMENTOS.get(n)
    claude = [("name", n), ("description", d), *([("argument-hint", yaml_texto(pista))] if pista else [])]
    # Claude Code agrega solo "ARGUMENTS: ..." cuando el cuerpo no usa $ARGUMENTS
    return {f".claude/skills/{n}/SKILL.md": markdown(claude, origen, cuerpo)}


CARPETAS_GENERADAS = [".claude/agents", ".claude/skills"]


def esperado() -> dict[str, str]:
    if not AGENTES.exists() or not COMANDOS.exists():
        raise OriginalInvalido(f"faltan los originales en {AGENTES} y {COMANDOS}")
    salida = {}
    for ruta in sorted(AGENTES.glob("*.md")):
        salida.update(copias_de_agente(ruta))
    for ruta in sorted(COMANDOS.glob("*/SKILL.md")):
        salida.update(copias_de_comando(ruta))
    return salida


def sobrantes(generados: dict[str, str]) -> list[Path]:
    """Copias con la marca GENERADO cuyo original ya no existe (p. ej. un agente que se borró)."""
    fuera = []
    for carpeta in CARPETAS_GENERADAS:
        base = RAIZ / carpeta
        if not base.exists():
            continue
        for archivo in base.rglob("*"):
            relativo = archivo.relative_to(RAIZ).as_posix()
            if archivo.is_file() and relativo not in generados and MARCA in archivo.read_text(encoding="utf-8", errors="ignore"):
                fuera.append(archivo)
    return fuera


def copias_desactualizadas() -> list[str]:
    """Copias de .claude/ que no coinciden con su original de .agents/ (o que sobran)."""
    generados = esperado()
    distintas = [r for r, contenido in generados.items()
                 if not (RAIZ / r).exists() or (RAIZ / r).read_text(encoding="utf-8").replace("\r\n", "\n") != contenido]
    return distintas + [a.relative_to(RAIZ).as_posix() for a in sobrantes(generados)]


def error_de_copias() -> str | None:
    """El ERROR de verificar sobre las copias de .claude/: desactualizadas, o imposibles de generar porque un original
    de .agents/ falta o está mal formado. None si están al día."""
    try:
        if copias_desactualizadas():
            return ("las copias de .claude/agents/ y .claude/skills/ no coinciden con los originales de .agents/: "
                    "ejecuta python biblioteca.py sincronizar")
    except (OriginalInvalido, OSError, ValueError) as e:
        return (f"no se pueden generar las copias de .claude/ desde .agents/ ({e}): corrige el original y ejecuta "
                "python biblioteca.py sincronizar")
    return None


def tipo_de_archivo(rel: str) -> str:
    """Qué es un archivo de documentos/ según su ruta relativa (con /): 'documento' (PDF o Word dentro de PDF/),
    'markdown' (dentro de Markdown/), 'dato' (el manifiesto o el catálogo de la raíz), 'web' (una página web: la
    biblioteca solo guarda PDF o Word) u 'otro' (cualquier otra cosa, o algo fuera de su carpeta: no debe publicarse)."""
    nombre = rel.rsplit("/", 1)[-1]
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    if rel in ("manifiesto.json", "catalogo.json"):
        return "dato"
    if extension in ("html", "htm"):
        return "web"
    if rel.startswith(f"{CARPETA_MARKDOWN}/") and extension == "md":
        return "markdown"
    if rel.startswith(f"{CARPETA_PDF}/") and extension in FORMATOS:
        return "documento"
    return "otro"


def enlaces_simbolicos(base: Path) -> list[Path]:
    """Enlaces simbólicos dentro de `base` (sin seguirlos): git los publicaría tal cual y SHA256SUMS no los cubre."""
    return [Path(carpeta) / nombre for carpeta, subcarpetas, archivos in os.walk(base)
            for nombre in subcarpetas + archivos if (Path(carpeta) / nombre).is_symlink()]


def main_sincronizar():
    p = Analizador(description="Genera las copias de .claude/ desde los originales de .agents/.",
                   formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_SINCRONIZAR)
    p.add_argument("--revisar", action="store_true", help="solo revisar, no escribir nada")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    args = p.parse_args()
    salida.iniciar(args.json, "sincronizar")

    try:
        generados = esperado()
    except (OriginalInvalido, OSError, ValueError) as e:
        salir_con_error(str(e))
    distintos = []
    for relativo, contenido in generados.items():
        ruta = RAIZ / relativo
        actual = ruta.read_text(encoding="utf-8").replace("\r\n", "\n") if ruta.exists() else None
        if actual == contenido:
            continue
        distintos.append(relativo)
        if not args.revisar:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            with open(ruta, "w", encoding="utf-8", newline="\n") as f:
                f.write(contenido)
            print(f"  escrito     {relativo}")
    viejos = sobrantes(generados)
    for archivo in viejos:
        relativo = archivo.relative_to(RAIZ).as_posix()
        if args.revisar:
            print(f"  sobrante    {relativo}")
        else:
            archivo.unlink()
            if not any(archivo.parent.iterdir()):
                archivo.parent.rmdir()
            print(f"  borrado     {relativo} (su original ya no existe)")

    salida.poner(total=len(generados), desactualizadas=distintos, sobrantes=[a.relative_to(RAIZ).as_posix() for a in viejos],
                 revisar=args.revisar)
    if args.revisar:
        for relativo in distintos:
            print(f"  desactualizado  {relativo}")
        if distintos or viejos:
            print(f"\nHay {len(distintos) + len(viejos)} copia(s) por regenerar: python biblioteca.py sincronizar")
            salida.cerrar(1)
            sys.exit(1)
        print(f"Todo al día: {len(generados)} copias coinciden con los originales de .agents/.")
        salida.cerrar(0)
        return
    print(f"\nListo: {len(distintos)} escrita(s), {len(viejos)} borrada(s), "
          f"{len(generados) - len(distintos)} ya estaban al día ({len(generados)} en total).")
    salida.cerrar(0)


# ====================================================================================================
# Herramienta: verificar
# ====================================================================================================
AYUDA_VERIFICAR = """
biblioteca.py verificar — Revisa la integridad completa de la biblioteca.

Se ejecuta SIEMPRE al terminar cada proceso (y al empezar, para ver el estado). Comprueba:

  ERRORES (hay que corregirlos antes de terminar):
    - PDFs que no son PDF real (firma %PDF) o están dañados. En la biblioteca también pueden ir, con su extensión,
      los pocos documentos que la fuente oficial publica solo en Word (.doc/.docx): se revisa que el archivo sea de
      verdad eso y que cada documento tenga UN solo archivo. Una página web (.html) es ERROR: solo PDF o Word.
    - Documento cuya huella SHA-256 no coincide con el manifiesto (alguien lo cambió a mano).
    - Entradas del catálogo o del manifiesto que no cumplen el esquema (referencia/esquema.md).
    - Markdown sin su PDF, o PDF/Markdown que no pertenece a ningún documento del catálogo, y cualquier otro
      archivo en documentos/ que no sea PDF, Markdown, manifiesto, catálogo o SHA256SUMS (todo lo que hay
      ahí se publica tal cual).
    - Markdown vacío o casi vacío (conversión fallida).
    - Markdown o JSON de documentos/ con finales de línea CRLF: git los publica con LF (.gitattributes), así
      que su huella en el repositorio no coincidiría con la de SHA256SUMS ni con la del disco.
    - Documentos del manifiesto cuyo PDF desapareció.
    - Catálogo con ids o archivos repetidos.
    - PDF comprimidos cuya ficha no tiene las dos huellas (original y comprimido) o no coinciden.
    - Temporales que quedaron en el proyecto (*.tmp y *.pyc de más de 10 minutos, __pycache__...).
      (el archivo del candado NO es temporal: es el archivo del candado.)
    - Con --final, también los archivos de trabajo de trabajo/. Sin --final salen como AVISO,
      porque mientras varios agentes trabajan en paralelo es normal que haya archivos de trabajo de otros.

  AVISOS (revísalos): PDF de más de 60 MB sin comprimir (PROCESO 4, biblioteca.py comprimir); documentos/SHA256SUMS
  desactualizado (lo regenera biblioteca.py publicar --preparar); y archivos o carpetas que no son parte del proyecto en
  su raíz (suelen ser restos de un agente). No se borran solos: podrían ser de una persona.

  PENDIENTES (no son errores; son el trabajo que falta):
    - Documentos del catálogo nunca descargados.
    - PDFs sin Markdown o con Markdown desactualizado.
    - Documentos no verificados en más de N días (--dias, por defecto 30).


USO:
  python biblioteca.py verificar            revisión completa
  python biblioteca.py verificar --dias 60  cambia el umbral de "sin verificar"
  python biblioteca.py verificar --limpiar  borra primero TODO lo temporal: los archivos de trabajo
                                         del agente (trabajo/), restos *.tmp, __pycache__, .DS_Store,
                                         cachés de desarrollo, carpetas vacías de documentos/ y la carpeta
                                         temporal del sistema de este proyecto (copias de los documentos
                                         reemplazados, caché y ejecuciones). Úsalo SIEMPRE al TERMINAR
                                         cada proceso (no a la mitad, si hay otros agentes trabajando). Al
                                         final solo queda el proyecto: la biblioteca, las fuentes y la bitácora.

Las herramientas nunca dejan temporales en el proyecto: cada ejecución usa su propia carpeta en la
carpeta temporal del sistema y la borra sola al terminar (si un proceso muere de golpe, su carpeta
se borra en cuanto cualquier herramienta (incluido verificar) detecta que ese proceso ya no existe). La
única excepción es el *.tmp momentáneo con que se guardan catalogo.json, fuentes.json y el manifiesto.
Los archivos de inspección del agente van en trabajo/ y se borran al final con --limpiar
(con --final o --limpiar cuentan como ERROR; sin ellos, solo AVISO, porque otros agentes pueden seguir
trabajando).

Código de salida: 0 = sin errores (puede haber pendientes); 1 = hay errores.
"""


LIMITE_COMMIT_MIB = 95        # GitHub bloquea 100 MiB por archivo: margen para no hacer commit de uno así
LIMITE_AGENTS_BYTES = 30 * 1024   # algunos agentes solo leen los primeros 32 KiB de AGENTS.md: avisar antes


def _limpiar() -> list:
    """Dentro del candado: si hay una descarga registrándose en ese momento, espera a que termine.
    Devuelve lo que NO se pudo borrar (p. ej. un archivo abierto por otro programa)."""
    with bloqueo():
        en_proyecto, fuera, fallidos = limpiar_temporales()
    print(f"Limpieza: {en_proyecto} temporal(es) borrado(s) del proyecto (trabajo/, *.tmp, __pycache__, .DS_Store, "
          f"cachés de desarrollo) y {fuera} archivo(s) de la carpeta temporal del sistema (anteriores/, caché y "
          f"ejecuciones).")
    if fallidos:
        print(f"AVISO: {len(fallidos)} temporal(es) NO se pudieron borrar (¿abiertos por otro programa?): "
              + ", ".join(str(f) for f in fallidos[:5]) + (" ..." if len(fallidos) > 5 else ""))
        print("       Cierra el programa que los tenga abiertos y repite: python biblioteca.py verificar --limpiar")
    print()
    return fallidos


def main() -> None:
    p = Analizador(description="Revisar la integridad de la biblioteca.",
                         formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_VERIFICAR)
    p.add_argument("--dias", type=int, default=30)
    p.add_argument("--limpiar", action="store_true", help="borrar temporales y archivos de trabajo antes de revisar")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    p.add_argument("--final", action="store_true",
                   help="cierre del proceso (sin otros agentes trabajando): los archivos de trabajo/ "
                        "también cuentan como ERROR")
    args = p.parse_args()
    salida.iniciar(args.json, "verificar")
    if args.limpiar:
        no_borrados = _limpiar()
        salida.poner(limpieza={"no_borrados": [relativa(f) for f in no_borrados]})

    catalogo = cargar_catalogo()
    manifiesto = cargar_manifiesto()
    docs = catalogo["documentos"]
    entidades = set(cargar_fuentes().get("entidades", {}))
    errores, pendientes, grandes = [], [], []

    # --- catálogo coherente
    ids = Counter(d["id"] for d in docs)
    errores += [f"id repetido en catálogo: {i}" for i, n in ids.items() if n > 1]
    rutas = Counter((d["grupo"], d["archivo"]) for d in docs)
    errores += [f"archivo repetido en catálogo: {g}/{a}" for (g, a), n in rutas.items() if n > 1]
    for d in docs:
        if d["estado"] in ("no_existe", "no_localizado", "abrogado") and not d.get("nota"):
            errores.append(f"{d['id']}: estado '{d['estado']}' sin nota que lo justifique")
        # la estructura de la biblioteca es una sola (herramientas/catalogo.py): carpeta, Abrogadas/ y nombre de archivo
        problema = problema_de_estructura(d)
        if problema:
            errores.append(f"{d['id']}: {problema}")
        if d.get("entidad") and d["entidad"] not in entidades:
            errores.append(f"{d['id']}: entidad '{d.get('entidad')}' no es uno de los 32 estados de fuentes.json")
        errores += [f"{d['id']}: esquema: {x}" for x in problemas_de_esquema_doc(d)]

    por_id = {d["id"]: d for d in docs}
    # el documento de cada entrada: PDF/<grupo>/<archivo> + .pdf (o .doc/.docx si la fuente solo publica Word)
    pdf_esperados = {ruta_pdf(d, "pdf").with_suffix("").resolve(): d for d in docs}
    md_esperados = {ruta_md(d).resolve(): d for d in docs}

    # --- archivos en documentos/: cada documento oficial (.pdf, o .doc/.docx si la fuente solo publica Word) en
    # PDF/<grupo>/ y su Markdown (.md) en Markdown/<grupo>/, con el mismo nombre; en la raíz solo el manifiesto, el
    # catálogo y SHA256SUMS. TODO lo que hay ahí se publica (SHA256SUMS y git add -A): cualquier otra cosa es un error.
    pdfs_disco, mds_disco = [], []
    errores += [f"Enlace simbólico en la biblioteca (se publicaría tal cual y no entra en SHA256SUMS): {relativa(e)}"
                for e in enlaces_simbolicos(DOCUMENTOS)]
    for rel, ruta in (archivos_de(DOCUMENTOS) if DOCUMENTOS.exists() else []):
        tipo = tipo_de_archivo(rel)
        if tipo == "markdown":
            mds_disco.append(ruta)
        elif tipo == "documento":
            pdfs_disco.append(ruta)
        elif tipo == "web":
            errores.append(f"Página web en la biblioteca: solo se guardan PDF o Word (busca el PDF o Word oficial; si no "
                           f"existe, retírala con catalogo retirar-pdf): documentos/{rel}")
        elif tipo == "otro":
            errores.append(f"Archivo que no pertenece a la biblioteca o está fuera de su carpeta (documentos oficiales en "
                           f"documentos/PDF/, Markdown en documentos/Markdown/; se publicaría tal cual): documentos/{rel}")
    por_documento: Counter[str] = Counter()
    sin_revisar: dict[str, int] = {}      # dependencia que falta -> documentos cuyo contenido no se pudo revisar
    huellas: dict[Path, str] = {}          # huellas ya calculadas (para comprobar SHA256SUMS sin rehacerlas)
    for pdf in pdfs_disco:
        formato = pdf.suffix.lower().lstrip(".")
        doc = pdf_esperados.get(pdf.with_suffix("").resolve()) if formato in FORMATOS else None
        if not doc:
            errores.append(f"Archivo que no pertenece a ningún documento del catálogo: {relativa(pdf)}")
            continue
        por_documento[doc["id"]] += 1
        try:
            ok, motivo, real = documento_valido(pdf, 1)
        except FaltaDependencia as e:   # no es un problema del archivo: el entorno está incompleto
            sin_revisar[str(e)] = sin_revisar.get(str(e), 0) + 1
            ok, real = True, formato
        else:
            if not ok:
                errores.append(f"Documento inválido ({motivo}): {relativa(pdf)}")
            elif real != formato:
                errores.append(f"La extensión no corresponde al contenido (es {real}): {relativa(pdf)}")
        registro = manifiesto.get(doc["id"])
        if not registro:
            errores.append(f"Documento sin registro en el manifiesto: {relativa(pdf)} "
                           f"(regístralo con: python biblioteca.py descargar {doc['id']} --archivo \"{relativa(pdf)}\")")
        else:
            huellas[pdf] = sha256_archivo(pdf)
            if registro.get("sha256") != huellas[pdf]:
                errores.append(f"La huella SHA-256 no coincide con el manifiesto (¿modificado a mano?): {relativa(pdf)}")
            if registro.get("formato") != formato:
                errores.append(f"El manifiesto dice formato '{registro.get('formato')}' y el archivo es .{formato}: "
                               f"{relativa(pdf)}")
            comp = registro.get("compresion")
            if comp and comp.get("sha256") != registro.get("sha256"):
                errores.append(f"La huella del comprimido no coincide con la del PDF guardado: {relativa(pdf)}")
        mb = pdf.stat().st_size / 1_000_000
        if pdf.stat().st_size > LIMITE_COMMIT_MIB * 1024 * 1024:
            errores.append(f"{relativa(pdf)} pesa {pdf.stat().st_size / 1024 / 1024:.0f} MiB: GitHub bloquea los "
                           f"archivos de 100 MiB o más. NO hagas commit; comprímelo antes: python biblioteca.py comprimir")
        if mb > UMBRAL_COMPRIMIR_MB and not (registro or {}).get("compresion"):
            grandes.append(f"{relativa(pdf)} ({mb:.0f} MB"
                           + (f", pasa del límite de {LIMITE_GITHUB_MB} MB de GitHub)" if mb >= LIMITE_GITHUB_MB else ")"))

    errores += [f"{i} tiene {n} documentos oficiales en su carpeta (debe haber uno solo: el vigente)"
                for i, n in por_documento.items() if n > 1]

    # --- Markdown en disco
    for md in mds_disco:
        doc = md_esperados.get(md.resolve())
        if not doc:
            errores.append(f"Markdown que no pertenece a ningún documento del catálogo: {relativa(md)}")
            continue
        if not ruta_pdf(doc).exists():
            errores.append(f"Markdown sin su documento: {relativa(md)}")
        if md.stat().st_size < MIN_CARACTERES_MD:
            errores.append(f"Markdown casi vacío ({md.stat().st_size} bytes): {relativa(md)}")
        elif b"\r\n" in md.read_bytes():
            errores.append(f"Markdown con finales de línea CRLF (git lo publicaría con LF y su huella cambiaría): "
                           f"{relativa(md)}; reconviértelo con python biblioteca.py convertir --forzar --id {doc['id']}")

    # --- documentos/SHA256SUMS (lo escribe biblioteca.py publicar --preparar): si existe, debe describir lo que hay en disco
    sumas_desactualizadas = ""
    if (DOCUMENTOS / "SHA256SUMS").is_file():
        calculadas = [(huellas.get(ruta) or sha256_archivo(ruta), rel) for rel, ruta in archivos_de(DOCUMENTOS)]
        s = comprobar_sumas(DOCUMENTOS, calculadas)
        if not s["ok"]:
            sumas_desactualizadas = (f"documentos/SHA256SUMS desactualizado ({len(s['faltantes'])} sin listar, "
                                     f"{len(s['sobrantes'])} ya no existen, {len(s['distintas'])} con otra huella"
                                     + (f", {s['lineas_invalidas']} líneas inválidas" if s["lineas_invalidas"] else "")
                                     + "): lo regenera python biblioteca.py publicar --preparar")

    # --- manifiesto contra disco
    for doc_id, registro in manifiesto.items():
        doc = por_id.get(doc_id)
        if not doc:
            errores.append(f"El manifiesto tiene un id que ya no está en el catálogo: {doc_id}")
            continue
        errores += [f"manifiesto de {doc_id}: esquema: {x}" for x in problemas_de_esquema_registro(registro)]
        if not ruta_pdf(doc).exists():
            errores.append(f"El manifiesto registra {doc_id} pero falta su documento")
            continue
        md_info = registro.get("markdown") or {}
        if registro.get("archivo") != ruta_relativa_pdf(doc) or (
                md_info.get("archivo") and md_info["archivo"] != ruta_relativa_md(doc)):
            errores.append(f"El manifiesto de {doc_id} apunta a otra ruta ({registro.get('archivo')}) que la de la "
                           f"estructura ({ruta_relativa_pdf(doc)}): ¿se movió a mano? Corrige con: python "
                           f"biblioteca.py catalogo mover {doc_id} --grupo \"{doc['grupo']}\"")
        if not markdown_al_dia(doc, registro):
            pendientes.append(f"falta convertir a Markdown: {doc_id}")
        ult = registro.get("ultima_verificacion")
        if ult and (datetime.now() - datetime.strptime(ult, "%Y-%m-%d")).days > args.dias:
            pendientes.append(f"sin verificar hace más de {args.dias} días: {doc_id} (último: {ult})")

    # --- documentos nunca descargados
    for d in docs:
        if pendiente_de_descarga(d, manifiesto):
            pendientes.append(f"nunca descargado: {d['id']}")
        if d.get("sin_pdf") and d["estado"] != "abrogado":
            errores.append(f"{d['id']}: 'sin_pdf' solo aplica a documentos abrogados")

    # --- temporales: en el proyecto no debe quedar ninguno al terminar un proceso
    huerfanos = borrar_huerfanos()          # restos de procesos que murieron de golpe
    restos = temporales_en_proyecto()
    en_trabajo = [r for r in restos if r == TRABAJO or TRABAJO in r.parents]
    if not args.final and not args.limpiar:
        restos = [r for r in restos if r not in en_trabajo]
    if restos:
        muestra = ", ".join(relativa(r) for r in restos[:5]) + (" ..." if len(restos) > 5 else "")
        errores.append(f"{len(restos)} temporal(es) en el proyecto ({muestra}): "
                       "bórralos con: python biblioteca.py verificar --limpiar")
    fuera = contar_archivos(TEMP_PROYECTO)
    avisos = []
    if en_trabajo and not (args.final or args.limpiar):
        avisos.append(f"{len(en_trabajo)} archivo(s) de trabajo en trabajo/ (de este u otros agentes): "
                      "el agente principal los borra al terminar con: python biblioteca.py verificar --limpiar")
    for dependencia, n in sin_revisar.items():
        avisos.append(f"no se pudo revisar el contenido de {n} documento(s): {dependencia}")
    if sumas_desactualizadas:
        avisos.append(sumas_desactualizadas)
    if grandes:
        avisos.append(f"{len(grandes)} PDF de más de {UMBRAL_COMPRIMIR_MB} MB sin comprimir (el usuario puede "
                      "pedir 'comprimir'; ver python biblioteca.py comprimir --listar): " + ", ".join(grandes[:5])
                      + (" ..." if len(grandes) > 5 else ""))
    avisos += [f"no es parte del proyecto (¿resto de un agente? si es tuyo y temporal, bórralo): "
              f"{relativa(p)}" for p in inesperados()]
    # --- manual y copias de .claude/: AGENTS.md cabe en 32 KiB; las copias generadas están al día
    agents_md = RAIZ / "AGENTS.md"
    if agents_md.exists() and agents_md.stat().st_size > LIMITE_AGENTS_BYTES:
        avisos.append(f"AGENTS.md pesa {agents_md.stat().st_size:,} bytes: algunos agentes solo leen 32,768; recórtalo "
                      f"(mueve detalle a referencia/) antes de que se trunque")
    error_copias = error_de_copias()
    if error_copias:
        errores.append(error_copias)

    # --- reporte
    activos = sum(1 for d in docs if d["estado"] in ("vigente", "abrogado"))
    print("=== VERIFICACIÓN DE LA BIBLIOTECA ===")
    print(f"Catálogo: {len(docs)} documentos ({activos} activos)")
    tipos = Counter(a.suffix.lower() for a in pdfs_disco)
    tipos_disco = {k.lstrip("."): v for k, v in tipos.items()}
    otros = tipos[".doc"] + tipos[".docx"]
    print(f"En disco: {tipos['.pdf']} PDF" + (f" + {otros} Word oficial" if otros else "")
          + f" | {len(mds_disco)} Markdown | manifiesto: {len(manifiesto)} registros")
    print(f"Temporales: {len(restos)} en el proyecto | {fuera} fuera del proyecto (caché/ejecuciones; "
          "se borran con --limpiar al terminar)"
          + (f" | {huerfanos} de procesos muertos borrados ahora" if huerfanos else ""))
    tipos = Counter(p.split(":")[0] for p in pendientes)
    print(f"\nPENDIENTES: {len(pendientes)}")
    for tipo, n in tipos.most_common():
        print(f"  {n:5}  {tipo}")
    primeros = pendientes[:15]
    for x in primeros:
        print(f"    - {x}")
    if len(pendientes) > len(primeros):
        print(f"    ... y {len(pendientes) - len(primeros)} más (python biblioteca.py catalogo listar --pendientes)")
    if avisos:
        print(f"\nAVISOS: {len(avisos)}")
        for a in avisos[:20]:
            print(f"  - {a}")
    print(f"\nERRORES: {len(errores)}")
    for error in errores[:50]:
        print(f"  - {error}")
    if len(errores) > 50:
        print(f"  ... y {len(errores) - 50} más")
    print("\nINTEGRIDAD: " + ("OK" if not errores else "HAY ERRORES — corrígelos antes de terminar"))
    salida.poner(integridad=not errores, catalogo=len(docs), activos=activos, en_disco=dict(tipos_disco), markdown=len(mds_disco),
                 manifiesto=len(manifiesto),
                 temporales={"en_proyecto": len(restos), "fuera": fuera, "huerfanos_borrados": huerfanos},
                 pendientes=pendientes, avisos=avisos, errores=errores)
    sys.exit(1 if errores else 0)


# ====================================================================================================
# Herramienta: pruebas (para desarrollar)
# ====================================================================================================
def main_pruebas():
    """pytest, ruff y mypy con sus cachés FUERA del proyecto: nada de .pytest_cache/.ruff_cache/.mypy_cache aquí.
    Requiere los paquetes de desarrollo: python biblioteca.py instalar --para desarrollo."""
    p = Analizador(description="Pruebas del código: pytest, ruff y mypy (cachés fuera del proyecto).")
    p.add_argument("--rapido", action="store_true", help="solo pytest")
    p.add_argument("pytest_args", nargs="*", help="argumentos para pytest (p. ej. -k markdown)")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    args = p.parse_args()
    salida.iniciar(args.json, "pruebas")
    entorno = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", BIBLIOTECA_SIN_ENTORNO="1",
                   RUFF_CACHE_DIR=str(CACHE / "ruff"), MYPY_CACHE_DIR=str(CACHE / "mypy"))
    pasos = [[sys.executable, "-m", "pytest", *args.pytest_args]]
    if not args.rapido:
        pasos += [[sys.executable, "-m", "ruff", "check", "."], [sys.executable, "-m", "mypy", "herramientas", "biblioteca.py"]]
    fallas = []
    for cmd in pasos:
        print("$", " ".join(cmd[2:]), flush=True)
        # con --json, la salida de pytest/ruff/mypy va a stderr (sys.stdout ya apunta ahí): stdout queda para el JSON
        if subprocess.run(cmd, cwd=RAIZ, env=entorno, check=False, stdout=sys.stdout).returncode != 0:
            fallas.append(cmd[2])
    salida.poner(resultado="OK" if not fallas else "FALLAN", fallas=fallas)
    print("\nPRUEBAS: " + ("OK" if not fallas else "fallan " + ", ".join(fallas)))
    sys.exit(1 if fallas else 0)
