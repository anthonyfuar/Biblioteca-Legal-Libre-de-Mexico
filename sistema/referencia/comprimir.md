# Comprimir PDF sin perder información (PROCESO 4)

> Referencia del manual `AGENTS.md` (sección 9.1). Herramienta: `biblioteca.py comprimir`. Skill: `comprimir`.
> Agente: `compresor`. Aquí está el **método completo**: qué hacer según el tipo de PDF, con qué
> herramientas y cuánto reduce cada nivel.

**Objetivo:** que ningún PDF de la biblioteca estorbe para publicarlo (GitHub rechaza archivos de 100 MB o
más y advierte desde 50 MB), **sin perder información**: mismas páginas, mismo texto y las mismas imágenes a
la vista. **Regla:** se comprimen los PDF de **más de 60 MB**, solo cuando el usuario lo pide y después de
elegir el nivel.

---

## 1. Paso a paso (lo que hace `comprimir`)

```
python sistema/biblioteca.py comprimir --listar          1. qué PDF pasan de 60 MB y de qué están hechos
(el agente pregunta el nivel)                  2. el usuario elige 10, 30 (recomendado), 50 u 80
python sistema/biblioteca.py comprimir --nivel 30         3. comprime, verifica y registra
```

Por cada PDF, `comprimir`:
1. **Diagnostica** de qué está hecho: imágenes en gris neutro, a color, en blanco y negro (1 bit), máscaras
   y el resto (texto, fuentes, dibujos). Clasifica el PDF en un tipo (sección 2).
2. **Comprime en una carpeta temporal** (nunca en su lugar), con las técnicas del nivel (sección 3).
3. **Verifica contra el original**, página por página: mismo número de páginas, **mismo texto** extraído de
   cada página y la **imagen de cada página** dibujada a 100 dpi. En el nivel 10 se exigen píxeles
   idénticos; en los demás, una fidelidad mínima (PSNR): 38 dB en el nivel 30, 32 dB en el 50 y 24 dB en
   el 80.
4. **Solo si pasa y pesa menos**, reemplaza al original en `biblioteca/`. Si no, lo descarta y avisa
   (`NO SE USA`). El resultado es **reproducible**: el mismo PDF con el mismo nivel da siempre el mismo archivo
   (el `/ID` del PDF se calcula del contenido, no del reloj), así que repetir la compresión no cambia la huella.
5. **Registra** en `manifiesto.json` → `compresion`, **siempre las dos huellas y los dos tamaños**, para poder
   comparar: `sha256_original` y `bytes_original` (el PDF oficial tal como se descargó; si se recomprime, se
   conservan los del oficial) y `sha256` y `bytes` (el comprimido guardado). También el nivel y el resultado de
   la verificación, y en la bitácora, el evento. `verificar` marca ERROR si falta alguna huella o si la
   del comprimido no coincide con el archivo.

**Integración con los demás procesos:**
- **ACTUALIZAR:** `descargar` compara la nueva descarga contra la huella del **oficial**
  (`sha256_original`). Si el portal no cambió, sale `SIN_CAMBIO` y el comprimido se queda. Si hay una versión
  nueva, la guarda **sin comprimir** y borra la marca de compresión; `verificar` volverá a avisar si
  pasa de 60 MB.
- **CONVERTIR:** el Markdown sigue al día, porque su `sha256_origen` se compara contra la huella del
  oficial. El texto del PDF no cambia al comprimir.
- **VERIFICAR:** avisa (no es error) de cada PDF de más de 60 MB sin comprimir y de los que pasan de 100 MB.

## 2. Cómo abordar cada tipo de PDF

