"""Pruebas de herramientas/catalogo.py."""
from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
from pathlib import Path

import pytest

from herramientas import catalogo, comun


# ====================================================================================================
# test_estructura
# ====================================================================================================
def test_grupos_validos():
    assert catalogo.validar_grupo("Constitucion") is None
    assert catalogo.validar_grupo("Constitucion/Reformas") is None
    assert catalogo.validar_grupo("Federal/Fiscal") is None
    assert catalogo.validar_grupo("Federal/Fiscal/Abrogadas") is None
    assert catalogo.validar_grupo("Federal/Fiscal/Resolucion_Miscelanea_Fiscal_2026") is None
    assert catalogo.validar_grupo("Federal/Tratados_internacionales/Comercio") is None
    assert catalogo.validar_grupo("Estados/Jalisco", "Jalisco") is None


def test_grupos_invalidos():
    assert catalogo.validar_grupo("Estados/Jalisco") is not None            # estatal sin entidad
    assert catalogo.validar_grupo("Federal/Fiscal", "Jalisco") is not None  # entidad en carpeta federal
    assert catalogo.validar_grupo("Federal/Inventada") is not None
    assert catalogo.validar_grupo("Federal/Fiscal/Otra") is not None
    assert catalogo.validar_grupo("Constitucion/Otra") is not None
    assert catalogo.validar_grupo("Federal/Tratados_internacionales") is not None


def test_abrogadas_solo_en_materias_federales():
    assert catalogo.grupo_segun_estado("Federal/Energia", "abrogado") == "Federal/Energia/Abrogadas"
    assert catalogo.grupo_segun_estado("Federal/Energia/Abrogadas", "vigente") == "Federal/Energia"
    assert catalogo.grupo_segun_estado("Federal/Fiscal/Resolucion_Miscelanea_Fiscal_2026", "abrogado") == \
        "Federal/Fiscal/Abrogadas/Resolucion_Miscelanea_Fiscal_2026"
    assert catalogo.grupo_segun_estado("Estados/Jalisco", "abrogado") == "Estados/Jalisco"
    assert catalogo.grupo_segun_estado("Constitucion", "abrogado") == "Constitucion"
    assert catalogo.grupo_segun_estado("Federal/Tratados_internacionales/Comercio", "abrogado") == \
        "Federal/Tratados_internacionales/Comercio"


def test_problema_de_estructura():
    doc = {"id": "x", "nombre": "Ley de Amparo", "grupo": "Federal/Amparo_y_Poder_Judicial", "archivo": "Ley_de_Amparo",
           "estado": "vigente"}
    assert catalogo.problema_de_estructura(doc) is None
    assert "abrogado" in catalogo.problema_de_estructura(dict(doc, estado="abrogado"))
    assert "no corresponde a su nombre" in catalogo.problema_de_estructura(dict(doc, archivo="Otro"))


# ====================================================================================================
# test_esquema
# ====================================================================================================
REGISTRO = {"archivo": "Federal/Fiscal/Ley_X.pdf", "formato": "pdf", "sha256": "a" * 64, "bytes": 10,
            "url": "https://www.diputados.gob.mx/x.pdf", "fecha_reforma": "2026-01-31", "fecha_descarga": "2026-10-06",
            "ultima_verificacion": "2026-10-06", "markdown": None}
DOC = {"id": "federal__ley_x", "nombre": "Ley X", "grupo": "Federal/Fiscal", "archivo": "Ley_X", "estado": "vigente",
       "pistas": {"urls": [{"url": "https://www.diputados.gob.mx/x.pdf", "fecha": "2026-10-06", "resultado": "ok"}]}}


def test_registro_valido_y_variantes():
    assert catalogo.problemas_de_esquema_registro(REGISTRO) == []
    assert catalogo.problemas_de_esquema_registro(dict(REGISTRO, fecha_reforma=None)) == []
    con_md = dict(REGISTRO, markdown={"archivo": "x.md", "sha256_origen": "a" * 64, "metodo": "ocr",
                                      "caracteres": 5, "fecha": "2026-10-06"})
    assert catalogo.problemas_de_esquema_registro(con_md) == []
    comprimido = dict(REGISTRO, compresion={"nivel": 30, "sha256_original": "b" * 64, "bytes_original": 20,
                                            "sha256": "a" * 64, "bytes": 10, "fecha": "2026-10-06"})
    assert catalogo.problemas_de_esquema_registro(comprimido) == []


