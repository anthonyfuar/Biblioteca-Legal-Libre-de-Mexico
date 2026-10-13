"""
descargar.py — PROCESO 2, ACTUALIZAR: descarga segura (HTTP), lectura de páginas, de PDF y de Word oficiales, fecha de
última reforma, y las herramientas `buscar`, `descargar` y `word`.
"""
from __future__ import annotations

import argparse
import difflib
import email.utils
import html
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING

from herramientas.catalogo import buscar_documento, exigir_estructura, registrar_pista, ruta_pdf, ruta_relativa_pdf
from herramientas.comun import (
    _DETENER,
    ANTERIORES,
    CACHE,
    CACHE_VIGENCIA_SEG,
    ENTRADA,
    Analizador,
    asegurar_carpetas,
    bitacora,
    bloqueo,
    cargar_catalogo,
    cargar_manifiesto,
    correr_hijo,
    guardar_catalogo,
    guardar_manifiesto,
    hoy,
    json_tambien_al_final,
    normalizar,
    palabras_clave,
    reemplazar_archivo,
    relativa,
    salida,
    salir_con_error,
    sha256_archivo,
    sin_acentos,
    temporal,
)
from herramientas.comun import PALABRAS_VACIAS as _PALABRAS_VACIAS
from herramientas.comun import secuencia as _secuencia

# ====================================================================================================
# Descarga HTTP con `requests`: User-Agent de navegador, verificación TLS siempre (nunca se desactiva),
# reintentos con espera exponencial ante fallos pasajeros, tope de bytes y plazo total, y planes de respaldo
# (sin www., https://, curl del sistema) ante errores de certificado; solo http(s). También la lista de
# sitios que no son fuente oficial.
# ====================================================================================================
if TYPE_CHECKING:        # solo para las anotaciones: ver la nota sobre `requests` más abajo
    import requests

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT_CONEXION = 15       # segundos para establecer la conexión con el portal
TIMEOUT_HTTP = 120          # segundos sin recibir datos antes de abandonar una descarga
MAX_BYTES = 1 << 30         # tope de una descarga: 1 GiB (el PDF más grande de la biblioteca pesa 164 MB)
PLAZO_TOTAL = 30 * 60       # plazo total de una descarga en segundos (30 min), reintentos y respaldos incluidos
ESPERAS_REINTENTO = (2, 6, 18)   # segundos antes de cada reintento (hasta 3; exponencial x3; ±25 % de azar)
CODIGOS_REINTENTO = frozenset({429, 500, 502, 503, 504})   # respuestas pasajeras: se reintentan; otros 4xx no
TAM_LECTURA = 1 << 16       # bytes por lectura de la red; entre lecturas se revisan _DETENER, tope y plazo
BLOQUE_MB = 1               # cada ~1 MB se abre el archivo, se escribe y se cierra (ver _guardar_por_bloques)
_CABECERAS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9",
    # el documento tal cual lo sirve el portal (sin gzip): los bytes guardados son los oficiales y coinciden
    # con Content-Length, como con urllib
    "Accept-Encoding": "identity",
}
# `requests` se importa DENTRO de las funciones que lo usan (convención del proyecto para terceros): las
# pruebas lo importan sin el entorno y el Python del sistema puede no tenerlo; así el error, si falta, es el de
# instalar.

# Sitios que NO son fuente oficial: sirven para orientarse (fechas, nombres), pero su copia nunca se guarda.
# Si una ley no está en ninguna fuente oficial en línea, se reporta como no localizada.
FUENTES_NO_OFICIALES = ("itesm.mx", "ordenjuridico.gob.mx", "justia.com", "vlex.com.mx", "vlex.com",
                        "studocu.com", "scribd.com", "docplayer.es", "vialidades.com.mx",
                        # gobiernos de otros países y sitios privados que aparecían como pistas de tratados: un
                        # tratado se toma de SRE, DOF o del organismo internacional, no de otro gobierno
                        "pj.gob.pe", "transparenciafiscal.gob.sv", "mjusticia.gob.es", "cecap.com.pa")


