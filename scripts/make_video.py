#!/usr/bin/env python3
"""Veltora Studio — fase 2 (desplegada 13-nov-2026).
Genera un video desde un tema cualquiera, sin adaptación manual por tema:
investigación (Wikipedia ES + DuckDuckGo Lite) -> guion (Pollinations texto
en llamadas cortas de ~50 palabras) -> imágenes (Pollinations sana) ->
voz Kokoro Santa em_santa -> render FFmpeg con subtítulos quemados.
Formatos: vertical 9:16 (720x1280, TikTok/Reels/Shorts) y horizontal 16:9
(1920x1080, YouTube largo). Duración objetivo: 30/35/45 s (5 clips) o 60 s
(7 clips). La duración manda el número de palabras del guion (~2,6/s).
Modo curar (fase 3, 14-nov-2026): --modo curar --video-url <enlace YouTube>
analiza el video viral (transcripción + metadatos, sin llave) y genera un
guion nuevo con su fórmula, sin copiarlo. Variantes: mismo tema cambiando
cosas, o la misma fórmula con otro tema (--tema-nuevo).
Reglas heredadas y verificadas (13-nov-2026):
- Pollinations texto anónimo: 1 llamada de ~110 palabras da HTTP 400
  output_token_limit_exceeded; llamadas cortas (~50 palabras) con
  instrucción CORTA funcionan; sin "no inventes" el modelo inventa datos.
- El mismo 400 lo dispara pedir "termina exactamente con..." junto con
  datos (run #8, 14-nov-2026): datos+CTA da 400, datos sin CTA da 200,
  CTA sin datos da 200. El CTA se añade al final del guion, no se pide
  al modelo. El cuerpo del error HTTP se registra en el log.
- Pollinations texto anónimo: límite por IP, 1 llamada a la vez y al menos
  30 s entre llamadas; al pasarlo devuelve HTTP 402 con cabecera
  Retry-After (fallo real 13-nov-2026, run #7: la 2ª llamada del guion
  dio 402 tres veces seguidas). El script espera 35 s entre llamadas de
  texto y respeta Retry-After ante 402/429 (hasta 6 reintentos).
- Pollinations imágenes: sin marcas en el prompt (una marca lo bloquea);
  descargas con curl sin -C - (Pollinations no soporta rangos, rc 33).
- Subtítulos .ass con PlayResX/Y del formato; .srt sin PlayRes escala la
  fuente ~4,4x y se corta.
- Sin datos verificados NO hay video: si la investigación falla, el script
  falla con error claro. No existe fallback de otro tema.
"""
import argparse
import html as htmlmod
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(BASE, "work")
# OUT en la raíz del repo (no en scripts/): el workflow busca out/video.mp4
# y out/report.json desde la raíz.
OUT = os.path.join(os.path.dirname(BASE), "out")
IMG_DIR = os.path.join(WORK, "img")

UA = {"User-Agent": "VeltoraStudio/2.0 (contacto: video educativo sin fines comerciales)"}
REPORT = {"fases": {}, "exito": False, "version": "fase2"}

CTA = "Sígueme si quieres más historias de relojes imposibles."
PALABRAS_POR_SEGUNDO = 2.2  # ~77 palabras para 35 s, ~132 para 60 s.
# Medido 13-nov-2026: guiones con muchas cifras van más lentos en Kokoro
# (Daytona, 90 palabras -> 46,5 s = 1,9 pal/s; Grandmaster 83 -> 34,9 s;
# Astron 101 -> 39,4 s). 2,2 deja el video dentro de 30-45 s y el reintento
# de velocidad corrige el resto.

# Música de fondo (decisión del usuario 13-nov-2026: SÍ, sin derechos).
# Pista: "Lovely Piano Song", de Kevin MacLeod, dominio público CC0
# (FreePD, cerrado en 2025; archivo verificado contra el original).
# Ver la procedencia completa en assets/MUSICA-LICENCIA.md.
MUSICA_TITULO = "Lovely Piano Song"
MUSICA_AUTOR = "Kevin MacLeod"
MUSICA_LICENCIA = "CC0 1.0 Universal (dominio público)"
MUSICA_DURACION_S = 95.8  # más larga que el video más largo (60 s): solo recorte
MUSICA_SHA1_B32 = "VH37BKF2IFY4U6TMAMBZFMU65UB6TXZP"  # digest Wayback CDX
MUSICA_URLS = [
    # Archivo original en el Internet Archive (captura 2019-04-03).
    "https://web.archive.org/web/20190403072328id_/https://freepd.com/music/Lovely%20Piano%20Song.mp3",
    # Espejo (copia bit a bit del original, mismo digest).
    "https://raw.githubusercontent.com/SoundSafari/CC0-1.0-Music/main/freepd.com/Lovely%20Piano%20Song.mp3",
]
MUSICA_VOLUMEN = 0.2  # ~-14 dB: audible por debajo de la voz, sin taparla

STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "un", "una", "unos", "unas",
    "en", "y", "con", "por", "para", "su", "sus", "más", "mas", "que",
    "es", "son", "se", "al", "lo", "como", "este", "esta", "esto",
}

# Imágenes genéricas de relojería, SIN marcas (una marca en el prompt lo
# bloquea en Pollinations, verificado 13-nov-2026). 5 clips para <=45 s,
# 7 clips para 60 s.
PROMPTS_IMAGEN = [
    "Macro close-up of a luxury mechanical chronograph wristwatch dial, dark background, dramatic studio lighting, photorealistic",
    "Tiny watch movement gears and balance wheel, macro photography, gold and steel components, dark background",
    "Luxury watch auction room, elegant bidders and display case, cinematic warm light, photorealistic",
    "Vintage race track at sunset, classic sports cars, cinematic golden light, photorealistic",
    "Elegant luxury wristwatch on a man's wrist, dark suit cuff, cinematic light, photorealistic",
    "Close-up of a chronograph subdial with vintage patina, textured dial, studio photography, dark background",
    "Collection of vintage luxury wristwatches in a dark wooden box, warm light, photorealistic",
]


def fase(nombre):
    return _Fase(nombre)


class _Fase:
    def __init__(self, nombre):
        self.nombre = nombre

    def __enter__(self):
        self.t0 = time.time()
        print(f"[{self.nombre}] inicio", flush=True)
        return self

    def __exit__(self, *exc):
        dt = time.time() - self.t0
        REPORT["fases"][self.nombre] = {"segundos": round(dt, 1)}
        print(f"[{self.nombre}] fin: {dt:.1f} s", flush=True)
        return False