def test_registro_invalido():
    p = catalogo.problemas_de_esquema_registro(dict(REGISTRO, md5="x", fecha_reforma="31-01-2026", formato="zip"))
    assert any("esquema 1" in x for x in p) and any("fecha_reforma" in x for x in p) and any("formato" in x for x in p)
    assert catalogo.problemas_de_esquema_registro(dict(REGISTRO, sha256="corta"))


def test_documento_valido_e_invalido():
    assert catalogo.problemas_de_esquema_doc(DOC) == []
    assert catalogo.problemas_de_esquema_doc(dict(DOC, entidad="Jalisco", nota="x", sin_pdf=True, nombres_anteriores=["a"])) == []
    p = catalogo.problemas_de_esquema_doc(dict(DOC, ambito="federal", nota="", estado="otro"))
    assert len(p) == 3
    malo = dict(DOC, pistas={"urls": [{"url": "u", "fecha": "06-10-2026", "resultado": "falló"}]})
    assert len(catalogo.problemas_de_esquema_doc(malo)) == 2


def test_huella_origen_y_markdown_al_dia(tmp_path, monkeypatch):
    assert catalogo.huella_origen({"sha256": "a" * 64}) == "a" * 64
    assert catalogo.huella_origen({"sha256": "a" * 64, "compresion": {"sha256_original": "b" * 64}}) == "b" * 64
    monkeypatch.setattr(catalogo, "DOCUMENTOS", tmp_path)
    doc = {"grupo": "Federal/Fiscal", "archivo": "Ley_X"}
    reg = {"sha256": "a" * 64, "markdown": {"sha256_origen": "a" * 64}}
    assert not catalogo.markdown_al_dia(doc, reg)                     # el .md no existe
    (tmp_path / "Markdown" / "Federal" / "Fiscal").mkdir(parents=True)
    (tmp_path / "Markdown" / "Federal" / "Fiscal" / "Ley_X.md").write_text("x")
    assert catalogo.markdown_al_dia(doc, reg)
    assert not catalogo.markdown_al_dia(doc, dict(reg, sha256="c" * 64))   # el PDF cambió


def test_datos_reales_cumplen_el_esquema():
    cat = json.loads(comun.CATALOGO.read_text(encoding="utf-8"))
    assert cat["version"] == comun.VERSION_CATALOGO
    malos = [(d.get("id"), p) for d in cat["documentos"] if (p := catalogo.problemas_de_esquema_doc(d))]
    assert malos == []
    if comun.MANIFIESTO.exists():
        man = json.loads(comun.MANIFIESTO.read_text(encoding="utf-8"))
        assert man["version"] == comun.VERSION_MANIFIESTO
        malos = [(i, catalogo.problemas_de_esquema_registro(r)) for i, r in man["documentos"].items()
                 if catalogo.problemas_de_esquema_registro(r)]
        assert malos == []


# ====================================================================================================
# test_pistas
# ====================================================================================================
def _doc_pista():
    return {"id": "x", "pistas": {"urls": [{"url": "https://a/1.pdf", "fecha": "2026-01-01", "resultado": "ok"}]}}


def test_misma_pista_no_reescribe():
    d = _doc_pista()
    assert catalogo.registrar_pista(d, "https://a/1.pdf", "ok") is False
    assert d["pistas"]["urls"][0]["fecha"] == "2026-01-01"          # la fecha no cambia: no ensucia el catálogo
    assert catalogo.registrar_pista(d, "http://a/1.pdf/", "ok") is False   # mismo sin esquema ni barra final


def test_pista_nueva_va_al_frente_sin_duplicar():
    d = _doc_pista()
    assert catalogo.registrar_pista(d, "https://b/2.pdf", "ok") is True
    assert [u["url"] for u in d["pistas"]["urls"]] == ["https://b/2.pdf", "https://a/1.pdf"]
    assert catalogo.registrar_pista(d, "https://a/1.pdf", "falla", "HTTP 404") is True
    assert d["pistas"]["urls"][0] == {"url": "https://a/1.pdf", "fecha": d["pistas"]["urls"][0]["fecha"],
                                      "resultado": "falla", "nota": "HTTP 404"}
    assert len(d["pistas"]["urls"]) == 2


def test_maximo_diez_pistas():
    d = _doc_pista()
    for i in range(15):
        catalogo.registrar_pista(d, f"https://c/{i}.pdf", "sin_probar")
    assert len(d["pistas"]["urls"]) == 10


def test_resultado_invalido():
    with pytest.raises(ValueError):
        catalogo.registrar_pista(_doc_pista(), "https://z", "falló")


# ====================================================================================================
# Pruebas de herramientas/catalogo.py sobre rutas y movimiento de documentos: mover_documento (el único lugar
# ====================================================================================================
@pytest.fixture
def biblioteca(tmp_path, monkeypatch):
    monkeypatch.setattr(catalogo, "DOCUMENTOS", tmp_path)
    return tmp_path


