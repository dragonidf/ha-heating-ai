.PHONY: test check ui
ui:
	python tools/build_ui.py
test:
	PYTHONPATH=apps/heating_ai python -m unittest discover -s tests -v
	node --test tests/frontend.test.mjs
check:
	python -m compileall -q apps/heating_ai
	python tools/build_ui.py --check
	node --check frontend/heating-panel.js
	python -c "import json,pathlib; json.loads(pathlib.Path('hacs.json').read_text()); assert [p.name for p in pathlib.Path('apps').iterdir() if p.is_dir()]==['heating_ai']"
