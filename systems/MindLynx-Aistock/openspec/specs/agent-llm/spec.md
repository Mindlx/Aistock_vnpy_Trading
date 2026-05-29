## Purpose

The Agent & LLM subsystem provides the AI reasoning layer for stock analysis. It supports two architectures: a single-agent mode that injects all 15 strategy prompts into one LLM call, and a multi-agent mode with staged specialist agents running ReAct loops with tool calling. The LLM layer wraps LiteLLM for multi-provider support with automatic fallback and model-specific parameter adaptation.

## Requirements

### Requirement: The LLM layer SHALL support multiple providers with automatic fallback
The system SHALL use LiteLLM to support multiple LLM providers (OpenAI, Anthropic, Gemini, DeepSeek, Kimi, Ollama, etc.). Providers SHALL be configured via a priority list (`LLM_CHANNELS`). If the primary provider fails, the next in the list SHALL be tried automatically. Each failed attempt SHALL use configurable retry logic with backoff.

#### Scenario: Provider fallback
- **WHEN** the primary LLM provider returns a 5xx error
- **THEN** the system SHALL retry with backoff, then fail over to the next configured provider in the `LLM_CHANNELS` list

#### Scenario: All providers fail
- **WHEN** all configured LLM providers fail
- **THEN** the system SHALL fall back to trend analysis signals (`_apply_trend_fallback`) and log the failure

### Requirement: Agent architecture SHALL support single and multi modes
The system SHALL support two agent architectures selected by `AGENT_ARCH`. In `single` mode: one LLM call with all strategy prompts and context injected into the system prompt. In `multi` mode: a staged pipeline of specialized agents (Technical → Intel → Risk → Specialist → Decision) each running its own ReAct loop with access to tools.

#### Scenario: Single agent mode
- **WHEN** `AGENT_ARCH=single`
- **THEN** `AgentExecutor` SHALL run a single ReAct loop with 4 stages: (1) quote+K-line, (2) technicals+chip, (3) news search, (4) report generation — all driven by one LLM with all 15 strategy prompts in context

#### Scenario: Multi-agent specialist mode
- **WHEN** `AGENT_ARCH=multi` and `AGENT_ORCHESTRATOR_MODE=specialist`
- **THEN** `AgentOrchestrator` SHALL run: TechnicalAgent → IntelAgent → RiskAgent → parallel SkillAgents (one per active strategy, max `AGENT_SKILL_MAX_CONCURRENT=5`) → SkillAggregator → DecisionAgent

### Requirement: Orchestrator SHALL support 4 depth modes
The `AgentOrchestrator` SHALL support 4 modes selectable via `AGENT_ORCHESTRATOR_MODE`: `quick` (Technical → Decision, ~2 LLM calls), `standard` (Technical → Intel → Decision, ~3 calls), `full` (Technical → Intel → Risk → Decision, ~4 calls), `specialist` (Technical → Intel → Risk → N x SkillAgent → Decision, ~5+N calls).

#### Scenario: Quick mode
- **WHEN** `AGENT_ORCHESTRATOR_MODE=quick`
- **THEN** the orchestrator SHALL skip Intel and Risk stages, running only Technical and Decision agents

### Requirement: Agents SHALL share context through AgentContext
All agents in a multi-agent run SHALL share a mutable `AgentContext` dictionary containing: `data` (realtime quotes, daily bars, chip data, trend analysis), `opinions` (each agent's `AgentOpinion`), and `risk_flags` (vetoes and downgrades). Each agent reads from and writes to this shared context.

#### Scenario: RiskAgent veto
- **WHEN** RiskAgent detects a high-risk factor and sets `veto_buy=True` on the context
- **THEN** DecisionAgent SHALL NOT output a "buy" recommendation regardless of other agent opinions

### Requirement: Strategy skills SHALL be loaded from YAML files
The system SHALL load trading strategy definitions from `strategies/*.yaml` files at startup. Each YAML SHALL define: name, display name, description, category, required tools, market regime affinity, and natural-language instructions. The `SkillManager` SHALL cache loaded skills and deep-copy them for thread-safe concurrent access.

#### Scenario: Strategy skill loading
- **WHEN** the system starts with `AGENT_SKILLS=bull_trend,volume_breakout`
- **THEN** it SHALL load only `bull_trend.yaml` and `volume_breakout.yaml` from the strategies directory and inject their instructions into the agent prompt

### Requirement: LLM parameters SHALL adapt to model type
The system SHALL auto-advert generation parameters per model: omit `temperature` for o-series models, use 1.0/0.6 for Kimi K2.6, apply standard temperature for other models. Model-specific configurations SHALL be maintained in `generation_params.py`.

#### Scenario: Model-specific temperature
- **WHEN** the configured model is a Kimi K2.6 model
- **THEN** the system SHALL apply temperature=1.0 for analysis and 0.6 for structured output generation

### Requirement: Tool execution SHALL support parallelism and caching
The agent runner SHALL support parallel tool execution via `ThreadPoolExecutor`. Tool results SHALL be cached by parameter hash to avoid redundant calls during the same analysis cycle. The runner SHALL enforce per-step timeouts with budget protection.

#### Scenario: Parallel tool execution
- **WHEN** an agent requests both `get_realtime_quote` and `get_daily_history` simultaneously
- **THEN** the runner SHALL execute both tool calls in parallel and collect results
