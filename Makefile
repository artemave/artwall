.DEFAULT_GOAL := check
.PHONY: check lint typecheck test coverage install-dev catalogue

# Run every check: lint, typecheck, and tests under 100%-coverage gate.
check: lint typecheck coverage

lint:
	python3 -m ruff check .

typecheck:
	python3 -m mypy

test:
	python3 -m unittest discover -s tests

coverage:
	python3 -m coverage run -m unittest discover -s tests
	python3 -m coverage report

# Regenerate the packaged first-run catalogue for the default filter-set (hits
# WDQS). Run + commit the result whenever DEFAULT_COLLECTIONS changes.
catalogue:
	python3 -c "from artwall.app import dump_catalogue; print('wrote', dump_catalogue())"

install-dev:
	python3 -m pip install -r requirements-dev.txt
	# Type stubs for the optional GTK overlay. Built from source for Gtk3 (the
	# package defaults to Gtk4) and --no-deps, since PyGObject itself is a system
	# package (python3-gobject), not something pip should build.
	PYGOBJECT_STUB_CONFIG=Gtk3,Gdk3 python3 -m pip install --no-deps --no-binary :all: PyGObject-stubs
