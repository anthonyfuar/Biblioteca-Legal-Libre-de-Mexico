"""Pruebas de herramientas/descargar.py."""
from __future__ import annotations

import argparse
import os
import random
import re
import socketserver
import sys
import threading
import time
import zipfile
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from herramientas import catalogo, comun, descargar
from herramientas.comun import _DETENER, CARPETA_PDF, DOCUMENTOS
from herramientas.descargar import decidir


# ====================================================================================================
# test_descarga_segura
# ====================================================================================================
def test_esquemas_no_http_rechazados(tmp_path):
    for url in ("file:///etc/hosts", "data:text/plain,hola", "ftp://127.0.0.1:1/x.pdf", "/etc/hosts", "gopher://x"):
        r = descargar._http_descargar(url, tmp_path / "d")
        assert r["ok"] is False and "http" in r["error"], url
        assert not (tmp_path / "d").exists()


def test_fuentes_no_oficiales():
    assert descargar.es_fuente_no_oficial("https://legislacion.vlex.com.mx/x") == "vlex.com.mx"
    assert descargar.es_fuente_no_oficial("http://www.ordenjuridico.gob.mx/x.pdf") == "ordenjuridico.gob.mx"
    assert descargar.es_fuente_no_oficial("https://www.pj.gob.pe/x") == "pj.gob.pe"
    assert descargar.es_fuente_no_oficial("https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf") is None
    assert descargar.es_fuente_no_oficial("https://notvlex.com.mx/x") is None    # solo el dominio o sus subdominios


def test_guardar_json_conserva_permisos_y_lf(tmp_path):
    f = tmp_path / "a.json"
    f.write_text("{}")
    os.chmod(f, 0o664)
    comun.guardar_json(f, {"a": "ñ", "b": [1, 2]})
    datos = f.read_bytes()
    assert b"\r" not in datos and datos.endswith(b"\n") and "ñ".encode() in datos
    assert f.stat().st_mode & 0o777 == 0o664
    nuevo = tmp_path / "n.json"
    comun.guardar_json(nuevo, {})
    assert nuevo.stat().st_mode & 0o777 == (0o666 & ~comun.umask())