def es_fuente_no_oficial(url: str) -> str | None:
    """El sitio no oficial de la URL (p. ej. 'itesm.mx'), o None si no está en la lista."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return next((d for d in FUENTES_NO_OFICIALES if host == d or host.endswith("." + d)), None)


def codificar_url(url: str) -> str:
    """Codifica acentos y espacios de la URL (p. ej. 'Código Civil.pdf' -> 'C%C3%B3digo%20Civil.pdf').
    Muchos portales estatales publican archivos con acentos en el nombre; sin esto la descarga falla.
    Los %xx que ya traiga se dejan intactos (es idempotente)."""
    partes = urllib.parse.urlsplit(url.strip())
    ruta = urllib.parse.quote(partes.path, safe="/%:@!$&'()*+,;=-._~")
    consulta = urllib.parse.quote(partes.query, safe="=&%:/?@!$'()*+,;-._~")
    return urllib.parse.urlunsplit((partes.scheme, partes.netloc, ruta, consulta, partes.fragment))


def _sin_www(url: str) -> str | None:
    partes = urllib.parse.urlsplit(url)
    if partes.hostname and partes.hostname.startswith("www."):
        return urllib.parse.urlunsplit(partes._replace(netloc=partes.netloc.replace("www.", "", 1)))
    return None


def _es_error_certificado(error: Exception) -> bool:
    import requests

    texto = str(error).upper()
    return isinstance(error, requests.exceptions.SSLError) or \
        "CERTIFICATE" in texto or "SSL" in texto or "HOSTNAME" in texto


def _texto_error(error: Exception) -> str:
    """'ConnectionError: Caused by NewConnectionError(...)' en vez de la parrafada de urllib3 con el pool y la URL."""
    texto = str(error)
    causa = texto.rfind("(Caused by ")
    if causa != -1:
        texto = texto[causa + 1:].removesuffix(")")      # sin el paréntesis del MaxRetryError exterior
    return f"{type(error).__name__}: {texto[:200]}"


def _tamano(n: int) -> str:
    for unidad, divisor in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= divisor:
            return f"{n / divisor:.1f} {unidad}".replace(".0 ", " ")
    return f"{n} bytes"


def _duracion(segundos: float) -> str:
    return f"{segundos / 60:.0f} min" if segundos >= 60 else f"{segundos:.0f} s"


def _retry_after(cabeceras) -> float | None:
    """Segundos que pide esperar la cabecera Retry-After (número o fecha HTTP), o None si no viene."""
    valor = (cabeceras.get("Retry-After") or "").strip()
    if not valor:
        return None
    if valor.isdigit():
        return float(valor)
    try:
        fecha = email.utils.parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        return None
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    return max(0.0, (fecha - datetime.now(timezone.utc)).total_seconds())


def _espera_reintento(n: int, pedida: float | None, vence: float) -> float | None:
    """Segundos a esperar antes del reintento n (0, 1, 2...): lo que pidió el servidor (Retry-After) o la
    espera exponencial con ±25 % de aleatoriedad. None si ya no quedan reintentos o no cabe en el plazo."""
    if n >= len(ESPERAS_REINTENTO):
        return None
    espera = pedida if pedida is not None else ESPERAS_REINTENTO[n] * random.uniform(0.75, 1.25)
    return espera if time.monotonic() + espera < vence else None


class _LimiteExcedido(Exception):
    """La descarga superó el tope de bytes o el plazo total: se cancela y se borra lo descargado."""


@dataclass(frozen=True)
class _Limites:
    max_bytes: int
    plazo_total: float
    vence: float                    # instante (time.monotonic) en que se agota el plazo total

    def restante(self) -> float:
        return self.vence - time.monotonic()

    def mensaje_plazo(self, recibidos: int = 0) -> str:
        return (f"se agotó el plazo total de {_duracion(self.plazo_total)} para la descarga"
                + (f" ({_tamano(recibidos)} recibidos)" if recibidos else ""))


@dataclass
class _Intento:
    """Resultado de UNA solicitud. Los campos extra dicen qué hacer después: repetirla (`reintentable`,
    tras `espera` segundos si el servidor lo pidió), probar los respaldos de certificado (`certificado`)
    o parar del todo (`definitivo`: tope, plazo, disco o interrupción; ninguna otra URL lo arreglaría)."""
    ok: bool
    error: str = ""
    url_final: str = ""
    http: int | None = None
    tipo: str = ""
    bytes: int = 0
    reintentable: bool = False
    certificado: bool = False
    definitivo: bool = False
    espera: float | None = None


def http_descargar(url: str, destino: Path, max_bytes: int = MAX_BYTES, plazo_total: float = PLAZO_TOTAL) -> dict:
    """Igual que _http_descargar, pero la descarga corre en un hilo aparte: así Ctrl+C detiene la
    herramienta al instante (y se borran sus temporales) aunque el servidor se haya quedado callado.
    `requests` no lo hace innecesario: en Windows un recv bloqueado no se interrumpe hasta que vence su
    timeout (TIMEOUT_HTTP segundos); el hilo principal, en cambio, solo espera a un Event y sí responde."""
    resultado: dict = {}
    fallo: list[BaseException] = []
    listo = threading.Event()

    def trabajo():
        try:
            resultado.update(_http_descargar(url, destino, max_bytes, plazo_total))
        except BaseException as e:  # noqa: BLE001 - no se oculta: se relanza en el hilo principal
            fallo.append(e)
        finally:
            listo.set()

    threading.Thread(target=trabajo, daemon=True).start()
    try:
        while not listo.wait(0.25):
            pass
    except KeyboardInterrupt:
        _DETENER.set()
        listo.wait(0.5)            # si el hilo está entre bloques, cierra y borra el archivo parcial
        print("\nInterrumpido por el usuario.")
        sys.exit(130)
    if fallo:
        raise fallo[0]
    return resultado


def _http_descargar(url: str, destino: Path, max_bytes: int = MAX_BYTES, plazo_total: float = PLAZO_TOTAL) -> dict:
    """Descarga `url` a `destino` con `requests` (TLS verificado con certifi) probando, en orden:
      1. la URL tal cual, con User-Agent de navegador y hasta 3 reintentos (2, 6 y 18 s, con algo de
         azar; o lo que pida Retry-After) ante 429, 500-504, errores de conexión, timeouts y cortes;
      2. si falló el certificado y el dominio empieza con 'www.': la misma URL sin 'www.';
      3. si la URL es http:// y el portal no respondió bien (conexión, timeout, 5xx, 429 agotado): la
         versión https:// (un 4xx es una respuesta definitiva de ese servidor y no se repite por https);
      4. si hubo un error de certificado/SSL: el 'curl' del sistema (que usa el almacén del SO).
    Los respaldos 2 y 3 se prueban una sola vez (la URL original ya reintentó). Un 4xx distinto de 429
    no se reintenta. Límites: `max_bytes` (se rechaza antes de descargar si Content-Length lo supera y
    se cancela a mitad si el cuerpo lo supera) y `plazo_total` (segundos para toda la descarga, con sus
    reintentos y respaldos); al superarlos ok=False y el archivo parcial se borra.
    NUNCA desactiva la verificación de certificados.
    Devuelve un dict con ok, url_final, http, tipo, bytes, intentos y error."""
    import requests

    intentos: list[str] = []
    esquema = urllib.parse.urlsplit(url.strip()).scheme.lower()
    if esquema not in ("http", "https"):     # nunca file://, data:, ftp://: solo portales en la web
        return _fallo(url, intentos,
                      f"solo se descargan URLs http:// o https:// (esta es '{esquema or 'sin esquema'}:')")
    url = codificar_url(url)
    limites = _Limites(max_bytes, plazo_total, time.monotonic() + plazo_total)
    variantes = [url]
    hubo_certificado = False
    ultimo_error = ""
    with requests.Session() as sesion:
        sesion.headers.update(_CABECERAS)
        i = 0
        while i < len(variantes):
            actual = variantes[i]
            i += 1
            r = _con_reintentos(sesion, actual, destino, limites, intentos, reintentar=actual == url)
            if r.ok:
                return {"ok": True, "url_final": r.url_final, "http": r.http, "tipo": r.tipo,
                        "bytes": r.bytes, "intentos": intentos, "error": ""}
            ultimo_error = r.error
            if r.definitivo:
                return _fallo(url, intentos, ultimo_error)
            if r.certificado:
                hubo_certificado = True
                alterna = _sin_www(actual)
                if alterna and alterna not in variantes:
                    variantes.insert(i, alterna)       # antes que la versión https://
            es_4xx = r.http is not None and 400 <= r.http < 500 and r.http not in CODIGOS_REINTENTO
            if actual.startswith("http://") and not es_4xx:
                segura = "https://" + actual[len("http://"):]
                if segura not in variantes:
                    variantes.append(segura)
    # Último respaldo: el programa 'curl' del sistema. En Windows y macOS usa el almacén de
    # certificados del sistema operativo, que sí completa cadenas de certificados que algunos
    # portales de gobierno envían incompletas. Sigue verificando certificados (sin --insecure).
    if hubo_certificado and shutil.which("curl") and limites.restante() > 0:
        con_curl = _descargar_con_curl(url, destino, max_bytes, min(600, limites.restante()))
        intentos.append(f"{url} -> curl del sistema: {con_curl['error'] or 'HTTP ' + str(con_curl['http'])}")
        if con_curl["ok"]:
            con_curl["intentos"] = intentos
            return con_curl
        ultimo_error = con_curl["error"] or ultimo_error
    return _fallo(url, intentos, ultimo_error)


def _fallo(url: str, intentos: list[str], error: str) -> dict:
    return {"ok": False, "url_final": url, "http": None, "tipo": "", "bytes": 0, "intentos": intentos, "error": error}


def _con_reintentos(sesion: requests.Session, url: str, destino: Path, limites: _Limites,
                    intentos: list[str], reintentar: bool) -> _Intento:
    """Hace la solicitud y, si `reintentar` y el fallo fue pasajero, la repite tras la espera que toque.
    Cada solicitud deja su línea en `intentos` ('url -> HTTP 200' o 'url -> error')."""
    n = 0
    while True:
        r = _una_solicitud(sesion, url, destino, limites)
        linea = f"{url} -> " + (f"HTTP {r.http}" if r.ok else r.error)
        espera = None
        if not r.ok and reintentar and r.reintentable:
            espera = _espera_reintento(n, r.espera, limites.vence)
            if espera is not None:
                linea += f"; reintento en {espera:.1f} s"
            elif r.espera is not None and n < len(ESPERAS_REINTENTO):
                linea += f"; el servidor pide esperar {_duracion(r.espera)}, más de lo que queda del plazo"
        intentos.append(linea)
        if r.ok or espera is None:
            return r
        if _DETENER.wait(espera):
            return _Intento(False, error="descarga detenida: la herramienta está terminando", definitivo=True)
        n += 1


def _una_solicitud(sesion: requests.Session, url: str, destino: Path, limites: _Limites) -> _Intento:
    import requests

    restante = limites.restante()
    if restante <= 0:
        return _Intento(False, error=limites.mensaje_plazo(), definitivo=True)
    timeout = (min(TIMEOUT_CONEXION, restante), min(TIMEOUT_HTTP, restante))
    try:
        with sesion.get(url, stream=True, allow_redirects=True, timeout=timeout, verify=True) as r:
            if not 200 <= r.status_code < 300:
                return _Intento(False, error=f"HTTP {r.status_code} {r.reason or ''}".rstrip(),
                                url_final=r.url, http=r.status_code,
                                reintentable=r.status_code in CODIGOS_REINTENTO, espera=_retry_after(r.headers))
            anunciado = (r.headers.get("Content-Length") or "").strip()
            if anunciado.isdigit() and int(anunciado) > limites.max_bytes:
                return _Intento(False, definitivo=True, http=r.status_code, url_final=r.url,
                                error=f"el servidor anuncia {_tamano(int(anunciado))} y el tope de descarga "
                                      f"es {_tamano(limites.max_bytes)}: no se descargó")
            destino.parent.mkdir(parents=True, exist_ok=True)
            total = _guardar_por_bloques(r, destino, limites)
            return _Intento(True, url_final=r.url, http=r.status_code,
                            tipo=r.headers.get("Content-Type", ""), bytes=total)
    except requests.exceptions.SSLError as e:        # certificado: no se repite; se prueban los respaldos
        return _Intento(False, error=_texto_error(e), certificado=True)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
            requests.exceptions.ChunkedEncodingError) as e:          # fallo de red pasajero: se reintenta
        return _Intento(False, error=_texto_error(e), reintentable=True, certificado=_es_error_certificado(e))
    except requests.RequestException as e:   # URL inválida, demasiadas redirecciones...: repetirla no ayuda
        return _Intento(False, error=_texto_error(e), certificado=_es_error_certificado(e))
    except _LimiteExcedido as e:
        return _Intento(False, error=str(e), definitivo=True)
    except InterruptedError as e:
        return _Intento(False, error=str(e), definitivo=True)
    except OSError as e:                     # disco lleno, sin permisos: no depende de la red
        return _Intento(False, error=_texto_error(e), definitivo=True)


def _guardar_por_bloques(respuesta: requests.Response, destino: Path, limites: _Limites,
                         bloque_mb: int = BLOQUE_MB) -> int:
    """Escribe la descarga en bloques de ~1 MB abriendo y cerrando el archivo cada vez: mientras se
    espera a la red no queda ningún archivo abierto, así que si la herramienta se interrumpe su
    carpeta temporal SÍ se puede borrar (en Windows un archivo abierto no se borra). Entre lecturas
    revisa _DETENER, el tope de bytes y el plazo; si la descarga no termina, borra el archivo parcial."""
    destino.write_bytes(b"")
    pendiente, en_memoria, total, completo = [], 0, 0, False
    try:
        for bloque in respuesta.iter_content(chunk_size=TAM_LECTURA):
            if _DETENER.is_set():
                raise InterruptedError("descarga detenida: la herramienta está terminando")
            total += len(bloque)
            if total > limites.max_bytes:
                raise _LimiteExcedido(f"la descarga supera el tope de {_tamano(limites.max_bytes)} "
                                      "(el servidor no anunció el tamaño o anunció uno menor): se canceló")
            if limites.restante() <= 0:
                raise _LimiteExcedido(limites.mensaje_plazo(total))
            pendiente.append(bloque)
            en_memoria += len(bloque)
            if en_memoria >= bloque_mb << 20:
                with open(destino, "ab") as f:
                    f.writelines(pendiente)
                pendiente, en_memoria = [], 0
        if pendiente:
            with open(destino, "ab") as f:
                f.writelines(pendiente)
        completo = True
    finally:
        if not completo:
            destino.unlink(missing_ok=True)
    return total


def _descargar_con_curl(url: str, destino: Path, max_bytes: int = MAX_BYTES, max_time: float = 600) -> dict:
    destino.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-L", "-sS", "-A", USER_AGENT, "--max-time", str(int(max_time)), "--max-filesize", str(max_bytes),
           "--proto", "=http,https", "--proto-redir", "=http,https",
           "-o", str(destino), "-w", "%{http_code}|%{url_effective}|%{content_type}", codificar_url(url)]
    try:
        r = correr_hijo(cmd, timeout=max_time + 60)       # registrado: si la herramienta termina, curl también
    except (OSError, subprocess.TimeoutExpired, InterruptedError) as e:
        destino.unlink(missing_ok=True)
        return {"ok": False, "http": None, "error": f"curl: {e}"}
    partes = (r.stdout or "").split("|")
    codigo = int(partes[0]) if partes and partes[0].isdigit() else None
    if r.returncode != 0 or not codigo or codigo >= 400:
        destino.unlink(missing_ok=True)
        return {"ok": False, "http": codigo, "error": f"curl: {(r.stderr or '').strip()[:160] or 'HTTP ' + str(codigo)}"}
    return {"ok": True, "url_final": partes[1] if len(partes) > 1 else url, "http": codigo,
            "tipo": partes[2] if len(partes) > 2 else "", "bytes": destino.stat().st_size, "error": ""}


# ====================================================================================================
# Páginas web: leerlas con la codificación correcta, sacar su texto visible y listar sus enlaces a documentos
# (aunque el nombre esté en otra celda de la tabla, en un botón onclick o en un visor incrustado).
# ====================================================================================================
class _LectorEnlaces(HTMLParser):
    """Junta todos los <a href> de una página con su texto visible.

    Muchos portales ponen el nombre de la ley en una celda de la tabla y el enlace
    de descarga en otra celda, solo con un ícono. Por eso, si el enlace no tiene
    texto útil, se usa el texto de su FILA: <tr>, <li> o un <div> que hace de fila
    (class="row..." o id="row..."; así lo hace Colima). Esa fila además suele traer la
    fecha de publicación y de última reforma."""

    CONTENEDORES = ("tr", "li")
    _DIV_FILA = re.compile(r"(?:^|[\s_-])row(?:$|[\s_-])", re.I)
    MAX_CONTEXTO = 300       # una "fila" más larga es una sección entera: no sirve como nombre

    _URL_EN_ONCLICK = re.compile(r"""['"]([^'"]+?\.pdf[^'"]*)['"]""", re.I)

    def __init__(self):
        super().__init__()
        self.enlaces = []
        self._href = None
        self._texto = []
        self._pila = []          # contenedores abiertos: [texto_acumulado, índices_de_enlaces]
        self._divs = []          # por cada <div> abierto: ¿es una fila?
        self._ignorar = 0        # dentro de <style>/<script> no hay texto visible

    def _es_div_fila(self, attrs) -> bool:
        a = dict(attrs)
        return bool(self._DIV_FILA.search(a.get("class") or "")) or (a.get("id") or "").lower().startswith("row")

    def _agregar_enlace(self, url: str) -> None:
        """Enlace sin <a> (visor embebido o botón con onclick): cuenta como uno de la fila."""
        self.enlaces.append([url, ""])
        if self._pila:
            self._pila[-1][1].append(len(self.enlaces) - 1)

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._ignorar += 1
        if tag == "div":
            fila = self._es_div_fila(attrs)
            self._divs.append(fila)
            if fila:
                self._pila.append([[], []])
        if tag in self.CONTENEDORES:
            self._pila.append([[], []])
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._texto = []
        a = dict(attrs)
        if tag in ("iframe", "embed", "object"):     # visores que incrustan el PDF
            incrustado = a.get("src") or a.get("data") or ""
            if ".pdf" in incrustado.lower():
                self._agregar_enlace(incrustado)
        elif tag != "a" and a.get("onclick"):          # botones: onclick="goToUrl('...pdf')"
            m = self._URL_EN_ONCLICK.search(a["onclick"])
            if m:
                self._agregar_enlace(m.group(1))

    def handle_data(self, data):
        if self._ignorar:
            return
        if self._href is not None:
            self._texto.append(data)
        for cont in self._pila:
            cont[0].append(data)

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self._ignorar:
            self._ignorar -= 1
        if tag == "a" and self._href:
            self.enlaces.append([self._href, " ".join("".join(self._texto).split())])
            if self._pila:
                self._pila[-1][1].append(len(self.enlaces) - 1)
            self._href = None
        elif (tag in self.CONTENEDORES or (tag == "div" and self._divs and self._divs.pop())) and self._pila:
            texto_cont, indices = self._pila.pop()
            contexto = " ".join(" ".join(texto_cont).split())
            if len(contexto) > self.MAX_CONTEXTO:
                contexto = ""
            for i in indices:
                propio = self.enlaces[i][1]
                if len(propio) < 12 and contexto:          # ícono o 'Descargar' -> usar la fila
                    self.enlaces[i][1] = f"{contexto} [{propio}]" if propio else contexto
                elif self._pila:                           # sin contexto útil: que decida la fila de afuera
                    self._pila[-1][1].append(i)


def enlaces_de_html(html: str, url_base: str) -> list[tuple[str, str]]:
    """Lista (url_absoluta, texto) de todos los enlaces de una página."""
    lector = _LectorEnlaces()
    try:
        lector.feed(html)
    except Exception:  # noqa: BLE001 - HTML roto: se usa lo que se alcanzó a leer
        pass
    vistos, salida = set(), []
    for href, texto in lector.enlaces:
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        absoluta = urllib.parse.urljoin(url_base, href.strip())
        if absoluta not in vistos:
            vistos.add(absoluta)
            salida.append((absoluta, texto))
    return salida


def parece_enlace_a_documento(url: str, texto: str) -> bool:
    u = url.lower()
    return (u.split("?")[0].endswith((".pdf", ".doc", ".docx")) or "pdf" in u or "descargar" in u
            or "download" in u or "documento" in u or "pdf" in texto.lower())


def es_word(url: str) -> bool:
    return url.lower().split("?")[0].endswith((".doc", ".docx"))


def leer_html(ruta: Path) -> str:
    datos = ruta.read_bytes()
    for codificacion in ("utf-8", "latin-1"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("utf-8", errors="replace")


# ====================================================================================================
# Fechas: lectura de la fecha de "última reforma" en la portada de un documento (funciones puras).
#
# Recibe texto y devuelve fechas; no lee archivos ni importa comun.py (comun importa de aquí). La
# lectura del archivo (texto_documento, fecha_de_pdf) vive en comun.py.
#
# API:
#   fechas_en(fragmento)          -> todas las fechas (año, mes, día) que hay en un fragmento
#   fecha_reforma(texto)          -> la fecha de la última reforma de la portada, o None
#   necesita_mas_paginas(texto)   -> True si la fecha puede estar más allá de las primeras páginas
#                                    (Ficha Genealógica de Querétaro, tabla de reformas de Puebla)
#   formato_fecha(fecha)          -> "AAAA-MM-DD" o "no encontrada"
#   leer_fecha(texto)             -> (año, mes, día) desde "AAAA-MM-DD" o "DD-MM-AAAA", o None
#
# Cómo decide fecha_reforma (de lo más confiable a lo menos; el primer nivel que da algo gana):
#   1. Rótulos de reforma: "última reforma", "última enmienda" (Nayarit, a veces "ENMIEDA"),
#      "reformada por última vez", "nueva reforma". Se toman las fechas de los 220 caracteres que
#      siguen y gana la MÁS RECIENTE (la publicación es posterior a la fecha del decreto).
#   2. Rótulos de actualización: "última actualización" (Veracruz, Aguascalientes), "última
#      modificación" (Guerrero, Campeche), "actualizado/a al"; y el encabezado de Guanajuato
#      "Reforma: P.O. ...; DD-MM-AAAA" (variantes "Reforma Publicada:", "Reformada", "Reforma P.O.").
#      Son de segundo nivel porque donde conviven con "última reforma" pueden traer otra fecha (la de
#      compilación): no deben ganarle al rótulo de reforma.
#   3. Querétaro: la fecha más reciente del "Historial de cambios" de la Ficha Genealógica.
#   4. Puebla (Orden Jurídico Poblano): tabla "REFORMAS / Publicación / Extracto del texto" con una
#      fila "dd/mmm/aaaa" por reforma, en orden cronológico, que termina justo antes del índice
#      ("CONTENIDO"). Solo se acepta si el índice está en el texto leído (la tabla está completa);
#      si no, es mejor no dar fecha que dar una vieja.
#   5. Ley federal sin reformas: "Nueva Ley DOF DD-MM-AAAA" (solo ese formato exacto).
#
# Siempre: las fechas posteriores a hoy se descartan (no son reformas: "ejercicio fiscal... 2027",
# "abrogado en un plazo que no exceda del 01/04/2027"); "Texto vigente a partir del <fecha>" es
# entrada en vigor, no reforma, y corta la ventana; y si entre el rótulo y la primera fecha aparece
# el rótulo de OTRA columna ("fe de erratas", "entrada en vigor"...), las fechas van en columnas y
# la primera es la de la última reforma (p. ej. copias de la CNDH).
# ====================================================================================================
Fecha = tuple[int, int, int]   # (año, mes, día)

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
         "noviembre": 11, "diciembre": 12}
MES_ABREV = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
             "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

# "09-04-2026", "09/04/2026", "09.04.2026"
FECHA_NUM = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})")
# "15 de marzo de 2025", "17 MAYO 2025", "1o. de enero del 2024", "1º de octubre de 2025",
# "03 de septiembre del año 2026" (el mes se valida con MESES, tolerando una errata: ver _mes)
FECHA_TXT = re.compile(r"(\d{1,2})\s*(?:o|ro|º|°)?\.?\s+(?:de\s+)?([a-z]+)\s+(?:del?\s+)?(?:ano\s+(?:de\s+)?)?(\d{4})")
# Baja California: "Última Reforma P.O. No. 50, Sección II, 04-Sep-2026" (mes abreviado entre guiones);
# Campeche: "05/diciembre/2023" (mes completo entre diagonales: "dic" + letras)
FECHA_ABREV = re.compile(r"(\d{1,2})\s*[-/.\s]\s*(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)[a-z]*\.?\s*[-/.\s]\s*(\d{4})")  # noqa: E501
# Chihuahua: "Última Reforma POE 2024.08.24" (año primero, con puntos)
FECHA_AMD = re.compile(r"(?<!\d)(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})(?!\d)")
_TODAS = (FECHA_NUM, FECHA_TXT, FECHA_ABREV, FECHA_AMD)
# PDFs con el espacio comido: "10 DE ABRILDE 2026"
_MES_PEGADO = re.compile(r"\b(" + "|".join(MESES) + r")de\b")

