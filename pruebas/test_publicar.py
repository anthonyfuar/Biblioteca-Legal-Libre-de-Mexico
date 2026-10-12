"""Pruebas de herramientas/publicar.py."""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from herramientas import comun, publicar, verificar
from herramientas.comun import salida

# ====================================================================================================
# Pruebas de herramientas/publicar.py (SHA256SUMS, CAMBIOS.md, bitácora, archivos grandes) y de herramientas/publicar.py
# ====================================================================================================
PROYECTO = Path(__file__).resolve().parent.parent
MOMENTO_ORIGINAL = publicar._momento        # el real (espera a que el reloj pase el segundo); los fixtures lo sustituyen


# ---------------------------------------------------------------------------
# SHA256SUMS
# ---------------------------------------------------------------------------
@pytest.fixture
def documentos(tmp_path):
    """Una documentos/ pequeña: PDF, Word, Markdown, los dos JSON, basura del sistema, temporales que git no
    publica y un SHA256SUMS viejo."""
    carpeta = tmp_path / "documentos"
    archivos = {
        "Federal/Energia/Ley_de_Hidrocarburos.pdf": b"%PDF-1.4 hidrocarburos",
        "Estados/Jalisco/Ley_de_Salud.doc": b"\xd0\xcf\x11\xe0 word",
        "Federal/Energia/Ley_de_Hidrocarburos.md": b"# Ley de Hidrocarburos\n",
        "manifiesto.json": b'{"version": 2}\n',
        "catalogo.json": b'{"documentos": []}\n',
        "Federal/.guardando_x.tmp": b"a medias",
        "__pycache__/algo.pyc": b"bytecode",
        "Estados/resto.PYC": b"bytecode",
    }
    for relativa, contenido in archivos.items():
        ruta = carpeta / relativa
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
    (carpeta / ".DS_Store").write_bytes(b"basura")
    (carpeta / "Federal" / "Thumbs.db").write_bytes(b"basura")
    (carpeta / "SHA256SUMS").write_text("viejo\n", encoding="utf-8")
    return carpeta


