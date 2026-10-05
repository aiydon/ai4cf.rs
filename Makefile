# ai4cf — download Codeforces problems, solve them with pi/deepseek, verify, report cost.
#
# Everything is configured in .env; `make solve LIMIT=5 JOBS=4` overrides it for one
# run (an exported variable wins over .env, `python-dotenv` never clobbers the env).

SHELL := /bin/bash
.DEFAULT_GOAL := help
UV    ?= uv
AI4CF ?= $(UV) run --quiet ai4cf
PROBLEM_DIR ?= problem

ifneq ($(LIMIT),)
export AI4CF_LIMIT := $(LIMIT)
endif
ifneq ($(JOBS),)
export AI4CF_JOBS := $(JOBS)
endif
ifneq ($(PROBLEM),)
export AI4CF_PROBLEM := $(PROBLEM)
endif

.PHONY: help all download solve smoke verify fmt lint cost status clean distclean

help:  ## show the available targets
	@printf '%s\n' \
	  'make all              download the problemset, then solve every pending problem' \
	  'make download         crawl the listing and create problem workspaces' \
	  'make solve            run pi on every pending problem (resumable)' \
	  'make smoke            download + solve exactly one problem (LIMIT=1)' \
	  'make verify           run every solved problem against its sample tests' \
	  'make fmt              rustfmt every main.rs and static/input.rs' \
	  'make lint             ruff check/format on this Python package' \
	  'make cost             per-problem cost, tokens and totals' \
	  'make status           per-problem state: done / failed / pending' \
	  'make clean            remove Rust build artifacts' \
	  'make distclean        remove every generated problem workspace' \
	  '' \
	  'overrides: make <target> LIMIT=N JOBS=N' \
	  'examples:  make solve PROBLEM=2262/F   (via uv run ai4cf solve -p 2262/F)' \
	  '           make all LIMIT=0 JOBS=4'

all: download solve  ## download + solve

download:  ## crawl the problemset and materialize problem workspaces
	$(AI4CF) download

solve:  ## run pi on every pending problem
	$(AI4CF) solve

smoke:  ## download and solve a single problem
	$(MAKE) all LIMIT=1

verify:  ## sample-test every solved problem
	$(AI4CF) verify

fmt:  ## format all Rust sources
	$(AI4CF) fmt

lint:  ## lint and check formatting of the Python package
	$(UV) run ruff check .
	$(UV) run ruff format --check .

cost:  ## cost report (per problem and total)
	$(AI4CF) cost

status:  ## per-problem state
	$(AI4CF) status

clean:  ## remove Rust build artifacts
	@test -d $(PROBLEM_DIR) && find $(PROBLEM_DIR) -type d -name target -prune -print -exec rm -rf {} + || true

distclean:  ## remove all generated problem workspaces
	rm -rf $(PROBLEM_DIR)/[0-9]*
