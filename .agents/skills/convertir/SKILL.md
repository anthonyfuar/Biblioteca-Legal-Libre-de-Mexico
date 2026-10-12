---
name: convertir
description: Convierte a Markdown los PDF de la Biblioteca Legal Libre de México cuyo Markdown falta o está desactualizado (MarkItDown; OCR en español para escaneados). Úsala SOLO cuando el usuario escriba "convertir", "convertir a markdown", "convertir <estado>" o responda que sí a "¿deseas convertirlos a Markdown?". Nunca por iniciativa propia ni como parte de "actualizar".
---
<!-- ORIGINAL (fuente única). Claude Code usa la copia generada en .claude/skills/: después de editarlo ejecuta python biblioteca.py sincronizar -->

# Convertir PDF a Markdown

**Alcance:** el texto que el usuario escribió junto al comando (un estado, un grupo o ids). Si no
escribió nada: todos los PDF con Markdown faltante o desactualizado.

Sigue `AGENTS.md`, sección 9. Trabaja desde la carpeta del proyecto. Cada Markdown se guarda **estructurado**
(front-matter, encabezados hasta `#### Artículo N`, párrafos unidos; `referencia/esquema.md`, sección 2), validado sin pérdida
de palabras; si `--listar` reporta Markdown "con formato anterior", ejecuta `python biblioteca.py convertir --reestructurar`
(reaplica el formato sin reconvertir; también acepta `--entidad`, `--grupo`, `--id`).

## 1. Dependencias
```
python biblioteca.py instalar --revisar --para convertir
```
- Código **1** → delega en el agente **`instalador`** (o ejecuta `python biblioteca.py instalar --para convertir`) y continúa.
- Código **2** (sin OCR) → se puede convertir, pero los escaneados fallarán: ofrece instalar el OCR
  con `python biblioteca.py instalar` antes de seguir.

## 2. Qué hay pendiente
```
python biblioteca.py convertir --listar            (o con --entidad "<Estado>", --grupo "<grupo>", --id ...)
```
Si el usuario no había aceptado ya una cantidad concreta, dile cuántos son y confirma.

## 3. Convertir
```
python biblioteca.py convertir                     (o con el mismo filtro del paso 2)
```
- Un solo `convertir` a la vez; máximo 5 procesos (por defecto 3). Nunca lances varios en paralelo.
- Con muchos documentos puede tardar horas: si hace falta, conviértelo por partes (`--entidad`,
  `--grupo`) e informa el avance entre partes. Puedes delegar cada parte en el agente `convertidor`,
  **una a la vez**, indicándole que es subagente y que NO ejecute `verificar --limpiar` (lo haces
  tú en el paso 4).
- Fallas: reintenta una vez con `--id <id>`; "OCR no disponible" → instala el OCR y reintenta.

## 4. Cierre
```
python biblioteca.py verificar --limpiar
```
(borra también todo lo temporal).
**La limpieza es obligatoria, siempre** (aunque algo haya fallado o se haya cancelado): al final
`verificar` debe decir `Temporales: 0 en el proyecto | 0 fuera del proyecto`, sin avisos de
archivos sueltos e `INTEGRIDAD: OK`. Si aparecen AVISOS de archivos que creó un agente, bórralos y repite
`verificar --limpiar`. No ejecutes `instalar --quitar` (el entorno instalado se queda entre procesos).
Informe en español: cuántos se convirtieron (cuántos con OCR), cuáles fallaron y por qué, y el
resultado de `verificar`.
