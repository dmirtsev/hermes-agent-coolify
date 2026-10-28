#!/usr/bin/env python3
"""Apply last: admission must precede billing journal and every provider call."""
import os
from pathlib import Path

root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))


def replace(path, old, new, label, count=1):
    source = path.read_text()
    if source.count(old) != count:
        raise RuntimeError("Context budget patch boundary: " + label)
    source = source.replace(old, new)
    compile(source, str(path), "exec")
    path.write_text(source)


api = root / "gateway/platforms/api_server.py"
replace(api,
    'MAX_REQUEST_BYTES = 10_000_000  # 10 MB — accommodates long agent conversations with tool calls\n',
    'MAX_REQUEST_BYTES = 16 * 1024 * 1024  # Resource guard; managed admission is in tokens.\n',
    "transport resource guard")
replace(api,
    '                body.get("tp_reading_context") if isinstance(body, dict) else None\n',
    '                body.get("tp_managed_method_context", body.get("tp_reading_context")) if isinstance(body, dict) else None,\n'
    '                full_context=isinstance(body, dict) and body.get("tp_context_budget") is not None,\n',
    "versioned full method admission")
replace(api,
    "from agent.behavior_policy import validate_behavior_policy, install_managed_system_prompt, managed_response_settings\n",
    "from agent.behavior_policy import validate_behavior_policy, install_managed_system_prompt, managed_response_settings\n"
    "from agent.context_budget import (ContextBudgetError, prepare_admission, require_admission, install_context_budget)\n"
    "from agent.durable_accounting import is_exact_completed_request as durable_exact_completed_request\n",
    "imports")
replace(api,
    '        messages = body.get("messages")\n',
    '''        try:
            if body.get("tp_context_budget") is not None and [m.get("role") for m in body.get("messages", [])] != ["system", "user"]:
                raise ContextBudgetError("hermes_context_initial_messages_invalid")
            # The exact journaled completion can be replayed under its original
            # admission receipt even after counters or canonicalization change.
            # begin_request rechecks the digest and can never dispatch it again.
            exact_completed = (body.get("tp_context_budget") is not None and
                not body.get("tp_preflight_only") and
                durable_exact_completed_request(request.headers.get("Idempotency-Key"), body))
            if exact_completed:
                context_admission = body.get("tp_context_admission")
                if not isinstance(context_admission, dict) or context_admission.get("admitted") is not True:
                    raise ContextBudgetError("hermes_context_admission_mismatch")
            else:
                context_admission = prepare_admission(body)
            if body.get("tp_preflight_only"):
                return web.json_response({"admission": context_admission})
            if not exact_completed:
                require_admission(body, context_admission)
        except (ContextBudgetError, OSError, ImportError) as error:
            code = str(error) if isinstance(error, ContextBudgetError) else "hermes_counter_unavailable"
            return web.json_response(_openai_error(code, code=code), status=422)

        messages = body.get("messages")
''', "free preflight and fail-closed admission")
replace(api,
    '                managed_continuation=managed_policy is not None,\n',
    '                managed_continuation=managed_policy is not None,\n'
    '                context_budget_body=body if context_admission is not None else None,\n',
    "execution context")
replace(api,
    '        sourced_text_only: bool = False,\n',
    '        context_budget_body: Optional[dict] = None,\n'
    '        sourced_text_only: bool = False,\n', "execution argument")
replace(api,
    '            install_managed_system_prompt(agent, managed_system_prompt, managed_response_format)\n',
    '            install_managed_system_prompt(agent, managed_system_prompt, managed_response_format)\n'
    '            install_context_budget(agent, context_budget_body or {})\n', "provider guard context")
replace(api,
    '                content = _normalize_chat_content(raw_content)\n',
    '                content = raw_content if context_admission is not None else _normalize_chat_content(raw_content)\n',
    "preserve admitted system text")
replace(api,
    '                    content = _normalize_multimodal_content(raw_content)\n',
    '                    content = raw_content if context_admission is not None else _normalize_multimodal_content(raw_content)\n',
    "preserve admitted user text")
replace(api,
    '            system_prompt = versioned_method_guard.prompt\n',
    '            if context_admission is None:\n'
    '                system_prompt = versioned_method_guard.prompt\n',
    "managed method already present in counted messages")
loop = root / "agent/conversation_loop.py"
replace(loop,
    '            if isinstance(am.get("content"), str):\n'
    '                am["content"] = am["content"].strip()\n',
    '            if isinstance(am.get("content"), str) and getattr(agent, "_tp_context_budget_body", None) is None:\n'
    '                am["content"] = am["content"].strip()\n',
    "preserve byte-exact counted message whitespace")
replace(loop,
    'from agent.usage_pricing import estimate_usage_cost, normalize_usage\n',
    'from agent.usage_pricing import estimate_usage_cost, normalize_usage\n'
    'from agent.context_budget import admit_provider_call, readable_managed_part, managed_completion_action\n', "provider guard import")
replace(loop,
    '                    _accounting_attempt_dispatched = True\n',
    '                    admit_provider_call(agent, next_api_kwargs)\n'
    '                    _accounting_attempt_dispatched = True\n', "final pre-transport admission")
