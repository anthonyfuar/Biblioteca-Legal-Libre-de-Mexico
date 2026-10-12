"""Pruebas de herramientas/convertir.py."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from herramientas import convertir

# ====================================================================================================
# Pruebas de herramientas/convertir.py: posprocesado del Markdown crudo a Markdown estructurado.
# ====================================================================================================
META = {
    "id": "federal__ley_de_prueba",
    "nombre": "Ley de Prueba: Reglamentaria del \"Artículo\" 5",
    "entidad": None,
    "grupo": "Federal/Fiscal",
    "estado": "vigente",
    "archivo": "PDF/Federal/Fiscal/Ley_de_Prueba.pdf",
    "fuente": "https://www.diputados.gob.mx/LeyesBiblio/pdf/LP.pdf",
    "fecha_reforma": "2026-06-29",
    "sha256_origen": "0123456789" * 6 + "abcd",
    "metodo": "markitdown",
    "convertido": "2026-10-06",
}


def paginas(*cuerpos: str, cabecera="LEY DE PRUEBA\nSecretaría General", pie="{n} de {total}") -> str:
    """Documento con una página por cuerpo, cabecera repetida al inicio y pie al final, separadas con \\x0c."""
    total = len(cuerpos)
    partes = []
    for n, cuerpo in enumerate(cuerpos, 1):
        pie_n = pie.format(n=n, total=total) if pie else ""
        partes.append(f"{cabecera}\n\n{cuerpo}\n\n{pie_n}\n")
    return "\x0c".join(partes)


# ---------------------------------------------------------------------------------------------------
# front-matter
# ---------------------------------------------------------------------------------------------------
def test_front_matter_orden_escape_y_null():
    bloque = convertir.front_matter(META)
    lineas = bloque.strip().split("\n")
    assert lineas[0] == "---" and lineas[-1] == "---"
    claves = [linea.split(":")[0] for linea in lineas[1:-1]]
    assert claves == list(convertir.CLAVES_FRONT_MATTER)
    assert 'nombre: "Ley de Prueba: Reglamentaria del \\"Artículo\\" 5"' in lineas      # dos puntos y comillas
    assert "entidad: null" in lineas
    assert "version: 2" in lineas and convertir.VERSION == 2
    assert 'fecha_reforma: "2026-06-29"' in lineas                                   # las fechas van entre comillas
    assert 'sha256_origen: "0123456789' in bloque
    assert "aviso: Conversión automática del documento oficial" in bloque


def test_front_matter_ida_y_vuelta():
    texto = convertir.front_matter(META) + "\nCuerpo del documento.\n"
    meta, cuerpo = convertir.separar_front_matter(texto)
    assert cuerpo == "\nCuerpo del documento.\n"
    for clave, valor in META.items():
        assert meta[clave] == valor, clave
    assert meta["version"] == 2 and meta["aviso"] == convertir.AVISO


def test_separar_front_matter_sin_bloque_o_con_regla_horizontal():
    assert convertir.separar_front_matter("Texto sin front-matter\n") == (None, "Texto sin front-matter\n")
    cuerpo = "---\nNo es front-matter porque no son claves\n---\nmás\n"
    assert convertir.separar_front_matter(cuerpo) == (None, cuerpo)


def test_separar_front_matter_solo_reconoce_el_propio():
    # Un bloque inicial 'Clave: valor' que no trae id ni version es cuerpo (regla horizontal de MarkItDown).
    cuerpo = "---\nNota: texto importante\nFecha: 2020\n---\nLEY X\n\nArtículo 1.- Uno.\n"
    assert convertir.separar_front_matter(cuerpo) == (None, cuerpo)
    salida, _ = convertir.estructurar(cuerpo, META)
    assert "Nota: texto importante" in salida and "Fecha: 2020" in salida


# ---------------------------------------------------------------------------------------------------
# limpiar: números de página, cabeceras, espacios y guiones
# ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("linea", ["12 de 345", "Página 12", "Pág. 12", "PÁGINA 12 de 30", "- 12 -", "## Página 12",
                                   "Pagina 7", "P á g i n a 12", "Hoja 3"])
def test_numeros_de_pagina_inequivocos(linea):
    assert convertir.es_numero_pagina(linea)


@pytest.mark.parametrize("linea", ["12", "52/2008", "Artículo 12", "Página web", "2 de cada 3 casos", "12,000"])
def test_lineas_que_no_son_numero_de_pagina_inequivoco(linea):
    assert not convertir.es_numero_pagina(linea)


def test_limpiar_quita_numeros_de_pagina_en_todas_sus_formas():
    crudo = paginas("Texto uno.", "Texto dos.", "Texto tres.", "Texto cuatro.", "Texto cinco.", pie="{n}")
    crudo = crudo.replace("\n1\n", "\n- 1 -\n").replace("\n2\n", "\nPágina 2\n").replace("\n3\n", "\n3 de 5\n")
    limpio, informe = convertir.limpiar(crudo)
    assert informe["numeros_pagina"] == 5
    assert "Texto uno." in limpio and "Texto cinco." in limpio
    for resto in ("- 1 -", "Página 2", "3 de 5", "\n4\n", "\n5\n", "\x0c"):
        assert resto not in limpio


def test_numero_suelto_fuera_de_la_zona_de_pagina_es_contenido():
    crudo = paginas("Monto:\n\n2026\n\nmás texto.", "Dos.", "Tres.", "Cuatro.", "Cinco.", pie="{n}")
    limpio, informe = convertir.limpiar(crudo)
    assert "2026" in limpio                      # un año en medio de la página no es folio
    assert informe["numeros_pagina"] == 5


def test_numero_de_articulo_citado_al_pie_y_etiquetas_de_lista_no_son_folio():
    cuerpos = ["Uno.", "Lo dispuesto en la parte final del artículo\n160.", "Tres.",
               "1.\n\nTexto del primer párrafo numerado.\n\n2.\n\nTexto del segundo.", "Cinco.", "Seis."]
    limpio, informe = convertir.limpiar(paginas(*cuerpos, pie="{n}"))
    assert "del artículo\n160." in limpio or "del artículo\n\n160." in limpio
    assert "\n1.\n" in limpio and "\n2.\n" in limpio
    assert informe["numeros_pagina"] == 6                 # solo los seis folios sin punto


def test_folio_real_tras_una_cita_de_articulo_si_se_quita():
    cuerpos = [f"Texto de la página {p}." for p in range(7)]
    cuerpos[3] = "Se adiciona la fracción V al artículo"
    crudo = "\x0c".join(f"{c}\n\n{119 + k}\n" for k, c in enumerate(cuerpos))
    limpio, informe = convertir.limpiar(crudo)
    assert informe["numeros_pagina"] == 7 and "122" not in limpio   # 122 sigue la secuencia 119-125: es folio


def test_limpiar_quita_cabeceras_repetidas_pero_no_lineas_legitimas():
    cuerpos = ["(REFORMADO, P.O. 23 DE MARZO DE 1997)\nArtículo 1.- Uno.\nPárrafo reformado DOF 10-06-2011"] * 5
    cuerpos = [c.replace("Artículo 1", f"Artículo {n}") for n, c in enumerate(cuerpos, 1)]
    cuerpos[1] = "CAPÍTULO I\nDISPOSICIONES GENERALES\n" + cuerpos[1]
    cuerpos[3] = "CAPÍTULO II\nDISPOSICIONES GENERALES\n" + cuerpos[3]
    crudo = paginas(*cuerpos, cabecera="LEY DE PRUEBA\nSecretaría General\nÚltima Reforma DOF 02-06-2026")
    limpio, informe = convertir.limpiar(crudo)
    # La cabecera de varias líneas se quita de las páginas 2-5; la primera copia se conserva entera.
    assert informe["cabeceras_repetidas"] == {"LEY DE PRUEBA": 4, "Secretaría General": 4, "Última Reforma DOF 02-06-2026": 4}
    assert limpio.count("Secretaría General") == 1
    assert limpio.count("Párrafo reformado DOF 10-06-2011") == 5      # anotación legítima, aunque se repita
    assert limpio.count("(REFORMADO, P.O. 23 DE MARZO DE 1997)") == 5
    assert limpio.count("DISPOSICIONES GENERALES") == 2
    assert limpio.count("CAPÍTULO") == 2


def test_metadatos_de_cabecera_quedan_una_vez():
    # Morelos: cada página trae Aprobación/Publicación/Vigencia; son datos jurídicos que no están en otro lado.
    cabecera = "Aprobación 2006/07/13\nPublicación 2006/09/06\nVigencia 2006/10/01\nExpidió XLIX Legislatura"
    cuerpos = [f"Artículo {n}.- Texto del artículo {n} con contenido suficiente." for n in range(1, 7)]
    limpio, informe = convertir.limpiar(paginas(*cuerpos, cabecera=cabecera, pie=""))
    for dato in ("Aprobación 2006/07/13", "Publicación 2006/09/06", "Vigencia 2006/10/01", "Expidió XLIX Legislatura"):
        assert limpio.count(dato) == 1, dato
    assert limpio.index("Vigencia 2006/10/01") < limpio.index("Artículo 1.-")     # queda arriba, en su primera página
    assert informe["lineas_cabeceras"] == 4 * 5
    salida, _ = convertir.estructurar(paginas(*cuerpos, cabecera=cabecera, pie=""), {"id": "x"})
    assert convertir.estructurar(salida, {"id": "x"})[0] == salida                        # idempotente con la copia conservada


def test_cabecera_que_no_esta_en_la_primera_pagina_se_quita_entera():
    # Si la primera página no trae la cabecera, no se conserva ninguna copia: quedaría en medio de una frase.
    portada = "PORTADA DE LA LEY\n\nDecreto que expide la ley y reforma otras disposiciones de la"
    cuerpos = [f"materia {p} en todo el estado.\nSegunda frase distinta de la página {p}.\nY una tercera, también de la"
               for p in ("dos", "tres", "cuatro", "cinco", "seis", "siete")]
    crudo = "\x0c".join([portada + "\n"] + [f"Secretaría de Servicios Parlamentarios\n\n{c}\n" for c in cuerpos])
    limpio, informe = convertir.limpiar(crudo)
    assert "Secretaría de Servicios Parlamentarios" not in limpio
    assert informe["cabeceras_repetidas"] == {"Secretaría de Servicios Parlamentarios": 6}
    assert limpio.count("Segunda frase distinta") == 6                    # el cuerpo no se toca
    # Una línea idéntica al pie de cada página pero cortada a media frase ('de la') no es cabecera.
    assert limpio.count("Y una tercera, también de la") == 6


def test_cabecera_repetida_menos_de_cuatro_veces_se_conserva():
    crudo = paginas("Uno.", "Dos.", "Tres.")
    limpio, informe = convertir.limpiar(crudo)
    assert informe["cabeceras_repetidas"] == {}
    assert limpio.count("Secretaría General") == 3


def test_titulo_en_la_primera_pagina_se_conserva_aunque_coincida_con_la_cabecera():
    cuerpos = ["LEY DE PRUEBA\n\nTexto publicado el 5 de febrero."] + ["Texto."] * 4
    limpio, informe = convertir.limpiar(paginas(*cuerpos))
    assert informe["cabeceras_repetidas"]["LEY DE PRUEBA"] == 4
    assert limpio.count("LEY DE PRUEBA") == 2          # la copia de la cabecera (primera página) y el título real
    assert limpio.split("Texto publicado")[1].count("LEY DE PRUEBA") == 0


def test_cabecera_en_documento_sin_form_feed_con_salto_en_blanco():
    paginas_ = ["LEY DE HACIENDA.\nÚltima actualización: 26/04/2025.\n"
                + "\n".join(f"Línea {i} de la página {p} con texto corrido" for i in "abcdefghijkl")
                for p in ("uno", "dos", "tres", "cuatro", "cinco", "seis")]
    crudo = "\n\n".join(paginas_) + "\n"
    limpio, informe = convertir.limpiar(crudo)
    assert informe["cabeceras_repetidas"] == {"LEY DE HACIENDA.": 5, "Última actualización: 26/04/2025.": 5}
    assert limpio.count("LEY DE HACIENDA.") == 1


def test_notas_de_reforma_numeradas_al_pie_no_son_cabecera():
    cuerpos = [f"Artículo {n}.- Texto.\n\n{n} Fracción reformada el 22/ago/2015." for n in range(1, 7)]
    limpio, informe = convertir.limpiar(paginas(*cuerpos, cabecera="CÓDIGO ELECTORAL\nOrden Jurídico Poblano"))
    assert informe["cabeceras_repetidas"] == {"CÓDIGO ELECTORAL": 5, "Orden Jurídico Poblano": 5}
    assert limpio.count("Fracción reformada el 22/ago/2015.") == 6


def test_variante_rara_de_la_cabecera_no_se_quita():
    cuerpos = [f"Texto distinto de la página {p}." for p in "abcdef"]
    cuerpos[2] = "Las autoridades del Estado de\nGuerrero;"              # la fracción termina en la cabecera 'GUERRERO.'
    limpio, informe = convertir.limpiar(paginas(*cuerpos, cabecera="GUERRERO.\nCONGRESO DEL ESTADO"))
    assert "Guerrero;" in limpio
    assert informe["cabeceras_repetidas"]["GUERRERO."] == 5     # 6 copias; la primera se conserva (acaba en punto)


def test_pie_de_varias_lineas_conserva_la_primera_copia_entera_y_las_variantes():
    pie = ("NOTA DE EDITOR: LA PRESENTE LEY SE ABROGA\nPOR DECRETO 123 PUBLICADO EL 1 DE ENERO\n"
           "SALVO LO DISPUESTO EN SU ARTÍCULO QUINTO.")
    cuerpos = [f"Texto de la página {p}.\n\n{pie}" for p in "abcdefg"]
    cuerpos[3] = ("Texto de la página d.\n\nNOTA DE EDITOR: LA PRESENTE LEY SE ABROGA POR\n"
                  "DECRETO 123 PUBLICADO EL 1 DE ENERO\nSALVO LO DISPUESTO EN SU ARTÍCULO QUINTO.")
    limpio, informe = convertir.limpiar(paginas(*cuerpos, cabecera="LEY ORGÁNICA MUNICIPAL", pie=""))
    assert "Texto de la página a.\n\n" + pie + "\n" in limpio                    # primera copia entera
    assert limpio.count("NOTA DE EDITOR: LA PRESENTE LEY SE ABROGA\n") == 1
    assert "LA PRESENTE LEY SE ABROGA POR\nDECRETO 123 PUBLICADO EL 1 DE ENERO\nSALVO" in limpio   # la variante, entera
    assert limpio.count("SALVO LO DISPUESTO EN SU ARTÍCULO QUINTO.") == 2
    # Cabecera de una línea: se quitan 6 de 7 (la de la primera página se queda); pie de 3 líneas: 5 copias quitadas
    # (ni la primera ni la variante).
    assert informe["lineas_cabeceras"] == 6 + 15
    assert limpio.count("LEY ORGÁNICA MUNICIPAL") == 1


def test_dobles_espacios_y_control():
    limpio, _ = convertir.limpiar("Texto  con   dobles  espacios.   \n\tOtra\xa0línea\x01 rara\n")
    assert limpio == "Texto con dobles espacios.\nOtra línea rara\n"
    assert convertir.limpiar("uno\x01dos")[0] == "uno dos\n"           # un control nunca pega palabras


def test_salto_de_pagina_a_media_linea_no_pega_palabras():
    limpio, _ = convertir.limpiar("texto de\x0cPÁGINA 3 PERIÓDICO OFICIAL\nsigue\n")
    assert "dePÁGINA" not in limpio
    assert limpio.startswith("texto de\nPÁGINA 3 PERIÓDICO OFICIAL\n")


def test_guiones_de_corte():
    crudo = "El desarrollo nacional y el desa-\nrrollo regional; los órganos político-\nadministrativos.\n"
    limpio, informe = convertir.limpiar(crudo)
    assert "el desarrollo regional" in limpio                 # 'desarrollo' ya aparece en el documento
    assert "político-administrativos" in limpio             # compuesto real: se conserva el guion
    assert informe["guiones_unidos"] == 1 and informe["guiones_conservados"] == 1


# ---------------------------------------------------------------------------------------------------
# unir_lineas
# ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("crudo, esperado", [
    ("Las personas gozarán de los derechos\nreconocidos en esta Constitución.\n",
     "Las personas gozarán de los derechos reconocidos en esta Constitución.\n"),
    ("conforme al artículo\n5 de la ley.\n", "conforme al artículo 5 de la ley.\n"),
    ("el plazo\n(cinco días) vence.\n", "el plazo (cinco días) vence.\n"),
    ("El Presidente de la\nRepública expedirá.\n", "El Presidente de la República expedirá.\n"),      # palabra de enlace
    ("Secretaría de Hacienda,\nCrédito Público.\n", "Secretaría de Hacienda, Crédito Público.\n"),
    ("La Nación Mexicana es única y\n\nculturas.\n", "La Nación Mexicana es única y culturas.\n"),     # cortada entre páginas
    ("I. Decidir, conforme a sus sistemas, sus\nformas internas de gobierno.\n",
     "I. Decidir, conforme a sus sistemas, sus formas internas de gobierno.\n"),
])
def test_unir_lineas_une(crudo, esperado):
    assert convertir.unir_lineas(crudo) == esperado


@pytest.mark.parametrize("crudo", [
    "Primera frase termina.\nSegunda frase.\n",
    "Son requisitos:\na) Ser mexicano.\n",
    "- uno\n- dos\n",
    "1. Primero\n2. Segundo\n",
    "| a | b |\n| c | d |\n",
    "## Encabezado\nTexto siguiente\n",
    "El tribunal resolverá\nEl juez ordenará\n",                        # la siguiente empieza en mayúscula
    "El plazo vence en\n(REFORMADO, P.O. 23 DE MARZO DE 1997)\n",       # anotación
    "Texto sin punto final\nArtículo 2.- Nuevo artículo.\n",
    "CAPÍTULO I\nDisposiciones Generales\n",
    "Artículo 5\nLas disposiciones de esta ley.\n",
    "II.- El Archivo Estatal; y\nIII.- Las Oficialías.\n",
])
def test_unir_lineas_no_une(crudo):
    unido = convertir.unir_lineas(crudo)
    assert all(linea.strip() in unido for linea in crudo.split("\n"))
    assert len([x for x in unido.split("\n") if x.strip()]) == len([x for x in crudo.split("\n") if x.strip()])


# Fracciones con romanos de 7 u 8 letras (XXXVIII, LXXVIII, LXXXVII, LXXXVIII) son inicio de lista y no se pegan a la
# fracción anterior aunque esta termine en palabra de enlace ('; y') o coma, con o sin línea en blanco de por medio.
@pytest.mark.parametrize("crudo", [
    "XXXVII. Sistema Educativo Estatal; y\nXXXVIII. Violencia escolar: la acción u omisión;\nXXXIX. Otra.\n",
    "XXXVII. Sistema Educativo Estatal; y\n\nXXXVIII. Violencia escolar: la acción u omisión;\n",
    "LXXVII. Texto de la fracción, y\nLXXVIII. Otra fracción;\n",
    "LXXXVI. Texto de la fracción; y\nLXXXVII. Otra fracción; y\nLXXXVIII. La última.\n",
])
def test_unir_lineas_no_une_fracciones_romanas_largas(crudo):
    unido = convertir.unir_lineas(crudo)
    for linea in crudo.split("\n"):
        if linea:
            assert linea in unido.split("\n"), linea
    assert convertir.unir_lineas(unido) == unido


@pytest.mark.parametrize("linea", ["XXXVIII. x", "LXXVIII. x", "LXXXVII. x", "LXXXVIII. x", "xxxviii. x"])
def test_es_lista_romanos_largos(linea):
    assert convertir._es_lista(linea)


def test_unir_lineas_separa_parrafos_y_conserva_tablas_y_codigo():
    crudo = "Párrafo uno que\nsigue.\nPárrafo dos.\n| a | b |\n|---|---|\n| 1 | 2 |\n```\ncódigo  x\ncódigo y\n```\n"
    assert convertir.unir_lineas(crudo) == (
        "Párrafo uno que sigue.\n\nPárrafo dos.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncódigo  x\ncódigo y\n```\n")


def test_unir_lineas_es_lineal_en_parrafos_largos():
    crudo = "\n".join(f"palabra{i} de la frase," for i in range(5000)) + "\nfin.\n"
    inicio = time.perf_counter()
    unido = convertir.unir_lineas(crudo)
    assert time.perf_counter() - inicio < 2
    assert unido.count("\n") == 1 and unido.endswith("fin.\n")


# ---------------------------------------------------------------------------------------------------
# encabezados
# ---------------------------------------------------------------------------------------------------
LEY = """LEY DE PRUEBA