| Tipo (lo dice `--listar`) | Cómo se reconoce | Qué conviene | Qué hace cada nivel |
|---|---|---|---|
| **Tablas o texto escaneados en gris** (p. ej. tablas de valores catastrales) | La mayor parte del peso son imágenes cuyos canales rojo, verde y azul casi no difieren (gris neutro), aunque vengan como JPEG a color | **Nivel 30**: 16 grises sin JPEG (o JPEG gris si hay mucho ruido). Idéntico a la vista | 10: solo sin pérdida · 30/50: 16 grises · 80: 4 grises (se pierden rellenos grises claros) |
| **Imagen con texto a color** (formularios, mapas, gráficas con color) | Imágenes con color real en una parte de sus píxeles | **Nivel 10 o 30**: el color real no se toca. Si no basta, 50 | 10/30: sin pérdida · 50: JPEG calidad 85 · 80: JPEG 60 y menos resolución (el texto pequeño puede empeorar) |
| **Fotos** (portadas, fotografías, anexos ilustrados) | Casi todo el peso son imágenes a color con degradados | **Nivel 50** si hace falta reducir: en fotos el JPEG 85 no se nota | 10/30: sin pérdida · 50: JPEG 85 · 80: JPEG 60 y máximo 1,600 px por lado |
| **Escaneo en blanco y negro** (1 bit) | Imágenes de 1 bit guardadas con Flate | **Nivel 10**: pasarlas a CCITT Group 4, el formato para blanco y negro, es **sin pérdida** y reduce mucho | Todos: G4 sin pérdida |
| **Texto vectorial** (PDF generado desde Word; la mayoría de las leyes) | Poco peso en imágenes | Casi nunca pasan de 60 MB. Nivel 10 si hiciera falta | Solo el reempaquetado sin pérdida |
| **Mixto** | Varios de los anteriores | Nivel 30: cada imagen recibe lo que le toca según su tipo | Cada imagen según su tipo |

**Reglas que se aplican siempre:**
- El **texto** del PDF (lo que se puede copiar y buscar) **nunca se toca**: se verifica idéntico.
- Las **máscaras** (transparencias) no se recodifican con pérdida; las de 1 bit sí pasan a G4 sin pérdida.
- Las imágenes con máscara no se reducen de resolución.
- Una imagen solo se reemplaza si la nueva **pesa menos**.
- Una imagen es "gris neutro" si sus canales difieren a lo más 24 niveles (el ruido típico de un JPEG).
- En los escaneos con mucho ruido de papel, los 16 grises sin JPEG pueden pesar **más** que el JPEG original;
  entonces se usa JPEG gris (quita los canales de color que sobran).

## 3. Técnicas por nivel

| Nivel | Nombre | Técnicas | Fidelidad exigida | Advertencia al usuario |
|---|---|---|---|---|
| **10** | Sin pérdida | JPEG reoptimizados sin tocar un píxel (Huffman óptimo y progresivo, mozjpeg) · blanco y negro a CCITT G4 · Flate al nivel 9, *object streams*, sin objetos huérfanos (qpdf) | Píxeles idénticos | Ninguna pérdida; reduce poco |
| **30** | **Recomendado** | Nivel 10 + imágenes en gris neutro a **16 grises** (Flate, sin JPEG); si eso pesa más (escaneos con ruido de papel), **JPEG gris calidad 90** | ≥ 38 dB por página | Pérdida invisible: a la vista es idéntico |
| **50** | Pérdida mínima | Nivel 30 (respaldo JPEG gris calidad 85) + imágenes a color en **JPEG calidad 85** | ≥ 32 dB | Artefactos leves en degradados si se amplía mucho |
| **80** | Pérdida visible | Gris a **4 niveles** (respaldo JPEG gris calidad 70), color en **JPEG calidad 60**, imágenes de más de **1,600 px** por lado reducidas | ≥ 24 dB | Se pierden los rellenos grises claros y baja la calidad de las fotos; el texto se lee |

El porcentaje del nivel es la **intensidad**, no una reducción garantizada: cuánto baja cada archivo depende
de su contenido (sección 4).

**Orden de los pasos:** primero todo lo sin pérdida (JPEG reoptimizados y G4); después lo con pérdida, que
solo se aplica a una imagen si pesa menos que su versión sin pérdida. Así un nivel mayor nunca pesa más que
uno menor. Al final qpdf reescribe el PDF con Flate al nivel 9 (hay que fijarle el nivel 9: si no, al
recomprimir todo anula la mejora).

## 4. Qué esperar de cada nivel

Resultados de referencia con documentos de la biblioteca (verificados contra el original en todas sus
páginas: mismo número de páginas y texto idéntico):

