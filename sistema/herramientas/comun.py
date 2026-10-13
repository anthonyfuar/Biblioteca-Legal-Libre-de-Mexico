"""
comun.py — Base común de las herramientas: rutas, arranque (consola y entorno de Python), salida --json, errores,
texto, temporales y procesos hijos, guardado atómico, bitácora y candado. Solo biblioteca estándar: se importa
con el Python del sistema antes de relanzarse con el del entorno.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

# ====================================================================================================
# Rutas y constantes. El proyecto (RAIZ) solo guarda el proyecto: lo instalado (entorno de Python, idioma español del
# OCR) y el candado viven en la carpeta de datos del usuario (LOCAL), y los temporales en la del sistema
# (TEMP_PROYECTO); ambas llevan una huella de la ruta del proyecto para que dos copias no se mezclen.
# ====================================================================================================
SISTEMA = Path(__file__).resolve().parent.parent   # lo que mantiene la biblioteca: código, pruebas y datos
RAIZ = SISTEMA.parent                 # la raíz del repositorio: biblioteca/, sistema/ y los manuales
ENTRADA = SISTEMA / "biblioteca.py"   # la única entrada de las herramientas
DOCUMENTOS = RAIZ / "biblioteca"           # LA BIBLIOTECA: las carpetas PDF/ y Markdown/, el catálogo y el manifiesto
CARPETA_PDF = "PDF"                        # biblioteca/PDF/<grupo>/: cada documento oficial (PDF, o Word si no hay PDF)
CARPETA_MARKDOWN = "Markdown"              # biblioteca/Markdown/<grupo>/: su Markdown, con la misma carpeta y nombre
CATALOGO = DOCUMENTOS / "catalogo.json"    # qué documentos debe tener la biblioteca + pistas para encontrarlos
MANIFIESTO = DOCUMENTOS / "manifiesto.json"  # huella (SHA-256), URL y fechas de cada documento guardado
FUENTES = SISTEMA / "fuentes.json"    # dónde buscar cada entidad (mapa de fuentes)
BITACORA = SISTEMA / "bitacora.jsonl"  # una línea por cada acción de las herramientas
TRABAJO = RAIZ / "trabajo"                 # archivos de inspección del AGENTE: solo durante un proceso
VERSION_CATALOGO = 3                       # esquema de biblioteca/catalogo.json (sistema/referencia/esquema.md)
VERSION_MANIFIESTO = 3                     # esquema de biblioteca/manifiesto.json
UMBRAL_COMPRIMIR_MB = 60                   # comprimir trabaja con los PDF que pasan de esto
LIMITE_GITHUB_MB = 100                     # GitHub rechaza archivos de este tamaño o más

_HUELLA_PROYECTO = hashlib.sha256(str(RAIZ).encode("utf-8")).hexdigest()[:10]


def _carpeta_local() -> Path:
    """Carpeta de datos del usuario para este proyecto (persiste entre procesos; no se publica)."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "biblioteca_legal_libre" / _HUELLA_PROYECTO


LOCAL = _carpeta_local()
VENV = LOCAL / "entorno"                   # entorno de Python con los paquetes (lo crea instalar)
TESSDATA = LOCAL / "tessdata"              # idioma español del OCR, si el sistema no lo traía
ENTORNO = LOCAL / "entorno.json"           # lo que encontró/instaló instalar
BLOQUEO = LOCAL / "candado"                # archivo del candado (nunca se borra: el sistema suelta el candado)

# Temporales de las HERRAMIENTAS: nunca dentro del proyecto; se borran solos al terminar cada ejecución.
TEMP_PROYECTO = Path(tempfile.gettempdir()) / "biblioteca_legal_libre" / _HUELLA_PROYECTO
ANTERIORES = TEMP_PROYECTO / "anteriores"  # copia del documento reemplazado, solo hasta el cierre del proceso
CACHE = TEMP_PROYECTO / "cache"            # índice de Diputados (se reutiliza unas horas)
CACHE_VIGENCIA_SEG = 6 * 3600              # después de esto la caché ya no se usa y se borra
HUERFANOS_HORAS = 12                       # red de seguridad para temporales de procesos muertos


def relativa(ruta: Path) -> str:
    """Ruta relativa a la carpeta del proyecto, con '/' (para mostrar)."""
    ruta = ruta.resolve()
    try:
        return ruta.relative_to(RAIZ).as_posix()
    except ValueError:
        return str(ruta)


