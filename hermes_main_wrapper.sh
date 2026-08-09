#!/command/with-contenv sh
set -eu

# Run in the supervised main process, not only in cont-init. s6 intentionally
# continues after a legacy cont-init failure, while a failure here prevents the
# API server from becoming healthy when release identity is required.
/opt/hermes/hermes_release_evidence.sh

exec /opt/hermes/docker/main-wrapper.sh "$@"