def test_reemplazar_archivo_atomico(tmp_path):
    origen = tmp_path / "o.bin"
    origen.write_bytes(b"nuevo")
    destino = tmp_path / "sub" / "d.bin"
    comun.reemplazar_archivo(origen, destino)
    assert destino.read_bytes() == b"nuevo" and not origen.exists()
    origen.write_bytes(b"otro")
    comun.reemplazar_archivo(origen, destino, conservar_origen=True)
    assert destino.read_bytes() == b"otro" and origen.exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_sha256_archivo(tmp_path):
    f = tmp_path / "x"
    f.write_bytes(b"abc")
    assert comun.sha256_archivo(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


# ====================================================================================================
# La matriz de decisión de descargar.py (NUEVO / SIN_CAMBIO / SIN_CAMBIO_REAL / ACTUALIZADO / DUDOSO).
# ====================================================================================================
A, B = "a" * 64, "b" * 64
V, N = (2025, 1, 1), (2026, 6, 30)


def test_sin_archivo_es_nuevo():
    assert decidir(False, None, B, None, None) == ("NUEVO", None)


def test_misma_huella_es_sin_cambio_aunque_no_haya_fechas():
    assert decidir(True, A, A, None, None) == ("SIN_CAMBIO", None)
    assert decidir(True, A, A, V, N) == ("SIN_CAMBIO", None)


def test_forzar_actualiza_aunque_la_fecha_sea_mas_vieja():
    assert decidir(True, A, B, N, V, forzar=True) == ("ACTUALIZADO", None)
    assert decidir(True, A, B, None, None, forzar=True) == ("ACTUALIZADO", None)


def test_fecha_mas_reciente_actualiza():
    assert decidir(True, A, B, V, N) == ("ACTUALIZADO", None)


def test_misma_fecha_es_regeneracion_del_servidor():
    assert decidir(True, A, B, N, N) == ("SIN_CAMBIO_REAL", None)


@pytest.mark.parametrize("fecha_actual, fecha_nueva, pista", [
    (N, V, "MÁS VIEJA"), (None, N, "no se pudo leer"), (V, None, "no se pudo leer"), (None, None, "no se pudo leer"),
])
def test_casos_dudosos(fecha_actual, fecha_nueva, pista):
    resultado, motivo = decidir(True, A, B, fecha_actual, fecha_nueva)
    assert resultado == "DUDOSO" and pista in motivo


# ====================================================================================================
# Pruebas de herramientas/descargar.py contra un servidor HTTP local (http.server en un hilo, puerto libre; sin red
# ====================================================================================================
CUERPO_PDF = b"%PDF-1.4\n" + random.Random(7).randbytes(2_500_000)   # > 1 MB: ejercita la escritura por bloques
BLOQUE = b"x" * (1 << 16)


class _Portal(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    solicitudes: ClassVar[dict[str, int]] = {}   # cuántas veces se pidió cada ruta (lo reinicia el fixture)

    def log_message(self, *_):                 # sin ruido en la salida de pytest
        pass

    def _responder(self, codigo, cuerpo=b"", tipo="application/octet-stream", extra=()):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Connection", "close")
        for clave, valor in extra:
            self.send_header(clave, valor)
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        n = self.solicitudes[self.path] = self.solicitudes.get(self.path, 0) + 1
        try:
            self._atender(n)
        except OSError:        # el cliente cortó a mitad (tope, plazo o interrupción): es lo esperado
            pass

    def _atender(self, n: int):
        if self.path == "/doc.pdf":
            self._responder(200, CUERPO_PDF, "application/pdf")
        elif self.path == "/redir":
            self._responder(302, extra=[("Location", "/doc.pdf")])
        elif self.path == "/inestable":        # 503 la primera vez, 200 después
            if n == 1:
                self._responder(503, b"ocupado", "text/plain")
            else:
                self._responder(200, b"ok", "text/plain")
        elif self.path == "/ocupado":          # 429 con Retry-After la primera vez, 200 después
            if n == 1:
                self._responder(429, b"", "text/plain", extra=[("Retry-After", "1")])
            else:
                self._responder(200, b"ok", "text/plain")
        elif self.path == "/anunciado":        # Content-Length mayor que el tope de la prueba
            self._responder(200, b"a" * 5000)
        elif self.path == "/chunked":          # sin Content-Length: 10 000 bytes por trozos
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            for _ in range(20):
                self.wfile.write(b"1f4\r\n" + b"c" * 500 + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        elif self.path == "/gotea":            # anuncia 64 MB y manda 64 KiB cada 0.2 s
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(BLOQUE) * 1000))
            self.send_header("Connection", "close")
            self.end_headers()
            for _ in range(1000):
                self.wfile.write(BLOQUE)
                time.sleep(0.2)
        elif self.path == "/detener":          # tras el primer bloque, la herramienta "empieza a terminar"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(BLOQUE) * 11))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(BLOQUE)
            _DETENER.set()
            for _ in range(10):
                time.sleep(0.02)
                self.wfile.write(BLOQUE)
        else:
            self._responder(404, b"no existe", "text/plain")


class _Servidor(ThreadingHTTPServer):
    def server_bind(self):     # sin socket.getfqdn(): en macOS puede tardar 10 s esperando al DNS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


@pytest.fixture
def portal():
    _Portal.solicitudes = {}
    servidor = _Servidor(("127.0.0.1", 0), _Portal)
    threading.Thread(target=servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


@pytest.fixture(autouse=True)
def _rapido_y_sin_curl(monkeypatch):
    """Esperas de milisegundos entre reintentos, _DETENER limpio y nunca el curl del sistema."""
    _DETENER.clear()
    monkeypatch.setattr(descargar, "ESPERAS_REINTENTO", (0.01, 0.01, 0.01))
    monkeypatch.setattr(descargar, "_descargar_con_curl",
                        lambda *a, **k: pytest.fail("no debe recurrir al curl del sistema"))
    yield
    _DETENER.clear()


def test_descarga_binaria(portal, tmp_path):
    destino = tmp_path / "sub" / "d.pdf"           # la carpeta se crea sola
    r = descargar.http_descargar(f"{portal}/doc.pdf", destino)
    assert r["ok"] is True and r["error"] == ""
    assert r["http"] == 200 and r["tipo"] == "application/pdf"
    assert r["bytes"] == len(CUERPO_PDF) and destino.read_bytes() == CUERPO_PDF
    assert r["url_final"] == f"{portal}/doc.pdf"
    assert r["intentos"] == [f"{portal}/doc.pdf -> HTTP 200"]


def test_redireccion_302(portal, tmp_path):
    r = descargar.http_descargar(f"{portal}/redir", tmp_path / "d.pdf")
    assert r["ok"] is True and r["http"] == 200
    assert r["url_final"] == f"{portal}/doc.pdf"
    assert r["bytes"] == len(CUERPO_PDF) and (tmp_path / "d.pdf").read_bytes() == CUERPO_PDF


def test_404_sin_reintento(portal, tmp_path):
    r = descargar.http_descargar(f"{portal}/no-existe", tmp_path / "d.pdf")
    assert r["ok"] is False and "HTTP 404" in r["error"]
    assert r["http"] is None and r["bytes"] == 0 and r["url_final"] == f"{portal}/no-existe"
    assert len(r["intentos"]) == 1 and _Portal.solicitudes["/no-existe"] == 1
    assert not (tmp_path / "d.pdf").exists()


def test_503_reintenta_y_acierta(portal, tmp_path):
    inicio = time.monotonic()
    r = descargar.http_descargar(f"{portal}/inestable", tmp_path / "d.txt")
    assert time.monotonic() - inicio < 2
    assert r["ok"] is True and r["http"] == 200 and (tmp_path / "d.txt").read_bytes() == b"ok"
    assert _Portal.solicitudes["/inestable"] == 2 and len(r["intentos"]) == 2
    assert "HTTP 503" in r["intentos"][0] and "reintento" in r["intentos"][0]
    assert r["intentos"][1].endswith("HTTP 200")


def test_429_respeta_retry_after(portal, tmp_path):
    inicio = time.monotonic()
    r = descargar.http_descargar(f"{portal}/ocupado", tmp_path / "d.txt")
    assert 1.0 <= time.monotonic() - inicio < 3      # esperó el segundo que pidió el servidor
    assert r["ok"] is True and _Portal.solicitudes["/ocupado"] == 2
    assert "HTTP 429" in r["intentos"][0] and "reintento en 1.0 s" in r["intentos"][0]


def test_tope_por_content_length(portal, tmp_path):
    r = descargar.http_descargar(f"{portal}/anunciado", tmp_path / "d.bin", max_bytes=1000)
    assert r["ok"] is False and "tope" in r["error"] and "4.9 KB" in r["error"]
    assert not (tmp_path / "d.bin").exists()
    assert len(r["intentos"]) == 1 and _Portal.solicitudes["/anunciado"] == 1     # definitivo: sin reintentos


def test_tope_por_cuerpo_chunked(portal, tmp_path):
    r = descargar.http_descargar(f"{portal}/chunked", tmp_path / "d.bin", max_bytes=1000)
    assert r["ok"] is False and "tope" in r["error"]
    assert not (tmp_path / "d.bin").exists()
    assert len(r["intentos"]) == 1


def test_chunked_dentro_del_tope(portal, tmp_path):
    r = descargar.http_descargar(f"{portal}/chunked", tmp_path / "d.bin", max_bytes=10_000)
    assert r["ok"] is True and r["bytes"] == 10_000 and (tmp_path / "d.bin").stat().st_size == 10_000


def test_plazo_total_excedido(portal, tmp_path):
    inicio = time.monotonic()
    r = descargar.http_descargar(f"{portal}/gotea", tmp_path / "d.pdf", plazo_total=1)
    assert time.monotonic() - inicio < 5
    assert r["ok"] is False and "plazo" in r["error"]
    assert not (tmp_path / "d.pdf").exists()


def test_esquemas_rechazados(tmp_path):
    for url in ("file:///etc/hosts", "data:text/plain,hola", "ftp://127.0.0.1:1/x.pdf", "/etc/hosts"):
        r = descargar.http_descargar(url, tmp_path / "d")
        assert r["ok"] is False and "http://" in r["error"] and r["intentos"] == [], url
        assert not (tmp_path / "d").exists()


def test_detener_a_mitad_borra_el_parcial(portal, tmp_path):
    r = descargar.http_descargar(f"{portal}/detener", tmp_path / "d.pdf")
    assert r["ok"] is False and "detenida" in r["error"]
    assert not (tmp_path / "d.pdf").exists()
    assert len(r["intentos"]) == 1                    # no se reintenta ni se prueba https://


def test_codificar_url():
    assert descargar.codificar_url("https://x.gob.mx/leyes/Código Civil.pdf") == \
        "https://x.gob.mx/leyes/C%C3%B3digo%20Civil.pdf"
    ya = "https://x.gob.mx/leyes/C%C3%B3digo%20Civil.pdf"
    assert descargar.codificar_url(ya) == ya                                   # los %xx se dejan intactos
    assert descargar.codificar_url("  https://x.gob.mx/a b.pdf?q=niño y&n=1 ") == \
        "https://x.gob.mx/a%20b.pdf?q=ni%C3%B1o%20y&n=1"


def test_es_fuente_no_oficial():
    assert descargar.es_fuente_no_oficial("https://legislacion.vlex.com.mx/x") == "vlex.com.mx"
    assert descargar.es_fuente_no_oficial("http://www.ordenjuridico.gob.mx/x.pdf") == "ordenjuridico.gob.mx"
    assert descargar.es_fuente_no_oficial("https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf") is None
    assert descargar.es_fuente_no_oficial("https://notvlex.com.mx/x") is None    # solo el dominio o sus subdominios


# ====================================================================================================
# Pruebas de herramientas/descargar.py: enlaces de una página (con el nombre de la ley en otra celda, botones y visores),
# ====================================================================================================
BASE = "https://congreso.gob.mx/leyes/index.html"


def _enlaces(cuerpo, base=BASE):
    return descargar.enlaces_de_html(f"<html><body>{cuerpo}</body></html>", base)


def test_enlace_simple_con_su_texto_y_url_absoluta():
    assert _enlaces('<a href="pdf/amparo.pdf">Ley de Amparo</a>') == [
        ("https://congreso.gob.mx/leyes/pdf/amparo.pdf", "Ley de Amparo")]


def test_enlace_que_solo_dice_pdf_usa_el_texto_de_su_fila():
    fila = ('<table><tr><td>Ley de Salud</td><td>12/03/2024</td>'
            '<td><a href="salud.pdf">PDF</a></td></tr></table>')
    (url, texto), = _enlaces(fila)
    assert url == "https://congreso.gob.mx/leyes/salud.pdf"
    assert texto == "Ley de Salud 12/03/2024 PDF [PDF]"


def test_enlace_con_icono_en_un_li_toma_el_nombre_del_li():
    (_, texto), = _enlaces('<ul><li>Código Civil <a href="cc.pdf"><img src="i.png"></a></li></ul>')
    assert texto == "Código Civil"


def test_div_con_clase_row_hace_de_fila_como_en_colima():
    cuerpo = ('<div class="row mb-2"><div class="col-8">Ley de Aguas</div>'
              '<div class="col-4"><a href="aguas.pdf">Descargar</a></div></div>')
    (_, texto), = _enlaces(cuerpo)
    assert texto == "Ley de Aguas Descargar [Descargar]"


def test_un_div_cualquiera_no_es_fila():
    (_, texto), = _enlaces('<div class="card">Ley de Aguas <a href="aguas.pdf">Descargar</a></div>')
    assert texto == "Descargar"


def test_boton_onclick_cuenta_como_enlace_de_su_fila():
    cuerpo = ('<table><tr><td>Ley de Tránsito</td>'
              '<td><button onclick="goToUrl(\'docs/transito.pdf\')">Ver</button></td></tr></table>')
    assert _enlaces(cuerpo) == [("https://congreso.gob.mx/leyes/docs/transito.pdf", "Ley de Tránsito Ver")]


def test_iframe_con_pdf_es_un_enlace_pero_no_otros_visores():
    assert _enlaces('<iframe src="/visor/ley.pdf"></iframe>') == [("https://congreso.gob.mx/visor/ley.pdf", "")]
    assert _enlaces('<iframe src="/visor/mapa.html"></iframe>') == []


def test_descarta_javascript_mailto_y_anclas():
    cuerpo = ('<a href="javascript:void(0)">x</a><a href="mailto:a@b.mx">correo</a>'
              '<a href="#arriba">subir</a><a href="ley.pdf">Ley</a>')
    assert [u for u, _ in _enlaces(cuerpo)] == ["https://congreso.gob.mx/leyes/ley.pdf"]


def test_resuelve_relativas_contra_la_base_y_quita_duplicados():
    cuerpo = ('<a href="../pdf/a.pdf">Primero</a><a href="/pdf/a.pdf">Repetido</a>'
              '<a href=" ../pdf/b.pdf ">Con espacios</a>')
    assert _enlaces(cuerpo) == [("https://congreso.gob.mx/pdf/a.pdf", "Primero"),
                                ("https://congreso.gob.mx/pdf/b.pdf", "Con espacios")]


def test_una_fila_demasiado_larga_no_sirve_como_nombre():
    cuerpo = f'<table><tr><td>{"bla " * 100}</td><td><a href="x.pdf">PDF</a></td></tr></table>'
    (_, texto), = _enlaces(cuerpo)
    assert texto == "PDF"


def test_ignora_scripts_y_estilos_dentro_de_la_fila():
    cuerpo = ('<table><tr><td><script>var x = "oculto";</script><style>td{}</style>Ley Verde</td>'
              '<td><a href="v.pdf">PDF</a></td></tr></table>')
    (_, texto), = _enlaces(cuerpo)
    assert texto == "Ley Verde PDF [PDF]"


def test_html_roto_no_revienta():
    roto = ('<table><tr><td>Ley <a href="a.pdf">Uno</a></td></div></tr></tr>'
            '<a href="b.pdf">Dos</a><li></ul>&&&<<< <a href="c.pdf"')
    assert [u for u, _ in descargar.enlaces_de_html(roto, BASE)] == [
        "https://congreso.gob.mx/leyes/a.pdf", "https://congreso.gob.mx/leyes/b.pdf"]
    assert descargar.enlaces_de_html("", BASE) == []


@pytest.mark.parametrize("url, texto, esperado", [
    ("https://x.gob.mx/leyes/ley.pdf", "", True),
    ("https://x.gob.mx/leyes/ley.PDF?v=2", "", True),
    ("https://x.gob.mx/leyes/ley.doc", "", True),
    ("https://x.gob.mx/leyes/ley.docx", "", True),
    ("https://x.gob.mx/descargar?id=7", "", True),
    ("https://x.gob.mx/download/7", "", True),
    ("https://x.gob.mx/documento/7", "", True),
    ("https://x.gob.mx/ver?id=7", "Ver PDF", True),
    ("https://x.gob.mx/leyes/index.html", "Inicio", False),
    ("https://x.gob.mx/leyes/ley.html", "Ley de Salud", False),
])
def test_parece_enlace_a_documento(url, texto, esperado):
    assert descargar.parece_enlace_a_documento(url, texto) is esperado


def test_es_word_solo_por_la_extension_real():
    assert descargar.es_word("https://x.gob.mx/ley.doc")
    assert descargar.es_word("https://x.gob.mx/ley.DOCX?descarga=1")
    assert not descargar.es_word("https://x.gob.mx/ley.pdf")
    assert not descargar.es_word("https://x.gob.mx/documentos/")


def test_leer_html_en_utf8_y_latin1(tmp_path):
    utf8 = tmp_path / "u.html"
    utf8.write_bytes("<p>Año y señal</p>".encode())
    assert descargar.leer_html(utf8) == "<p>Año y señal</p>"
    latin = tmp_path / "l.html"
    latin.write_bytes("<p>Año y señal</p>".encode("latin-1"))
    assert descargar.leer_html(latin) == "<p>Año y señal</p>"




# ====================================================================================================
# Pruebas de la fecha de reforma (herramientas/descargar.py): cada formato de portada que la biblioteca ha encontrado.
# ====================================================================================================
def _futuro(dias: int = 400) -> date:
    return date.today() + timedelta(days=dias)


# ---------------------------------------------------------------------------
# fechas_en: formatos de fecha
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fragmento, esperado", [
    ("dof 09-04-2026", (2026, 4, 9)),
    ("el 15 de marzo de 2025", (2025, 3, 15)),
    ("17 mayo 2025", (2025, 5, 17)),
    ("1o. de enero del 2024", (2024, 1, 1)),
    ("1º de octubre de 2025", (2025, 10, 1)),
    ("poe 2024.08.24", (2024, 8, 24)),                     # Chihuahua, año primero
    ("p.o. no. 50, seccion ii, 04-sep-2026", (2026, 9, 4)),  # Baja California, mes abreviado
    ("p.o.e. 05/diciembre/2023", (2023, 12, 5)),           # Campeche, mes completo entre diagonales
    ("10 de abrilde 2026", (2026, 4, 10)),                 # espacio comido
    ("03 de septiembre del ano 2026", (2026, 9, 3)),       # Chiapas, "del año"
    ("10 de julo de 2026", (2026, 7, 10)),                 # errata en el mes (Veracruz)
])
def test_fechas_en_formatos(fragmento, esperado):
    assert esperado in descargar.fechas_en(fragmento)


