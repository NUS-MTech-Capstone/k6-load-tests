K6 ?= k6
NODE ?= node
PYTHON ?= python3

.PHONY: smoke load stress agentic audit smoke-report load-report stress-report agentic-report audit-report inspect

smoke:
	$(K6) run scripts/smoke.js

smoke-report:
	K6=$(K6) $(NODE) tools/run-k6-artifacts.mjs smoke

load:
	$(K6) run scripts/session_lifecycle.js

load-report:
	K6=$(K6) $(NODE) tools/run-k6-artifacts.mjs load

stress:
	$(K6) run scripts/stress_session_start.js

stress-report:
	K6=$(K6) $(NODE) tools/run-k6-artifacts.mjs stress

agentic:
	$(K6) run scripts/agentic_ai_reads.js

agentic-report:
	K6=$(K6) $(NODE) tools/run-k6-artifacts.mjs agentic

audit:
	$(K6) run scripts/audit_reads.js

audit-report:
	K6=$(K6) $(NODE) tools/run-k6-artifacts.mjs audit

inspect:
	$(K6) inspect scripts/smoke.js
	$(K6) inspect scripts/session_lifecycle.js
	$(K6) inspect scripts/agentic_ai_reads.js
	$(K6) inspect scripts/audit_reads.js
	$(K6) inspect scripts/stress_session_start.js
	$(K6) inspect scripts/terminal_capacity.js
	$(K6) inspect scripts/terminal_soak.js

test:
	$(NODE) --test tests/*.test.mjs
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'