# Nivel 1: rótulos de reforma ("enmien?da": Nayarit escribe "ÚLTIMA ENMIENDA" y a veces "ENMIEDA")
ROTULO_REFORMA = re.compile(r"ultim[ao]s?\s+(?:reforma|enmien?da)|reformad[ao]\s+por\s+ultima\s+vez|nueva\s+reforma")
# Nivel 2: rótulos de actualización (Veracruz "Última Actualización 8 DE OCTUBRE DE 2025", Guerrero
# "ÚLTIMAS MODIFICACIONES PUBLICADAS EN EL PERIÓDICO OFICIAL ... 23 DE DICIEMBRE DE 2025" y "ÚLTIMAS
# ADICIONES PUBLICADAS ...", Campeche "última modificación publicada en P.O.E. 05/diciembre/2023")
ROTULO_ACTUALIZACION = re.compile(r"ultim[ao]s?\s+(?:actualizacion|modificaci(?:on|ones)|adicion(?:es)?)|actualizad[ao]s?\s+(?:al|a\s+la|hasta\s+el)\b")  # noqa: E501
# Nivel 2: encabezado de Guanajuato, al inicio de una línea: "Reforma: P.O. Núm. 252; Tercera Parte;
# DL 113; 18-12-2025", "Reforma Publicada: P.O. ...", "Reformada P.O. No. 107; 3ª. Parte; 29-05-2026",
# "Reforma P.O. Número 187, Sexta Parte, 17-09-2024". Las notas del cuerpo "(REFORMADA, P.O. 3 DE JUNIO
# DE 2011)" y "Artículo reformado P.O. 18-09-2018" no empiezan así. Solo en la zona de encabezado
# (ZONA_ENCABEZADO): Tabasco lista sus reformas con líneas parecidas, una por reforma, y una lista
# cortada por el límite de páginas daría una fecha vieja.
ROTULO_REFORMA_PO = re.compile(r"(?m)^[ \t]*reforma(?:da)?(?:\s+publicada)?\s*:?\s*(?:anexo\s+al\s+)?p\.?\s*o\b[^\n]{0,120}")
ZONA_ENCABEZADO = 3000
VENTANA = 220
# Rótulos de OTRAS columnas: si aparecen entre el rótulo y la primera fecha, las fechas van en
# columnas y la primera es la de la última reforma (p. ej. copias de la CNDH)
OTROS_ROTULOS = re.compile(r"fe de erratas|abrogaci|entrada en vigor|aclaraci|fecha de publicaci")
# La abrogación no es una reforma: en "Última reforma DOF 12-01-2016 Ley Abrogada DOF 09-08-2019" la última
# reforma es la de 2016. (Una fe de erratas posterior SÍ cuenta: cambia el texto, y su fecha es la que permite
# detectar como ACTUALIZADO una versión corregida.)
ROTULO_ABROGACION = re.compile(r"abrogaci|abrogad[ao]")
# Entrada en vigor, no reforma (Zacatecas: "TEXTO VIGENTE A PARTIR DEL 24 DE JULIO DE 1986"): lo que
# sigue a esta frase no cuenta
ROTULO_IGNORADO = re.compile(r"(?:texto\s+)?vigente\s+a\s+partir")

# Querétaro: tabla "Historial de cambios" de su Ficha Genealógica, con fechas "20/12/2014 (No. 77)"
FICHA = re.compile(r"ficha genealogica|historial de cambios")
FECHA_FICHA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*\(\s*no\.?\s*\d+")

# Puebla (Orden Jurídico Poblano): tabla de reformas con fechas "30/abr/1985" que termina justo antes
# del índice ("CONTENIDO" en su propia línea, seguido de líneas con puntos de relleno)
PUEBLA = re.compile(r"orden juridico poblano")
PUEBLA_TABLA = re.compile(r"extracto del texto")
PUEBLA_FIN_TABLA = re.compile(r"\n[ \t]*contenido[ \t]*\n[\s\S]{0,400}?\.{6,}")
FECHA_PUEBLA = re.compile(r"(?<!\d)(\d{1,2})/(ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)/(\d{4})(?!\d)")

# Ley federal que aún no tiene reformas: la portada de Diputados dice "Nueva Ley DOF 18-03-2025" (si
# tuviera reformas diría también "Última reforma"). Solo se acepta ese formato exacto: en portadas
# estatales la fecha de publicación original podría esconder una reforma.
NUEVA_LEY_DOF = re.compile(r"nuev[ao]\s+(?:ley|codigo|reglamento)\s+dof\s+(\d{1,2})-(\d{1,2})-(\d{4})")


def _valida(a: int, m: int, d: int) -> bool:
    return 1 <= d <= 31 and 1 <= m <= 12 and 1900 <= a <= 2100


def _mes(palabra: str) -> int | None:
    """Número del mes a partir de su nombre, tolerando una errata ("julo", "diciemre", "septiembe"): la
    palabra va entre "<día> de" y "de <año>", donde solo puede haber un mes. Sin errata válida: None."""
    if palabra in MESES:
        return MESES[palabra]
    if len(palabra) < 4:
        return None
    parecido = difflib.get_close_matches(palabra, MESES, n=1, cutoff=0.8)
    return MESES[parecido[0]] if parecido else None


def fechas_en(fragmento: str) -> list[Fecha]:
    """Todas las fechas (año, mes, día) de un fragmento ya en minúsculas y sin acentos, en todos los
    formatos que aparecen en las portadas. No valida que el día exista en ese mes (31-02 pasa)."""
    fechas: list[Fecha] = []
    fragmento = _MES_PEGADO.sub(r"\1 de", fragmento or "")
    for d, m, a in FECHA_NUM.findall(fragmento):
        if _valida(int(a), int(m), int(d)):
            fechas.append((int(a), int(m), int(d)))
    for d, mes, a in FECHA_TXT.findall(fragmento):
        numero = _mes(mes)
        if numero and 1 <= int(d) <= 31:
            fechas.append((int(a), numero, int(d)))
    for a, m, d in FECHA_AMD.findall(fragmento):
        if _valida(int(a), int(m), int(d)):
            fechas.append((int(a), int(m), int(d)))
    for d, mes, a in FECHA_ABREV.findall(fragmento):
        if 1 <= int(d) <= 31 and 1900 <= int(a) <= 2100:
            fechas.append((int(a), MES_ABREV[mes], int(d)))
    return fechas


def _hoy() -> Fecha:
    h = date.today()
    return h.year, h.month, h.day


def _sin_futuras(fechas: list[Fecha]) -> list[Fecha]:
    """Una reforma no puede ser posterior a hoy: "ejercicio fiscal del 1 de enero al 31 de diciembre de
    2027" o "abrogado en un plazo que no exceda del 01/04/2027" no son fechas de reforma."""
    hoy = _hoy()
    return [f for f in fechas if f <= hoy]


def _tras_rotulo(base: str, rotulo: re.Pattern, otro_rotulo: re.Pattern | None = None) -> list[Fecha]:
    """Fechas que siguen a cada aparición de un rótulo en `base` (dentro de VENTANA caracteres).
    `otro_rotulo` (p. ej. "última actualización" tras "última reforma") cierra la ventana: las fechas
    que lo siguen son suyas, no del rótulo buscado; pero si aparece ANTES de la primera fecha, los
    rótulos van en columnas y la primera fecha es la del rótulo buscado."""
    candidatas: list[Fecha] = []
    for m in rotulo.finditer(base):
        ventana = base[m.end(): m.end() + VENTANA]
        corte = ROTULO_IGNORADO.search(ventana)
        if corte:
            ventana = ventana[: corte.start()]
        primera = min((mm.start() for rx in _TODAS for mm in rx.finditer(ventana)), default=0)
        columnas = bool(OTROS_ROTULOS.search(ventana[:primera]))
        abrogacion = None if columnas else ROTULO_ABROGACION.search(ventana, primera)
        if abrogacion:            # tras la fecha de la última reforma, la de la abrogación: no es una reforma
            ventana = ventana[: abrogacion.start()]
        otro = otro_rotulo.search(ventana) if otro_rotulo else None
        if otro and otro.start() > primera:
            ventana = ventana[: otro.start()]
        elif otro:
            columnas = True
        fechas = fechas_en(ventana)
        if not fechas:
            continue
        if columnas:
            primera_fecha = fechas_en(ventana[primera: primera + 40])
            candidatas += primera_fecha[:1] or fechas
        else:
            candidatas += fechas
    return candidatas


def _fecha_ficha(plano_num: str) -> Fecha | None:
    """Querétaro: la fecha más reciente de la tabla "Historial de cambios" de la Ficha Genealógica."""
    if not FICHA.search(plano_num):
        return None
    fichas = [(int(a), int(me), int(d)) for d, me, a in FECHA_FICHA.findall(plano_num)
              if _valida(int(a), int(me), int(d))]
    return max(fichas) if fichas else None


def _tabla_puebla(plano_num: str) -> tuple[bool, bool, Fecha | None]:
    """(es tabla de Puebla, tabla completa, fecha más reciente). La tabla va en orden cronológico y
    termina antes del índice "CONTENIDO": si el índice no está en el texto leído, la tabla sigue en
    páginas que no se leyeron y la fecha sería vieja; en ese caso no se da ninguna."""
    if not (PUEBLA.search(plano_num) and PUEBLA_TABLA.search(plano_num)):
        return False, False, None
    fin = PUEBLA_FIN_TABLA.search(plano_num)
    if not fin:
        return True, False, None
    region = plano_num[: fin.start()]
    fechas = _sin_futuras([(int(a), MES_ABREV[mes], int(d)) for d, mes, a in FECHA_PUEBLA.findall(region)
                           if 1 <= int(d) <= 31 and 1900 <= int(a) <= 2100])
    return True, True, (max(fechas) if fechas else None)


def fecha_reforma(texto: str) -> Fecha | None:
    """Busca la fecha de 'última reforma' en la portada (texto crudo: aquí se normaliza). Entiende
    formatos federales ('Última Reforma DOF 09-04-2026') y estatales ('última reforma publicada en el
    Periódico Oficial el 15 de marzo de 2025', 'Última Actualización 8 DE OCTUBRE DE 2025', el
    encabezado de Guanajuato, la Ficha Genealógica de Querétaro y la tabla de Puebla). Devuelve
    (año, mes, día) o None. Los niveles y sus motivos están en la cabecera del módulo."""
    plano = normalizar(texto)
    plano_num = sin_acentos(texto or "").lower()
    bases = (plano_num, plano)   # con puntuación (para "09-04-2026") y sin ella (para rótulos partidos)
    # Nivel 1: rótulos de reforma (la ventana termina donde empieza un rótulo de actualización)
    candidatas = _sin_futuras([f for base in bases for f in _tras_rotulo(base, ROTULO_REFORMA, ROTULO_ACTUALIZACION)])
    if candidatas:
        return max(candidatas)
    # Nivel 2: rótulos de actualización y encabezado de Guanajuato
    candidatas = [f for base in bases for f in _tras_rotulo(base, ROTULO_ACTUALIZACION)]
    for m in ROTULO_REFORMA_PO.finditer(plano_num[:ZONA_ENCABEZADO]):
        candidatas += fechas_en(m.group(0))
    candidatas = _sin_futuras(candidatas)
    if candidatas:
        return max(candidatas)
    # Nivel 3: Ficha Genealógica (Querétaro)
    fecha = _fecha_ficha(plano_num)
    if fecha:
        return fecha
    # Nivel 4: tabla de reformas de Puebla (solo si está completa)
    _, _, fecha = _tabla_puebla(plano_num)
    if fecha:
        return fecha
    # Nivel 5: ley federal sin reformas
    nueva = NUEVA_LEY_DOF.search(plano_num[:ZONA_ENCABEZADO])
    if nueva:
        return int(nueva.group(3)), int(nueva.group(2)), int(nueva.group(1))
    return None


def necesita_mas_paginas(texto: str) -> bool:
    """True si la fecha puede estar más allá de las primeras páginas: la Ficha Genealógica de
    Querétaro (su historial sigue en páginas posteriores) o la tabla de reformas de Puebla cuando el
    índice que la cierra aún no aparece. Quien lee el archivo (comun.fecha_de_pdf) lee entonces 12."""
    plano_num = sin_acentos(texto or "").lower()
    if FICHA.search(plano_num):
        return True
    es_puebla, completa, _ = _tabla_puebla(plano_num)
    return es_puebla and not completa


def formato_fecha(fecha: Fecha | None) -> str:
    """(2026, 4, 9) -> '2026-04-09' (ISO); None -> 'no encontrada'."""
    return f"{fecha[0]:04d}-{fecha[1]:02d}-{fecha[2]:02d}" if fecha else "no encontrada"


_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DMA = re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})")


def leer_fecha(texto: str | None) -> Fecha | None:
    """'2026-04-09' o '09-04-2026' (también con '/') -> (2026, 4, 9). None si está vacío, no tiene ese
    formato o la fecha no existe (31-02-2026). Para la opción --reforma y para leer el manifiesto."""
    t = (texto or "").strip()
    m = _ISO.fullmatch(t)
    if m:
        a, me, d = (int(x) for x in m.groups())
    else:
        m = _DMA.fullmatch(t)
        if not m:
            return None
        d, me, a = (int(x) for x in m.groups())
    try:
        date(a, me, d)
    except ValueError:
        return None
    return a, me, d


# ====================================================================================================
# Herramienta: word
# ====================================================================================================
AYUDA_WORD = """
biblioteca.py word — Lee el texto de un documento de Word OFICIAL (.doc de Word 97-2003 o .docx) sin
programas externos (ni Word ni LibreOffice): solo Python y el paquete olefile (para el .doc).

Algunas fuentes oficiales publican el texto vigente solo en Word (p. ej. el Congreso de Baja California
Sur). Ese archivo se guarda en la biblioteca TAL CUAL; esta herramienta solo lee su texto:
  - biblioteca.py descargar lo usa para revisar la portada (nombre, estado y fecha de la última reforma);
  - biblioteca.py convertir lo usa para el Markdown cuando MarkItDown no puede (MarkItDown lee .docx, no .doc).
El XML del .docx se lee con defusedxml (protege de XML malicioso).

Qué entrega: TODO el texto, en su orden, sin agregar ni quitar nada: los párrafos (uno por bloque) y las
tablas (en formato de tabla de Markdown, con sus filas y columnas). Omite lo que no es texto: imágenes
(escudos, firmas), y las instrucciones internas de los campos de Word (conserva su resultado visible).

Prueba a mano (muestra el Markdown):
  python sistema/biblioteca.py word <archivo.doc|.docx>
"""


