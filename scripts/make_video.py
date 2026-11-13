#!/usr/bin/env python3
"""Prueba mínima: video vertical 35 s (Seiko Astron) con pipeline recortado.
Pasos: guion (Pollinations texto en 2 llamadas de ~55 palabras, con fallback
verificado solo como último recurso) -> 5 imágenes (Pollinations sana) ->
voz Kokoro Santa em_santa -> render FFmpeg 720x1280 con subtítulos quemados
-> MP4 + report.json con tiempos por fase.
Solo prueba. Nada de publicación ni frontal.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(BASE, "work")
# OUT en la raíz del repo (no en scripts/): el workflow busca out/video.mp4
# y out/report.json desde la raíz.
OUT = os.path.join(os.path.dirname(BASE), "out")
IMG_DIR = os.path.join(WORK, "img")

UA = {"User-Agent": "VeltoraVideoTest/1.0 (prueba sin fines comerciales)"}
REPORT = {"fases": {}, "exito": False}

# Guion verificado (fallback). Datos: Seiko Quartz-Astron 35SQ, primer reloj
# de cuarzo del mundo, 25-dic-1969, Tokio; 100 unidades, oro 18 quilates,
# 450.000 yenes (precio de un Toyota Corolla); cristal de cuarzo 8.192 Hz,
# precisión ±5 segundos al mes. Fuentes verificadas 13-nov-2026 (Wikipedia,
# WristBuzz, Time on the Move).
GUION_FALLBACK = [
    "¿Un reloj que costaba lo mismo que un carro? Eso pedían por este Seiko en 1969.",
    "Es el Seiko Astron, el primer reloj de cuarzo del mundo. Solo hicieron cien, todos en oro de dieciocho quilates.",
    "Salió a la venta el veinticinco de diciembre de mil novecientos sesenta y nueve, en Tokio. Costaba cuatrocientas cincuenta mil yenes.",
    "Su secreto era un cristal de cuarzo que vibraba ocho mil ciento noventa y dos veces por segundo. Se desviaba solo cinco segundos al mes.",
    "Ese reloj cambió la relojería para siempre y casi acaba con los mecánicos. Sígueme si quieres más historias de relojes imposibles.",
]

PROMPTS_IMAGEN = [
    "Macro close-up of a vintage 1969 gold quartz wristwatch, 18k yellow gold case, dark background, dramatic studio lighting, photorealistic",
    "Tiny quartz crystal oscillator and electronic watch movement, macro photography, gold and silver components, dark background",
    "1969 Tokyo street at Christmas night, vintage neon signs, cinematic moody lighting, photorealistic",
    "Close-up of a luxury gold wristwatch dial with simple baton markers, textured gold dial, studio photography, dark background",
    "Elegant gold wristwatch on dark velvet with a vintage car silhouette behind, cinematic golden light, vertical composition, photorealistic",
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


def bajar_archivo(url, ruta):
    """Descarga binaria con curl: reintentos contra cortes de red.
    Sin -C - (resume): Pollinations no soporta rangos de bytes y curl
    falla con rc 33 cuando el archivo ya existe (verificado 13-nov-2026);
    cada intento descarga desde cero y se borra el archivo previo."""
    if os.path.exists(ruta):
        os.remove(ruta)
    for i in range(3):
        r = subprocess.run(
            ["curl", "-fsSL", "--retry", "2", "--retry-all-errors",
             "--retry-delay", "5", "-o", ruta, url],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and os.path.exists(ruta) and os.path.getsize(ruta) > 10000:
            return os.path.getsize(ruta)
        print(f"curl intento {i}: rc={r.returncode} {r.stderr.strip()[-200:]}", flush=True)
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"no se pudo bajar {url}")


def http_get(url, timeout=45, intentos=2):
    """GET de texto (Pollinations). Un 4xx no se reintenta."""
    import urllib.error

    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={**UA, "Connection": "close"})
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


def contar_palabras(texto):
    return len([w for w in re.split(r"\s+", texto.strip()) if w])


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


def _llamada_guion(prompt, n_parrafos, palabras_min, palabras_max, obligatorias):
    """Una llamada a Pollinations texto. Devuelve (parrafos, palabras) o None."""
    url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt)
    try:
        status, body = http_get(url, timeout=45)
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
        and all(k in bajo for k in obligatorias)
    )
    print(f"Pollinations texto: status={status}, palabras={palabras}, parrafos={len(paras)}, ok={ok}", flush=True)
    return (paras, palabras) if ok else None


def generar_guion(tema):
    """Guion en 2 llamadas a Pollinations texto (anónimo, modelo por defecto).
    Una sola llamada con ~110 palabras da HTTP 400 output_token_limit_exceeded
    (verificado 13-nov-2026); dos llamadas de ~50/55 palabras quedan bajo el
    límite, pero SOLO con instrucción corta: con la instrucción larga la
    llamada 1 daba 400 incluso pidiendo 40 palabras. Verificado 13-nov-2026.
    El fallback verificado es solo último recurso si alguna llamada falla."""
    # Instrucción CORTA (ver arriba): la larga empuja el total por encima
    # del límite anónimo y da 400.
    prompt1 = (
        f"Guion en español, 3 párrafos cortos, unas 50 palabras, sobre {tema}. "
        "Datos: primer reloj de cuarzo del mundo, 25 de diciembre de 1969, "
        "cien unidades en oro de 18 quilates. Usa solo estos datos, no inventes otros. "
        "Primer párrafo: gancho con el precio de un carro. Solo el guion."
    )
    prompt1_corto = prompt1.replace("unas 50 palabras", "unas 40 palabras")
    prompt2 = (
        f"Guion en español, 2 párrafos cortos, unas 55 palabras, sobre {tema}. "
        "Datos: cristal de cuarzo de 8192 hercios, precisión de 5 segundos al mes, "
        "desató la crisis del cuarzo. Usa solo estos datos, no inventes otros. "
        "Último párrafo termina exactamente con: "
        "Sígueme si quieres más historias de relojes imposibles. Solo el guion."
    )
    prompt2_corto = prompt2.replace("unas 55 palabras", "unas 40 palabras")
    parte1 = None
    for p in (prompt1, prompt1_corto):
        parte1 = _llamada_guion(p, 3, 35, 60, ["astron"])
        if parte1:
            break
    parte2 = None
    for p in (prompt2, prompt2_corto):
        parte2 = _llamada_guion(p, 2, 35, 65, ["sígueme"])
        if parte2:
            break
    if parte1 and parte2:
        clips = parte1[0] + parte2[0]
        texto = " ".join(clips)
        bajo = texto.lower()
        ok = (
            len(clips) == 5
            and 80 <= contar_palabras(texto) <= 170
            and ("1969" in texto or "sesenta y nueve" in bajo)
            and "cuarzo" in bajo
            and "astron" in bajo
            and "sígueme" in bajo
        )
        print(f"guion 2 llamadas: {contar_palabras(texto)} palabras, ok={ok}", flush=True)
        if ok:
            REPORT["guion_llamada1_palabras"] = parte1[1]
            REPORT["guion_llamada2_palabras"] = parte2[1]
            return clips, "pollinations-2-llamadas"
        print("Guion combinado no válido, uso fallback", flush=True)
    else:
        print("Alguna llamada del guion falló, uso fallback", flush=True)
    return list(GUION_FALLBACK), "fallback"


def bajar_imagenes():
    os.makedirs(IMG_DIR, exist_ok=True)
    rutas = []
    for i, prompt in enumerate(PROMPTS_IMAGEN):
        url = (
            "https://image.pollinations.ai/prompt/"
            + urllib.parse.quote(prompt)
            + "?model=sana&width=768&height=768&seed=11"
        )
        ruta = os.path.join(IMG_DIR, f"{i}.jpg")
        try:
            n = bajar_archivo(url, ruta)
        except Exception as e:
            raise RuntimeError(f"imagen {i} no se pudo bajar: {e}")
        with open(ruta, "rb") as f:
            magic = f.read(2)
        if magic != b"\xff\xd8" or n < 10000:
            raise RuntimeError(f"imagen {i}: archivo no válido ({n} bytes)")
        print(f"imagen {i}: {n} bytes", flush=True)
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


def generar_voz(texto, wav_path):
    from kokoro_onnx import Kokoro

    ultimo_error = None
    for nombre, modelo, voices in bajar_modelo_kokoro():
        for lang in ("es", None):
            try:
                print(f"voz: modelo={nombre} lang={lang}", flush=True)
                kokoro = Kokoro(modelo, voices)
                kwargs = {"voice": "em_santa", "speed": 1.0}
                if lang:
                    kwargs["lang"] = lang
                samples, sr = kokoro.create(texto, **kwargs)
                import soundfile as sf

                sf.write(wav_path, samples, sr)
                return nombre, lang or "defecto"
            except Exception as e:
                ultimo_error = e
                print(f"voz falló modelo={nombre} lang={lang}: {e}", flush=True)
    raise RuntimeError(f"Kokoro falló en todos los intentos: {ultimo_error}")


def duracion_audio(wav_path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", wav_path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def envolver(texto, ancho=28):
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


def escribir_ass(clips, duraciones, ruta):
    """Subtítulos en .ass con PlayResX/Y = 720x1280. Un .srt sin PlayRes hace
    que libass asuma 384x288 y escale la fuente ~4,4x: las letras salían
    gigantes y cortadas por los bordes (verificado 13-nov-2026)."""
    t = 0.0
    eventos = []
    for clip, d in zip(clips, duraciones):
        ini, fin = t, t + d
        texto = envolver(clip).replace("\\n", "\\N")
        eventos.append(
            f"Dialogue: 0,{_fmt_ass(ini)},{_fmt_ass(fin)},Default,,0,0,0,,{texto}"
        )
        t = fin
    contenido = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 720\n"
        "PlayResY: 1280\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,DejaVu Sans,34,&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,40,40,130,1\n"
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


def renderizar(imagenes, duraciones, wav_path, srt_path, salida):
    segs = []
    for i, (img, d) in enumerate(zip(imagenes, duraciones)):
        frames = max(1, int(round(d * 30)))
        seg = os.path.join(WORK, f"seg{i}.mp4")
        cmd([
            "ffmpeg", "-y", "-i", img, "-vf",
            f"scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
            f"zoompan=z='1+0.12*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s=720x1280:fps=30,format=yuv420p",
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tema", default="Seiko Astron")
    ap.add_argument("--duracion", type=float, default=35)
    ap.add_argument("--outdir", default=OUT)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    t_total = time.time()
    REPORT.update({"tema": args.tema, "duracion_objetivo_s": args.duracion})
    try:
        with fase("guion"):
            clips, fuente = generar_guion(args.tema)
            texto = " ".join(clips)
            REPORT["guion_fuente"] = fuente
            REPORT["guion_palabras"] = contar_palabras(texto)
            REPORT["guion_clips"] = len(clips)
            print(f"guion: {REPORT['guion_palabras']} palabras, {len(clips)} clips, fuente={fuente}", flush=True)
        with fase("imagenes"):
            imagenes = bajar_imagenes()
            REPORT["imagenes_ok"] = len(imagenes)
        wav_path = os.path.join(WORK, "voz.wav")
        with fase("voz"):
            modelo, lang = generar_voz(texto, wav_path)
            REPORT["voz_modelo"] = modelo
            REPORT["voz_lang"] = lang
            REPORT["voz_duracion_s"] = round(duracion_audio(wav_path), 2)
            print(f"voz: {REPORT['voz_duracion_s']} s, modelo={modelo}", flush=True)
        with fase("render"):
            d_total = REPORT["voz_duracion_s"]
            chars = [len(c) for c in clips]
            duraciones = [d_total * c / sum(chars) for c in chars]
            REPORT["clip_duraciones_s"] = [round(d, 2) for d in duraciones]
            srt_path = os.path.join(WORK, "subs.ass")
            escribir_ass(clips, duraciones, srt_path)
            salida = os.path.join(args.outdir, "video.mp4")
            renderizar(imagenes, duraciones, wav_path, srt_path, salida)
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
