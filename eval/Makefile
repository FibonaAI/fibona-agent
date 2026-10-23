# Run Fibona on Harbor datasets on our own hosts: make run CONFIG=configs/<name>.yaml.

# The one Harbor version; uv fetches and caches it on first use.
HARBOR_VERSION := 0.24.0
PYTHON := uv run --no-project --with harbor==$(HARBOR_VERSION) python
# Python for task containers, staged here and synced to /opt/fibona/python on every host.
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/python

need = $(foreach v,$1,$(if $($v),,$(error $v is required)))

.PHONY: run dist sync

# One harbor process schedules every task on the config's hosts and writes jobs/<run>.
run: sync
	PYTHONPATH=. $(PYTHON) run.py $(CONFIG)

# Our wheel, its pinned dependency wheels, uv and Python. Task containers install from them offline,
# so hosts must share this machine's CPU architecture.
dist:
	rm -rf dist && uv build --wheel -o dist ..
	uv export --project .. --frozen --no-hashes --no-dev --no-emit-project -q > dist/requirements.txt
	uv python install 3.12
	uvx --python 3.12 pip download -q --only-binary=:all: -r dist/requirements.txt -d dist
	cp "$$(command -v uv)" dist/

# Copy dist and python to /opt/fibona on every host in the config.
sync: dist
	$(call need,CONFIG)
	$(PYTHON) run.py $(CONFIG) hosts | while read -r host; do \
	  ssh -n "$$host" 'sudo mkdir -p /opt/fibona && sudo chown "$$USER" /opt/fibona' && \
	  rsync -a --delete dist python "$$host:/opt/fibona/" </dev/null || exit 1; \
	done