class ErrorWord(Exception):
    """El archivo no se pudo leer; el mensaje explica por qué."""


def tipo_word(ruta: Path) -> str | None:
    """'doc' o 'docx' según la firma interna del archivo (no la extensión); None si no es Word."""
    with open(ruta, "rb") as f:
        inicio = f.read(8)
    if inicio == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":    # contenedor de Office 97-2003 (Word, Excel, PowerPoint)
        return "doc" if "WordDocument".encode("utf-16-le") in Path(ruta).read_bytes() else None
    if inicio.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(ruta) as z:
                return "docx" if "word/document.xml" in z.namelist() else None
        except zipfile.BadZipFile:
            return None
    return None


def _limpiar(texto: str) -> str:
    """Solo normaliza la forma de escribir los caracteres (no cambia el texto que se lee)."""
    texto = unicodedata.normalize("NFC", texto)
    return (texto.replace("\t", " ").replace("\xa0", " ").replace("\x1e", "-").replace("‑", "-")
            .replace("\u200b", "").replace("﻿", ""))


# Modelo: lista de bloques. Párrafo = {"texto": str}; tabla = {"filas": [[(x0, x1, [textos])]]}, con los
# bordes de cada celda (twips en .doc, ancho de rejilla en .docx) para ubicarla en su columna.

# ---------------------------------------------------------------------------------------------------
# .doc (Word 97-2003): formato binario, especificación pública [MS-DOC]
# ---------------------------------------------------------------------------------------------------
_TAM_OPERANDO = {0: 1, 1: 1, 2: 2, 3: 4, 4: 2, 5: 2, 7: 3}


def _sprms(grpprl: bytes):
    """(propiedad, valor) de una lista de propiedades de Word (SPRM)."""
    i = 0
    while i + 2 <= len(grpprl):
        sprm = struct.unpack_from("<H", grpprl, i)[0]
        i += 2
        spra = sprm >> 13
        if spra == 6:
            if sprm in (0xD606, 0xD608):          # definición de tabla: su tamaño va en 2 bytes
                if i + 2 > len(grpprl):
                    return
                n = struct.unpack_from("<H", grpprl, i)[0] - 1
                op, i = grpprl[i + 2: i + 2 + n], i + 2 + n
            else:
                if i >= len(grpprl):
                    return
                n = grpprl[i]
                op, i = grpprl[i + 1: i + 1 + n], i + 1 + n
        else:
            n = _TAM_OPERANDO[spra]
            op, i = grpprl[i: i + n], i + n
        yield sprm, op


def _props_parrafo(grpprl: bytes) -> dict:
    p = {}
    for sprm, op in _sprms(grpprl):
        if sprm == 0x2416 and op:                 # el párrafo está en una tabla
            p["tabla"] = op[0]
        elif sprm == 0x2417 and op:               # marca de fin de fila
            p["fin_fila"] = op[0]
        elif sprm == 0xD608 and op:               # bordes de las celdas de la fila
            n = op[0]
            if len(op) >= 1 + 2 * (n + 1):
                p["bordes"] = list(struct.unpack_from(f"<{n + 1}h", op, 1))
    return p


class _Doc:
    def __init__(self, ruta: Path):
        import olefile  # si falta, el ImportError llega a documentos.texto_documento como FaltaDependencia
        try:
            ole = olefile.OleFileIO(str(ruta))
            try:
                self.wd = ole.openstream("WordDocument").read()
                if struct.unpack_from("<H", self.wd, 0)[0] != 0xA5EC:
                    raise ErrorWord("no es un documento de Word 97-2003")
                flags = struct.unpack_from("<H", self.wd, 0x0A)[0]
                if flags & 0x0100:
                    raise ErrorWord("el documento está protegido con contraseña")
                self.tabla = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
            finally:
                ole.close()
        except ErrorWord:
            raise
        except Exception as e:
            raise ErrorWord(f"no se pudo leer el .doc: {e}") from e
        csw = struct.unpack_from("<H", self.wd, 32)[0]
        pos = 34 + csw * 2
        cslw = struct.unpack_from("<H", self.wd, pos)[0]
        self.ccp_text = struct.unpack_from("<i", self.wd, pos + 2 + 12)[0]    # caracteres del texto principal
        self.base_fclcb = pos + 2 + cslw * 4 + 2
        self.estilos = self._estilos()

    def _fclcb(self, i: int):
        return struct.unpack_from("<II", self.wd, self.base_fclcb + i * 8)

    def caracteres(self):
        """(caracter, posición en el archivo) del texto principal, siguiendo la tabla de piezas (CLX)."""
        fc_clx, lcb_clx = self._fclcb(33)
        clx = self.tabla[fc_clx: fc_clx + lcb_clx]
        i = 0
        while i < len(clx) and clx[i] == 0x01:
            i += 3 + struct.unpack_from("<h", clx, i + 1)[0]
        if i >= len(clx) or clx[i] != 0x02:
            raise ErrorWord("no se reconoció la estructura del texto (CLX)")
        lcb = struct.unpack_from("<I", clx, i + 1)[0]
        plc = clx[i + 5: i + 5 + lcb]
        n = (lcb - 4) // 12
        cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
        for k in range(n):
            fc = struct.unpack_from("<I", plc, (n + 1) * 4 + k * 8 + 2)[0]
            comprimido = bool(fc & 0x40000000)
            fc &= 0x3FFFFFFF
            cp0, cp1 = cps[k], min(cps[k + 1], self.ccp_text)
            if cp0 >= self.ccp_text:
                break
            if comprimido:           # 1 byte por caracter (Windows-1252)
                base = fc // 2
                for j, ch in enumerate(self.wd[base: base + cp1 - cp0].decode("cp1252", errors="replace")):
                    yield ch, base + j
            else:                    # 2 bytes por caracter (UTF-16)
                for j, ch in enumerate(self.wd[fc: fc + 2 * (cp1 - cp0)].decode("utf-16-le", errors="replace")):
                    yield ch, fc + 2 * j

    def corridas_parrafo(self):
        """[(inicio, fin, propiedades)] de cada párrafo, por posición en el archivo (páginas FKP)."""
        fc, lcb = self._fclcb(13)
        plc = self.tabla[fc: fc + lcb]
        n = (lcb - 4) // 8
        out = []
        for pn in (x & 0x3FFFFF for x in struct.unpack_from(f"<{n}I", plc, (n + 1) * 4)):
            pag = self.wd[pn * 512: pn * 512 + 512]
            cuenta = pag[511]
            rgfc = struct.unpack_from(f"<{cuenta + 1}I", pag, 0)
            for k in range(cuenta):
                props = {}
                off = pag[(cuenta + 1) * 4 + k * 13] * 2
                if off:
                    cb = pag[off]
                    datos = pag[off + 1: off + 2 * cb] if cb else pag[off + 2: off + 2 + 2 * pag[off + 1]]
                    if len(datos) >= 2:
                        props["istd"] = struct.unpack_from("<H", datos, 0)[0]
                        props.update(_props_parrafo(datos[2:]))
                out.append((rgfc[k], rgfc[k + 1], props))
        out.sort(key=lambda x: x[0])
        return out

    def _estilos(self) -> dict:
        """Propiedades de párrafo de cada estilo (un estilo puede marcar que sus párrafos van en tabla)."""
        fc, lcb = self._fclcb(1)
        st = self.tabla[fc: fc + lcb]
        estilos: dict[int, dict] = {}
        if len(st) < 6:
            return estilos
        cb_info = struct.unpack_from("<H", st, 0)[0]
        cstd, cb_base = struct.unpack_from("<HH", st, 2)
        pos = 2 + cb_info
        for istd in range(cstd):
            if pos + 2 > len(st):
                break
            cb = struct.unpack_from("<H", st, pos)[0]
            std = st[pos + 2: pos + 2 + cb]
            pos += 2 + cb
            if cb < 6:
                continue
            w = struct.unpack_from("<H", std, 2)[0]
            sgc, base = w & 0x0F, w >> 4
            i = cb_base
            if i + 2 > len(std):
                continue
            i += 2 + struct.unpack_from("<H", std, i)[0] * 2 + 2      # el nombre del estilo
            props = {"base": base}
            i += i % 2
            if sgc == 1 and i + 2 <= len(std):
                cbu = struct.unpack_from("<H", std, i)[0]
                props.update(_props_parrafo(std[i + 4: i + 2 + cbu]))
            estilos[istd] = props
        return estilos

    def del_estilo(self, istd: int, clave: str, defecto=0):
        for _ in range(12):
            e = self.estilos.get(istd)
            if not e:
                return defecto
            if clave in e:
                return e[clave]
            if e["base"] == 0x0FFF:
                return defecto
            istd = e["base"]
        return defecto


class _Buscador:
    """Propiedades de la corrida que contiene una posición (llegan casi siempre en orden)."""
    def __init__(self, corridas):
        self.c, self.i = corridas, 0

    def __call__(self, fc):
        c = self.c
        if self.i < len(c) and c[self.i][0] <= fc < c[self.i][1]:
            return c[self.i][2]
        lo, hi = 0, len(c)
        while lo < hi:
            m = (lo + hi) // 2
            if c[m][1] <= fc:
                lo = m + 1
            else:
                hi = m
        if lo >= len(c) or c[lo][0] > fc:
            return {}
        self.i = lo
        return c[lo][2]


def _bloques_doc(ruta: Path) -> list:
    d = _Doc(ruta)
    pap = _Buscador(d.corridas_parrafo())
    bloques: list[dict] = []
    actual: list[str] = []
    campos: list[bool] = []
    tabla: dict | None = None
    fila: list[list[str]] = []
    celda: list[str] = []
    for ch, fc in d.caracteres():
        if ch == "\x13":                  # campo de Word: se omite su instrucción y se conserva su resultado
            campos.append(True)
            continue
        if ch == "\x14":
            if campos:
                campos[-1] = False
            continue
        if ch == "\x15":
            if campos:
                campos.pop()
            continue
        if campos and campos[-1]:
            continue
        if ch in ("\r", "\x07"):          # fin de párrafo o de celda
            pp = pap(fc)
            texto = _limpiar("".join(actual)).strip()
            actual = []
            if ch == "\x07" and pp.get("fin_fila"):
                tabla = tabla or {"filas": []}
                if celda:
                    fila.append(celda)
                bordes = pp.get("bordes") or []
                if len(bordes) >= len(fila) + 1:
                    tabla["filas"].append([(bordes[k], bordes[k + 1], fila[k]) for k in range(len(fila))])
                else:
                    tabla["filas"].append([(k, k + 1, fila[k]) for k in range(len(fila))])
                fila, celda = [], []
                continue
            if pp.get("tabla", d.del_estilo(pp.get("istd", 0), "tabla")):
                tabla = tabla or {"filas": []}
                celda.append(texto)
                if ch == "\x07":
                    fila.append(celda)
                    celda = []
                continue
            if tabla is not None:
                bloques.append(tabla)
                tabla, fila, celda = None, [], []
            bloques.append({"texto": texto})
            continue
        if ch in ("\x01", "\x02", "\x03", "\x04", "\x05", "\x08", "\x1f"):  # imágenes, objetos, notas, guion opcional
            continue
        actual.append("\n" if ch in ("\x0b", "\x0c") else ch)
    if tabla is not None:
        bloques.append(tabla)
    return bloques


# ---------------------------------------------------------------------------------------------------
# .docx (Office Open XML)
# ---------------------------------------------------------------------------------------------------
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _texto_parrafo_docx(p) -> str:
    partes = []
    for r in p.iter(_W + "r"):
        if r.find(_W + "instrText") is not None:
            continue
        for n in r:
            if n.tag == _W + "t":
                partes.append(n.text or "")
            elif n.tag == _W + "tab":
                partes.append("\t")
            elif n.tag in (_W + "br", _W + "cr"):
                partes.append("\n")
            elif n.tag == _W + "noBreakHyphen":
                partes.append("-")
    return _limpiar("".join(partes)).strip()


def _bloques_docx(ruta: Path) -> list:
    # el .docx viene de internet: defusedxml evita "billion laughs" y entidades externas. Se importa aquí (no al
    # cargar el módulo): los terceros solo se importan cuando hacen falta.
    from defusedxml import ElementTree as ET
    try:
        with zipfile.ZipFile(ruta) as z:
            cuerpo = ET.fromstring(z.read("word/document.xml")).find(_W + "body")
    except Exception as e:
        raise ErrorWord(f"no se pudo leer el .docx: {e}") from e
    bloques: list[dict] = []
    for elemento in cuerpo:
        if elemento.tag == _W + "p":
            bloques.append({"texto": _texto_parrafo_docx(elemento)})
        elif elemento.tag == _W + "tbl":
            anchos = [int(g.get(_W + "w") or 0) for g in elemento.iter(_W + "gridCol")]
            filas = []
            for tr in elemento.findall(_W + "tr"):
                x, fila = 0, []
                for tc in tr.findall(_W + "tc"):
                    span = tc.find(f"{_W}tcPr/{_W}gridSpan")
                    n = int(span.get(_W + "val")) if span is not None else 1
                    x0, x1 = (sum(anchos[:x]), sum(anchos[:x + n])) if anchos else (x, x + n)
                    fila.append((x0, x1, [_texto_parrafo_docx(p) for p in tc.iter(_W + "p")]))
                    x += n
                filas.append(fila)
            bloques.append({"filas": filas})
    return bloques


# ---------------------------------------------------------------------------------------------------
# Salidas
# ---------------------------------------------------------------------------------------------------
def bloques(ruta: Path) -> list:
    tipo = tipo_word(ruta)
    if tipo == "doc":
        return _bloques_doc(ruta)
    if tipo == "docx":
        return _bloques_docx(ruta)
    raise ErrorWord("no es un documento de Word (.doc o .docx)")


def texto_plano(ruta: Path) -> str:
    """Todo el texto en orden, un párrafo por renglón (para revisar la portada y la fecha)."""
    partes = []
    for b in bloques(ruta):
        if "texto" in b:
            partes.append(b["texto"])
        else:
            for fila in b["filas"]:
                partes.append(" ".join(t for _, _, celda in fila for t in celda if t))
    return "\n".join(partes)


