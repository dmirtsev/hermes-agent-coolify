from pathlib import Path

api = Path("/opt/hermes/gateway/platforms/api_server.py")
source = api.read_text()


def replace(old, new, label):
    global source
    if source.count(old) != 1:
        raise RuntimeError("Behavior policy patch boundary: " + label)
    source = source.replace(old, new, 1)


replace("from agent.sourced_text import strict_context_execution_mode\n",
        "from agent.sourced_text import strict_context_execution_mode\n"
        "from agent.behavior_policy import validate_behavior_policy, install_managed_system_prompt, managed_response_settings\n", "imports")
replace('''        messages = body.get("messages")
''', '''        try:
            managed_policy = validate_behavior_policy(body, request.headers.get("Idempotency-Key"))
        except ValueError as error:
            return web.json_response(_openai_error(str(error), code=str(error)), status=400)

        messages = body.get("messages")
''', "admission")
replace('''                dialogue_model_code=dialogue_model_code,
            )
''', '''                dialogue_model_code=dialogue_model_code,
                managed_system_prompt=system_prompt if managed_policy is not None else None,
                managed_response_format=body.get("response_format") if managed_policy is not None else None,
            )
''', "execution")
replace('''        dialogue_model_code: Optional[str] = None,
    ) -> tuple:''', '''        dialogue_model_code: Optional[str] = None,
        managed_system_prompt: Optional[str] = None,
        managed_response_format: Optional[dict] = None,
    ) -> tuple:''', "argument")
replace('''        dialogue_model_code: Optional[str] = None,
    ) -> Any:''', '''        dialogue_model_code: Optional[str] = None,
        managed_response_format: Optional[dict] = None,
    ) -> Any:''', "constructor argument")
replace('''            dialogue_model_code, runtime_kwargs, model, reasoning_config, fallback_model
        )

        agent = AIAgent(''', '''            dialogue_model_code, runtime_kwargs, model, reasoning_config, fallback_model
        )
        runtime_kwargs = managed_response_settings(runtime_kwargs, managed_response_format)

        agent = AIAgent(''', "provider output settings")
replace('''                    dialogue_model_code=dialogue_model_code,
                )''', '''                    dialogue_model_code=dialogue_model_code,
                    managed_response_format=managed_response_format,
                )''', "constructor output format")
replace('''            if sourced_text_only:
''', '''            install_managed_system_prompt(agent, managed_system_prompt)
            if sourced_text_only:
''', "replacement")
replace('''        if durable_replayed:
            response_headers["X-Hermes-Idempotency-Replayed"] = "true"
''', '''        if durable_replayed:
            response_headers["X-Hermes-Idempotency-Replayed"] = "true"
        if managed_policy is not None:
            response_headers["X-Hermes-Policy-Id"] = managed_policy["version_id"]
            response_headers["X-Hermes-Policy-Sha256"] = managed_policy["sha256"]
''', "receipt")
compile(source, str(api), "exec")
api.write_text(source)

prompt = Path("/opt/hermes/agent/system_prompt.py")
source = prompt.read_text()
anchor = "    # Local import to avoid pulling model_tools at module load.  Tests\n"
if source.count(anchor) != 1:
    raise RuntimeError("Behavior policy prompt-builder boundary")
source = source.replace(anchor, '''    managed = getattr(agent, "_tp_managed_system_prompt", None)
    if managed is not None:
        return {"stable": managed, "context": "", "volatile": ""}
'''+anchor, 1)
compile(source, str(prompt), "exec")
prompt.write_text(source)
