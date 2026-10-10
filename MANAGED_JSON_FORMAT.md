# Managed chart answer format

Cabinet sends `response_format: {"type":"json_object"}` for a managed
Hermes request only when its serialized source includes indexed calculation
statements. The same request path serves the chart dialogue, White Sheet
and Telegram. The model still needs native statement references; provider
JSON mode does not make an invented calculation true.

For managed requests the gateway admits only this exact format together
with a valid managed policy. It passes the value through the handler and per-request
agent constructor into `request_overrides.response_format`, after model
routing. Shared settings and other agents are not mutated. No repair or
second paid generation is added.

Absence of the field remains valid for existing managed requests and
ordinary contexts without indexed facts. Durable accounting hashes the
original body, including the optional field. An old completed body replays
unchanged; adding the field to the same request key causes a payload conflict
before provider dispatch. Cabinet recovery must use its frozen payload.

Local verification includes admission, both model profiles, constructor and
actual provider-kwargs capture, no-format legacy behavior, and durable replay.
No calculation configuration, policy publication, credential or routing
fallback is changed by this feature.

Legacy White Sheet planner and Wikipedia extraction requests already include
response_format without tp_hermes_policy. They retain their previous transport;
only managed requests validate and forward the new provider JSON override.
Claiming hermes_managed_v1 without a valid policy still fails admission.
