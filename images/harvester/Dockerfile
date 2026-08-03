# The harvester needs the *host* datapath CLIs, so this image is deliberately
# not distroless: iptables-save, ipvsadm, conntrack and bpftool must be present.
# Build context: images/harvester
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 \
      iptables \
      ipvsadm \
      conntrack \
      bpftool \
      procps \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/mtdp
COPY collect.py /opt/mtdp/collect.py

# Runs privileged with hostPID; see deploy/harvester/daemonset.yaml.
ENTRYPOINT ["python3", "-u", "/opt/mtdp/collect.py"]
CMD ["--interval-s=5"]
