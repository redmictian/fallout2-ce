FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
RUN apt-get update && apt-get install -y --no-install-recommends qemu-system-x86 qemu-utils seabios python3 curl ca-certificates genisoimage openssh-client procps gnupg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 runner && useradd -u 1000 -g 1000 -M runner \
    && useradd -u 1001 -M console \
    && mkdir -p /opt/qual /work /images /evidence \
    && chown 1000:1000 /work /images /evidence && chmod 0700 /work
COPY supervisor.py control.py serial_history.py /opt/nixboy-qemu/
USER 1000:1000
WORKDIR /work
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/opt/nixboy-qemu/supervisor.py"]