| Documento (tipo) | Original | Nivel 10 | Nivel 30 | Nivel 80 |
|---|---|---|---|---|
| Código Fiscal de la Ciudad de México (1,142 páginas; **tablas en gris** y fotos a color) | 163.9 MB | 123.7 MB (píxeles idénticos) | **56.5 MB** (fidelidad mínima 43.3 dB: invisible) | 34.0 MB (31.1 dB: el relleno gris claro se vuelve blanco) |
| Ley de Ingresos de Sinaloa (tablas escaneadas en gris, **con ruido de papel**) | 52.7 MB | 44.1 MB | 44.1 MB | 26.6 MB (33.4 dB) |
| Anexo 1 de la RMF (**imágenes a color**) | 32.1 MB | 26.4 MB | 26.4 MB (el color no se toca) | 26.4 MB |
| Código de Procedimientos Civiles de Yucatán (**escaneo en blanco y negro**, 1 bit) | 30.4 MB | **18.7 MB** (G4, sin pérdida) | 18.7 MB | 18.7 MB |

Por encima de unos 35 dB la diferencia no se ve. La verificación sí detecta pérdidas: si una sola imagen que
se repite en cientos de páginas se degrada, la comparación marca todas esas páginas; y si un nivel baja la
fidelidad por debajo de su mínimo, el comprimido se rechaza y se conserva el original.

## 5. Herramientas y librerías (todas libres y gratuitas)

| Herramienta | Para qué | Licencia |
|---|---|---|
| **pikepdf** (Python, sobre **qpdf 12**) | Abrir el PDF, recorrer y reemplazar imágenes, reescribir con Flate nivel 9 y *object streams* | MPL 2.0 (qpdf: Apache 2.0) |
| **mozjpeg-lossless-optimization** | Reoptimizar JPEG sin pérdida (mozjpeg/jpegtran) | BSD |
| **Pillow** | Decodificar imágenes, recodificar en JPEG, escribir CCITT G4 (vía libtiff) | MIT-CMU |
| **NumPy** | Cuantizar a 16 o 4 grises, empaquetar bits, comparar píxeles | BSD |
| **pypdfium2** (PDFium) | Dibujar cada página y extraer su texto para la verificación | Apache 2.0 / BSD |
| **zlib** (biblioteca estándar de Python) | Compresión Flate de las imágenes en gris | zlib |

Se instalan con `python sistema/biblioteca.py instalar` (están en `requirements.txt`).

**Qué NO se usa y por qué:**
- **Ghostscript `/ebook` o `/screen`:** bajan la resolución de todo sin verificar.
- **Compresores en línea:** mandan el documento a terceros y no se pueden verificar.
- **JBIG2 con pérdida:** en modo con pérdida puede **cambiar un dígito por otro** en escaneos (caso conocido
  de las fotocopiadoras Xerox, 2013). Inaceptable en tablas de valores.
- **OCR para comparar:** en tablas escaneadas es inestable (un cambio mínimo mueve miles de palabras); por eso
  se mide la fidelidad de cada página (PSNR).

## 6. Si algo sale mal

| Síntoma | Qué pasa | Qué hacer |
|---|---|---|
| `NO SE USA (no pasó la verificación)` | El comprimido cambió el texto, las páginas o bajó de la fidelidad exigida | Se conserva el original: nada se perdió. Prueba un nivel menor o repórtalo |
| `NO SE USA (no pesa menos)` | El PDF ya estaba bien comprimido | Nada que hacer |
| `sigue pasando de 100 MB` | El nivel no alcanza para ese contenido | `--nivel 50 --recomprimir --id <id>`. Si ni el 80 alcanza, se puede dividir en tomos a mano (sin pérdida) |
| `OMITIDO ...: ya está comprimido` | Ya se comprimió antes | Para otro nivel: `--recomprimir` (parte del ya comprimido y lo verifica contra él) |
| `Falta una dependencia para comprimir` | No se ha instalado pikepdf u otra | `python sistema/biblioteca.py instalar` |

**Modo prueba:** `--salida CARPETA` deja los comprimidos en otra carpeta sin tocar la biblioteca ni el
manifiesto, para compararlos con calma antes de aplicar un nivel.
