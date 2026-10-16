.PHONY: test check
test:
	PYTHONPATH=apps/heating_ai python -m unittest discover -s tests -v
check:
	python -m compileall -q apps/heating_ai
	python -c "import json,pathlib; json.loads(pathlib.Path('hacs.json').read_text()); assert [p.name for p in pathlib.Path('apps').iterdir() if p.is_dir()]==['heating_ai']"
