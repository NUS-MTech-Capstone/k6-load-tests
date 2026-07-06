K6 ?= k6

.PHONY: smoke load stress agentic audit inspect

smoke:
	$(K6) run scripts/smoke.js

load:
	$(K6) run scripts/session_lifecycle.js

stress:
	$(K6) run scripts/stress_session_start.js

agentic:
	$(K6) run scripts/agentic_ai_reads.js

audit:
	$(K6) run scripts/audit_reads.js

inspect:
	$(K6) inspect scripts/smoke.js
	$(K6) inspect scripts/session_lifecycle.js
	$(K6) inspect scripts/agentic_ai_reads.js
	$(K6) inspect scripts/audit_reads.js
	$(K6) inspect scripts/stress_session_start.js