def _tabla_markdown(filas: list) -> list[str]:
    """Una tabla de Markdown: las columnas son la unión de los bordes de todas las filas; el texto de una
    celda que abarca varias columnas va en la primera (las demás quedan vacías)."""
    filas = [f for f in filas if f]
    if not filas:
        return []
    cortes = sorted({x for fila in filas for x0, x1, _ in fila for x in (x0, x1)})
    ncol = max(1, len(cortes) - 1)
    renglones = []
    for fila in filas:
        celdas = [""] * ncol
        for x0, _x1, textos in fila:
            texto = " ".join(t.replace("\n", " ") for t in textos if t).replace("|", "\\|").strip()
            celdas[min(cortes.index(x0), ncol - 1)] = texto
        renglones.append("| " + " | ".join(celdas) + " |")
    renglones.insert(1, "|" + "---|" * ncol)
    return renglones


def a_markdown(ruta: Path) -> str:
    """El texto del Word en Markdown: un párrafo por bloque (separados por una línea en blanco) y las
    tablas como tablas de Markdown. Los párrafos vacíos del documento no se repiten."""
    salida = []
    for b in bloques(ruta):
        if "texto" in b:
            if b["texto"]:
                salida.append(b["texto"])
        else:
            tabla = _tabla_markdown(b["filas"])
            if tabla:
                salida.append("\n".join(tabla))
    return "\n\n".join(salida).strip() + "\n"


def main_word() -> None:
    """python sistema/biblioteca.py word <archivo.doc|.docx>: muestra el texto en Markdown (prueba a mano)."""
    p = Analizador(description="Muestra el texto de un Word oficial (.doc o .docx) en Markdown.",
                   formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_WORD)
    p.add_argument("archivo", type=Path, help="el .doc o .docx")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    args = p.parse_args()
    salida.iniciar(args.json, "word")
    if not args.archivo.is_file():
        salir_con_error(f"no existe el archivo {args.archivo}")
    try:
        texto = a_markdown(args.archivo)
    except ErrorWord as e:
        salir_con_error(str(e))
    print(texto)
    salida.poner(resultado="OK", caracteres=len(texto), texto=texto)


# ====================================================================================================
# Los documentos oficiales guardados: validar que un archivo sea PDF (o Word o página), leer su texto, su fecha
# de reforma y revisar que sea el documento correcto (título en la portada, nombre completo, entidad).
# ====================================================================================================
class FaltaDependencia(Exception):
    """No se pudo leer el texto porque falta un paquete de Python (no porque el archivo esté dañado)."""


MIN_BYTES_PDF = 10_000      # menos que esto casi siempre es una página de error
MIN_CARACTERES_MD = 200     # menos que esto = PDF escaneado (necesita OCR)

def es_pdf(ruta: Path, min_bytes: int = MIN_BYTES_PDF) -> tuple[bool, str]:
    """La prueba decisiva es la firma %PDF: un HTML guardado como .pdf NO pasa."""
    if not ruta.exists():
        return False, "el archivo no existe"
    with open(ruta, "rb") as f:
        inicio = f.read(1024)
    es_html = b"<html" in inicio.lower() or b"<!doctype" in inicio.lower()
    # La especificación PDF permite bytes basura antes de la firma: se busca en los primeros 1024.
    if b"%PDF" not in inicio or es_html:
        muestra = inicio[:60].decode("latin-1", errors="replace").strip()
        tipo = "HTML (página web, no PDF)" if es_html else "no es PDF"
        return False, f"{tipo}: los primeros bytes son '{muestra[:40]}'"
    tam = ruta.stat().st_size
    if tam < min_bytes:
        return False, f"PDF sospechosamente chico ({tam} bytes, mínimo {min_bytes})"
    return True, "PDF válido"


def quitar_envoltorio_pdf(ruta: Path) -> int:
    """Algunos portales (p. ej. Nayarit) anteponen HTML al PDF real aunque respondan application/pdf.
    Si dentro del archivo hay un PDF completo (%PDF- ... %%EOF), deja solo eso y devuelve cuántos
    bytes quitó al inicio (0 = no había nada que reparar)."""
    datos = ruta.read_bytes()
    inicio = datos.find(b"%PDF-", 0, 1 << 20)
    fin = datos.rfind(b"%%EOF")
    if inicio <= 0 or fin < inicio:
        return 0
    ruta.write_bytes(datos[inicio: fin + 5] + b"\n")
    return inicio

CARACTERES_POR_PAGINA = 3500                   # para leer "las primeras páginas" de un Word o una página


def es_html(ruta: Path) -> bool:
    with open(ruta, "rb") as f:
        inicio = f.read(4096).lower()
    return b"<html" in inicio or b"<!doctype html" in inicio


def formato_archivo(ruta: Path) -> str | None:
    """'pdf', 'doc', 'docx' o 'html' según el CONTENIDO del archivo (no su extensión); None si no es
    ninguno ('html' sirve para reconocer una página web y rechazarla: la biblioteca solo guarda PDF o Word). Un PDF
    con HTML pegado antes (Nayarit) cuenta como PDF: biblioteca.py descargar lo recorta."""
    if not ruta.exists():
        return None
    if es_pdf(ruta, 1)[0]:
        return "pdf"
    tipo = tipo_word(ruta)
    if tipo:
        return tipo
    return "html" if es_html(ruta) else None



def texto_documento(ruta: Path, paginas: int = 3) -> str:
    """Texto de las primeras `paginas` de un documento guardado (0 = todo), sea PDF o Word.
    Vacío si no tiene texto (PDF escaneado) o no se puede leer."""
    formato = formato_archivo(ruta)
    if formato not in ("doc", "docx"):
        return texto_pdf(ruta, paginas)
    try:
        texto = texto_plano(ruta)
    except ImportError as e:
        raise FaltaDependencia(f"falta el paquete '{(e.name or 'olefile').split('.')[0]}' para leer Word: "
                               "ejecuta python sistema/biblioteca.py instalar") from e
    except Exception:  # noqa: BLE001 - archivo dañado: se trata como sin texto
        return ""
    return texto if not paginas else texto[: paginas * CARACTERES_POR_PAGINA]


def documento_valido(ruta: Path, min_bytes: int = MIN_BYTES_PDF) -> tuple[bool, str, str | None]:
    """(válido, motivo, formato) de un documento oficial: un PDF real (firma %PDF y tamaño mínimo) o un Word
    con texto. Una página web nunca es válida: la biblioteca solo guarda PDF o Word."""
    formato = formato_archivo(ruta)
    if formato in ("doc", "docx"):
        n = len(texto_documento(ruta, paginas=0).strip())
        if n < MIN_CARACTERES_MD:
            return False, f"Word casi sin texto ({n} caracteres): ¿está dañado o es otro documento?", formato
        return True, f"Word oficial (.{formato})", formato
    if formato == "html":
        return False, "es una página web: la biblioteca solo guarda PDF o Word", None
    ok, motivo = es_pdf(ruta, min_bytes)
    return ok, motivo, "pdf" if ok else None


def texto_pdf(ruta: Path, paginas: int = 3) -> str:
    """Texto de las primeras `paginas` (0 = todo el PDF). Vacío si es escaneado o está dañado;
    FaltaDependencia si pdfminer no está instalado."""
    try:
        from pdfminer.high_level import extract_text
    except ImportError as e:
        raise FaltaDependencia("falta el paquete 'pdfminer.six' para leer PDF: ejecuta "
                               "python sistema/biblioteca.py instalar") from e
    try:
        return extract_text(str(ruta), maxpages=paginas) or ""
    except Exception:  # noqa: BLE001 - PDF dañado, cifrado o sin texto
        return ""


def fecha_de_pdf(ruta: Path, texto: str | None = None):
    """Fecha de la última reforma de un documento (PDF o Word), leída por fechas.fecha_reforma.
    Si la portada remite a una tabla larga (Ficha Genealógica de Querétaro, tabla de reformas de Puebla) que
    sigue más allá de las primeras páginas, lee hasta 12 para no quedarse con una reforma vieja."""
    texto = texto if texto is not None else texto_documento(ruta)
    if necesita_mas_paginas(texto):
        return fecha_reforma(texto_documento(ruta, paginas=12)) or fecha_reforma(texto)
    return fecha_reforma(texto)


# El artículo 1 real, no una cita del preámbulo ("...reforma en su artículo primero los artículos...")
_INICIO_CUERPO = re.compile(r"(?<!\bsu )(?<!\bel )(?<!\bdel )(?<!\bal )\barticulo (?:1|1o|primero)\b")


def _portada(plano: str) -> str:
    """La parte del texto (ya normalizado) ANTES del artículo 1: título, fechas y decreto. El cuerpo
    de una ley suele citar a otras (p. ej. el art. 1 de la Ley Aduanera cita al Código Fiscal de la
    Federación), así que el título se busca solo en la portada."""
    m = _INICIO_CUERPO.search(plano)
    portada = plano[:m.start()] if m else plano
    return portada if len(portada) >= 100 else plano   # sin portada útil: todo el texto


def coincidencia_titulo(nombre: str, texto: str):
    """Qué fracción de las palabras clave del nombre aparece en la portada (antes del artículo 1).
    None si el PDF no tiene texto (escaneado). Sirve para detectar que se descargó
    un documento equivocado."""
    plano = normalizar(texto)
    if len(plano) < 200:
        return None
    clave = palabras_clave(nombre)
    if not clave:
        return None
    presentes = set(_portada(plano).split())
    return len(clave & presentes) / len(clave)


_TITULOS_OFICIALES = ("ley", "codigo", "reglamento", "constitucion", "estatuto")


def _posicion(clave: list[str], palabras: list[str]) -> int:
    return f" {' '.join(palabras)} ".find(f" {' '.join(clave)} ")


def _se_expide(clave: list[str], plano: str) -> bool:
    """El decreto de expedición dice 'Se expide la <nombre>' / 'expide el <nombre>': es la señal más
    fuerte de cuál es la ley, aunque la portada solo traiga 'LEY DE SALUD' o un encabezado del P.O."""
    for m in re.finditer(r"\bexpid[eao]s?\b", plano[:20000]):
        palabras = [p for p in plano[m.end(): m.end() + 350].split() if p not in _PALABRAS_VACIAS and len(p) > 2]
        if 0 <= _posicion(clave, palabras) <= 3:
            return True
    return False


def revisar_nombre(nombre: str, texto: str, otros_nombres=()) -> tuple[bool | None, str]:
    """Revisión estricta del nombre (solo para nombres oficiales: Ley, Código, Reglamento,
    Constitución, Estatuto). Devuelve (resultado, motivo si falla); None = no aplica (nombres
    descriptivos como 'Decreto de reforma...' o PDF sin texto).
      1. El nombre completo, palabra por palabra y en orden (sin contar 'de', 'para el', 'del'...),
         debe aparecer en la PORTADA (antes del artículo 1). Detecta la ley SUSTITUTA y la ley que
         solo CITA a la buscada en su cuerpo.
      2. Ningún OTRO documento del catálogo (`otros_nombres`) debe aparecer antes que él: si el PDF
         dice 'Ley del Impuesto sobre la Renta' antes que 'Ley del IVA', es la LISR. La Constitución
         no cuenta como competidora (todas las leyes la citan en su preámbulo).
    Medición de falsas alarmas y cruces aceptados: sistema/referencia/actualizar.md (sección 1)."""
    clave = _secuencia(nombre)
    plano = normalizar(texto)
    if not clave or clave[0] not in _TITULOS_OFICIALES or len(plano) < 200:
        return None, ""
    portada = [p for p in _portada(plano).split() if p not in _PALABRAS_VACIAS and len(p) > 2]
    if _posicion(clave, portada) < 0 and not _se_expide(clave, plano):
        return False, f"el nombre completo '{nombre}' NO aparece en la portada (antes del artículo 1)"
    todo = _secuencia(texto)
    propio = _posicion(clave, todo)
    for otro in otros_nombres:
        competidor = _secuencia(otro)
        if not competidor or competidor == clave or competidor[0] not in _TITULOS_OFICIALES:
            continue
        if clave[0] != "constitucion" and competidor[0] == "constitucion":
            continue
        if 0 <= _posicion(competidor, todo) < propio:
            return False, f"antes que '{nombre}' aparece el nombre de OTRO documento del catálogo: '{otro}'"
    return True, ""


# Cómo se nombra cada entidad dentro de sus propias leyes (para detectar que un PDF
# es de OTRO estado: p. ej. el Código Penal de Michoacán guardado como el de Chihuahua).
_NOMBRES_ENTIDAD = {
    "ciudad de mexico": ["ciudad de mexico", "distrito federal"],
    "estado de mexico": ["estado de mexico", "estado libre y soberano de mexico", "mexiquense"],
    "michoacan": ["michoacan"],
    "coahuila": ["coahuila"],
    "veracruz": ["veracruz"],
}


def menciona_entidad(entidad: str, texto: str):
    """True si el texto menciona la entidad; None si el PDF no tiene texto (escaneado)."""
    plano = normalizar(texto)
    if len(plano) < 200:
        return None
    clave = normalizar(entidad)
    variantes = _NOMBRES_ENTIDAD.get(clave, [clave])
    return any(v in plano for v in variantes)


# ====================================================================================================
# Herramienta: buscar
# ====================================================================================================
AYUDA_BUSCAR = """
biblioteca.py buscar — Herramientas de BÚSQUEDA para el agente actualizador.

No descarga nada a la biblioteca: solo ayuda a ENCONTRAR la URL correcta.
Cuando tengas la URL, regístrala con biblioteca.py descargar. Lo que descarga para leer queda fuera del
proyecto: la página se borra al terminar; el índice de Diputados se guarda en caché (hasta 6 h) y
se borra al cerrar el proceso con biblioteca.py verificar --limpiar.

USO:
  python sistema/biblioteca.py buscar diputados "Código Fiscal de la Federación"
      Busca por NOMBRE en el índice oficial de leyes federales de la Cámara de
      Diputados. Muestra la URL vigente del PDF y la fecha de la última reforma.
      Con esa fecha puedes saber si hay algo nuevo SIN descargar.

  python sistema/biblioteca.py buscar diputados "Reglamento de Tránsito" --reglamentos
      Igual, pero en el índice de REGLAMENTOS federales.

  python sistema/biblioteca.py buscar enlaces <URL_DE_UNA_PÁGINA> [--filtro "texto"] [--todos] [--solo-pdf] [--limite N]
      Descarga una página web (p. ej. el listado de leyes de un congreso estatal)
      y lista los enlaces que parecen documentos (PDF, 'descargar', etc.) con su
      texto visible. --filtro deja solo los que contienen esas palabras
      (sin importar acentos ni mayúsculas). --todos incluye también los enlaces que no parecen
      documentos. --solo-pdf deja solo los que terminan en .pdf. Muestra 80 filas por defecto: si la
      cabecera dice "se muestran los primeros N", sube --limite.
"""