# ====================================================================================================
# Lo que depende del sistema operativo: saber si un pid sigue vivo (Windows sin os.kill), meter a los hijos en un
# Job Object de Windows (mueren con la herramienta) y el umask (permisos de los archivos nuevos).
# ====================================================================================================
def pid_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # En Windows NO se usa os.kill(pid, 0): terminaría el proceso.
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        manejador = kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not manejador:
            return ctypes.get_last_error() == 5  # type: ignore[attr-defined]                    # 5 = acceso denegado (existe); 87 = no existe
        try:
            codigo = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(manejador, ctypes.byref(codigo)):
                return True
            return codigo.value == 259                             # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(manejador)
    try:
        os.kill(pid, 0)                                            # POSIX: la señal 0 solo pregunta
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_TRABAJO_WIN = None


def meter_en_trabajo(p: subprocess.Popen) -> None:
    """Windows: mete al hijo en un Job Object con KILL_ON_JOB_CLOSE. Si la herramienta muere de golpe
    (taskkill /F, Administrador de tareas), el sistema cierra el Job y mata también a los hijos."""
    global _TRABAJO_WIN
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        if _TRABAJO_WIN is None:
            class _Basica(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _Extendida(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basica), ("IoInfo", ctypes.c_uint64 * 6),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
            trabajo = k32.CreateJobObjectW(None, None)
            info = _Extendida()
            info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not trabajo or not k32.SetInformationJobObject(trabajo, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return
            _TRABAJO_WIN = trabajo
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject(_TRABAJO_WIN, int(p._handle))  # type: ignore[attr-defined]
    except (OSError, AttributeError, ValueError):
        pass


def umask() -> int:
    actual = os.umask(0)
    os.umask(actual)
    return actual


# ====================================================================================================
# Salida de las herramientas para agentes: con `--json`, stdout lleva un solo objeto JSON (resultado, código de
# salida y los datos que cada herramienta aporta) y todo el texto humano se desvía a stderr. Sin `--json` no
# cambia nada. El JSON se emite al terminar, también si la herramienta sale con sys.exit (lo hace
# ejecutar), así los agentes nunca tienen que interpretar texto.
#
# Uso en una herramienta:
#     salida.iniciar(args.json, "descargar")      # tras leer los argumentos
#     salida.poner(resultado="NUEVO", sha256=...)  # en los puntos clave
# ====================================================================================================
class _Salida:
    """Estado del modo --json. Uso en una herramienta: salida.iniciar(args.json, "descargar") tras leer los argumentos
    y salida.poner(resultado="NUEVO", ...) en los puntos clave; ejecutar() llama salida.cerrar(código) al terminar."""

    def __init__(self) -> None:
        self._actual: dict[str, Any] | None = None
        self._stdout_real: Any = None

    def iniciar(self, activar: bool, herramienta: str) -> None:
        """Activa el modo JSON (si `activar`): guarda el stdout real y manda el texto humano a stderr."""
        if not activar:
            return
        self._actual = {"herramienta": herramienta}
        self._stdout_real = sys.stdout
        sys.stdout = sys.stderr

    def activo(self) -> bool:
        return self._actual is not None

    def poner(self, **campos: Any) -> None:
        """Agrega datos al JSON de salida (no hace nada sin --json)."""
        if self._actual is not None:
            self._actual.update(campos)

    def agregar(self, lista: str, elemento: Any) -> None:
        """Agrega un elemento a una lista del JSON de salida (p. ej. un documento de un lote)."""
        if self._actual is not None:
            self._actual.setdefault(lista, []).append(elemento)

    def cerrar(self, codigo: int) -> None:
        """Emite el JSON por el stdout real, con el código de salida (lo llama ejecutar al terminar)."""
        if self._actual is None:
            return
        datos = {"ok": codigo == 0, "codigo": codigo, **self._actual}
        self._actual = None
        print(json.dumps(datos, ensure_ascii=False, default=str), file=self._stdout_real, flush=True)


salida = _Salida()


# ====================================================================================================
# Cómo terminan las herramientas cuando algo está mal: mensaje ERROR por stderr y código de salida claro
# (1 = error, 64 = uso incorrecto, nunca 2, que es DUDOSO).
# ====================================================================================================
def salir_con_error(mensaje: str, codigo: int = 1) -> NoReturn:
    print(f"ERROR: {mensaje}", file=sys.stderr, flush=True)
    salida.poner(error=mensaje)
    sys.exit(codigo)


CODIGO_USO = 64     # error de uso (opción mal escrita, combinación inválida): EX_USAGE, distinto de DUDOSO (2)


class Analizador(argparse.ArgumentParser):
    """ArgumentParser de las herramientas: un error de uso sale con código 64 y mensaje en stderr, para
    que nunca se confunda con DUDOSO (2) ni con ERROR (1). Sin abreviaturas de opciones (`--pu` no es `--push`):
    así los permisos por texto del comando (`.claude/settings.json`) no se pueden esquivar."""
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        self.print_usage(sys.stderr)
        salida.poner(error=f"uso: {message}")
        self.exit(CODIGO_USO, f"ERROR de uso: {message}\n")


def json_tambien_al_final(subcomandos) -> None:
    """Que `--json` funcione también después del subcomando (`catalogo resumen --json`), no solo antes
    (`catalogo --json resumen`). SUPPRESS: si no se escribe ahí, no pisa el valor de antes del subcomando."""
    for sub in subcomandos.choices.values():
        sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                         help="salida en JSON por stdout (el texto va a stderr)")


# ====================================================================================================
# Textos y nombres: normalización (funciones puras, sin dependencias ni estado).
#
# Las usan comun.py (que las reexporta), fechas.py y las herramientas. Separadas de comun.py para poder
# probarlas e importarlas sin arrastrar el arranque de las herramientas (venv, señales, temporales).
# ====================================================================================================
def sin_acentos(texto: str) -> str:
    """'Código' -> 'Codigo'. También convierte ñ en n (es el criterio de todo el proyecto: los nombres de
    archivo y las comparaciones de nombres no distinguen ñ de n)."""
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y con espacios simples (para comparar textos)."""
    texto = sin_acentos(texto or "").lower()
    texto = re.sub(r"[^a-z0-9 ]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def nombre_archivo(nombre: str) -> str:
    """'Código Fiscal de la Federación' -> 'Codigo_Fiscal_de_la_Federacion'."""
    texto = sin_acentos(nombre).replace("°", "o").replace("º", "o").replace("ª", "a")
    texto = re.sub(r"[^A-Za-z0-9 ]+", " ", texto)
    return re.sub(r"\s+", "_", texto.strip())


PALABRAS_VACIAS = frozenset({"de", "la", "el", "los", "las", "del", "y", "para", "en", "a", "al",
                             "por", "e", "o", "sus", "su", "con"})


def palabras_clave(texto: str) -> set[str]:
    """Las palabras con significado de un nombre (sin artículos ni preposiciones, de 3+ letras)."""
    return {p for p in normalizar(texto).split() if p not in PALABRAS_VACIAS and len(p) > 2}


def secuencia(texto: str) -> list[str]:
    """Como palabras_clave, pero en orden y con repeticiones (para buscar un nombre completo)."""
    return [p for p in normalizar(texto).split() if p not in PALABRAS_VACIAS and len(p) > 2]


# ====================================================================================================
# Arranque de las herramientas: consola UTF-8, entorno de Python del proyecto (mismo proceso o relanzar con su Python),
# señales de Windows y ruido de pdfminer. Ningún efecto ocurre al importar: todo lo hace preparar(), que llama
# ejecutar(). Para no usar el entorno: variable de entorno BIBLIOTECA_SIN_ENTORNO=1.
# ====================================================================================================
_PREPARADO = False


def consola_utf8() -> None:
    """Consola en UTF-8 con saltos \n (Windows: acentos, ✔ y sin \r\n en los ids). También para instalar, que corre
    sin preparar()."""
    for flujo in (sys.stdout, sys.stderr):
        reconfigurar = getattr(flujo, "reconfigure", None)
        if reconfigurar:
            try:
                reconfigurar(encoding="utf-8", newline="\n")
            except ValueError:
                pass


def preparar() -> None:
    """Lo que necesita cualquier herramienta ANTES de trabajar (lo llama biblioteca.py antes de importarla, nunca un
    import): consola en UTF-8 con saltos \n, el entorno de Python del proyecto (o relanzarse con su Python),
    Ctrl+Break limpio en Windows y pdfminer sin avisos en inglés. Solo la primera vez."""
    global _PREPARADO
    if _PREPARADO:
        return
    _PREPARADO = True
    consola_utf8()
    _usar_venv()
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, lambda *_: sys.exit(130))   # cierre de consola: salida limpia
        except (ValueError, OSError):   # no estamos en el hilo principal
            pass
    logging.getLogger("pdfminer").setLevel(logging.ERROR)


def _version_de_venv() -> tuple[int, int] | None:
    try:
        texto = (VENV / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^\s*version(?:_info)?\s*=\s*(\d+)\.(\d+)", texto, re.M)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _paquetes_de_venv() -> Path | None:
    if os.name == "nt":
        carpeta = VENV / "Lib" / "site-packages"
        return carpeta if carpeta.is_dir() else None
    candidatas = sorted((VENV / "lib").glob("python*/site-packages"))
    return candidatas[0] if candidatas else None


def _usar_venv() -> None:
    if os.environ.get("BIBLIOTECA_SIN_ENTORNO") or os.environ.get("BIBLIOTECA_EN_ENTORNO"):
        return
    python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists() or Path(sys.prefix).resolve() == VENV.resolve():
        return
    paquetes = _paquetes_de_venv()
    if paquetes and _version_de_venv() == tuple(sys.version_info[:2]):
        import site
        antes = list(sys.path)
        site.addsitedir(str(paquetes))
        nuevos = [p for p in sys.path if p not in antes]
        # primero la carpeta del proyecto, luego el entorno, luego lo del sistema
        sys.path[:] = antes[:1] + nuevos + antes[1:]
        return
    principal = sys.modules.get("__main__")
    guion = getattr(principal, "__file__", None)
    if not guion:          # importado desde una consola interactiva: no relanzar
        return
    entorno = dict(os.environ, BIBLIOTECA_EN_ENTORNO="1", PYTHONDONTWRITEBYTECODE="1")   # sin __pycache__
    try:
        r = subprocess.run([str(python), os.path.abspath(guion), *sys.argv[1:]], env=entorno, check=False)
    except KeyboardInterrupt:
        sys.exit(130)
    sys.exit(r.returncode)


# ====================================================================================================
# Temporales por ejecución (fuera del proyecto, se borran solos, huérfanos por pid), procesos hijos registrados
# (se matan al salir), limpieza al cerrar y el punto de entrada común ejecutar().
# ====================================================================================================
_TEMPORAL_PROCESO: Path | None = None
_EJECUCION = re.compile(r"ejecucion_(\d+)_")


def temporal() -> Path:
    """Carpeta temporal de ESTA ejecución. Se crea al primer uso y se borra al terminar."""
    global _TEMPORAL_PROCESO
    if _TEMPORAL_PROCESO is None:
        borrar_huerfanos()
        for _ in range(5):   # otro agente pudo borrar en ese instante la carpeta padre (estaba vacía)
            try:
                TEMP_PROYECTO.mkdir(parents=True, exist_ok=True)
                _TEMPORAL_PROCESO = Path(tempfile.mkdtemp(prefix=f"ejecucion_{os.getpid()}_", dir=TEMP_PROYECTO))
                break
            except FileNotFoundError:
                time.sleep(0.1)
        else:
            _TEMPORAL_PROCESO = Path(tempfile.mkdtemp(prefix=f"biblioteca_legal_libre_{os.getpid()}_"))
        atexit.register(_limpiar_al_salir)
    return _TEMPORAL_PROCESO


def _quitar_vacias() -> None:
    """Quita las carpetas temporales del proyecto que quedaron vacías (nunca borra contenido)."""
    for carpeta in (CACHE, TEMP_PROYECTO, TEMP_PROYECTO.parent):
        try:
            carpeta.rmdir()
        except OSError:
            pass


_DETENER = threading.Event()          # avisa a las descargas en curso que deben parar
_HIJOS: set = set()                   # procesos hijos vivos (curl, conversiones): se detienen al salir
_HIJOS_LOCK = threading.Lock()


def correr_hijo(cmd: list[str], timeout: float, **kwargs) -> subprocess.CompletedProcess:
    """Como subprocess.run(capture_output=True, text=True), pero el hijo queda registrado: si la
    herramienta termina (error, Ctrl+C), el hijo se detiene y no sigue escribiendo por su cuenta. En POSIX el hijo
    encabeza su propio grupo de procesos, para detener también lo que él lanzó (ver _matar)."""
    if _DETENER.is_set():
        raise InterruptedError("la herramienta está terminando: no se lanzan más procesos")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", start_new_session=os.name != "nt", **kwargs)
    meter_en_trabajo(p)
    with _HIJOS_LOCK:
        _HIJOS.add(p)
    if _DETENER.is_set():      # la interrupción llegó mientras se lanzaba: matar_hijos pudo no verlo
        _matar(p)
    try:
        salida, errores = p.communicate(timeout=timeout)
    except BaseException:      # tope vencido, Ctrl+C o la herramienta que termina: nada sigue trabajando solo
        _matar(p)
        try:
            p.communicate(timeout=30)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    finally:
        with _HIJOS_LOCK:
            _HIJOS.discard(p)
    return subprocess.CompletedProcess(cmd, p.returncode, salida, errores)


def _matar(p: subprocess.Popen) -> None:
    """Detiene al hijo y a todo lo que lanzó. Matar solo al hijo no basta: `git push`, por ejemplo, deja subiendo a
    git-remote-https y pack-objects, que terminarían la subida por su cuenta y tendrían abiertas las tuberías. En
    POSIX se mata el grupo de procesos del hijo; en Windows, al hijo (el Job Object detiene al resto al salir)."""
    try:
        if os.name == "nt":
            p.kill()
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


def matar_hijos() -> None:
    _DETENER.set()             # ningún hilo lanza procesos nuevos a partir de aquí
    with _HIJOS_LOCK:
        hijos = list(_HIJOS)
    for p in hijos:
        _matar(p)


atexit.register(matar_hijos)


def _limpiar_al_salir() -> None:
    _DETENER.set()
    matar_hijos()
    if _TEMPORAL_PROCESO is not None:
        for _ in range(10):   # en Windows un archivo abierto no se puede borrar: se reintenta un momento
            shutil.rmtree(_TEMPORAL_PROCESO, ignore_errors=True)
            if not _TEMPORAL_PROCESO.exists():
                break
            time.sleep(0.2)
    _quitar_vacias()


def _viejo(ruta: Path, minutos: float = 10) -> bool:
    try:
        return time.time() - ruta.stat().st_mtime > minutos * 60
    except OSError:
        return False


def borrar_huerfanos() -> int:
    """Borra temporales que ya nadie usa: carpetas de ejecuciones cuyo proceso murió (o con más de
    HUERFANOS_HORAS) y cachés caducadas. Nunca toca las de procesos vivos. Devuelve cuántos borró."""
    if not TEMP_PROYECTO.exists():
        return 0
    borrados = 0
    for carpeta in TEMP_PROYECTO.glob("ejecucion_*"):
        if carpeta == _TEMPORAL_PROCESO:
            continue
        m = _EJECUCION.match(carpeta.name)
        muerta = not m or not pid_vivo(int(m.group(1))) or _viejo(carpeta, HUERFANOS_HORAS * 60)
        if muerta:
            shutil.rmtree(carpeta, ignore_errors=True)
            borrados += not carpeta.exists()
    if CACHE.exists():
        for archivo in CACHE.iterdir():
            if archivo.is_file() and _viejo(archivo, CACHE_VIGENCIA_SEG / 60):
                try:
                    archivo.unlink()
                    borrados += 1
                except OSError:
                    pass
    _quitar_vacias()
    return borrados


def contar_archivos(carpeta: Path) -> int:
    return sum(1 for f in carpeta.rglob("*") if f.is_file()) if carpeta.exists() else 0


# Lo que debe haber en la raíz del repositorio (lo demás se avisa: suelen ser restos de un agente)
_RAIZ_ESPERADA = {"README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "biblioteca", "sistema", "trabajo", ".agents",
                  ".claude", ".git", ".gitignore", ".gitattributes", ".github",
                  ".vscode", ".idea", ".cursor", ".venv"}    # editores y un entorno propio: de la persona, no se avisan
# Lo que debe haber en sistema/
_SISTEMA_ESPERADO = {"biblioteca.py", "herramientas", "pruebas", "referencia", "fuentes.json", "bitacora.jsonl",
                          "CAMBIOS.md", "requirements.txt", "pyproject.toml"}
# Archivos que crean el Finder y el Explorador por su cuenta: no son del proyecto; --limpiar los borra
BASURA_SO = {".DS_Store", "Thumbs.db", "desktop.ini"}
# Cachés de las herramientas de desarrollo si se corren a mano (biblioteca.py pruebas las deja fuera); --limpiar las borra
CACHES_DESARROLLO = {".ruff_cache", ".mypy_cache", ".pytest_cache"}
_NO_REVISAR = {".git", ".venv"}


def _recorrer(raiz: Path):
    """Como os.walk, sin entrar en .git/ ni en un .venv/ creado a mano (el entorno de las herramientas vive fuera)."""
    for carpeta, subcarpetas, archivos in os.walk(raiz):
        subcarpetas[:] = [s for s in subcarpetas if not (Path(carpeta) == raiz and s in _NO_REVISAR)]
        yield Path(carpeta), subcarpetas, archivos


def temporales_en_proyecto(todos: bool = False) -> list[Path]:
    """Lo temporal que NO debe quedar en el proyecto al terminar un proceso: archivos de trabajo del agente
    (trabajo/), restos de escrituras interrumpidas (*.tmp) y cachés de Python (__pycache__). Los *.tmp recientes se
    omiten salvo con todos=True (pueden ser una escritura en curso)."""
    restos: list[Path] = []
    if TRABAJO.exists():
        restos += [f for f in TRABAJO.rglob("*") if f.is_file()] or [TRABAJO]
    for carpeta, subcarpetas, archivos in _recorrer(RAIZ):
        if "__pycache__" in subcarpetas:
            restos.append(carpeta / "__pycache__")
            subcarpetas.remove("__pycache__")
        for nombre in archivos:
            if nombre.endswith((".tmp", ".pyc")):
                ruta = carpeta / nombre
                if todos or _viejo(ruta):
                    restos.append(ruta)
    return restos


def inesperados() -> list[Path]:
    """Archivos o carpetas que no son parte del proyecto en su raíz ni en sistema/ (p. ej. un listado que el
    agente dejó ahí). No se borran solos: podrían ser de una persona."""
    return [p for carpeta, esperado in ((RAIZ, _RAIZ_ESPERADA), (SISTEMA, _SISTEMA_ESPERADO))
            for p in sorted(carpeta.iterdir())
            if p.name not in esperado and p.name not in BASURA_SO and p.name not in CACHES_DESARROLLO]


def basura() -> list[Path]:
    """Lo que el sistema operativo o las herramientas de desarrollo crean por su cuenta en el proyecto (.DS_Store,
    Thumbs.db, .ruff_cache/...): no es un error, pero --limpiar lo borra para que la carpeta quede limpia."""
    restos = [base / c for base in (RAIZ, SISTEMA) for c in CACHES_DESARROLLO if (base / c).exists()]
    for carpeta, _subcarpetas, archivos in _recorrer(RAIZ):
        restos += [carpeta / a for a in archivos if a in BASURA_SO]
    return restos


def _borrar(ruta: Path) -> bool:
    if ruta.is_dir() and not ruta.is_symlink():
        shutil.rmtree(ruta, ignore_errors=True)
    else:
        try:
            ruta.unlink(missing_ok=True)
        except OSError:
            pass
    return not ruta.exists()


def limpiar_temporales() -> tuple[int, int, list[Path]]:
    """Borra TODO lo temporal: lo del proyecto (temporales_en_proyecto) y la carpeta temporal del
    sistema de este proyecto (caché y ejecuciones). Úsese al TERMINAR un proceso (lo hace el agente
    principal, dentro del candado), no a la mitad: borraría lo de otros agentes que sigan trabajando.
    Devuelve (borrados en el proyecto, archivos borrados fuera, lo que NO se pudo borrar)."""
    borrados, fallidos = 0, []
    for resto in temporales_en_proyecto(todos=True):
        if _borrar(resto):
            borrados += 1
        else:
            fallidos.append(resto)
    for resto in basura():
        if _borrar(resto):
            borrados += 1
    # trabajo/ (del agente): al cerrar ya no hace falta. anteriores/ (la copia del documento que se reemplazó, solo
    # sirve DURANTE el proceso para deshacer si algo falla al guardar) se borra abajo con la carpeta temporal.
    if TRABAJO.exists():
        n = contar_archivos(TRABAJO)
        if _borrar(TRABAJO):
            borrados += n
        elif TRABAJO not in fallidos:
            fallidos.append(TRABAJO)
    # carpetas vacías que dejaron mover/renombrar/retirar-pdf dentro de biblioteca/
    if DOCUMENTOS.exists():
        for carpeta in sorted((c for c in DOCUMENTOS.rglob("*") if c.is_dir()), key=lambda c: -len(c.parts)):
            try:
                carpeta.rmdir()              # solo si está vacía; nunca borra contenido
            except OSError:
                pass
    antes = contar_archivos(TEMP_PROYECTO)
    for elemento in (list(TEMP_PROYECTO.iterdir()) if TEMP_PROYECTO.exists() else []):
        if elemento == _TEMPORAL_PROCESO:
            continue
        m = _EJECUCION.match(elemento.name)
        if m and pid_vivo(int(m.group(1))) and not _viejo(elemento, HUERFANOS_HORAS * 60):
            continue           # es de una herramienta que sigue trabajando: no se toca
        if not _borrar(elemento):
            fallidos.append(elemento)
    fuera = antes - contar_archivos(TEMP_PROYECTO)
    _quitar_vacias()
    return borrados, fuera, fallidos


def ejecutar(principal) -> None:
    """Punto de entrada común de las herramientas: borra temporales de procesos que murieron de golpe y
    convierte Ctrl+C (en cualquier momento) en una salida limpia con código 130."""
    try:
        borrar_huerfanos()
    except OSError:
        pass
    preparar()
    try:
        principal()
    except KeyboardInterrupt:
        _DETENER.set()
        matar_hijos()
        print("\nInterrumpido por el usuario.")
        salida.cerrar(130)
        sys.exit(130)
    except SystemExit as e:
        codigo = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        salida.cerrar(codigo)
        raise
    salida.cerrar(0)


# ====================================================================================================
# Lectura y escritura segura de los archivos de datos: JSON atómico (temporal + os.replace, siempre con saltos
# de línea LF, permisos conservados), catálogo, manifiesto y mapa de fuentes con su versión de esquema, bitácora, huella SHA-256 y
# reemplazo atómico de documentos.
# ====================================================================================================
def hoy() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def asegurar_carpetas() -> None:
    DOCUMENTOS.mkdir(parents=True, exist_ok=True)


def bitacora(evento: dict) -> None:
    """Agrega una línea a bitacora.jsonl (historial auditable)."""
    evento = {"fecha": ahora(), **evento}
    with open(BITACORA, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")


def leer_json(ruta: Path, defecto):
    for intento in range(100):      # Windows: falla si otro proceso lo está reemplazando en ese instante
        try:
            if not ruta.exists():
                return defecto
            crudo = ruta.read_bytes()   # el archivo se cierra antes de parsear
            break
        except (PermissionError, FileNotFoundError):
            if intento == 99:
                raise
            time.sleep(0.1)
    try:
        return json.loads(crudo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        salir_con_error(f"{relativa(ruta)} no es un JSON válido ({e}). Restáuralo del respaldo o de git; no lo edites a mano.")


def guardar_json(ruta: Path, datos) -> None:
    """Escritura atómica: primero a un temporal y luego se reemplaza.
    Si el proceso se interrumpe, el archivo anterior queda intacto."""
    texto = json.dumps(datos, ensure_ascii=False, indent=2) + "\n"
    guardar_bytes(ruta, texto.encode("utf-8"))   # siempre \n: los mismos bytes en Windows, macOS y Linux


def guardar_bytes(ruta: Path, contenido: bytes) -> None:
    """Escritura atómica de un archivo (ver guardar_json)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".guardando_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contenido)
        try:    # mkstemp crea el temporal solo legible por el dueño (0600): se conservan los permisos
            os.chmod(tmp, ruta.stat().st_mode & 0o777 if ruta.exists() else 0o666 & ~umask())
        except OSError:
            pass
        for intento in range(100):          # Windows: falla si otro proceso lo está leyendo en ese instante
            try:
                os.replace(tmp, ruta)
                break
            except PermissionError:
                if intento == 99:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------
# Catálogo, fuentes y manifiesto
# ---------------------------------------------------------------------------
def cargar_catalogo() -> dict:
    datos = leer_json(CATALOGO, None)
    if not datos or "documentos" not in datos:
        salir_con_error(f"No se encontró un catálogo válido en {CATALOGO}")
    if datos.get("version") != VERSION_CATALOGO:
        salir_con_error(f"{relativa(CATALOGO)} tiene el esquema {datos.get('version')!r}; esta versión de las herramientas "
                        f"usa el {VERSION_CATALOGO} (sistema/referencia/esquema.md). Actualiza las herramientas o "
                        "migra el catálogo.")
    return datos


def guardar_catalogo(catalogo: dict) -> None:
    catalogo["version"] = VERSION_CATALOGO
    catalogo["actualizado"] = hoy()
    guardar_json(CATALOGO, catalogo)


def cargar_fuentes() -> dict:
    return leer_json(FUENTES, {})


def cargar_manifiesto() -> dict:
    """Los registros del manifiesto (id -> registro). El archivo lleva versión y fecha; si no existe, vacío."""
    datos = leer_json(MANIFIESTO, None)
    if datos is None:
        return {}
    if "documentos" not in datos or datos.get("version") != VERSION_MANIFIESTO:
        salir_con_error(f"{relativa(MANIFIESTO)} tiene el esquema {datos.get('version')!r}; esta versión de las "
                        f"herramientas usa el {VERSION_MANIFIESTO} (sistema/referencia/esquema.md).")
    return datos["documentos"]


def guardar_manifiesto(manifiesto: dict) -> None:
    guardar_json(MANIFIESTO, {
        "descripcion": "Manifiesto de la Biblioteca Legal Libre de México: qué documento está guardado, de dónde salió, "
                       "su huella SHA-256, su fecha de reforma y el estado de su Markdown. Lo escriben solo las "
                       "herramientas de biblioteca.py (esquema: sistema/referencia/esquema.md).",
        "version": VERSION_MANIFIESTO, "actualizado": hoy(), "documentos": dict(sorted(manifiesto.items())),
    })


def sha256_archivo(ruta: Path) -> str:
    """Huella SHA-256 de un archivo (la que guardan el manifiesto y la bitácora)."""
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def reemplazar_archivo(origen: Path, destino: Path, conservar_origen: bool = False) -> None:
    """Pone `origen` en `destino` de forma atómica: se copia a un *.tmp junto al destino y se renombra.
    Una interrupción nunca deja un documento a medias en la biblioteca. (Windows: reintenta si otro
    proceso está leyendo el destino en ese instante.)"""
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.parent / f".{destino.name}.guardando.tmp"
    shutil.copyfile(origen, tmp)
    for intento in range(100):
        try:
            os.replace(tmp, destino)
            break
        except PermissionError:
            if intento == 99:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)
    if not conservar_origen:
        Path(origen).unlink(missing_ok=True)


