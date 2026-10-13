---
name: comprimir
description: Comprime los PDF grandes de la Biblioteca Legal Libre de México (los de más de 60 MB) para que se puedan publicar (GitHub rechaza archivos de 100 MB o más). Úsala SOLO cuando el usuario escriba "comprimir", "comprime los pdf", "reducir el peso de los pdf" o acepte comprimir. ANTES de comprimir PREGUNTA el nivel (10, 30 recomendado, 50 u 80 %) advirtiendo de las posibles pérdidas. Cada PDF comprimido se verifica contra el original (páginas, texto e imagen de cada página).
---
<!-- ORIGINAL (fuente única). Claude Code usa la copia generada en .claude/skills/: después de editarlo ejecuta python sistema/biblioteca.py sincronizar -->

# Comprimir los PDF grandes

**Alcance:** todos los PDF de la biblioteca que pasan de **60 MB** (regla fija), o solo los ids que el
usuario indique, si también pasan de 60 MB. Si el usuario ya dijo el nivel ("comprimir 30"), úsalo y no
vuelvas a preguntar.

Sigue `AGENTS.md`, sección 9.1, y el método de `sistema/referencia/comprimir.md`. Trabaja desde la carpeta del proyecto.

## 1. Dependencias
```
python sistema/biblioteca.py instalar --revisar --para comprimir
```
Código **1** → delega en el agente **`instalador`** (o ejecuta `python sistema/biblioteca.py instalar --para comprimir`) y continúa.

## 2. Qué hay que comprimir
```
python sistema/biblioteca.py comprimir --listar
```
Muestra cada PDF de más de 60 MB, su peso y de qué está hecho (tablas o texto escaneados en gris, fotos a
color, escaneo en blanco y negro, texto vectorial o mixto). Si dice `PENDIENTES: 0`, no hay nada que
comprimir: díselo al usuario y ve al paso 5.

## 3. PREGUNTA el nivel (obligatorio, salvo que el usuario ya lo haya dicho)
Enseña la lista del paso 2 y pregunta, con estas advertencias:

> **¿Con qué nivel de compresión?** (el porcentaje es la intensidad; cuánto baja cada archivo depende de su contenido)
> - **10 % — sin pérdida.** Se ve idéntico píxel por píxel. Reduce poco (típicamente 5-25 %).
> - **30 % — RECOMENDADO. Pérdida invisible.** Las tablas y el texto escaneados se guardan en 16 grises;
>   el color real y el texto no se tocan. En el Código Fiscal de la CDMX: 163.9 → 56.5 MB (66 % menos),
>   sin diferencia a la vista.
> - **50 % — pérdida mínima.** Además recomprime las fotos a color: puede haber artefactos leves al ampliar.
> - **80 % — pérdida visible.** Solo 4 grises (los rellenos grises claros se pierden), fotos en calidad baja
>   y menos resolución. El texto se lee, pero la calidad baja. Úsalo solo si los otros no bastan.

Si el usuario no elige, **no comprimas**. Si responde "sí" sin nivel, usa el recomendado (30).

## 4. Comprimir
```
python sistema/biblioteca.py comprimir --nivel <10|30|50|80>          (o con --id <ids> si pidió solo algunos)
```
- Cada PDF se comprime en una carpeta temporal, se **verifica** contra el original (mismo número de
  páginas, mismo texto en cada página e imagen de cada página: píxeles idénticos en el nivel 10 o una
  fidelidad mínima de 38 / 32 / 24 dB en los niveles 30 / 50 / 80) y solo entonces reemplaza al original.
  Si no pasa o no pesa menos, se conserva el original (`NO SE USA`).
- El manifiesto guarda **siempre las dos huellas y los dos tamaños** en `compresion`: los del PDF oficial
  (`sha256_original`, `bytes_original`) y los del comprimido (`sha256`, `bytes`), para poder comparar. Una nueva
  descarga idéntica al oficial sigue siendo SIN_CAMBIO y el Markdown sigue al día. `verificar` exige
  que las dos huellas estén completas.
- Si un PDF sigue pasando de 100 MB, dilo y ofrece un nivel mayor (`--nivel 50 --recomprimir --id <id>`).
- Tarda de segundos a unos minutos por PDF (verifica todas las páginas). Un solo `comprimir` a la vez.

## 5. Cierre
```
python sistema/biblioteca.py verificar --limpiar
```
**La limpieza es obligatoria, siempre** (aunque algo haya fallado o se haya cancelado): `verificar`
debe decir `Temporales: 0 en el proyecto | 0 fuera del proyecto` e `INTEGRIDAD: OK`, sin AVISOS de archivos
sueltos (el aviso de "PDF de más de 60 MB sin comprimir" es informativo: menciónalo en el informe).

## 6. Informe (en español)
Por cada PDF: tamaño antes → después (% menos), nivel, resultado de la verificación (páginas, texto,
fidelidad) y si quedó por encima de 100 MB. Los que no se usaron, con el motivo. Resultado de `verificar`.
