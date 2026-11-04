#!/usr/bin/env sh
# Saca el esqueleto (doctype/html/head/body) de un prototipo de docs/prototipos/ para
# publicarlo como artifact de Claude, que pone el suyo. Los prototipos son documentos
# HTML completos (abrirlos desde el repo anda); esas líneas van solas, sin nada más.
#   tools/publicar_prototipo.sh docs/prototipos/f1-reconocimiento.html > /tmp/f1.html
# Límite: el esqueleto del artifact no lleva lang="es" (lo pone la plataforma).
set -eu
if [ $# -ne 1 ] || [ ! -f "$1" ]; then
  echo "uso: $0 docs/prototipos/<prototipo>.html" >&2
  exit 2
fi
grep -v -i -E '^[[:space:]]*(<!doctype html>|</?html([[:space:]][^>]*)?>|</?head>|</?body>|<meta charset=[^>]*>|<meta name="viewport"[^>]*>)[[:space:]]*$' "$1"
