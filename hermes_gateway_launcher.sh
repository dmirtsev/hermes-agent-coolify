#!/command/with-contenv sh
# Start exactly one foreground gateway.  During a rolling deploy the old
# container can briefly keep Hermes' persistent runtime lock; retrying here
# lets it finish its clean shutdown instead of turning a safe handoff into a
# failed deployment.
set -eu

# Be explicit even if an orchestrator overrides Docker's WORKDIR. The Hermes
# prompt builder inspects parent directories and must never inherit /root as
# its current directory after the worker drops privileges.
cd "${HERMES_HOME:-/opt/data}"

attempt=1
max_attempts=12
while [ "$attempt" -le "$max_attempts" ]; do
  if hermes gateway run --no-supervise -v; then
    exit 0
  fi
  if [ "$attempt" -eq "$max_attempts" ]; then
    exit 1
  fi
  echo "[hermes-launcher] gateway start attempt $attempt failed; retrying after handoff"
  attempt=$((attempt + 1))
  sleep 5
done
