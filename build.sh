#!/bin/bash
# The app is one file, readers_scanner.py; it is kept in parts/ to stay readable. This glues them.
set -e
cd "$(dirname "$0")"
cat parts/00_head.py parts/05_languages.py parts/10_core.py parts/20_engine.py parts/30_ui.py parts/40_main.py > readers_scanner.py
chmod +x readers_scanner.py
python3 -c "import ast; ast.parse(open('readers_scanner.py').read())"