INDICES = {
    "leyes": "https://www.diputados.gob.mx/LeyesBiblio/index.htm",
    "reglamentos": "https://www.diputados.gob.mx/LeyesBiblio/regla.htm",
}
BASE_DIPUTADOS = "https://www.diputados.gob.mx/LeyesBiblio/"


def _indice(tipo: str) -> str:
    """Descarga (o reutiliza de la caché) el índice de Diputados. La caché vive fuera del proyecto,
    en la carpeta temporal del sistema; se reutiliza hasta 6 horas y se borra al cerrar el proceso
    (biblioteca.py verificar --limpiar) o sola cuando caduca."""
    cache = CACHE / f"indice_{tipo}.htm"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_VIGENCIA_SEG:
        return leer_html(cache)
    descarga = temporal() / f"indice_{tipo}.htm"   # temporal de ESTA ejecución (agentes en paralelo)
    r = http_descargar(INDICES[tipo], descarga)
    if not r["ok"]:
        salir_con_error(f"No se pudo descargar el índice de {tipo}: {r['error']}\n"
                              f"Intentos: {r['intentos']}")
    for _ in range(5):   # otro agente pudo borrar en ese instante la carpeta de la caché (estaba vacía)
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            os.replace(descarga, cache)
            return leer_html(cache)
        except OSError:          # carpeta borrada por otro agente, o caché abierta por otro (Windows)
            time.sleep(0.1)
    return leer_html(descarga)     # sin caché esta vez: se usa lo recién descargado


def filas_indice(tipo: str) -> list[dict]:
    """Cada fila del índice: texto visible, liga del PDF y fecha de última reforma."""
    crudo = _indice(tipo)
    filas = []
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", crudo, flags=re.S | re.I):
        pdfs = re.findall(r'href="([^"]+\.pdf)"', fila, flags=re.I)
        if not pdfs:
            continue
        texto = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fila)).split())
        fechas = [(int(a), int(m), int(d)) for d, m, a in re.findall(r"(\d{2})/(\d{2})/(\d{4})", texto)]
        filas.append({
            "texto": texto,
            "pdf": pdfs[0] if pdfs[0].startswith("http") else BASE_DIPUTADOS + pdfs[0].lstrip("/"),
            "ultima_reforma": max(fechas) if fechas else None,
        })
    return filas


def cmd_diputados(args):
    tipo = "reglamentos" if args.reglamentos else "leyes"
    filas = filas_indice(tipo)
    buscado = normalizar(args.nombre)
    exactas = [f for f in filas if re.search(r"\b" + re.escape(buscado) + r"\b", normalizar(f["texto"]))]
    if exactas:
        resultados, etiqueta = exactas, "coincidencia por nombre completo"
    else:
        clave = palabras_clave(args.nombre)
        puntuadas = sorted(((len(clave & palabras_clave(f["texto"])) / max(len(clave), 1), f)
                            for f in filas), key=lambda x: -x[0])
        resultados = [f for p, f in puntuadas[:8] if p > 0.3]
        etiqueta = "SIN coincidencia exacta; estas son las más parecidas (revisa con cuidado)"
    print(f"Índice de {tipo} de Diputados: {len(filas)} documentos. {etiqueta}.")
    salida.poner(indice=tipo, documentos_en_indice=len(filas), coincidencia=etiqueta,
                 resultados=[{"texto": f["texto"], "pdf": f["pdf"],
                              "ultima_reforma": formato_fecha(f["ultima_reforma"]) if f["ultima_reforma"] else None}
                             for f in resultados[:8]])
    if not resultados:
        print("Nada parecido. Prueba con otro nombre o revisa si la ley fue abrogada/renombrada.")
        return
    for f in resultados[:8]:
        print(f"\n  {f['texto'][:170]}")
        print(f"  PDF vigente:     {f['pdf']}")
        print(f"  Última reforma:  {formato_fecha(f['ultima_reforma'])}")


def cmd_enlaces(args):
    destino = temporal() / "pagina.html"   # temporal de ESTA ejecución; se borra solo al terminar
    r = http_descargar(args.url, destino)
    if not r["ok"]:
        salir_con_error(f"No se pudo abrir la página: {r['error']}\nIntentos: {r['intentos']}\n"
                              "Si el portal bloquea descargas automáticas, usa tu herramienta web (WebFetch) "
                              "u otra fuente oficial (ver biblioteca.py catalogo fuentes).")
    ok_pdf, _ = es_pdf(destino, min_bytes=1)
    if ok_pdf:
        print("Esa URL ya ES un PDF (no una página). Regístralo directo con biblioteca.py descargar.")
        return
    enlaces = enlaces_de_html(leer_html(destino), r["url_final"])
    if not args.todos:
        enlaces = [(u, t) for u, t in enlaces if parece_enlace_a_documento(u, t)]
    if args.filtro:
        palabras = normalizar(args.filtro).split()
        enlaces = [(u, t) for u, t in enlaces
                   if all(p in normalizar(t + " " + sin_acentos(u)) for p in palabras)]
    if args.solo_pdf:
        enlaces = [(u, t) for u, t in enlaces if sin_acentos(u).lower().split("?")[0].endswith(".pdf")]
    recorte = f", se muestran los primeros {args.limite}: usa --limite o --filtro" if len(enlaces) > args.limite else ""
    print(f"Página: {r['url_final']}  ({len(enlaces)} enlaces{' filtrados' if args.filtro else ''}{recorte})")
    salida.poner(pagina=r["url_final"], total=len(enlaces),
                 enlaces=[{"url": u, "texto": t, "word": es_word(u)} for u, t in enlaces])   # con --json van todos
    for i, (u, t) in enumerate(enlaces[: args.limite], 1):
        word = ("  [WORD, no es PDF: si es la única versión vigente, ver sistema/referencia/actualizar.md]"
                if es_word(u) else "")
        print(f"{i:4}. {t[:110] or '(sin texto)'}{word}\n      {u}")
    if len(enlaces) > args.limite:
        print(f"\n... {len(enlaces) - args.limite} enlaces más (usa --limite o un --filtro más preciso).")
    if not enlaces:
        print("No hay enlaces a documentos. La página puede cargar el listado con JavaScript: "
              "prueba --todos, busca un enlace a 'leyes'/'legislación', o usa WebFetch.")


def main_buscar():
    p = Analizador(description="Herramientas de búsqueda (no descargan a la biblioteca).",
                   formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_BUSCAR)
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    sub = p.add_subparsers(dest="comando", required=True)
    d = sub.add_parser("diputados", help="Buscar por nombre en el índice oficial de Diputados")
    d.add_argument("nombre")
    d.add_argument("--reglamentos", action="store_true")
    d.set_defaults(fn=cmd_diputados)
    e = sub.add_parser("enlaces", help="Listar enlaces a documentos de una página web")
    e.add_argument("url")
    e.add_argument("--filtro")
    e.add_argument("--todos", action="store_true")
    e.add_argument("--solo-pdf", action="store_true", dest="solo_pdf", help="solo enlaces cuya URL termina en .pdf")
    e.add_argument("--limite", type=int, default=80)
    e.set_defaults(fn=cmd_enlaces)
    json_tambien_al_final(sub)
    args = p.parse_args()
    salida.iniciar(args.json, "buscar")
    args.fn(args)


# ====================================================================================================
# Herramienta: descargar
# ====================================================================================================
AYUDA_DESCARGAR = """
biblioteca.py descargar — Descarga, valida, compara y guarda UN documento.

El agente encuentra la URL (con su razonamiento, biblioteca.py buscar o búsqueda web) y
esta herramienta hace el resto de forma segura y siempre igual:

  1. Descarga a una carpeta temporal propia de esta ejecución, fuera del proyecto (nunca
     directo a la biblioteca; se borra sola al terminar, pase lo que pase), con
     User-Agent de navegador y respaldos (certificados, sin 'www.', https).
  2. Valida que sea un PDF REAL (firma %PDF). Si trae HTML pegado antes del PDF, lo recorta.
     Si llega una página HTML, busca dentro el enlace al PDF; si hay uno claro lo sigue, si hay
     varios te los lista.
     Si la fuente oficial publica el documento solo en WORD (.doc o .docx: se reconoce por su firma
     interna, no por la extensión), se guarda ESE archivo tal cual, sin convertirlo, en la misma carpeta
     y con su extensión real; su texto (para las revisiones) se lee con el lector de Word propio
     (biblioteca.py word). Una PÁGINA WEB nunca se guarda: la biblioteca solo guarda PDF o Word.
  3. Revisa que sea el documento correcto, en su PORTADA (lo que va antes del artículo 1):
     palabras del nombre y, en leyes/códigos/reglamentos, el nombre completo en orden (detecta la
     ley SUSTITUTA o una ley que solo CITA a la buscada); en estatales, que mencione al estado.
     Si el PDF es escaneado (sin texto) no se puede revisar: lo dice y hay que revisarlo a ojo.
  4. Compara contra lo que ya está guardado (huella SHA-256 y fecha de reforma):
       - huella idéntica                       -> SIN_CAMBIO (se descarta)
       - huella distinta, misma fecha          -> SIN_CAMBIO_REAL (el servidor solo
                                                  regeneró el archivo; se descarta). Si además es
                                                  otro formato (un Word frente al PDF guardado), se
                                                  conserva el guardado CON su URL
       - huella distinta, reforma más reciente -> ACTUALIZADO (se reemplaza)
       - no había archivo                      -> NUEVO (se guarda)
       - cualquier caso raro                   -> DUDOSO (no se toca nada; decide el agente)
  5. Registra: manifiesto (SHA-256, URL, fechas), pista en el catálogo (para la próxima
     vez) y bitácora. Mientras dura el proceso, la versión reemplazada se guarda en anteriores/<id>.pdf,
     en la carpeta temporal del sistema (para deshacer si algo falla al guardar); verificar --limpiar la
     borra al cerrar.

USO:
  python sistema/biblioteca.py descargar <id> <URL>                 descargar y registrar
  python sistema/biblioteca.py descargar <id> <URL> --revisar       solo inspeccionar (no guarda nada)
  python sistema/biblioteca.py descargar <id> <URL> --revisar --buscar "abroga"
                                                         además, fragmentos de TODO el PDF con ese
                                                         texto (para leer los transitorios)
  python sistema/biblioteca.py descargar <id> --archivo ruta.pdf [--origen URL]
                                                         registrar un PDF obtenido de otra forma
                                                         (--origen = URL oficial de donde salió)
  python sistema/biblioteca.py descargar <id> <URL> --forzar        aceptar un caso DUDOSO que ya revisaste
  --permitir-pequeno   aceptar PDFs legítimos de menos de 10 KB
  --reforma AAAA-MM-DD fecha de última reforma que TÚ leíste (también DD-MM-AAAA): si la portada no la trae
                       o la herramienta la lee mal. Manda sobre la leída y corrige también un documento ya
                       guardado (SIN_CAMBIO)
  python sistema/biblioteca.py descargar --lote lista.txt           uno por línea: "id URL" o "id URL AAAA-MM-DD"
                                                         (la fecha, opcional, es la de --reforma). Descarga
                                                         uno tras otro y resume cada uno con su resultado,
                                                         fecha y título; la URL debe salir del listado o
                                                         índice oficial (nunca inventada)

CÓDIGOS DE SALIDA (guíate siempre por la línea "RESULTADO:"):
  0 = NUEVO, ACTUALIZADO, SIN_CAMBIO, SIN_CAMBIO_REAL o REVISADO (con --revisar)
  1 = ERROR: no se pudo descargar, o no es un PDF ni un Word con texto. También sale 1, con una línea "ERROR: ..." y sin
      "RESULTADO:", si el id no existe, si está marcado no_existe/no_localizado, si el --archivo no
      existe, si no se indicó URL ni --archivo, si falta pdfminer o si el candado está ocupado
      demasiado tiempo
  2 = DUDOSO (no se guardó; revisa y, si procede, repite con --forzar)
  3 = VARIOS_ENLACES: la URL es una página con varios PDFs posibles (elige uno de la lista)
  64 = error de uso (opción mal escrita, --buscar sin --revisar, --origen sin --archivo); sin "RESULTADO:"
  130 = interrumpido con Ctrl+C o Ctrl+Break
Los mensajes "ERROR: ..." salen por stderr; "RESULTADO: ..." por stdout.
"""


UMBRAL_TITULO = 0.5   # fracción mínima de palabras del nombre que deben aparecer en la portada


def _elegir_enlace(doc: dict, html_ruta, url_base: str):
    """En una página HTML, intenta encontrar el enlace al PDF del documento."""
    ruido = ("aviso_privacidad", "aviso-privacidad", "aviso de privacidad", "avisoprivacidad")
    enlaces = [(u, t) for u, t in enlaces_de_html(leer_html(html_ruta), url_base)
               if parece_enlace_a_documento(u, t)
               and not any(r in (u + " " + t).lower() for r in ruido)]
    if not enlaces:
        return None, []
    # Si hay enlaces a PDF, se descartan visores (officeapps) y versiones Word del mismo documento.
    def es_pdfish(u):
        ul = u.lower()
        return "pdf" in ul and "officeapps" not in ul and not ul.split("?")[0].endswith((".doc", ".docx"))
    if any(es_pdfish(u) for u, _ in enlaces):
        enlaces = [(u, t) for u, t in enlaces if es_pdfish(u)]
    clave = palabras_clave(doc["nombre"])
    puntuados = sorted(
        ((len(clave & palabras_clave(t + " " + sin_acentos(u).replace("_", " "))) / max(len(clave), 1), u, t)
         for u, t in enlaces), reverse=True)
    if len(puntuados) == 1:
        return puntuados[0][1], puntuados
    mejor, segundo = puntuados[0][0], puntuados[1][0]
    if mejor >= 0.5 and mejor - segundo >= 0.15:
        return puntuados[0][1], puntuados
    return None, puntuados


def _mostrar_fragmentos(pdf, buscado: str, maximo: int = 12) -> None:
    """Muestra los fragmentos de TODO el PDF que contienen `buscado` (sin importar acentos ni
    mayúsculas). Sirve para leer los transitorios: 'abroga', 'se expide', 'entrará en vigor'..."""
    texto = " ".join(texto_documento(pdf, paginas=0).split())
    plano = sin_acentos(texto).lower()
    clave = sin_acentos(buscado).lower()
    posiciones, inicio = [], 0
    while (i := plano.find(clave, inicio)) != -1:
        posiciones.append(i)
        inicio = i + len(clave)
    print(f"\nFragmentos con '{buscado}' en todo el PDF: {len(posiciones)}")
    for i in posiciones[:maximo]:
        print(f"  … {texto[max(0, i - 220): i + 280]} …\n")
    if len(posiciones) > maximo:
        print(f"  ... y {len(posiciones) - maximo} más (usa un texto más preciso).")


