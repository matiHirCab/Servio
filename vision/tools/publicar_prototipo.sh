#!/usr/bin/env sh
# Saca el esqueleto (doctype/html/head/body) de un prototipo de docs/prototipos/ para
# publicarlo como artifact de Claude, que pone el suyo. Los prototipos son documentos
# HTML completos (abrirlos desde el repo anda); esas líneas van solas, sin nada más.
#   vision/tools/publicar_prototipo.sh docs/prototipos/f1-reconocimiento.html > /tmp/f1.html
# Límite: el esqueleto del artifact no lleva lang="es" (lo pone la plataforma). <title> y los
# <link> de fuentes se dejan: la plataforma los toma del principio del archivo.
set -eu
if [ $# -ne 1 ] || [ ! -f "$1" ]; then
  echo "uso: $0 docs/prototipos/<prototipo>.html" >&2
  exit 2
fi
out=$(grep -v -i -E '^[[:space:]]*(<!doctype html>|</?html([[:space:]][^>]*)?>|</?head>|</?body([[:space:]][^>]*)?>|<meta[^>]*(charset|viewport)[^>]*>)[[:space:]]*$' "$1" || true)
# si quedó algo del esqueleto (una etiqueta compartiendo línea con otra cosa), no se publica así
if printf '%s\n' "$out" | grep -q -i -E '</?(html|head|body)([[:space:]>])'; then
  echo "$1: quedan etiquetas html/head/body que no están solas en su línea; acomodalas" >&2
  exit 3
fi
printf '%s\n' "$out"
