# CLAUDE.md — Biblioteca Legal Libre de México

> **Ellos se la saben. Ahora tú también.** · La ley completa, gratis y para todos.

Este proyecto mantiene la biblioteca con **cinco procesos separados**. Todo es en español.

| El usuario escribe | Usa | Qué pasa |
|---|---|---|
| `instalar` | `/instalar` o el subagente `instalador` | Detecta el sistema operativo y lo que falta, e instala solo eso |
| `actualizar ...` | `/actualizar` (reparte bloques entre subagentes `actualizador`) | Solo PDF. Al final informa y **pregunta** si se desea convertir |
| `convertir ...` o "sí" a esa pregunta | `/convertir` o el subagente `convertidor` | PDF → Markdown |
| `comprimir ...` | `/comprimir` o el subagente `compresor` | PDF de más de 60 MB: **pregunta el nivel** (10, 30 recomendado, 50, 80) y verifica cada uno contra el original |
| `publicar` | `/publicar` o el subagente `publicador` | Integridad, `SHA256SUMS`, `CAMBIOS.md` y commit; **pregunta** antes del `push` |

Si el usuario escribe la palabra sin la diagonal, sigue exactamente los pasos del comando.
Si algo falta al empezar, el proceso de instalación corre solo y después **continúa** lo pedido.

## Notas para Claude Code

- Todas las herramientas aceptan `--json` (stdout = un objeto con `ok`, `codigo`, `resultado` y datos; el texto
  va a stderr): úsalo para leer resultados sin interpretar texto.
- Ejecuta los comandos **desde la carpeta del proyecto** como `python sistema/biblioteca.py <herramienta>` (sin `cd`
  ni otra ruta): así coinciden con los permisos preaprobados de `.claude/settings.json`. En Windows el
  comando puede ser `python` o `py`; en macOS/Linux, `python3`.
- Los subagentes `actualizador` heredan este archivo y el manual: dales solo su **bloque** y recuérdales
  que no ejecutan `convertir`, `instalar` ni `verificar --limpiar`. Máximo 8 a la vez.
- **Nunca conviertas a Markdown sin que el usuario lo pida o lo acepte, ni comprimas sin que elija (o acepte) el nivel,
  ni hagas `git push` sin su autorización expresa (solo con `publicar --push`, que además siempre pasa por el
  diálogo de permisos de Claude Code: `.claude/settings.json` lo pone en `ask`).**
- Al terminar cualquier proceso, **siempre** (aunque algo falle): `python sistema/biblioteca.py verificar --limpiar`
  hasta que diga 0 temporales, sin avisos de archivos sueltos e `INTEGRIDAD: OK`. `instalar --quitar`
  no es parte del cierre. No escribas scripts propios si una herramienta ya lo hace
  (`descargar --lote`, `catalogo listar --solo-ids`).
- La organización de `biblioteca/` es **una sola** (`sistema/referencia/estructura.md`): nunca muevas archivos
  ni inventes carpetas; usa `catalogo agregar/mover/marcar/renombrar`, que la respetan solas.
- **La biblioteca crece**: todo documento oficial nuevo que aparezca (ley, código, reglamento, tratado, decreto) se
  agrega con `catalogo agregar` en su carpeta y se descarga. **Solo PDF o Word, nunca páginas web.**
- Los archivos de `.claude/agents/` y `.claude/skills/` son copias generadas desde los originales de
  `.agents/` con `python sistema/biblioteca.py sincronizar`: si hay que cambiar un agente o un comando,
  edita `.agents/` y vuelve a generar.

---

## Manual completo

@AGENTS.md