def test_fechas_en_descarta_invalidas():
    assert descargar.fechas_en("45-13-2026 y 12 de fecha 2020 y 3 de mes de 1800") == []
    assert descargar.fechas_en("") == []


# ---------------------------------------------------------------------------
# fecha_reforma: rótulos de reforma (nivel 1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("Última Reforma DOF 09-04-2026", (2026, 4, 9)),
    ("ÚLTIMA REFORMA PUBLICADA EN EL PERIÓDICO OFICIAL EL 15 DE MARZO DE 2025", (2025, 3, 15)),
    ("Última reforma 17 MAYO 2025", (2025, 5, 17)),
    ("última reforma publicada el 1o. de enero del 2024", (2024, 1, 1)),
    ("Última Reforma POE 2024.08.24", (2024, 8, 24)),
    ("Última Reforma P.O. No. 50, Sección II, 04-Sep-2026", (2026, 9, 4)),
    ("ÚLTIMA ENMIENDA PUBLICADA EN EL PERIÓDICO OFICIAL: 3 DE MAYO DE 2024", (2024, 5, 3)),
    ("ÚLTIMA ENMIEDA PUBLICADA EN EL PERIÓDICO OFICIAL: 3 DE MAYO DE 2024", (2024, 5, 3)),   # Nayarit
    ("Última reforma publicada: 10 DE ABRILDE 2026", (2026, 4, 10)),
    ("Reformada por última vez el 2 de febrero de 2023", (2023, 2, 2)),
    ("Última reforma publicada mediante periódico oficial número 128 de fecha 03 de septiembre "
     "del año 2026. Decreto número 302.", (2026, 9, 3)),
])
def test_fecha_reforma_rotulos(texto, esperado):
    assert descargar.fecha_reforma(texto) == esperado


def test_fecha_reforma_toma_la_mas_reciente_de_la_ventana():
    texto = ("ÚLTIMA REFORMA: Decreto 123 de fecha 10 de marzo de 2025, publicado en el Periódico Oficial "
             "el 20 de marzo de 2025.")
    assert descargar.fecha_reforma(texto) == (2025, 3, 20)


def test_fecha_reforma_no_toma_la_publicacion_original():
    texto = ("Última reforma publicada en el periódico oficial: 26 de mayo de 2025. Ley publicada en el "
             "suplemento al número 1 del periódico oficial del estado, el domingo 1 de enero de 2006.")
    assert descargar.fecha_reforma(texto) == (2025, 5, 26)