def _doc_ley(**cambios):
    doc = {"id": "lh", "nombre": "Ley de Hidrocarburos", "grupo": "Federal/Energia",
           "archivo": "Ley_de_Hidrocarburos", "estado": "vigente"}
    doc.update(cambios)
    return doc


def _guardar(doc, formato="pdf", markdown=True):
    """Crea el PDF (o Word) y el Markdown del documento y devuelve su manifiesto."""
    pdf = catalogo.ruta_pdf(doc, formato)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 prueba")
    registro = {"archivo": catalogo.ruta_relativa_pdf(doc, formato)}
    if markdown:
        md = catalogo.ruta_md(doc)
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("# prueba", encoding="utf-8")
        registro["markdown"] = {"archivo": catalogo.ruta_relativa_md(doc)}
    return {doc["id"]: registro}


def test_mover_documento_lleva_pdf_y_markdown_a_la_vez_y_corrige_el_manifiesto(biblioteca):
    doc = _doc_ley()
    manifiesto = _guardar(doc)
    assert catalogo.mover_documento(doc, "Federal/Energia/Abrogadas", {"documentos": [doc]}, manifiesto) is True
    assert (doc["grupo"], doc["archivo"]) == ("Federal/Energia/Abrogadas", "Ley_de_Hidrocarburos")
    assert (biblioteca / "PDF/Federal/Energia/Abrogadas/Ley_de_Hidrocarburos.pdf").read_bytes() == b"%PDF-1.4 prueba"
    assert (biblioteca / "Markdown/Federal/Energia/Abrogadas/Ley_de_Hidrocarburos.md").read_text() == "# prueba"
    assert not (biblioteca / "PDF/Federal/Energia/Ley_de_Hidrocarburos.pdf").exists()
    assert not (biblioteca / "Markdown/Federal/Energia/Ley_de_Hidrocarburos.md").exists()
    assert manifiesto["lh"]["archivo"] == "PDF/Federal/Energia/Abrogadas/Ley_de_Hidrocarburos.pdf"
    assert manifiesto["lh"]["markdown"]["archivo"] == "Markdown/Federal/Energia/Abrogadas/Ley_de_Hidrocarburos.md"


def test_mover_documento_borra_las_carpetas_vacias_pero_no_las_raices(biblioteca):
    doc = _doc_ley()
    manifiesto = _guardar(doc)
    catalogo.mover_documento(doc, "Constitucion", {"documentos": [doc]}, manifiesto)
    assert not (biblioteca / "PDF/Federal").exists() and not (biblioteca / "Markdown/Federal").exists()
    assert (biblioteca / "PDF/Constitucion/Ley_de_Hidrocarburos.pdf").exists()
    assert (biblioteca / "Markdown/Constitucion/Ley_de_Hidrocarburos.md").exists()
    assert biblioteca.is_dir()                                        # la raíz de la biblioteca no se borra


def test_mover_documento_conserva_la_extension_real_del_word(biblioteca):
    doc = _doc_ley(id="bcs-salud", nombre="Ley de Salud", grupo="Estados/Baja_California_Sur",
               archivo="Ley_de_Salud", entidad="Baja California Sur")
    manifiesto = _guardar(doc, formato="doc", markdown=False)
    assert catalogo.mover_documento(doc, doc["grupo"], {"documentos": [doc]}, manifiesto,
                                      nuevo_archivo="Ley_de_Salud_para_el_Estado") is True
    carpeta = biblioteca / "PDF/Estados/Baja_California_Sur"
    assert (carpeta / "Ley_de_Salud_para_el_Estado.doc").exists()
    assert not (carpeta / "Ley_de_Salud.doc").exists() and not (carpeta / "Ley_de_Salud_para_el_Estado.pdf").exists()
    assert manifiesto["bcs-salud"]["archivo"] == "PDF/Estados/Baja_California_Sur/Ley_de_Salud_para_el_Estado.doc"
    assert "markdown" not in manifiesto["bcs-salud"]
    assert carpeta.is_dir()                                  # la carpeta sigue en uso: no se borra


def test_mover_documento_rechaza_un_grupo_fuera_de_la_estructura(biblioteca, capsys):
    doc = _doc_ley()
    manifiesto = _guardar(doc)
    with pytest.raises(SystemExit) as salida:
        catalogo.mover_documento(doc, "Federal/Inventada", {"documentos": [doc]}, manifiesto)
    assert salida.value.code == 1
    assert "no es parte de la estructura" in capsys.readouterr().err
    assert doc["grupo"] == "Federal/Energia"
    assert (biblioteca / "PDF/Federal/Energia/Ley_de_Hidrocarburos.pdf").exists()
    assert manifiesto["lh"]["archivo"] == "PDF/Federal/Energia/Ley_de_Hidrocarburos.pdf"


