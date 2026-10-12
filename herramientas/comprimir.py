"""
comprimir.py — PROCESO 4, COMPRIMIR: la herramienta `comprimir`.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import time
import warnings
import zlib
from pathlib import Path
from typing import Any

from herramientas.catalogo import huella_origen, problema_de_estructura, ruta_pdf
from herramientas.comun import (
    LIMITE_GITHUB_MB,
    UMBRAL_COMPRIMIR_MB,
    Analizador,
    bitacora,
    bloqueo,
    cargar_catalogo,
    cargar_manifiesto,
    guardar_manifiesto,
    hoy,
    reemplazar_archivo,
    salida,
    salir_con_error,
    sha256_archivo,
    temporal,
)

# ====================================================================================================
# Herramienta: comprimir
# ====================================================================================================
AYUDA_COMPRIMIR = """
biblioteca.py comprimir — Reduce el peso de los PDF grandes de la biblioteca (por defecto, los de más de 60 MB).

ES UN PROCESO APARTE (PROCESO 4, COMPRIMIR): solo se ejecuta cuando el usuario lo pide, y el agente le
PREGUNTA antes el nivel. Método completo, variaciones por tipo de PDF y mediciones: referencia/comprimir.md

NIVELES (el % es la INTENSIDAD, no una reducción garantizada: la reducción depende del contenido)
  10  Sin pérdida           Píxeles idénticos. Reoptimiza los JPEG (mozjpeg, sin tocar un píxel), pasa los
                            escaneos en blanco y negro (1 bit) a CCITT Group 4 y reescribe el PDF con qpdf
                            (Flate al máximo, object streams, sin objetos huérfanos). Se aplica en todos los niveles.
  30  RECOMENDADO           Pérdida invisible. Además, las imágenes que en realidad son gris neutro (tablas,
                            texto escaneado) se guardan con 16 niveles de gris, sin JPEG (o, si eso pesa más,
                            como en escaneos con ruido de papel, en JPEG gris calidad 90). Color real: intacto.
  50  Pérdida mínima        Además, las imágenes a color se recodifican en JPEG calidad 85.
  80  Pérdida visible       Gris con 4 niveles (se pierden rellenos grises claros), color en JPEG calidad 60 y
                            las imágenes de más de 1,600 px por lado se reducen. El texto sigue legible.

Cada PDF comprimido se VERIFICA contra el original antes de usarlo: mismo número de páginas, mismo texto
en cada página y, al dibujar cada página, píxeles idénticos (nivel 10) o una fidelidad mínima (PSNR):
38 dB (nivel 30), 32 dB (50) o 24 dB (80). Si no pasa, o no pesa menos, se conserva el original.

USO:
  python biblioteca.py comprimir --listar                    qué PDF pasan de 60 MB y de qué están hechos
  python biblioteca.py comprimir --nivel 30                  comprime todos los de más de 60 MB
  python biblioteca.py comprimir --nivel 30 --id ID [ID...]  solo esos (si pasan del umbral)
  --umbral MB        sube el umbral (60 por defecto). REGLA: los PDF de 60 MB o menos NUNCA se tocan,
                     ni en modo prueba ni aunque se pidan por --id; el umbral no puede bajar de 60
  --salida CARPETA   MODO PRUEBA: deja los comprimidos en CARPETA/<grupo>/<archivo>.pdf y su registro en
                     CARPETA/../registro.json, sin tocar la biblioteca ni el manifiesto
  --recomprimir      también los que ya se comprimieron (se parte del archivo ya comprimido)
  --dpi N            resolución de la verificación (100 por defecto)

En la biblioteca, el PDF comprimido reemplaza al original en documentos/ y el manifiesto guarda SIEMPRE
en "compresion" las dos huellas y los dos tamaños (sha256_original/bytes_original del PDF oficial y
sha256/bytes del comprimido), el nivel y el resultado de la verificación. biblioteca.py verificar exige que estén
completas. Así una nueva descarga idéntica del oficial sigue siendo SIN_CAMBIO y el Markdown sigue al día.
El reemplazo se hace dentro del candado y solo si el PDF en disco sigue siendo el que se comprimió: si otro
agente lo actualizó mientras tanto, el comprimido se descarta (nunca se retrocede a una reforma anterior).

