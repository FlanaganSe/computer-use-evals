PoC Evaluation Harness for Desktop-Scale UI Agents
Executive summary and PoC scope
A “quick PoC evaluation harness” for desktop-scale UI agents should answer a narrow feasibility question: Can a local-first, hybrid sensor-fusion agent reliably complete multi-step, cross-app workflows under realistic OS constraints—while producing evidence (logs, metrics, replays) that surfaces failure modes early enough to make a go/no-go decision? This PoC is not a benchmark paper; it is a production-feasible test rig that can be evolved into an internal evaluation platform. The design targets cross-model comparability, repeatable tasks, and unknown-unknown discovery (rare dialogs, timing races, permission prompts, UI drift, multi-window quirks). The “not hacky” bar means: rely on official OS APIs, explicit permissions, clean process boundaries, and a stable observation/action contract rather than brittle pixel scraping alone. This aligns with how modern computer-use APIs recommend deploying UI-control agents—run them in isolated browser/VM harnesses, keep a human-in-the-loop for high-impact actions, and treat UI content as untrusted input. 

Research and industry evidence suggests that fully open-ended desktop automation remains difficult: OSWorld reports humans at ~72% success vs best model ~12% on open-ended tasks, indicating long-horizon compounding errors and GUI grounding issues remain major barriers. 
 Windows Agent Arena similarly reports ~19.5% model success vs ~74.5% human on Windows tasks and highlights the importance of scalable, parallelizable evaluation infrastructure. 
 Therefore, a PoC harness should be designed to (a) test hybrid sensing and semantic actions early, and (b) measure whether improvements are due to the harness (better state) vs the model (better planning).

Assumptions (explicit because they materially affect the design): OS distribution is unspecified; the team size is unspecified; the security posture is unspecified. The plan below therefore chooses defaults that are portable and safe: local-first by default, minimal privilege, explicit consent flows, and clear fallbacks where OS policies prohibit global observation/control (notably on Linux/Wayland). 

Minimal viable architecture with local-first hybrid sensor fusion
A production-feasible PoC should separate responsibilities into: (1) Context Broker (local sensors, normalized state), (2) Semantic Action API (typed actions with verification), (3) Model Adapter (per-model prompt/tool glue), and (4) Evaluation Orchestrator (task runner, metrics, replay, stress tests). This “gym-like” separation mirrors how evaluation ecosystems standardize observation/action spaces to enable cross-agent comparisons. 

Key principle: the harness owns truth and determinism; models are treated as interchangeable planners that consume normalized context and emit actions.

Evaluation Orchestrator

Local Harness Services

Model adapters

Model A\n(OpenAI / other)

Model B

Model C

OS + Apps

Accessibility APIs + events

Window/foreground events

Filesystem events

Clipboard/Pasteboard

Permissioned capture (screen/window)

Context Broker (daemon)

Semantic Action API (executor)

Policy: consent, allowlists, redaction

Audit log + replay store

Task suite + stressors

Runner (reset/step)

Metrics + scoring

Cross-model comparer



Show code
Why hybrid sensor fusion is the minimal viable “desktop-scale” approach: screenshot-only agents depend on repeated pixel interpretation, which is brittle in dense UIs (grounding benchmarks like ScreenSpot and ScreenSpot‑Pro show low best-model accuracy in high-resolution settings). 
 In contrast, parsing accessibility trees and window events provides more stable semantics (roles, names, enabled state), which Windows UI Automation explicitly provides for programmatic access and control patterns. 
 The PoC harness should therefore treat pixels as fallback and verification, not primary state.

Recent research supports this architectural split between planning and execution: GPA (Apr 2026) argues for fast, deterministic local GUI replay from demonstrations, positioned as a component other agents can call so they “reason and orchestrate while GPA handles GUI execution,” and reports comparative pilot findings (higher success rate and faster execution vs a computer-use baseline). 
 Even if you do not adopt that system directly, the harness should enable this “planner/executor” decomposition for evaluation.

