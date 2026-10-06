#!/usr/bin/env python3
"""Apply after accounting/context patches to the reviewed, digest-pinned source."""
import os
from pathlib import Path

target = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes")) / "gateway/platforms/api_server.py"
source = target.read_text()
if "from agent.dialogue_models import" in source:
    raise SystemExit("dialogue model patch already present")


def replace(old, new, name, count=1):
    global source
    found = source.count(old)
    if found != count:
        raise SystemExit(f"expected {count} {name} anchors, found {found}")
    source = source.replace(old, new)


replace("from agent.design_completion import handle_design_completion\n",
        "from agent.design_completion import handle_design_completion\n"
        "from agent.dialogue_models import (DialogueModelError, validate_dialogue_model, dialogue_agent_settings, public_dialogue_models)\n", "imports")
replace('''        strict_context_only: bool = False,
    ) -> Any:''', '''        strict_context_only: bool = False,
        dialogue_model_code: Optional[str] = None,
    ) -> Any:''', "agent constructor argument")
replace('''        fallback_model = GatewayRunner._load_fallback_model()

        agent = AIAgent(''', '''        fallback_model = GatewayRunner._load_fallback_model()
        runtime_kwargs, model, reasoning_config, fallback_model = dialogue_agent_settings(
            dialogue_model_code, runtime_kwargs, model, reasoning_config, fallback_model
        )

        agent = AIAgent(''', "per-agent profile")
replace('''        messages = body.get("messages")
''', '''        try:
            dialogue_model_code = validate_dialogue_model(body, request.headers.get("Idempotency-Key"))
        except DialogueModelError as error:
            return web.json_response(_openai_error(str(error), code=str(error)), status=400)

        messages = body.get("messages")
''', "pre-dispatch admission", count=1)
replace('''                sourced_text_only=strict_sourced_text,
''', '''                sourced_text_only=strict_sourced_text,
                dialogue_model_code=dialogue_model_code,
''', "execution profile")
replace('''        sourced_text_only: bool = False,
    ) -> tuple:''', '''        sourced_text_only: bool = False,
        dialogue_model_code: Optional[str] = None,
    ) -> tuple:''', "execution argument")
replace('''                    strict_context_only=strict_context_only,
                )''', '''                    strict_context_only=strict_context_only,
                    dialogue_model_code=dialogue_model_code,
                )''', "agent profile argument")
replace('''            "release": _wrapper_release_evidence(),''', '''            "release": _wrapper_release_evidence(),
            "dialogue_models": public_dialogue_models(),''', "public capability", count=2)
compile(source, str(target), "exec")
target.write_text(source)
