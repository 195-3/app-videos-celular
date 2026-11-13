# app-videos-celular — PRUEBA

Repo de prueba (solo prueba, nada de producción) del proyecto de hacer
videos verticales 30-45 s desde un tema, con GitHub Actions como runner.

## Qué hace la prueba

1. En Actions → `test-video` → Run workflow, con el tema
   (por defecto: Grandmaster Chime Patek Philippe).
2. El workflow genera guion (Pollinations, con fallback verificado),
   5 imágenes (Pollinations sana), voz Kokoro Santa (em_santa),
   y renderiza con FFmpeg a 720x1280 con subtítulos quemados.
3. El MP4 sale como artifact `video-35s` y el reporte con los tiempos
   queda en `run-report.json` en este repo.

## Coste

Repo público: minutos de Actions ilimitados, sin tarjeta.
Sin cuentas nuevas, sin datos personales, sin publicación automática.

## Licencia

Uso personal. El pipeline se basa en ideas del proyecto Sloth
(Apache 2.0 + Commons Clause): no se vende el software ni un servicio
basado en él. Los videos hechos con el pipeline son del usuario.