Cross-OS instrumentation plan with permission models and fallbacks
The Context Broker should expose a uniform “desktop graph” built from officially supported primitives. OS-specific constraints determine what can be observed and controlled.

Accessibility tools - Inspect - Win32 apps | Microsoft Learn
OS X Accessibility Inspector (UIElementInspector) Tool for UI Scripting -  Questions & Suggestions - Keyboard Maestro Discourse
openSUSE Software
Accerciser - Download (Linux) - Softpedia

Windows instrumentation
Primary semantics (UI + events):

UI Automation (UIA) client API provides programmatic access to most desktop UI elements and exposes control patterns for interaction, enabling semantic action execution. 
WinEvent hooks via SetWinEventHook + event constants (e.g., EVENT_SYSTEM_FOREGROUND) enable event-driven updates (focus changes, UI events) rather than polling screenshots. 
Pixels (permissioned capture):

Windows.Graphics.Capture provides a modern capture API; it includes access classes and picker-based selection patterns, implying explicit user-mediated consent in many flows. 
Artifacts:

Filesystem: ReadDirectoryChangesW and related guidance enable watching directories for task verifiers (e.g., “file downloaded”). 
Clipboard: Win32 clipboard docs note that “all applications have access” and explicitly warn: “The clipboard should not be used to transfer sensitive data,” which should shape minimization policies in the harness. 
Control constraints (important for “production-feasible”):

UIPI can block cross-integrity UI automation; Microsoft’s guidance for automation tools describes UIPI as preventing certain interactions across different integrity levels and provides troubleshooting patterns. 

Fallback strategy: If UIA element access fails (e.g., custom-rendered apps), fall back to region/window capture + vision grounding (ScreenSpot-style) and coordinate-based actions, but mark these steps as higher-risk in evaluation logs. 
macOS instrumentation
Primary semantics (UI + events):

AXUIElement provides access to accessibility objects; apps must be trusted accessibility clients (checked via AXIsProcessTrustedWithOptions). 
AXObserverCreate enables receiving accessibility notifications from a target application, supporting event-driven “UI changed” updates. 
Practical enumeration: Quartz Window Services and CGWindowListCopyWindowInfo provide window inventory/metadata in the user session, helpful for multi-window workflow state. 
Pixels (permissioned capture):

ScreenCaptureKit is the modern capture framework; Apple’s guidance notes the system prompts for Screen Recording permission the first time and requires restarting the app after granting permission. 
Artifacts:

File system events: Apple’s File System Events API provides notifications when directory hierarchies change (useful for verifiers). 
Clipboard: NSPasteboard exposes pasteboard access; it is system-wide and should be treated as sensitive context. 
Fallback strategy: If AX trees are incomplete for specific apps, prefer (1) application-level automation where available (e.g., browser automation via Playwright for web), then (2) pixels + coordinate actions as last resort, and log the fallback path explicitly to surface “accessibility coverage” as a feasibility risk. 

Linux with Wayland (and why this is not just “Linux = X11”)
Wayland’s security model limits the kind of global screen inspection and input injection that X11-era tools relied on; the ecosystem’s solution is portal-mediated, user-consented capture/control. 

Primary semantics (UI):

AT-SPI2 is the freedesktop accessibility stack, transported over D-Bus; GNOME documentation notes assistive technologies receive information via the AT-SPI D-Bus protocol, and toolkits like GTK implement it. 
Pixels and control via portals:

Screen cast portal: org.freedesktop.portal.ScreenCast for monitor/window capture sessions. 
Remote desktop portal: org.freedesktop.portal.RemoteDesktop for mediated remote desktop sessions and device types (keyboard/pointer/touch). 
InputCapture portal: for capturing input device events with compositor-controlled activation (strongly relevant to “unknown unknowns” like focus loss). 
Artifacts:

