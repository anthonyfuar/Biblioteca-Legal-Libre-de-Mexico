"""
instalar.py — PROCESO 1, INSTALAR: la herramienta `instalar`. Solo biblioteca estándar: corre antes de que exista el entorno.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from herramientas.comun import ENTORNO, LOCAL, SISTEMA, TESSDATA, VENV, Analizador, salida

# ====================================================================================================
# Herramienta: instalar
# ====================================================================================================
AYUDA_INSTALAR = """
instalar — Detecta el sistema operativo y las dependencias que faltan, e instala SOLO lo necesario.

Funciona igual en Windows, macOS y Linux. Solo usa la biblioteca estándar de Python, así que puede
ejecutarse en una computadora recién instalada. Lo instalado vive FUERA de la carpeta del proyecto, en la
carpeta de datos del usuario (macOS: ~/Library/Caches/biblioteca_legal_libre/<huella>; Windows:
%LOCALAPPDATA%\\biblioteca_legal_libre\\<huella>; Linux: ~/.cache/biblioteca_legal_libre/<huella>).

USO:
  python sistema/biblioteca.py instalar              diagnostica e instala TODO (perfil completo, con OCR)
  python sistema/biblioteca.py instalar --revisar    solo diagnostica (no instala nada)
  python sistema/biblioteca.py instalar --para actualizar | comprimir | convertir | todo | desarrollo
                                          instala solo lo que necesita ese proceso (secciones de
                                          requirements.txt): ACTUALIZAR ≈ 25 MB (requests, pdfminer,
                                          olefile...), COMPRIMIR añade pikepdf/numpy/Pillow, CONVERTIR y todo
                                          añaden markitdown y el OCR; desarrollo, además, pytest/ruff/mypy
  python sistema/biblioteca.py instalar --sin-ocr    no instalar Tesseract (solo hace falta para PDFs escaneados);
                                          si ya está instalado con español, lo detecta y da código 0
  python sistema/biblioteca.py instalar --quitar     utilidad manual (NO es parte del cierre de los procesos): borra
                                          el entorno, el idioma español descargado y entorno.json. El
                                          siguiente proceso lo reinstala solo. No toca programas del
                                          sistema (Python, Tesseract, curl).

QUÉ REVISA (en orden):
  1. Python 3.10 o superior      -> lo exige markitdown; si falta, lo dice y no sigue (ver README.md).
  2. Entorno de Python propio     -> así no se toca el Python del sistema y se evita el error
                                    "externally-managed-environment" de Ubuntu/Debian recientes.
  3. Paquetes del perfil         -> las secciones de requirements.txt que pide --para.
  4. Tesseract OCR               -> winget (Windows), brew (macOS), apt/dnf/pacman (Linux).
  5. Idioma español 'spa' de Tesseract -> si el sistema no lo trae, se descarga el archivo oficial a la
                                    carpeta de datos (no requiere permisos de administrador).
  6. curl (opcional)             -> respaldo para portales con certificados incompletos.

Las demás herramientas usan el entorno automáticamente: basta con escribir "python sistema/biblioteca.py <herramienta>".

CÓDIGOS DE SALIDA:
  0 = todo listo (actualizar y convertir, incluido OCR)
  1 = falta algo esencial (no se puede actualizar ni convertir)
  2 = listo para actualizar y convertir, pero SIN OCR (los PDFs escaneados no se podrán convertir)
