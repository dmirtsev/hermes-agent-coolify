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


def replace_once(path: Path, old: str, new: str, name: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one {name} anchor in {path}, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


for required in (TRANSPORT, LOOP, STREAM_HELPERS, AUXILIARY, API_SERVER):
    if not required.is_file():
        raise SystemExit(f"required pinned Hermes source is missing: {required}")

if "hermes_openrouter_accounting" in API_SERVER.read_text(encoding="utf-8"):
    raise SystemExit("OpenRouter accounting patch is already present")


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
                    record_openrouter_response(agent, response)
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

logger = logging.getLogger(__name__)
""",
    "API server accounting import",
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
    """            with openrouter_accounting_scope(agent):
                result = agent.run_conversation(
                    user_message=user_message,
                    conversation_history=conversation_history,
                    task_id=effective_task_id,
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
                "_hermes_openrouter_accounting": build_openrouter_accounting(
                    agent, request_id="hermesacct_" + uuid.uuid4().hex
                ),
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
