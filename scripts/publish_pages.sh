#!/usr/bin/env bash
# Veltora Studio — publica la página y los videos en GitHub Pages.
# Uso: publish_pages.sh [video.mp4] [run_id] [tema] [formato] [duracion_s]
# Sin argumentos: solo republica la página (sin video nuevo).
# Con argumentos: añade el video como videos/<run_id>.mp4 y actualiza
# videos/latest.json.
#
# Estrategia: rama gh-pages huérfana (1 solo commit). Cada publicación
# reconstruye la rama desde cero con la página actual + los últimos 20
# videos. Así el historial no crece con blobs de 3 MB por video.
# La página (page/) vive en main; este script la copia a la raíz de
# gh-pages. Los videos viejos se conservan descargándolos de la rama
# anterior antes de reconstruir.
#
# Requisitos: GITHUB_TOKEN con contents:write (el workflow ya lo tiene) y
# GITHUB_REPOSITORY (owner/repo). No usa acciones de terceros: solo git.
set -euo pipefail

REPO_DIR="${GITHUB_WORKSPACE:-.}"
PAGE_DIR="$REPO_DIR/page"
MAX_VIDEOS=20

VIDEO_SRC="${1:-}"
RUN_ID="${2:-}"
TEMA="${3:-}"
FORMATO="${4:-}"
DURACION_S="${5:-}"
export RUN_ID TEMA FORMATO DURACION_S

cd "$REPO_DIR"
OWNER="${GITHUB_REPOSITORY%%/*}"
REPO="${GITHUB_REPOSITORY##*/}"
PAGES_URL="https://$OWNER.github.io/$REPO"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. Recuperar los videos de la publicación anterior (si existe).
mkdir -p "$TMP/new/videos"
if git ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
  git clone -q --depth 1 --branch gh-pages "https://github.com/$GITHUB_REPOSITORY.git" "$TMP/old" 2>/dev/null || true
  if [ -d "$TMP/old/videos" ]; then
    cp "$TMP/old/videos/"*.mp4 "$TMP/new/videos/" 2>/dev/null || true
    echo "videos anteriores recuperados: $(ls "$TMP/new/videos" 2>/dev/null | wc -l)"
    # No republicar videos cuyo run fue borrado en la app: borrar el run
    # tiene que significar que el video desaparece de verdad (14-nov-2026).
    # Solo se elimina con 404 confirmado; cualquier otra respuesta (403,
    # error de red) conserva el archivo para no borrar por un fallo de la API.
    for f in "$TMP/new/videos/"*.mp4; do
      [ -e "$f" ] || continue
      id="$(basename "$f" .mp4)"
      code="$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/repos/$GITHUB_REPOSITORY/actions/runs/$id" || true)"
      if [ "$code" = "404" ]; then
        echo "video $id.mp4: run borrado en la app, no se republica"
        rm -f "$f"
      fi
    done
  fi
fi

# 2. Página actual desde main.
cp "$PAGE_DIR/index.html" "$PAGE_DIR/manifest.json" "$PAGE_DIR/icon.svg" "$TMP/new/"
touch "$TMP/new/.nojekyll"

# 3. Video nuevo (si lo hay).
if [ -n "$VIDEO_SRC" ] && [ -f "$VIDEO_SRC" ]; then
  cp "$VIDEO_SRC" "$TMP/new/videos/$RUN_ID.mp4"
  echo "video nuevo: $RUN_ID.mp4 ($(stat -c%s "$TMP/new/videos/$RUN_ID.mp4") bytes)"
fi

# 4. Podar a los últimos 20 videos (por fecha de modificación).
cd "$TMP/new/videos"
ls -t *.mp4 2>/dev/null | tail -n +$((MAX_VIDEOS + 1)) | xargs -r rm -f || true
cd "$TMP/new"

# 5. latest.json con el video más reciente (si hay video nuevo).
if [ -n "$RUN_ID" ]; then
  VIDEO_URL="$PAGES_URL/videos/$RUN_ID.mp4"
  python3 - "$VIDEO_URL" <<'EOF'
import json, os, sys, time
url = sys.argv[1]
data = {
    "run_id": os.environ.get("RUN_ID", ""),
    "tema": os.environ.get("TEMA", ""),
    "formato": os.environ.get("FORMATO", ""),
    "duracion_s": os.environ.get("DURACION_S", ""),
    "video_url": url,
    "fecha_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
with open("videos/latest.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(json.dumps(data, indent=2, ensure_ascii=False))
EOF
fi

# 6. Commit huérfano y push forzado a gh-pages.
git init -q -b gh-pages
git config user.name "video-bot"
git config user.email "video-bot@users.noreply.github.com"
git add -A
git commit -qm "Veltora Studio: publicar página y videos ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
git remote add origin "https://x-access-token:${GITHUB_TOKEN}@github.com/$GITHUB_REPOSITORY.git"
git push -q --force origin gh-pages
echo "PUBLICADO en $PAGES_URL"
echo "Página: $PAGES_URL/"
if [ -n "$RUN_ID" ]; then
  echo "Video: $PAGES_URL/videos/$RUN_ID.mp4"
fi