# ====================================================================================================
# Candado del sistema operativo sobre LOCAL/candado: varios agentes trabajan en paralelo y toda lectura-
# modificación-escritura del catálogo o del manifiesto va dentro de `with bloqueo():`. Si el dueño muere, el
# sistema lo suelta solo; el archivo nunca se borra (guarda el pid solo como dato).
# ====================================================================================================


def _dueno_del_bloqueo() -> int | None:
    try:
        m = re.match(r"pid (\d+)", BLOQUEO.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return int(m.group(1)) if m else None


_BYTE_CANDADO = 1 << 20   # se bloquea un byte lejos del texto: el pid (al inicio) siempre se puede leer


def _intentar_candado(fd: int) -> bool:
    """Candado del sistema operativo sobre el archivo del candado (el archivo NUNCA se borra).
    Si el dueño muere, el sistema lo suelta solo: no existen candados huérfanos."""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _BYTE_CANDADO, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _soltar_candado(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, _BYTE_CANDADO, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def bloqueo(espera_max_seg: int = 600):
    LOCAL.mkdir(parents=True, exist_ok=True)
    inicio, avisado = time.time(), False
    fd = os.open(BLOQUEO, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while not _intentar_candado(fd):
            espera = time.time() - inicio
            if espera > 5 and not avisado:
                print(f"Esperando el candado (lo tiene el proceso {_dueno_del_bloqueo() or '?'})...",
                      flush=True)
                avisado = True
            if espera > espera_max_seg:
                salir_con_error(f"El proceso {_dueno_del_bloqueo() or '?'} tiene ocupado {BLOQUEO} desde hace "
                                "mucho. Cierra esa herramienta (el candado se suelta solo cuando termina).")
            time.sleep(0.25)
        try:   # el pid es solo informativo (para el mensaje de espera)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"pid {os.getpid()} desde {ahora()}".ljust(64).encode())
        except OSError:
            pass
        try:
            yield
        finally:
            _soltar_candado(fd)
    finally:
        os.close(fd)
