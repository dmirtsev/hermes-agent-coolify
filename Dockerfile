FROM nousresearch/hermes-agent@sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce

ENV HERMES_HOME=/opt/data

COPY tp_knowledge_mcp_setup.sh /opt/hermes/tp_knowledge_mcp_setup.sh
RUN chmod +x /opt/hermes/tp_knowledge_mcp_setup.sh && \
    rm -f /etc/s6-overlay/s6-rc.d/user/contents.d/dashboard && \
    printf '%s\n' \
      '#!/command/with-contenv sh' \
      'set -eu' \
      '/opt/hermes/docker/stage2-hook.sh' \
      'exec /opt/hermes/tp_knowledge_mcp_setup.sh' \
      > /etc/cont-init.d/01-hermes-setup && \
    chmod +x /etc/cont-init.d/01-hermes-setup

ENTRYPOINT ["/init", "/opt/hermes/docker/main-wrapper.sh"]
CMD ["gateway", "run", "--no-supervise", "-v"]
