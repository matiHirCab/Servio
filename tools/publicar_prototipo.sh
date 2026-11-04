#!/usr/bin/env sh
# Saca el esqueleto (doctype/html/head/body) de un prototipo de docs/prototipos/ para
# publicarlo como artifact de Claude, que pone el suyo. Uso: tools/publicar_prototipo.sh docs/prototipos/f1-reconocimiento.html > /tmp/f1.html
grep -v -E '^(<!doctype html>|</?html( lang="es")?>|</?head>|</?body>|<meta charset="utf-8">|<meta name="viewport" [^>]*>)$' "$1"
