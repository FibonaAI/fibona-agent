# Everything is a job: make job CONFIG=<config.yaml>, starting from example.yaml. See job.py.

# Pinned tools; uv fetches and caches the Python ones on first use, crane mirrors task images.
HARBOR_VERSION := 0.24.0
VOLC_SDK_VERSION := 5.0.54
TOS_SDK_VERSION := 2.9.3
CRANE_VERSION := 0.20.2
PYTHON := uv run --no-project --with harbor==$(HARBOR_VERSION) --with volcengine-python-sdk==$(VOLC_SDK_VERSION) \
	--with tos==$(TOS_SDK_VERSION) python
PYPI_INDEX ?= https://pypi.org/simple
export UV_DEFAULT_INDEX := $(PYPI_INDEX)
# Python for task containers, staged here and synced to /opt/fibona/python on every host.
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/python

need = $(foreach v,$1,$(if $($v),,$(error $v is required)))

.PHONY: job dist image viz

# Run one benchmark job end to end and archive it: mirror task images, launch ECS workers, sync dist,
# run Harbor, delete the workers, upload experiments/<run>/ to the config's TOS archive.
# Usage: make job CONFIG=/path/to/config.yaml        (start from example.yaml; relative paths are from eval/)
# Workers are deleted however the job ends; only a SIGKILLed job leaves them, tagged fibona-job=<run> in ECS.
# Needs VOLCENGINE_ACCESS_KEY/VOLCENGINE_SECRET_KEY and TOS_ACCESS_KEY/TOS_SECRET_KEY in the environment.
job: dist bin/crane
	$(call need,CONFIG)
	$(PYTHON) job.py $(CONFIG)

# Build what job syncs to /opt/fibona on every worker: our wheel, its pinned dependency wheels, uv and
# Python. Task containers install from them offline, so workers must share this machine's CPU architecture.
# Usage: make dist                                   (make job runs it; use alone only to inspect dist/)
dist:
	rm -rf dist && uv build --wheel -o dist ..
	uv export --project .. --frozen --no-hashes --no-dev --no-emit-project -q > dist/requirements.txt
	uv python install 3.12
	uvx --with pysocks --python 3.12 pip@25.3 download --disable-pip-version-check -q --only-binary=:all: --index-url $(PYPI_INDEX) -r dist/requirements.txt -d dist
	cp "$$(command -v uv)" dist/

# Download the pinned crane binary that copies task images into the registry, digest for digest.
# Usage: make bin/crane                              (make job fetches it on first use)
bin/crane:
	mkdir -p bin
	curl -fsSL https://github.com/google/go-containerregistry/releases/download/v$(CRANE_VERSION)/go-containerregistry_Linux_x86_64.tar.gz \
		| tar xz -C bin crane

# Turn a fresh Ubuntu 22.04 ECS instance into a CPU worker like REF (see build/image.sh); afterwards stop
# it and create the custom image whose id goes in the configs' ecs.image_id. The reference bundle
# (Docker debs, proxy, registry login) streams from REF to HOST without touching this machine's disk.
# Usage: make image HOST=<new instance IP, root SSH> KEY=<harbor-runner private key> PUBKEY=<its .pub>
#        make image ... REF=<another configured worker>
REF ?= 10.42.2.111
image:
	$(call need,HOST PUBKEY KEY)
	ssh -i $(KEY) -o IdentitiesOnly=yes harbor-runner@$(REF) sudo -n bash -s bundle < build/image.sh \
		| ssh root@$(HOST) 'rm -rf /tmp/fibona-image && mkdir -m 700 /tmp/fibona-image && tar x -C /tmp/fibona-image'
	ssh root@$(HOST) bash -s -- "'$$(cat $(PUBKEY))'" < build/image.sh

# Browse jobs: an index on VIZ_PORT whose runs and trials open in harbor view on VIZ_PORT+1.
# Usage: make viz [VIZ_DATA=<jobs folder, e.g. unpacked TOS archives>] [VIZ_PORT=8080]  (rerun after new jobs)
# localhost only: harbor view has no access control and can delete jobs; use an SSH tunnel.
VIZ_PORT ?= 8080
VIZ_DATA ?= experiments
viz:
	python3 viz/build.py $(VIZ_DATA)
	trap 'kill 0' EXIT; \
	uv run --no-project --with harbor==$(HARBOR_VERSION) harbor view viz/jobs --jobs --port $$(($(VIZ_PORT) + 1)) & \
	python3 -m http.server -b 127.0.0.1 -d viz/site $(VIZ_PORT)