CÓDIGOS DE SALIDA: 0 = todo bien (o nada que comprimir) · 1 = algún PDF no pasó la verificación o falló
"""


# pikepdf avisa en inglés cuando una máscara tiene alfa premultiplicado: no afecta a lo que se hace aquí
warnings.filterwarnings("ignore", category=UserWarning, module="pikepdf")


MB = 1_000_000
UMBRAL_MB = UMBRAL_COMPRIMIR_MB        # regla del proyecto: solo los PDF de MÁS de 60 MB
UMBRAL_COLOR = 24        # diferencia máxima entre canales R, G y B para considerar una imagen "gris neutro"

NIVELES: dict[int, dict[str, Any]] = {
    10: {"nombre": "sin pérdida", "gris_bits": None, "jpeg_color": None, "max_lado": None, "jpeg_gris": None,
         "psnr_min": None,
         "aviso": "Ninguna pérdida: el PDF se ve idéntico píxel por píxel. Reduce poco (típicamente 5-25 %)."},
    30: {"nombre": "recomendado (pérdida invisible)", "gris_bits": 4, "jpeg_color": None, "max_lado": None,
         "jpeg_gris": 90, "psnr_min": 38.0,
         "aviso": "Las tablas y el texto escaneados se guardan con 16 grises: a la vista es idéntico. El color "
                  "real y el texto no se tocan. En el Código Fiscal de la CDMX redujo 66 % (163.9 -> 56.5 MB)."},
    50: {"nombre": "pérdida mínima", "gris_bits": 4, "jpeg_color": 85, "max_lado": None, "jpeg_gris": 85,
         "psnr_min": 32.0,
         "aviso": "Además recomprime las fotos e imágenes a color (JPEG calidad 85): pueden aparecer "
                  "artefactos leves en degradados si se amplían mucho."},
    80: {"nombre": "pérdida visible", "gris_bits": 2, "jpeg_color": 60, "max_lado": 1600, "jpeg_gris": 70,
         "psnr_min": 24.0,
         "aviso": "Gris con solo 4 niveles (los rellenos grises claros se vuelven blancos), fotos en JPEG "
                  "calidad 60 y menos resolución en imágenes grandes. El texto se lee, pero la calidad baja "
                  "de forma visible. Úsalo solo si los otros niveles no bastan."},
}


def _dependencias():
    try:
        import mozjpeg_lossless_optimization  # noqa: F401
        import numpy  # noqa: F401
        import pikepdf  # noqa: F401
        import pypdfium2  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as e:
        salir_con_error(f"Falta una dependencia para comprimir ({e.name}). Ejecuta: python biblioteca.py instalar")


# ---------------------------------------------------------------------------
# Diagnóstico: de qué está hecho el PDF
# ---------------------------------------------------------------------------
def _es_imagen(obj) -> bool:
    import pikepdf
    return isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Image"


def _mascaras(pdf) -> set:
    import pikepdf
    usadas = set()
    for obj in pdf.objects:
        if _es_imagen(obj):
            for clave in ("/SMask", "/Mask"):
                m = obj.get(clave)
                if isinstance(m, pikepdf.Stream):
                    usadas.add(m.objgen)
    return usadas


def _filtros(obj) -> list[str]:
    import pikepdf
    f = obj.get("/Filter")
    if f is None:
        return []
    return [str(x) for x in (f if isinstance(f, pikepdf.Array) else [f])]


def _pil(obj):
    import pikepdf
    try:
        return pikepdf.PdfImage(obj).as_pil_image()
    except Exception:  # noqa: BLE001 - formato raro (CMYK con Decode, JBIG2...): se deja como está
        return None


def _gris_neutro(im) -> bool:
    import numpy as np
    if im.mode in ("L", "LA", "1"):
        return True
    if im.mode not in ("RGB", "RGBA", "P"):
        return False
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    return max(int(np.abs(a[..., 0] - a[..., 1]).max()), int(np.abs(a[..., 1] - a[..., 2]).max())) <= UMBRAL_COLOR


def diagnosticar(ruta: Path) -> dict:
    """Peso por tipo de contenido y clasificación del PDF (sirve para advertir qué nivel conviene)."""
    import pikepdf
    with pikepdf.open(ruta) as pdf:
        mascaras = _mascaras(pdf)
        gris = color = otras = bn = n_gris = n_color = n_bn = 0
        total_streams = 0
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Stream):
                continue
            n = len(obj.read_raw_bytes())
            total_streams += n
            if not _es_imagen(obj):
                continue
            if obj.get("/BitsPerComponent") == 1 and not obj.get("/ImageMask") and obj.objgen not in mascaras:
                bn += n
                n_bn += 1
                continue
            if obj.objgen in mascaras or obj.get("/ImageMask") or obj.get("/BitsPerComponent") != 8:
                otras += n
                continue
            im = _pil(obj)
            if im is None:
                otras += n
            elif _gris_neutro(im):
                gris += n
                n_gris += 1
            else:
                color += n
                n_color += 1
        paginas = len(pdf.pages)
    imagenes = gris + color + otras + bn
    if imagenes < 0.3 * total_streams:
        tipo = "texto y gráficos vectoriales (poco peso en imágenes)"
    elif bn >= 0.7 * imagenes:
        tipo = "escaneo en blanco y negro (1 bit)"
    elif gris >= 0.7 * imagenes:
        tipo = "tablas o texto escaneados (imágenes en gris)"
    elif color >= 0.7 * imagenes:
        tipo = "fotos o imágenes a color"
    else:
        tipo = "mixto (tablas en gris y fotos a color)"
    return {"paginas": paginas, "tipo": tipo, "imagenes_gris": n_gris, "imagenes_color": n_color,
            "imagenes_bn": n_bn, "mb_bn": round(bn / MB, 1),
            "mb_gris": round(gris / MB, 1), "mb_color": round(color / MB, 1), "mb_otras": round(otras / MB, 1),
            "mb_resto": round((total_streams - imagenes) / MB, 1)}


# ---------------------------------------------------------------------------
# Compresión
# ---------------------------------------------------------------------------
def _optimizar_jpeg(pdf) -> int:
    """JPEG sin pérdida: tablas de Huffman óptimas y modo progresivo (mozjpeg). Cada imagen se comprueba:
    si no queda idéntica píxel por píxel, o no se reduce, se conserva la original."""
    import mozjpeg_lossless_optimization as mjo
    import numpy as np
    import pikepdf
    from PIL import Image
    ahorro = 0
    for obj in pdf.objects:
        if not _es_imagen(obj) or _filtros(obj) != ["/DCTDecode"]:
            continue
        crudo = obj.read_raw_bytes()
        try:
            nuevo = mjo.optimize(crudo)
            a, b = Image.open(io.BytesIO(crudo)), Image.open(io.BytesIO(nuevo))
            igual = a.mode == b.mode and a.size == b.size and np.array_equal(np.asarray(a), np.asarray(b))
        except Exception:  # noqa: BLE001
            continue
        if igual and len(nuevo) < len(crudo):
            obj.write(nuevo, filter=pikepdf.Name.DCTDecode, decode_parms=obj.get("/DecodeParms"))
            ahorro += len(crudo) - len(nuevo)
    return ahorro


def _a_g4(pdf) -> int:
    """Escaneos en blanco y negro (1 bit) guardados con Flate -> CCITT Group 4, el formato estándar para
    ellos. Es SIN PÉRDIDA: se decodifica el G4 resultante y se exige que sea idéntico bit a bit."""
    import numpy as np
    import pikepdf
    from PIL import Image
    ahorro = 0
    # también las que funcionan como máscara (/SMask, /Mask) de otra imagen: sus bits quedan idénticos
    for obj in pdf.objects:
        if (not _es_imagen(obj) or obj.get("/BitsPerComponent") != 1 or obj.get("/ImageMask") or "/Decode" in obj
                or _filtros(obj) in (["/CCITTFaxDecode"], ["/JBIG2Decode"])):
            continue
        if str(obj.get("/ColorSpace")) != "/DeviceGray":
            continue
        try:
            w, h = int(obj.Width), int(obj.Height)
            filas = np.frombuffer(obj.read_bytes(), np.uint8)[: h * ((w + 7) // 8)].reshape(h, (w + 7) // 8)
            bits = np.unpackbits(filas, axis=1)[:, :w]          # 1 = blanco, 0 = negro (DeviceGray)
            im = Image.fromarray((bits * 255).astype(np.uint8)).convert("1")
            buf = io.BytesIO()
            im.save(buf, "TIFF", compression="group4", tiffinfo={278: h})   # una sola tira
            tif: Any = Image.open(buf)          # TIFF: tag_v2 no está en los tipos de PIL
            if len(tif.tag_v2[273]) != 1:
                continue
            buf.seek(tif.tag_v2[273][0])
            datos = buf.read(tif.tag_v2[279][0])
            # comprobación: el TIFF decodificado debe ser idéntico a la imagen original
            if not np.array_equal(np.asarray(tif.convert("L")) > 127, bits.astype(bool)):
                continue
        except Exception:  # noqa: BLE001
            continue
        antes = len(obj.read_raw_bytes())
        if len(datos) >= antes:
            continue
        # El códec CCITT guarda los bits 0 como corridas "blancas". Si en la imagen el 0 es negro
        # (PhotometricInterpretation = 1, como en DeviceGray), el PDF debe decir BlackIs1 = true para que
        # esas corridas vuelvan a ser 0. (La verificación de página detecta cualquier inversión.)
        cero_es_negro = tif.tag_v2.get(262, 0) == 1
        obj.write(datos, filter=pikepdf.Name.CCITTFaxDecode,
                  decode_parms=pikepdf.Dictionary(K=-1, Columns=w, Rows=h, BlackIs1=cero_es_negro))
        ahorro += antes - len(datos)
    return ahorro


def _jpeg_gris(obj, im, calidad: int) -> int:
    """Respaldo para escaneos en gris con mucho ruido de papel, donde los pocos grises sin JPEG pesan más:
    JPEG en escala de grises (quita los canales de color que sobran) a calidad alta."""
    import mozjpeg_lossless_optimization as mjo
    import pikepdf
    buf = io.BytesIO()
    im.convert("L").save(buf, "JPEG", quality=calidad, optimize=True, progressive=True)
    datos = mjo.optimize(buf.getvalue())
    antes = len(obj.read_raw_bytes())
    if len(datos) >= antes * 0.9:      # solo si ahorra al menos 10 %: si no, no vale la recompresión
        return 0
    obj.write(datos, filter=pikepdf.Name.DCTDecode)
    obj.ColorSpace = pikepdf.Name.DeviceGray
    obj.BitsPerComponent = 8
    if "/Decode" in obj:
        del obj["/Decode"]
    return antes - len(datos)


def _a_gris(obj, im, bits: int) -> int:
    """Guarda la imagen como gris de 2**bits niveles, Flate nivel 9 sin predictor (con el ruido de un JPEG
    de origen, el predictor PNG comprime peor). DeviceGray con bpc=bits: no hace falta paleta."""
    import numpy as np
    import pikepdf
    n = 2 ** bits - 1
    q = np.round(np.asarray(im.convert("L")).astype(np.float32) / 255 * n).astype(np.uint8)
    filas = np.packbits(np.unpackbits(q[..., None], axis=-1)[..., 8 - bits:].reshape(q.shape[0], -1), axis=1)
    datos = zlib.compress(filas.tobytes(), 9)
    antes = len(obj.read_raw_bytes())
    if len(datos) >= antes:
        return 0
    obj.write(datos, filter=pikepdf.Name.FlateDecode)
    obj.ColorSpace = pikepdf.Name.DeviceGray
    obj.BitsPerComponent = bits
    if "/Decode" in obj:
        del obj["/Decode"]
    return antes - len(datos)


def _a_jpeg(obj, im, calidad: int, max_lado: int | None) -> int:
    """Recodifica una imagen a color en JPEG (y la reduce si pasa de max_lado y no tiene máscara)."""
    import mozjpeg_lossless_optimization as mjo
    import pikepdf
    from PIL import Image
    if im.mode not in ("RGB", "P", "RGBA"):
        return 0
    rgb = im.convert("RGB")
    tiene_mascara = obj.get("/SMask") is not None or obj.get("/Mask") is not None
    if max_lado and max(rgb.size) > max_lado and not tiene_mascara:
        escala = max_lado / max(rgb.size)
        rgb = rgb.resize((max(1, round(rgb.width * escala)), max(1, round(rgb.height * escala))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=calidad, optimize=True, progressive=True)
    datos = mjo.optimize(buf.getvalue())
    antes = len(obj.read_raw_bytes())
    if len(datos) >= antes:
        return 0
    obj.write(datos, filter=pikepdf.Name.DCTDecode)
    obj.ColorSpace = pikepdf.Name.DeviceRGB
    obj.BitsPerComponent = 8
    obj.Width, obj.Height = rgb.width, rgb.height
    if "/Decode" in obj:
        del obj["/Decode"]
    return antes - len(datos)


def comprimir(origen: Path, destino: Path, nivel: int) -> dict:
    import pikepdf
    cfg = NIVELES[nivel]
    pikepdf.settings.set_flate_compression_level(9)
    res: dict[str, int] = {"gris": 0, "color": 0, "ahorro_gris": 0, "ahorro_color": 0}
    with pikepdf.open(origen) as pdf:
        # primero lo SIN pérdida: así cada paso con pérdida se compara contra la mejor versión sin pérdida
        res["ahorro_jpeg_sin_perdida"] = _optimizar_jpeg(pdf)
        res["ahorro_g4"] = _a_g4(pdf)
        if cfg["gris_bits"] or cfg["jpeg_color"]:
            mascaras = _mascaras(pdf)
            for obj in list(pdf.objects):
                if not _es_imagen(obj) or obj.objgen in mascaras or obj.get("/ImageMask") \
                        or obj.get("/BitsPerComponent") != 8:
                    continue
                im = _pil(obj)
                if im is None:
                    continue
                if _gris_neutro(im):
                    if cfg["gris_bits"]:
                        a = _a_gris(obj, im, cfg["gris_bits"])
                        if not a and cfg["jpeg_gris"]:
                            a = _jpeg_gris(obj, im, cfg["jpeg_gris"])
                        res["gris"] += bool(a)
                        res["ahorro_gris"] += a
                elif cfg["jpeg_color"]:
                    a = _a_jpeg(obj, im, cfg["jpeg_color"], cfg["max_lado"])
                    res["color"] += bool(a)
                    res["ahorro_color"] += a
        destino.parent.mkdir(parents=True, exist_ok=True)
        # deterministic_id: el /ID del PDF sale del contenido y no del azar, así el mismo PDF comprimido con el mismo
        # nivel da siempre el mismo archivo (la misma huella: git no ve un cambio donde no lo hay)
        pdf.save(destino, compress_streams=True, recompress_flate=True,
                 stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate, deterministic_id=True)
    return res


# ---------------------------------------------------------------------------
# Verificación contra el original
# ---------------------------------------------------------------------------
def verificar(original: Path, comprimido: Path, nivel: int, dpi: int) -> dict:
    """Mismo número de páginas, mismo texto por página y fidelidad de la imagen de cada página."""
    import numpy as np
    import pypdfium2 as pdfium
    minimo = NIVELES[nivel]["psnr_min"]
    do, dc = pdfium.PdfDocument(str(original)), pdfium.PdfDocument(str(comprimido))
    r: dict[str, Any] = {"paginas": [len(do), len(dc)], "texto_distinto": [], "paginas_cambiadas": 0,
         "psnr_min_db": None, "psnr_prom_db": None, "paginas_bajo_minimo": []}
    try:
        if len(do) != len(dc):
            r["ok"] = False
            return r
        valores = []
        for i in range(len(do)):
            po, pc = do[i], dc[i]
            to, tc = po.get_textpage(), pc.get_textpage()
            if to.get_text_range() != tc.get_text_range():
                r["texto_distinto"].append(i + 1)
            a = po.render(scale=dpi / 72).to_numpy()
            b = pc.render(scale=dpi / 72).to_numpy()
            if a.shape != b.shape:
                r["paginas_bajo_minimo"].append(i + 1)
            elif not np.array_equal(a, b):
                r["paginas_cambiadas"] += 1
                mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
                v = 99.0 if mse == 0 else 10 * float(np.log10(255 ** 2 / mse))
                valores.append(v)
                if minimo is None or v < minimo:
                    r["paginas_bajo_minimo"].append(i + 1)
            for x in (to, tc, po, pc):
                x.close()
        if valores:
            r["psnr_min_db"] = round(min(valores), 1)
            r["psnr_prom_db"] = round(sum(valores) / len(valores), 1)
        r["ok"] = not r["texto_distinto"] and not r["paginas_bajo_minimo"]
        return r
    finally:
        do.close()
        dc.close()


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
def _candidatos(args, catalogo, manifiesto) -> list[dict]:
    por_id = {d["id"]: d for d in catalogo["documentos"]}
    ids = args.id or list(manifiesto)
    salida = []
    for doc_id in ids:
        doc = por_id.get(doc_id)
        if not doc or doc_id not in manifiesto:
            if args.id:
                print(f"OMITIDO {doc_id}: no tiene PDF guardado")
            continue
        problema = problema_de_estructura(doc)    # solo se trabaja dentro de la estructura
        if problema:
            print(f"OMITIDO {doc_id}: {problema}")
            continue
        pdf = ruta_pdf(doc)
        if pdf.suffix != ".pdf":                         # un Word o una página oficial se guarda tal cual
            if args.id:
                print(f"OMITIDO {doc_id}: su documento oficial no es PDF ({pdf.suffix}); se conserva tal cual")
            continue
        if not pdf.exists() or pdf.stat().st_size / MB <= args.umbral:
            if args.id and pdf.exists():
                print(f"OMITIDO {doc_id}: pesa {pdf.stat().st_size / MB:.1f} MB; los PDF de {args.umbral:g} MB o "
                      "menos no se tocan")
            continue
        salida.append(doc)
    return salida


def main():
    p = Analizador(description="Comprimir los PDF grandes de la biblioteca.",
                         formatter_class=argparse.RawDescriptionHelpFormatter, epilog=AYUDA_COMPRIMIR)
    p.add_argument("--listar", action="store_true", help="solo lista los candidatos y de qué están hechos")
    p.add_argument("--nivel", type=int, choices=sorted(NIVELES), help="10, 30 (recomendado), 50 u 80")
    p.add_argument("--id", nargs="+", help="solo estos documentos")
    p.add_argument("--umbral", type=float, default=UMBRAL_MB, help=f"MB (por defecto {UMBRAL_MB})")
    p.add_argument("--salida", help="MODO PRUEBA: carpeta donde dejar los comprimidos (no toca la biblioteca)")
    p.add_argument("--recomprimir", action="store_true", help="incluye los que ya se comprimieron")
    p.add_argument("--dpi", type=int, default=100)
    p.add_argument("--json", action="store_true", help="salida en JSON por stdout (el texto va a stderr)")
    args = p.parse_args()
    salida.iniciar(args.json, "comprimir")
    if not args.listar and args.nivel is None:
        p.error("indica --nivel 10, 30, 50 u 80 (o --listar)")
    if args.umbral < UMBRAL_COMPRIMIR_MB:
        p.error(f"el umbral no puede bajar de {UMBRAL_COMPRIMIR_MB} MB: los PDF más ligeros no se tocan")
    _dependencias()

    catalogo, manifiesto = cargar_catalogo(), cargar_manifiesto()
    candidatos = _candidatos(args, catalogo, manifiesto)
    salida.poner(umbral_mb=args.umbral, candidatos=[d["id"] for d in candidatos])
    if not candidatos:
        print(f"PENDIENTES: 0 — ningún PDF pasa de {args.umbral:g} MB. Nada que comprimir.")
        return
    if args.listar:
        print(f"PDF de más de {args.umbral:g} MB: {len(candidatos)}")
        for doc in candidatos:
            pdf = ruta_pdf(doc)
            reg = manifiesto[doc["id"]]
            d = diagnosticar(pdf)
            ya = reg.get("compresion")
            print(f"\n  {doc['id']}\n    {pdf.stat().st_size / MB:.1f} MB · {d['paginas']} páginas · {d['tipo']}"
                  + (f" · YA COMPRIMIDO (nivel {ya['nivel']})" if ya else ""))
            print(f"    imágenes en gris: {d['imagenes_gris']} ({d['mb_gris']} MB) · a color: {d['imagenes_color']} "
                  f"({d['mb_color']} MB) · blanco y negro: {d['imagenes_bn']} ({d['mb_bn']} MB) · otras: "
                  f"{d['mb_otras']} MB · texto y demás: {d['mb_resto']} MB")
            salida.agregar("diagnostico", {"id": doc["id"], "mb": round(pdf.stat().st_size / MB, 1),
                                           "ya_comprimido": ya["nivel"] if ya else None, **d})
        print("\nNiveles: " + " · ".join(f"{n} = {c['nombre']}" for n, c in NIVELES.items()))
        return

    cfg = NIVELES[args.nivel]
    modo_prueba = Path(args.salida).resolve() if args.salida else None
    print(f"Nivel {args.nivel} ({cfg['nombre']}): {cfg['aviso']}")
    if modo_prueba:
        print(f"MODO PRUEBA: los comprimidos quedan en {modo_prueba} (la biblioteca no se toca)")
    registro_prueba = (modo_prueba.parent / "registro.json") if modo_prueba else None
    fallas = 0
    for n, doc in enumerate(candidatos, 1):
        pdf = ruta_pdf(doc)
        reg = manifiesto[doc["id"]]
        if reg.get("compresion") and not args.recomprimir and not modo_prueba:
            print(f"[{n}/{len(candidatos)}] OMITIDO {doc['id']}: ya está comprimido (nivel "
                  f"{reg['compresion']['nivel']}); usa --recomprimir para otro nivel")
            continue
        t0 = time.time()
        antes = pdf.stat().st_size
        huella_antes = sha256_archivo(pdf)       # para comprobar, al reemplazar, que nadie lo cambió entre tanto
        print(f"[{n}/{len(candidatos)}] {doc['id']} ({antes / MB:.1f} MB) ...", flush=True)
        tmp = temporal() / f"{doc['id']}.pdf"
        try:
            detalle = comprimir(pdf, tmp, args.nivel)
            despues = tmp.stat().st_size
            v = verificar(pdf, tmp, args.nivel, args.dpi)
        except Exception as e:  # noqa: BLE001 - un PDF que no se puede procesar no detiene a los demás
            fallas += 1
            print(f"      FALLA: {type(e).__name__}: {str(e)[:200]}")
            bitacora({"herramienta": "comprimir", "id": doc["id"], "resultado": "FALLA", "detalle": str(e)[:200]})
            salida.agregar("resultados", {"id": doc["id"], "resultado": "FALLA", "detalle": str(e)[:200]})
            continue
        fidelidad = ("píxeles idénticos" if not v["paginas_cambiadas"] else
                     f"{v['paginas_cambiadas']} páginas con cambios, PSNR mín {v['psnr_min_db']} dB / "
                     f"prom {v['psnr_prom_db']} dB")
        print(f"      {antes / MB:.1f} -> {despues / MB:.1f} MB ({1 - despues / antes:.0%} menos) · páginas "
              f"{v['paginas'][0]}/{v['paginas'][1]} · texto distinto en {len(v['texto_distinto'])} · {fidelidad}")
        if not v["ok"] or despues >= antes:
            fallas += not v["ok"]
            motivo = "no pasó la verificación" if not v["ok"] else "no pesa menos"
            print(f"      NO SE USA ({motivo}): se conserva el original")
            bitacora({"herramienta": "comprimir", "id": doc["id"], "resultado": "DESCARTADO",
                            "nivel": args.nivel, "detalle": motivo})
            salida.agregar("resultados", {"id": doc["id"], "resultado": "DESCARTADO", "detalle": motivo, "verificacion": v})
            tmp.unlink(missing_ok=True)
            continue
        # SIEMPRE las dos huellas y los dos tamaños: los del PDF OFICIAL (aunque se recomprima, se conservan
        # los del oficial, no los de un comprimido intermedio) y los del comprimido. Sirven para comparar.
        # Los del oficial se toman del manifiesto FRESCO dentro del candado (abajo).
        info = {"nivel": args.nivel, "sha256": sha256_archivo(tmp), "bytes": despues, "fecha": hoy(),
                "verificacion": {"dpi": args.dpi, "paginas": v["paginas"][0], "texto": "idéntico",
                                 "paginas_cambiadas": v["paginas_cambiadas"], "psnr_min_db": v["psnr_min_db"],
                                 "psnr_prom_db": v["psnr_prom_db"]},
                "detalle": {"imagenes_a_gris": detalle["gris"], "imagenes_color_recodificadas": detalle["color"]}}
        if modo_prueba:
            info["sha256_original"] = huella_origen(reg)
            info["bytes_original"] = (reg.get("compresion") or {}).get("bytes_original") or antes
            destino = modo_prueba.joinpath(*doc["grupo"].split("/"), doc["archivo"] + ".pdf")
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.unlink(missing_ok=True)          # un comprimido anterior de la misma prueba
            shutil.move(str(tmp), destino)
            datos = json.loads(registro_prueba.read_text(encoding="utf-8")) if registro_prueba.exists() else {}
            datos[doc["id"]] = {**info, "archivo": destino.relative_to(modo_prueba).as_posix()}
            registro_prueba.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            with bloqueo():
                man = cargar_manifiesto()
                reg_actual = man.get(doc["id"])
                motivo = None
                if reg_actual is None:
                    motivo = "el documento se retiró del manifiesto mientras se comprimía"
                elif sha256_archivo(pdf) != huella_antes:
                    motivo = "el PDF cambió mientras se comprimía (otro agente lo actualizó): vuelve a ejecutar comprimir"
                if motivo:
                    fallas += 1
                    print(f"      NO SE USA: {motivo}")
                    bitacora({"herramienta": "comprimir", "id": doc["id"], "resultado": "DESCARTADO",
                                    "nivel": args.nivel, "detalle": motivo})
                    salida.agregar("resultados", {"id": doc["id"], "resultado": "DESCARTADO", "detalle": motivo})
                    tmp.unlink(missing_ok=True)
                    continue
                previa = reg_actual.get("compresion") or {}
                info["sha256_original"] = huella_origen(reg_actual)
                info["bytes_original"] = previa.get("bytes_original") or antes
                reemplazar_archivo(tmp, pdf)
                reg_actual.update({"sha256": info["sha256"], "bytes": despues, "compresion": info})
                guardar_manifiesto(man)
            bitacora({"herramienta": "comprimir", "id": doc["id"], "resultado": "OK", "nivel": args.nivel,
                            "mb": [round(antes / MB, 1), round(despues / MB, 1)], "psnr_min_db": v["psnr_min_db"]})
        aviso = f" · sigue pasando de {LIMITE_GITHUB_MB} MB: prueba un nivel mayor" if despues / MB >= LIMITE_GITHUB_MB else ""
        print(f"      OK en {time.time() - t0:.0f} s{aviso}")
        salida.agregar("resultados", {"id": doc["id"], "resultado": "OK", "nivel": args.nivel, "mb_antes": round(antes / MB, 1),
                                      "mb_despues": round(despues / MB, 1), "verificacion": v, "modo_prueba": bool(modo_prueba)})
    print(f"\nTerminado: {len(candidatos)} candidato(s), {fallas} con falla de verificación.")
    salida.poner(fallas=fallas)
    sys.exit(1 if fallas else 0)
