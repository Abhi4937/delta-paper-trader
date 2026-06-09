# The Software Development Master Checklist
### An end-to-end reference: every phase, what you build, what you test, and the strong default

> **How to read "Best default":** There is no universal best — a 3-person startup, a regulated bank, and a 2,000-engineer platform org make different correct choices. "Best default" = the option a competent team reaches for absent special constraints. Forks are flagged with → *alt*.

> **The golden rule:** Don't collect tools per phase. Optimize the **DORA outcomes** (bottom of this doc) and pick whatever serves them.

---

## PHASE 0 — Discovery & Inception
*Decide whether the project should exist at all.*

**Build**
- [ ] Product vision & North Star metric
- [ ] Business case + success metrics (KPIs / OKRs)
- [ ] Market / competitive analysis
- [ ] Rough scope, cost, and risk register
- [ ] Stakeholder map
- [ ] MVP definition (concierge / Wizard-of-Oz where useful)

**Validate**
- [ ] Desirability (do users want it?)
- [ ] Viability (does it make business sense?)
- [ ] Feasibility (can we build it?)
- [ ] De-risk high-uncertainty bets with a spike/POC

**Best default:** Dual-track agile (continuous discovery feeding delivery) + a Design Sprint to validate anything high-risk before funding.

**Gate:** Funded, scoped, approved, with measurable success criteria defined.

---

## PHASE 1 — Requirements Engineering
*Turn fuzzy intent into a precise, testable spec.*

**Build**
- [ ] Functional requirements
- [ ] Non-functional requirements / quality attributes (use ISO/IEC 25010) → performance, scalability, availability, security, SLAs/SLOs
- [ ] User stories with acceptance criteria (INVEST-compliant)
- [ ] Story map (whole-journey view, not a flat backlog)
- [ ] Executable acceptance criteria in **BDD / Gherkin** (Given-When-Then)
- [ ] Prioritized backlog (MoSCoW / weighted scoring)
- [ ] Personas / Jobs-to-be-Done
- [ ] Wireframes / low-fi prototypes
- [ ] Compliance & regulatory requirements
- [ ] Requirements traceability matrix (requirement → test)

**Validate**
- [ ] Every requirement is unambiguous, complete, consistent, testable, traceable
- [ ] Conflicts resolved here, not in production
- [ ] "Definition of Ready" met

**Best default:** Story mapping for shape + BDD/Gherkin for testable criteria. This eliminates unverifiable requirements.

**Gate:** Every requirement has clear acceptance criteria mapped to a measurable test.

---

## PHASE 2 — Architecture & Technical Design
*Structure the system to meet requirements — especially the non-functional ones.*