def test_fecha_reforma_columnas_con_fe_de_erratas():
    # Copias de la CNDH: "Última reforma | Fe de erratas | ..." y luego las fechas en el mismo orden
    texto = "Última reforma Fe de erratas 15-03-2025 20-04-2025"
    assert descargar.fecha_reforma(texto) == (2025, 3, 15)


def test_fecha_reforma_descarta_futuras():
    f = _futuro()
    assert descargar.fecha_reforma(f"Última reforma DOF {f.day:02d}-{f.month:02d}-{f.year}") is None
    texto = (f"Última reforma DOF 09-04-2026 para el ejercicio fiscal del 1 de enero al 31 de diciembre "
             f"de {f.year}")
    assert descargar.fecha_reforma(texto) == (2026, 4, 9)


def test_fecha_reforma_ignora_texto_vigente_a_partir():
    # Zacatecas: la entrada en vigor no es reforma, aunque sea posterior
    texto = "Última Reforma POG 15-12-2025\nTEXTO VIGENTE A PARTIR DEL 1 DE ENERO DE 2026"
    assert descargar.fecha_reforma(texto) == (2025, 12, 15)
    assert descargar.fecha_reforma("Ley publicada el 24 de mayo de 1986.\nTEXTO VIGENTE A PARTIR DEL 24 DE JULIO DE 1986") is None


def test_fecha_reforma_errata_en_el_mes_no_cae_en_la_publicacion():
    texto = ("Última Actualización: 10 de julo de 2026. Ley publicada en la Gaceta Oficial el 30 de junio "
             "de 2000.")
    assert descargar.fecha_reforma(texto) == (2026, 7, 10)


def test_fecha_reforma_sin_rotulo_ni_texto():
    assert descargar.fecha_reforma("") is None
    assert descargar.fecha_reforma(None) is None
    assert descargar.fecha_reforma("Ley publicada en el Periódico Oficial el 15 de marzo de 2025") is None
    assert descargar.fecha_reforma("Última reforma: TEXTO ORIGINAL") is None   # Morelos, sin reformas


# ---------------------------------------------------------------------------
# fecha_reforma: rótulos de actualización y encabezado de Guanajuato (nivel 2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto, esperado", [
    ("Coordinación de Investigaciones Legislativas\nÚltima Actualización\n8 DE OCTUBRE DE 2025\n"
     "Código publicado en la Gaceta Oficial el 15 de septiembre de 1932.", (2025, 10, 8)),        # Veracruz
    ("Última actualización publicada en G.O.E: 3 de julio de 2020", (2020, 7, 3)),
    ("ultima actualizacion: 23/06/2026.", (2026, 6, 23)),                                        # Aguascalientes
    ("ÚLTIMAS MODIFICACIONES PUBLICADAS EN EL PERIÓDICO OFICIAL DEL GOBIERNO DEL ESTADO DE GUERRERO, "
     "EN SU EDICIÓN No. 102, DE FECHA MARTES 23 DE DICIEMBRE DE 2025. TEXTO ORIGINAL. CÓDIGO PUBLICADO "
     "EN EL PERIÓDICO OFICIAL EDICIÓN No. 103 ALCANCE V, DE FECHA MARTES 27 DICIEMBRE DE 2022.", (2025, 12, 23)),
    ("ÚLTIMA MODIFICACIÓN PUBLICADA EN EL PERIÓDICO OFICIAL P.O. EDICIÓN No 28 ALCANCE I, DE FECHA "
     "MARTES 07 DE ABRIL DE 2026.", (2026, 4, 7)),
    ("ÚLTIMAS ADICIONES PUBLICADAS EN EL PERIÓDICO OFICIAL EDICIÓN No. 21, DE FECHA MARTES 12 DE MARZO "
     "DE 2024. DECRETO 685", (2024, 3, 12)),
    ("Documento de consulta, última modificación publicada en P.O.E. 05/diciembre/2023", (2023, 12, 5)),  # Campeche
    ("Texto actualizado al 15 de marzo de 2026", (2026, 3, 15)),
    ("Ley actualizada al 15-03-2026", (2026, 3, 15)),
])
def test_fecha_reforma_rotulos_de_actualizacion(texto, esperado):
    assert descargar.fecha_reforma(texto) == esperado


def test_fecha_reforma_la_actualizacion_no_le_gana_a_la_reforma():
    # Aguascalientes: conviven y no siempre coinciden; manda "última reforma"
    texto = ("Última actualización: 26/04/2025. Última reforma publicada en el periódico oficial: "
             "26 de mayo de 2025.")
    assert descargar.fecha_reforma(texto) == (2025, 5, 26)
    texto = "Última reforma DOF 09-04-2026. Última actualización: 30/09/2026."
    assert descargar.fecha_reforma(texto) == (2026, 4, 9)


GUANAJUATO = """Código Civil para el Estado de Guanajuato
H. CONGRESO DEL ESTADO DE GUANAJUATO
Instituto de Investigaciones Legislativas
Expidió: XLVI Legislatura
Publicada: P.O. Núm. 39, 14-05-1967
Reforma: P.O. Núm. 252; Tercera Parte; DL 113; 18-12-2025
Art. 2. (DEROGADO, P.O. 21 DE MAYO DE 2026)
(REFORMADA SU DENOMINACIÓN, P.O. 3 DE JUNIO DE 2026)
Artículo reformado P.O. 30-11-2026
"""


def test_fecha_reforma_guanajuato_encabezado():
    assert descargar.fecha_reforma(GUANAJUATO) == (2025, 12, 18)


@pytest.mark.parametrize("linea", [
    "Reforma Publicada: P.O. Núm. 132; Tercera Parte; 03-07-2026",
    "Reformada P.O. No. 107; 3ª. Parte; 29-05-2026",
    "Reformada: P.O. Núm. 107; Tercera Parte; 29-05-2026",
    "Reforma P.O. Número 187, Sexta Parte,  17-09-2024",
    "Reforma: P.O. Número 154; Segunda Parte; Decreto:204; 04-08-2026",
])
def test_fecha_reforma_guanajuato_variantes(linea):
    texto = f"Publicada: P.O. Núm. 39, 14-05-1967\n{linea}\n"
    assert descargar.fecha_reforma(texto) == descargar.fechas_en(linea.lower())[-1]


def test_fecha_reforma_guanajuato_varias_lineas_toma_la_mas_reciente():
    texto = ("Publicada: P.O. Núm. 39, 14-05-1967\n"
             "Reforma: P.O. Núm. 100; Tercera Parte; 18-12-2023\n"
             "Reforma: P.O. Núm. 252; Tercera Parte; 18-12-2025\n"
             "Reforma: P.O. Núm. 200; Tercera Parte; 18-12-2024\n")
    assert descargar.fecha_reforma(texto) == (2025, 12, 18)


def test_fecha_reforma_guanajuato_solo_publicada_no_cuenta():
    assert descargar.fecha_reforma("Publicada: P.O. Núm. 70, Tercera Parte, 08-04-2026\n") is None


def test_fecha_reforma_guanajuato_solo_en_el_encabezado():
    # Tabasco lista sus reformas una por línea; fuera de la zona de encabezado no se toma
    texto = "x" * (descargar.ZONA_ENCABEZADO + 10) + "\nReformada P.O. 7065 Spto. C de fecha 22 de mayo de 2010\n"
    assert descargar.fecha_reforma(texto) is None


# ---------------------------------------------------------------------------
# Querétaro (Ficha Genealógica), Puebla (tabla) y Nueva Ley DOF
# ---------------------------------------------------------------------------

FICHA = """Ficha Genealógica
Historial de cambios
20/12/2014 (No. 77) Se reforman los artículos 1 y 2
15/06/2022 (No. 40) Se adiciona el artículo 3
03/02/2019 (No. 12) Fe de erratas
"""