def _sha(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def test_sumas_lista_todo_ordenado_sin_sha256sums_basura_ni_temporales(documentos):
    lista = publicar.sumas(documentos)
    rutas = [ruta for _, ruta in lista]
    assert rutas == sorted(rutas)
    assert rutas == ["Estados/Jalisco/Ley_de_Salud.doc", "Federal/Energia/Ley_de_Hidrocarburos.md",
                     "Federal/Energia/Ley_de_Hidrocarburos.pdf", "catalogo.json", "manifiesto.json"]
    assert dict((r, s) for s, r in lista)["Federal/Energia/Ley_de_Hidrocarburos.pdf"] == _sha(b"%PDF-1.4 hidrocarburos")
    assert all(len(suma) == 64 and suma == suma.lower() for suma, _ in lista)
    assert [r for r, _ in publicar.archivos_de(documentos)] == rutas


def test_escribir_sumas_formato_exacto_de_sha256sum(documentos):
    ruta = publicar.escribir_sumas(documentos)
    assert ruta == documentos / "SHA256SUMS"
    crudo = ruta.read_bytes()
    assert not crudo.startswith(b"\xef\xbb\xbf") and b"\r" not in crudo and crudo.endswith(b"\n")
    lineas = crudo.decode("utf-8").split("\n")
    assert lineas[-1] == "" and len(lineas) == 6
    assert lineas[1] == _sha(b"# Ley de Hidrocarburos\n") + "  Federal/Energia/Ley_de_Hidrocarburos.md"
    assert lineas[3] == _sha(b'{"documentos": []}\n') + "  catalogo.json"
    for linea in lineas[:-1]:
        suma, separador, resto = linea[:64], linea[64:66], linea[66:]
        assert separador == "  " and len(suma) == 64 and resto and not resto.startswith(" ")


def test_escribir_sumas_exige_la_carpeta(tmp_path):
    with pytest.raises(FileNotFoundError):
        publicar.escribir_sumas(tmp_path / "no_existe")


def test_comprobar_sumas_sin_archivo(documentos):
    (documentos / "SHA256SUMS").unlink()
    estado = publicar.comprobar_sumas(documentos)
    assert estado["existe"] is False and estado["ok"] is False
    assert estado["faltantes"] == [] and estado["sobrantes"] == [] and estado["distintas"] == []


def test_comprobar_sumas_al_dia_y_luego_faltante_sobrante_y_distinto(documentos):
    publicar.escribir_sumas(documentos)
    estado = publicar.comprobar_sumas(documentos)
    assert estado["ok"] is True and estado["listadas"] == estado["en_disco"] == 5
    (documentos / "Federal/Energia/Ley_Nueva.pdf").write_bytes(b"%PDF-1.4 nueva")
    (documentos / "Estados/Jalisco/Ley_de_Salud.doc").unlink()
    (documentos / "manifiesto.json").write_bytes(b'{"version": 3}\n')
    estado = publicar.comprobar_sumas(documentos)
    assert estado["ok"] is False
    assert estado["faltantes"] == ["Federal/Energia/Ley_Nueva.pdf"]
    assert estado["sobrantes"] == ["Estados/Jalisco/Ley_de_Salud.doc"]
    assert estado["distintas"] == ["manifiesto.json"]
    assert publicar.comprobar_sumas(documentos, publicar.sumas(documentos)) == estado


def test_comprobar_sumas_cuenta_lineas_mal_formadas_y_acepta_el_separador_binario(documentos):
    publicar.escribir_sumas(documentos)
    archivo = documentos / "SHA256SUMS"
    texto = archivo.read_text(encoding="utf-8").replace("  catalogo.json", " *catalogo.json")
    archivo.write_text(texto + "esto no es una suma\n", encoding="utf-8")
    estado = publicar.comprobar_sumas(documentos)
    assert estado["lineas_invalidas"] == 1 and estado["ok"] is False
    assert estado["faltantes"] == [] and estado["distintas"] == []


# ---------------------------------------------------------------------------
# CAMBIOS.md
# ---------------------------------------------------------------------------
def test_ultima_publicacion_sin_archivo_y_sin_entradas(tmp_path):
    assert publicar.ultima_publicacion(tmp_path / "CAMBIOS.md") is None
    cambios = tmp_path / "CAMBIOS.md"
    cambios.write_text(f"{publicar.ENCABEZADO_CAMBIOS}\n\n{publicar.EXPLICACION_CAMBIOS}\n", encoding="utf-8")
    assert publicar.ultima_publicacion(cambios) is None
    assert publicar.cambios_sin_entradas_reconocibles(cambios) is False
    cambios.write_text(f"{publicar.ENCABEZADO_CAMBIOS}\n\nTexto escrito a mano, sin entradas.\n", encoding="utf-8")
    assert publicar.cambios_sin_entradas_reconocibles(cambios) is True


def test_ultima_publicacion_toma_la_fecha_mas_reciente_con_su_marcador(tmp_path):
    cambios = tmp_path / "CAMBIOS.md"
    cambios.write_text(
        f"{publicar.ENCABEZADO_CAMBIOS}\n\n"
        "## 2026-10-06 — 1 nuevo, 0 actualizados\n<!-- publicacion: 2026-10-06 17:45:12 -->\n\n- algo\n\n"
        "## 2026-09-30 — 0 nuevos, 17 actualizados\n<!-- publicacion: 2026-09-30 13:30:00 -->\n\n- algo\n\n"
        "## 2026-09-01\n\nEntrada escrita a mano, sin marcador.\n", encoding="utf-8")
    assert publicar.ultima_publicacion(cambios) == "2026-10-06 17:45:12"
    fechas = [fecha for fecha, _ in publicar.entradas(cambios)]
    assert fechas == ["2026-10-06 17:45:12", "2026-09-30 13:30:00", "2026-09-01"]
    ultima = publicar.ultima_entrada(cambios)
    assert ultima is not None and ultima[1].startswith("## 2026-10-06 — 1 nuevo")


@pytest.mark.parametrize("encabezado", ["## 2026-10-06 - 1 nuevo, 0 actualizados", "## 2026-10-06 – 1 nuevo, 0 actualizados",
                                        "##  2026-10-06 — 1 nuevo, 0 actualizados  ", "## 2026-10-06 ", "## 2026-10-06"])
def test_entradas_reconocen_encabezados_editados_a_mano(tmp_path, encabezado):
    cambios = tmp_path / "CAMBIOS.md"
    cambios.write_text(f"{publicar.ENCABEZADO_CAMBIOS}\n\n{encabezado}\n\n- algo\n", encoding="utf-8")
    assert publicar.ultima_publicacion(cambios) == "2026-10-06"
    assert publicar.cambios_sin_entradas_reconocibles(cambios) is False
    publicar.insertar_entrada(cambios, "## 2026-10-07 — 0 nuevos, 1 actualizado\n<!-- publicacion: 2026-10-07 10:00:00 -->\n")
    texto = cambios.read_text(encoding="utf-8")
    assert texto.index("## 2026-10-07") < texto.index(encabezado.strip())
    assert "\n\n\n" not in texto
    if "nuevo" in encabezado:
        asunto, _ = publicar.mensaje_commit(f"{encabezado}\n- algo\n")
        assert asunto == "Biblioteca 2026-10-06: 1 nuevo, 0 actualizados"


def test_insertar_entrada_crea_el_archivo_y_pone_la_nueva_primero(tmp_path):
    cambios = tmp_path / "CAMBIOS.md"
    publicar.insertar_entrada(cambios, "## 2026-09-30 — 0 nuevos, 1 actualizado\n"
                                          "<!-- publicacion: 2026-09-30 13:30:00 -->\n\n- a\n")
    texto = cambios.read_text(encoding="utf-8")
    assert texto.startswith(publicar.ENCABEZADO_CAMBIOS + "\n\n" + publicar.EXPLICACION_CAMBIOS + "\n\n## 2026-09-30")
    assert texto.endswith("- a\n") and "\r" not in texto
    publicar.insertar_entrada(cambios, "## 2026-10-06 — 1 nuevo, 0 actualizados\n"
                                          "<!-- publicacion: 2026-10-06 17:45:12 -->\n\n- b\n")
    texto = cambios.read_text(encoding="utf-8")
    assert texto.index("## 2026-10-06") < texto.index("## 2026-09-30")
    assert "\n\n## 2026-09-30" in texto and "\n\n\n" not in texto
    assert publicar.ultima_publicacion(cambios) == "2026-10-06 17:45:12"


def test_insertar_entrada_sin_entradas_reconocibles_va_tras_el_encabezado(tmp_path):
    cambios = tmp_path / "CAMBIOS.md"
    cambios.write_text(f"{publicar.ENCABEZADO_CAMBIOS}\n\n{publicar.EXPLICACION_CAMBIOS}\n\nNotas a mano.\n",
                       encoding="utf-8")
    publicar.insertar_entrada(cambios, "## 2026-10-07 — 0 nuevos, 1 actualizado\n")
    texto = cambios.read_text(encoding="utf-8")
    assert texto.index("## 2026-10-07") < texto.index("Notas a mano.")


# ---------------------------------------------------------------------------
# Bitácora -> entrada
# ---------------------------------------------------------------------------
EVENTOS = [
    {"fecha": "2026-09-20 10:00:00", "herramienta": "descargar", "id": "viejo__ley", "resultado": "NUEVO",
     "fecha_reforma": "2026-01-01"},
    {"fecha": "2026-09-20 10:00:01", "herramienta": "catalogo", "accion": "agregar", "id": "viejo__ley", "nombre": "Ley Vieja"},
    {"fecha": "2026-10-01 09:00:00", "herramienta": "descargar", "id": "federal__ley_del_sector_hidrocarburos",
     "resultado": "NUEVO", "fecha_reforma": "2025-03-18", "url": "https://www.diputados.gob.mx/x.pdf"},
    {"fecha": "2026-10-01 09:00:05", "herramienta": "descargar", "id": "federal__ley_del_sector_hidrocarburos",
     "resultado": "ACTUALIZADO", "fecha_reforma": "2026-09-30"},
    {"fecha": "2026-10-01 09:01:00", "herramienta": "descargar", "id": "jalisco__codigo_civil", "resultado": "ACTUALIZADO",
     "fecha_reforma": "2026-08-15"},
    {"fecha": "2026-10-01 09:01:30", "herramienta": "descargar", "id": "jalisco__codigo_civil", "resultado": "SIN_CAMBIO"},
    {"fecha": "2026-10-01 09:02:00", "herramienta": "descargar", "id": "jalisco__ley_x", "resultado": "DUDOSO",
     "detalle": "otra"},
    {"fecha": "2026-10-01 09:03:00", "herramienta": "catalogo", "accion": "marcar", "id": "federal__ley_de_hidrocarburos",
     "de": "vigente", "a": "abrogado", "nota": "Abrogada por la Ley del Sector Hidrocarburos (DOF 18-03-2025)"},
    {"fecha": "2026-10-01 09:03:10", "herramienta": "catalogo", "accion": "marcar", "id": "jalisco__ley_y",
     "de": "vigente", "a": "vigente", "nota": "solo nota"},
    {"fecha": "2026-10-01 09:03:20", "herramienta": "catalogo", "accion": "marcar", "id": "jalisco__ley_z",
     "de": "vigente", "a": "no_existe", "nota": "La materia se regula en el Código"},
    {"fecha": "2026-10-01 09:04:00", "herramienta": "catalogo", "accion": "agregar",
     "id": "federal__ley_del_sector_hidrocarburos", "nombre": "Ley del Sector Hidrocarburos", "url": "https://x", "nota": None},
    {"fecha": "2026-10-01 09:05:00", "herramienta": "catalogo", "accion": "eliminar", "id": "federal__duplicada",
     "nombre": "Ley Duplicada", "grupo": "Federal/Ambiental", "estado_previo": "no_existe", "nota": "Entrada duplicada"},
    {"fecha": "2026-10-01 09:06:00", "herramienta": "catalogo", "accion": "renombrar", "id": "bcs__constitucion",
     "de": "Constitución Política del Estado de BCS", "a": "Constitución Política del Estado Libre y Soberano de BCS",
     "nota": "Nombre oficial"},
    {"fecha": "2026-10-01 09:07:00", "herramienta": "catalogo", "accion": "mover", "id": "federal__ley_movida",
     "de": "Federal/Energia", "a": "Federal/Ambiental", "nota": None},
    {"fecha": "2026-10-01 09:08:00", "herramienta": "catalogo", "accion": "quitar-pista", "id": "x", "a": "http://y",
     "nota": None},
    {"fecha": "2026-10-01 10:00:00", "herramienta": "convertir", "id": "jalisco__codigo_civil", "resultado": "OK",
     "metodo": "markitdown", "caracteres": 123456},
    {"fecha": "2026-10-01 10:00:01", "herramienta": "convertir", "id": "federal__ley_del_sector_hidrocarburos", "resultado": "OK",
     "metodo": "ocr", "caracteres": 2000},
    {"fecha": "2026-10-01 10:00:02", "herramienta": "convertir", "id": "jalisco__ley_q", "resultado": "FALLA", "detalle": "x"},
    {"fecha": "2026-10-01 10:30:00", "herramienta": "convertir", "accion": "reestructurar", "id": "jalisco__codigo_civil",
     "resultado": "OK", "metodo": "markitdown", "caracteres": 120000, "articulos": 3, "formato": 2},
    {"fecha": "2026-10-01 10:30:01", "herramienta": "convertir", "accion": "reestructurar", "id": "jalisco__codigo_civil",
     "resultado": "OK", "metodo": "markitdown", "caracteres": 120000, "articulos": 3, "formato": 2},
    {"fecha": "2026-10-01 11:00:00", "herramienta": "comprimir", "id": "cdmx__codigo_fiscal", "resultado": "OK", "nivel": 30,
     "mb": [163.9, 56.5], "psnr_min_db": 41.2},
    {"fecha": "2026-10-01 11:00:01", "herramienta": "comprimir", "id": "cdmx__otro", "resultado": "DESCARTADO", "nivel": 30},
    {"fecha": "2026-10-01 12:00:00", "herramienta": "migracion", "accion": "esquema_v2", "detalle": "catalogo.json migrado"},
    {"fecha": "2026-10-01 13:00:00", "herramienta": "publicar", "accion": "preparar", "desde": None,
     "publicacion": "2026-10-01 13:00:00"},
    {"fecha": "2026-10-02 08:00:00", "herramienta": "descargar", "id": "futuro__ley", "resultado": "NUEVO",
     "fecha_reforma": "2026-10-01"},
]


@pytest.fixture
def bitacora(tmp_path):
    ruta = tmp_path / "bitacora.jsonl"
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("esto no es json\n")
        for e in EVENTOS:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return ruta


def test_entrada_cambios_clasifica_todo_y_cuenta(bitacora):
    texto, conteos = publicar.entrada_cambios(bitacora, "2026-09-30 00:00:00", "2026-10-01 23:59:59")
    assert conteos == {"nuevos": 1, "actualizados": 1, "abrogados": 1, "agregados": 1, "eliminados": 1, "renombrados": 1,
                       "movidos": 1, "otros": 1, "convertidos": 2, "reestructurados": 1, "comprimidos": 1, "migraciones": 1,
                       "total": 13}
    lineas = texto.splitlines()
    assert lineas[0] == ("## 2026-10-01 — 1 nuevo, 1 actualizado, 1 abrogado, 1 agregado al catálogo, 1 eliminado del catálogo, "
                         "1 renombrado, 1 movido de carpeta, 1 otro cambio del catálogo, 2 convertidos a Markdown, "
                         "1 Markdown con formato nuevo, 1 PDF comprimido, 1 tarea de mantenimiento")
    # reestructurar no es convertir: se cuenta aparte y una vez por documento, aunque se repita
    assert "### Markdown con formato nuevo (1)" in texto and "- formato 2: 1 documento(s), sin reconvertir el original" in texto
    assert lineas[1] == "<!-- publicacion: 2026-10-01 23:59:59 -->"
    assert "desde la publicación anterior (2026-09-30 00:00:00)" in lineas[3]
    assert "### Documentos nuevos (1)" in lineas
    assert "- `federal__ley_del_sector_hidrocarburos` — reforma 2026-09-30" in lineas
    assert "- `jalisco__codigo_civil` — reforma 2026-08-15" in lineas
    assert "- `federal__ley_de_hidrocarburos` — Abrogada por la Ley del Sector Hidrocarburos (DOF 18-03-2025)" in lineas
    assert "- Ley del Sector Hidrocarburos (`federal__ley_del_sector_hidrocarburos`)" in lineas
    assert "- Ley Duplicada (`federal__duplicada`) — Entrada duplicada" in lineas
    assert ("- `bcs__constitucion`: «Constitución Política del Estado de BCS» → «Constitución Política del Estado Libre y "
            "Soberano de BCS» — Nombre oficial") in lineas
    assert "- `federal__ley_movida`: Federal/Energia → Federal/Ambiental" in lineas
    assert "- `jalisco__ley_z`: vigente → no_existe — La materia se regula en el Código" in lineas
    assert "### Convertidos a Markdown (2)" in lineas and "- markitdown: 1" in lineas and "- ocr: 1" in lineas
    assert "- `cdmx__codigo_fiscal`: 163.9 → 56.5 MB (nivel 30)" in lineas
    assert "- esquema_v2: catalogo.json migrado" in lineas
    assert "jalisco__ley_x" not in texto and "jalisco__ley_y" not in texto and "jalisco__ley_q" not in texto
    assert "cdmx__otro" not in texto and "quitar-pista" not in texto and "publicar" not in texto
    assert "viejo__ley" not in texto and "Ley Vieja" not in texto       # anterior a `desde`
    assert "futuro__ley" not in texto                                     # posterior a `hoy` (hasta)
    assert texto.endswith("\n") and "\n\n\n" not in texto


def test_entrada_cambios_con_nombres_y_desde_el_inicio(bitacora):
    nombres = {"jalisco__codigo_civil": "Código Civil del Estado de Jalisco"}
    texto, conteos = publicar.entrada_cambios(bitacora, None, "2026-10-06", nombres=nombres)
    assert conteos["nuevos"] == 3 and conteos["agregados"] == 2
    assert "- Código Civil del Estado de Jalisco (`jalisco__codigo_civil`) — reforma 2026-08-15" in texto
    assert "- Ley Vieja (`viejo__ley`)" in texto and "futuro__ley" in texto      # un `hoy` sin hora incluye ese día
    assert "desde el inicio de la bitácora (primera publicación) hasta 2026-10-06." in texto
    assert texto.splitlines()[1] == "<!-- publicacion: 2026-10-06 -->"


def test_entrada_cambios_sin_eventos_nuevos_devuelve_texto_vacio(bitacora, tmp_path):
    texto, conteos = publicar.entrada_cambios(bitacora, "2026-10-02 08:00:00", "2026-10-06")
    assert texto == "" and conteos["total"] == 0 and all(v == 0 for v in conteos.values())
    texto, conteos = publicar.entrada_cambios(tmp_path / "no_existe.jsonl", None, "2026-10-06")
    assert texto == "" and conteos["total"] == 0


def test_leer_bitacora_ventana_desde_hasta_y_ultima_preparacion(bitacora):
    eventos = publicar.leer_bitacora(bitacora, "2026-10-01 09:00:00", "2026-10-01 09:01:00")
    assert [e["fecha"] for e in eventos] == ["2026-10-01 09:00:05", "2026-10-01 09:01:00"]   # (desde, hasta]
    assert publicar.ultima_preparacion(bitacora) == "2026-10-01 13:00:00"
    assert publicar.ultima_preparacion(bitacora.parent / "no.jsonl") is None


def test_mensaje_commit_asunto_corto_y_listas_recortadas(bitacora):
    texto, _ = publicar.entrada_cambios(bitacora, "2026-09-30", "2026-10-06 17:45:12")
    asunto, cuerpo = publicar.mensaje_commit(texto, maximo_por_lista=1)
    assert asunto == "Biblioteca 2026-10-06: 2 nuevos, 1 actualizado" and len(asunto) <= 72
    assert "<!--" not in cuerpo and "###" not in cuerpo
    assert "Documentos nuevos (2):" in cuerpo and "Convertidos a Markdown (2):" in cuerpo
    assert "- markitdown: 1" in cuerpo and "- ocr: 1" not in cuerpo
    assert "… y 1 más (ver CAMBIOS.md)" in cuerpo
    assert "Co-Authored-By" not in cuerpo
    asunto, cuerpo = publicar.mensaje_commit("texto sin encabezado\n- a\n")
    assert asunto == "Publicación de la biblioteca" and cuerpo == "- a"


def test_mensaje_commit_varias_suma_conteos_y_concatena():
    a = ("## 2026-10-06 — 1 nuevo, 2 actualizados\n<!-- publicacion: 2026-10-06 10:00:00 -->\n\n"
         "### Documentos nuevos (1)\n- `x`\n")
    b = ("## 2026-10-07 — 0 nuevos, 3 actualizados\n<!-- publicacion: 2026-10-07 10:00:00 -->\n\n"
         "### Documentos actualizados (3)\n- `y`\n")
    assert publicar.mensaje_commit_varias([a]) == publicar.mensaje_commit(a)
    asunto, cuerpo = publicar.mensaje_commit_varias([a, b])
    assert asunto == "Biblioteca 2026-10-07: 1 nuevo, 5 actualizados"
    assert cuerpo.index("[2026-10-07 — 0 nuevos, 3 actualizados]") < cuerpo.index("[2026-10-06 — 1 nuevo, 2 actualizados]")
    assert "- `x`" in cuerpo and "- `y`" in cuerpo and "<!--" not in cuerpo
    assert publicar.mensaje_commit_varias([]) == ("Publicación de la biblioteca", "")


def test_entrada_cambios_aplica_el_mapa_de_ids_migrados_y_normaliza_fechas(tmp_path):
    ruta = tmp_path / "bitacora.jsonl"
    eventos = [
        {"fecha": "2026-09-30 10:00:00", "herramienta": "descargar", "id": "leyes_federales_x__ley_a", "resultado": "ACTUALIZADO",
         "fecha_reforma": "08-09-2026"},
        {"fecha": "2026-09-30 10:01:00", "herramienta": "catalogo", "accion": "renombrar", "id": "leyes_federales_x__ley_a",
         "de": "Ley A", "a": "Ley A bis", "nota": None},
        {"fecha": "2026-10-06 16:24:16", "herramienta": "migracion", "accion": "ids_federales", "detalle": "ids",
         "mapa": {"leyes_federales_x__ley_a": "federal__ley_a"}},
    ]
    with open(ruta, "w", encoding="utf-8") as f:
        for e in eventos:
            f.write(json.dumps(e) + "\n")
    texto, conteos = publicar.entrada_cambios(ruta, None, "2026-10-06", nombres={"federal__ley_a": "Ley A bis"})
    assert conteos["actualizados"] == 1 and conteos["renombrados"] == 1 and conteos["migraciones"] == 1
    assert "- Ley A bis (`federal__ley_a`) — reforma 2026-09-08" in texto
    assert "- `federal__ley_a`: «Ley A» → «Ley A bis»" in texto
    assert "leyes_federales_x__ley_a" not in texto


# ---------------------------------------------------------------------------
# Archivos grandes
# ---------------------------------------------------------------------------
def test_archivos_grandes_ignora_git_venv_y_trabajo(tmp_path):
    def disperso(ruta: Path, mib: int):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        with open(ruta, "wb") as f:
            f.seek(mib * 1024 * 1024)
            f.write(b"0")
    disperso(tmp_path / "documentos/Federal/Grande.pdf", 2)
    disperso(tmp_path / "documentos/Federal/Chico.pdf", 0)
    disperso(tmp_path / ".git/objects/enorme", 3)
    disperso(tmp_path / ".venv/lib/enorme", 3)
    disperso(tmp_path / "trabajo/enorme.pdf", 3)              # archivos de trabajo del agente: no se publican
    disperso(tmp_path / "herramientas/datos.bin", 2)          # el código sí se publica
    assert publicar.archivos_grandes(tmp_path, limite_mib=1) == [tmp_path / "documentos/Federal/Grande.pdf",
                                                                 tmp_path / "herramientas/datos.bin"]
    assert publicar.archivos_grandes(tmp_path, limite_mib=5) == []


# ---------------------------------------------------------------------------
# git sobre un repositorio temporal (nunca el real)
# ---------------------------------------------------------------------------
def _git(raiz: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=raiz, capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture
def repositorio(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(tmp_path, "config", "user.name", "Pruebas")
    _git(tmp_path, "config", "user.email", "pruebas@example.com")
    (tmp_path / ".gitignore").write_text("trabajo/\n.venv/\n*.tmp\n", encoding="utf-8")
    (tmp_path / "documentos").mkdir()
    (tmp_path / "documentos" / "a.pdf").write_bytes(b"%PDF-1.4 a")
    (tmp_path / "bitacora.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "trabajo").mkdir()
    (tmp_path / "trabajo" / "notas.txt").write_text("listado del agente\n", encoding="utf-8")
    return tmp_path


def test_estado_git_sin_repositorio(tmp_path):
    assert publicar.estado_git(tmp_path)["repositorio"] is False


def test_estado_git_detecta_un_repositorio_padre(repositorio):
    sub = repositorio / "subcarpeta"
    sub.mkdir()
    estado = publicar.estado_git(sub)
    assert estado["repositorio"] is False and estado["repositorio_padre"]


def test_estado_git_y_commit_en_repositorio_temporal(repositorio):
    estado = publicar.estado_git(repositorio)
    assert estado["repositorio"] is True and estado["rama"] == "main" and estado["commits"] == 0
    assert estado["remoto"] is None and estado["origen_main"] is False and estado["adelante"] is None
    assert sorted(estado["cambios"]) == [".gitignore", "bitacora.jsonl", "documentos/a.pdf"]
    assert publicar.cambios_prohibidos(estado["cambios"]) == []
    assert publicar.cambios_prohibidos([".venv/x", "trabajo/notas.txt", "bitacora.jsonl", "herramientas/comun.py"]
                                       ) == [".venv/x", "trabajo/notas.txt"]
    assert publicar.cambios_pendientes(["bitacora.jsonl", "a.txt"]) == ["a.txt"]
    sha, resumen = publicar.hacer_commit(repositorio, "Biblioteca 2026-10-06: 1 nuevo, 0 actualizados",
                                         "Documentos nuevos (1):\n- a")
    assert len(sha) >= 7 and "3 files changed" in resumen
    mensaje = _git(repositorio, "log", "-1", "--format=%B")
    assert mensaje.startswith("Biblioteca 2026-10-06: 1 nuevo, 0 actualizados\n\nDocumentos nuevos (1):\n- a\n\n")
    assert mensaje.rstrip().endswith(publicar.COAUTORIA)
    estado = publicar.estado_git(repositorio)
    assert estado["commits"] == 1 and estado["cambios"] == [] and estado["tamano"]["objetos"] >= 5
    assert publicar.objetos_por_subir(repositorio, "main") is None or publicar.objetos_por_subir(repositorio, "main") > 0


def test_cambios_git_cuenta_una_sola_vez_los_renombrados(repositorio):
    publicar.hacer_commit(repositorio, "uno", "")
    (repositorio / "largo.txt").write_text("".join(f"línea {i} con contenido suficiente para la similitud\n" for i in range(60)),
                                           encoding="utf-8")
    publicar.hacer_commit(repositorio, "dos", "")
    _git(repositorio, "mv", "documentos/a.pdf", "documentos/b.pdf")          # renombrado en el índice: "R  nuevo\0viejo"
    assert publicar.cambios_git(repositorio) == ["documentos/b.pdf"]
    (repositorio / "largo.txt").rename(repositorio / "largo2.txt")             # en el árbol de trabajo: " R nuevo\0viejo"
    _git(repositorio, "add", "-N", "largo2.txt")
    assert sorted(publicar.cambios_git(repositorio)) == ["documentos/b.pdf", "largo2.txt"]


def test_hacer_commit_acepta_un_cuerpo_enorme(repositorio):
    cuerpo = "\n".join(f"- `documento_{i}` — reforma 2026-01-01" for i in range(3000))      # > 100,000 caracteres
    assert len(cuerpo) > 100_000
    sha, _ = publicar.hacer_commit(repositorio, "Biblioteca 2026-10-06: 3000 nuevos, 0 actualizados", cuerpo)
    mensaje = _git(repositorio, "log", "-1", "--format=%B")
    assert sha and "- `documento_2999` — reforma 2026-01-01" in mensaje and mensaje.rstrip().endswith(publicar.COAUTORIA)


def test_rutas_ignoradas(repositorio):
    (repositorio / "documentos" / "x.tmp").write_bytes(b"x")
    (repositorio / "documentos" / "b.pdf").write_bytes(b"b")
    rutas = ["documentos/x.tmp", "documentos/b.pdf", "documentos/a.pdf"]
    assert publicar.rutas_ignoradas(repositorio, rutas) == ["documentos/x.tmp"]
    assert publicar.rutas_ignoradas(repositorio, ["documentos/b.pdf"]) == []
    assert publicar.rutas_ignoradas(repositorio, []) == []


def test_ocultar_credenciales():
    oc = publicar.ocultar_credenciales
    assert oc("https://usuario:token@github.com/u/r.git") == "https://github.com/u/r.git"
    assert oc("https://ghp_token@github.com/u/r.git") == "https://github.com/u/r.git"
    assert oc("token@github.com:u/r.git") == "github.com:u/r.git"
    assert oc("git@github.com:u/r.git") == "git@github.com:u/r.git"
    assert oc("ssh://git@github.com/u/r.git") == "ssh://git@github.com/u/r.git"
    assert oc("https://github.com/u/r.git") == "https://github.com/u/r.git"
    assert oc("fatal: could not set 'remote.origin.url' to 'https://u:SECRETO@github.com/x'") == \
        "fatal: could not set 'remote.origin.url' to 'https://github.com/x'"
    assert oc("remote add origin 'SECRETO@github.com:u/r.git'") == "remote add origin 'github.com:u/r.git'"
    assert oc("Co-Authored-By: X <noreply@anthropic.com>, pruebas@example.com") == \
        "Co-Authored-By: X <noreply@anthropic.com>, pruebas@example.com"
    assert publicar.lleva_credenciales("https://u:t@github.com/x") and not publicar.lleva_credenciales("git@github.com:u/r.git")


def test_git_no_filtra_el_token_cuando_remote_add_falla(repositorio, capsys):
    (repositorio / ".git" / "config.lock").write_bytes(b"")                 # git no puede escribir .git/config
    stdout_antes = sys.stdout
    salida.iniciar(True, "publicar")                                         # como con --json: el texto va a stderr
    try:
        with pytest.raises(SystemExit) as fin:
            publicar._git(repositorio, "remote", "add", "origin", "https://usuario:SECRETO@github.com/u/r.git")
        assert fin.value.code == 1
        error = salida._actual["error"]
    finally:
        salida.cerrar(1)
        sys.stdout = stdout_antes
    capturado = capsys.readouterr()
    assert "SECRETO" not in capturado.err and "SECRETO" not in capturado.out and "SECRETO" not in error
    assert "falló" in error and "https://github.com/u/r.git" in error


def test_uso_incorrecto_sale_con_64():
    env = dict(os.environ, BIBLIOTECA_SIN_ENTORNO="1")
    r = subprocess.run([sys.executable, str(PROYECTO / "biblioteca.py"), "publicar", "--remoto", "https://x/y.git", "--revisar"],
                       capture_output=True, text=True, env=env, check=False)
    assert r.returncode == 64 and "--remoto solo tiene sentido con --push" in r.stderr
    r = subprocess.run([sys.executable, str(PROYECTO / "biblioteca.py"), "publicar"], capture_output=True, text=True, env=env,
                       check=False)
    assert r.returncode == 64
    r = subprocess.run([sys.executable, str(PROYECTO / "biblioteca.py"), "publicar", "--pu"], capture_output=True, text=True,
                       env=env, check=False)
    assert r.returncode == 64                                                # sin abreviaturas: --pu no es --push


@pytest.mark.skipif(not os.environ.get("BIBLIOTECA_PRUEBAS_LENTAS"), reason="lenta: verificar.py hashea toda la biblioteca")
def test_revisar_real_devuelve_json(tmp_path):
    env = dict(os.environ, BIBLIOTECA_SIN_ENTORNO="1")
    r = subprocess.run([sys.executable, str(PROYECTO / "biblioteca.py"), "publicar", "--revisar", "--json"],
                       capture_output=True, text=True, env=env, check=False, timeout=900)
    datos = json.loads(r.stdout.strip().splitlines()[-1])
    assert datos["herramienta"] == "publicar" and datos["modo"] == "revisar"
    assert {"verificador", "grandes", "sumas", "cambios", "git", "listo", "siguiente"} <= set(datos)
    assert datos["codigo"] in (0, 1) and datos["listo"] == (datos["codigo"] == 0)


# ---------------------------------------------------------------------------
# Los modos completos (--preparar, --commit, --push) sobre carpetas y repositorios temporales
# ---------------------------------------------------------------------------
@pytest.fixture
def proyecto(repositorio, monkeypatch):
    """El repositorio temporal como si fuera el proyecto: rutas, verificador, catálogo, bitácora y candado
    sustituidos para que nada toque la biblioteca real. La bitácora se escribe con el escritor real (almacen.bitacora)
    sobre el archivo temporal; `eventos` guarda además cada evento en memoria."""
    eventos = []
    bitacora_tmp = repositorio / "bitacora.jsonl"
    monkeypatch.setattr(publicar, "RAIZ", repositorio)
    monkeypatch.setattr(publicar, "DOCUMENTOS", repositorio / "documentos")
    monkeypatch.setattr(publicar, "CAMBIOS", repositorio / "CAMBIOS.md")
    monkeypatch.setattr(publicar, "BITACORA", bitacora_tmp)
    monkeypatch.setattr(comun, "BITACORA", bitacora_tmp)
    monkeypatch.setattr(publicar, "verificador", lambda: {"integridad": True, "errores": [], "avisos": [], "pendientes": 0,
                                                          "catalogo": 1, "en_disco": {"pdf": 1}, "markdown": 0})
    monkeypatch.setattr(publicar, "_nombres", lambda: {"federal__ley_a": "Ley A"})

    def registrar(evento):
        eventos.append(evento)
        comun.bitacora(evento)
    monkeypatch.setattr(publicar, "bitacora", registrar)
    monkeypatch.setattr(publicar, "bloqueo", nullcontext)
    monkeypatch.setattr(publicar, "_momento", comun.ahora)         # sin esperar al siguiente segundo (más rápido)
    with open(bitacora_tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"fecha": "2026-10-01 09:00:00", "herramienta": "descargar", "id": "federal__ley_a",
                            "resultado": "NUEVO", "fecha_reforma": "2026-09-30"}) + "\n")
    return repositorio, eventos


def _bare(raiz: Path, nombre: str, monkeypatch) -> str:
    """Un remoto 'bare' local que hace de GitHub (la URL file:// solo se admite en las pruebas)."""
    remoto = raiz.parent / nombre
    _git(raiz.parent, "init", "-q", "--bare", str(remoto))
    monkeypatch.setattr(publicar, "_URL_REMOTO", re.compile(r"^file://\S+$"))
    return remoto.as_uri()


def test_cmd_preparar_escribe_sumas_y_cambios_y_es_idempotente(proyecto, capsys):
    raiz, eventos = proyecto
    publicar.cmd_preparar()
    sumas = (raiz / "documentos" / "SHA256SUMS").read_text(encoding="utf-8")
    assert sumas == f"{hashlib.sha256(b'%PDF-1.4 a').hexdigest()}  a.pdf\n"
    cambios = (raiz / "CAMBIOS.md").read_text(encoding="utf-8")
    assert cambios.startswith(publicar.ENCABEZADO_CAMBIOS) and "## " in cambios
    assert "- Ley A (`federal__ley_a`) — reforma 2026-09-30" in cambios
    assert eventos[-1]["accion"] == "preparar" and eventos[-1]["entrada"] is True and eventos[-1]["conteos"]["nuevos"] == 1
    assert eventos[-1]["sumas"] == {"archivos": 1, "reescrito": True}
    texto = capsys.readouterr().out
    assert "SHA256SUMS: generado (1 archivos)" in texto and "CAMBIOS.md: entrada añadida" in texto
    assert "Siguiente paso: python biblioteca.py publicar --commit" in texto
    # segunda vez: nada nuevo en la bitácora (el marcador de la entrada es posterior) y las sumas siguen al día
    publicar.cmd_preparar()
    assert (raiz / "CAMBIOS.md").read_text(encoding="utf-8") == cambios
    assert eventos[-1]["entrada"] is False and eventos[-1]["sumas"]["reescrito"] is False
    texto = capsys.readouterr().out
    assert "no se añade entrada" in texto and "Nada que preparar" in texto and "Siguiente paso" not in texto


def test_cmd_preparar_exige_integridad(proyecto, monkeypatch, capsys):
    raiz, _ = proyecto
    monkeypatch.setattr(publicar, "verificador", lambda: {"integridad": False, "errores": ["algo roto"], "avisos": []})
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_preparar()
    assert fin.value.code == 1 and "algo roto" in capsys.readouterr().out
    assert not (raiz / "documentos" / "SHA256SUMS").exists() and not (raiz / "CAMBIOS.md").exists()


def test_cmd_preparar_no_pierde_ni_duplica_eventos_concurrentes(proyecto, monkeypatch):
    """Un agente registra un evento mientras --preparar trabaja (dentro del candado, después del momento de la
    publicación): no entra en esta entrada y sí en la siguiente."""
    raiz, _ = proyecto
    monkeypatch.setattr(publicar, "_momento", MOMENTO_ORIGINAL)

    @contextmanager
    def bloqueo_que_escribe():
        comun.bitacora({"herramienta": "descargar", "id": "jalisco__ley_b", "resultado": "NUEVO",
                          "fecha_reforma": "2026-10-02"})
        yield
    monkeypatch.setattr(publicar, "bloqueo", bloqueo_que_escribe)
    publicar.cmd_preparar()
    cambios = (raiz / "CAMBIOS.md").read_text(encoding="utf-8")
    assert "federal__ley_a" in cambios and "jalisco__ley_b" not in cambios
    publicar.cmd_preparar()
    cambios = (raiz / "CAMBIOS.md").read_text(encoding="utf-8")
    assert cambios.count("jalisco__ley_b") == 1 and cambios.count("federal__ley_a") == 1
    assert len(publicar.entradas(raiz / "CAMBIOS.md")) == 2


def test_ventana_toma_la_mas_reciente_entre_cambios_y_bitacora(proyecto, capsys):
    raiz, _ = proyecto
    publicar.cmd_preparar()
    marcador = publicar.ultima_publicacion(raiz / "CAMBIOS.md")
    assert marcador and publicar.ultima_preparacion(raiz / "bitacora.jsonl") == marcador
    # alguien borra el marcador y el encabezado queda irreconocible: la ventana sigue viniendo de la bitácora
    texto = (raiz / "CAMBIOS.md").read_text(encoding="utf-8")
    texto = re.sub(r"<!--.*?-->\n", "", texto).replace("## ", "Entrada del ")
    (raiz / "CAMBIOS.md").write_text(texto, encoding="utf-8")
    desde, avisos = publicar._ventana()
    assert desde == marcador and len(avisos) == 2
    assert any("ninguna entrada reconocible" in a for a in avisos) and any("más reciente" in a for a in avisos)
    publicar.cmd_preparar()
    salida_texto = capsys.readouterr().out
    assert "AVISO: CAMBIOS.md tiene contenido pero ninguna entrada reconocible" in salida_texto
    assert "no se añade entrada" in salida_texto           # la bitácora no volvió a volcarse
    assert (raiz / "CAMBIOS.md").read_text(encoding="utf-8") == texto


def test_cmd_commit_exige_preparar_y_luego_confirma(proyecto, capsys):
    raiz, eventos = proyecto
    with pytest.raises(SystemExit) as fin:          # sin SHA256SUMS todavía
        publicar.cmd_commit()
    assert fin.value.code == 1
    publicar.cmd_preparar()
    publicar.cmd_commit()
    texto = capsys.readouterr().out
    assert "Mensaje: Biblioteca " in texto and "1 nuevo, 0 actualizados" in texto
    assert eventos[-1]["accion"] == "commit" and eventos[-1]["cambios"] >= 4 and eventos[-1]["entradas"] == 1
    mensaje = _git(raiz, "log", "-1", "--format=%B")
    assert mensaje.startswith("Biblioteca ") and "Documentos nuevos (1):" in mensaje and publicar.COAUTORIA in mensaje
    assert publicar.estado_git(raiz)["commits"] == 1 and publicar.estado_git(raiz)["cambios"] == []
    assert "SHA256SUMS" in _git(raiz, "ls-files") and "trabajo/notas.txt" not in _git(raiz, "ls-files")
    with pytest.raises(SystemExit) as fin:          # sin cambios: no hay nada que confirmar
        publicar.cmd_commit()
    assert fin.value.code == 1
    # solo cambian las herramientas: el asunto lo dice, sin reutilizar la entrada ya confirmada
    (raiz / "herramienta.py").write_text("print(1)\n", encoding="utf-8")
    publicar.cmd_commit()
    assert "sin cambios en los documentos" in _git(raiz, "log", "-1", "--format=%s")
    # corregir una errata en la entrada ya confirmada tampoco la vuelve a anunciar
    cambios = raiz / "CAMBIOS.md"
    cambios.write_text(cambios.read_text(encoding="utf-8").replace("Ley A", "Ley A (corregida)"), encoding="utf-8")
    publicar.cmd_commit()
    assert "sin cambios en los documentos" in _git(raiz, "log", "-1", "--format=%s")


def test_cmd_commit_con_dos_entradas_pendientes_las_suma(proyecto, monkeypatch):
    raiz, _ = proyecto
    monkeypatch.setattr(publicar, "_momento", MOMENTO_ORIGINAL)   # los eventos siguientes deben quedar después del marcador
    publicar.cmd_preparar()                                  # 1 nuevo (se olvida el commit)
    for doc in ("jalisco__ley_b", "jalisco__ley_c"):
        comun.bitacora({"herramienta": "descargar", "id": doc, "resultado": "ACTUALIZADO", "fecha_reforma": "2026-10-02"})
    (raiz / "documentos" / "b.pdf").write_bytes(b"%PDF-1.4 b")
    publicar.cmd_preparar()                                  # 0 nuevos, 2 actualizados
    assert len(publicar.entradas(raiz / "CAMBIOS.md")) == 2
    publicar.cmd_commit()
    mensaje = _git(raiz, "log", "-1", "--format=%B")
    assert mensaje.startswith("Biblioteca ") and ": 1 nuevo, 2 actualizados\n" in mensaje
    assert "jalisco__ley_b" in mensaje and "federal__ley_a" in mensaje
    assert publicar.entradas_pendientes(raiz, raiz / "CAMBIOS.md") == []


def test_cmd_commit_se_niega_si_gitignore_no_tapa_trabajo(proyecto, capsys):
    raiz, _ = proyecto
    publicar.cmd_preparar()
    (raiz / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_commit()
    assert fin.value.code == 1 and "trabajo/notas.txt" in capsys.readouterr().out
    assert publicar.estado_git(raiz)["commits"] == 0


def test_cmd_commit_se_niega_si_sha256sums_lista_un_archivo_ignorado(proyecto, capsys):
    raiz, _ = proyecto
    (raiz / "documentos" / "secreto.pdf").write_bytes(b"%PDF-1.4 s")
    with open(raiz / ".gitignore", "a", encoding="utf-8") as f:
        f.write("documentos/secreto.pdf\n")
    publicar.cmd_preparar()
    assert "secreto.pdf" in (raiz / "documentos" / "SHA256SUMS").read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_commit()
    assert fin.value.code == 1 and "documentos/secreto.pdf" in capsys.readouterr().out
    assert publicar.estado_git(raiz)["commits"] == 0


def test_cmd_push_sin_remoto_explica_y_con_remoto_local_sube(proyecto, monkeypatch, capsys):
    raiz, eventos = proyecto
    publicar.cmd_preparar()
    publicar.cmd_commit()
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push(None)
    assert fin.value.code == 1 and "https://github.com/new" in capsys.readouterr().out
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push("no es una url")
    assert fin.value.code == 1
    remoto = _bare(raiz, "remoto.git", monkeypatch)
    publicar.cmd_push(remoto)
    texto = capsys.readouterr().out
    assert "primera subida" in texto and "Publicado:" in texto and "tope: 60 min" in texto
    assert _git(Path(remoto[7:]), "rev-parse", "main") == _git(raiz, "rev-parse", "main")
    estado = publicar.estado_git(raiz)
    assert estado["origen_main"] is True and estado["adelante"] == 0 and estado["remoto"] == remoto
    assert eventos[-1] == {"herramienta": "publicar", "accion": "push", "remoto": remoto, "rama": "main",
                           "commit": _git(raiz, "rev-parse", "--short", "HEAD"), "resultado": "OK",
                           "bytes_estimados": eventos[-1]["bytes_estimados"], "primera": True}
    # tras el push solo cambió la bitácora: --revisar dice que todo está publicado y --commit no hace commits vacíos
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_revisar()
    texto = capsys.readouterr().out
    assert fin.value.code == 0 and "LISTO PARA PUBLICAR: SÍ · siguiente paso: nada: todo está publicado" in texto
    assert "0 cambio(s) sin commit (solo la bitácora del push anterior)" in texto
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_commit()
    assert fin.value.code == 1 and "solo la bitácora del push anterior" in capsys.readouterr().err
    # segunda subida con un commit nuevo
    (raiz / "otra.txt").write_text("x\n", encoding="utf-8")
    publicar.cmd_commit()
    publicar.cmd_push(None)
    assert "1 commit(s) nuevos" in capsys.readouterr().out
    assert _git(Path(remoto[7:]), "rev-parse", "main") == _git(raiz, "rev-parse", "main")


def test_cmd_push_rechaza_credenciales_en_la_url_sin_repetirlas(proyecto, capsys):
    raiz, eventos = proyecto
    publicar.cmd_preparar()
    publicar.cmd_commit()
    n = len(eventos)
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push("https://usuario:SECRETO@github.com/u/r.git")
    assert fin.value.code == 1
    capturado = capsys.readouterr()
    assert "SECRETO" not in capturado.err and "SECRETO" not in capturado.out
    assert "credenciales" in capturado.err and "gh auth login" in capturado.err
    assert len(eventos) == n and "SECRETO" not in (raiz / "bitacora.jsonl").read_text(encoding="utf-8")
    assert "SECRETO" not in (raiz / ".git" / "config").read_text(encoding="utf-8")


def test_cmd_push_cambiar_de_remoto_vuelve_a_ser_primera_subida(proyecto, monkeypatch, capsys):
    raiz, _ = proyecto
    publicar.cmd_preparar()
    publicar.cmd_commit()
    a = _bare(raiz, "a.git", monkeypatch)
    publicar.cmd_push(a)
    assert publicar.estado_git(raiz)["origen_main"] is True
    b = _bare(raiz, "b.git", monkeypatch)
    monkeypatch.setattr(publicar, "LIMITE_PUSH_BYTES", 1)
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push(b)
    texto = capsys.readouterr().out
    assert fin.value.code == 1 and "primera subida" in texto and "ESTRATEGIA POR BLOQUES" in texto
    assert "main" not in _git(Path(b[7:]), "branch", "--list")
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/remotes/origin/main"], cwd=raiz, capture_output=True,
                       text=True, check=False)
    assert r.returncode != 0                                   # el origin/main del remoto anterior ya no existe
    assert publicar.estado_git(raiz)["remoto"] == b


def test_cmd_push_se_niega_si_pesa_mas_del_limite(proyecto, monkeypatch, capsys):
    raiz, _ = proyecto
    publicar.cmd_preparar()
    publicar.cmd_commit()
    remoto = _bare(raiz, "remoto2.git", monkeypatch)
    monkeypatch.setattr(publicar, "LIMITE_PUSH_BYTES", 1)
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push(remoto)
    texto = capsys.readouterr().out
    assert fin.value.code == 1 and "ESTRATEGIA POR BLOQUES" in texto
    assert "main" not in _git(Path(remoto[7:]), "branch", "--list")          # nada se subió


def test_cmd_push_vencido_queda_en_la_bitacora(proyecto, monkeypatch, capsys):
    raiz, eventos = proyecto
    publicar.cmd_preparar()
    publicar.cmd_commit()
    remoto = _bare(raiz, "remoto3.git", monkeypatch)
    real = publicar.correr_hijo

    def lento(cmd, timeout, **kwargs):
        if "push" in cmd[:4]:
            raise subprocess.TimeoutExpired(cmd, timeout)
        return real(cmd, timeout, **kwargs)
    monkeypatch.setattr(publicar, "correr_hijo", lento)
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push(remoto)
    capturado = capsys.readouterr()
    assert fin.value.code == 1 and "no reanuda subidas, así que nada quedó en el remoto" in capturado.err
    assert eventos[-1]["accion"] == "push" and eventos[-1]["resultado"] == "ERROR" and "cancelado" in eventos[-1]["detalle"]
    assert "main" not in _git(Path(remoto[7:]), "branch", "--list")


# ---------------------------------------------------------------------------
# push por bloques (GitHub rechaza un push de 2 GB o más)
# ---------------------------------------------------------------------------
def test_agrupar_en_bloques_nunca_parte_un_commit():
    pesos = [("a", 5), ("b", 1), ("c", 1), ("d", 9), ("e", 2), ("f", 2)]
    assert publicar.agrupar_en_bloques(pesos, 4) == [("a", 5, 1), ("c", 2, 2), ("d", 9, 1), ("f", 4, 2)]
    assert publicar.agrupar_en_bloques(pesos, 100) == [("f", 20, 6)]
    assert publicar.agrupar_en_bloques([], 4) == []


def _binario(ruta: Path, semilla: int, n: int = 200_000) -> None:
    """Un PDF de mentira que zlib no puede comprimir: pesa en git lo mismo que en disco."""
    ruta.write_bytes(b"%PDF-1.4\n" + random.Random(semilla).randbytes(n))


def test_pesos_de_commits_suman_lo_que_se_sube_y_cuentan_las_ramas_unidas(repositorio):
    publicar.hacer_commit(repositorio, "uno", "")
    _git(repositorio, "switch", "-q", "-c", "lateral")
    _binario(repositorio / "documentos" / "lateral.pdf", 1, 50_000)
    publicar.hacer_commit(repositorio, "lateral", "")
    _git(repositorio, "switch", "-q", "main")
    _binario(repositorio / "documentos" / "principal.pdf", 2, 30_000)
    publicar.hacer_commit(repositorio, "principal", "")
    _git(repositorio, "merge", "-q", "--no-ff", "-m", "unión", "lateral")
    commits = publicar.commits_por_subir(repositorio, None)
    assert len(commits) == 3                       # la línea principal: uno, principal y la unión (no el commit lateral)
    assert commits[-1] == _git(repositorio, "rev-parse", "main")
    pesos = publicar.pesos_de_commits(repositorio, commits, None)
    if pesos is None:
        pytest.skip("este git no tiene rev-list --disk-usage")
    assert sum(peso for _, peso in pesos) == publicar.objetos_por_subir(repositorio, "main")
    assert 30_000 < pesos[1][1] < 50_000 < pesos[2][1]          # la unión sube lo de la rama lateral
    assert publicar.pesos_de_commits(repositorio, commits[1:], commits[0]) == pesos[1:]


def test_cmd_push_por_bloques_y_retoma_desde_el_que_fallo(proyecto, monkeypatch, capsys):
    raiz, eventos = proyecto
    publicar.cmd_preparar()
    publicar.cmd_commit()                                      # commit 1: pequeño
    for i in range(3):                                         # commits 2-4: unos 200 KB cada uno
        _binario(raiz / "documentos" / f"grande{i}.pdf", i)
        publicar.hacer_commit(raiz, f"grande {i}", "")
    commits = _git(raiz, "rev-list", "--reverse", "main").split()
    monkeypatch.setattr(publicar, "BLOQUE_PUSH_BYTES", 300_000)
    remoto = _bare(raiz, "bloques.git", monkeypatch)
    real = publicar.correr_hijo
    pushes: list[list[str]] = []

    def falla_el_segundo(cmd, timeout, **kwargs):
        if "push" in cmd[:4]:
            pushes.append(cmd)
            if len(pushes) == 2:
                return subprocess.CompletedProcess(cmd, 1, "", "error: RPC failed; HTTP 500 curl 22")
        return real(cmd, timeout, **kwargs)
    monkeypatch.setattr(publicar, "correr_hijo", falla_el_segundo)
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push(remoto)
    capturado = capsys.readouterr()
    assert fin.value.code == 1 and "Se sube POR BLOQUES: 3 push" in capturado.out
    assert "bloque 1/3: 2 commits hasta" in capturado.out and "bloque 3/3: 1 commit hasta" in capturado.out
    assert "Ya están en el remoto 1 de 3 bloques: repite --push y sigue desde el bloque 2" in capturado.err
    # el primero, sin -u y hasta un commit; sin el cálculo sparse, que reenviaría lo que solo cambió de carpeta
    assert pushes[0][1:] == ["-c", "pack.useSparse=false", "push", "origin", f"{commits[1]}:refs/heads/main"]
    assert _git(Path(remoto[7:]), "rev-parse", "main") == commits[1]              # lo ya subido se quedó
    assert [(e.get("bloque"), e.get("resultado"), e.get("primera")) for e in eventos[-2:]] == [
        ("1/3", "OK", True), ("2/3", "ERROR", None)]
    estado = publicar.estado_git(raiz)
    assert estado["origen_main"] is True and estado["adelante"] == 2
    # repetir --push sigue desde lo que falta: dos bloques más, el último con -u
    monkeypatch.setattr(publicar, "correr_hijo", real)
    publicar.cmd_push(None)
    texto = capsys.readouterr().out
    assert "2 commit(s) nuevos" in texto and "Se sube POR BLOQUES: 2 push" in texto
    assert "Comprobado en el remoto: main = " + commits[3][:7] in texto and "en 2 bloques" in texto
    assert _git(Path(remoto[7:]), "rev-parse", "main") == commits[3]
    assert [(e.get("bloque"), e.get("resultado"), e.get("primera")) for e in eventos[-2:]] == [
        ("1/2", "OK", False), ("2/2", "OK", False)]
    estado = publicar.estado_git(raiz)
    assert estado["adelante"] == 0
    assert _git(raiz, "rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main"


def test_cmd_push_no_sube_nada_si_un_commit_solo_pasa_del_limite(proyecto, monkeypatch, capsys):
    raiz, eventos = proyecto
    publicar.cmd_preparar()
    publicar.cmd_commit()
    _binario(raiz / "documentos" / "enorme.pdf", 9)
    publicar.hacer_commit(raiz, "enorme", "")
    (raiz / "otra.txt").write_text("x\n", encoding="utf-8")
    publicar.hacer_commit(raiz, "chico", "")
    monkeypatch.setattr(publicar, "BLOQUE_PUSH_BYTES", 50_000)
    monkeypatch.setattr(publicar, "LIMITE_PUSH_BYTES", 150_000)
    remoto = _bare(raiz, "limite.git", monkeypatch)
    n = len(eventos)
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_push(remoto)
    capturado = capsys.readouterr()
    assert fin.value.code == 1 and "ESTRATEGIA POR BLOQUES" in capturado.out and "más de 1.8 GiB" in capturado.err
    assert "main" not in _git(Path(remoto[7:]), "branch", "--list") and len(eventos) == n


def test_cmd_revisar_no_esta_listo_sin_preparar(proyecto, capsys):
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_revisar()
    texto = capsys.readouterr().out
    assert fin.value.code == 1 and "LISTO PARA PUBLICAR: NO · siguiente paso: python biblioteca.py publicar --preparar" in texto
    publicar.cmd_preparar()
    capsys.readouterr()
    with pytest.raises(SystemExit) as fin:
        publicar.cmd_revisar()
    texto = capsys.readouterr().out
    assert fin.value.code == 0 and "siguiente paso: python biblioteca.py publicar --commit" in texto



# ====================================================================================================
# verificar: originales de .agents/, enlaces simbólicos y páginas web
# ====================================================================================================
def test_original_de_agents_mal_formado_es_error_y_no_tumba_verificar(tmp_path, monkeypatch):
    agentes, comandos = tmp_path / ".agents" / "agents", tmp_path / ".agents" / "skills"
    agentes.mkdir(parents=True)
    comandos.mkdir(parents=True)
    for nombre, valor in {"RAIZ": tmp_path, "AGENTES": agentes, "COMANDOS": comandos}.items():
        monkeypatch.setattr(verificar, nombre, valor)
    original = agentes / "instalador.md"
    original.write_text("---\nname: instalador\ndescription: x\n", encoding="utf-8")       # sin el '---' de cierre
    assert "no se pueden generar las copias" in (verificar.error_de_copias() or "")
    original.write_text("---\nname: otro\ndescription: x\n---\ncuerpo\n", encoding="utf-8")
    assert "debe ser 'instalador'" in (verificar.error_de_copias() or "")
    original.write_text("---\nname: instalador\ndescription: x\n---\ncuerpo\n", encoding="utf-8")
    assert "no coinciden" in (verificar.error_de_copias() or "")                    # bien formado, sin su copia


def test_verificar_ve_los_enlaces_simbolicos_de_la_biblioteca(tmp_path):
    (tmp_path / "Estados").mkdir()
    real = tmp_path / "real.pdf"
    real.write_bytes(b"%PDF")
    (tmp_path / "Estados" / "x.pdf").symlink_to(real)
    (tmp_path / "carpeta").symlink_to(tmp_path / "Estados", target_is_directory=True)
    assert sorted(p.name for p in verificar.enlaces_simbolicos(tmp_path)) == ["carpeta", "x.pdf"]


def test_verificar_marca_las_paginas_web_y_acepta_solo_pdf_o_word():
    assert verificar.tipo_de_archivo("PDF/Estados/Jalisco/Ley_de_Salud.pdf") == "documento"
    assert verificar.tipo_de_archivo("PDF/Estados/Baja_California_Sur/Ley_de_Salud.doc") == "documento"
    assert verificar.tipo_de_archivo("PDF/Estados/Chiapas/Ley.docx") == "documento"
    assert verificar.tipo_de_archivo("Markdown/Estados/Jalisco/Ley_de_Salud.md") == "markdown"
    assert verificar.tipo_de_archivo("PDF/Constitucion/Reformas/Decretos.html") == "web"
    assert verificar.tipo_de_archivo("PDF/Federal/Fiscal/Ley.HTM") == "web"
    assert verificar.tipo_de_archivo("manifiesto.json") == "dato"
    assert verificar.tipo_de_archivo("PDF/Estados/Jalisco/notas.txt") == "otro"
    assert verificar.tipo_de_archivo("ley_suelta.pdf") == "otro"               # fuera de la carpeta de su grupo
    # cada cosa en su carpeta: el documento oficial en PDF/ y el Markdown en Markdown/
    assert verificar.tipo_de_archivo("Estados/Jalisco/Ley_de_Salud.pdf") == "otro"
    assert verificar.tipo_de_archivo("PDF/Estados/Jalisco/Ley_de_Salud.md") == "otro"
    assert verificar.tipo_de_archivo("Markdown/Estados/Jalisco/Ley_de_Salud.pdf") == "otro"