Filesystem: inotify is the standard Linux file event API (useful for watchers); fanotify exists for broader monitoring scenarios, but is higher privilege and should be avoided in a PoC unless your threat model demands it. 
Fallback strategy: When global desktop control isn’t available due to compositor/portal limitations, shift task coverage toward (a) instrumentable apps (browser automation, CLI tools), and (b) explicit user-consented portal sessions. Treat “cannot instrument this compositor” as a first-class outcome the harness must report, not a bug. 

Context Broker and Semantic Action API spec
A PoC should ship an SDK that makes “desktop scale” concrete: a uniform observation graph + typed actions + event stream + consent hooks. This makes cross-model evaluation feasible because models can be swapped without changing OS code.

Core data model (normalized)
DesktopSnapshot
timestamp_ms
focused_window_id
windows[] (WindowInfo)
a11y_roots[] (A11yNode summaries for top windows; full trees available by paging)
recent_events_cursor (for incremental fetch)
artifacts:
downloads_watch (optional)
clipboard_summary (redacted)
fs_recent (redacted paths)
A11yNode (summarized)
node_id, role, name_redacted, value_redacted, bounds, enabled, actions[], children_count
Event (append-only, event-sourced)
event_id, timestamp_ms, type, payload, risk_level, consent_context_id
These mirror what OS APIs naturally provide: UIA exposes general properties plus control patterns; AX/AT‑SPI expose roles/attributes/actions. 

Minimum viable method signatures (illustrative)
get_snapshot

snapshot + deltas

propose action

precheck + execute

events

postcheck + result

Planner model

Context Broker

Semantic Action API

OS APIs



Show code
Table A: API surface (PoC MVP)
API method	Inputs	Outputs	Permissions required (typical)
GET /capabilities	none	OS, supported sensors/actions, consent requirements	none
POST /consent/session	scopes[], purpose, ttl_ms	consent_session_id, granted scopes	user approval for capture/control on macOS/Wayland; admin policy optional 
GET /snapshot	window_scope, a11y_depth, redaction_level	DesktopSnapshot	accessibility permission on macOS; none/limited on Windows; AT‑SPI availability on Linux 
POST /events/subscribe	types[], filters, cursor	stream handle	WinEvent hooks on Windows; AXObserver on macOS; AT‑SPI events on Linux 
POST /capture/request	target (screen/window/region), purpose	capture_handle	Screen Recording permission on macOS; GraphicsCapture consent on Windows; ScreenCast portal on Wayland 
GET /capture/frame	capture_handle, format, max_px	image frame (optionally redacted)	same as above
POST /artifact/watch	kind (downloads/fs), path_scope, ttl_ms	watcher id	filesystem access; must be scoped/minimized 
POST /action/locate	selector (a11y query) + optional visual hint	element_ref + confidence	accessibility access; optional capture permission 
POST /action/invoke	element_ref	result + post-state hash	accessibility access 
POST /action/set_value	element_ref, text	result + verification	accessibility access; clipboard avoided by default 
POST /action/hotkey	key chord	result	input injection constraints vary; portals on Wayland 
POST /action/wait_for	predicate over snapshot/events + timeout	success/failure + evidence	none (uses broker state); reduces timing flakiness
POST /action/rollback	rollback_kind	best-effort undo; evidence	depends (e.g., app undo stacks); always audited
POST /audit/export	run_id	signed bundle (events/actions/screens)	encryption + access control recommended 

Security/consent hooks must be first-class: (a) explicit scope grants, (b) redaction levels, (c) run-level “kill switch,” and (d) immutable audit export. This is consistent with privacy risk management framing in NIST’s Privacy Framework and risk governance expectations in NIST AI RMF. 

Harness engineering patterns, memory/state policies, and auditability
Task authoring and intent categorization
A production-feasible PoC should define tasks as structured specs rather than ad-hoc scripts:

