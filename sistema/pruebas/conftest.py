"""Configuración común de las pruebas: las herramientas se importan sin relanzarse con el entorno de Python
(BIBLIOTECA_SIN_ENTORNO) y sin __pycache__ en el proyecto."""
import os
import sys
from pathlib import Path

os.environ.setdefault("BIBLIOTECA_SIN_ENTORNO", "1")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # la carpeta del proyecto (herramientas/)