def test_fecha_reforma_ficha_genealogica():
    assert descargar.fecha_reforma(FICHA) == (2022, 6, 15)
    assert descargar.necesita_mas_paginas(FICHA) is True


PUEBLA_COMPLETA = """Gobierno del Estado de Puebla
Secretaría de Gobernación
Orden Jurídico Poblano
Ley Orgánica Municipal
Publicación
Extracto del texto
REFORMAS
13/mar/2001
Se expide la Ley Orgánica Municipal.
17/abr/2026
ÚNICO. Se reforma la fracción II del artículo 56 de la Ley Orgánica Municipal.
8/jun/2026
ÚNICO. Se reforma el artículo 44 de la Ley Orgánica Municipal.
5
Orden Jurídico Poblano
CONTENIDO
LEY ORGÁNICA MUNICIPAL ..................................................... 17
CAPÍTULO I ............................................................................. 17
Artículo 1. Fracción reformada el 25/sep/2026.
"""


def test_fecha_reforma_puebla_tabla_completa():
    # La fecha es la más reciente de la tabla; las notas del cuerpo (tras el índice) no cuentan
    assert descargar.fecha_reforma(PUEBLA_COMPLETA) == (2026, 6, 8)
    assert descargar.necesita_mas_paginas(PUEBLA_COMPLETA) is False


def test_fecha_reforma_puebla_tabla_incompleta_no_da_fecha():
    incompleta = PUEBLA_COMPLETA.split("5\nOrden Jurídico Poblano\nCONTENIDO", maxsplit=1)[0]
    assert descargar.fecha_reforma(incompleta) is None
    assert descargar.necesita_mas_paginas(incompleta) is True


def test_fecha_reforma_puebla_fechas_largas_del_extracto_no_cuentan():
    texto = PUEBLA_COMPLETA.replace("ÚNICO. Se reforma el artículo 44 de la Ley Orgánica Municipal.",
                                    "ÚNICO. Entrará en vigor el 1 de septiembre de 2026.")
    assert descargar.fecha_reforma(texto) == (2026, 6, 8)


def test_fecha_reforma_nueva_ley_dof():
    assert descargar.fecha_reforma("Nueva Ley DOF 18-03-2025") == (2025, 3, 18)
    assert descargar.fecha_reforma("Nuevo Reglamento DOF 18-03-2025") == (2025, 3, 18)
    # Solo ese formato exacto: una portada estatal con "nueva ley" y otra fecha no cuenta
    assert descargar.fecha_reforma("Nueva Ley POG 18-06-2025") is None
    assert descargar.fecha_reforma("Nueva Ley publicada el 18 de marzo de 2025") is None
    # Si hay reformas, manda "última reforma"
    assert descargar.fecha_reforma("Nueva Ley DOF 18-03-2025\nÚltima reforma DOF 09-04-2026") == (2026, 4, 9)


def test_necesita_mas_paginas_normal():
    assert descargar.necesita_mas_paginas("Última Reforma DOF 09-04-2026") is False
    assert descargar.necesita_mas_paginas("") is False


# ---------------------------------------------------------------------------
# formato_fecha y leer_fecha
# ---------------------------------------------------------------------------

def test_formato_fecha_iso():
    assert descargar.formato_fecha((2026, 4, 9)) == "2026-04-09"
    assert descargar.formato_fecha((1999, 12, 31)) == "1999-12-31"
    assert descargar.formato_fecha(None) == "no encontrada"


@pytest.mark.parametrize("texto, esperado", [
    ("2026-04-09", (2026, 4, 9)),
    ("09-04-2026", (2026, 4, 9)),
    ("2026/04/09", (2026, 4, 9)),
    ("9/4/2026", (2026, 4, 9)),
    (" 2026-04-09 ", (2026, 4, 9)),
    ("31-02-2026", None),      # no existe
    ("2026-13-01", None),
    ("09-04-26", None),
    ("hoy", None),
    ("", None),
    (None, None),
])
def test_leer_fecha(texto, esperado):
    assert descargar.leer_fecha(texto) == esperado


def test_ida_y_vuelta():
    fecha = descargar.fecha_reforma("Última Reforma DOF 09-04-2026")
    assert descargar.leer_fecha(descargar.formato_fecha(fecha)) == fecha


# ====================================================================================================
# Pruebas de herramientas/descargar.py: tipo de Word por su firma, lectura de un .docx mínimo (párrafos, tabla con
# ====================================================================================================
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