Intent category: e.g., web_form_fill, file_transform, email_draft, cross_app_copy_paste, install_configure, search_retrieve, data_entry.
Preconditions: OS locale, network availability, seeded accounts/test sites, initial window state.
Observable success criteria: ideally machine-checkable (file exists, text equals, settings toggled). OSWorld emphasizes execution-based evaluation; this is the gold standard for multi-step tasks. 
Sensor requirements: explicitly declare what the harness is allowed to read (a11y, files, capture). This is critical for feasibility decisions because it quantifies “how often we fall back to pixels.”
Readiness checks, verification, rollback
End-to-end reliability is dominated by timing, modal dialogs, and transient windows. PoC harnesses should implement:

Readiness checks as primitives (wait_for) rather than letting models guess timing. This mirrors robust automation practice and is a core enabler for multi-step evaluations. 
Verification after every high-impact step: compare before/after state hashes on target windows/elements; verify file system artifacts via watchers (Windows ReadDirectoryChangesW, macOS FSEvents, Linux inotify). 
Rollback policy: implement best-effort undo where safe, but treat rollback failures as first-class signals in the harness (unknown-unknown exposure).
Memory and state strategies for evaluation
For evaluation, memory is less about “making the model smarter” and more about making runs inspectable and comparable.

Episodic log (append-only): events + actions + tool outputs. This supports replay and root-cause analysis (critical for feasibility).
Periodic summaries: the harness (not necessarily the model) generates step summaries for later failure clustering and cross-model comparison; when using LLM summarization, batch it (non-interactive) to reduce cost. 
Retrieval over past episodes: for evaluation analytics (“did this model fail for the same reason?”), store embeddings of summaries keyed by app/task/error signature; keep raw screenshots out of the retrieval store by default.
Retention/redaction policies: default to minimal retention and redact sensitive text fields; GDPR’s data minimization principle is a useful baseline even outside the EU. 
Evaluation design, task templates, metrics, and unknown-unknown stress testing
A PoC harness should combine: (1) a small deterministic core suite (repeatable, machine-scored), (2) a semi-open suite (controlled websites/apps with variability), and (3) stress tests designed explicitly to surface unknown unknowns.

Metrics (PoC-focused but research-aligned)
Task success rate and time-to-success (end-to-end). OSWorld and Windows Agent Arena use success as the headline metric for open-ended tasks. 
Step success rate / recovery rate: percentage of failed steps recovered via fallback/repair. Grounding papers emphasize step-level evaluation because long-horizon success is a product of repeated correct localizations. 
Fallback rate: fraction of steps requiring pixel-only actions vs semantic actions (critical feasibility indicator).
Latency distribution: p50/p95 wall time per step and per task; vendor docs emphasize latency optimization patterns and batching for efficiency. 
Safety/compliance metrics: number of times consent scopes were escalated; number of high-risk actions gated/blocked; audit completeness (events/actions captured). 
Table B: task suite templates (small but extensible)
Task template	Description	Success criteria (machine-checkable)	Sensors required	Difficulty
Download-and-verify	Fetch a file from a seeded test site and confirm it appears in Downloads	File exists with expected name/size/hash	Browser control + FS watcher	Low
Copy across apps	Copy structured text from a doc viewer into a spreadsheet row	Cell values match expected schema	A11y + optional clipboard summary	Med
Multi-window form workflow	Open settings dialog, set 2 values, confirm persisted	Values read back from UI	A11y + window events	Med
Authentication gate handling	Log into a test web app with MFA simulator (single-use code)	Reaches post-login page	Browser control + capture fallback	Med–High
File transform	Open a file, export to another format, verify output	Output file present + opens	A11y + FS watcher + capture fallback	High
Interrupt-driven dialog	During a task, inject a modal (permission/update prompt) and require safe handling	Task either completes or safely aborts with explanation	Window events + capture	High
Network flake	Drop network mid-task and require retry/backoff/outcome logging	Proper error classification; no unsafe repeated actions	System/network fault injector	High
DPI/scale drift	Change display scaling between steps; agent must recover element selection	Completes with bounded retries	A11y + capture + window bounds	High
Locale/IME shift	Switch keyboard layout/IME and execute text entry & verify	Correct string value in target field	A11y + semantic text actions	High

