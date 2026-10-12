"""Pruebas de herramientas/comun.py."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from herramientas import comun


# ====================================================================================================
# test_texto
# ====================================================================================================
def test_sin_acentos_y_enie():
    assert comun.sin_acentos("Código Ñandú") == "Codigo Nandu"


def test_normalizar():
    assert comun.normalizar("  Ley   de  AMPARO, (vigente) ") == "ley de amparo vigente"
    assert comun.normalizar("") == ""


def test_nombre_archivo():
    assert comun.nombre_archivo("Código Fiscal de la Federación") == "Codigo_Fiscal_de_la_Federacion"
    assert comun.nombre_archivo("Ley 1o. de Enero/2024 (bis)") == "Ley_1o_de_Enero_2024_bis"


def test_palabras_clave_y_secuencia():
    assert comun.palabras_clave("Ley de la Comisión Nacional de los Derechos Humanos") == \
        {"ley", "comision", "nacional", "derechos", "humanos"}
    assert comun.secuencia("Ley del Trabajo y del Trabajo") == ["ley", "trabajo", "trabajo"]


# ====================================================================================================
# Dos procesos haciendo leer-modificar-escribir del mismo JSON bajo el candado no pierden actualizaciones.
# ====================================================================================================
PROYECTO = Path(__file__).resolve().parent.parent
TRABAJADOR = r'''
import os, sys, json
sys.path.insert(0, %r)
from herramientas import comun
comun.BLOQUEO = __import__("pathlib").Path(%r)
f = __import__("pathlib").Path(%r)
for _ in range(25):
    with comun.bloqueo():
        n = comun.leer_json(f, {"n": 0})["n"]
        comun.guardar_json(f, {"n": n + 1})
'''


def test_dos_procesos_no_pierden_incrementos(tmp_path):
    f = tmp_path / "contador.json"
    bloqueo = tmp_path / ".bloqueo"
    env = dict(os.environ, BIBLIOTECA_SIN_ENTORNO="1")
    codigo = TRABAJADOR % (str(PROYECTO), str(bloqueo), str(f))
    procesos = [subprocess.Popen([sys.executable, "-c", codigo], env=env) for _ in range(4)]
    for p in procesos:
        assert p.wait(timeout=120) == 0
    assert json.loads(f.read_text())["n"] == 100


# ====================================================================================================
# Limpieza, archivos de la raíz y arranque
# ====================================================================================================
def test_limpiar_cuenta_anteriores_como_fuera_del_proyecto(tmp_path, monkeypatch):
    raiz, temp = tmp_path / "proyecto", tmp_path / "temporal"
    for nombre, valor in {"RAIZ": raiz, "TRABAJO": raiz / "trabajo", "DOCUMENTOS": raiz / "documentos",
                          "TEMP_PROYECTO": temp, "ANTERIORES": temp / "anteriores", "CACHE": temp / "cache"}.items():
        monkeypatch.setattr(comun, nombre, valor)
    (raiz / "documentos").mkdir(parents=True)
    (raiz / "trabajo").mkdir()
    (raiz / "trabajo" / "listado.txt").write_text("x", encoding="utf-8")
    (temp / "anteriores").mkdir(parents=True)
    for i in range(3):
        (temp / "anteriores" / f"doc{i}.pdf").write_bytes(b"%PDF")
    assert comun.limpiar_temporales() == (1, 3, [])     # 1 del proyecto (trabajo/), 3 fuera (anteriores/)
    assert not (raiz / "trabajo").exists() and not (temp / "anteriores").exists()


def test_carpetas_de_editores_y_entorno_propio_no_son_restos(tmp_path, monkeypatch):
    monkeypatch.setattr(comun, "RAIZ", tmp_path)
    for carpeta in (".vscode", ".idea", ".cursor", ".venv", "documentos"):
        (tmp_path / carpeta).mkdir()
    (tmp_path / "listado.txt").write_text("x", encoding="utf-8")
    assert [p.name for p in comun.inesperados()] == ["listado.txt"]


def test_instalar_escribe_en_utf8_aunque_la_consola_no_lo_sea():
    # Windows con consola cp1252: sin consola_utf8() la ayuda (≈, ✔) fallaría con UnicodeEncodeError
    env = dict(os.environ, BIBLIOTECA_SIN_ENTORNO="1", PYTHONIOENCODING="cp1252")
    r = subprocess.run([sys.executable, "-B", str(PROYECTO / "biblioteca.py"), "instalar", "--help"],
                       capture_output=True, env=env, timeout=120, check=False)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "≈" in r.stdout.decode("utf-8")


# ====================================================================================================
# Procesos hijos: al vencer el tope o al interrumpir, no queda nada trabajando solo
# ====================================================================================================
def _muere(pid: int, segundos: float = 10) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if not comun.pid_vivo(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(os.name == "nt", reason="grupos de procesos de POSIX")
def test_correr_hijo_al_vencer_el_tope_detiene_tambien_a_los_nietos(tmp_path):
    pids = tmp_path / "pids"
    inicio = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        comun.correr_hijo(["sh", "-c", f"sleep 60 & echo $! > '{pids}'; wait"], timeout=1)
    assert time.monotonic() - inicio < 15            # el nieto ya no deja abiertas las tuberías
    assert _muere(int(pids.read_text()))


@pytest.mark.skipif(os.name == "nt", reason="grupos de procesos de POSIX")
def test_correr_hijo_interrumpido_detiene_al_hijo_y_a_los_nietos(tmp_path):
    class Interrupcion(Exception):
        pass

    def interrumpir(*_):
        raise Interrupcion

    pids = tmp_path / "pids"
    anterior = signal.signal(signal.SIGALRM, interrumpir)
    try:
        signal.setitimer(signal.ITIMER_REAL, 1.0)
        with pytest.raises(Interrupcion):
            comun.correr_hijo(["sh", "-c", f"sleep 60 & echo $$ $! > '{pids}'; wait"], timeout=60)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, anterior)
    hijo, nieto = (int(x) for x in pids.read_text().split())
    assert _muere(hijo) and _muere(nieto)
    assert not comun._HIJOS
