#!/usr/bin/env sh
set -eu

printf '\n=== Hermes runtime diagnostics ===\n'
printf 'Date: %s\n' "$(date -Iseconds 2>/dev/null || date)"
printf 'User: %s\n' "$(id 2>/dev/null || true)"
printf 'Pwd: %s\n' "$(pwd)"
printf '\n--- Environment hints, redacted ---\n'
env | sort | grep -Ei '^(HERMES|GATEWAY|OPENROUTER|OPENAI|MODEL|MCP|GBRAIN|PORT|HOST)_' | sed -E 's/(TOKEN|KEY|SECRET|PASSWORD)=.*/\1=<redacted>/I' || true

printf '\n--- Executables ---\n'
command -v hermes || true
command -v gateway || true
command -v python || true
command -v python3 || true
command -v node || true
command -v bun || true

printf '\n--- Hermes CLI help ---\n'
if command -v hermes >/dev/null 2>&1; then
  hermes --help || true
  printf '\n--- hermes gateway --help ---\n'
  hermes gateway --help || true
  printf '\n--- hermes api --help ---\n'
  hermes api --help || true
  printf '\n--- hermes chat --help ---\n'
  hermes chat --help || true
  printf '\n--- hermes run --help ---\n'
  hermes run --help || true
fi

printf '\n--- Known paths ---\n'
ls -la /opt 2>/dev/null || true
ls -la /opt/hermes 2>/dev/null || true
ls -la /opt/data 2>/dev/null || true
ls -la /app 2>/dev/null || true
ls -la /usr/local/bin 2>/dev/null | head -80 || true

printf '\n--- Listening ports ---\n'
(ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null || true)

printf '\n--- Candidate files ---\n'
find /opt/hermes /opt/data /app /usr/local -type f \( -name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.toml' \) 2>/dev/null | head -300 || true

printf '\n--- API route / auth grep ---\n'
grep -RInE 'api/hermes/chat|/api/|no_cookie|login_url|unauthenticated|Authorization|Bearer|cookie|app\.post|router\.post|@app\.post|FastAPI|express' /opt/hermes /opt/data /app /usr/local 2>/dev/null | head -300 || true

printf '\n--- Python package hints ---\n'
if command -v python >/dev/null 2>&1; then
  python - <<'PY' || true
import pkgutil
mods = sorted([m.name for m in pkgutil.iter_modules() if 'hermes' in m.name.lower() or 'gateway' in m.name.lower()])
print(mods)
PY
fi
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY' || true
import pkgutil
mods = sorted([m.name for m in pkgutil.iter_modules() if 'hermes' in m.name.lower() or 'gateway' in m.name.lower()])
print(mods)
PY
fi

printf '\n=== Diagnostics complete ===\n'