def _retry_after_de_cabeceras(ruta_headers, defecto):
    """Lee Retry-After de un archivo de cabeceras HTTP guardado por curl
    (opción -D). Devuelve `defecto` si no hay cabecera o no se puede leer."""
    try:
        with open(ruta_headers, encoding="utf-8", errors="replace") as f:
            for linea in f:
                if linea.lower().startswith("retry-after:"):
                    return float(linea.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return defecto


def bajar_archivo(url, ruta):
    """Descarga binaria con curl: reintentos contra cortes de red.
    Sin -C - (resume): Pollinations no soporta rangos de bytes y curl
    falla con rc 33 cuando el archivo ya existe (verificado 13-nov-2026);
    cada intento descarga desde cero y se borra el archivo previo.
    Ante HTTP 402/429 de Pollinations (límite por IP), se respeta la
    cabecera Retry-After antes de reintentar (fallo real 13-nov-2026);
    sin espaciamiento proactivo entre imágenes, solo espera al fallar."""
    if os.path.exists(ruta):
        os.remove(ruta)
    es_pollinations = "pollinations.ai" in url
    intentos = 4 if es_pollinations else 3
    headers_path = ruta + ".headers"
    espera_base = 45
    for i in range(intentos):
        if es_pollinations:
            args = ["curl", "-fsSL", "-D", headers_path, "-o", ruta, url]
        else:
            args = ["curl", "-fsSL", "--retry", "2", "--retry-all-errors",
                    "--retry-delay", "5", "-o", ruta, url]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(ruta) and os.path.getsize(ruta) > 10000:
            if os.path.exists(headers_path):
                os.remove(headers_path)
            return os.path.getsize(ruta)
        print(f"curl intento {i}: rc={r.returncode} {r.stderr.strip()[-200:]}", flush=True)
        if i < intentos - 1:
            espera = 5 * (i + 1)
            if es_pollinations and ("402" in r.stderr or "429" in r.stderr):
                ra = _retry_after_de_cabeceras(headers_path, espera_base)
                espera = ra + 5
                espera_base = min(espera_base * 2, 180)
                print(f"Pollinations imagen: límite 402/429, espero {espera:.0f} s (Retry-After)", flush=True)
            time.sleep(espera)
    if os.path.exists(headers_path):
        os.remove(headers_path)
    raise RuntimeError(f"no se pudo bajar {url}")


def http_get(url, timeout=45, intentos=2, headers=None):
    """GET de texto. Un 4xx no se reintenta."""
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(
                url, headers={**UA, "Connection": "close", **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise
            ultimo = e
            print(f"http intento {i}: HTTP {e.code}", flush=True)
        except Exception as e:
            ultimo = e
            print(f"http intento {i} falló: {e}", flush=True)
        time.sleep(5 * (i + 1))
    raise ultimo


# Límite de ritmo de Pollinations Classic API (texto anónimo): 1 llamada a
# la vez, al menos 30 s entre llamadas; al pasarlo devuelve HTTP 402 con
# cabecera Retry-After. El runner de Actions comparte IP con muchos
# usuarios, así que el límite se alcanza fácil (fallo real 13-nov-2026,
# run #7: la 2ª llamada del guion dio 402 tres veces seguidas con
# reintentos a 8 s). Espaciamiento proactivo de 35 s entre llamadas.
POLL_TEXT_MIN_GAP_S = 35
_ultima_llamada_texto = 0.0


def _espera_retry_after(headers, defecto):
    """Segundos a esperar según la cabecera Retry-After (+5 s de margen).
    `headers` es el mapping de cabeceras de la respuesta (o None)."""
    espera = defecto
    ra = None
    try:
        if headers is not None:
            ra = headers.get("Retry-After")
            if ra is None:
                # dict plano (no HTTPMessage): la clave puede venir en minúscula
                ra = headers.get("retry-after")
        if ra is not None:
            espera = float(ra)
    except (TypeError, ValueError):
        pass
    return max(0.0, espera) + 5


def pollinations_texto(url, timeout=40, max_intentos=6):
    """GET a text.pollinations.ai con límite de ritmo: al menos 35 s desde
    la llamada de texto anterior, y ante HTTP 402/429 se respeta la
    cabecera Retry-After con reintentos de espera creciente. Otros 4xx
    (p. ej. 400 output_token_limit_exceeded) no se reintentan: los
    gestiona el mecanismo de variantes de generar_guion."""
    global _ultima_llamada_texto
    ahora = time.time()
    espera = POLL_TEXT_MIN_GAP_S - (ahora - _ultima_llamada_texto)
    if espera > 0:
        print(f"Pollinations texto: espero {espera:.0f} s desde la llamada "
              f"anterior (límite 30 s entre llamadas)", flush=True)
        time.sleep(espera)
    ultimo = None
    espera_base = 30
    for i in range(max_intentos):
        try:
            req = urllib.request.Request(
                url, headers={**UA, "Connection": "close"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                _ultima_llamada_texto = time.time()
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            _ultima_llamada_texto = time.time()
            # Registrar el CUERPO del error: sin él el 400 del run #8 no
            # se pudo diagnosticar (el log solo decía "HTTP Error 400").
            # La lectura puede fallar con IncompleteRead si el servidor
            # cierra la conexión: usar entonces los bytes parciales.
            try:
                cuerpo = e.read().decode("utf-8", "replace")
            except BaseException as _e:
                parcial = getattr(_e, "partial", b"") or b""
                try:
                    cuerpo = parcial.decode("utf-8", "replace")
                except Exception:
                    cuerpo = ""
            if not cuerpo and e.code == 400:
                # La lectura urllib del cuerpo a veces falla porque el
                # servidor cierra la conexión (IncompleteRead con 0 bytes);
                # un intento con curl suele entregar el cuerpo. Solo para
                # 400 (diagnóstico): reemitir ante 402/429 gastaría cuota
                # de ritmo. Es una sola llamada extra por error.
                try:
                    _r = subprocess.run(
                        ["curl", "-s", "--max-time", "20",
                         "-A", UA["User-Agent"], url],
                        capture_output=True, text=True, errors="replace",
                        timeout=25)
                    if _r.stdout:
                        cuerpo = _r.stdout
                except Exception:
                    pass
            if cuerpo:
                print(f"Pollinations texto: HTTP {e.code} cuerpo={cuerpo[:300]!r}", flush=True)
            if e.code in (402, 429) and i < max_intentos - 1:
                espera = _espera_retry_after(e.headers, espera_base)
                espera_base = min(espera_base * 2, 120)
                print(f"Pollinations texto: HTTP {e.code}, reintento "
                      f"{i + 1}/{max_intentos} en {espera:.0f} s", flush=True)
                time.sleep(espera)
                continue
            raise
        except Exception as e:
            ultimo = e
            print(f"Pollinations texto intento {i} falló: {e}", flush=True)
            if i < max_intentos - 1:
                time.sleep(5 * (i + 1))
    raise ultimo


def contar_palabras(texto):
    return len([w for w in re.split(r"\s+", texto.strip()) if w])


def palabras_clave_tema(tema):
    """Palabras significativas del tema para validar el guion."""
    out = []
    for w in re.split(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", tema.lower()):
        if len(w) >= 3 and w not in STOPWORDS and w not in out:
            out.append(w)
    return out[:3]


# ---------------------------------------------------------------------------
# Investigación: Wikipedia ES (base) + DuckDuckGo Lite (segunda fuente)
# ---------------------------------------------------------------------------

def _frases_con_datos(texto, claves=(), distintivas=()):
    """Frases con cifras (años, precios, cantidades). Las cifras son lo que
    hace verificable un dato en un guion corto. Las frases que mencionan el
    tema puntúan más; las que mencionan la palabra distintiva del tema
    (la que no está en el título del artículo) puntúan aún más: el
    extracto de Wikipedia mezcla historia general del tema."""
    texto = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", texto)
    texto = re.sub(r"\s+", " ", texto)
    frases = re.split(r"(?<=[.!?])\s+", texto)
    buenas = []
    for f in frases:
        f = f.strip()
        if len(f) < 25 or len(f) > 300:
            continue
        if "[" in f or "editar" in f.lower():
            continue
        if re.search(r"enlaces externos|véase también|vease tambien|categoría multimedia|categoria multimedia", f.lower()):
            continue  # restos de la estructura del artículo, no datos
        digitos = re.findall(r"\d{4}|\d[\d.,]*", f)
        if not digitos:
            continue
        puntos = 0
        if re.search(r"\b(1[89]\d{2}|20[0-3]\d)\b", f):
            puntos += 2  # año concreto
        puntos += min(len(digitos), 3)
        if len(f) < 160:
            puntos += 1
        bajo = f.lower()
        if any(k in bajo for k in claves):
            puntos += 2  # menciona el tema
        if any(k in bajo for k in distintivas):
            puntos += 3  # menciona lo distintivo del tema
        if re.search(r"millones|dólares|dolares|euros|récord|record|subasta|million|dollars|auction", bajo):
            puntos += 3  # precio récord o subasta: el gancho de estos videos
        buenas.append((puntos, f))
    buenas.sort(key=lambda x: -x[0])
    return [f for _, f in buenas]


def _chips_de_frases(frases, confirmados, distintivas, claves,
                     exigir_confirmado=True):
    """Convierte frases en fragmentos cortos con cifras, estilo lista de
    datos ("desde 1963, cuatro generaciones; segunda serie 1988").
    El modelo compone el guion con sus palabras; con frases completas
    las copiaba tal cual (verificado 13-nov-2026, Daytona).
    La coma decimal española ("17,8") NO parte el fragmento: solo se
    parte por coma seguida de espacio. Se descartan subordinadas sueltas
    ("que Newman le había regalado...") que quedan sin sujeto.
    VALIDEZ (fallo Nautilus 13-nov-2026): el fragmento solo vale si su
    FRASE MADRE menciona el tema (una palabra distintiva, o cualquier
    palabra del tema si no hay distintivas) y, cuando hay números
    confirmados en ambas fuentes, el fragmento contiene uno. La mención
    se mira en la frase, no en el fragmento: al partir por comas, la
    palabra del tema y la cifra pueden quedar en segmentos distintos
    ("...Nautilus, from the 1976 steel 3700 Jumbo..."). Sin la segunda
    condición, un año no confirmado ("En marzo de 1877") pasaba como
    dato verificado; sin la primera, un chip de otro reloj con un número
    confirmado ("Ingenieur... 1976") se colaba en el guion.
    `exigir_confirmado=False` (solo artículo dedicado del modelo): el
    artículo mismo es la fuente verificada; exigir además un número
    confirmado en DDG dejaba fuera modelos como el GMT Master II, donde
    solo se confirma el calibre (3285) y salía 1 chip (fallo 14-nov-2026).
    La protección Nautilus sigue: el artículo tiene que ser el del modelo
    (puntos >= 10), no uno tangencial."""
    refs = distintivas if distintivas else claves
    chips = []
    for f in frases:
        if not any(k in f.lower() for k in refs):
            continue
        # Segmentos: los cortos (<15) se unen al siguiente en vez de
        # descartarse, porque suelen llevar el año ("En 2017, el Daytona
        # de Paul Newman se subastó...": sin unir, el 2017 se perdía).
        segmentos = [s.strip().strip(".").strip()
                     for s in re.split(r"[:,;](?=\s|$)", f)]
        fusionados, buf = [], ""
        for s in segmentos:
            if buf:
                s = buf + ", " + s
                buf = ""
            if len(s) < 15:
                buf = s
                continue
            fusionados.append(s)
        for seg in fusionados:
            if len(seg) > 160:
                continue
            if re.match(r"^(que|y|cuando|donde|como|para|pero)\s", seg, re.I):
                continue
            if re.search(r"\d+\s*(views|likes|loves|shares|comments|followers|suscriptores|seguidores)", seg, re.I):
                continue  # contadores de interacción, no datos del reloj
            if not re.search(r"\d", seg):
                continue
            if exigir_confirmado and confirmados and not (_numeros(seg) & set(confirmados)):
                continue
            if seg not in chips:
                chips.append(seg)
    return chips


def _norm_num(s):
    """Normaliza un número para compararlo entre fuentes: la coma decimal
    española ("17,8") y el punto inglés ("17.8") son el mismo número, y
    los separadores de miles ("17,752,500" / "17.752.500") se quitan.
    Sin esto, el 17,8 de Wikipedia nunca confirmaba el 17.8 de DDG.
    El punto final de frase ("50.º") no es parte del número."""
    s = s.replace(" ", "").replace("\u00a0", "").rstrip(".,")
    if re.fullmatch(r"\d+,\d{1,2}", s):  # coma decimal: 17,8 -> 17.8
        return s.replace(",", ".")
    if "," in s:  # miles con coma: 17,752,500 -> 17752500
        return s.replace(",", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # miles con punto
        return s.replace(".", "")
    return s


def _numeros(texto):
    return {_norm_num(n) for n in re.findall(r"\b\d{4}\b|\b\d[\d.,]{2,}\b", texto)}


def _wiki_candidato(lang, tema, claves, max_hits=3):
    """Busca el artículo de Wikipedia (idioma `lang`) que de verdad trata
    del tema. Prueba hasta `max_hits` resultados en orden.
    PUERTA DE RELEVANCIA (fallo real 13-nov-2026, Nautilus): la búsqueda
    en español de "Patek Philippe Nautilus" devuelve el artículo de la
    EMPRESA ("Patek Philippe & Co."), y sus frases (1851, 1877, subasta
    de "La hija del relojero" en 2023) NO hablan del Nautilus. El modelo
    las pegó al Nautilus y el video salió con datos falsos. Por eso un
    candidato solo vale si al menos una de sus frases con datos menciona
    una palabra distintiva (del tema pero no del título del artículo).
    Devuelve (puntuación, titulo, frases, distintivas). Puntuación: 10
    si el título es el del modelo mismo (contiene una distintiva, o no
    hay distintivas porque el título cubre todo el tema), más 1 por cada
    palabra del tema en el título. Sirve para preferir el artículo
    dedicado ("Patek Philippe Nautilus") sobre uno tangencial (el
    diseñador "Gérald Genta"). Sin candidato válido: (0, None, [], [])."""
    base = f"https://{lang}.wikipedia.org/w/api.php"
    try:
        q = urllib.parse.quote(tema)
        status, body = http_get(
            f"{base}?action=query&list=search&srsearch={q}&srlimit=5&format=json",
            timeout=20)
        res = json.loads(body.decode("utf-8", "replace"))
        hits = res.get("query", {}).get("search", [])[:max_hits]
    except Exception as e:
        print(f"investigación: Wikipedia {lang} búsqueda falló: {e}", flush=True)
        return 0, None, [], []
    mejor = None
    for hit in hits:
        titulo = hit["title"]
        # Palabra distintiva: del tema pero no del título del artículo
        # (p. ej. "paul" en "Rolex Daytona Paul Newman" -> artículo "Rolex Daytona").
        titulo_bajo = titulo.lower()
        distintivas = [k for k in claves if k not in titulo_bajo]
        try:
            status, body = http_get(
                f"{base}?action=query&prop=extracts&explaintext=1"
                "&exsectionformat=plain&titles="
                + urllib.parse.quote(titulo) + "&format=json", timeout=20)
            pag = json.loads(body.decode("utf-8", "replace"))
            pages = pag.get("query", {}).get("pages", {})
            ext = ""
            for p in pages.values():
                ext = p.get("extract", "") or ""
        except Exception as e:
            print(f"investigación: Wikipedia {lang} extracto de '{titulo}' falló: {e}", flush=True)
            continue
        frases = _frases_con_datos(ext[:12000], claves, distintivas)
        if distintivas:
            # La puerta mira TODAS las frases con datos, no solo las 5
            # mejores: en el artículo bueno ("Rolex Daytona") las frases
            # de Paul Newman no siempre entran en el top 5 por puntos.
            menciona = any(any(d in f.lower() for d in distintivas) for f in frases)
            if not menciona:
                print(f"investigación: Wikipedia {lang} '{titulo}' descartado: "
                      f"ninguna frase con datos menciona {distintivas}", flush=True)
                continue
        es_modelo = (not distintivas
                     or any(d in titulo_bajo for d in distintivas))
        puntos = (10 if es_modelo else 0) + sum(
            1 for k in claves if k in titulo_bajo)
        print(f"investigación: Wikipedia {lang} '{titulo}', {len(frases)} frases con datos, puntos={puntos}", flush=True)
        if mejor is None or puntos > mejor[0]:
            mejor = (puntos, titulo, frases, distintivas)
    return mejor if mejor is not None else (0, None, [], [])


def _chip_valido(chip, confirmados, distintivas, claves):
    """Un fragmento solo entra al guion si menciona el tema (una palabra
    distintiva, o cualquier palabra del tema si no hay distintivas) Y,
    cuando hay números confirmados en ambas fuentes, contiene uno de
    ellos. Sin la segunda condición, un chip con un año no confirmado
    ("En marzo de 1877") pasaba como dato verificado y el modelo lo
    pegaba a otro reloj (fallo Nautilus 13-nov-2026). Sin la primera,
    un chip de otro reloj con un número confirmado ("Ingenieur... 1976")
    se colaba en el guion."""
    bajo = chip.lower()
    refs = distintivas if distintivas else claves
    if not any(k in bajo for k in refs):
        return False
    if confirmados:
        return any(n in chip for n in confirmados)
    return True


def investigar(tema):
    """Devuelve (datos, info). datos: texto corto (<=350 caracteres) con
    hechos verificados para los prompts del guion. info: detalle para el
    reporte. Falla si no hay datos verificados: sin investigación no hay
    video, y nunca se usa el fallback de otro tema."""
    info = {"tema": tema}
    claves = palabras_clave_tema(tema)
    wiki_frases, articulo, distintivas = [], None, []
    fuente = None
    puntos, titulo, frases, dist = _wiki_candidato("es", tema, claves)
    if titulo:
        wiki_frases, articulo, distintivas, fuente = frases, titulo, dist, "wikipedia-es"
    if puntos < 10:
        # Sin artículo del modelo en español (pasa con el Nautilus, donde
        # lo mejor es el diseñador): probar en inglés; gana el de mayor
        # puntuación y el español empata. El guion se escribe en español
        # igual; los datos pueden venir en inglés.
        puntos_en, titulo_en, frases_en, dist_en = _wiki_candidato("en", tema, claves)
        if titulo_en and puntos_en > puntos:
            wiki_frases, articulo, distintivas, fuente = frases_en, titulo_en, dist_en, "wikipedia-en"
    if articulo:
        info["articulo_wiki"] = articulo
        info["frases_wiki"] = len(wiki_frases)

    ddg_texto, ddg_n = "", 0
    for intento_ddg in range(3):  # DDG Lite a veces devuelve 0 resultados
        try:
            status, body = http_get(
                "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(tema),
                timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            h = body.decode("utf-8", "replace")
            titulos = re.findall(r"class=['\"]result-link['\"]>(.*?)</a>", h)
            snips = re.findall(r"class=['\"]result-snippet['\"]>(.*?)</td>", h, re.S)
            limpio = lambda s: htmlmod.unescape(re.sub(r"<[^>]+>", "", s)).strip()
            ddg_n = len(titulos)
            ddg_texto = " ".join(limpio(s) for s in snips)
            info["resultados_ddg"] = ddg_n
            print(f"investigación: DDG Lite, {ddg_n} resultados", flush=True)
            if ddg_n > 0:
                break
            print(f"investigación: DDG Lite dio 0 resultados, reintento en {5 * (intento_ddg + 1)} s", flush=True)
            time.sleep(5 * (intento_ddg + 1))
        except Exception as e:
            print(f"investigación: DDG Lite falló: {e}", flush=True)
            break

    # Números confirmados: aparecen en Wikipedia Y en DDG (dos fuentes).
    nums_wiki = set()
    for f in wiki_frases:
        nums_wiki |= _numeros(f)
    nums_ddg = _numeros(ddg_texto)
    confirmados = sorted(nums_wiki & nums_ddg)
    info["numeros_confirmados"] = confirmados
    print(f"investigación: números confirmados en ambas fuentes: {confirmados}", flush=True)

    # Datos como fragmentos cortos con cifras (no frases completas: el
    # modelo las copiaba). Cada chip pasa el filtro de validez: número
    # confirmado en ambas fuentes, o mención de una palabra distintiva.
    # Artículo dedicado del modelo (puntos >= 10): la exigencia del número
    # confirmado no aplica a sus frases (ver _chips_de_frases); a los
    # chips de DDG sí se les exige siempre.
    articulo_dedicado = (puntos >= 10)
    frases = wiki_frases[:12]
    chips = _chips_de_frases(frases, confirmados, distintivas, claves,
                             exigir_confirmado=not articulo_dedicado)
    if len(chips) < 2:
        # Los chips de DDG se SUMAN a los de Wikipedia (no los sustituyen):
        # un tema como el Nautilus solo tiene 1 chip válido en Wikipedia
        # (el artículo dedicado no existe) y el resto viene de DDG.
        alt = _frases_con_datos(ddg_texto, claves)
        alt_chips = _chips_de_frases(alt[:10], confirmados, distintivas, claves)
        nuevos = [c for c in alt_chips if c not in chips]
        if nuevos:
            chips = chips + nuevos
            if fuente is None:
                fuente = "ddg-lite"
            print(f"investigación: DDG Lite aporta {len(nuevos)} chips válidos", flush=True)
    if len(chips) < 2:
        raise RuntimeError(
            "investigación sin datos verificados para el tema "
            f"(wiki: {len(wiki_frases)} frases, ddg: {ddg_n} resultados, "
            f"chips válidos: {len(chips)}; confirmados: {confirmados})")
    # Garantizar que al menos un número confirmado en ambas fuentes esté
    # en los datos, si existe.
    if confirmados and not any(c in " ".join(chips) for c in confirmados):
        for f in wiki_frases:
            extra = _chips_de_frases([f], confirmados, distintivas, claves)
            if extra:
                chips = extra + chips
                break
    chips = chips[:6]
    datos = ", ".join(chips)
    if len(datos) > 280:  # el prompt del guion tiene que seguir siendo corto
        datos = datos[:280].rsplit(" ", 1)[0]
    if len(datos) < 40:
        raise RuntimeError("investigación: datos demasiado cortos para el guion")
    info["fuente_datos"] = fuente
    info["datos"] = datos
    info["chips"] = chips
    return datos, confirmados, info


# ---------------------------------------------------------------------------
# Curar: YouTube -> transcripción -> análisis (fase 3, 14-nov-2026)
# Pegar el enlace de un video viral: la app saca su transcripción y
# metadatos (sin llave), analiza gancho/estructura/CTA con Pollinations
# texto (la misma IA gratis del guion) y genera un guion nuevo con esa
# fórmula, sin copiar el contenido. Dos variantes: mismo tema cambiando
# cosas, o la misma fórmula con otro tema.
# ---------------------------------------------------------------------------

def extraer_video_id(url):
    """ID de YouTube de una URL (watch, youtu.be, shorts, live, embed)."""
    m = re.search(r"(?:v=|/shorts/|/live/|/embed/|youtu\.be/)([\w-]{11})",
                  url or "")
    return m.group(1) if m else None


def _limpiar_titulo(titulo):
    """Título limpio para investigar: sin emojis ni signos, max 80 chars."""
    limpio = re.sub(r"[^\w\s]", " ", titulo or "")
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio[:80]


def _limpiar_transcripcion(texto):
    """Quita marcas de la transcripción automática ([Música], [Music],
    [Applause]...) y colapsa espacios. Sin esto, el análisis y el
    fallback por reglas trabajan sobre ruido (fallo real 14-nov-2026:
    el gancho sacado de la transcripción era '[Música] [Música]...')."""
    texto = re.sub(r"\[[^\]]{0,40}\]", " ", texto or "")
    return re.sub(r"\s+", " ", texto).strip()


def obtener_transcripcion(video_id):
    """Transcripción del video (español o inglés). youtube-transcript-api
    tiene dos APIs según la versión: 1.x (fetch) y 0.6.x (get_transcript).
    Se prueban ambas. Sin transcripción no hay análisis del contenido."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as e:
        raise RuntimeError(f"youtube-transcript-api no instalado: {e}")
    errores = []
    try:  # API 1.x
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["es", "en"])
        texto = " ".join(s.text for s in fetched)
        if len(texto.strip()) >= 200:
            return texto
        errores.append("fetch: transcripción demasiado corta")
    except Exception as e:
        errores.append(f"fetch: {e}")
    try:  # API 0.6.x
        partes = YouTubeTranscriptApi.get_transcript(video_id, languages=["es", "en"])
        texto = " ".join(p.get("text", "") for p in partes)
        if len(texto.strip()) >= 200:
            return texto
        errores.append("get_transcript: transcripción demasiado corta")
    except Exception as e:
        errores.append(f"get_transcript: {e}")
    raise RuntimeError("sin transcripción del video (" + "; ".join(errores) + ")")


def obtener_metadatos_yt(video_id, url):
    """Título, descripción y vistas del video. Primero yt-dlp (sin llave);
    si falla, oEmbed de YouTube para el título (sin llave). Los campos
    que no se consiguen quedan vacíos; no es un fallo del run."""
    meta = {"titulo": "", "descripcion": "", "vistas": None, "autor": ""}
    try:
        r = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", "--no-warnings",
             "--no-playlist", url],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            d = json.loads(r.stdout)
            meta["titulo"] = d.get("title", "") or ""
            meta["descripcion"] = d.get("description", "") or ""
            meta["vistas"] = d.get("view_count")
            meta["autor"] = d.get("uploader", "") or ""
            return meta
        print(f"curar: yt-dlp falló (rc={r.returncode}), pruebo oEmbed", flush=True)
    except Exception as e:
        print(f"curar: yt-dlp falló ({e}), pruebo oEmbed", flush=True)
    try:
        status, body = http_get(
            "https://www.youtube.com/oembed?url="
            + urllib.parse.quote(url) + "&format=json", timeout=20)
        d = json.loads(body.decode("utf-8", "replace"))
        meta["titulo"] = d.get("title", "") or ""
        meta["autor"] = d.get("author_name", "") or ""
    except Exception as e:
        print(f"curar: oEmbed falló: {e}", flush=True)
    return meta


def curar_fuente(video_url):
    """Fuente del video viral: metadatos + transcripción. Falla si no hay
    contenido con el que trabajar (sin transcripción y sin descripción
    suficiente no hay análisis posible)."""
    video_id = extraer_video_id(video_url)
    if not video_id:
        raise RuntimeError("enlace de YouTube no válido (no se encontró el ID del video)")
    meta = obtener_metadatos_yt(video_id, video_url)
    transcripcion = ""
    try:
        transcripcion = _limpiar_transcripcion(obtener_transcripcion(video_id))
    except Exception as e:
        print(f"curar: {e}", flush=True)
    texto_util = (transcripcion + " " + meta.get("descripcion", "")).strip()
    if len(texto_util) < 200:
        raise RuntimeError(
            "el video no tiene transcripción ni descripción suficiente para analizarlo")
    info = {
        "video_id": video_id,
        "titulo": meta.get("titulo", ""),
        "autor": meta.get("autor", ""),
        "vistas": meta.get("vistas"),
        "transcripcion_chars": len(transcripcion),
        "descripcion_chars": len(meta.get("descripcion", "")),
    }
    print(f"curar: video {video_id} '{meta.get('titulo', '')[:60]}' "
          f"vistas={meta.get('vistas')} transcripción={len(transcripcion)} chars",
          flush=True)
    return meta, transcripcion, info


# Palabras genéricas que no dicen de qué reloj trata el video: se
# quitan al derivar el tema desde el título.
GENERICS_TITULO = {
    "restoration", "restore", "restored", "restoring", "rusty", "water",
    "damaged", "video", "review", "unboxing", "historia", "story", "watch",
    "watches", "reloj", "relojes", "the", "of", "a", "an", "y", "en", "el",
    "la", "los", "las", "de", "del", "con", "por", "para", "su", "sus",
    "full", "complete", "part", "parte", "how", "to", "is", "it", "this",
    "that", "with", "from", "and", "in", "on", "no", "si", "más", "mas",
    "que", "es", "son", "se", "al", "lo", "como", "este", "esta", "esto",
    "una", "unos", "unas", "un",
}


def _tema_desde_titulo(titulo):
    """Tema corto desde el título: se queda la parte antes del separador
    de canal (│ |) y se quitan las palabras genéricas. 'Restoration of
    Rusty Rolex - Water damaged 1996 GMT Master II' -> 'Rolex 1996 GMT
    Master II'. Sin esto el tema era el título entero (80 caracteres) y
    la investigación no encontraba el artículo del modelo."""
    base = re.split(r"[│|]", titulo or "")[0]
    palabras = [w for w in re.split(r"[^\w]+", base) if w]
    utiles = [w for w in palabras
              if w.lower() not in GENERICS_TITULO and len(w) >= 2
              and not re.fullmatch(r"(19|20)\d{2}", w)]
    tema = " ".join(utiles)[:60].strip()
    return tema if len(utiles) >= 2 else _limpiar_titulo(titulo)[:60]


def _parse_analisis(texto):
    """Saca GANCHO/ESTRUCTURA/CTA/CAMBIO/TEMA de la salida del modelo.
    Las frases de estilo se recortan a 10 palabras: van al prompt del
    guion y los prompts largos dan HTTP 400 (verificado 13-nov-2026).
    Tolera listas numeradas ('1. GANCHO: ...') y viñetas: el modelo no
    siempre devuelve las líneas limpias."""
    out = {}
    for m in re.finditer(
            r"(?im)^\s*(?:\d+[.)]\s*|[-*•]\s*)?"
            r"(GANCHO|ESTRUCTURA|CTA|CAMBIO|TEMA)\s*[:\-]\s*(.+)$", texto):
        out[m.group(1).lower()] = m.group(2).strip()
    if "cta" in out:
        out["cierre"] = out.pop("cta")
    for k in ("gancho", "cierre"):
        if k in out:
            palabras = out[k].split()
            out[k] = " ".join(palabras[:10])
    if "tema" in out:
        out["tema"] = " ".join(out["tema"].split()[:5]).strip(" \"“”''")
    return out


def _estilo_sano(frase):
    """Una frase de estilo solo vale si es texto limpio: sin corchetes ni
    marcas de música, con al menos 2 palabras. Una frase basura en el
    prompt del guion puede disparar el HTTP 400 (fallo real 14-nov-2026:
    'Gancho: i [Música] [Música]...' y 3 variantes fallidas)."""
    if not frase:
        return None
    if "[" in frase or "]" in frase:
        return None
    if re.search(r"música|music", frase, re.I):
        return None
    if len(frase.split()) < 2:
        return None
    return frase


def analizar_viral(titulo, transcripcion, descripcion):
    """Análisis corto del viral con Pollinations texto: gancho, estructura,
    CTA, qué cambiar y sobre qué reloj/modelo trata (TEMA, para investigar
    los datos). Salida compacta en 5 líneas. Si el modelo falla, análisis
    por reglas (primera y última frase): es débil pero no pierde el run.
    La transcripción de muchos videos es sobre todo música y ruido: se
    combina con la descripción, que suele traer el contenido real."""
    texto = ((transcripcion or "") + "\n" + (descripcion or ""))[:4000]
    variantes = [
        ("Analiza este video en 5 líneas cortas. GANCHO: cómo empieza. "
         "ESTRUCTURA: cómo sigue. CTA: cómo termina. CAMBIO: qué cambiarías. "
         f"TEMA: el reloj o modelo, en 2-4 palabras. Video: {titulo}. "
         f"Texto: {texto[:2500]}"),
        ("En 5 líneas: GANCHO, ESTRUCTURA, CTA, CAMBIO, TEMA. "
         f"Video: {titulo}. Texto: {texto[:1200]}"),
    ]
    for i, prompt in enumerate(variantes):
        try:
            url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt)
            status, body = pollinations_texto(url, timeout=40)
            crudo = body.decode("utf-8", "replace").strip()
            print(f"curar: análisis variante {i + 1} crudo: {crudo[:500]!r}",
                  flush=True)
            analisis = _parse_analisis(crudo)
            analisis["gancho"] = _estilo_sano(analisis.get("gancho"))
            analisis["cierre"] = _estilo_sano(analisis.get("cierre"))
            if analisis.get("gancho") and analisis.get("tema"):
                analisis["fuente"] = f"pollinations-v{i + 1}"
                print(f"curar: análisis {analisis}", flush=True)
                return analisis
            print(f"curar: análisis variante {i + 1} incompleto: {analisis}",
                  flush=True)
        except Exception as e:
            print(f"curar: análisis variante {i + 1} falló: {e}", flush=True)
    # Fallback por reglas: primera frase = gancho, última = cierre. Se
    # sacan de la DESCRIPCIÓN (texto limpio) antes que de la transcripción
    # (ruido de la transcripción automática).
    fuente_texto = (descripcion or "") if len((descripcion or "").strip()) >= 200 else texto
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", fuente_texto)
              if len(f.strip()) > 20]
    analisis = {
        "gancho": _estilo_sano(" ".join((frases[0] if frases else "").split()[:10])),
        "estructura": "datos y historia del reloj",
        "cierre": _estilo_sano(" ".join((frases[-1] if frases else "").split()[:10])),
        "cambio": "mismo tema con otros ejemplos",
        "tema": _tema_desde_titulo(titulo),
        "fuente": "reglas",
    }
    print(f"curar: análisis por reglas {analisis}", flush=True)
    return analisis


# ---------------------------------------------------------------------------
# Guion: Pollinations texto en llamadas cortas (~50 palabras cada una)
# ---------------------------------------------------------------------------

def _parrafos(texto):
    """Divide en párrafos: primero por líneas en blanco; si no hay,
    por saltos de línea simples. Quita preámbulos del modelo."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    if len(paras) == 1:
        lineas = [l.strip() for l in texto.split("\n") if l.strip()]
        if len(lineas) > 1:
            paras = lineas
    paras = [
        p for p in paras
        if not (len(p.split()) < 12 and re.match(
            r"^(aquí|claro|por supuesto|vale|perfecto)", p.lower()))
    ]
    return paras


def _llamada_guion(prompt, n_parrafos, palabras_min, palabras_max, claves):
    """Una llamada a Pollinations texto. Devuelve (parrafos, palabras) o None."""
    url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt)
    try:
        # pollinations_texto ya gestiona el límite de ritmo: 35 s entre
        # llamadas y reintentos con Retry-After ante 402/429 (hasta 6).
        # Un 400 (output_token_limit_exceeded) no se reintenta aquí: lo
        # gestionan las 3 variantes de generar_guion (cada una más corta).
        status, body = pollinations_texto(url, timeout=40)
    except Exception as e:
        print(f"Pollinations texto falló: {e}", flush=True)
        return None
    texto = body.decode("utf-8", "replace").strip()
    paras = _parrafos(texto)
    palabras = contar_palabras(texto)
    bajo = texto.lower()
    ok = (
        status == 200
        and len(paras) == n_parrafos
        and palabras_min <= palabras <= palabras_max
        and (not claves or any(k in bajo for k in claves))
    )
    print(f"Pollinations texto: status={status}, palabras={palabras}, "
          f"parrafos={len(paras)}, ok={ok}", flush=True)
    return (paras, palabras) if ok else None


def generar_guion(tema, datos, confirmados, n_clips, palabras_objetivo, chips=None,
                  gancho_estilo=None, cierre_estilo=None,
                  texto_extra_verificado=""):
    """Guion en llamadas cortas a Pollinations texto (anónimo).
    Una sola llamada con ~110 palabras da HTTP 400 output_token_limit_exceeded
    (verificado 13-nov-2026); cada llamada pide ~50 palabras con instrucción
    CORTA: la instrucción larga daba 400 incluso pidiendo 40 palabras.
    Sin "no inventes", el modelo inventa datos. El último párrafo termina
    con el CTA exacto, pero el CTA NO se pide al modelo: pedir "termina
    exactamente con..." junto con los datos agota el límite de tokens de
    salida del modelo gratuito (HTTP 400 output_token_limit_exceeded,
    verificado 14-nov-2026, run #8: la llamada 2 falló con datos+CTA y
    pasó sin el CTA, y también pasó con CTA pero sin datos). El CTA se
    añade al final del último párrafo y la validación lo comprueba igual.
    Cada llamada recibe SOLO su parte de los datos:
    con los mismos datos en todas, la segunda llamada repetía la primera
    (verificado 13-nov-2026, Daytona). En modo curar, `gancho_estilo` y
    `cierre_estilo` (frases cortas del análisis del viral) se añaden a la
    primera y última llamada: son forma, no texto exacto, así que no
    disparan el 400 de "termina exactamente con..." (run #8).
    La lista de cifras permitidas va en TODAS las llamadas (incluida la
    variante mínima): el modelo cambia cifras de memoria aunque se le
    diga "usa solo estos datos" (3186 por 3285, tres veces, 14-nov-2026).
    """
    claves = palabras_clave_tema(tema)
    permitidos = sorted(
        (_numeros(" ".join(chips)) if chips else set())
        | set(confirmados or []) | _numeros(texto_extra_verificado))
    linea_cifras = (f"Cifras permitidas: {', '.join(permitidos[:10])}. "
                    "No uses ninguna otra cifra. ") if permitidos else ""
    n_llamadas = max(2, math.ceil(palabras_objetivo / 50))
    por_llamada = palabras_objetivo // n_llamadas
    # Reparto de párrafos (clips) entre llamadas: la primera se lleva más.
    reparto, resto = divmod(n_clips, n_llamadas)
    n_parrafos = []
    for i in range(n_llamadas):
        n_parrafos.append(reparto + (1 if i < resto else 0))
    # Reparto de datos entre llamadas: grupos contiguos de chips.
    grupos_datos = []
    grupos_chips = []
    if chips:
        por_grupo, resto_c = divmod(len(chips), n_llamadas)
        idx = 0
        for i in range(n_llamadas):
            n = por_grupo + (1 if i < resto_c else 0)
            grupos_chips.append(chips[idx:idx + n])
            grupos_datos.append(", ".join(chips[idx:idx + n]) if n else datos)
            idx += n
    else:
        grupos_datos = [datos] * n_llamadas
        grupos_chips = [[] for _ in range(n_llamadas)]
    print(f"guion: {palabras_objetivo} palabras objetivo, {n_llamadas} llamadas, "
          f"reparto de párrafos {n_parrafos}, claves={claves}", flush=True)
    partes = []
    for i, n_par in enumerate(n_parrafos):
        palabras = por_llamada if i < n_llamadas - 1 else palabras_objetivo - por_llamada * (n_llamadas - 1)
        base = (
            f"Guion en español, {n_par} párrafos cortos, unas {palabras} palabras, "
            f"sobre {tema}. Datos: {grupos_datos[i]} Usa solo estos datos, no inventes otros. "
            "Redacta con tus palabras. "
        )
        if linea_cifras:
            base += linea_cifras
        if i == 0:
            base += "Primer párrafo: gancho con el dato más sorprendente. "
            if gancho_estilo:
                base += f"Gancho: {gancho_estilo}. "
        if i == n_llamadas - 1 and cierre_estilo:
            base += f"Cierre: {cierre_estilo}. "
        base += "Solo el guion."
        # Tres variantes por llamada: la pedida, una más corta y una aún más
        # corta sin la frase de redacción (por si el 400 viene del largo del
        # prompt). Pollinations anónimo da 400 esporádicos; cada variante es
        # un intento nuevo con su propio rango de palabras válido.
        variantes = [
            (base, palabras),
            (base.replace(f"unas {palabras} palabras", "unas 40 palabras"), 40),
            (base.replace(f"unas {palabras} palabras", "unas 30 palabras").replace(
                "Redacta con tus palabras. ", ""), 30),
        ]
        if i == n_llamadas - 1:
            # Último intento con prompt mínimo (un solo chip): por si el
            # 400 persiste con los datos (run #8). La lista de cifras va
            # también aquí: sin ella, esta variante escribió 3186 por 3285.
            mini_datos = grupos_chips[i][0] if grupos_chips[i] else tema
            variantes.append((
                f"Guion en español, {n_par} párrafos cortos, unas 30 palabras, "
                f"sobre {tema}. Datos: {mini_datos} Usa solo estos datos, "
                f"no inventes otros. {linea_cifras}Solo el guion.", 30))
        parte = None
        for j, (p, objetivo) in enumerate(variantes):
            parte = _llamada_guion(
                p, n_par, max(20, objetivo - 12), objetivo + 15, claves)
            if parte:
                break
            if j < len(variantes) - 1:
                print(f"guion: llamada {i + 1} variante {j + 1} falló, reintento en 8 s", flush=True)
                time.sleep(8)
        if not parte:
            print(f"guion: llamada {i + 1} falló, no hay fallback de otro tema", flush=True)
            raise RuntimeError(f"guion: la llamada {i + 1} a Pollinations falló")
        partes.append(parte)
    clips = [p for paras, _ in partes for p in paras]
    # El CTA se añade aquí (no se pide al modelo: ver docstring). Si el
    # modelo ya terminó con él, no se duplica.
    if not clips[-1].rstrip().rstrip(".").endswith(CTA.rstrip(".")):
        clips[-1] = clips[-1].rstrip() + " " + CTA
    texto = " ".join(clips)
    total = contar_palabras(texto)
    bajo = texto.lower()
    termina_cta = texto.rstrip().rstrip(".").endswith(CTA.rstrip("."))
    # El guion tiene que llevar al menos una cifra de los datos que se le
    # pasaron al modelo (no inventada): con artículo dedicado, los chips
    # no exigen número confirmado en ambas fuentes, así que la cifra se
    # busca en los chips, no en `confirmados` (fallo 14-nov-2026: el guion
    # usaba 2019/2005/3285 de los chips y la validación pedía 1954).
    numeros_datos = _numeros(" ".join(chips)) if chips else set()
    if not numeros_datos:
        numeros_datos = set(confirmados or [])
    tiene_numero = (not numeros_datos or any(
        c in texto or c.replace(".", ",") in texto for c in numeros_datos))
    # Y ninguna cifra fuera de las verificadas: el modelo cambia cifras
    # de memoria (fallo 14-nov-2026: escribió calibre 3186 cuando los
    # datos decían 3285). En modo curar, el título y la descripción del
    # video también son fuentes verificadas (p. ej. el año 1996 del reloj).
    numeros_permitidos = set(numeros_datos) | set(confirmados or [])
    if texto_extra_verificado:
        numeros_permitidos |= _numeros(texto_extra_verificado)
    numeros_guion = _numeros(texto)
    inventados = sorted(n for n in numeros_guion if n not in numeros_permitidos)
    fallos = []
    if len(clips) != n_clips:
        fallos.append(f"clips {len(clips)} != {n_clips}")
    # El mínimo es flexible (las variantes cortas de reintento dan menos
    # palabras): lo que manda es la duración real de la voz, que se reporta.
    if not (palabras_objetivo * 0.7 <= total <= palabras_objetivo * 1.25):
        fallos.append(f"palabras {total} fuera de rango")
    if not termina_cta:
        fallos.append("no termina con el CTA")
    if not tiene_numero:
        fallos.append("sin número confirmado en el guion")
    if inventados:
        fallos.append(f"números no verificados en el guion: {', '.join(inventados)}")
    ok = not fallos
    print(f"guion combinado: {total} palabras, clips={len(clips)}, ok={ok}, fallos={fallos}", flush=True)
    if not ok:
        raise RuntimeError(f"guion combinado no válido: {'; '.join(fallos)}")
    REPORT["guion_llamadas"] = [p[1] for p in partes]
    return clips, "pollinations"


# ---------------------------------------------------------------------------
# Imágenes, voz, render
# ---------------------------------------------------------------------------

def bajar_imagenes(n, formato):
    os.makedirs(IMG_DIR, exist_ok=True)
    ancho, alto = (768, 768) if formato == "vertical" else (1024, 576)
    rutas = []
    for i in range(n):
        prompt = PROMPTS_IMAGEN[i]
        if formato == "horizontal":
            prompt += ", wide 16:9 composition"
        url = (
            "https://image.pollinations.ai/prompt/"
            + urllib.parse.quote(prompt)
            + f"?model=sana&width={ancho}&height={alto}&seed=11"
        )
        ruta = os.path.join(IMG_DIR, f"{i}.jpg")
        try:
            nbytes = bajar_archivo(url, ruta)
        except Exception as e:
            raise RuntimeError(f"imagen {i} no se pudo bajar: {e}")
        with open(ruta, "rb") as f:
            magic = f.read(2)
        if magic != b"\xff\xd8" or nbytes < 10000:
            raise RuntimeError(f"imagen {i}: archivo no válido ({nbytes} bytes)")
        print(f"imagen {i}: {nbytes} bytes", flush=True)
        rutas.append(ruta)
    return rutas


def bajar_modelo_kokoro():
    model_dir = os.path.join(WORK, "kokoro")
    os.makedirs(model_dir, exist_ok=True)
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    voices = os.path.join(model_dir, "voices-v1.0.bin")
    if not (os.path.exists(voices) and os.path.getsize(voices) > 100000):
        n = bajar_archivo(base + "voices-v1.0.bin", voices)
        print(f"voices-v1.0.bin: {n} bytes", flush=True)
    modelos = [
        ("int8", os.path.join(model_dir, "kokoro-v1.0.int8.onnx"), base + "kokoro-v1.0.int8.onnx"),
        ("fp32", os.path.join(model_dir, "kokoro-v1.0.onnx"), base + "kokoro-v1.0.onnx"),
    ]
    for nombre, ruta, url in modelos:
        if not (os.path.exists(ruta) and os.path.getsize(ruta) > 1000000):
            print(f"bajando modelo {nombre}...", flush=True)
            n = bajar_archivo(url, ruta)
            print(f"modelo {nombre}: {n} bytes", flush=True)
        yield nombre, ruta, voices


def generar_voz(texto, wav_path, duracion_objetivo):
    """Voz Kokoro Santa. Si la duración real se desvía más del 15% del
    objetivo, un reintento con la velocidad ajustada (0,9–1,1)."""
    from kokoro_onnx import Kokoro

    def _intento(speed):
        ultimo = None
        for nombre, modelo, voices in bajar_modelo_kokoro():
            for lang in ("es", None):
                try:
                    print(f"voz: modelo={nombre} lang={lang} speed={speed}", flush=True)
                    kokoro = Kokoro(modelo, voices)
                    kwargs = {"voice": "em_santa", "speed": speed}
                    if lang:
                        kwargs["lang"] = lang
                    samples, sr = kokoro.create(texto, **kwargs)
                    import soundfile as sf
                    sf.write(wav_path, samples, sr)
                    return nombre, lang or "defecto", duracion_audio(wav_path)
                except Exception as e:
                    ultimo = e
                    print(f"voz falló modelo={nombre} lang={lang}: {e}", flush=True)
        raise RuntimeError(f"Kokoro falló en todos los intentos: {ultimo}")

    modelo, lang, dur = _intento(1.0)
    desv = abs(dur - duracion_objetivo) / duracion_objetivo
    if desv > 0.15:
        speed = max(0.9, min(1.1, dur / duracion_objetivo))
        print(f"voz: duración {dur:.1f}s vs objetivo {duracion_objetivo}s "
              f"({desv:.0%}), reintento con speed={speed:.2f}", flush=True)
        modelo2, lang2, dur2 = _intento(speed)
        if abs(dur2 - duracion_objetivo) < abs(dur - duracion_objetivo):
            modelo, lang, dur = modelo2, lang2, dur2
    return modelo, lang, dur


def duracion_audio(wav_path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", wav_path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def obtener_musica():
    """Pista de fondo CC0. Primero el archivo del repo (assets/); si no
    está, descarga del Internet Archive (original FreePD) o del espejo.
    Devuelve la ruta o None si no se consigue (la música es opcional:
    sin ella el video sale solo con voz, no falla el run)."""
    import base64
    import hashlib

    repo_mp3 = os.path.join(os.path.dirname(BASE), "assets", "musica-fondo.mp3")
    if os.path.exists(repo_mp3) and os.path.getsize(repo_mp3) > 1000000:
        print(f"música: usando {repo_mp3} (del repo)", flush=True)
        return repo_mp3
    destino = os.path.join(WORK, "musica-fondo.mp3")
    if os.path.exists(destino) and os.path.getsize(destino) > 1000000:
        return destino
    for url in MUSICA_URLS:
        try:
            print(f"música: descargando {url[:80]}...", flush=True)
            bajar_archivo(url, destino)
            with open(destino, "rb") as f:
                digest = base64.b32encode(hashlib.sha1(f.read()).digest()).decode()
            if digest == MUSICA_SHA1_B32:
                print("música: digest verificado (original FreePD)", flush=True)
                return destino
            print(f"música: digest distinto ({digest}), pruebo la siguiente fuente", flush=True)
            os.remove(destino)
        except Exception as e:
            print(f"música: no se pudo bajar de esta fuente: {e}", flush=True)
    return None


def mezclar_musica(voz_wav, musica_mp3, salida_wav, duracion):
    """Mezcla la música suave debajo de la voz. La voz manda: la música va
    a volumen 0,2 (~-14 dB) con entrada y salida en fundido, y amix con
    normalize=0 para no bajar el nivel de la voz.
    OJO (verificado 13-nov-2026): la voz de Kokoro es mono 24 kHz y la
    conversión automática mono->estéreo de FFmpeg la baja ~3 dB (factor
    1/sqrt(2) del rematrix). Por eso la voz se sube a estéreo 48 kHz con
    `pan` (duplica a nivel completo) ANTES de mezclar; con `pan` la mezcla
    mide lo teórico (voz intacta + música ~11 dB por debajo)."""
    fade_out_ini = max(0.0, duracion - 2.0)
    filtro = (
        "[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0[v];"
        f"[1:a]aresample=48000,pan=stereo|c0=c0|c1=c1,atrim=0:{duracion:.2f},"
        f"volume={MUSICA_VOLUMEN},"
        f"afade=t=in:st=0:d=1,afade=t=out:st={fade_out_ini:.2f}:d=2[m];"
        "[v][m]amix=inputs=2:duration=first:normalize=0[a]"
    )
    cmd([
        "ffmpeg", "-y", "-i", voz_wav, "-stream_loop", "-1", "-i", musica_mp3,
        "-filter_complex", filtro, "-map", "[a]",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", salida_wav,
    ])
    return duracion_audio(salida_wav)


def normalizar_voz(voz_wav, salida_wav):
    """Voz mono 24 kHz -> estéreo 48 kHz a nivel completo (mismo `pan` que
    en la mezcla, para que el video sin música suene igual de fuerte que
    el video con música). Sin esto, la conversión automática de FFmpeg
    en el render baja la voz ~3 dB."""
    cmd([
        "ffmpeg", "-y", "-i", voz_wav, "-af",
        "aresample=48000,pan=stereo|c0=c0|c1=c0",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", salida_wav,
    ])
    return duracion_audio(salida_wav)


def envolver(texto, ancho):
    palabras = texto.split()
    lineas, actual = [], ""
    for w in palabras:
        if len(actual) + len(w) + 1 > ancho and actual:
            lineas.append(actual)
            actual = w
        else:
            actual = (actual + " " + w).strip()
    if actual:
        lineas.append(actual)
    return "\\n".join(lineas)


def escribir_ass(clips, duraciones, ruta, ancho_px, alto_px, fuente_px, ancho_linea, margen_v):
    """Subtítulos en .ass con PlayResX/Y del formato. Un .srt sin PlayRes hace
    que libass asuma 384x288 y escale la fuente ~4,4x: las letras salían
    gigantes y cortadas por los bordes (verificado 13-nov-2026)."""
    t = 0.0
    eventos = []
    for clip, d in zip(clips, duraciones):
        ini, fin = t, t + d
        texto = envolver(clip, ancho_linea).replace("\\n", "\\N")
        eventos.append(
            f"Dialogue: 0,{_fmt_ass(ini)},{_fmt_ass(fin)},Default,,0,0,0,,{texto}"
        )
        t = fin
    contenido = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {ancho_px}\n"
        f"PlayResY: {alto_px}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,DejaVu Sans,{fuente_px},&H00FFFFFF,&H000000FF,&H00000000,"
        f"&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,40,40,{margen_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        + "\n".join(eventos)
        + "\n"
    )
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)


def _fmt_ass(s):
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    seg, ms = divmod(ms, 1000)
    return f"{h}:{m:02d}:{seg:02d}.{ms // 10:02d}"


def cmd(args, check=True):
    print("+ " + " ".join(args), flush=True)
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise RuntimeError(f"falló: {' '.join(args[:4])} (rc={r.returncode})")
    return r


def renderizar(imagenes, duraciones, wav_path, srt_path, salida, ancho_px, alto_px):
    segs = []
    for i, (img, d) in enumerate(zip(imagenes, duraciones)):
        frames = max(1, int(round(d * 30)))
        seg = os.path.join(WORK, f"seg{i}.mp4")
        cmd([
            "ffmpeg", "-y", "-i", img, "-vf",
            f"scale={ancho_px}:{alto_px}:force_original_aspect_ratio=increase,"
            f"crop={ancho_px}:{alto_px},"
            f"zoompan=z='1+0.12*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={ancho_px}x{alto_px}:fps=30,format=yuv420p",
            "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "30", seg,
        ])
        segs.append(seg)
    lista = os.path.join(WORK, "concat.txt")
    with open(lista, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    cmd([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lista,
        "-i", wav_path, "-vf", f"subtitles={srt_path}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart", salida,
    ])


def verificar_mp4(ruta):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_type,codec_name,width,height",
         "-of", "json", ruta],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(r.stdout)
    fmt = info.get("format", {})
    streams = info.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    return {
        "duracion_s": round(float(fmt.get("duration", 0)), 2),
        "mb": round(int(fmt.get("size", 0)) / 1e6, 2),
        "width": video[0].get("width") if video else None,
        "height": video[0].get("height") if video else None,
        "video_codec": video[0].get("codec_name") if video else None,
        "tiene_audio": bool(audio),
        "audio_codec": audio[0].get("codec_name") if audio else None,
    }


# Formato: dimensiones, fuente de subtítulos, ancho de línea, margen vertical.
FORMATOS = {
    "vertical": {"w": 720, "h": 1280, "fuente": 34, "linea": 28, "margen_v": 130},
    "horizontal": {"w": 1920, "h": 1080, "fuente": 44, "linea": 42, "margen_v": 100},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tema", default="Seiko Astron")
    ap.add_argument("--duracion", type=float, default=35)
    ap.add_argument("--formato", choices=["vertical", "horizontal"], default="vertical")
    ap.add_argument("--musica", choices=["si", "no"], default="si")
    ap.add_argument("--modo", choices=["tema", "curar"], default="tema")
    ap.add_argument("--video-url", default="")
    ap.add_argument("--variante", choices=["mismo-tema", "otro-tema"], default="mismo-tema")
    ap.add_argument("--tema-nuevo", default="")
    ap.add_argument("--outdir", default=OUT)
    args = ap.parse_args()
    if args.modo == "curar" and not args.video_url:
        ap.error("modo curar necesita --video-url")
    if args.modo == "curar" and args.variante == "otro-tema" and not args.tema_nuevo:
        ap.error("variante otro-tema necesita --tema-nuevo")
    if args.formato not in FORMATOS:
        ap.error("formato debe ser vertical u horizontal")
    fmt = FORMATOS[args.formato]
    n_clips = 5 if args.duracion <= 45 else 7
    palabras_objetivo = int(round(args.duracion * PALABRAS_POR_SEGUNDO))
    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    t_total = time.time()
    REPORT.update({
        "tema": args.tema,
        "modo": args.modo,
        "duracion_objetivo_s": args.duracion,
        "formato": args.formato,
        "dimensiones": f"{fmt['w']}x{fmt['h']}",
        "clips_previstos": n_clips,
        "palabras_objetivo": palabras_objetivo,
        "musica_pedida": args.musica,
    })
    try:
        tema_efectivo = args.tema
        gancho_estilo = cierre_estilo = None
        investigacion_hecha = False
        if args.modo == "curar":
            REPORT["variante"] = args.variante
            with fase("curar-fuente"):
                meta, transcripcion, info_curar = curar_fuente(args.video_url)
                REPORT["curar"] = info_curar
            with fase("curar-analisis"):
                analisis = analizar_viral(
                    meta.get("titulo", ""), transcripcion,
                    meta.get("descripcion", ""))
                REPORT["curar"].update(analisis)
                gancho_estilo = analisis.get("gancho")
                cierre_estilo = analisis.get("cierre")
                # El TEMA del modelo no es fiable: inventa títulos
                # ("Rusty Rolex Revival", fallo 14-nov-2026). Candidatos en
                # orden: el derivado del título (determinista) y luego el
                # del modelo; se usa el primero con datos verificados.
                tema_titulo = _tema_desde_titulo(meta.get("titulo", ""))
                tema_modelo = (analisis.get("tema") or "").strip()
                candidatos = [tema_titulo] if tema_titulo else []
                if tema_modelo and tema_modelo.lower() != tema_titulo.lower():
                    candidatos.append(tema_modelo)
                if args.variante == "otro-tema":
                    candidatos = [args.tema_nuevo]
                REPORT["curar"]["candidatos_tema"] = candidatos
                print(f"curar: candidatos de tema {candidatos}", flush=True)
                tema_efectivo = None
                for cand in candidatos:
                    try:
                        datos, confirmados, info_inv = investigar(cand)
                        tema_efectivo = cand
                        break
                    except Exception as e:
                        print(f"curar: investigar('{cand}') falló: {e}",
                              flush=True)
                if tema_efectivo is None:
                    raise RuntimeError(
                        "curar: no se encontraron datos verificados del "
                        "tema del video")
                REPORT["tema"] = tema_efectivo
                REPORT["investigacion"] = info_inv
                print(f"curar: tema efectivo '{tema_efectivo}'", flush=True)
                print(f"datos verificados: {datos}", flush=True)
                # La fase de investigación ya se hizo aquí: se marca para
                # no repetirla abajo.
                investigacion_hecha = True
        if not investigacion_hecha:
            with fase("investigacion"):
                datos, confirmados, info_inv = investigar(tema_efectivo)
                REPORT["investigacion"] = info_inv
                print(f"datos verificados: {datos}", flush=True)
        with fase("guion"):
            texto_extra = (
                (meta.get("titulo", "") + " " + meta.get("descripcion", ""))
                if args.modo == "curar" else "")
            try:
                clips, fuente = generar_guion(
                    tema_efectivo, datos, confirmados, n_clips, palabras_objetivo,
                    info_inv.get("chips"), gancho_estilo, cierre_estilo,
                    texto_extra)
            except RuntimeError as e:
                if "números no verificados" not in str(e):
                    raise
                # El modelo cambia cifras de memoria (3186 por 3285): un
                # reintento completo del guion. La lista de cifras
                # permitidas ya va en todos los prompts.
                print("guion: cifras inventadas, reintento completo del guion",
                      flush=True)
                clips, fuente = generar_guion(
                    tema_efectivo, datos, confirmados, n_clips, palabras_objetivo,
                    info_inv.get("chips"), gancho_estilo, cierre_estilo,
                    texto_extra)
            texto = " ".join(clips)
            REPORT["guion_fuente"] = fuente
            REPORT["guion_palabras"] = contar_palabras(texto)
            REPORT["guion_clips"] = len(clips)
            print(f"guion: {REPORT['guion_palabras']} palabras, {len(clips)} clips, fuente={fuente}", flush=True)
        with fase("imagenes"):
            imagenes = bajar_imagenes(n_clips, args.formato)
            REPORT["imagenes_ok"] = len(imagenes)
        wav_path = os.path.join(WORK, "voz.wav")
        with fase("voz"):
            modelo, lang, dur_voz = generar_voz(texto, wav_path, args.duracion)
            REPORT["voz_modelo"] = modelo
            REPORT["voz_lang"] = lang
            REPORT["voz_duracion_s"] = round(dur_voz, 2)
            print(f"voz: {REPORT['voz_duracion_s']} s, modelo={modelo}", flush=True)
        audio_final = wav_path
        if args.musica == "si":
            with fase("musica"):
                pista = obtener_musica()
                if pista:
                    mezcla = os.path.join(WORK, "mezcla.wav")
                    try:
                        dur_mezcla = mezclar_musica(
                            wav_path, pista, mezcla, REPORT["voz_duracion_s"])
                        audio_final = mezcla
                        REPORT["musica"] = {
                            "titulo": MUSICA_TITULO,
                            "autor": MUSICA_AUTOR,
                            "licencia": MUSICA_LICENCIA,
                            "volumen": MUSICA_VOLUMEN,
                            "duracion_s": round(dur_mezcla, 2),
                        }
                        print(f"música: {MUSICA_TITULO} ({MUSICA_AUTOR}, "
                              f"{MUSICA_LICENCIA}), volumen {MUSICA_VOLUMEN}", flush=True)
                    except Exception as e:
                        # La música es opcional: sin ella el video sale solo
                        # con voz; no se pierde el run por la música.
                        print(f"música: mezcla falló ({e}), sigo solo con voz", flush=True)
                        REPORT["musica"] = {"error": str(e)}
                else:
                    print("música: sin pista disponible, sigo solo con voz", flush=True)
                    REPORT["musica"] = {"error": "pista no disponible"}
        if audio_final == wav_path:
            # Sin música: normalizar la voz a estéreo 48 kHz a nivel
            # completo (si no, el render la baja ~3 dB al convertirla).
            voz48 = os.path.join(WORK, "voz48.wav")
            try:
                normalizar_voz(wav_path, voz48)
                audio_final = voz48
            except Exception as e:
                print(f"voz: normalización falló ({e}), sigo con la voz original", flush=True)
        with fase("render"):
            d_total = REPORT["voz_duracion_s"]
            chars = [len(c) for c in clips]
            duraciones = [d_total * c / sum(chars) for c in chars]
            REPORT["clip_duraciones_s"] = [round(d, 2) for d in duraciones]
            srt_path = os.path.join(WORK, "subs.ass")
            escribir_ass(clips, duraciones, srt_path,
                         fmt["w"], fmt["h"], fmt["fuente"], fmt["linea"], fmt["margen_v"])
            salida = os.path.join(args.outdir, "video.mp4")
            renderizar(imagenes, duraciones, audio_final, srt_path, salida,
                       fmt["w"], fmt["h"])
            REPORT["video"] = verificar_mp4(salida)
            print(f"video: {REPORT['video']}", flush=True)
        REPORT["tiempo_total_s"] = round(time.time() - t_total, 1)
        REPORT["exito"] = True
    except Exception as e:
        REPORT["tiempo_total_s"] = round(time.time() - t_total, 1)
        REPORT["exito"] = False
        REPORT["error"] = str(e)
        print(f"ERROR: {e}", flush=True)
    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2, ensure_ascii=False)
    print(json.dumps(REPORT, indent=2, ensure_ascii=False))
    sys.exit(0 if REPORT["exito"] else 1)


if __name__ == "__main__":
    main()
