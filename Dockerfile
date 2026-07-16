FROM nousresearch/hermes-agent@sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce

ENV HERMES_HOME=/opt/data

COPY entrypoint.sh /entrypoint.sh
COPY hermes_basic_auth_proxy.py /hermes_basic_auth_proxy.py
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["hermes", "gateway", "run", "--no-supervise"]
