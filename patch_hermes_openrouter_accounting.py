#!/usr/bin/env python3
"""Apply the OpenRouter accounting seam to the digest-pinned Hermes source.

Every replacement is anchored to the reviewed v0.16.0 source.  A source drift
or a second application aborts the image build instead of silently producing
an unaccounted runtime.
"""

from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))
TRANSPORT = ROOT / "agent/transports/chat_completions.py"
LOOP = ROOT / "agent/conversation_loop.py"
STREAM_HELPERS = ROOT / "agent/chat_completion_helpers.py"
AUXILIARY = ROOT / "agent/auxiliary_client.py"
API_SERVER = ROOT / "gateway/platforms/api_server.py"
AGENT_INIT = ROOT / "agent/agent_init.py"
PLUGINS = ROOT / "hermes_cli/plugins.py"


def replace_once(path: Path, old: str, new: str, name: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one {name} anchor in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


for required in (TRANSPORT, LOOP, STREAM_HELPERS, AUXILIARY, API_SERVER, AGENT_INIT, PLUGINS):
    if not required.is_file():
        raise SystemExit(f"required pinned Hermes source is missing: {required}")

if "hermes_openrouter_accounting" in API_SERVER.read_text(encoding="utf-8"):
    raise SystemExit("OpenRouter accounting patch is already present")

replace_once(
    AGENT_INIT,
    '''    try:
        _ctx_cfg = _agent_cfg.get("context", {}) if isinstance(_agent_cfg, dict) else {}
        _engine_name = _ctx_cfg.get("engine", "compressor") or "compressor"
    except Exception:
        pass

    if _engine_name != "compressor":
''',
    '''    try:
        _ctx_cfg = _agent_cfg.get("context", {}) if isinstance(_agent_cfg, dict) else {}
        _engine_name = _ctx_cfg.get("engine", "compressor") or "compressor"
    except Exception:
        pass
    try:
        from agent.versioned_methods import strict_context_active
        if strict_context_active():
            _engine_name = "compressor"
    except Exception:
        pass

    if _engine_name != "compressor":
''',
    "strict context engine isolation",
)

replace_once(
    PLUGINS,
    '''def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    """Invoke a lifecycle hook on all loaded plugins.

    Returns a list of non-``None`` return values from plugin callbacks.
    """
    return get_plugin_manager().invoke_hook(hook_name, **kwargs)
''',
    '''def invoke_hook(hook_name: str, **kwargs: Any) -> List[Any]:
    """Invoke a lifecycle hook on all loaded plugins."""
    try:
        from agent.versioned_methods import strict_context_active
        if strict_context_active():
            return []
    except Exception:
        pass
    return get_plugin_manager().invoke_hook(hook_name, **kwargs)
''',
    "strict context plugin hook isolation",
)

replace_once(
    PLUGINS,
    '''def invoke_middleware(kind: str, **kwargs: Any) -> List[Any]:
    """Invoke registered middleware callbacks.

    Returns a list of non-``None`` return values from middleware callbacks.
    """
    return get_plugin_manager().invoke_middleware(kind, **kwargs)
''',
    '''def invoke_middleware(kind: str, **kwargs: Any) -> List[Any]:
    """Invoke registered middleware callbacks."""
    try:
        from agent.versioned_methods import strict_context_active
        if strict_context_active():
            return []
    except Exception:
        pass
    return get_plugin_manager().invoke_middleware(kind, **kwargs)
''',
    "strict context plugin middleware isolation",
)


replace_once(
    TRANSPORT,
    "from agent.transports.types import NormalizedResponse, ToolCall, Usage\n",
    "from agent.transports.types import NormalizedResponse, ToolCall, Usage\n"
    "from agent.openrouter_accounting import extract_openrouter_generation\n",
    "transport accounting import",
)

replace_once(
    TRANSPORT,
    """        if rd:
            provider_data["reasoning_details"] = rd

        return NormalizedResponse(
""",
    """        if rd:
            provider_data["reasoning_details"] = rd

        # Retain upstream identity/usage evidence in the normalized response.
        # Financial aggregation is enabled only when the server-side agent is
        # configured for OpenRouter; this transport never sees or trusts the
        # cosmetic model from the incoming gateway request.
        provider_data["upstream_generation"] = extract_openrouter_generation(response)

        return NormalizedResponse(
""",
    "normalized upstream generation evidence",
)

replace_once(
    TRANSPORT,
    """        # Request overrides (user config)
        overrides = params.get("request_overrides")
        if overrides:
            for k, v in overrides.items():
                if k == "extra_body" and isinstance(v, dict):
                    extra_body.update(v)
                else:
                    api_kwargs[k] = v

        if extra_body:
""",
    """        # Request overrides (user config)
        overrides = params.get("request_overrides")
        if overrides:
            for k, v in overrides.items():
                if k == "extra_body" and isinstance(v, dict):
                    extra_body.update(v)
                elif k == "extra_headers" and isinstance(v, dict):
                    merged_headers = dict(api_kwargs.get("extra_headers") or {})
                    merged_headers.update(v)
                    api_kwargs[k] = merged_headers
                else:
                    api_kwargs[k] = v

        # OpenRouter exposes the selected upstream provider only when routing
        # metadata is requested. Preserve valid profile/caller headers, then
        # enforce the backend-owned evidence header after request overrides.
        if str(getattr(profile, "name", "") or "").strip().lower() == "openrouter":
            raw_headers = api_kwargs.get("extra_headers")
            accounting_headers = dict(raw_headers) if isinstance(raw_headers, dict) else {}
            accounting_headers["X-OpenRouter-Metadata"] = "enabled"
            api_kwargs["extra_headers"] = accounting_headers

        if extra_body:
""",
    "OpenRouter routing metadata header",
)


replace_once(
    LOOP,
    "from agent.usage_pricing import estimate_usage_cost, normalize_usage\n",
    "from agent.usage_pricing import estimate_usage_cost, normalize_usage\n"
    "from agent.openrouter_accounting import (\n"
    "    record_openrouter_response,\n"
    "    record_openrouter_unresolved_attempt,\n"
    ")\n",
    "conversation accounting import",
)

replace_once(
    LOOP,
    """            try:
                agent._reset_stream_delivery_tracking()
""",
    """            _accounting_attempt_dispatched = False
            _accounting_response_recorded = False
            try:
                agent._reset_stream_delivery_tracking()
""",
    "per-attempt accounting state",
)

replace_once(
    LOOP,
    """                def _perform_api_call(next_api_kwargs):
                    if _use_streaming:
""",
    """                def _perform_api_call(next_api_kwargs):
                    nonlocal _accounting_attempt_dispatched
                    _accounting_attempt_dispatched = True
                    if _use_streaming:
""",
    "provider dispatch accounting marker",
)

replace_once(
    LOOP,
    """                response = run_llm_execution_middleware(
                    api_kwargs,
                    _perform_api_call,
                    original_request=_original_api_kwargs,
                    task_id=effective_task_id,
                    turn_id=turn_id,
                    api_request_id=api_request_id,
                    session_id=agent.session_id or "",
                    platform=agent.platform or "",
                    model=agent.model,
                    provider=agent.provider,
                    base_url=agent.base_url,
                    api_mode=agent.api_mode,
                    api_call_count=api_call_count,
                    middleware_trace=list(_llm_middleware_trace),
                )
""" + "                \n" + """                api_duration = time.time() - api_start_time
""",
    """                response = run_llm_execution_middleware(
                    api_kwargs,
                    _perform_api_call,
                    original_request=_original_api_kwargs,
                    task_id=effective_task_id,
                    turn_id=turn_id,
                    api_request_id=api_request_id,
                    session_id=agent.session_id or "",
                    platform=agent.platform or "",
                    model=agent.model,
                    provider=agent.provider,
                    base_url=agent.base_url,
                    api_mode=agent.api_mode,
                    api_call_count=api_call_count,
                    middleware_trace=list(_llm_middleware_trace),
                )

                # Record before response validation: an invalid response that
                # Hermes retries can still represent an OpenRouter-billed
                # generation.  Dispatched exceptions are represented by an
                # unresolved record so a later success cannot hide them.
                if not getattr(response, "_hermes_accounting_recorded", False):
                    # A durable write failure raises InterruptedError. Mark the
                    # provider response as seen before writing so the interrupt
                    # path cannot manufacture a second evidence event or retry.
                    _accounting_response_recorded = True
                    record_openrouter_response(agent, response)
                else:
                    _accounting_response_recorded = True

                api_duration = time.time() - api_start_time
""",
    "per-attempt accounting record",
)

replace_once(
    LOOP,
    """            except InterruptedError:
                if thinking_spinner:
""",
    """            except InterruptedError:
                if _accounting_attempt_dispatched and not _accounting_response_recorded:
                    record_openrouter_unresolved_attempt(agent, "interrupted_provider_attempt")
                if thinking_spinner:
""",
    "interrupted accounting attempt",
)

replace_once(
    LOOP,
    """            except Exception as api_error:
                # Stop spinner silently — retry status is buffered and
""",
    """            except Exception as api_error:
                if _accounting_attempt_dispatched and not _accounting_response_recorded:
                    record_openrouter_unresolved_attempt(agent, "provider_attempt_failed")
                # Stop spinner silently — retry status is buffered and
""",
    "failed accounting attempt",
)


replace_once(
    STREAM_HELPERS,
    "from agent.error_classifier import FailoverReason\n",
    "from agent.error_classifier import FailoverReason\n"
    "from agent.openrouter_accounting import (\n"
    "    record_openrouter_response,\n"
    "    record_openrouter_unresolved_attempt,\n"
    ")\n",
    "stream accounting import",
)

replace_once(
    STREAM_HELPERS,
    """        stream = request_client.chat.completions.create(**stream_kwargs)

        # Capture rate limit headers from the initial HTTP response.
""",
    """        stream = request_client.chat.completions.create(**stream_kwargs)

        # OpenRouter also exposes the authoritative generation id as an HTTP
        # response header.  Keep it because the final usage-only SSE chunk may
        # omit choices and some SDK versions discard unknown JSON fields.
        stream_response = getattr(stream, "response", None)
        stream_headers = getattr(stream_response, "headers", None)
        generation_id = None
        if stream_headers is not None:
            try:
                generation_id = stream_headers.get("X-Generation-Id") or stream_headers.get("x-generation-id")
            except Exception:
                generation_id = None
        openrouter_metadata = None
        executed_provider = None

        # Capture rate limit headers from the initial HTTP response.
""",
    "stream generation header capture",
)

replace_once(
    STREAM_HELPERS,
    """        for chunk in stream:
            last_chunk_time["t"] = time.time()
            agent._touch_activity("receiving stream response")

            # Update per-attempt diagnostic counters.  Best-effort —
""",
    """        for chunk in stream:
            last_chunk_time["t"] = time.time()
            agent._touch_activity("receiving stream response")

            chunk_id = getattr(chunk, "id", None)
            if isinstance(chunk_id, str) and chunk_id.strip():
                generation_id = chunk_id.strip()
            chunk_extra = getattr(chunk, "model_extra", None) or {}
            chunk_metadata = getattr(chunk, "openrouter_metadata", None)
            if chunk_metadata is None and isinstance(chunk_extra, dict):
                chunk_metadata = chunk_extra.get("openrouter_metadata")
            if chunk_metadata is not None:
                openrouter_metadata = chunk_metadata
            chunk_provider = getattr(chunk, "provider", None)
            if chunk_provider is None and isinstance(chunk_extra, dict):
                chunk_provider = chunk_extra.get("provider")
            if isinstance(chunk_provider, str) and chunk_provider.strip():
                executed_provider = chunk_provider.strip()

            # Update per-attempt diagnostic counters.  Best-effort —
""",
    "stream chunk identity capture",
)

replace_once(
    STREAM_HELPERS,
    """        return SimpleNamespace(
            id="stream-" + str(uuid.uuid4()),
            model=model_name,
            choices=[mock_choice],
            usage=usage_obj,
        )
""",
    """        _stream_response = SimpleNamespace(
            id=generation_id or ("stream-hermes-" + str(uuid.uuid4())),
            model=model_name,
            provider=executed_provider,
            openrouter_metadata=openrouter_metadata,
            choices=[mock_choice],
            usage=usage_obj,
        )
        record_openrouter_response(agent, _stream_response)
        _stream_response._hermes_accounting_recorded = True
        return _stream_response
""",
    "stream reconstructed response identity",
)

replace_once(
    STREAM_HELPERS,
    """                except Exception as e:
                    # If the main poll loop force-closed this request because
""",
    """                except Exception as e:
                    # A hidden streaming retry may follow a billable partial
                    # generation whose final usage never reached Hermes.
                    record_openrouter_unresolved_attempt(
                        agent, "stream_provider_attempt_failed"
                    )
                    # If the main poll loop force-closed this request because
""",
    "hidden stream retry accounting",
)

replace_once(
    STREAM_HELPERS,
    """            return SimpleNamespace(
                id=PARTIAL_STREAM_STUB_ID,
                model=getattr(agent, "model", "unknown"),
                choices=[SimpleNamespace(
                    index=0, message=_stub_msg, finish_reason=_stub_finish_reason,
                )],
                usage=None,
                _dropped_tool_names=_partial_names or None,
            )
""",
    """            _partial_stub = SimpleNamespace(
                id=PARTIAL_STREAM_STUB_ID,
                model=getattr(agent, "model", "unknown"),
                choices=[SimpleNamespace(
                    index=0, message=_stub_msg, finish_reason=_stub_finish_reason,
                )],
                usage=None,
                _dropped_tool_names=_partial_names or None,
            )
            _partial_stub._hermes_accounting_recorded = True
            return _partial_stub
""",
    "partial stream accounting marker",
)

replace_once(
    STREAM_HELPERS,
    """            if summary_extra_body:
                summary_kwargs["extra_body"] = summary_extra_body

            if agent.api_mode == "anthropic_messages":
""",
    """            if summary_extra_body:
                summary_kwargs["extra_body"] = summary_extra_body
            if (
                (agent.provider or "").strip().lower() == "openrouter"
                or agent._is_openrouter_url()
            ):
                summary_kwargs["extra_headers"] = {
                    "X-OpenRouter-Metadata": "enabled"
                }

            if agent.api_mode == "anthropic_messages":
""",
    "summary routing metadata",
)

replace_once(
    STREAM_HELPERS,
    """                summary_response = agent._ensure_primary_openai_client(reason="iteration_limit_summary").chat.completions.create(**summary_kwargs)
                _summary_result = agent._get_transport().normalize_response(summary_response)
""",
    """                summary_response = agent._ensure_primary_openai_client(reason="iteration_limit_summary").chat.completions.create(**summary_kwargs)
                record_openrouter_response(agent, summary_response)
                _summary_result = agent._get_transport().normalize_response(summary_response)
""",
    "iteration summary accounting",
)

replace_once(
    STREAM_HELPERS,
    """                if summary_extra_body:
                    summary_kwargs["extra_body"] = summary_extra_body

                summary_response = agent._ensure_primary_openai_client(reason="iteration_limit_summary_retry").chat.completions.create(**summary_kwargs)
                _retry_result = agent._get_transport().normalize_response(summary_response)
""",
    """                if summary_extra_body:
                    summary_kwargs["extra_body"] = summary_extra_body
                if (
                    (agent.provider or "").strip().lower() == "openrouter"
                    or agent._is_openrouter_url()
                ):
                    summary_kwargs["extra_headers"] = {
                        "X-OpenRouter-Metadata": "enabled"
                    }

                summary_response = agent._ensure_primary_openai_client(reason="iteration_limit_summary_retry").chat.completions.create(**summary_kwargs)
                record_openrouter_response(agent, summary_response)
                _retry_result = agent._get_transport().normalize_response(summary_response)
""",
    "iteration summary retry accounting",
)

replace_once(
    STREAM_HELPERS,
    """    except Exception as e:
        logger.warning(f"Failed to get summary response: {e}")
""",
    """    except Exception as e:
        record_openrouter_unresolved_attempt(agent, "iteration_summary_failed")
        logger.warning(f"Failed to get summary response: {e}")
""",
    "failed iteration summary accounting",
)


replace_once(
    AUXILIARY,
    "import json\n",
    "import json\n"
    "from agent.openrouter_accounting import (\n"
    "    record_current_openrouter_response,\n"
    "    record_current_openrouter_unresolved_attempt,\n"
    ")\n",
    "auxiliary accounting import",
)

replace_once(
    AUXILIARY,
    """    if merged_extra:
        kwargs["extra_body"] = merged_extra

    return kwargs


def _validate_llm_response(response: Any, task: str = None) -> Any:
""",
    """    if merged_extra:
        kwargs["extra_body"] = merged_extra

    # Auxiliary clients own retry/fallback chains. Until every dispatch has
    # durable generation evidence, any request-owned auxiliary call prevents
    # a fully reconciled debit instead of under-reporting OpenRouter spend.
    record_current_openrouter_unresolved_attempt(
        "auxiliary_path_not_fully_instrumented"
    )
    return kwargs


def _validate_llm_response(response: Any, task: str = None) -> Any:
""",
    "auxiliary fail-closed dispatch marker",
)

replace_once(
    AUXILIARY,
    """        ) from exc
    return response


def call_llm(
""",
    """        ) from exc
    record_current_openrouter_response(response)
    return response


def call_llm(
""",
    "auxiliary successful response evidence",
)


replace_once(
    API_SERVER,
    """from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    is_network_accessible,
)

logger = logging.getLogger(__name__)
""",
    """from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    is_network_accessible,
)
from agent.openrouter_accounting import (
    accounting_for_request,
    build_openrouter_accounting,
    openrouter_accounting_scope,
)
from agent.durable_accounting import (
    JournalError as DurableJournalError,
    RequestConflictError as DurableRequestConflictError,
    RequestInFlightError as DurableRequestInFlightError,
    RequestKeyError as DurableRequestKeyError,
    RequestNotFoundError as DurableRequestNotFoundError,
    RequestUnresolvedError as DurableRequestUnresolvedError,
    begin_request as durable_begin_request,
    complete_request as durable_complete_request,
    durable_agent_request_scope,
    fail_request as durable_fail_request,
    get_request_view as durable_get_request_view,
    internal_auth_token as durable_internal_auth_token,
    internal_authorized as durable_internal_authorized,
    reconcile_request as durable_reconcile_request,
    request_payload_sha256 as durable_payload_sha256,
    seal_not_dispatched as durable_seal_not_dispatched,
)
from agent.design_completion import handle_design_completion
from agent.versioned_methods import (
    VersionedMethodContextError,
    prepare_versioned_method_context,
    strict_context_scope,
)
from agent.knowledge_policy import (
    KnowledgePolicyError,
    MODEL_ONLY,
    prepare_knowledge_policy,
)

logger = logging.getLogger(__name__)
""",
    "API server accounting import",
)

replace_once(
    API_SERVER,
    '''        gateway_session_key: Optional[str] = None,
    ) -> Any:
''',
    '''        gateway_session_key: Optional[str] = None,
        strict_context_only: bool = False,
        knowledge_tools_disabled: bool = False,
    ) -> Any:
''',
    "strict context agent creation argument",
)

replace_once(
    API_SERVER,
    '''        enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))

        max_iterations = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))
''',
    '''        enabled_toolsets = sorted(_get_platform_tools(user_config, "api_server"))

        max_iterations = int(os.getenv("HERMES_MAX_ITERATIONS", "90"))
        if strict_context_only or knowledge_tools_disabled:
            # Exact-method and caller-denied readings execute without built-in
            # or MCP tools. A globally registered knowledge MCP server cannot
            # override the request-scoped deny.
            enabled_toolsets = []
            max_iterations = 1
''',
    "strict context tool isolation",
)

replace_once(
    API_SERVER,
    '''            session_db=self._ensure_session_db(),
            fallback_model=fallback_model,
            reasoning_config=reasoning_config,
            gateway_session_key=gateway_session_key,
''',
    '''            session_db=None if strict_context_only else self._ensure_session_db(),
            fallback_model=fallback_model,
            reasoning_config=reasoning_config,
            gateway_session_key=None if strict_context_only else gateway_session_key,
            skip_context_files=strict_context_only,
            skip_memory=strict_context_only,
''',
    "strict context memory isolation",
)

replace_once(
    API_SERVER,
    '''        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(_openai_error("Invalid JSON in request body"), status=400)

        messages = body.get("messages")
''',
    '''        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(_openai_error("Invalid JSON in request body"), status=400)

        try:
            knowledge_policy_guard = prepare_knowledge_policy(
                body.get("tp_knowledge_policy") if isinstance(body, dict) else None
            )
        except KnowledgePolicyError as exc:
            return web.json_response(
                _openai_error(str(exc), code="invalid_tp_knowledge_policy"),
                status=400,
            )

        try:
            # An explicit model_only deny wins over any contradictory reading
            # package or allowed knowledge-base list supplied in the payload.
            reading_context = (
                None
                if knowledge_policy_guard is not None
                and knowledge_policy_guard.mode == MODEL_ONLY
                else body.get("tp_reading_context")
                if isinstance(body, dict)
                else None
            )
            versioned_method_guard = prepare_versioned_method_context(reading_context)
        except VersionedMethodContextError as exc:
            return web.json_response(
                _openai_error(str(exc), code="invalid_tp_reading_context"),
                status=400,
            )

        messages = body.get("messages")
''',
    "versioned method request guard",
)

replace_once(
    API_SERVER,
    '''        stream = _coerce_request_bool(body.get("stream"), default=False)

        # Extract system message (becomes ephemeral system prompt layered ON TOP of core)
''',
    '''        stream = _coerce_request_bool(body.get("stream"), default=False)
        if stream and versioned_method_guard is not None:
            return web.json_response(
                _openai_error(
                    "Versioned astrology readings require a non-streaming evidence receipt",
                    code="tp_reading_stream_unsupported",
                ),
                status=400,
            )

        # Extract system message (becomes ephemeral system prompt layered ON TOP of core)
''',
    "versioned method non-streaming boundary",
)

replace_once(
    API_SERVER,
    '''                conversation_messages.append({"role": role, "content": content})

        # Extract the last user message as the primary input
''',
    '''                conversation_messages.append({"role": role, "content": content})

        if versioned_method_guard is not None:
            # Caller system/history content is not an authorized knowledge
            # source for an exact-method execution.
            system_prompt = versioned_method_guard.prompt

        # Extract the last user message as the primary input
''',
    "versioned method prompt injection",
)

replace_once(
    API_SERVER,
    '''        if conversation_messages:
            user_message = conversation_messages[-1].get("content", "")
            history = conversation_messages[:-1]

        if not _content_has_visible_payload(user_message):
''',
    '''        if conversation_messages:
            user_message = conversation_messages[-1].get("content", "")
            history = conversation_messages[:-1]
        if versioned_method_guard is not None:
            history = []

        if not _content_has_visible_payload(user_message):
''',
    "strict context request history isolation",
)

replace_once(
    API_SERVER,
    '''        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        # Allow caller to continue an existing session by passing X-Hermes-Session-Id.
''',
    '''        gateway_session_key, key_err = self._parse_session_key_header(request)
        if key_err is not None:
            return key_err

        if versioned_method_guard is not None and (
            gateway_session_key or request.headers.get("X-Hermes-Session-Id", "").strip()
        ):
            return web.json_response(
                _openai_error(
                    "Versioned astrology readings forbid shared session and memory headers",
                    code="tp_reading_shared_context_forbidden",
                ),
                status=400,
            )

        # Allow caller to continue an existing session by passing X-Hermes-Session-Id.
''',
    "strict context session header rejection",
)

replace_once(
    API_SERVER,
    '''        provided_session_id = request.headers.get("X-Hermes-Session-Id", "").strip()
        if provided_session_id:
''',
    '''        provided_session_id = request.headers.get("X-Hermes-Session-Id", "").strip()
        if versioned_method_guard is not None:
            session_id = versioned_method_guard.isolated_session_id
        elif provided_session_id:
''',
    "strict context isolated session identity",
)

replace_once(
    API_SERVER,
    '''    async def _handle_chat_completions(self, request: "web.Request") -> "web.Response":
''',
    '''    async def _handle_design_completions(self, request: "web.Request") -> "web.Response":
        return await handle_design_completion(self, request, web)

    async def _handle_chat_completions(self, request: "web.Request") -> "web.Response":
''',
    "bounded design completion handler",
)

replace_once(
    API_SERVER,
    '''    async def _handle_chat_completions(self, request: "web.Request") -> "web.Response":
''',
    '''    def _durable_accounting_auth_error(self, request: "web.Request"):
        if durable_internal_auth_token(self._api_key) is None:
            return web.json_response(
                _openai_error("Accounting endpoint is not configured", err_type="server_error"),
                status=503,
            )
        if not durable_internal_authorized(request.headers.get("Authorization"), self._api_key):
            return web.json_response(_openai_error("Unauthorized"), status=401)
        return None

    async def _handle_internal_accounting_get(self, request: "web.Request") -> "web.Response":
        auth_error = self._durable_accounting_auth_error(request)
        if auth_error:
            return auth_error
        try:
            value = durable_get_request_view(request.match_info["request_key"])
            return web.json_response(value)
        except DurableRequestKeyError:
            return web.json_response(_openai_error("Invalid request key"), status=400)
        except DurableRequestNotFoundError:
            return web.json_response(_openai_error("Accounting request not found"), status=404)
        except (DurableJournalError, sqlite3.Error, OSError):
            logger.exception("Durable accounting read failed")
            return web.json_response(
                _openai_error("Accounting journal unavailable", err_type="server_error"),
                status=503,
            )

    async def _handle_internal_accounting_reconcile(self, request: "web.Request") -> "web.Response":
        auth_error = self._durable_accounting_auth_error(request)
        if auth_error:
            return auth_error
        try:
            value = await asyncio.to_thread(
                durable_reconcile_request, request.match_info["request_key"]
            )
            return web.json_response(value)
        except DurableRequestKeyError:
            return web.json_response(_openai_error("Invalid request key"), status=400)
        except DurableRequestNotFoundError:
            return web.json_response(_openai_error("Accounting request not found"), status=404)
        except (DurableJournalError, sqlite3.Error, OSError):
            logger.exception("Durable accounting reconciliation failed")
            return web.json_response(
                _openai_error("Accounting reconciliation unavailable", err_type="server_error"),
                status=503,
            )

    async def _handle_internal_accounting_seal_not_dispatched(
        self, request: "web.Request"
    ) -> "web.Response":
        auth_error = self._durable_accounting_auth_error(request)
        if auth_error:
            return auth_error
        try:
            body = await request.json()
            payload_sha256 = body.get("payload_sha256") if isinstance(body, dict) else None
            value = durable_seal_not_dispatched(
                request.match_info["request_key"], payload_sha256
            )
            return web.json_response(value)
        except DurableRequestKeyError:
            return web.json_response(_openai_error("Invalid request key"), status=400)
        except DurableRequestConflictError:
            return web.json_response(
                _openai_error("Idempotency payload conflict"), status=409
            )
        except (DurableJournalError, sqlite3.Error, OSError, ValueError, TypeError):
            logger.exception("Durable not-dispatched seal failed")
            return web.json_response(
                _openai_error("Accounting journal unavailable", err_type="server_error"),
                status=503,
            )

    async def _handle_chat_completions(self, request: "web.Request") -> "web.Response":
''',
    "durable accounting internal handlers",
)

replace_once(
    API_SERVER,
    '''        # Non-streaming: run the agent (with optional Idempotency-Key)
        async def _compute_completion():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
            )

        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            fp = _make_request_fingerprint(body, keys=["model", "messages", "tools", "tool_choice", "stream"])
            try:
                result, usage = await _idem_cache.get_or_set(idempotency_key, fp, _compute_completion)
            except Exception as e:
                logger.error("Error running agent for chat completions: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )
        else:
            try:
                result, usage = await _compute_completion()
            except Exception as e:
                logger.error("Error running agent for chat completions: %s", e, exc_info=True)
                return web.json_response(
                    _openai_error(f"Internal server error: {e}", err_type="server_error"),
                    status=500,
                )
''',
    '''        # Non-streaming idempotent requests are journaled before dispatch.
        # The journal, not process memory, decides whether provider execution is
        # allowed. Streaming remains on Hermes' native path in this V1.
        idempotency_key = request.headers.get("Idempotency-Key")
        payload_sha256 = None
        durable_replayed = False

        async def _compute_completion():
            return await self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
                accounting_request_key=idempotency_key,
                strict_context_only=versioned_method_guard is not None,
                knowledge_tools_disabled=bool(
                    knowledge_policy_guard and knowledge_policy_guard.tools_disabled
                ),
            )

        if idempotency_key:
            payload_sha256 = durable_payload_sha256(body)
            try:
                decision = durable_begin_request(idempotency_key, payload_sha256)
            except DurableRequestKeyError:
                return web.json_response(
                    _openai_error("Invalid Idempotency-Key", code="invalid_idempotency_key"),
                    status=400,
                )
            except DurableRequestConflictError:
                return web.json_response(
                    _openai_error(
                        "Idempotency-Key was already used with a different payload",
                        code="idempotency_payload_conflict",
                    ),
                    status=409,
                )
            except DurableRequestInFlightError:
                return web.json_response(
                    _openai_error("Request is still in flight", code="idempotency_in_flight"),
                    status=409,
                )
            except DurableRequestUnresolvedError:
                return web.json_response(
                    _openai_error(
                        "Previous execution is unresolved; provider dispatch is blocked",
                        code="idempotency_unresolved",
                    ),
                    status=409,
                )
            except (DurableJournalError, sqlite3.Error, OSError):
                logger.exception("Durable accounting claim failed")
                return web.json_response(
                    _openai_error("Accounting journal unavailable", err_type="server_error"),
                    status=503,
                )

            if decision["state"] == "completed":
                result, usage = decision["result"], decision["usage"]
                durable_replayed = True
            else:
                try:
                    result, usage = await _compute_completion()
                    # Store the immutable result and exact accounting before any
                    # successful HTTP response can leave the gateway.
                    durable_complete_request(
                        idempotency_key, payload_sha256, result, usage
                    )
                except Exception:
                    try:
                        durable_fail_request(
                            idempotency_key, payload_sha256, "agent_execution_failed"
                        )
                    except (DurableJournalError, sqlite3.Error, OSError):
                        logger.exception("Durable accounting failure marker failed")
                    logger.exception("Error running idempotent chat completion")
                    return web.json_response(
                        _openai_error("Internal server error", err_type="server_error"),
                        status=500,
                    )
        else:
            try:
                result, usage = await _compute_completion()
            except Exception:
                logger.exception("Error running agent for chat completions")
                return web.json_response(
                    _openai_error("Internal server error", err_type="server_error"),
                    status=500,
                )
''',
    "durable non-streaming idempotency",
)

replace_once(
    API_SERVER,
    '''        idempotency_key = request.headers.get("Idempotency-Key")
        payload_sha256 = None
        durable_replayed = False
''',
    '''        idempotency_key = request.headers.get("Idempotency-Key")
        if (
            versioned_method_guard is not None
            and versioned_method_guard.receipt["request_id"] != idempotency_key
        ):
            return web.json_response(
                _openai_error(
                    "Reading request identity differs from Idempotency-Key",
                    code="tp_reading_request_identity_mismatch",
                ),
                status=409,
            )
        payload_sha256 = None
        durable_replayed = False
''',
    "versioned method request identity",
)

replace_once(
    API_SERVER,
    '''        if gateway_session_key:
            response_headers["X-Hermes-Session-Key"] = gateway_session_key

        # Hard-fail path: no usable assistant text AND a real failure → 5xx
''',
    '''        if gateway_session_key:
            response_headers["X-Hermes-Session-Key"] = gateway_session_key
        if durable_replayed:
            response_headers["X-Hermes-Idempotency-Replayed"] = "true"
        if versioned_method_guard is not None:
            response_headers["X-Hermes-Context-Isolation"] = "strict-v1"
        if knowledge_policy_guard is not None:
            response_headers["X-Hermes-Knowledge-Policy"] = knowledge_policy_guard.mode

        # Hard-fail path: no usable assistant text AND a real failure → 5xx
''',
    "durable replay response header",
)

replace_once(
    API_SERVER,
    '''        gateway_session_key: Optional[str] = None,
    ) -> tuple:
''',
    '''        gateway_session_key: Optional[str] = None,
        accounting_request_key: Optional[str] = None,
        strict_context_only: bool = False,
        knowledge_tools_disabled: bool = False,
    ) -> tuple:
''',
    "durable request key execution argument",
)

replace_once(
    API_SERVER,
    '''            agent = self._create_agent(
                ephemeral_system_prompt=ephemeral_system_prompt,
                session_id=session_id,
                stream_delta_callback=stream_delta_callback,
                tool_progress_callback=tool_progress_callback,
                tool_start_callback=tool_start_callback,
                tool_complete_callback=tool_complete_callback,
                gateway_session_key=gateway_session_key,
            )
            if agent_ref is not None:
''',
    '''            with strict_context_scope(strict_context_only):
                agent = self._create_agent(
                    ephemeral_system_prompt=ephemeral_system_prompt,
                    session_id=session_id,
                    stream_delta_callback=stream_delta_callback,
                    tool_progress_callback=tool_progress_callback,
                    tool_start_callback=tool_start_callback,
                    tool_complete_callback=tool_complete_callback,
                    gateway_session_key=gateway_session_key,
                    strict_context_only=strict_context_only,
                    knowledge_tools_disabled=knowledge_tools_disabled,
                )
            if agent_ref is not None:
''',
    "strict context agent execution boundary",
)

replace_once(
    API_SERVER,
    '''            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
''',
    '''            self._app.router.add_get(
                "/internal/accounting/{request_key}",
                self._handle_internal_accounting_get,
            )
            self._app.router.add_post(
                "/internal/accounting/{request_key}/reconcile",
                self._handle_internal_accounting_reconcile,
            )
            self._app.router.add_post(
                "/internal/accounting/{request_key}/seal-not-dispatched",
                self._handle_internal_accounting_seal_not_dispatched,
            )
            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)
            self._app.router.add_post("/v1/design/completions", self._handle_design_completions)
''',
    "durable accounting internal routes",
)

replace_once(
    API_SERVER,
    """            result = agent.run_conversation(
                user_message=user_message,
                conversation_history=conversation_history,
                task_id=effective_task_id,
            )
            usage = {
""",
    """            with strict_context_scope(strict_context_only), durable_agent_request_scope(agent, accounting_request_key), openrouter_accounting_scope(agent):
                result = agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history,
                    task_id=effective_task_id,
                )
                _durable_openrouter_accounting = build_openrouter_accounting(
                    agent,
                    request_id="hermesacct_" + uuid.uuid4().hex,
                    durable_request_key=accounting_request_key,
                )
            usage = {
""",
    "request accounting scope",
)

replace_once(
    API_SERVER,
    """            usage = {
                "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
            }
""",
    """            usage = {
                "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
                "cache_read_tokens": getattr(agent, "session_cache_read_tokens", 0) or 0,
                "cache_write_tokens": getattr(agent, "session_cache_write_tokens", 0) or 0,
                "reasoning_tokens": getattr(agent, "session_reasoning_tokens", 0) or 0,
                "_hermes_openrouter_accounting": _durable_openrouter_accounting,
            }
""",
    "agent accounting aggregate",
)

replace_once(
    API_SERVER,
    """            err_body["error"]["hermes"] = {
                "completed": completed,
                "partial": is_partial,
                "failed": is_failed,
            }
            response_headers["X-Hermes-Completed"] = "false"
""",
    """            err_body["error"]["hermes"] = {
                "completed": completed,
                "partial": is_partial,
                "failed": is_failed,
            }
            accounting = accounting_for_request(
                usage.get("_hermes_openrouter_accounting"), completion_id
            )
            if accounting is not None:
                err_body["hermes_accounting"] = accounting
            response_headers["X-Hermes-Completed"] = "false"
""",
    "hard-failure accounting response",
)

replace_once(
    API_SERVER,
    """        if is_partial or is_failed or not completed:
            response_data["hermes"] = {
""",
    """        accounting = accounting_for_request(
            usage.get("_hermes_openrouter_accounting"), completion_id
        )
        if accounting is not None:
            response_data["hermes_accounting"] = accounting

        if is_partial or is_failed or not completed:
            response_data["hermes"] = {
""",
    "successful accounting response",
)

replace_once(
    API_SERVER,
    '''        if is_partial or is_failed or not completed:
            response_data["hermes"] = {
''',
    '''        if versioned_method_guard is not None:
            response_data["tp_method_execution"] = versioned_method_guard.receipt
        if knowledge_policy_guard is not None:
            response_data["tp_knowledge_policy"] = knowledge_policy_guard.receipt

        if is_partial or is_failed or not completed:
            response_data["hermes"] = {
''',
    "versioned method execution receipt",
)

replace_once(
    API_SERVER,
    """        # Store the complete response object for future chaining / GET retrieval
        if store:
""",
    """        accounting = accounting_for_request(
            usage.get("_hermes_openrouter_accounting"), response_id
        )
        if accounting is not None:
            response_data["hermes_accounting"] = accounting

        # Store the complete response object for future chaining / GET retrieval
        if store:
""",
    "Responses API accounting response",
)

print("patched pinned Hermes OpenRouter accounting seam")