def _reparar_si_hace_falta(candidato) -> None:
    """Si el 'PDF' trae HTML o basura antes de la firma (pasa en Nayarit), recorta el PDF real."""
    if es_pdf(candidato, 1)[0]:
        return
    quitados = quitar_envoltorio_pdf(candidato)
    if quitados:
        print(f"AVISO: el archivo traía {quitados:,} bytes de HTML/basura antes del PDF; se recortó desde "
              "la firma %PDF hasta el último %%EOF (el contenido del PDF no cambia).")


def _formato_oficial(candidato) -> str | None:
    """'doc'/'docx' si llegó un Word; None en otro caso (un PDF, o una página en la que hay que buscar el enlace
    al PDF: una página web nunca se guarda)."""
    formato = formato_archivo(candidato)
    return formato if formato in ("doc", "docx") else None


def _obtener(doc: dict, args, candidato) -> tuple[str, str]:
    """Deja el documento candidato en `candidato`. Devuelve la URL de la que salió y su formato: 'pdf', o
    'doc'/'docx' si la fuente oficial solo lo publica en Word (se guarda tal cual)."""
    if args.archivo:
        origen = Path(args.archivo)
        if not origen.exists():
            salir_con_error(f"No existe el archivo {origen}")
        shutil.copyfile(origen, candidato)
        # con --origen se conserva la URL real (manifiesto y pista del catálogo)
        url = args.origen or f"archivo local: {origen.name}"
        formato = _formato_oficial(candidato)
        if not formato:
            _reparar_si_hace_falta(candidato)
        return url, formato or "pdf"

    r = http_descargar(args.url, candidato)
    if not r["ok"]:
        bitacora({"herramienta": "descargar", "id": doc["id"], "url": args.url,
                        "resultado": "ERROR", "detalle": r["error"]})
        print("RESULTADO: ERROR (no se pudo descargar)")
        salida.poner(resultado="ERROR", motivo=r["error"], intentos=r["intentos"])
        print(f"  Motivo: {r['error']}")
        print("  Intentos:\n    " + "\n    ".join(r["intentos"]))
        print("  Sugerencia: la URL cambió o el portal bloquea descargas automáticas. Busca la liga "
              "vigente (python sistema/biblioteca.py catalogo fuentes ...) o descarga el PDF con otra "
              "herramienta y regístralo con --archivo.")
        sys.exit(1)
    url_final = r["url_final"]
    formato = _formato_oficial(candidato)
    if formato:
        return url_final, formato
    _reparar_si_hace_falta(candidato)

    ok, motivo = es_pdf(candidato, 1)
    if not ok and "HTML" in motivo:
        elegido, lista = _elegir_enlace(doc, candidato, url_final)
        if elegido:
            print(f"AVISO: la URL era una página web; se siguió el enlace al documento: {elegido}")
            r2 = http_descargar(elegido, candidato)
            if not r2["ok"]:
                bitacora({"herramienta": "descargar", "id": doc["id"], "url": elegido,
                                "resultado": "ERROR", "detalle": r2["error"]})
                print(f"RESULTADO: ERROR (el enlace encontrado en la página no se pudo descargar: {r2['error']})")
                salida.poner(resultado="ERROR", motivo=r2["error"], enlace=elegido)
                sys.exit(1)
            formato = _formato_oficial(candidato)
            if not formato:
                _reparar_si_hace_falta(candidato)
            return r2["url_final"], formato or "pdf"
        candidato.unlink(missing_ok=True)
        if lista:
            print("RESULTADO: VARIOS_ENLACES (la URL es una página, no un PDF)")
            print("  Estos enlaces parecen documentos; elige el correcto y vuelve a ejecutar con esa URL:")
            for p, u, t in lista[:15]:
                print(f"   [{p:.0%}] {t[:90] or '(sin texto)'}\n          {u}")
            salida.poner(resultado="VARIOS_ENLACES",
                         enlaces=[{"puntuacion": round(p, 2), "url": u, "texto": t} for p, u, t in lista[:15]])
            sys.exit(3)
        bitacora({"herramienta": "descargar", "id": doc["id"], "url": url_final,
                        "resultado": "ERROR", "detalle": "página web sin enlaces a PDF"})
        print("RESULTADO: ERROR (la URL devuelve una página web sin enlaces a PDF)")
        salida.poner(resultado="ERROR", motivo="página web sin enlaces a PDF")
        print("  Puede ser un visor con JavaScript o una página de error. Revisa la página con tu "
              "herramienta web o busca la liga directa al PDF o Word. La biblioteca solo guarda PDF o Word: "
              "si el documento solo existe como página web, busca su PDF o Word en otra fuente oficial y, "
              "si no hay, no se guarda (repórtalo).")
        sys.exit(1)
    return url_final, "pdf"


def _linea_lote(linea: str) -> tuple[str, str, str | None] | None:
    """'id URL' o 'id URL AAAA-MM-DD' -> (id, URL, fecha o None); None si la línea no tiene ese formato. La URL
    puede traer espacios (algunos portales los usan): la fecha solo cuenta como tal si es la última palabra."""
    partes = linea.split(None, 1)
    if len(partes) != 2:
        return None
    doc_id, resto = partes
    fecha = re.search(r"\s(\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4})$", resto)
    if fecha:
        return doc_id, resto[: fecha.start()].strip(), fecha.group(1)
    return doc_id, resto.strip(), None


def _lote(args) -> None:
    """Descarga una lista 'id URL' uno por uno (cada uno en su propio proceso, con las mismas
    validaciones) y resume cada resultado en una línea. Los DUDOSO y ERROR se revisan a mano después."""
    ruta = Path(args.lote)
    if not ruta.exists():
        salir_con_error(f"No existe el archivo {ruta}")
    # utf-8-sig y splitlines: toleran el BOM y los \r de un archivo escrito en Windows
    lineas = [x.strip() for x in ruta.read_text(encoding="utf-8-sig").splitlines()]
    validas = [x for x in lineas if x and not x.startswith("#")]
    leidas = [_linea_lote(x) for x in validas]
    malas = [x for x, t in zip(validas, leidas, strict=True) if t is None]
    if malas or not validas:
        salir_con_error("Cada línea debe ser 'id URL' o 'id URL AAAA-MM-DD' (separados por espacio o tabulador)"
                        + (f"; revisa: {malas[0][:60]}" if malas else "; el archivo está vacío"))
    trabajos = [t for t in leidas if t is not None]
    extra = (["--forzar"] if args.forzar else []) + (["--permitir-pequeno"] if args.pequeno else [])
    conteo: dict[str, int] = {}
    print(f"Lote de {len(trabajos)} documento(s):", flush=True)
    for n, (doc_id, url, reforma) in enumerate(trabajos, 1):
        try:
            r = correr_hijo([sys.executable, str(ENTRADA), "descargar", doc_id, url, *extra,
                             *(["--reforma", reforma] if reforma else [])], timeout=3600)
            lineas = (r.stdout or "").splitlines()
            errores = (r.stderr or "").strip().splitlines()
        except subprocess.TimeoutExpired:
            lineas, errores = ["RESULTADO: ERROR (tiempo agotado: una hora)"], []
        resultado = next((x for x in lineas if x.startswith("RESULTADO:")), None)
        if resultado is None:            # error de uso, id inexistente o excepción: no hay línea RESULTADO
            resultado = next((x for x in errores if x.startswith("ERROR")), None) \
                or ("RESULTADO: ERROR (" + (errores[-1][:200] if errores else "sin salida") + ")")
        clave = resultado.replace("RESULTADO:", "").split()[0].rstrip(":") if resultado.split() else "ERROR"
        conteo[clave] = conteo.get(clave, 0) + 1
        detalles = [x.strip() for x in lineas if x.startswith(("Última reforma:", "Coincide título:", "AVISO:"))]
        print(f"[{n}/{len(trabajos)}] {doc_id}\n      {resultado.strip()[:300]}", flush=True)
        for d in detalles:
            print(f"      {' '.join(d.split())[:200]}", flush=True)
        salida.agregar("documentos", {"id": doc_id, "url": url, "resultado": clave, "detalle": resultado.strip()[:300],
                                      "reforma_indicada": reforma, "detalles": detalles})
    print("\nResumen del lote: " + "  ".join(f"{k}={v}" for k, v in sorted(conteo.items())))
    salida.poner(resumen=conteo, total=len(trabajos))
    print("Los DUDOSO y ERROR no se guardaron: revísalos uno por uno (paso D y C del manual).")


def main():
    p = Analizador(description="Descargar, validar, comparar y guardar un documento.",
                         formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_DESCARGAR)
    p.add_argument("id", nargs="?", help="id del documento en el catálogo")
    p.add_argument("url", nargs="?", help="URL del PDF (o de la página que lo contiene)")
    p.add_argument("--lote", metavar="ARCHIVO",
                   help="archivo con una línea 'id URL' por documento: los descarga uno por uno y resume")
    p.add_argument("--archivo", help="registrar un PDF local en vez de descargar")
    p.add_argument("--origen", help="con --archivo: URL oficial de donde salió (se guarda como pista)")
    p.add_argument("--revisar", action="store_true", help="solo inspeccionar, no guardar")
    p.add_argument("--buscar", metavar="TEXTO",
                   help="con --revisar: muestra en TODO el PDF los fragmentos que contienen ese texto "
                        "(p. ej. 'abroga' para leer los transitorios)")
    p.add_argument("--forzar", action="store_true", help="aceptar un caso DUDOSO ya revisado")
    p.add_argument("--permitir-pequeno", action="store_true", dest="pequeno")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    p.add_argument("--reforma", metavar="AAAA-MM-DD",
                   help="fecha de última reforma que TÚ leíste en el documento: para cuando la portada no la "
                        "trae o la herramienta la lee mal. Manda sobre la leída (se avisa si difieren) y también "
                        "corrige la ficha de un documento ya guardado (resultado SIN_CAMBIO)")
    args = p.parse_args()
    salida.iniciar(args.json, "descargar")
    if args.lote:
        if args.id or args.url or args.archivo or args.revisar:
            p.error("--lote se usa solo, con --forzar o --permitir-pequeno si hace falta")
        return _lote(args)
    if not args.id:
        p.error("indica el id del documento (o --lote archivo)")
    if args.url:
        args.url = args.url.strip().strip("\"'")      # un retorno de carro o comillas de un script propio dan 404 falsos
    if args.reforma:
        args.reforma = leer_fecha(args.reforma)
        if not args.reforma:
            p.error("--reforma debe ser una fecha AAAA-MM-DD (o DD-MM-AAAA)")
    if args.buscar and not args.revisar:
        p.error("--buscar solo se usa junto con --revisar (no guarda nada)")
    if args.origen and not args.archivo:
        p.error("--origen solo se usa junto con --archivo")
    if not args.url and not args.archivo:
        salir_con_error("Indica una URL o --archivo ruta.pdf")
    try:  # sin pdfminer todos los PDF parecerían escaneados y no se podría revisar título ni fecha
        import pdfminer  # noqa: F401
    except ImportError:
        salir_con_error("Falta la dependencia pdfminer. Ejecuta primero: python sistema/biblioteca.py instalar")

    asegurar_carpetas()
    catalogo = cargar_catalogo()
    doc = buscar_documento(catalogo, args.id)
    if doc["estado"] in ("no_existe", "no_localizado") and not args.revisar:
        salir_con_error(f"'{doc['id']}' está marcado como {doc['estado']}. Si ya existe/lo encontraste, "
                              f"primero: python sistema/biblioteca.py catalogo marcar {doc['id']} --estado vigente "
                              "--nota \"...\"")

    # Solo fuentes oficiales: la copia de otro sitio nunca se guarda (ni siquiera la de la misma reforma)
    for u in (args.url, args.origen):
        sitio = es_fuente_no_oficial(u or "")
        if sitio:
            salir_con_error(f"{sitio} no es una fuente oficial: su copia no se guarda en la biblioteca. Busca "
                                  "el documento en el congreso, el periódico oficial u otro organismo público; si no "
                                  "está en ninguna fuente oficial en línea, repórtalo como no localizado.")
    candidato = temporal() / f"{doc['id']}.descarga"   # fuera del proyecto; se borra solo al terminar
    url, formato = _obtener(doc, args, candidato)
    sitio = es_fuente_no_oficial(url)          # por si la URL oficial redirigió a otro sitio
    if sitio:
        candidato.unlink(missing_ok=True)
        salir_con_error(f"La URL terminó en {sitio}, que no es una fuente oficial: no se guarda nada.")

    ok, motivo, _ = documento_valido(candidato, 1 if args.pequeno else MIN_BYTES_PDF)
    if not ok:
        candidato.unlink(missing_ok=True)
        bitacora({"herramienta": "descargar", "id": doc["id"], "url": url, "resultado": "ERROR", "detalle": motivo})
        print(f"RESULTADO: ERROR (no es un documento válido: {motivo})")
        salida.poner(resultado="ERROR", motivo=motivo)
        if "chico" in motivo:
            print("  Si confirmaste que es un documento oficial legítimamente corto (p. ej. un anexo de "
                  "una página), repite con --permitir-pequeno.")
        sys.exit(1)

    texto = texto_documento(candidato)
    fecha_leida = fecha_nueva = fecha_de_pdf(candidato, texto)
    fecha_manual = False
    if args.reforma:              # la indicó el agente tras leer el documento: manda sobre la que se leyó
        fecha_nueva, fecha_manual = args.reforma, True
    titulo = coincidencia_titulo(doc["nombre"], texto)
    # otros documentos del mismo ámbito: si su nombre aparece antes que el buscado, el PDF es de ellos
    otros = [d["nombre"] for d in catalogo["documentos"]
             if d["id"] != doc["id"] and (d.get("entidad") or None) == (doc.get("entidad") or None)]
    nombre_ok, motivo_nombre = revisar_nombre(doc["nombre"], texto, otros)
    # Una falsa alarma del NOMBRE que ya se confirmó con --forzar para ESTA URL no se repite, siempre que
    # la alarma sea exactamente la misma (si la URL empieza a entregar otra ley, el motivo cambia).
    registro_previo = cargar_manifiesto().get(doc["id"]) or {}
    url_ya_validada = (nombre_ok is False and registro_previo.get("url") == url
                       and registro_previo.get("alarma_confirmada") == motivo_nombre)
    huella_nueva = sha256_archivo(candidato)
    # El MISMO archivo (byte a byte) que ya está aceptado en la biblioteca no puede ser otro documento: sus
    # alarmas de título, nombre o entidad ya se revisaron (o no las tuvo) y no se repiten en cada actualización.
    huella_aceptada = (registro_previo.get("compresion") or {}).get("sha256_original") or registro_previo.get("sha256")
    ya_aceptado = bool(huella_aceptada) and huella_nueva == huella_aceptada
    marca_nombre = motivo_nombre if nombre_ok is False and (args.forzar or url_ya_validada or ya_aceptado) else None
    tam_nuevo = candidato.stat().st_size

    salida.poner(id=doc["id"], nombre=doc["nombre"], url=url, formato=formato, bytes=tam_nuevo, sha256=huella_nueva,
                 fecha_reforma=formato_fecha(fecha_nueva) if fecha_nueva else None, fecha_manual=fecha_manual,
                 fecha_leida=formato_fecha(fecha_leida) if fecha_leida else None, ya_aceptado=ya_aceptado,
                 coincide_titulo=None if titulo is None else round(titulo, 2), nombre_en_portada=nombre_ok,
                 motivo_nombre=motivo_nombre or None)
    print(f"Documento:        {doc['nombre']}  ({doc['id']})")
    print(f"Fuente:           {url}")
    etiqueta = {"pdf": "PDF descargado:", "doc": "Word (.doc) descargado:", "docx": "Word (.docx) descargado:",
                "html": "Página web descargada:"}[formato]
    print(f"{etiqueta:<17} {tam_nuevo:,} bytes | SHA-256 {huella_nueva}"
          + ("" if formato == "pdf" else "  (el archivo oficial, tal cual)"))
    print(f"Última reforma:   {formato_fecha(fecha_nueva)} "
          + ("(indicada con --reforma)" if fecha_manual else "(según la portada)"))
    if fecha_manual and fecha_leida and fecha_leida != fecha_nueva:
        print(f"AVISO:            la herramienta leyó {formato_fecha(fecha_leida)} en la portada; se usa la indicada "
              "con --reforma")
    print("Coincide título:  " + ("SIN TEXTO (PDF escaneado): no se pudo validar nombre ni fecha; ábrelo y "
                            "confirma a ojo que es el documento" if titulo is None else f"{titulo:.0%}")
          + ("" if nombre_ok is None else f" | nombre en la portada: {'sí' if nombre_ok else 'NO'}")
          + (" (ya confirmado antes con --forzar para esta URL)" if url_ya_validada else "")
          + (" | idéntico al documento ya aceptado: sus alarmas no se repiten" if ya_aceptado else ""))
    if nombre_ok is False:
        print(f"                  {motivo_nombre}")

    if args.revisar:
        inicio = " ".join(texto.split())[:400]
        print(f"Texto extraído:   {len(texto):,} caracteres de las primeras páginas"
              + ("" if texto.strip() else " (PDF escaneado: hace falta OCR)"))
        print(f"Inicio del texto: {inicio or '(sin texto)'}")
        if args.buscar:
            _mostrar_fragmentos(candidato, args.buscar)
        candidato.unlink(missing_ok=True)
        print("RESULTADO: REVISADO (no se guardó nada)")
        salida.poner(resultado="REVISADO", texto_inicio=inicio, caracteres=len(texto))
        return

    motivo = alarma_de_contenido(titulo, nombre_ok, motivo_nombre, doc.get("entidad"), texto, forzar=args.forzar,
                                 ya_aceptado=ya_aceptado, url_ya_validada=url_ya_validada)
    if motivo:
        candidato.unlink(missing_ok=True)
        _dudoso(doc, url, motivo)

    # A partir de aquí se decide y se registra: dentro del candado, releyendo catálogo y
    # manifiesto, para no pisar lo que otro agente haya guardado mientras descargábamos.
    with bloqueo():
        _decidir_y_registrar(args, doc["id"], candidato, url, fecha_nueva, huella_nueva, tam_nuevo, marca_nombre,
                             formato)


