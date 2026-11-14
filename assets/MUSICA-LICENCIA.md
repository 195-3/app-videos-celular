# Música de fondo — procedencia y licencia

**Decisión del usuario (13-nov-2026):** SÍ a la música de fondo, **SIN derechos de autor** (ya tuvo un Short bloqueado por la canción "Speed Energy" con reclamo de Content ID).

## Pista elegida

| Dato | Valor |
|---|---|
| Título | Lovely Piano Song |
| Autor | Kevin MacLeod |
| Licencia | **CC0 1.0 Universal (dominio público)** — sin atribución obligatoria, uso comercial y no comercial, modificación permitida |
| Licencia (URL) | https://creativecommons.org/publicdomain/zero/1.0/ |
| Archivo en el repo | `assets/musica-fondo.mp3` (3.831.480 bytes, MP3 320 kbps, 44,1 kHz, estéreo, 95,8 s) |
| Duración | 95,8 s (más larga que el video más largo, 60 s: solo recorte, sin bucle) |
| Carácter | Piano suave y parejo (media −20,8 dB, máx. −3,5 dB; tramos de 10 s entre −19,7 y −21,7 dB: sin picos bruscos) |

## Cadena de verificación (13-nov-2026, todo comprobado en vivo)

1. **FreePD.com (fuente original, cerrada a finales de 2025):** su lema era *"Public Domain Music — Creative Commons 0 — Completely Royalty Free"*. Verificado en la captura del Internet Archive de su página principal (ene-2025) y en su aviso de cierre (página actual, nov-2026: "sharing millions of free-to-use, Public Domain music downloads").
2. **Archivo original:** Wayback Machine CDX para `https://freepd.com/music/Lovely%20Piano%20Song.mp3` — captura 2019-04-03, HTTP 200, `audio/mpeg`, digest SHA-1 (base32) `VH37BKF2IFY4U6TMAMBZFMU65UB6TXZP`, 2.789.022 bytes (el digest no cambió en ninguna captura 2019–2025).
3. **Descarga verificada:** el archivo descargado del Internet Archive (`web.archive.org/web/20190403072328id_/https://freepd.com/music/Lovely%20Piano%20Song.mp3`) y el del espejo (`raw.githubusercontent.com/SoundSafari/CC0-1.0-Music/main/freepd.com/Lovely%20Piano%20Song.mp3`) miden 3.831.480 bytes y su SHA-1 en base32 es **idéntico al digest del CDX**. La copia del espejo es bit a bit el archivo original de FreePD.
4. **Espejo:** `SoundSafari/CC0-1.0-Music` (README: colección de música de dominio público/CC0-1.0 agrupada por el sitio que la agregaba originalmente; con proceso de retirada si algo estuviera mal categorizado).
5. **Por qué no Incompetech:** la misma música de Kevin MacLeod en incompetech.com es **CC-BY (exige atribución)**; la versión de FreePD es la misma obra dedicada al dominio público (CC0). Se usa la versión CC0, no la CC-BY.

## Mezcla (cómo suena en el video)

- La música va a **volumen 0,2 (~−14 dB)** debajo de la voz, con fundido de entrada (1 s) y de salida (últimos 2 s). Medido: música sola a 0,2 = media −34,8 dB; voz = media −23,2 dB → la música queda **~11 dB por debajo de la voz**: audible, sin taparla.
- Mezcla con `amix` (`normalize=0`) y la voz subida a estéreo 48 kHz con `pan` antes de mezclar. Medido en la mezcla final: media −22,9 dB, máx. −1,5 dB (la voz queda intacta; sin recorte).
- La música es opcional: si la pista no está y no se puede descargar, el video sale solo con voz (el run no falla por la música). Se puede apagar por video desde la página (casilla "Música de fondo") o con `--musica no`.

## Sobre el riesgo de Content ID (honesto)

- **CC0 significa que no hay derechos de autor sobre esta pista:** nadie puede reclamarla legalmente. El usuario tiene el derecho y la prueba (este archivo) para disputar cualquier reclamo.
- **Un reclamo automático erróneo sigue siendo posible con cualquier audio** (los sistemas automáticos se equivocan, incluso con música de dominio público). La diferencia con "Speed Energy" es que aquella tenía dueño; esta no. Si algún día llegara un reclamo, se disputa con la licencia CC0 y esta procedencia, y se gana.
- Si algún día se cambia de pista, hay que repetir esta verificación (título, autor, licencia, digest) y actualizar este archivo. **Nunca usar una pista sin verificar su licencia.**