def test_mover_documento_estatal_no_sale_de_la_carpeta_de_su_estado(biblioteca):
    doc = _doc_ley(grupo="Estados/Jalisco", entidad="Jalisco")
    for destino in ("Estados/Colima", "Federal/Energia"):
        with pytest.raises(SystemExit):
            catalogo.mover_documento(doc, destino, {"documentos": [doc]}, {})
    assert doc["grupo"] == "Estados/Jalisco"


def test_mover_documento_rechaza_el_choque_con_otro_documento(biblioteca, capsys):
    doc = _doc_ley()
    otro = _doc_ley(id="lsh", nombre="Ley del Sector Hidrocarburos", grupo="Federal/Energia/Abrogadas")
    with pytest.raises(SystemExit):
        catalogo.mover_documento(doc, "Federal/Energia/Abrogadas", {"documentos": [doc, otro]}, {})
    assert "Ya existe lsh" in capsys.readouterr().err
    assert doc["grupo"] == "Federal/Energia"


def test_mover_documento_no_hace_nada_si_el_destino_es_el_mismo(biblioteca):
    doc = _doc_ley()
    manifiesto = _guardar(doc)
    cat = {"documentos": [doc]}
    assert catalogo.mover_documento(doc, "Federal/Energia", cat, manifiesto) is False
    assert catalogo.mover_documento(doc, "Federal/Energia", cat, manifiesto,
                                      nuevo_archivo="Ley_de_Hidrocarburos") is False
    assert (biblioteca / "PDF/Federal/Energia/Ley_de_Hidrocarburos.pdf").exists()
    assert manifiesto["lh"]["archivo"] == "PDF/Federal/Energia/Ley_de_Hidrocarburos.pdf"


def test_grupo_de_estado():
    assert catalogo.grupo_de_estado("Baja California Sur") == "Estados/Baja_California_Sur"
    assert catalogo.grupo_de_estado("Querétaro") == "Estados/Queretaro"
    assert catalogo.grupo_de_estado("Ciudad de México") == "Estados/Ciudad_de_Mexico"


def test_ruta_pdf_devuelve_el_archivo_que_exista(biblioteca):
    doc = _doc_ley()
    base = biblioteca / "PDF/Federal/Energia/Ley_de_Hidrocarburos"
    assert catalogo.ruta_pdf(doc) == base.with_suffix(".pdf")            # no hay nada: el .pdf
    assert catalogo.ruta_pdf(doc, "html") == base.with_suffix(".html")
    base.parent.mkdir(parents=True)
    base.with_suffix(".doc").write_bytes(b"doc")
    assert catalogo.ruta_pdf(doc) == base.with_suffix(".doc")            # solo existe el Word
    base.with_suffix(".pdf").write_bytes(b"pdf")
    assert catalogo.ruta_pdf(doc) == base.with_suffix(".pdf")            # con ambos gana el PDF


def test_rutas_relativas_como_en_el_manifiesto(biblioteca):
    doc = _doc_ley()
    assert catalogo.ruta_relativa_pdf(doc) == "PDF/Federal/Energia/Ley_de_Hidrocarburos.pdf"
    assert catalogo.ruta_relativa_pdf(doc, "docx") == "PDF/Federal/Energia/Ley_de_Hidrocarburos.docx"
    assert catalogo.ruta_relativa_md(doc) == "Markdown/Federal/Energia/Ley_de_Hidrocarburos.md"
    estatal = _doc_ley(grupo="Estados/Nuevo_Leon", entidad="Nuevo León")
    assert catalogo.ruta_md(estatal) == biblioteca / "Markdown/Estados/Nuevo_Leon/Ley_de_Hidrocarburos.md"


def test_buscar_documento_por_id_o_con_sugerencia(capsys):
    cat = {"documentos": [_doc_ley(id="ley-hidrocarburos"), _doc_ley(id="ley-aduanera")]}
    assert catalogo.buscar_documento(cat, "ley-aduanera")["id"] == "ley-aduanera"
    with pytest.raises(SystemExit) as salida:
        catalogo.buscar_documento(cat, "ley-hidrocarburo")
    assert salida.value.code == 1
    err = capsys.readouterr().err
    assert "No existe el id 'ley-hidrocarburo'" in err and "ley-hidrocarburos" in err
    with pytest.raises(SystemExit):
        catalogo.buscar_documento(cat, "zzzz")
    assert "Quisiste decir" not in capsys.readouterr().err