TÍTULO PRIMERO
DISPOSICIONES GENERALES

CAPÍTULO I
Del Objeto

Artículo 1.- Las disposiciones de esta ley son de orden público.

Artículo 2. Para los efectos de esta ley se entiende por:

ARTÍCULO 3o.- Se deroga.

Artículo 4 Bis. Texto del cuatro bis.

Artículo 5-A.- Texto del cinco A.

Art. 6.- Texto del seis.

Artículo 7

Texto del siete en la línea siguiente, conforme al artículo 5 de esta ley.

CAPÍTULO II DE LAS SANCIONES

SECCIÓN PRIMERA
De las multas

Artículo 8.- Las sanciones.

TRANSITORIOS

Artículo Primero.- La presente ley entrará en vigor al día siguiente.

ARTÍCULO SEGUNDO. Se abroga la ley anterior.
"""


def test_encabezados_unidades_articulos_y_transitorios():
    salida = convertir.encabezados(LEY)
    assert "# TÍTULO PRIMERO. DISPOSICIONES GENERALES\n" in salida
    assert "## CAPÍTULO I. Del Objeto\n" in salida
    assert "## CAPÍTULO II DE LAS SANCIONES\n" in salida
    assert "### SECCIÓN PRIMERA. De las multas\n" in salida
    assert "#### Artículo 1\n\nLas disposiciones de esta ley son de orden público.\n" in salida
    assert "#### Artículo 2\n\nPara los efectos de esta ley se entiende por:\n" in salida
    assert "#### Artículo 3o.\n\nSe deroga.\n" in salida
    assert "#### Artículo 4 Bis\n\nTexto del cuatro bis.\n" in salida
    assert "#### Artículo 5-A\n\nTexto del cinco A.\n" in salida
    assert "#### Artículo 6\n\nTexto del seis.\n" in salida
    assert "#### Artículo 7\n\nTexto del siete en la línea siguiente, conforme al artículo 5 de esta ley.\n" in salida
    assert "## TRANSITORIOS\n" in salida
    assert "#### Artículo Primero\n\nLa presente ley entrará en vigor" in salida
    assert "#### Artículo Segundo\n\nSe abroga" in salida
    assert salida.count("#### ") == 10
    assert "LEY DE PRUEBA\n" in salida and "# LEY" not in salida


def test_articulo_con_numero_decimal():
    texto = "Artículo 1.1.- Uno uno.\n\nArtículo 1.2.- Uno dos.\n\nArtículo 2.1: Dos uno.\n\nArtículo 3.- Texto normal.\n"
    salida = convertir.encabezados(texto)
    assert "#### Artículo 1.1\n\nUno uno.\n" in salida and "#### Artículo 1.2\n\nUno dos.\n" in salida
    assert "#### Artículo 2.1\n\nDos uno.\n" in salida and "#### Artículo 3\n\nTexto normal.\n" in salida
    assert convertir._articulo("Artículo 1.- texto")["num"] == "1"


def test_encabezados_no_convierte_menciones_ni_pocos_articulos():
    texto = "Artículo 5 de la Ley de Amparo se aplica.\nArtículo 6, fracción II, dispone.\nArtículo 7 y 8 de la ley.\n"
    assert convertir.encabezados(texto) == texto
    pocos = "Artículo 1.- Uno.\n\nArtículo 2.- Dos.\n\nCAPÍTULO I\n\nLas disposiciones de este capítulo.\n"
    salida = convertir.encabezados(pocos)
    assert "#### " not in salida and "# CAPÍTULO I\n" in salida


def test_encabezados_no_convierte_apendice_de_historial():
    texto = ("Artículo 1.- Uno.\n\nArtículo 2.- Dos.\n\nArtículo 3.- Tres.\n\n"
             "ARTÍCULO 492.- fue reformado por Decreto No. 362, publicado en el Periódico Oficial No. 51.\n"
             "ARTÍCULO 493.- fue derogado por Decreto No. 12.\n")
    salida = convertir.encabezados(texto)
    assert salida.count("#### ") == 3
    assert "ARTÍCULO 492.- fue reformado por Decreto No. 362, publicado en el Periódico Oficial No. 51.\n" in salida
    assert "ARTÍCULO 493.- fue derogado por Decreto No. 12.\n" in salida


def test_encabezados_articulos_no_crecientes_son_sospechosos():
    texto = "\n".join(f"Artículo {n}.- Texto." for n in (9, 3, 7, 1, 8, 2, 6, 4)) + "\n"
    _, informe = convertir._encabezados(texto)
    assert informe["articulos_sospechosos"] and informe["articulos"] == 0


def test_encabezados_ordinales_solo_tras_transitorios():
    texto = ("ARTÍCULO ÚNICO.- Se expide la ley.\n\nArtículo 1.- Uno.\n\nArtículo 2.- Dos.\n\nArtículo 3.- Tres.\n\n"
             "TRANSITORIOS\n\nARTÍCULO ÚNICO.- Entrará en vigor.\n")
    salida = convertir.encabezados(texto)
    assert salida.startswith("ARTÍCULO ÚNICO.- Se expide la ley.\n")
    assert salida.count("#### Artículo Único\n") == 1
    # Sin artículos numerados, los ordinales (3 o más) sí son la estructura del documento (Leyes de Ingresos).
    ingresos = "ARTÍCULO PRIMERO.- Uno.\n\nARTÍCULO SEGUNDO.- Dos.\n\nARTÍCULO TERCERO.- Tres.\n"
    assert convertir.encabezados(ingresos).count("#### Artículo ") == 3


def test_encabezados_indice_no_se_convierte():
    indice = "\n".join(f"Artículo {n} ........ {10 + n}" for n in range(1, 10))
    cuerpo = "\n\n".join(f"Artículo {n}.- Texto del {n}." for n in range(1, 10))
    salida = convertir.encabezados("ÍNDICE\n" + indice + "\n\nTÍTULO ÚNICO\n\n" + cuerpo + "\n")
    assert salida.count("#### Artículo ") == 9
    assert "Artículo 1 ........ 11" in salida
    tirada = "\n".join(f"Artículo {n}" for n in range(1, 12))        # índice sin puntos: solo rótulos, sin texto
    salida = convertir.encabezados("CONTENIDO\n" + tirada + "\n\n" + cuerpo + "\n")
    assert salida.count("#### Artículo ") == 9 and "\nArtículo 11\n" in salida


def test_encabezados_indice_con_titulos():
    titulos = ["Objeto", "Supletoriedad", "Definiciones", "Autoridades", "Competencia", "Sanciones", "Recursos",
               "Plazos", "Notificaciones", "Vigencia"]
    indice = "\n".join(f"ARTÍCULO {n}. {t}" for n, t in enumerate(titulos, 1))
    cuerpo = "\n\n".join(f"ARTÍCULO {n}. {t}\nTexto del artículo {n} con su contenido completo."
                          for n, t in enumerate(titulos, 1))
    salida = convertir.encabezados("CONTENIDO\n\n" + indice + "\n\nLIBRO PRIMERO\n\n" + cuerpo + "\n")
    assert salida.count("#### Artículo ") == 10               # solo el cuerpo: el índice reinicia la numeración
    assert "ARTÍCULO 1. Objeto\nARTÍCULO 2. Supletoriedad" in salida
    # Un bloque de artículos derogados del cuerpo no es índice.
    derogados = "\n\n".join(f"Artículo {n}.- Se deroga." for n in range(1, 11)) + "\n\nArtículo 11.- Vigente.\n"
    assert convertir.encabezados(derogados).count("#### Artículo ") == 11


def test_apartado_despues_de_un_articulo_es_subdivision():
    dentro = ("CAPÍTULO I\n\nArtículo 5.- El Estado garantizará:\n\nAPARTADO A\n\nDe los derechos.\n\nAPARTADO B\n\n"
              "De las obligaciones.\n\nArtículo 6.- Seis.\n\nArtículo 7.- Siete.\n")
    salida = convertir.encabezados(dentro)
    assert "## APARTADO" not in salida and "### APARTADO" not in salida
    assert "\nAPARTADO A\n\nDe los derechos.\n" in salida and "\nAPARTADO B\n" in salida
    assert salida.count("#### Artículo ") == 3
    fuera = ("CAPÍTULO I\n\nAPARTADO A\n\nDEL CONTROL VEHICULAR\n\nArtículo 1.- Uno.\n\nArtículo 2.- Dos.\n\n"
             "APARTADO B\n\nDE OTROS SERVICIOS\n\nArtículo 3.- Tres.\n")
    salida = convertir.encabezados(fuera)
    assert "## APARTADO A. DEL CONTROL VEHICULAR\n" in salida and "## APARTADO B. DE OTROS SERVICIOS\n" in salida


def test_niveles_de_unidades():
    cuatro = ("LIBRO PRIMERO\n\nTÍTULO I\n\nCAPÍTULO I\n\nSECCIÓN PRIMERA\n\n"
              "Artículo 1.- A.\n\nArtículo 2.- B.\n\nArtículo 3.- C.\n")
    salida, informe = convertir._encabezados(cuatro)
    assert "# LIBRO PRIMERO\n" in salida and "## TÍTULO I\n" in salida
    assert "### CAPÍTULO I\n" in salida and "### SECCIÓN PRIMERA\n" in salida      # cuatro clases: las dos menores comparten
    assert informe["niveles"] == {"LIBRO": 1, "TITULO": 2, "CAPITULO": 3, "SECCION": 3}
    solo_capitulos = "CAPÍTULO I\n\nTexto.\n\nCAPÍTULO II\n\nTexto.\n"
    salida_capitulos = convertir.encabezados(solo_capitulos)
    assert salida_capitulos.count("\n# CAPÍTULO") == 1 and salida_capitulos.startswith("# CAPÍTULO I")


def test_transitorios_formas():
    for forma in ("TRANSITORIOS", "ARTÍCULOS TRANSITORIOS", "T R A N S I T O R I O S", "Transitorios:", "TRANSITORIO",
                  "TRANSITORIOS DEL DECRETO DE REFORMAS"):
        texto = f"Texto.\n\n{forma}\n\nPrimero.- Entra en vigor.\n"
        assert convertir.encabezados(texto) == f"Texto.\n\n## {forma}\n\nPrimero.- Entra en vigor.\n"
    for forma in ("transitorio.", "TRANSITORIO SEGUNDO DEL DECRETO QUE MODIFICA EL CÓDIGO.", "Transitorio)"):
        assert "## " not in convertir.encabezados(f"Texto.\n\n{forma}\n")


# ---------------------------------------------------------------------------------------------------
# estructurar y validar
# ---------------------------------------------------------------------------------------------------
def documento_crudo() -> str:
    cuerpos = [
        "LEY DE PRUEBA\n\nTÍTULO PRIMERO\nDISPOSICIONES GENERALES\n\n"
        "Artículo 1.- Las disposiciones de esta ley son de\norden público.",
        "Artículo 2.- El desa-\nrrollo nacional; el desarrollo regional y los órganos político-\nadministrativos.\n\n"
        "I. Primera fracción que\nsigue;",
        "II. Segunda fracción.\n\nArtículo 3.- Tercero, según el oficio 5/DEC-12 y la regla 171-1.\n\n"
        "| Concepto | Cuota |\n|---|---|\n| Placas | $45.00 |",
        "CAPÍTULO II\nDe las sanciones\n\nArtículo 4.- Cuarto, conforme al artículo 2 de esta ley.",
        "TRANSITORIOS\n\nArtículo Primero.- Entrará en vigor.\n\nArtículo Segundo.- Se abroga la ley de 1990.",
    ]
    return paginas(*cuerpos, cabecera="LEY DE PRUEBA\nSecretaría General\nÚltima Reforma DOF 02-06-2026")


def test_estructurar_compone_todo_e_informa():
    crudo = documento_crudo()
    salida, informe = convertir.estructurar(crudo, META)
    meta, cuerpo = convertir.separar_front_matter(salida)
    assert meta["id"] == META["id"] and meta["version"] == 2
    # La primera copia de la cabecera (3 líneas) se conserva; las demás páginas la pierden.
    assert cuerpo.startswith("\nLEY DE PRUEBA\n\nSecretaría General\n\nÚltima Reforma DOF 02-06-2026\n\nLEY DE PRUEBA\n\n"
                             "# TÍTULO PRIMERO. DISPOSICIONES GENERALES\n\n#### Artículo 1\n\n"
                             "Las disposiciones de esta ley son de orden público.\n\n#### Artículo 2\n\n"
                             "El desarrollo nacional; el desarrollo regional y los órganos político-administrativos.\n\n"
                             "I. Primera fracción que sigue;\n\nII. Segunda fracción.\n\n#### Artículo 3\n\n"
                             "Tercero, según el oficio 5/DEC-12 y la regla 171-1.\n\n"
                             "| Concepto | Cuota |\n|---|---|\n| Placas | $45.00 |\n\n## CAPÍTULO II. De las sanciones\n")
    assert "## TRANSITORIOS\n\n#### Artículo Primero\n\nEntrará en vigor.\n\n#### Artículo Segundo\n\nSe abroga" in cuerpo
    assert cuerpo.count("Secretaría General") == 1 and "\x0c" not in salida and " de 5" not in cuerpo
    assert informe["encabezados"] == {1: 1, 2: 2, 3: 0, 4: 6}
    assert informe["articulos"] == 6 and informe["transitorios"] == 1
    assert informe["lineas_quitadas"] == {"numeros_pagina": 5, "cabeceras_repetidas": 12}
    assert informe["guiones_unidos"] == 1 and informe["guiones_conservados"] == 1
    assert informe["palabras_antes"] > informe["palabras_despues"] > 0
    assert informe["validacion"]["ok"] and informe["validacion"]["lineas_sospechosas"] == []
    assert informe["validacion"] == convertir.validar(crudo, salida)
    assert salida.endswith("\n") and "\n\n\n" not in salida


def test_estructurar_es_idempotente():
    crudo = documento_crudo()
    primera, _ = convertir.estructurar(crudo, META)
    segunda, informe = convertir.estructurar(primera, META)
    assert segunda == primera
    assert informe["lineas_quitadas"] == {"numeros_pagina": 0, "cabeceras_repetidas": 0} and informe["lineas_unidas"] == 0


def test_validar_ok_con_perdida_controlada_y_falla_con_perdida_real():
    crudo = documento_crudo()
    salida, _ = convertir.estructurar(crudo, META)
    resultado = convertir.validar(crudo, salida)
    assert resultado["ok"], resultado
    assert resultado["palabras_perdidas"] == [] and resultado["palabras_nuevas"] == []
    roto = salida.replace("Se abroga la ley de 1990.", "Se abroga.")
    resultado = convertir.validar(crudo, roto)
    assert not resultado["ok"]
    assert set(resultado["palabras_perdidas"]) == {"la", "ley", "de", "1990"}
    inventado = salida.replace("Tercero,", "Tercero inventado,")
    assert convertir.validar(crudo, inventado)["palabras_nuevas"] == ["inventado"]


def test_validar_detecta_numeros_con_signos_acentos_y_orden():
    crudo = documento_crudo()
    salida, _ = convertir.estructurar(crudo, META)
    sin_signos = salida.replace("5/DEC-12", "5 DEC 12")
    resultado = convertir.validar(crudo, sin_signos)
    assert not resultado["ok"] and resultado["numeros_perdidos"] == ["5/dec-12"]
    sin_acento = salida.replace("Entrará en vigor.", "Entrara en vigor.")
    assert convertir.validar(crudo, sin_acento)["palabras_perdidas"] == ["entrará"]
    desordenado = salida.replace("Se abroga la ley de 1990.", "La ley de 1990 se abroga.")
    resultado = convertir.validar(crudo, desordenado)
    assert not resultado["ok"] and resultado["bigramas_distintos"] > 0 and resultado["total_perdidas"] == 0


def test_revision_independiente_de_lo_quitado():
    lineas = ["Las autoridades del Estado de", "Guerrero;", "", "12", "", "13", "2 Fracción reformada el 22/ago/2015.",
              "LEY X", "LEY X", "LEY X", "LEY X"]
    an = convertir._Analisis(lineas=lineas, quitar={1: "cabeceras_repetidas", 3: "numeros_pagina", 5: "numeros_pagina",
                                            6: "cabeceras_repetidas", 7: "cabeceras_repetidas", 8: "cabeceras_repetidas"})
    motivos = {s["texto"]: s["motivo"] for s in convertir._revisar_quitadas(an)}
    assert motivos["Guerrero;"] == "termina en ';'"
    assert motivos["12"] == motivos["13"] == "número suelto junto a otro número quitado"
    assert motivos["2 Fracción reformada el 22/ago/2015."] == "nota de reforma"
    assert "LEY X" not in motivos
    assert not convertir._comparar("\n".join(lineas), [lineas[0], lineas[2], lineas[4]], an, lineas[0])["ok"]


RAIZ = Path(__file__).resolve().parent.parent
CONSTITUCION = RAIZ / "documentos/Markdown/Constitucion/Constitucion_Politica_de_los_Estados_Unidos_Mexicanos.md"


@pytest.mark.skipif(not CONSTITUCION.exists(), reason="la biblioteca no está descargada")
def test_constitucion_real():
    # Sirve con la biblioteca cruda (formato 1) o ya estructurada (formato 2): el resultado debe ser el mismo y estable.
    texto = CONSTITUCION.read_text(encoding="utf-8")
    meta = dict(META, nombre="Constitución Política de los Estados Unidos Mexicanos")
    salida, informe = convertir.estructurar(texto, meta)
    if convertir.separar_front_matter(texto)[0] is None:          # crudo: aquí es donde se quitan las cabeceras
        assert informe["articulos"] >= 136 and informe["cabeceras_repetidas"]["Secretaría General"] >= 400
    assert salida.count("\n#### Artículo ") >= 136 and salida.count("Secretaría General") <= 1
    assert "#### Artículo 1o.\n\nEn los Estados Unidos Mexicanos todas las personas gozarán" in salida
    assert "## Capítulo I. De los Derechos Humanos y sus Garantías\n" in salida
    assert informe["validacion"]["ok"], informe["validacion"]
    assert convertir.estructurar(salida, meta)[0] == salida


@pytest.mark.parametrize("linea, etiqueta", [
    ("Artículo 4o.-A.- (Se deroga).", "Artículo 4o.-A"),
    ("ARTÍCULO 1691- A.- Texto", "Artículo 1691-A"),
    ("Artículo 158-Ñ.- Texto", "Artículo 158-Ñ"),
    ("Artículo 15-III.- Texto", "Artículo 15-III"),
    ("Artículo 5 BIS-1.- Texto", "Artículo 5 Bis-1"),
    ("Artículo 208-QUARTER.- Texto", "Artículo 208-Quarter"),
    ("Artículo 449-QUINTUS", "Artículo 449-Quintus"),
    ("Artículo 195-Z-11.- Texto", "Artículo 195-Z-11"),
    ("Artículo 176-Bis.1", "Artículo 176-Bis.1"),
    ("Artículo 9-1.- Texto", "Artículo 9-1"),
    ("Artículo 2.A.1: Texto", "Artículo 2.A.1"),
])
def test_formas_de_numero_de_articulo(linea, etiqueta):
    assert convertir._articulo(linea)["etiqueta"] == etiqueta


def test_rotulo_con_separador_pegado_al_texto():
    a = convertir._articulo("Artículo 263-La prestación del servicio.")
    assert a["etiqueta"] == "Artículo 263" and a["resto"].startswith("La prestación")
    a = convertir._articulo("Artículo 5o.- A ninguna ley se dará efecto retroactivo.")
    assert a["etiqueta"] == "Artículo 5o." and a["resto"].startswith("A ninguna")


def test_folios_consecutivos_con_corte_de_pagina_no_son_sospechosos():
    lineas = ["Texto final.", "", "344", "", "345", ""]
    an = convertir._Analisis(lineas=lineas, quitar={2: "numeros_pagina", 4: "numeros_pagina"}, inicio_pagina={4})
    assert convertir._revisar_quitadas(an) == []                 # página vacía al final: folio 344 y folio 345
    an = convertir._Analisis(lineas=lineas, quitar={2: "numeros_pagina", 4: "numeros_pagina"})
    assert len(convertir._revisar_quitadas(an)) == 2             # sin corte de página en medio: lista arrastrada


def test_apartado_tras_articulo_ordinal_es_idempotente():
    texto = ("TRANSITORIOS\n\nArtículo Único.- Se reforma el artículo 5.\n\nAPARTADO PRIMERO.- SE REFORMA EL SEGUNDO PÁRRAFO.\n\n"
             "APARTADO SEGUNDO.- SE DEROGA.\n\nArtículo 1.- Uno.\n\nArtículo 2.- Dos.\n\nArtículo 3.- Tres.\n")
    primera = convertir.encabezados(texto)
    assert "# APARTADO" not in primera and "\nAPARTADO PRIMERO.- SE REFORMA" in primera
    assert convertir.encabezados(primera) == primera


def test_validacion_no_se_confunde_con_el_rotulo_al_inicio_de_pagina_ni_con_menciones_cortadas():
    cuerpos = ["Artículo 62-B.- Texto previo que termina en\nArtículo 62-C de este Código y sigue.",
               "Artículo 62-C. La Defensoría para la Protección de los Derechos del Contribuyente.",
               "Artículo 63.- Tres.", "Artículo 64.- Cuatro, según la regla 171-1.", "Artículo 65.- Cinco."]
    crudo = paginas(*cuerpos)                      # la página 2 empieza con '\x0cArtículo 62-C. ...'
    salida, informe = convertir.estructurar(crudo, META)
    assert informe["validacion"]["ok"], informe["validacion"]
    assert "#### Artículo 62-C\n" in salida and "termina en Artículo 62-C de este Código" in salida


def test_pagina_n_que_no_es_folio_se_conserva():
    # 'Página: 531' en la ficha de una tesis y 'Página 31' de una fe de erratas son contenido: no son el folio del
    # documento (no se repiten con esa forma) ni están junto a un salto de página.
    cuerpos = [f"Artículo {n}.- Texto del artículo {n} con contenido suficiente.\nOtra línea del cuerpo." for n in range(1, 6)]
    cuerpos[2] += "\nTesis P./J. 81/2003\nPágina: 531\nRegistro: 183 263"
    cuerpos[3] += "\nFE DE ERRATAS\nPágina 31\nDICE:\nArtículo 5.- Uno.\nDEBE DECIR:\nArtículo 5.- Dos."
    limpio, _ = convertir.limpiar(paginas(*cuerpos))
    assert "Página: 531" in limpio and "Página 31" in limpio
    assert "1 de 5" not in limpio                                       # el folio del documento sí se quita


def test_numeros_con_signos_cotejan_montos_y_respetan_menciones_de_articulo():
    fichas = convertir._numeros_con_signos("Monto de 2,595,560.00 pesos, tasa de 2.5 y la regla 171-1.\n"
                                    "conforme al artículo 17-A y al art. 7.35 del código")
    assert "2,595,560.00" in fichas and "2.5" in fichas and "171-1" in fichas
    assert "17-a" not in fichas and "7.35" not in fichas              # mención de artículo: forma tolerada
    crudo = "Artículo 1.- Se cobrarán $2,595,560.00 por el servicio.\nArtículo 2.- Dos.\nArtículo 3.- Tres.\n"
    salida, informe = convertir.estructurar(crudo, {"id": "x"})
    assert informe["validacion"]["ok"]
    alterada = salida.replace("2,595,560.00", "2 595 560 00")
    v = convertir.validar(crudo, alterada)
    assert not v["ok"] and "2,595,560.00" in v["numeros_perdidos"]


# ---------------------------------------------------------------------------------------------------
# Estructura: casos límite
# ---------------------------------------------------------------------------------------------------
def test_articulo_con_decimal_de_cuatro_cifras():
    a = convertir._articulo("Artículo 7.1000.- La fianza es un contrato")
    assert a and a["etiqueta"] == "Artículo 7.1000" and a["resto"] == "La fianza es un contrato" and not a["indice"]
    assert convertir._RX_NUM_ARTICULO_SUELTO.match("0.0032") is None      # la ficha suelta conserva el decimal corto


def test_sufijos_latinos_largos_y_letra_minuscula():
    assert convertir._articulo("Artículo 76 Quaterdecies.- Si un establecimiento")["etiqueta"] == "Artículo 76 Quaterdecies"
    assert convertir._articulo("Artículo 228 b.- Las acciones")["etiqueta"] == "Artículo 228 b"
    a = convertir._articulo("Artículo 5o. A ninguna persona podrá impedirse")
    assert a and a["etiqueta"] == "Artículo 5o." and a["resto"].startswith("A ninguna persona")   # 'A' es preposición


def test_mencion_cortada_por_el_salto_no_es_encabezado():
    crudo = ("Artículo 280.- Si la demanda de nulidad se presenta fuera del plazo que establece el\n"
             "Artículo 292.\n\nArtículo 281.- Uno.\n\nArtículo 282.- Dos.\n\nArtículo 283.- Tres.\n")
    salida, informe = convertir.estructurar(crudo, {"id": "x"})
    assert "#### Artículo 292" not in salida and informe["validacion"]["ok"]
    assert convertir.estructurar(salida, {"id": "x"})[0] == salida


def test_nombre_de_unidad_cortado_se_completa():
    crudo = ("SECCIÓN QUINTA A\nDE LA COMISIÓN DE ADMINISTRACIÓN\n\nArtículo 1.- Uno.\n\nArtículo 2.- Dos.\n\n"
             "Artículo 3.- Tres.\n")
    salida, informe = convertir.estructurar(crudo, {"id": "x"})
    assert "# SECCIÓN QUINTA A. DE LA COMISIÓN DE ADMINISTRACIÓN" in salida and informe["validacion"]["ok"]
    assert convertir.estructurar(salida, {"id": "x"})[0] == salida


def test_cabecera_que_termina_en_enlace_al_inicio_de_pagina_se_quita():
    # Guerrero: 'LEY NÚMERO 450 DE VÍCTIMAS DEL ESTADO LIBRE Y' abre cada página: es cabecera, no frase cortada.
    cuerpos = [f"Artículo {n}.- Texto del artículo {n} con contenido suficiente para la página." for n in range(1, 8)]
    limpio, informe = convertir.limpiar(paginas(*cuerpos, cabecera="LEY NÚMERO 450 DE VÍCTIMAS DEL ESTADO LIBRE Y", pie=""))
    assert informe["cabeceras_repetidas"].get("LEY NÚMERO 450 DE VÍCTIMAS DEL ESTADO LIBRE Y", 0) >= 6
    assert limpio.count("LEY NÚMERO 450 DE VÍCTIMAS DEL ESTADO LIBRE Y") <= 1


# ====================================================================================================
# Integración del Markdown estructurado en convertir.py: metadatos del front-matter y guardado en sitio.
# ====================================================================================================
DOC = {"id": "jalisco__ley_x", "nombre": 'Ley "X" del Estado de Jalisco', "entidad": "Jalisco",
       "grupo": "Estados/Jalisco", "estado": "vigente", "archivo": "Ley_X"}
REGISTRO = {"archivo": "PDF/Estados/Jalisco/Ley_X.pdf", "url": "https://congreso.gob.mx/x.pdf", "fecha_reforma": None,
            "sha256": "a" * 64, "compresion": {"sha256_original": "b" * 64}}
CRUDO = ("LEY X\n\nCAPÍTULO I\nDisposiciones generales\n\nArtículo 1. Esta ley es de orden\npúblico.\n\n"
         "Artículo 2. Se aplica en todo\nel estado.\n\nArtículo 3. Entra en vigor al día siguiente.\n")


def test_meta_markdown_toma_identidad_del_catalogo_y_origen_del_manifiesto():
    meta = convertir.meta_markdown(DOC, REGISTRO, "markitdown", "2026-10-06")
    assert meta["id"] == "jalisco__ley_x" and meta["entidad"] == "Jalisco" and meta["fuente"] == REGISTRO["url"]
    assert meta["sha256_origen"] == "b" * 64            # la huella del oficial, no la del comprimido
    assert meta["fecha_reforma"] is None and meta["convertido"] == "2026-10-06" and meta["metodo"] == "markitdown"
    assert set(meta) <= set(convertir.CLAVES_FRONT_MATTER)


def test_estructurar_texto_valida_y_pone_front_matter():
    meta = convertir.meta_markdown(DOC, REGISTRO, "markitdown", "2026-10-06")
    texto, informe, validacion = convertir.estructurar_texto(CRUDO, meta)
    assert validacion["ok"] and informe["articulos"] == 3
    fm, cuerpo = convertir.separar_front_matter(texto)
    assert fm and fm["id"] == "jalisco__ley_x" and fm["version"] == convertir.VERSION and fm["nombre"] == DOC["nombre"]
    assert "#### Artículo 2" in cuerpo and "Se aplica en todo el estado." in cuerpo


def test_estructurar_en_sitio_escribe_el_formato_vigente(tmp_path: Path):
    ruta = tmp_path / "x.md"
    ruta.write_text(CRUDO, encoding="utf-8")
    r = convertir._estructurar_en_sitio(ruta, convertir.meta_markdown(DOC, REGISTRO, "markitdown", "2026-10-06"))
    assert r["formato"] == convertir.VERSION and r["articulos"] == 3 and "aviso" not in r
    texto = ruta.read_text(encoding="utf-8")
    assert texto.startswith("---\n") and texto.endswith("\n") and r["caracteres"] == len(texto)
    assert convertir.validar(CRUDO, texto)["ok"]
    # reaplicarlo no cambia nada (idempotente)
    r2 = convertir._estructurar_en_sitio(ruta, convertir.meta_markdown(DOC, REGISTRO, "markitdown", "2026-10-06"))
    assert r2["formato"] == convertir.VERSION and ruta.read_text(encoding="utf-8") == texto


def test_estructurar_en_sitio_conserva_el_crudo_si_perderia_palabras(tmp_path: Path, monkeypatch):
    ruta = tmp_path / "x.md"
    ruta.write_text(CRUDO, encoding="utf-8")
    real = convertir.estructurar

    def estructurar_con_perdida(crudo, meta):
        texto, informe = real(crudo, meta)
        informe["validacion"] = {"ok": False, "total_perdidas": 2, "palabras_perdidas": ["orden", "público"]}
        return texto, informe
    monkeypatch.setattr(convertir, "estructurar", estructurar_con_perdida)
    r = convertir._estructurar_en_sitio(ruta, convertir.meta_markdown(DOC, REGISTRO, "markitdown", "2026-10-06"))
    assert r["formato"] == 1 and "perdería 2 palabra(s)" in r["aviso"] and "orden" in r["aviso"]
    assert ruta.read_text(encoding="utf-8") == CRUDO          # el archivo no se tocó


def test_motivo_rechazo_resume_cada_causa():
    v = {"ok": False, "total_perdidas": 1, "palabras_perdidas": ["413"], "numeros_perdidos": ["5/dec-12"],
         "bigramas_distintos": 2, "lineas_sospechosas": [{"linea": 7, "texto": "Guerrero;", "motivo": "termina en ';'"}]}
    m = convertir.motivo_rechazo(v)
    assert "perdería 1 palabra(s) (413)" in m and "5/dec-12" in m and "orden" in m and "línea 7" in m
    assert convertir.motivo_rechazo({"ok": False}) == "la validación no lo aceptó"



# ====================================================================================================
# comprimir: el mismo PDF con el mismo nivel da siempre el mismo archivo
# ====================================================================================================
def test_comprimir_da_el_mismo_archivo_cada_vez(tmp_path: Path):
    pytest.importorskip("pikepdf")
    imagen = pytest.importorskip("PIL.Image")
    from herramientas import comprimir
    from herramientas.comun import sha256_archivo
    original = tmp_path / "original.pdf"
    gris = imagen.new("L", (400, 400), 255)
    for x in range(0, 400, 8):                        # una "tabla" escaneada en gris (lo que el nivel 30 reduce)
        for y in range(400):
            gris.putpixel((x, y), 90 + (x % 50))
    gris.save(original, "PDF", resolution=100)
    primero, segundo = tmp_path / "1.pdf", tmp_path / "2.pdf"
    comprimir.comprimir(original, primero, 30)
    time.sleep(1.1)       # sin /ID determinista, qpdf lo genera con el reloj (resolución de un segundo)
    comprimir.comprimir(original, segundo, 30)
    assert sha256_archivo(primero) == sha256_archivo(segundo)