These templates align with observed benchmark pain points (long-horizon, multi-step control failures) in OSWorld and WAA but remain implementable in a PoC. 

Unknown-unknown stress testing (“chaos for GUIs”)
To surface feasibility risks, introduce structured randomness:

Dialog injection: synthetic modals (security prompt, save prompt) at random steps.
Timing jitter: random delays and asynchronous loads to test readiness checks.
UI drift: window repositioning, DPI scaling changes, theme switches; ScreenSpot‑Pro highlights that high-res professional UIs create small targets and complex layouts. 
Permission revocation: revoke screen recording or portal session mid-run (macOS/Wayland) and require safe degradation. 
Cross-app interference: notifications and focus stealing; on Windows, foreground-change events are explicitly observable via event constants, enabling the harness to detect and score robustness. 
Evaluation pipeline diagram (how runs become comparable artifacts):

Select tasks + stressors

Provision sandbox (VM/container)\nseed accounts, files

Run N episodes per model\n(time/cost budgets)

Collect: events, snapshots,\nactions, captures, artifacts

Automated scoring\n(success checks)

Failure clustering\n(root causes, fallback rate)

Cross-model report\n(win-rate, latency, safety)

Feasibility decision memo\n+ prioritized fixes



Show code
OSWorld and Windows Agent Arena emphasize reproducibility and scalable evaluation; WAA notes parallelization can reduce full evaluation time dramatically, which directly informs PoC harness choices (parallel run execution and clean environment resets). 

Model integration patterns, compliance checklist, and implementation plan
Recommended integration patterns (model-agnostic harness, per-model adapters)
A PoC should expose a single tool interface to models: “get_snapshot,” “subscribe_events,” “invoke/set_value/wait_for,” “request_capture,” and return structured results. This matches modern agent-native API patterns like the Responses API and built-in tools such as computer use. 

Vendor/tooling baselines to support in adapters:

OpenAI: Use the Responses API and computer-use guide patterns; the guide explicitly discusses different harness shapes and emphasizes isolation and human-in-loop gating. 
 Data controls documentation describes retention/training defaults and configurable usage expectations for API data. 
Google: Gemini Computer Use describes screenshot-based observation and action generation through a tool-like interface, suitable for a model adapter. 
Anthropic: The computer use tool documentation describes a tool schema and system prompt pattern; it can be adapted to call your local Semantic Action API instead of raw mouse/keyboard injection. 
Recent research directions worth enabling (even if not fully implemented): diffusion-based GUI grounding (Mar 2026) suggests alternative perception models can improve step success rate but introduce latency tradeoffs; the harness should therefore measure grounding accuracy vs latency as first-class metrics. 

Table C: integration patterns (local-only vs cloud-only vs hybrid)
Pattern	Pros	Cons	Privacy risk	Latency	Complexity
Local-only	Best data control; low network dependency; deterministic execution possible (e.g., semantics-first)	Requires local GPU/CPU capacity; local model quality may lag frontier; packaging effort	Lowest	Lowest–Med	High (systems + ML ops)
Cloud-only	Fastest to prototype; use vendor “computer use” tools directly	Highest privacy exposure; network jitter; per-step model calls amplify cost; long-horizon failures remain high	Highest	Highest	Low–Med
Hybrid (recommended PoC default)	Keep raw pixels local; send redacted/structured context; use cloud reasoning only when needed; can batch analytics	More engineering; must design redaction/consent carefully	Med (config-dependent)	Med	High

This table reflects vendor guidance emphasizing isolation, security controls, and latency optimization/batching as production best practices. 

Privacy/security/legal mitigations and compliance checklist (PoC-ready)
A PoC harness is effectively a privileged observability/control system. Treat it like security-sensitive infrastructure:

