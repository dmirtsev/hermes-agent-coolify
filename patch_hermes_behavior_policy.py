#!/usr/bin/env python3
"""Add the reviewed managed-policy protocol to the pinned production gateway."""

import os
from pathlib import Path


root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))
api = root / "gateway/platforms/api_server.py"
source = api.read_text()


def replace(old, new, label):
    global source
    if source.count(old) != 1:
        raise RuntimeError("Behavior policy patch boundary: " + label)
    source = source.replace(old, new, 1)


replace(
    "from agent.sourced_text import strict_context_execution_mode\n",
    "from agent.sourced_text import strict_context_execution_mode\n"
    "from agent.behavior_policy import validate_behavior_policy, install_managed_system_prompt, managed_response_settings\n",
    "imports",
)
replace(
    '''        messages = body.get("messages")
''',
    '''        try:
            managed_policy = validate_behavior_policy(body, request.headers.get("Idempotency-Key"))
        except ValueError as error:
            return web.json_response(_openai_error(str(error), code=str(error)), status=400)

        messages = body.get("messages")
''',
    "admission",
)
replace(
    '''                sourced_text_only=strict_sourced_text,
            )
''',
    '''                sourced_text_only=strict_sourced_text,
                managed_system_prompt=system_prompt if managed_policy is not None else None,
                managed_response_format=body.get("response_format") if managed_policy is not None else None,
                managed_continuation=managed_policy is not None,
            )
''',
    "execution",
)
replace(
    '''        sourced_text_only: bool = False,
    ) -> tuple:''',
    '''        sourced_text_only: bool = False,
        managed_system_prompt: Optional[str] = None,
        managed_response_format: Optional[dict] = None,
        managed_continuation: bool = False,
    ) -> tuple:''',
    "execution arguments",
)
replace(
    '''        strict_context_only: bool = False,
    ) -> Any:''',
    '''        strict_context_only: bool = False,
        managed_response_format: Optional[dict] = None,
        managed_continuation: bool = False,
    ) -> Any:''',
    "constructor argument",
)
replace(
    '''            max_iterations = 1
''',
    '''            max_iterations = 3 if managed_continuation else 1
''',
    "managed bounded continuation budget",
)
replace(
    '''        fallback_model = GatewayRunner._load_fallback_model()

        agent = AIAgent(''',
    '''        fallback_model = GatewayRunner._load_fallback_model()
        runtime_kwargs = managed_response_settings(runtime_kwargs, managed_response_format)

        agent = AIAgent(''',
    "provider output settings",
)
replace(
    '''                    strict_context_only=strict_context_only,
                )''',
    '''                    strict_context_only=strict_context_only,
                    managed_response_format=managed_response_format,
                    managed_continuation=managed_continuation,
                )''',
    "constructor output format",
)
replace(
    '''            if sourced_text_only:
''',
    '''            install_managed_system_prompt(agent, managed_system_prompt)
            if sourced_text_only:
''',
    "system replacement",
)
replace(
    '''        if durable_replayed:
            response_headers["X-Hermes-Idempotency-Replayed"] = "true"
''',
    '''        if durable_replayed:
            response_headers["X-Hermes-Idempotency-Replayed"] = "true"
        if managed_policy is not None:
            response_headers["X-Hermes-Policy-Id"] = managed_policy["version_id"]
            response_headers["X-Hermes-Policy-Sha256"] = managed_policy["sha256"]
''',
    "receipt",
)
# Managed JSON retries must request a fresh complete object; concatenating
# an incomplete object with a second full object would produce invalid JSON.
loop = root / "agent/conversation_loop.py"
loop_source = loop.read_text()
old_prompt_sig = "def _get_continuation_prompt(is_partial_stub: bool, dropped_tools: Optional[List[str]] = None) -> str:"
new_prompt_sig = "def _get_continuation_prompt(is_partial_stub: bool, dropped_tools: Optional[List[str]] = None, managed_json: bool = False) -> str:"
if loop_source.count(old_prompt_sig) != 1:
    raise RuntimeError("Behavior policy patch boundary: continuation prompt signature")
loop_source = loop_source.replace(old_prompt_sig, new_prompt_sig, 1)
old_prompt_branch = """    else:
        return (
            "[System: Your previous response was truncated by the output "
            "length limit. Continue exactly where you left off. Do not "
            "restart or repeat prior text. Finish the answer directly.]"
        )"""
new_prompt_branch = """    else:
        if managed_json:
            return (
                "[System: The previous JSON response was truncated. Return one new, complete, valid JSON object now. "
                "Do not continue or repeat the partial prefix. Use the same supplied facts and answer the same user question concisely. "
                "Return JSON only.]"
            )
        return (
            "[System: Your previous response was truncated by the output "
            "length limit. Continue exactly where you left off. Do not "
            "restart or repeat prior text. Finish the answer directly.]"
        )"""
if loop_source.count(old_prompt_branch) != 1:
    raise RuntimeError("Behavior policy patch boundary: continuation prompt body")
loop_source = loop_source.replace(old_prompt_branch, new_prompt_branch, 1)
old_call = "_get_continuation_prompt(\n                                    _is_partial_stream_stub, _dropped_tools\n                                )"
new_call = "_get_continuation_prompt(\n                                    _is_partial_stream_stub, _dropped_tools,\n                                    managed_json=getattr(agent, \"_tp_managed_json\", False),\n                                )"
if loop_source.count(old_call) != 1:
    raise RuntimeError("Behavior policy patch boundary: continuation prompt call")
loop_source = loop_source.replace(old_call, new_call, 1)
old = """                            if assistant_message.content:
                                truncated_response_parts.append(assistant_message.content)"""
new = """                            if assistant_message.content and not getattr(agent, "_tp_managed_json", False):
                                truncated_response_parts.append(assistant_message.content)"""
if loop_source.count(old) != 1:
    raise RuntimeError("Behavior policy patch boundary: managed JSON length parts")
loop.write_text(loop_source.replace(old, new, 1))

compile(source, str(api), "exec")
api.write_text(source)

prompt = root / "agent/system_prompt.py"
source = prompt.read_text()
anchor = "    # Local import to avoid pulling model_tools at module load.  Tests\n"
if source.count(anchor) != 1:
    raise RuntimeError("Behavior policy prompt-builder boundary")
source = source.replace(
    anchor,
    '''    managed = getattr(agent, "_tp_managed_system_prompt", None)
    if managed is not None:
        return {"stable": managed, "context": "", "volatile": ""}
''' + anchor,
    1,
)
compile(source, str(prompt), "exec")
prompt.write_text(source)
