FROM nousresearch/hermes-agent@sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce

ARG SOURCE_COMMIT=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.source="https://github.com/dmirtsev/hermes-agent-coolify" \
      org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.base.digest="sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce"

ENV HERMES_HOME=/opt/data \
    HERMES_GATEWAY_NO_SUPERVISE=true \
    HERMES_WRAPPER_COMMIT=${SOURCE_COMMIT} \
    HERMES_WRAPPER_BUILD_DATE=${BUILD_DATE} \
    HERMES_UPSTREAM_IMAGE_DIGEST=sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce \
    HERMES_UPSTREAM_REVISION=a38003be3d8ce87565915105b2d6261ba2cdb723 \
    HERMES_UPSTREAM_VERSION=0.16.0

# The upstream gateway builds its system prompt by walking from the current
# directory to a possible Git root. Coolify starts the image from /root, while
# the Hermes worker runs without permission to stat /root/.git. Keep every
# gateway turn in the readable persistent Hermes home instead.
WORKDIR /opt/data

COPY tp_knowledge_mcp_setup.sh /opt/hermes/tp_knowledge_mcp_setup.sh
COPY hermes_fixed_model_setup.sh /opt/hermes/hermes_fixed_model_setup.sh
COPY hermes_release_evidence.sh /opt/hermes/hermes_release_evidence.sh
COPY hermes_main_wrapper.sh /opt/hermes/hermes_main_wrapper.sh
COPY hermes_gateway_launcher.sh /opt/hermes/hermes_gateway_launcher.sh
COPY hermes_openrouter_accounting.py /opt/hermes/agent/openrouter_accounting.py
COPY hermes_durable_accounting.py /opt/hermes/agent/durable_accounting.py
COPY hermes_runtime_status.py /opt/hermes/agent/runtime_status_guard.py
COPY patch_hermes_health.py /opt/hermes-wrapper/patch_hermes_health.py
COPY patch_hermes_openrouter_accounting.py /opt/hermes-wrapper/patch_hermes_openrouter_accounting.py
COPY patch_hermes_runtime_status.py /opt/hermes-wrapper/patch_hermes_runtime_status.py
COPY patch_hermes_container_boot.py /opt/hermes-wrapper/patch_hermes_container_boot.py
RUN chmod +x /opt/hermes/tp_knowledge_mcp_setup.sh && \
    chmod +x /opt/hermes/hermes_fixed_model_setup.sh && \
    chmod +x /opt/hermes/hermes_release_evidence.sh && \
    chmod +x /opt/hermes/hermes_main_wrapper.sh && \
    chmod +x /opt/hermes/hermes_gateway_launcher.sh && \
    /opt/hermes/.venv/bin/python /opt/hermes-wrapper/patch_hermes_health.py && \
    /opt/hermes/.venv/bin/python /opt/hermes-wrapper/patch_hermes_runtime_status.py && \
    /opt/hermes/.venv/bin/python /opt/hermes-wrapper/patch_hermes_container_boot.py && \
    /opt/hermes/.venv/bin/python /opt/hermes-wrapper/patch_hermes_openrouter_accounting.py && \
    rm -f /etc/s6-overlay/s6-rc.d/user/contents.d/dashboard && \
    printf '%s\n' \
      '#!/command/with-contenv sh' \
      'set -eu' \
      '/opt/hermes/docker/stage2-hook.sh' \
      'exec /opt/hermes/tp_knowledge_mcp_setup.sh' \
      > /etc/cont-init.d/01-hermes-setup && \
    chmod +x /etc/cont-init.d/01-hermes-setup && \
    printf '%s\n' \
      '#!/command/with-contenv sh' \
      'set -eu' \
      'exec /opt/hermes/hermes_fixed_model_setup.sh' \
      > /etc/cont-init.d/02-hermes-fixed-model && \
    chmod +x /etc/cont-init.d/02-hermes-fixed-model

ENTRYPOINT ["/init", "/opt/hermes/hermes_main_wrapper.sh"]
# Run one foreground gateway; the patched reconciler registers the s6 slot but
# deliberately leaves it down, so redeploys cannot create a second instance.
CMD ["/opt/hermes/hermes_gateway_launcher.sh"]