replace(loop,
    '                        if assistant_message is not None and not _trunc_has_tool_calls:\n',
    '''                        if (getattr(agent, "_tp_context_budget_body", None) is not None
                                and assistant_message is not None and not _trunc_has_tool_calls
                                and readable_managed_part(assistant_message.content, getattr(agent, "_tp_managed_json", False))):
                            messages.append(agent._build_assistant_message(assistant_message, finish_reason))
                            agent._cleanup_task_resources(effective_task_id)
                            agent._persist_session(messages, conversation_history)
                            return {"final_response": assistant_message.content, "messages": messages,
                                    "api_calls": api_call_count, "completed": False, "partial": True,
                                    "error": "Response truncated; manual continuation available"}
                        if assistant_message is not None and not _trunc_has_tool_calls:
''', "commit readable length part without replacement")
replace(loop,
    '''                            interim_msg = agent._build_assistant_message(assistant_message, finish_reason)
                            messages.append(interim_msg)
''',
    '''                            interim_msg = agent._build_assistant_message(assistant_message, finish_reason)
                            if getattr(agent, "_tp_context_budget_body", None) is not None and getattr(agent, "_tp_managed_json", False):
                                # Fresh JSON repair uses the full original context;
                                # a malformed candidate is not a conversation part.
                                messages[:] = [dict(item) for item in agent._tp_context_budget_body["messages"] if item["role"] != "system"]
                            else:
                                messages.append(interim_msg)
''', "fresh JSON repair without candidate history")
replace(loop,
    '''                                continue_msg = {
                                    "role": "user",
                                    "content": _continue_content,
                                }
''',
    '''                                if getattr(agent, "_tp_context_budget_body", None) is not None and getattr(agent, "_tp_managed_json", False):
                                    _continue_content = (
                                        "[System: The previous JSON draft was incomplete and was not published. "
                                        "Return one complete valid JSON object for the same original question and supplied facts. "
                                        "Give a coherent useful part that fits this response; the user can request further parts. "
                                        "Do not shorten the overall requested analysis to a summary and do not repeat already published history. "
                                        "Do not continue a malformed JSON prefix. JSON only.]"
                                    )
                                continue_msg = {
                                    "role": "user",
                                    "content": _continue_content,
                                }
''', "repair is distinct from manual content continuation")
replace(loop, '                final_msg = agent._build_assistant_message(assistant_message, finish_reason)\n\n                # Pop thinking-only prefill and empty-response retry', '                final_msg = agent._build_assistant_message(assistant_message, finish_reason)\n\n                _managed_action, _managed_detail = managed_completion_action(agent, final_response)\n                if _managed_action == "fail":\n                    messages[:] = [dict(item) for item in agent._tp_context_budget_body["messages"] if item["role"] != "system"]\n                    agent._cleanup_task_resources(effective_task_id)\n                    agent._persist_session(messages, conversation_history)\n                    return {"final_response": "", "messages": messages, "api_calls": api_call_count,\n                            "completed": False, "failed": True, "error": _managed_detail}\n                if _managed_action == "retry":\n                    messages[:] = [dict(item) for item in agent._tp_context_budget_body["messages"] if item["role"] != "system"]\n                    messages.append({"role": "user", "content": _managed_detail})\n                    agent._session_messages = messages\n                    continue\n\n                # Pop thinking-only prefill and empty-response retry', "semantic managed transit repair before normal publication")
replace(loop, '                        if (getattr(agent, "_tp_context_budget_body", None) is not None\n                                and assistant_message is not None and not _trunc_has_tool_calls\n                                and readable_managed_part(assistant_message.content, getattr(agent, "_tp_managed_json", False))):', '                        _managed_action, _managed_detail = managed_completion_action(agent, assistant_message.content if assistant_message is not None else "")\n                        if _managed_action == "fail":\n                            messages[:] = [dict(item) for item in agent._tp_context_budget_body["messages"] if item["role"] != "system"]\n                            agent._cleanup_task_resources(effective_task_id)\n                            agent._persist_session(messages, conversation_history)\n                            return {"final_response": "", "messages": messages, "api_calls": api_call_count,\n                                    "completed": False, "failed": True, "error": _managed_detail}\n                        if (getattr(agent, "_tp_context_budget_body", None) is not None\n                                and assistant_message is not None and not _trunc_has_tool_calls\n                                and _managed_action == "pass"\n                                and readable_managed_part(assistant_message.content, getattr(agent, "_tp_managed_json", False))):', "semantic managed transit repair before length publication")
replace(loop, '                                continue_msg = {\n                                    "role": "user",\n                                    "content": _continue_content,\n                                }\n', '                                if _managed_action == "retry":\n                                    _continue_content = _managed_detail\n                                continue_msg = {\n                                    "role": "user",\n                                    "content": _continue_content,\n                                }\n', "native semantic feedback in bounded length repair")
helpers = root / "agent/chat_completion_helpers.py"
replace(helpers,
    '    print(f"⚠️  Reached maximum iterations ({agent.max_iterations}). Requesting summary...")\n',
    '    if getattr(agent, "_tp_context_budget_body", None) is not None:\n'
    '        return ""  # No unbudgeted fourth call after the automatic-attempt limit.\n'
    '    print(f"⚠️  Reached maximum iterations ({agent.max_iterations}). Requesting summary...")\n',
    "no fourth summary call")
replace(helpers,
    '                result["response"] = request_client.chat.completions.create(**api_kwargs)\n',
    '                if getattr(agent, "_tp_context_budget_body", None) is not None:\n'
    '                    request_client = request_client.with_options(max_retries=0)\n'
    '                result["response"] = request_client.chat.completions.create(**api_kwargs)\n',
    "no hidden SDK retries")
