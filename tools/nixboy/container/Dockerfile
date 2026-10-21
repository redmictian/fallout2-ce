FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash python3 coreutils findutils grep sed gawk less nano procps \
    ca-certificates curl iproute2 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 nixboy \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash nixboy

COPY protocol.py /opt/nixboy/protocol.py
COPY container/control.py /opt/nixboy/control.py
COPY container/shell.py /opt/nixboy/shell.py
COPY container/nixboy /usr/local/bin/nixboy
RUN chmod 0755 /usr/local/bin/nixboy
USER 1000:1000
WORKDIR /home/nixboy
ENV TERM=xterm-256color HOME=/home/nixboy NIXBOY_CONTEXT_PATH=/tmp/nixboy/context.json
CMD ["python3", "-u", "/opt/nixboy/control.py", "serve"]
