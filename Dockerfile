FROM nousresearch/hermes-agent@sha256:3326d81d12518be9b3ada3546b4abf97c2ac663e72978a7f8f27503c1ccaedce

#COPY entrypoint.sh /entrypoint.sh
#RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]