DOCUMENTO = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}"><w:body>
<w:p><w:r><w:t>LEY DE PRUEBA</w:t></w:r></w:p>
<w:p><w:r><w:t>Artículo 1.</w:t><w:tab/><w:t>Primera línea</w:t><w:br/><w:t>Segunda</w:t></w:r></w:p>
<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>3</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
<w:p/>
<w:tbl><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="3000"/></w:tblGrid>
<w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Encabezado</w:t></w:r></w:p></w:tc></w:tr>
<w:tr><w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>b | c</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
<w:p><w:r><w:t>Fin</w:t></w:r></w:p>
</w:body></w:document>"""


def _docx_xml(ruta, xml=DOCUMENTO, nombre="word/document.xml"):
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr(nombre, xml)
    return ruta


@pytest.fixture
def docx(tmp_path):
    return _docx_xml(tmp_path / "prueba.docx")


def _doc_real():
    carpeta = DOCUMENTOS / CARPETA_PDF / "Estados" / "Baja_California_Sur"
    docs = sorted(carpeta.glob("*.doc"), key=lambda p: p.stat().st_size) if carpeta.is_dir() else []
    if not docs:
        pytest.skip("no hay un .doc real de Baja California Sur en la biblioteca")
    return docs[0]


def test_tipo_word_docx_por_su_contenido(tmp_path, docx):
    assert descargar.tipo_word(docx) == "docx"
    assert descargar.tipo_word(_docx_xml(tmp_path / "otro.zip", "<x/>", "otro.xml")) is None      # zip sin document.xml
    falso = tmp_path / "falso.docx"
    falso.write_bytes(b"PK\x03\x04 esto no es un zip")
    assert descargar.tipo_word(falso) is None


def test_tipo_word_ole_sin_worddocument_no_es_doc(tmp_path):
    excel = tmp_path / "hoja.xls"
    excel.write_bytes(OLE + b"\x00" * 600 + "Workbook".encode("utf-16-le") + b"\x00" * 100)
    assert descargar.tipo_word(excel) is None
    texto = tmp_path / "texto.doc"
    texto.write_bytes(b"solo texto plano")
    assert descargar.tipo_word(texto) is None


def test_bloques_docx_parrafos_tabla_tab_br_y_campos(docx):
    bloques = descargar._bloques_docx(docx)
    assert bloques[0] == {"texto": "LEY DE PRUEBA"}
    assert bloques[1] == {"texto": "Artículo 1. Primera línea\nSegunda"}      # tab -> espacio, br -> salto
    assert bloques[2] == {"texto": "3"}                                      # del campo PAGE solo su resultado
    assert bloques[3] == {"texto": ""}
    assert bloques[4] == {"filas": [[(0, 5000, ["Encabezado"])], [(0, 2000, ["a"]), (2000, 5000, ["b | c"])]]}
    assert bloques[5] == {"texto": "Fin"}


def test_texto_plano_docx_un_parrafo_por_renglon(docx):
    assert descargar.texto_plano(docx) == "LEY DE PRUEBA\nArtículo 1. Primera línea\nSegunda\n3\n\nEncabezado\na b | c\nFin"


def test_a_markdown_docx_omite_parrafos_vacios_y_arma_la_tabla(docx):
    assert descargar.a_markdown(docx) == (
        "LEY DE PRUEBA\n\nArtículo 1. Primera línea\nSegunda\n\n3\n\n"
        "| Encabezado |  |\n|---|---|\n| a | b \\| c |\n\nFin\n")


def test_tabla_sin_rejilla_usa_el_indice_de_columna(tmp_path):
    xml = (f'<w:document xmlns:w="{W}"><w:body><w:tbl>'
           '<w:tr><w:tc><w:p><w:r><w:t>x</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>y</w:t></w:r></w:p></w:tc></w:tr>'
           '</w:tbl></w:body></w:document>')
    ruta = _docx_xml(tmp_path / "t.docx", xml)
    assert descargar._bloques_docx(ruta) == [{"filas": [[(0, 1, ["x"]), (1, 2, ["y"])]]}]
    assert descargar.a_markdown(ruta) == "| x | y |\n|---|---|\n"


def test_error_word_si_no_es_word_o_esta_danado(tmp_path):
    pdf = tmp_path / "no.doc"
    pdf.write_bytes(b"%PDF-1.4 esto es un pdf")
    with pytest.raises(descargar.ErrorWord, match="no es un documento de Word"):
        descargar.texto_plano(pdf)
    with pytest.raises(descargar.ErrorWord, match=re.escape("no se pudo leer el .docx")):
        descargar.a_markdown(_docx_xml(tmp_path / "roto.docx", "<w:document>sin cerrar"))
    basura = tmp_path / "basura.doc"           # firma OLE y marca WordDocument, pero sin estructura
    basura.write_bytes(OLE + b"\x00" * 100 + "WordDocument".encode("utf-16-le") + b"\x00" * 100)
    assert descargar.tipo_word(basura) == "doc"
    with pytest.raises(descargar.ErrorWord, match=re.escape("no se pudo leer el .doc")):
        descargar.bloques(basura)


def test_doc_real_tipo_texto_y_markdown():
    ruta = _doc_real()
    assert descargar.tipo_word(ruta) == "doc"
    texto = descargar.texto_plano(ruta)
    assert len(texto) > 10_000
    assert "Baja California Sur" in texto
    md = descargar.a_markdown(ruta)
    assert "Artículo" in md or "ARTÍCULO" in md


# ====================================================================================================
# Pruebas de herramientas/descargar.py: validar PDF/Word/página, leer su texto y revisar nombre, entidad y fecha.
# ====================================================================================================
RELLENO = "Publicada en el Diario Oficial de la Federación. TEXTO VIGENTE. Última reforma publicada DOF 09-04-2026. "
CUERPO_LEY = ("ARTÍCULO 1o. Esta ley es de orden público. Las disposiciones del Código Fiscal de la Federación "
          "y de la Ley Orgánica del Poder Judicial de la Federación se aplicarán supletoriamente. ") * 3
OTROS = ["Ley del Impuesto sobre la Renta", "Constitución Política de los Estados Unidos Mexicanos",
         "Decreto de reforma a la Ley de Amparo"]


def _pdf(ruta, prefijo=b"", tam=12_000):
    ruta.write_bytes(prefijo + b"%PDF-1.4\n" + b"0" * tam + b"\n%%EOF\n")
    return ruta


def _docx_texto(ruta, texto):
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           f"<w:p><w:r><w:t>{texto}</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("word/document.xml", xml)
    return ruta


def test_es_pdf_acepta_la_firma_aunque_haya_basura_antes(tmp_path):
    assert descargar.es_pdf(_pdf(tmp_path / "a.pdf", prefijo=b"\x00" * 500)) == (True, "PDF válido")


def test_es_pdf_no_busca_la_firma_mas_alla_de_1024_bytes(tmp_path):
    ok, motivo = descargar.es_pdf(_pdf(tmp_path / "a.pdf", prefijo=b"\x00" * 1100))
    assert not ok and "no es PDF" in motivo


def test_es_pdf_rechaza_html_disfrazado_aunque_mencione_pdf(tmp_path):
    ruta = tmp_path / "a.pdf"
    ruta.write_bytes(b"<!DOCTYPE html><html><body>%PDF-1.4 error</body></html>" + b" " * 20_000)
    ok, motivo = descargar.es_pdf(ruta)
    assert not ok and motivo.startswith("HTML")


def test_es_pdf_tamano_minimo_y_archivo_inexistente(tmp_path):
    chico = _pdf(tmp_path / "chico.pdf", tam=100)
    ok, motivo = descargar.es_pdf(chico)
    assert not ok and "chico" in motivo
    assert descargar.es_pdf(chico, min_bytes=1) == (True, "PDF válido")
    assert descargar.es_pdf(tmp_path / "no.pdf") == (False, "el archivo no existe")


def test_quitar_envoltorio_pdf_recorta_el_html_pegado_antes(tmp_path):
    ruta = tmp_path / "nayarit.pdf"
    html = b"<html><body>portal</body></html>\n"
    ruta.write_bytes(html + b"%PDF-1.4\ncontenido\n%%EOF\nbasura final")
    assert descargar.quitar_envoltorio_pdf(ruta) == len(html)
    assert ruta.read_bytes() == b"%PDF-1.4\ncontenido\n%%EOF\n"


def test_quitar_envoltorio_pdf_sin_envoltorio_no_toca_el_archivo(tmp_path):
    ruta = _pdf(tmp_path / "sano.pdf")
    antes = ruta.read_bytes()
    assert descargar.quitar_envoltorio_pdf(ruta) == 0
    assert ruta.read_bytes() == antes
    sin_fin = tmp_path / "sin_eof.pdf"
    sin_fin.write_bytes(b"<html>x</html>%PDF-1.4 sin fin")
    assert descargar.quitar_envoltorio_pdf(sin_fin) == 0


def test_es_html_y_formato_archivo_por_contenido_no_por_extension(tmp_path):
    html = tmp_path / "pagina.pdf"
    html.write_bytes(b"<!DOCTYPE HTML><html><body>hola</body></html>")
    assert descargar.es_html(html) and descargar.formato_archivo(html) == "html"
    assert descargar.formato_archivo(_pdf(tmp_path / "a.bin", tam=5)) == "pdf"      # basta la firma
    assert descargar.formato_archivo(_docx_texto(tmp_path / "a.docx", "hola")) == "docx"
    otro = tmp_path / "otro.pdf"
    otro.write_bytes(b"\x00\x01\x02 nada que ver" * 10)
    assert not descargar.es_html(otro) and descargar.formato_archivo(otro) is None
    assert descargar.formato_archivo(tmp_path / "no_existe.pdf") is None
    assert descargar.texto_documento(otro) == ""                                     # no es nada: sin texto


def test_formato_archivo_reconoce_un_doc_real():
    assert descargar.formato_archivo(_doc_real()) == "doc"


# --- texto_documento / documento_valido ---------------------------------------------------------------

def test_texto_documento_lee_un_pdf_real_de_la_biblioteca():
    pdfs = sorted(DOCUMENTOS.rglob("*.pdf"), key=lambda p: p.stat().st_size) if DOCUMENTOS.is_dir() else []
    if not pdfs:
        pytest.skip("no hay PDF en la biblioteca")
    ruta = pdfs[0]
    texto = descargar.texto_documento(ruta)
    if not texto.strip():
        pytest.skip(f"{ruta.name} no tiene texto (escaneado)")
    assert descargar.documento_valido(ruta)[2] == "pdf"
    assert len(descargar.texto_documento(ruta, paginas=1)) <= len(descargar.texto_documento(ruta, paginas=0))


def test_una_pagina_web_se_reconoce_pero_no_se_lee_como_documento(tmp_path):
    ruta = tmp_path / "ley.html"
    ruta.write_text("<html><head><script>var x=1;</script></head><body><h1>Ley de Prueba</h1>"
                    "<p>Artículo 1.</p></body></html>", encoding="utf-8")
    assert descargar.formato_archivo(ruta) == "html"           # se reconoce para poder rechazarla
    assert descargar.texto_documento(ruta) == ""                # no es un documento de la biblioteca


def test_texto_documento_recorta_por_paginas_en_word(tmp_path):
    ruta = _docx_texto(tmp_path / "largo.docx", "x" * (2 * descargar.CARACTERES_POR_PAGINA))
    assert len(descargar.texto_documento(ruta, paginas=1)) == descargar.CARACTERES_POR_PAGINA
    assert len(descargar.texto_documento(ruta, paginas=0)) == 2 * descargar.CARACTERES_POR_PAGINA


def test_documento_valido_word_con_texto_y_casi_sin_texto(tmp_path):
    bueno = _docx_texto(tmp_path / "bueno.docx", "Artículo. " * 50)
    assert descargar.documento_valido(bueno) == (True, "Word oficial (.docx)", "docx")
    ok, motivo, formato = descargar.documento_valido(_docx_texto(tmp_path / "vacio.docx", "hola"))
    assert (ok, formato) == (False, "docx") and "casi sin texto" in motivo


def test_una_pagina_web_nunca_es_un_documento_valido(tmp_path):
    # Regla de la biblioteca: solo PDF o Word; una página web, aunque tenga el texto completo, no se guarda
    ruta = tmp_path / "ley.html"
    ruta.write_text("<html><body><p>" + "texto oficial. " * 300 + "</p></body></html>", encoding="utf-8")
    assert descargar.documento_valido(ruta) == (False, "es una página web: la biblioteca solo guarda PDF o Word", None)
    assert descargar._formato_oficial(ruta) is None              # no se guarda tal cual: se busca el PDF o Word


def test_documento_valido_pdf(tmp_path):
    assert descargar.documento_valido(_pdf(tmp_path / "a.pdf")) == (True, "PDF válido", "pdf")
    ok, _, formato = descargar.documento_valido(_pdf(tmp_path / "b.pdf", tam=10))
    assert (ok, formato) == (False, None)


# --- coincidencia_titulo / revisar_nombre / menciona_entidad -------------------------------------------

def test_coincidencia_titulo_solo_cuenta_la_portada_antes_del_articulo_1():
    texto = "LEY DE AMPARO, REGLAMENTARIA DE LOS ARTÍCULOS 103 Y 107 DE LA CONSTITUCIÓN. " + RELLENO + CUERPO_LEY
    assert descargar.coincidencia_titulo("Ley de Amparo", texto) == 1.0
    # el Código Fiscal solo se cita en el cuerpo: de sus 3 palabras clave la portada solo tiene 'federacion'
    assert descargar.coincidencia_titulo("Código Fiscal de la Federación", texto) == pytest.approx(1 / 3)


def test_coincidencia_titulo_none_sin_texto_o_sin_palabras_clave():
    assert descargar.coincidencia_titulo("Ley de Amparo", "LEY DE AMPARO") is None      # escaneado
    assert descargar.coincidencia_titulo("de la", RELLENO + CUERPO_LEY) is None


def test_coincidencia_titulo_no_corta_en_una_cita_del_preambulo_ni_en_portadas_cortas():
    texto = ("DECRETO por el que se reforma en su artículo 1 el Código de Comercio. " + RELLENO
             + "LEY GENERAL DE SOCIEDADES MERCANTILES. " + RELLENO + CUERPO_LEY)
    assert descargar.coincidencia_titulo("Ley General de Sociedades Mercantiles", texto) == 1.0
    # menos de 100 caracteres antes del artículo 1: se usa todo el texto
    assert descargar.coincidencia_titulo("Código Fiscal de la Federación", "LEY DE AMPARO. " + CUERPO_LEY) == 1.0


def test_revisar_nombre_acepta_el_nombre_completo_en_la_portada():
    texto = "LEY DEL IMPUESTO AL VALOR AGREGADO. " + RELLENO + CUERPO_LEY
    assert descargar.revisar_nombre("Ley del Impuesto al Valor Agregado", texto, OTROS) == (True, "")


def test_revisar_nombre_rechaza_la_ley_sustituta_y_la_cita_en_el_cuerpo():
    sustituta = "LEY DEL SECTOR HIDROCARBUROS. " + RELLENO + CUERPO_LEY + " Se abroga la Ley de Hidrocarburos."
    ok, motivo = descargar.revisar_nombre("Ley de Hidrocarburos", sustituta)
    assert ok is False and "NO aparece en la portada" in motivo
    ok, motivo = descargar.revisar_nombre("Código Fiscal de la Federación", "LEY ADUANERA. " + RELLENO + CUERPO_LEY)
    assert ok is False and "Código Fiscal de la Federación" in motivo


def test_revisar_nombre_rechaza_si_otro_documento_del_catalogo_aparece_antes():
    texto = "LEY DEL IMPUESTO SOBRE LA RENTA. " + RELLENO + "LEY DEL IMPUESTO AL VALOR AGREGADO. " + RELLENO + CUERPO_LEY
    ok, motivo = descargar.revisar_nombre("Ley del Impuesto al Valor Agregado", texto, OTROS)
    assert ok is False and "OTRO documento" in motivo and "Impuesto sobre la Renta" in motivo


def test_revisar_nombre_no_aplica_a_nombres_descriptivos_ni_a_pdf_sin_texto():
    assert descargar.revisar_nombre("Decreto de reforma a la Ley de Amparo", RELLENO + CUERPO_LEY) == (None, "")
    assert descargar.revisar_nombre("Ley de Amparo", "LEY DE AMPARO") == (None, "")


def test_revisar_nombre_rescata_con_se_expide_la_ley():
    texto = ("H. CONGRESO DEL ESTADO DE SONORA. DECRETO NÚMERO 45. " + RELLENO
             + "ARTÍCULO PRIMERO. Se expide la Ley de Salud del Estado de Sonora, para quedar como sigue: "
             "LEY DE SALUD. " + CUERPO_LEY)
    assert descargar.revisar_nombre("Ley de Salud del Estado de Sonora", texto) == (True, "")


def test_revisar_nombre_la_constitucion_no_cuenta_como_competidora():
    texto = ("Reglamentaria de los artículos 103 y 107 de la Constitución Política de los Estados Unidos "
             "Mexicanos. LEY DE AMPARO. " + RELLENO + CUERPO_LEY)
    assert descargar.revisar_nombre("Ley de Amparo", texto, OTROS) == (True, "")


def test_menciona_entidad_con_sus_variantes():
    cdmx = "Código Civil para el Distrito Federal. " + RELLENO + CUERPO_LEY
    assert descargar.menciona_entidad("Ciudad de México", cdmx) is True
    edomex = "Código Administrativo. Gaceta del Gobierno. Los mexiquenses... " + RELLENO + CUERPO_LEY
    assert descargar.menciona_entidad("Estado de México", edomex) is True
    chih = "Código Penal del Estado de Chihuahua. " + RELLENO + CUERPO_LEY
    assert descargar.menciona_entidad("Michoacán", chih) is False
    assert descargar.menciona_entidad("Chihuahua", chih) is True
    assert descargar.menciona_entidad("Jalisco", "Ley de Jalisco") is None


# --- fecha_de_pdf / FaltaDependencia -------------------------------------------------------------------

def test_fecha_de_pdf_con_texto_dado_no_lee_el_archivo(tmp_path):
    inexistente = tmp_path / "no_existe.pdf"
    assert descargar.fecha_de_pdf(inexistente, "TEXTO VIGENTE Última Reforma DOF 09-04-2026") == (2026, 4, 9)
    assert descargar.fecha_de_pdf(inexistente, "sin fechas") is None


def test_fecha_de_pdf_lee_doce_paginas_si_la_portada_remite_a_una_ficha(tmp_path, monkeypatch):
    leidas = []

    def falso_texto(ruta, paginas=3):
        leidas.append(paginas)
        return "Historial de cambios 20/12/2014 (No. 77) 15/03/2025 (No. 12)"

    monkeypatch.setattr(descargar, "texto_documento", falso_texto)
    fecha = descargar.fecha_de_pdf(tmp_path / "queretaro.pdf", "Ficha Genealógica. Historial de cambios")
    assert leidas == [12] and fecha == (2025, 3, 15)


def test_falta_dependencia_si_no_esta_pdfminer(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pdfminer.high_level", None)
    with pytest.raises(descargar.FaltaDependencia, match="pdfminer"):
        descargar.texto_documento(_pdf(tmp_path / "a.pdf"))


def test_falta_dependencia_si_word_no_puede_importar_su_paquete(tmp_path, monkeypatch):
    def sin_paquete(ruta):
        raise ImportError("No module named 'olefile'", name="olefile")

    monkeypatch.setattr(descargar, "texto_plano", sin_paquete)
    with pytest.raises(descargar.FaltaDependencia, match="olefile"):
        descargar.texto_documento(_docx_texto(tmp_path / "a.docx", "hola"))


def test_falta_dependencia_si_no_esta_defusedxml_para_docx(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "defusedxml", None)
    monkeypatch.setitem(sys.modules, "defusedxml.ElementTree", None)
    with pytest.raises(descargar.FaltaDependencia, match="defusedxml"):
        descargar.texto_documento(_docx_texto(tmp_path / "a.docx", "hola"))


def test_falta_dependencia_si_no_esta_olefile_para_doc(monkeypatch):
    ruta = _doc_real()
    monkeypatch.setitem(sys.modules, "olefile", None)
    with pytest.raises(descargar.FaltaDependencia, match="olefile"):
        descargar.texto_documento(ruta)



# ====================================================================================================
# Fechas indicadas con --reforma, alarmas de un archivo ya aceptado, lotes y abrogaciones
# ====================================================================================================
TEXTO_JALISCO = "Ley de Hidrocarburos del Estado de Jalisco. " + "Artículo 1. Texto de la ley. " * 20
TEXTO_OTRO = "Ley de Hidrocarburos del Estado de Colima. " + "Artículo 1. Texto de la ley. " * 20


def test_alarma_de_contenido_no_se_repite_para_un_archivo_ya_aceptado():
    def alarma(titulo=1.0, nombre_ok=True, entidad=None, texto=TEXTO_JALISCO, **k):
        opciones = {"forzar": False, "ya_aceptado": False, "url_ya_validada": False, **k}
        return descargar.alarma_de_contenido(titulo, nombre_ok, "el nombre completo NO aparece", entidad, texto,
                                             **opciones)
    assert alarma() is None
    assert "30%" in alarma(titulo=0.3)
    assert "NO aparece" in alarma(nombre_ok=False)
    assert "no menciona a 'Jalisco'" in alarma(entidad="Jalisco", texto=TEXTO_OTRO)
    assert alarma(entidad="Jalisco") is None
    # el mismo archivo que ya se aceptó (o --forzar): ninguna de las tres alarmas se repite
    for caso in ({"titulo": 0.3}, {"nombre_ok": False}, {"entidad": "Jalisco", "texto": TEXTO_OTRO}):
        assert alarma(**caso, ya_aceptado=True) is None
        assert alarma(**caso, forzar=True) is None
    # la confirmación por URL solo cubre la alarma del nombre, no la de título ni la de entidad
    assert alarma(nombre_ok=False, url_ya_validada=True) is None
    assert alarma(titulo=0.3, url_ya_validada=True) is not None


def test_linea_del_lote_con_fecha_opcional():
    assert descargar._linea_lote("ley https://x.mx/a.pdf") == ("ley", "https://x.mx/a.pdf", None)
    assert descargar._linea_lote("ley\thttps://x.mx/a.pdf 2026-03-15") == ("ley", "https://x.mx/a.pdf", "2026-03-15")
    assert descargar._linea_lote("ley https://x.mx/a.pdf 15-03-2026") == ("ley", "https://x.mx/a.pdf", "15-03-2026")
    # URLs con espacios (algunos portales): la fecha solo cuenta si es la última palabra
    assert descargar._linea_lote("ley https://x.mx/Ley de Salud.pdf") == ("ley", "https://x.mx/Ley de Salud.pdf", None)
    assert descargar._linea_lote("ley https://x.mx/Ley 2026.pdf 2026-01-02") == ("ley", "https://x.mx/Ley 2026.pdf",
                                                                               "2026-01-02")
    assert descargar._linea_lote("solo-el-id") is None


@pytest.fixture
def ya_guardado(tmp_path, monkeypatch):
    """Un documento ya guardado (sin fecha de reforma en su ficha) y un candidato idéntico, con catálogo y
    manifiesto en memoria."""
    doc = {"id": "lh", "nombre": "Ley de Hidrocarburos", "grupo": "Federal/Energia",
           "archivo": "Ley_de_Hidrocarburos", "estado": "vigente"}
    monkeypatch.setattr(catalogo, "DOCUMENTOS", tmp_path / "documentos")
    guardado = catalogo.ruta_pdf(doc, "pdf")
    guardado.parent.mkdir(parents=True)
    guardado.write_bytes(CUERPO_PDF[:5000])
    huella = comun.sha256_archivo(guardado)
    man = {"lh": {"archivo": catalogo.ruta_relativa_pdf(doc, "pdf"), "formato": "pdf", "sha256": huella,
                  "bytes": 5000, "url": "https://x.mx/ley.pdf", "fecha_reforma": None, "fecha_descarga": "2026-10-09",
                  "ultima_verificacion": "2026-10-09", "markdown": None}}
    cat = {"documentos": [doc]}
    for nombre, valor in {"cargar_catalogo": lambda: cat, "cargar_manifiesto": lambda: man,
                          "guardar_manifiesto": lambda m: None, "guardar_catalogo": lambda c: None,
                          "bitacora": lambda e: None}.items():
        monkeypatch.setattr(descargar, nombre, valor)
    candidato = tmp_path / "candidato.pdf"
    candidato.write_bytes(CUERPO_PDF[:5000])
    return man, candidato, huella


def test_reforma_corrige_la_fecha_de_un_documento_ya_guardado(ya_guardado):
    # Con --reforma, un documento ya guardado (resultado SIN_CAMBIO) también recibe la fecha
    man, candidato, huella = ya_guardado
    descargar._decidir_y_registrar(argparse.Namespace(forzar=False), "lh", candidato, "https://x.mx/ley.pdf",
                                   (2025, 3, 18), huella, 5000)
    assert man["lh"]["fecha_reforma"] == "2025-03-18"


def test_reforma_corrige_una_fecha_mal_leida(ya_guardado):
    # La fecha guardada estaba mal leída (p. ej. la publicación original en vez de la última reforma)
    man, candidato, huella = ya_guardado
    man["lh"]["fecha_reforma"] = "2014-07-03"
    descargar._decidir_y_registrar(argparse.Namespace(forzar=False), "lh", candidato, "https://x.mx/ley.pdf",
                                   (2026, 9, 28), huella, 5000)
    assert man["lh"]["fecha_reforma"] == "2026-09-28"


def test_la_abrogacion_no_es_la_ultima_reforma_pero_la_fe_de_erratas_si_cuenta(tmp_path):
    inexistente = tmp_path / "no_existe.pdf"
    # Portada de Diputados de una ley abrogada: la última reforma es la de 2016, no la abrogación de 2019
    abrogada = ("LEY FEDERAL DE EXTINCIÓN DE DOMINIO, REGLAMENTARIA DEL ARTÍCULO 22 DE LA CONSTITUCIÓN Nueva Ley "
                "publicada en el DOF 29-05-2009 Última reforma publicada DOF 12-01-2016 Ley Abrogada DOF 09-08-2019")
    assert descargar.fecha_de_pdf(inexistente, abrogada) == (2016, 1, 12)
    assert descargar.fecha_de_pdf(inexistente, "CÓDIGO FEDERAL DE PROCEDIMIENTOS CIVILES Última reforma publicada "
                                               "DOF 07-06-2021 Código Abrogado DOF 07-06-2023") == (2021, 6, 7)
    # Una fe de erratas posterior cambia el texto: su fecha cuenta (así una versión corregida sale ACTUALIZADO)
    erratas = ("CÓDIGO PENAL PARA EL ESTADO DE HIDALGO ÚLTIMA REFORMA PUBLICADA EN EL ALCANCE DOS DEL PERIÓDICO "
               "OFICIAL: 06 DE AGOSTO DE 2026. Código publicado en el Alcance al Periódico Oficial, el sábado 9 de "
               "junio de 1990. Fe de Erratas publicada en el P.O. 25 de septiembre de 2026.")
    assert descargar.fecha_de_pdf(inexistente, erratas) == (2026, 9, 25)