def decidir(existe: bool, huella_actual: str | None, huella_nueva: str, fecha_actual, fecha_nueva,
            forzar: bool = False) -> tuple[str, str | None]:
    """La decisión de ACTUALIZAR, como función pura (se prueba en pruebas/test_descargar.py):
    (resultado, motivo) con resultado NUEVO, SIN_CAMBIO, SIN_CAMBIO_REAL, ACTUALIZADO o DUDOSO (y su motivo).
      - no había archivo                       -> NUEVO
      - huella idéntica                        -> SIN_CAMBIO
      - --forzar                               -> ACTUALIZADO (el agente ya revisó)
      - fecha nueva más reciente               -> ACTUALIZADO
      - misma fecha                            -> SIN_CAMBIO_REAL (el servidor regeneró el archivo)
      - fecha nueva más vieja                  -> DUDOSO (URL desactualizada)
      - sin fecha en alguna de las dos         -> DUDOSO (no se puede comparar)"""
    if not existe:
        return "NUEVO", None
    if huella_actual == huella_nueva:
        return "SIN_CAMBIO", None
    if forzar:
        return "ACTUALIZADO", None
    if fecha_nueva and fecha_actual:
        if fecha_nueva > fecha_actual:
            return "ACTUALIZADO", None
        if fecha_nueva == fecha_actual:
            return "SIN_CAMBIO_REAL", None
        return "DUDOSO", ("la fuente tiene una reforma MÁS VIEJA que la guardada. Probablemente es una URL "
                          "desactualizada: busca la versión vigente.")
    return "DUDOSO", ("el archivo cambió (huella distinta) pero no se pudo leer la fecha de reforma de ambas "
                      "versiones. Verifica en el portal la fecha de publicación; si es más reciente, repite con --forzar.")


def alarma_de_contenido(titulo: float | None, nombre_ok: bool | None, motivo_nombre: str | None, entidad: str | None,
                        texto: str, *, forzar: bool, ya_aceptado: bool, url_ya_validada: bool) -> str | None:
    """Por qué la portada hace dudar de que sea el documento buscado (motivo del DUDOSO), o None si se puede
    guardar. No hay alarma con --forzar (el agente ya lo revisó) ni si el archivo es idéntico al que ya está aceptado
    en la biblioteca (no puede ser otro documento). La alarma del nombre confirmada antes para esa misma URL
    (url_ya_validada) tampoco se repite."""
    if forzar or ya_aceptado:
        return None
    if titulo is not None and titulo < UMBRAL_TITULO:
        return (f"la portada solo contiene {titulo:.0%} de las palabras del nombre: puede ser OTRO documento. "
                "Revisa con --revisar y, si es correcto, usa --forzar.")
    if nombre_ok is False and not url_ya_validada:
        palabras = f" ({titulo:.0%})" if titulo is not None else ""
        return (f"sus palabras aparecen{palabras}, pero {motivo_nombre}: puede ser la ley SUSTITUTA, "
                "otra ley que solo la cita, o el nombre del catálogo es inexacto. Revisa con --revisar "
                "(y --buscar \"abroga\"): otra ley -> paso E (marcar/agregar); misma ley con otro nombre -> "
                "biblioteca.py catalogo renombrar; es correcto -> --forzar. Si el PDF es una EDICIÓN COMPLETA "
                "del periódico oficial, un dictamen o trae varios decretos, confirma con --revisar --buscar "
                "\"<frase del decreto>\" y usa --forzar.")
    if entidad and menciona_entidad(entidad, texto) is False:
        return (f"el PDF no menciona a '{entidad}' en sus primeras páginas: puede ser la ley de OTRO estado "
                "(muchos estados tienen leyes con el mismo nombre). Revisa con --revisar y, si es correcto, usa --forzar.")
    return None


def _decidir_y_registrar(args, doc_id, candidato, url, fecha_nueva, huella_nueva, tam_nuevo, marca_nombre=None,
                         formato="pdf"):
    catalogo = cargar_catalogo()
    doc = buscar_documento(catalogo, doc_id)
    exigir_estructura(doc)          # nunca se guarda un documento fuera de la estructura de la biblioteca
    guardado = ruta_pdf(doc)        # el que ya está (con su extensión: .pdf, .doc...), si hay
    destino = ruta_pdf(doc, formato)
    manifiesto = cargar_manifiesto()
    registro = manifiesto.get(doc["id"], {})

    otro_formato = guardado.exists() and guardado.suffix != destino.suffix
    huella_actual = fecha_actual = None
    if guardado.exists():
        # si el guardado está comprimido, se compara contra la huella del PDF oficial del que salió
        huella_actual = (registro.get("compresion") or {}).get("sha256_original") or sha256_archivo(guardado)
        if huella_actual != huella_nueva:
            fecha_actual = _fecha_de_registro(registro) or fecha_de_pdf(guardado)
            print(f"Versión guardada: {guardado.stat().st_size:,} bytes | reforma {formato_fecha(fecha_actual)}")
    resultado, motivo = decidir(guardado.exists(), huella_actual, huella_nueva, fecha_actual, fecha_nueva, args.forzar)
    if resultado == "DUDOSO":
        candidato.unlink(missing_ok=True)
        _dudoso(doc, url, motivo)

    if resultado in ("SIN_CAMBIO", "SIN_CAMBIO_REAL"):
        candidato.unlink(missing_ok=True)
        # misma reforma en otro formato (un Word frente al PDF oficial guardado): se conserva el guardado y
        # también SU url, para que el registro siga diciendo de dónde salió ese archivo
        registro.update({"ultima_verificacion": hoy()} if otro_formato else {"url": url, "ultima_verificacion": hoy()})
        destino = guardado
        huella_disco = sha256_archivo(destino)
        if registro.get("sha256") != huella_disco:   # ficha ausente o desincronizada: se corrige con el PDF real
            if "sha256" in registro:
                print("AVISO: la ficha del manifiesto no coincidía con el PDF guardado; se corrigió.")
            fecha_disco = fecha_nueva or fecha_de_pdf(destino)   # fecha_nueva ya trae la de --reforma
            registro.update({"archivo": ruta_relativa_pdf(doc, destino.suffix.lstrip(".")),
                             "formato": destino.suffix.lstrip("."), "sha256": huella_disco,
                             "bytes": destino.stat().st_size,
                             "fecha_reforma": formato_fecha(fecha_disco) if fecha_disco else None})
            registro.setdefault("fecha_descarga", hoy())
            registro.setdefault("markdown", None)
        elif resultado == "SIN_CAMBIO":
            # mismo PDF: si el lector de fechas mejoró desde que se guardó, o el agente indicó la fecha con
            # --reforma, la ficha se corrige. Sin fecha legible se conserva la guardada, salvo que sea futura.
            guardada = _fecha_de_registro(registro)
            ahora_ = datetime.now()
            if fecha_nueva:
                registro["fecha_reforma"] = formato_fecha(fecha_nueva)
            elif guardada and guardada > (ahora_.year, ahora_.month, ahora_.day):
                registro["fecha_reforma"] = None
        manifiesto[doc["id"]] = registro
        mensaje = ("idéntico al guardado" if resultado == "SIN_CAMBIO"
                   else f"es la misma reforma que el {guardado.suffix.lstrip('.').upper()} ya guardado: se conserva, "
                        f"con su fuente ({registro.get('url')})" if otro_formato
                   else "el servidor regeneró el archivo pero es la misma reforma")
    else:
        if guardado.exists():
            anterior = ANTERIORES / f"{doc['id']}{guardado.suffix}"
            anterior.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(guardado, anterior)
        reemplazar_archivo(candidato, destino)
        registro.update({
            "archivo": ruta_relativa_pdf(doc, formato), "formato": formato,
            "sha256": huella_nueva, "bytes": tam_nuevo, "url": url,
            "fecha_reforma": formato_fecha(fecha_nueva) if fecha_nueva else None,
            "fecha_descarga": hoy(), "ultima_verificacion": hoy(),
        })
        registro.setdefault("markdown", None)
        registro.pop("compresion", None)      # es un documento oficial nuevo, sin comprimir
        manifiesto[doc["id"]] = registro
        mensaje = f"guardado en {relativa(destino)}"
        if otro_formato:
            mensaje += f" (reemplaza al {guardado.suffix.lstrip('.').upper()} anterior)"

    if marca_nombre:
        registro["alarma_confirmada"] = marca_nombre
    else:
        registro.pop("alarma_confirmada", None)
    try:
        guardar_manifiesto(manifiesto)
    except OSError:
        if resultado in ("NUEVO", "ACTUALIZADO"):   # que el documento no quede sin su registro
            anterior = ANTERIORES / f"{doc['id']}{guardado.suffix}"
            if resultado == "ACTUALIZADO" and anterior.exists():
                reemplazar_archivo(anterior, guardado, conservar_origen=True)
                if otro_formato:
                    destino.unlink(missing_ok=True)
            elif resultado == "NUEVO":
                destino.unlink(missing_ok=True)
        raise
    if resultado == "ACTUALIZADO" and otro_formato:
        guardado.unlink(missing_ok=True)      # el registro ya apunta al nuevo: el de otro formato sobra
    if not url.startswith("archivo local") and registrar_pista(doc, url, "ok"):
        guardar_catalogo(catalogo)      # solo si la pista cambió: una verificación no reescribe el catálogo
    bitacora({"herramienta": "descargar", "id": doc["id"], "url": url, "resultado": resultado,
                    "sha256": huella_nueva, "fecha_reforma": formato_fecha(fecha_nueva) if fecha_nueva else None,
                    "formato": formato})
    print(f"RESULTADO: {resultado} ({mensaje})")
    salida.poner(resultado=resultado, detalle=mensaje, archivo=registro.get("archivo"),
                 markdown_pendiente=resultado in ("NUEVO", "ACTUALIZADO") or not registro.get("markdown"))
    if resultado in ("NUEVO", "ACTUALIZADO"):
        # La conversión a Markdown es un proceso APARTE: al final se le pregunta al usuario.
        print("Markdown: pendiente (no se convierte aquí; se ofrece al usuario al terminar la actualización)")


def _fecha_de_registro(registro: dict):
    return leer_fecha(registro.get("fecha_reforma"))


def _dudoso(doc: dict, url: str, motivo: str) -> None:
    bitacora({"herramienta": "descargar", "id": doc["id"], "url": url, "resultado": "DUDOSO", "detalle": motivo})
    print(f"RESULTADO: DUDOSO (no se guardó nada): {motivo}")
    salida.poner(resultado="DUDOSO", motivo=motivo)
    sys.exit(2)
