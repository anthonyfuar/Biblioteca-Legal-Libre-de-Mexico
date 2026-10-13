---
name: instalar
description: Detecta el sistema operativo (Windows, macOS o Linux) y las dependencias que faltan para la Biblioteca Legal Libre de México (Python, su entorno, paquetes, Tesseract OCR y su idioma español) e instala solo lo necesario. Úsala cuando el usuario escriba "instalar" o "instala las dependencias", o cuando una herramienta diga "No module named ...", "Falta la dependencia ..." o "ejecuta python sistema/biblioteca.py instalar".
---
<!-- ORIGINAL (fuente única). Claude Code usa la copia generada en .claude/skills/: después de editarlo ejecuta python sistema/biblioteca.py sincronizar -->

# Instalar dependencias

**Modo:** el texto que el usuario escribió junto al comando (`completo` o `sin-ocr`). Si no escribió
nada: completo, con OCR.

Sigue `AGENTS.md`, sección 6, o delega en el agente **`instalador`**. Trabaja desde la carpeta
del proyecto.

1. **Python 3.10+**: prueba `python --version`, `python3 --version` y `py --version`. Si no hay,
   instálalo según el sistema (tabla en AGENTS.md §6, paso 1).
2. **Diagnóstico**: `python sistema/biblioteca.py instalar --revisar`
3. **Instalación**: `python sistema/biblioteca.py instalar` (o `--sin-ocr` si el modo es `sin-ocr`; `--para actualizar|comprimir|convertir` instala solo lo de ese proceso).
   Antes, di en una línea qué se va a instalar; en Windows puede aparecer una ventana de permiso.
4. **Resultado**: 0 = todo listo · 2 = listo sin OCR · 1 = falta algo esencial.
   Las líneas **→ MANUAL** traen el comando que debe ejecutar el usuario (requiere contraseña de
   administrador): **nunca pidas ni escribas contraseñas**.
5. Si la instalación venía de otro proceso (actualizar o convertir), **continúa** con ese proceso y no
   limpies todavía (ese proceso limpia al final). Si "instalar" fue lo único que pidió el usuario,
   cierra con `python sistema/biblioteca.py verificar --limpiar`: debe quedar con 0 temporales y sin avisos de archivos
   sueltos. `instalar --quitar` no es parte de ningún cierre: es una utilidad manual que borra el entorno instalado.