def test_pendiente_de_descarga():
    assert catalogo.pendiente_de_descarga(_doc_ley(), {}) is True
    assert catalogo.pendiente_de_descarga(_doc_ley(estado="abrogado"), {}) is True
    assert catalogo.pendiente_de_descarga(_doc_ley(), {"lh": {}}) is False
    assert catalogo.pendiente_de_descarga(_doc_ley(sin_pdf=True), {}) is False
    assert catalogo.pendiente_de_descarga(_doc_ley(estado="no_existe"), {}) is False
    assert catalogo.pendiente_de_descarga(_doc_ley(estado="no_localizado"), {}) is False


def test_exigir_estructura_detiene_si_el_archivo_no_es_el_nombre_oficial(capsys):
    catalogo.exigir_estructura(_doc_ley())
    with pytest.raises(SystemExit):
        catalogo.exigir_estructura(_doc_ley(archivo="Otro"))
    assert "No se escribió nada" in capsys.readouterr().err


def test_retirar_pdf_funciona_si_la_carpeta_temporal_esta_en_otro_disco(biblioteca, tmp_path, monkeypatch):
    # La copia de seguridad va a anteriores/, en la carpeta temporal del sistema: puede ser otro disco, donde un simple
    # rename falla con EXDEV.
    otro_disco = tmp_path / "otro_disco"
    doc = _doc_ley()
    cat, man = {"documentos": [doc]}, _guardar(doc)
    for nombre, valor in {"ANTERIORES": otro_disco / "anteriores", "bloqueo": contextlib.nullcontext,
                          "cargar_catalogo": lambda: cat, "cargar_manifiesto": lambda: man,
                          "guardar_catalogo": lambda c: None, "guardar_manifiesto": lambda m: None,
                          "bitacora": lambda e: None}.items():
        monkeypatch.setattr(catalogo, nombre, valor)
    reales = {"rename": os.rename, "replace": os.replace}

    def sin_cruzar_discos(funcion):
        def mover(origen, destino):
            if Path(origen).is_relative_to(otro_disco) != Path(destino).is_relative_to(otro_disco):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return reales[funcion](origen, destino)
        return mover
    monkeypatch.setattr(os, "rename", sin_cruzar_discos("rename"))
    monkeypatch.setattr(os, "replace", sin_cruzar_discos("replace"))
    pdf, md = catalogo.ruta_pdf(doc), catalogo.ruta_md(doc)
    catalogo.cmd_retirar_pdf(argparse.Namespace(id=doc["id"], nota="era otra ley"))
    assert (otro_disco / "anteriores" / "lh.retirado.pdf").read_bytes() == b"%PDF-1.4 prueba"
    assert not pdf.exists() and not md.exists() and doc["id"] not in man



def test_anotar_una_pista_existente_no_la_mueve_ni_cambia_su_resultado(monkeypatch):
    doc = _doc_ley(pistas={"urls": [{"url": "https://a.mx/buena.pdf", "fecha": "2026-10-09", "resultado": "ok"},
                                    {"url": "https://a.mx/vieja.pdf", "fecha": "2026-09-01", "resultado": "falla"}]})
    cat = {"documentos": [doc]}
    for nombre, valor in {"bloqueo": contextlib.nullcontext, "cargar_catalogo": lambda: cat,
                          "guardar_catalogo": lambda c: None, "bitacora": lambda e: None}.items():
        monkeypatch.setattr(catalogo, nombre, valor)
    catalogo.cmd_pista(argparse.Namespace(id="lh", url="http://a.mx/vieja.pdf", nota="texto original de 2012",
                                          quitar=False))
    urls = doc["pistas"]["urls"]
    assert [u["url"] for u in urls] == ["https://a.mx/buena.pdf", "https://a.mx/vieja.pdf"]
    assert urls[1]["resultado"] == "falla" and urls[1]["nota"] == "texto original de 2012"
    catalogo.cmd_pista(argparse.Namespace(id="lh", url="https://a.mx/nueva.pdf", nota=None, quitar=False))
    assert urls is not doc["pistas"]["urls"] and doc["pistas"]["urls"][0]["url"] == "https://a.mx/nueva.pdf"


def test_json_tambien_despues_del_subcomando():
    p = comun.Analizador()
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("resumen")
    comun.json_tambien_al_final(sub)
    assert p.parse_args(["resumen", "--json"]).json is True
    assert p.parse_args(["--json", "resumen"]).json is True
    assert p.parse_args(["resumen"]).json is False