"""


REQUISITOS = SISTEMA / "requirements.txt"     # una sección '# [nombre]' por perfil
PERFILES = {"actualizar": ["actualizar"], "comprimir": ["actualizar", "comprimir"],
            "convertir": ["actualizar", "comprimir", "convertir"], "todo": ["actualizar", "comprimir", "convertir"],
            "desarrollo": ["actualizar", "comprimir", "convertir", "desarrollo"]}
TESSDATA_LOCAL = TESSDATA
URL_SPA = "https://github.com/tesseract-ocr/tessdata/raw/main/spa.traineddata"   # repositorio oficial
# Qué módulo(s) de Python aporta cada paquete cuando su nombre no coincide con el del paquete
# (la ÚNICA lista de dependencias es requirements.txt; de ahí salen los módulos que se comprueban).
_MODULOS_DE_PAQUETE = {"markitdown": ["markitdown"], "pdfminer.six": ["pdfminer"],
                       "pillow": ["PIL"]}


def paquetes_de(perfil: str) -> list[str]:
    """Las líneas de requirements.txt (p. ej. 'requests>=2.32.5') de las secciones del perfil."""
    paquetes: list[str] = []
    seccion = None
    for linea in (REQUISITOS.read_text(encoding="utf-8").splitlines() if REQUISITOS.exists() else []):
        m = re.match(r"#\s*\[(\w+)\]", linea.strip())
        if m:
            seccion = m.group(1)
            continue
        linea = linea.split("#", 1)[0].strip()
        if linea and seccion in PERFILES[perfil]:
            paquetes.append(linea)
    return paquetes


def modulos_de(perfil: str) -> list[str]:
    """Módulos de Python que exige el perfil (los que aporta cada paquete de sus secciones)."""
    modulos: list[str] = []
    for linea in paquetes_de(perfil):
        paquete = linea.split("[", 1)[0]
        for sep in ("==", ">=", "<=", "~=", "!=", "<", ">"):
            paquete = paquete.split(sep, 1)[0]
        for m in _MODULOS_DE_PAQUETE.get(paquete.strip().lower(), [paquete.strip().replace("-", "_")]):
            if m not in modulos:
                modulos.append(m)
    return modulos
RUTAS_TESSERACT_WINDOWS = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                           r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]

def paso(dep: str, est: str, detalle: str = "") -> None:
    salida.agregar("pasos", {"dependencia": dep, "estado": est, "detalle": detalle})
    marca = {"OK": "✔", "INSTALADO": "✔", "FALTA": "✘", "AVISO": "!", "MANUAL": "→"}.get(est, "·")
    print(f"  {marca} {dep}: {est}{' — ' + detalle if detalle else ''}")


def correr(cmd: list[str], mostrar: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    if mostrar:
        print(f"    $ {' '.join(cmd)}")
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          check=False)


# ---------------------------------------------------------------------------
# Sistema operativo
# ---------------------------------------------------------------------------
def sistema() -> dict:
    so = platform.system()   # Windows | Darwin | Linux
    info = {"so": {"Darwin": "macOS"}.get(so, so), "version": platform.release(), "gestor": None}
    if so == "Windows" and shutil.which("winget"):
        info["gestor"] = "winget"
    elif so == "Darwin" and shutil.which("brew"):
        info["gestor"] = "brew"
    elif so == "Linux":
        for g in ("apt-get", "dnf", "pacman", "zypper"):
            if shutil.which(g):
                info["gestor"] = g
                break
    return info


def prefijo_admin() -> list[str] | None:
    """Linux: [] si somos root, ['sudo','-n'] si sudo no pide contraseña, None si hace falta la del usuario."""
    if not hasattr(os, "geteuid"):
        return []
    if os.geteuid() == 0:
        return []
    try:
        if shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=30,
                                                   check=False).returncode == 0:
            return ["sudo", "-n"]
    except subprocess.TimeoutExpired:
        pass
    return None


def instalar_paquetes_sistema(info: dict, paquetes: dict, descripcion: str, revisar: bool,
                              manual: str = "ver README.md") -> bool:
    """paquetes: {gestor: [comando...]}. Devuelve True si se instaló."""
    gestor = info["gestor"]
    if not gestor or gestor not in paquetes:
        paso(descripcion, "MANUAL", f"no se encontró un gestor de paquetes compatible; {manual}")
        return False
    cmd: list[str] = paquetes[gestor]
    pre: list[str] | None = []
    if info["so"] == "Linux":
        pre = prefijo_admin()
        if pre is None:
            paso(descripcion, "MANUAL", "requiere tu contraseña de administrador. Ejecuta tú: sudo " + " ".join(cmd))
            return False
        cmd = pre + cmd
    if revisar:
        paso(descripcion, "FALTA", "se instalaría con: " + " ".join(cmd))
        return False
    if gestor == "apt-get":
        correr([*(pre or []), "apt-get", "update"])   # sin esto apt puede no encontrar el paquete
    if info["so"] == "Windows":
        print("    (Windows puede mostrar una ventana pidiendo permiso: acéptala)")
    r = correr(cmd, timeout=3600)
    if r.returncode != 0:
        paso(descripcion, "FALTA", f"falló la instalación: {(r.stderr or r.stdout).strip()[-300:]}")
        return False
    return True


# ---------------------------------------------------------------------------
# 1. Python
# ---------------------------------------------------------------------------
def revisar_python() -> bool:
    v = sys.version_info
    remedio = (f"brew reinstall --build-from-source python@{v.major}.{v.minor} && brew pin python@{v.major}.{v.minor}"
               if platform.system() == "Darwin" else "reinstala Python desde python.org o con tu gestor de paquetes")
    try:
        import pyexpat  # noqa: F401 - lo necesitan plistlib, xml.etree, pip y markitdown
    except ImportError as e:
        paso("Python", "FALTA", f"este Python no puede cargar pyexpat ({str(e)[:90]}). En macOS con Homebrew pasa "
                                f"cuando el paquete viene enlazado a un libexpat más nuevo que el del sistema; "
                                f"remedio: {remedio}")
        return False
    if platform.system() == "Darwin" and not platform.mac_ver()[0]:
        paso("Python", "FALTA", f"este Python no reconoce la versión de macOS (platform.mac_ver() vacío) y pip no "
                                f"funcionará; remedio: {remedio}")
        return False
    if v < (3, 10):
        otro = mejor_python()
        version_venv = _version_de(python_venv()) if python_venv().exists() else None
        if version_venv and version_venv >= (3, 10):
            paso("Python", "OK", f"{v.major}.{v.minor} aquí, pero el entorno tiene {version_venv[0]}.{version_venv[1]} y las "
                                 "herramientas lo usan solas")
            return True
        if otro != sys.executable:
            paso("Python", "OK", f"{v.major}.{v.minor} aquí; el entorno se creará con {otro}")
            return True
        paso("Python", "FALTA", f"tienes {v.major}.{v.minor}; se necesita 3.10 o superior (lo exige markitdown). "
                                "macOS: brew install python@3.12 · Windows: winget install -e --id Python.Python.3.12 · "
                                "Debian/Ubuntu: sudo apt-get install -y python3 python3-venv python3-pip")
        return False
    paso("Python", "OK", f"{v.major}.{v.minor}.{v.micro}")
    return True


# ---------------------------------------------------------------------------
# 2 y 3. Entorno virtual y paquetes
# ---------------------------------------------------------------------------
def python_venv() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _version_de(python: str | Path) -> tuple[int, int] | None:
    """(mayor, menor) del intérprete, o None si no corre."""
    try:
        r = subprocess.run([str(python), "-c", "import sys;print(sys.version_info[0], sys.version_info[1])"],
                           capture_output=True, text=True, timeout=30, check=False)
        mayor, menor = r.stdout.split()
        return int(mayor), int(menor)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def mejor_python() -> str:
    """El intérprete con el que se crea el entorno: este mismo si es 3.10+; si no, el 3.10+ más nuevo del PATH
    (python3.13, python3.12...). Así `python3 sistema/biblioteca.py instalar` funciona aunque el python3 del
    sistema sea viejo."""
    if sys.version_info >= (3, 10):  # este módulo corre también con Pythons viejos, para avisar
        return sys.executable
    for nombre in ("python3.14", "python3.13", "python3.12", "python3.11", "python3.10", "python3", "python", "py"):
        ruta = shutil.which(nombre)
        if ruta and (_version_de(ruta) or (0, 0)) >= (3, 10):
            return ruta
    return sys.executable


def revisar_venv(info: dict, revisar: bool) -> bool:
    if python_venv().exists():
        paso("Entorno de Python", "OK", str(VENV))
        return True
    if revisar:
        paso("Entorno de Python", "FALTA", f"se crearía con: python -m venv {VENV}")
        return False
    r = correr([mejor_python(), "-m", "venv", str(VENV)])
    if r.returncode != 0 and "ensurepip" in (r.stderr or "") + (r.stdout or ""):
        shutil.rmtree(VENV, ignore_errors=True)
        paso("Entorno de Python", "FALTA", "falló ensurepip al crear el entorno (pip no se pudo instalar dentro). Suele ser un "
                                               "Python incompleto: en macOS con Homebrew, brew reinstall --build-from-source "
                                               "python@3.12; en Debian/Ubuntu, apt-get install python3-venv")
        return False
    if r.returncode != 0 and info["so"] == "Linux":
        # Debian/Ubuntu separan el módulo venv en otro paquete
        version = f"python{sys.version_info.major}.{sys.version_info.minor}-venv"
        instalado = instalar_paquetes_sistema(info, {"apt-get": ["apt-get", "install", "-y", version, "python3-venv"]},
                                              "Módulo venv de Python", revisar)
        if instalado:
            shutil.rmtree(VENV, ignore_errors=True)
            r = correr([mejor_python(), "-m", "venv", str(VENV)])
    if r.returncode != 0 or not python_venv().exists():
        paso("Entorno de Python", "FALTA", (r.stderr or r.stdout).strip()[-300:])
        return False
    paso("Entorno de Python", "INSTALADO", str(VENV))
    return True


def modulos_faltantes(modulos: list[str]) -> list[str]:
    """Un solo subproceso prueba todos los módulos (markitdown tarda unos segundos en importarse)."""
    prueba = "import importlib,sys\n" + "\n".join(
        f"try: importlib.import_module({m!r})\nexcept Exception: print({m!r})" for m in modulos)
    try:
        r = subprocess.run([str(python_venv()), "-c", prueba], capture_output=True, text=True, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        return list(modulos)
    return [m for m in (r.stdout or "").split() if m in modulos] if r.returncode == 0 else list(modulos)


def revisar_paquetes(revisar: bool, perfil: str) -> bool:
    modulos = modulos_de(perfil)
    if not python_venv().exists():
        paso(f"Paquetes de Python ({perfil})", "FALTA", "primero hace falta el entorno virtual")
        return False
    faltan = modulos_faltantes(modulos)
    if not faltan:
        paso(f"Paquetes de Python ({perfil})", "OK", ", ".join(modulos))
        return True
    if revisar:
        paso(f"Paquetes de Python ({perfil})", "FALTA",
             f"faltan {', '.join(faltan)}; se instalarían de requirements.txt ({', '.join(PERFILES[perfil])})")
        return False
    correr([str(python_venv()), "-m", "pip", "install", "--upgrade", "pip"])
    r = correr([str(python_venv()), "-m", "pip", "install", *paquetes_de(perfil)], timeout=3600)
    faltan = modulos_faltantes(modulos)
    if faltan:
        paso(f"Paquetes de Python ({perfil})", "FALTA", f"siguen faltando {', '.join(faltan)}: {(r.stderr or '').strip()[-300:]}")
        return False
    paso(f"Paquetes de Python ({perfil})", "INSTALADO", ", ".join(modulos))
    return True


# ---------------------------------------------------------------------------
# 4 y 5. Tesseract e idioma español
# ---------------------------------------------------------------------------
def ruta_tesseract() -> str | None:
    for c in [shutil.which("tesseract")] + (RUTAS_TESSERACT_WINDOWS if os.name == "nt" else []) + \
             ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract"]:
        if c and os.path.isfile(c):
            return c
    return None


def revisar_tesseract(info: dict, revisar: bool) -> str | None:
    t = ruta_tesseract()
    if t:
        paso("Tesseract OCR", "OK", t)
        return t
    paquetes = {
        "winget": ["winget", "install", "--id", "UB-Mannheim.TesseractOCR", "-e", "--silent",
                   "--accept-package-agreements", "--accept-source-agreements"],
        "brew": ["brew", "install", "tesseract", "tesseract-lang"],
        "apt-get": ["apt-get", "install", "-y", "tesseract-ocr", "tesseract-ocr-spa"],
        "dnf": ["dnf", "install", "-y", "tesseract", "tesseract-langpack-spa"],
        "pacman": ["pacman", "-S", "--noconfirm", "tesseract", "tesseract-data-spa"],
        "zypper": ["zypper", "--non-interactive", "install", "tesseract-ocr", "tesseract-ocr-traineddata-spanish"],
    }
    manual = {"Windows": "instálalo desde https://github.com/UB-Mannheim/tesseract/wiki",
              "macOS": "instala Homebrew (https://brew.sh) y luego: brew install tesseract tesseract-lang",
              }.get(info["so"], "instala 'tesseract' y su idioma español con el gestor de tu distribución")
    if instalar_paquetes_sistema(info, paquetes, "Tesseract OCR", revisar, manual):
        t = ruta_tesseract()
        if t:
            paso("Tesseract OCR", "INSTALADO", t)
            return t
        paso("Tesseract OCR", "AVISO", "se instaló pero no aparece aún; cierra y abre la terminal y repite")
    return None


def idiomas(tesseract: str, tessdata: Path | None = None) -> set[str]:
    cmd = [tesseract]
    if tessdata:
        cmd += ["--tessdata-dir", str(tessdata)]
    try:
        r = subprocess.run([*cmd, "--list-langs"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return set()
    salida = (r.stdout or "") + (r.stderr or "")
    return {linea.strip() for linea in salida.splitlines()[1:] if linea.strip() and " " not in linea.strip()}


def revisar_espanol(tesseract: str, revisar: bool) -> str | None:
    """Devuelve 'sistema' o la ruta de tessdata local si hay español; None si no."""
    if "spa" in idiomas(tesseract):
        paso("Tesseract: idioma español (spa)", "OK", "incluido en el sistema")
        return "sistema"
    if (TESSDATA_LOCAL / "spa.traineddata").exists() and "spa" in idiomas(tesseract, TESSDATA_LOCAL):
        paso("Tesseract: idioma español (spa)", "OK", f"local en {TESSDATA_LOCAL}")
        return str(TESSDATA_LOCAL)
    if revisar:
        paso("Tesseract: idioma español (spa)", "FALTA", f"se descargaría de {URL_SPA}")
        return None
    TESSDATA_LOCAL.mkdir(parents=True, exist_ok=True)
    destino = TESSDATA_LOCAL / "spa.traineddata"
    print(f"    descargando {URL_SPA} ...")
    try:
        with urllib.request.urlopen(urllib.request.Request(URL_SPA, headers={"User-Agent": "Mozilla/5.0"}),
                                    timeout=300) as r, open(destino, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:  # noqa: BLE001
        destino.unlink(missing_ok=True)
        paso("Tesseract: idioma español (spa)", "FALTA", f"no se pudo descargar: {e}")
        return None
    if "spa" in idiomas(tesseract, TESSDATA_LOCAL):
        paso("Tesseract: idioma español (spa)", "INSTALADO", f"en {TESSDATA_LOCAL}")
        return str(TESSDATA_LOCAL)
    paso("Tesseract: idioma español (spa)", "FALTA", "el archivo se descargó pero Tesseract no lo reconoce")
    return None


def terminar(codigo: int, mensaje: str) -> None:
    """Imprime el RESULTADO, lo deja en el JSON (con --json) y sale con el código."""
    print(mensaje)
    salida.poner(resultado=mensaje.replace("RESULTADO: ", ""))
    salida.cerrar(codigo)
    sys.exit(codigo)


def quitar() -> None:
    """Borra lo instalado (el entorno de Python, el idioma español descargado y entorno.json, todo FUERA del
    proyecto). Es una utilidad manual, no parte del cierre de los procesos; el siguiente proceso lo reinstala solo.
    No toca el archivo del candado (nunca se borra) ni desinstala programas del sistema (Python, Tesseract, curl)."""
    import time
    objetivos = [VENV, TESSDATA, ENTORNO]
    fallidos = []
    for ruta in objetivos:
        if not ruta.exists():
            continue
        for _ in range(20):                    # Windows: un archivo recién cerrado puede tardar en liberarse
            try:
                if ruta.is_dir():
                    shutil.rmtree(ruta)
                else:
                    ruta.unlink()
                break
            except OSError:
                time.sleep(0.25)
        if ruta.exists():
            fallidos.append(ruta)
        else:
            print(f"  ✔ borrado: {ruta}")
    if fallidos:
        terminar(1, "RESULTADO: NO se pudo borrar " + ", ".join(str(f) for f in fallidos)
                 + ". ¿Sigue abierta alguna herramienta o un programa usa esos archivos? Ciérralo y repite "
                   "python sistema/biblioteca.py instalar --quitar")
    terminar(0, "RESULTADO: entorno quitado. El próximo proceso lo reinstala solo (python sistema/biblioteca.py instalar).")


# ---------------------------------------------------------------------------
def main():
    p = Analizador(description="Detecta e instala las dependencias.",
                   formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_INSTALAR)
    p.add_argument("--revisar", action="store_true", help="solo diagnosticar, no instalar")
    p.add_argument("--para", choices=sorted(PERFILES), default="todo",
                   help="instalar solo lo que necesita ese proceso (por defecto: todo)")
    p.add_argument("--sin-ocr", action="store_true", help="no instalar Tesseract")
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    p.add_argument("--quitar", action="store_true",
                   help="utilidad manual: borra el entorno, el idioma español descargado y entorno.json (se reinstalan solos)")
    args = p.parse_args()
    salida.iniciar(args.json, "instalar")
    if args.quitar:
        if args.revisar or args.sin_ocr or args.para != "todo":
            p.error("--quitar se usa solo")
        return quitar()

    info = sistema()
    print(f"Sistema: {info['so']} {info['version']} | gestor de paquetes: {info['gestor'] or 'ninguno detectado'}")
    print("Modo: " + ("solo revisión (no se instala nada)" if args.revisar else "revisar e instalar lo que falte")
          + f" · perfil: {args.para} (requirements.txt: {', '.join(PERFILES[args.para])})")
    print(f"Lo instalado vive fuera del proyecto: {LOCAL}")
    print()

    esenciales = revisar_python()
    esenciales = revisar_venv(info, args.revisar) and esenciales
    esenciales = revisar_paquetes(args.revisar, args.para) and esenciales
    curl = shutil.which("curl")
    paso("curl (opcional)", "OK" if curl else "AVISO", curl or "no está; solo se usa como respaldo para certificados")

    tesseract, espanol = None, None
    sin_ocr = args.sin_ocr or args.para in ("actualizar", "comprimir")   # esos procesos no usan OCR
    if sin_ocr:
        # No se instala nada de OCR, pero si ya está, se detecta (y el resultado es "todo listo")
        tesseract = ruta_tesseract()
        if tesseract and "spa" in idiomas(tesseract):
            espanol = "sistema"
        elif tesseract and (TESSDATA_LOCAL / "spa.traineddata").exists() and "spa" in idiomas(tesseract, TESSDATA_LOCAL):
            espanol = str(TESSDATA_LOCAL)
        if tesseract and espanol:
            paso("Tesseract OCR con español", "OK", f"{tesseract} (ya estaba instalado)")
        else:
            paso("Tesseract OCR", "AVISO", f"no se instala ({'--sin-ocr' if args.sin_ocr else 'perfil ' + args.para}); "
                                           "solo hace falta para convertir PDFs escaneados")
    else:
        tesseract = revisar_tesseract(info, args.revisar)
        if tesseract:
            espanol = revisar_espanol(tesseract, args.revisar)

    ocr = bool(tesseract and espanol)
    if not args.revisar:
        ENTORNO.parent.mkdir(parents=True, exist_ok=True)
        with open(ENTORNO, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "sistema": info,
            "python_venv": str(python_venv()) if python_venv().exists() else None,
            "tesseract": tesseract, "tessdata": espanol if espanol not in (None, "sistema") else None,
            "ocr_listo": ocr, "esenciales_listos": esenciales, "perfil": args.para,
        }, ensure_ascii=False, indent=2) + "\n")

    print()
    salida.poner(sistema=info, perfil=args.para, esenciales=esenciales, ocr=ocr, tesseract=tesseract,
                 python_venv=str(python_venv()))
    if not esenciales:
        terminar(1, "RESULTADO: FALTAN DEPENDENCIAS ESENCIALES. Revisa las líneas marcadas con ✘ o →.")
    if args.para in ("actualizar", "comprimir"):
        terminar(0, f"RESULTADO: LISTO para {args.para} (perfil {args.para}; para convertir, instala con --para convertir).")
    if not ocr:
        terminar(2, "RESULTADO: LISTO para actualizar y convertir, pero SIN OCR: los PDFs escaneados no se podrán "
                    "convertir a Markdown (todo lo demás funciona).")
    terminar(0, "RESULTADO: TODO LISTO (actualizar y convertir, incluido OCR en español).")
