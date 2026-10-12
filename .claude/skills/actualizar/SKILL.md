---
name: actualizar
description: "Actualiza los PDF de la Biblioteca Legal Libre de México. Úsala cuando el usuario escriba \"actualizar\", \"actualiza la biblioteca\", \"actualizar federal\", \"actualizar <estado>\", \"actualizar pendientes\" o pida revisar si hay leyes nuevas o reformadas. Busca en fuentes oficiales, descarga, compara por SHA-256 y fecha de reforma, verifica, informa y al final PREGUNTA si se desea convertir a Markdown (nunca convierte por su cuenta)."
argument-hint: "[todo | federal | pendientes | <estado> | <nombre de ley>]"
---
<!-- GENERADO por biblioteca.py sincronizar desde .agents/skills/actualizar/SKILL.md. No lo edites aquí: edita .agents/skills/actualizar/SKILL.md y vuelve a generar. -->

# Actualizar la biblioteca (solo PDF)

**Alcance:** el texto que el usuario escribió junto al comando o después de la palabra "actualizar"
(`federal`, `pendientes`, un estado o el nombre de una ley). Si no escribió nada: **todo** el catálogo.

Sigue `AGENTS.md`, secciones 7 y 8. Trabaja desde la carpeta del proyecto.

## 1. Dependencias (solo si hace falta)
```
python biblioteca.py instalar --revisar --para actualizar
```
Si sale con código **1**, delega en el agente **`instalador`** (o ejecuta tú
`python biblioteca.py instalar --para actualizar`) y, cuando quede en 0 o 2, **continúa**. Los códigos 0 y 2
permiten actualizar.

## 2. Preparación
Anota la fecha y hora de inicio (`AAAA-MM-DD HH:MM`). Luego:
```
python biblioteca.py verificar
python biblioteca.py catalogo resumen
```

## 3. Plan
- Traduce el alcance a una lista de bloques:
  - todo → bloque `federal` + los 32 estados en bloques de 3 a 4.
  - `federal` → los grupos federales (puedes partirlos en 2 o 3 bloques).
  - un estado → un solo bloque.
  - `pendientes` → `python biblioteca.py catalogo listar --pendientes --limite 2000`, agrupados por entidad.
  - una ley → `python biblioteca.py catalogo listar --buscar "<nombre>"` y hazlo tú directo (AGENTS.md §7).
- Prioriza dentro de cada bloque: pendientes, leyes de ingresos y RMF (cambian cada año) y lo no
  verificado en más de 30 días.

## 4. Ejecución
Si tu editor permite subagentes, reparte los bloques entre agentes **`actualizador`** (definidos en
`.agents/agents/` y, para Claude Code, en `.claude/agents/`), **máximo 8 a la vez**;
si hay más bloques, lanza los siguientes conforme terminen. A cada uno dale:

> Bloque: **<nombre del bloque y lista de entidades/grupos>**. Aplica la sección 7 de AGENTS.md a
> cada documento del bloque, empezando por los pendientes. NO ejecutes convertir, instalar ni
> verificar --limpiar (la limpieza la hago yo al final). Nombra tus archivos de trabajo en
> trabajo/ con el prefijo de tu bloque.
> Tu último mensaje debe ser el resumen descrito en tu definición (agente `actualizador`).

Si tu editor no permite subagentes, haz los bloques tú mismo, uno tras otro, siguiendo
AGENTS.md §7.

**La biblioteca crece:** todo documento oficial nuevo que aparezca (ley, código, reglamento, tratado, decreto),
aunque no sustituya a otro, se agrega y se descarga (AGENTS.md, regla 14). **Solo PDF o Word, nunca páginas web**
(regla 13). Un documento nuevo se agrega solo en una carpeta de la estructura única (`referencia/estructura.md`,
`catalogo grupos`); las abrogadas las mueve `catalogo marcar`. Nunca muevas archivos a mano.

## 5. Cierre (cuando TODOS los bloques terminaron)
```
python biblioteca.py verificar --limpiar
python biblioteca.py catalogo reporte --desde "<fecha y hora de inicio>"      (con --entidad/--grupo si el alcance fue uno)
python biblioteca.py convertir --listar       (con el MISMO alcance: un estado → --entidad "<Estado>";
                                           federal → --entidad federal; pendientes o una ley → --id <ids
                                           de esta corrida>; todo → sin filtro)
```
Si `verificar` reporta ERRORES, corrígelos con las herramientas y vuelve a verificar.
**La limpieza es obligatoria, siempre** (aunque algo haya fallado o se haya cancelado): al final
`verificar` debe decir `Temporales: 0 en el proyecto | 0 fuera del proyecto`, sin avisos de
archivos sueltos e `INTEGRIDAD: OK`. Si aparecen AVISOS de archivos que creó un agente, bórralos y repite
`verificar --limpiar`. Límpialo **antes** de hacer la pregunta final. No ejecutes `instalar --quitar`
(el entorno instalado se queda entre procesos).
**No ejecutes `convertir` sin `--listar`.**

## 6. Informe final (en español) y PREGUNTA
- Documentos revisados y conteo NUEVO / ACTUALIZADO / SIN CAMBIO.
- **Lista de actualizados** con su nueva fecha de reforma.
- **Pendientes**: DUDOSO o no localizados, con motivo.
- Cambios al catálogo y a las fuentes.
- Resultado final de `verificar` (`INTEGRIDAD: OK` esperado y 0 temporales).
- Si `verificar` avisó de **PDF de más de 60 MB sin comprimir**, dilo: el usuario puede pedir
  `comprimir` (PROCESO 4). No comprimas por tu cuenta.

Termina **siempre** con la pregunta (N = número que dio `convertir --listar`):

> **Se descargaron X PDFs nuevos y se actualizaron Y. Hay N PDFs sin su versión en Markdown.
> ¿Deseas convertirlos a Markdown ahora?**

Si N es 0, no preguntes. Si el usuario dice que sí, sigue el comando **`convertir`** con ese mismo
filtro (o delega en el agente `convertidor`). Si dice que no, termina.
