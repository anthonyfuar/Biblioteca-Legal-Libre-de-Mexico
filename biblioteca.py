"""
biblioteca.py — Punto de entrada único de las herramientas de la Biblioteca Legal Libre de México.

USO:  python biblioteca.py <herramienta> [opciones]        ayuda de cada una: python biblioteca.py <herramienta> --help

  instalar     detecta e instala lo que falte: Python, entorno, paquetes y OCR en español
  catalogo     consulta y mantiene el catálogo: resumen, listar, ver, fuentes, marcar, agregar, mover, renombrar...
  buscar       encuentra URLs: índice de leyes de la Cámara de Diputados, enlaces de una página
  descargar    descarga, valida, compara y registra un documento oficial
  convertir    PDF -> Markdown estructurado (solo si el usuario lo pide o lo acepta)
  comprimir    reduce los PDF de más de 60 MB, verificados contra el original (pregunta el nivel)
  verificar    integridad de la biblioteca; con --limpiar, borra los temporales al terminar
  publicar     prepara y sube la biblioteca a GitHub (el push, solo con autorización expresa)
  sincronizar  regenera las copias de .claude/ desde los originales de .agents/
  word         muestra el texto de un Word oficial (.doc/.docx)
  pruebas      pytest, ruff y mypy del código (para desarrollar)

Todas aceptan --json (stdout = un objeto con ok, codigo, resultado y datos; el texto va a stderr).
Python 3.10 o más nuevo; con uno más viejo, `instalar` crea el entorno con el más nuevo que encuentre.
"""
import importlib
import sys
from pathlib import Path

sys.dont_write_bytecode = True                           # sin __pycache__ en el proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent))

# herramienta -> (módulo de herramientas/, función)
HERRAMIENTAS = {
    "instalar": ("instalar", "main"),
    "catalogo": ("catalogo", "main"),
    "buscar": ("descargar", "main_buscar"),
    "descargar": ("descargar", "main"),
    "convertir": ("convertir", "main"),
    "comprimir": ("comprimir", "main"),
    "verificar": ("verificar", "main"),
    "publicar": ("publicar", "main"),
    "sincronizar": ("verificar", "main_sincronizar"),
    "word": ("descargar", "main_word"),
    "pruebas": ("verificar", "main_pruebas"),
}


def main() -> None:
    nombre = sys.argv[1] if len(sys.argv) > 1 else ""
    if nombre not in HERRAMIENTAS:
        ayuda = nombre in ("-h", "--help", "ayuda")
        print(__doc__.strip(), file=sys.stdout if ayuda else sys.stderr)
        if nombre and not ayuda:
            print(f"\nERROR de uso: '{nombre}' no es una herramienta.", file=sys.stderr)
        sys.exit(0 if ayuda else 64)
    modulo, funcion = HERRAMIENTAS[nombre]
    from herramientas import comun
    if nombre == "instalar":                 # solo biblioteca estándar: corre antes de que exista el entorno
        comun.consola_utf8()
        sys.argv = [f"biblioteca.py {nombre}", *sys.argv[2:]]
        importlib.import_module("herramientas.instalar").main()
        return
    comun.preparar()                         # con los argumentos intactos: si hace falta, se relanza con el entorno
    sys.argv = [f"biblioteca.py {nombre}", *sys.argv[2:]]
    comun.ejecutar(getattr(importlib.import_module(f"herramientas.{modulo}"), funcion))


if __name__ == "__main__":
    main()
