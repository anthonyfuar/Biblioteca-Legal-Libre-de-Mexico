---
name: publicar
description: Publica la Biblioteca Legal Libre de México en GitHub. Úsala SOLO cuando el usuario escriba "publicar", "sube la biblioteca a GitHub" o "haz el commit y súbelo". Revisa la integridad, genera biblioteca/SHA256SUMS y la entrada de CAMBIOS.md, hace el commit y PREGUNTA antes del push; nunca empuja a GitHub sin autorización expresa del usuario.
---
<!-- ORIGINAL (fuente única). Claude Code usa la copia generada en .claude/skills/: después de editarlo ejecuta python sistema/biblioteca.py sincronizar -->

# Publicar la biblioteca en GitHub

**Alcance:** todo el repositorio (herramientas + `biblioteca/`), rama `main`, sin LFS. Si el usuario escribió
una URL junto al comando ("publicar https://github.com/usuario/repositorio.git"), es la del remoto.

Sigue `AGENTS.md`, sección 9.2. Trabaja desde la carpeta del proyecto. **Nunca ejecutes `git push`, `git remote
add` ni `git commit` por tu cuenta: todo pasa por `publicar`.**

## 1. Revisar
```
python sistema/biblioteca.py publicar --revisar
```
Muestra la integridad (`verificar --final`), los archivos de más de 95 MiB (GitHub bloquea 100 MiB), si
`biblioteca/SHA256SUMS` está al día, qué entraría en `CAMBIOS.md` desde la última publicación, el tamaño del
repositorio y si hay remoto. No escribe nada.
- Si hay **ERRORES** de integridad, corrígelos con las herramientas (sección 8, fase 3 de `AGENTS.md`) y repite.
- Si hay archivos de **más de 95 MiB**, no se puede publicar: dile al usuario cuáles son y ofrécele el proceso
  `comprimir` (nivel 30 recomendado). No sigas hasta que quepan.

## 2. Preparar
```
python sistema/biblioteca.py publicar --preparar
```
Exige `INTEGRIDAD: OK`; escribe `biblioteca/SHA256SUMS` (huella SHA-256 de cada archivo de `biblioteca/`) y
añade a `CAMBIOS.md` la entrada de esta publicación (nuevos, actualizados, abrogados, agregados, eliminados,
renombrados, convertidos y comprimidos desde la última entrada, tomados de la bitácora). Si no hay cambios desde
la última publicación, lo dice: díselo al usuario y cierra (paso 5).

## 3. Commit
```
python sistema/biblioteca.py publicar --commit
```
Hace `git add -A` y el commit con el resumen de la entrada de `CAMBIOS.md`. Si no hay cambios en git, lo dice.

## 4. PREGUNTA antes del push (obligatorio)
Enseña el resumen (commit, cuántos archivos, tamaño que se subirá, remoto) y pregunta:

> **¿Autorizas subirlo a GitHub ahora (`git push origin main`)?** Si aún no existe el repositorio, créalo vacío
> en GitHub (sin README ni licencia) y dame su URL.

- Solo con un **"sí" claro del usuario en esta conversación**:
  ```
  python sistema/biblioteca.py publicar --push                      (o --push --remoto <URL> la primera vez)
  ```
  La herramienta mide lo que falta subir (GitHub rechaza un push de 2 GB o más), empuja `main` y comprueba en el
  remoto que quedó igual. Si pasa de 1 GiB, lo sube sola **por bloques** de commits, del más viejo al más nuevo;
  puede tardar horas: si tu entorno corta procesos largos, ejecútala en segundo plano y espera a que termine.
- Si el usuario dice que no o no responde, **termina ahí**: el commit queda hecho y listo para subir después.
- Si un bloque falla (red, tiempo), lo ya subido se queda en GitHub: repetir `--push` sigue desde ese bloque.
  Si un solo commit pasa de 1.8 GiB, la herramienta explica cómo rehacerlo en partes; no lo hagas sin que el
  usuario lo acepte.

## 5. Cierre
```
python sistema/biblioteca.py verificar --limpiar
```
Obligatorio siempre: `INTEGRIDAD: OK`, 0 temporales y sin AVISOS de archivos sueltos.

## 6. Informe (en español)
Qué se preparó (SHA256SUMS, entrada de `CAMBIOS.md` con sus conteos), el commit (hash y mensaje), si se subió o
no (y por qué), la URL del repositorio y el resultado de `verificar`. No inventes resultados.