Data minimization: collect only the minimum required state; GDPR Article 5 and regulator guidance frame minimization as collecting “adequate, relevant and limited” data. 
Consent & transparency: use OS-native consent where required (macOS Screen Recording; Wayland portals) and record consent artifacts in the audit log. 
Encryption + access control: if using any cloud inference, rely on strong encryption in transit/at rest, and prefer configurable retention/zero-retention settings where available. 
Clipboard restrictions: treat clipboard as sensitive; Windows docs explicitly warn against using it for sensitive transfers, so default to semantic “set_value” rather than clipboard-based workflows. 
Sandboxing: run evaluations inside VMs or containerized desktops where feasible; this aligns with recommended practice for computer-use harnesses and enables clean resets between episodes. 
Governance: adopt a lightweight risk review aligned to NIST AI RMF and NIST Privacy Framework (threat modeling, risk tolerances, auditability). 
Prototype implementation plan (milestones, minimal dependencies, effort)
A realistic PoC can be executed in ~6–10 weeks by a small engineering team, depending on OS coverage and security requirements. This is an estimate (not a claim of fact) and assumes existing CI and basic infra.

Milestone 1: Harness skeleton + task runner (1–2 weeks)

Define task spec schema (YAML/JSON), reset/step loop, scoring hooks.
Implement run bundling: traces + screenshots (optional) + metrics.
Milestone 2: Windows + macOS “happy path” instrumentation (2–3 weeks)

Implement Context Broker collectors:
Windows UIA + WinEvent hooks + FS watcher + capture.
macOS AXUIElement + AXObserver + Window list + ScreenCaptureKit + FSEvents.
Implement Semantic Action API subset: locate/invoke/set_value/wait_for/hotkey.
Milestone 3: Wayland-first Linux support (2–3 weeks)

Implement AT-SPI state extraction (where available).
Implement portal-based capture/control (ScreenCast/RemoteDesktop/InputCapture).
Add capability negotiation: if compositor/portal backend lacks features, tasks are skipped with explicit “instrumentation unavailable” outcomes. 
Milestone 4: Cross-model adapters + cross-model reporting (1–2 weeks)

Adapters: Responses API tool-calling; Gemini Computer Use; Anthropic tool-calling.
Add batching for analytics and summaries (post-run) to reduce cost. 
Milestone 5: Unknown-unknown stressors + feasibility memo (1 week)

Add dialog injection, network flake, DPI drift, permission revocation scenarios.
Produce a “feasibility dashboard”: success, latency, fallback rate, top failure clusters.
Prioritized reading list (recent research + key industry docs)
Highest leverage for this PoC:

OSWorld (open-ended desktop tasks; execution-based evaluation). 
Windows Agent Arena (Windows-focused scalable evaluation; parallelization). 
BrowserGym ecosystem (standardized “gym-like” evaluation methodology, transferable to desktop harness design). 
SeeClick + ScreenSpot (screenshot-based GUI agent grounding benchmark). 
ScreenSpot‑Pro (high-resolution professional GUI grounding; highlights small-target complexity). 
GPA (Apr 2026; deterministic local GUI process automation from demonstrations; planner/executor split). 
Vision-language diffusion for GUI grounding (Mar 2026; accuracy/latency tradeoffs). 
Key OS and platform docs:

Windows UI Automation overview + control patterns; WinEventHook/event constants; Windows.Graphics.Capture; clipboard and filesystem watchers. 
macOS AXUIElement + AXObserver + trust checks; ScreenCaptureKit permission behavior; Quartz window services; FSEvents; NSPasteboard. 
Wayland/portal stack: XDG ScreenCast/RemoteDesktop/InputCapture; Wayland security posture. 
NIST AI RMF + NIST Privacy Framework; GDPR minimization principle text and regulator guidance. 
OpenAI computer-use guide, Responses API migration, latency optimization, batch, and data controls/enterprise privacy for deployment considerations. 