**Build**
- [ ] High-level architecture (C4 model: context, container, component, code)
- [ ] Architectural style decision (monolith / microservices / event-driven / serverless)
- [ ] Domain model — **DDD** bounded contexts + context mapping (via **event storming**)
- [ ] Service boundaries aligned to **Team Topologies** (Conway's Law)
- [ ] Data architecture & schema design — including **consistency model** (strong vs eventual), **indexing**, **partitioning / sharding**, normalization vs denormalization, **multi-tenancy** model (SaaS: shared vs isolated), and **schema evolution / registry** approach
- [ ] **Contract-first API design** (OpenAPI / AsyncAPI / Protobuf)
- [ ] Technology selection + explicit build-vs-buy analysis
- [ ] Capacity & scaling plan
- [ ] **Caching strategy** — layers (client / CDN / app / DB), pattern (cache-aside / read-through / write-through / write-behind), TTL, eviction policy, and *invalidation* (the hard part)
- [ ] **Rate limiting / throttling / backpressure** — protect services from overload & abuse
- [ ] **API versioning & deprecation** strategy — evolve contracts without breaking clients
- [ ] **Async / background processing** — job queues, message brokers, workers for slow or bursty work
- [ ] Security architecture & threat model
- [ ] **Identity & access design** — authentication vs authorization, OAuth2 / OIDC, SSO, **RBAC / ABAC**, MFA, session & token management
- [ ] **Encryption design** — in transit (TLS) and at rest, key management (KMS) and rotation
- [ ] Resilience design (timeouts, retries, circuit breakers, idempotency, failure modes)
- [ ] **Architecture Decision Records (ADRs)** — the *why* behind each choice
- [ ] Fitness functions (for evolutionary architecture)
- [ ] Apply structural design principles (see Appendix): Separation of Concerns, **Low Coupling / High Cohesion**, Dependency Inversion, Composition over Inheritance

**Validate**
- [ ] Reviewed against quality attributes (scalability, availability, latency, maintainability, security, cost)
- [ ] Architecture review board / ATAM trade-off analysis
- [ ] Cloud Well-Architected framework checklist
- [ ] Benchmarks/spikes confirm unproven assumptions before commitment

**Best default:** C4 + ADRs + contract-first APIs, with Team Topologies guiding boundaries. Add fitness functions for evolving systems.

**Gate:** Architecture signed off, contracts agreed across teams, major risks mitigated.

---

## PHASE 3 — Planning & Foundation Setup
*Build the scaffolding that makes everything afterward repeatable and safe.*

**Build**
- [ ] Sprint/release plan & roadmap
- [ ] Work breakdown + estimates
- [ ] Repository structure (mono- vs multi-repo)
- [ ] **CI/CD pipeline skeleton**
- [ ] Environments (dev/test/staging/prod) via **Infrastructure as Code**
- [ ] Branching strategy
- [ ] Coding standards, linting, pre-commit hooks, scaffolding templates
- [ ] Secrets management (e.g. Vault) wired from day one
- [ ] Dev-prod parity (devcontainers / Nix)
- [ ] **Test-data management** strategy (seeding, anonymized prod data, synthetic data)
- [ ] **Ephemeral / preview environments** (spun up per pull request)
- [ ] Baseline observability wiring
- [ ] "Definition of Done"

**Validate**
- [ ] A trivial change flows the full pipeline end-to-end (**walking skeleton**)

**Best default:** Walking skeleton + trunk-based development. At scale: an **Internal Developer Platform** (golden paths) so every new service inherits the paved road.

**Gate:** A developer can commit and have it auto-built, tested, and deployed to non-prod.

---

## PHASE 4 — Implementation / Development
*Write code in small, continuously integrated increments.*

**Build**
- [ ] Production source code
- [ ] Unit tests alongside/before code (TDD)
- [ ] Database migrations (expand-contract style)
- [ ] Feature flags for incomplete features
- [ ] Inline docs + documentation-as-code
- [ ] Observability instrumented *as you write* (logs/metrics/traces)
- [ ] Conventional commits
- [ ] Apply code-level design principles (see Appendix): **DRY, KISS, YAGNI**, **SOLID**, Encapsulation, Law of Demeter, Fail Fast, Least Astonishment

**Test (shift-left — starts here)**
- [ ] Static analysis (linters + **SAST** for security)
- [ ] **Secret scanning** (detect committed credentials — gitleaks / trufflehog) — distinct from secrets *management*
- [ ] Unit tests
- [ ] Type checking

**Best default:** TDD + trunk-based + feature flags + conventional commits, with telemetry written alongside the feature.

**Gate:** Compiles, unit tests pass, static analysis clean, ready for review.

---

## PHASE 5 — Code Review & Integration
*Peer quality gate + merge into shared code.*

**Build**
- [ ] Pull/merge request (small, ideally stacked diffs)
- [ ] Review comments & revisions
- [ ] Integrated mainline branch

**Validate**
- [ ] Correctness, readability, standards, security, test adequacy, architectural fit
- [ ] Review SLAs / checklists / automated review bots

**Test (CI runs automatically, blocks merge)**
- [ ] Full unit suite
- [ ] **Integration tests** (+ consumer-driven contract tests)
- [ ] Build verification
- [ ] Dependency vulnerability scan (SCA)
- [ ] Coverage thresholds
- [ ] **Merge queue** (so individually-green PRs don't break main when combined)

**Best default:** Small PRs + automated gating + merge queue. → *alt:* pair/mob programming replacing async review.

**Gate:** Reviewer-approved + all CI green → merge.

---

## PHASE 6 — Testing & QA
*Layered and largely automated. Run many cheap tests, few expensive ones.*

**Functional layers**
- [ ] Unit
- [ ] Integration (+ **contract testing**, e.g. Pact)
- [ ] Component / API
- [ ] End-to-end (E2E)
- [ ] Acceptance / UAT (against business criteria)
- [ ] Regression
- [ ] Smoke / sanity
- [ ] **Exploratory testing** (skilled, unscripted human)
- [ ] Visual regression / snapshot

**Non-functional**
- [ ] Performance — load, stress, soak/endurance, spike
- [ ] Scalability
- [ ] Security — **DAST**, penetration testing, SCA
- [ ] Reliability — **chaos engineering**, failover
- [ ] Usability & accessibility (WCAG)
- [ ] Compatibility (browser/device/OS)
- [ ] Compliance / audit

**Advanced techniques**
- [ ] Property-based testing
- [ ] Fuzzing
- [ ] Mutation testing (tests your tests)
- [ ] Canary analysis
- [ ] Flaky-test quarantine
- [ ] **Testing in production** (shift-right, behind flags)

**Best default:** Testing **trophy** (integration-weighted) for web/services + Pact for microservices + deliberate testing-in-production. → *alt:* classic pyramid for libraries / low-level code.

**Gate:** All suites pass, coverage/quality thresholds met, no open critical/high defects, perf & security targets satisfied.

---

## PHASE 7 — Build & Release Engineering
*Turn approved code into a deployable, versioned, reproducible artifact.*

**Build**
- [ ] Build artifact (binary / container image / package)
- [ ] Semantic versioning (automated: conventional commits → semantic-release)
- [ ] **Software Bill of Materials (SBOM)**
- [ ] Signed artifacts (**Sigstore / cosign**) + provenance (**in-toto**)
- [ ] Artifact registry storage
- [ ] Release notes / changelog (auto-generated)

**Validate**
- [ ] Reproducible/hermetic builds (Bazel where needed)
- [ ] Artifact integrity & provenance
- [ ] **SLSA**-aligned supply-chain security
- [ ] **Container image scanning** (OS-level vulns — Trivy / Grype) — distinct from dependency SCA
- [ ] **Open-source license compliance** (license scan + obligations; pairs with the SBOM)

**Best default:** SLSA-aligned pipeline + Sigstore signing + fully automated semantic versioning. Manual versioning / unsigned artifacts = legacy.

**Gate:** Immutable, signed, versioned artifact ready to promote.

---

## PHASE 8 — Deployment / Continuous Delivery
*Promote the artifact through environments into production, safely.*

**Build**
- [ ] Deployment manifests
- [ ] Config separated from code (config-as-data)
- [ ] **Expand-contract (parallel-change) DB migrations** — no downtime, no lockstep deploys
- [ ] Immutable infrastructure
- [ ] Multi-region / DR deployment

**Strategies**
- [ ] Blue-green (instant switch + rollback)
- [ ] Canary (small %, watch metrics, expand)
- [ ] Rolling (replace in batches)
- [ ] Feature flags / dark launch
- [ ] Shadow/mirror traffic

**Test**
- [ ] Post-deploy smoke tests
- [ ] Synthetic monitoring
- [ ] Automated canary analysis (new vs old metrics)
- [ ] Automatic rollback on failed health checks

**Best default:** **GitOps** (Argo CD / Flux) + **progressive delivery** (automated metric-based canary) + expand-contract migrations.

**Gate:** Healthy on production signals; rollback path verified.

---

## PHASE 9 — Operations, Observability & SRE
*Keep it alive, fast, and reliable.*

**Build**
- [ ] Observability stack — logs, metrics, **distributed traces** (standardize on **OpenTelemetry**)
- [ ] Dashboards
- [ ] SLIs / SLOs + **error budgets**
- [ ] Alerting rules (symptom/SLO-based — alert on user pain, not every cause)
- [ ] Runbooks
- [ ] On-call rotation + **Incident Command** structure (severities, roles)
- [ ] Status page
- [ ] **Backups + regularly tested restores** (an untested backup is not a backup)
- [ ] **RTO / RPO** targets defined (recovery time / data-loss tolerance)
- [ ] Log & data **retention policies**
- [ ] Continuous profiling + auto-remediation/self-healing

**Validate (continuously)**
- [ ] Golden signals (latency, traffic, errors, saturation)
- [ ] **RED** (Rate/Errors/Duration) + **USE** (Utilization/Saturation/Errors)
- [ ] Performance against SLOs

**Activities**
- [ ] Incident response + blameless postmortems
- [ ] Capacity planning
- [ ] Cost optimization (FinOps)
- [ ] DR drills
- [ ] Toil reduction via automation

**Best default:** OpenTelemetry + SLO/error-budget alerting + formal incident command + blameless postmortems.

**Gate (ongoing):** Stay within error budget; breaching it shifts priority from features to reliability.

---

## PHASE 10 — Maintenance, Evolution & Feedback Loop
*Where software spends most of its life and budget — and where the cycle closes.*

**Build**
- [ ] Bug fixes & security patches
- [ ] Performance improvements
- [ ] New features (driven by production analytics + user feedback)
- [ ] **Technical-debt remediation** (explicit budget each cycle; boy-scout rule)
- [ ] **Automated dependency updates** (Renovate / Dependabot)
- [ ] Deprecation / migration plans
- [ ] A/B & experimentation platform
- [ ] Knowledge management (reduce bus-factor)

**Validate**
- [ ] Every change re-enters the full pipeline — nothing skips the gates because it's "small"

**Best default:** Automated dependency hygiene + explicit tech-debt allocation + **Strangler Fig** for legacy modernization (never a big-bang rewrite).

**Loop-back:** Validated learnings feed straight back into **Phase 1**. The cycle spirals forward.

---

## CROSS-CUTTING CONCERNS
*Woven through every phase — never bolted on at the end.*

- [ ] **Security (DevSecOps):** threat modeling (design) → SAST (code) → SCA (CI) → DAST/pentest (QA) → signing (build) → secrets mgmt (deploy) → runtime protection (ops). "Shift-left, shield-right."
- [ ] **Version & change management:** code, config, infra, docs all versioned and traceable
- [ ] **Documentation:** requirements, ADRs, APIs, runbooks, onboarding — kept current
- [ ] **Observability/telemetry:** instrumented from the first line of code
- [ ] **Compliance & governance:** audit trails, data privacy (GDPR / CCPA), regulatory frameworks where applicable (SOC 2, ISO 27001, PCI-DSS, HIPAA)
- [ ] **Delivery methodology:** explicit choice (Scrum / Kanban / Shape Up) + ceremonies (planning, standup, review, retro), estimation, backlog refinement
- [ ] **Responsible disclosure:** security.txt / bug-bounty / vulnerability-reporting path
- [ ] **Accessibility (a11y)** and **internationalization (i18n/l10n)**
- [ ] **Privacy-by-design / data governance**
- [ ] **Data lifecycle:** classification, retention, archival, deletion / right-to-erasure (GDPR)
- [ ] **Open-source license compliance** (obligations tracked alongside the SBOM)
- [ ] **Cost & sustainability:** FinOps / GreenOps awareness
- [ ] **Quality gates & automation:** automated checks at every boundary — nothing unverified advances
- [ ] **Team Topologies:** org structure designed alongside system structure

---

## FRONTEND / CLIENT-SIDE TRACK
*The phases above lean backend/infra. Client-facing products carry this parallel set of concerns across design, build, and test.*

- [ ] **Rendering strategy** — CSR vs SSR vs SSG vs ISR (and when each fits)
- [ ] **Design system / component library** — shared, versioned UI components + design tokens
- [ ] **State management** — local, global, and server-cache/async state
- [ ] **Responsive & adaptive design** across breakpoints
- [ ] **Web performance budgets** — Core Web Vitals (LCP / INP / CLS), bundle size, code-splitting, lazy loading, image optimization
- [ ] **Client-side caching** — HTTP caching, service workers
- [ ] **Progressive enhancement / graceful degradation**
- [ ] **Offline-first / PWA** (where relevant)
- [ ] **SEO** — semantic markup, metadata, sitemaps, structured data (public web)
- [ ] **Web security specifics** — CSP, CORS, secure cookies, XSS / CSRF defenses
- [ ] **Accessibility (WCAG)** built in, not retrofitted (e.g. axe)
- [ ] **i18n / l10n** — locale, timezone, currency, RTL support

---

## DOMAIN-SPECIFIC TRACKS (apply only if relevant)
*Not every project needs these — include the ones that match your product.*

**Data & analytics engineering** (if data-intensive)
- [ ] Ingestion pipelines (ETL / ELT), batch vs **stream** processing
- [ ] Data warehouse / lake / lakehouse
- [ ] **Data quality & validation**, schema contracts
- [ ] Data catalog, **lineage**, governance
- [ ] Orchestration (workflow schedulers)

**Machine learning / MLOps** (if ML-powered)
- [ ] Data labeling & versioning, feature store
- [ ] Experiment tracking, **model registry**
- [ ] Training & evaluation pipelines, reproducibility
- [ ] Deployment + **model/data drift monitoring**, retraining triggers
- [ ] Responsible AI — bias, explainability, governance

**Mobile** (if native/hybrid app)
- [ ] Code signing + **app-store submission / review**
- [ ] Device / OS fragmentation testing
- [ ] **Offline-first** sync, push notifications
- [ ] Over-the-air (OTA) updates, crash reporting
- [ ] Battery / network / storage efficiency

---

## THE SCOREBOARD — Is any of this actually working?

**DORA metrics** (the evidence-based measure of delivery performance):
- [ ] **Deployment frequency** — how often you ship
- [ ] **Lead time for changes** — commit → production
- [ ] **Change failure rate** — % of deploys causing failure
- [ ] **Mean time to restore (MTTR)** — recovery speed

**SPACE framework** — for developer productivity & wellbeing (Satisfaction, Performance, Activity, Communication, Efficiency).

> Instrument DORA as your scoreboard. It tells you, objectively, whether all the practices above are paying off. Elite teams aren't elite because they own the most tools — they're elite on these four numbers.

---

## APPENDIX — DESIGN PRINCIPLES
*Heuristics for the one enemy: complexity. Not laws — rules of thumb that regularly conflict, where judgment is knowing which to bend. The goal under all of them: code that's easy to understand and safe to change.*

**Core trio (universal)**
- [ ] **DRY** — Don't Repeat Yourself; every piece of knowledge lives in one place
- [ ] **KISS** — Keep It Simple; simplest solution that works
- [ ] **YAGNI** — You Aren't Gonna Need It; don't build for imagined futures

**SOLID (object-oriented)**
- [ ] **S**ingle Responsibility — one reason to change
- [ ] **O**pen/Closed — open to extension, closed to modification
- [ ] **L**iskov Substitution — subtypes usable wherever the parent is
- [ ] **I**nterface Segregation — many small interfaces over one fat one
- [ ] **D**ependency Inversion — depend on abstractions, not details

**Structural / relationship**
- [ ] Separation of Concerns
- [ ] **Low Coupling** (loosely connected parts) — *the deepest pair*
- [ ] **High Cohesion** (related things grouped together)
- [ ] Encapsulation / Information Hiding
- [ ] Abstraction
- [ ] Composition over Inheritance
- [ ] Law of Demeter (talk only to immediate collaborators)

**Behavioral / pragmatic**
- [ ] Principle of Least Astonishment (behave as expected)
- [ ] Fail Fast (surface errors immediately)
- [ ] Single Source of Truth (DRY for data/state)
- [ ] Convention over Configuration
- [ ] Principle of Least Privilege (minimal necessary access)

**Advanced — GRASP (responsibility assignment)**
- [ ] Information Expert, Creator, Controller, Low Coupling, High Cohesion, Polymorphism, Pure Fabrication, Indirection, Protected Variations

> **The trap:** treating any one as absolute. DRY can create harmful coupling; YAGNI conflicts with Open/Closed. Balance them against how likely change actually is — principles serve the goal, they aren't the goal.

---

## APPENDIX — DESIGN PATTERNS
*Reusable, named solutions to recurring problems. Principles tell you* why *and* what to aim for*; patterns are battle-tested* how*s. Use them as vocabulary, not as a checklist to force into code.*

**Gang of Four — Creational** (how objects get made)
- [ ] Factory Method / Abstract Factory · Builder · Prototype · Singleton

**Gang of Four — Structural** (how objects compose)
- [ ] Adapter · Decorator · Facade · Proxy · Composite · Bridge · Flyweight

**Gang of Four — Behavioral** (how objects collaborate)
- [ ] Strategy · Observer · Command · State · Iterator · Template Method · Chain of Responsibility · Mediator · Visitor · Memento

**Enterprise / application patterns**
- [ ] Repository · Unit of Work · Dependency Injection · MVC / MVVM · DTO · Service Layer

**Distributed / cloud patterns**
- [ ] Circuit Breaker · Retry with backoff · Saga · CQRS · Event Sourcing · Sidecar · API Gateway · Bulkhead · Outbox

> **The trap (again):** patterns are a vocabulary for problems you *have*, not a goal. Forcing patterns onto simple code is a classic over-engineering smell — the inverse of YAGNI.

---

## THE BIG SHIFT (beginner → advanced)
The beginner runs these phases **once, sequentially.**
The advanced org runs them **continuously and in parallel** — code being written, tested, integrated, deployed, and operated *simultaneously*, many times a day, with automated gates ensuring safety and a tight feedback loop from production back to planning.

That continuous, gated, looping machine — not the linear list — **is** what "end to end" means at scale.
