# Build context: images/churngen
FROM python:3.12-alpine

# The generator opens up to ~8000 sockets/s (profile W4). Without a raised
# nofile limit the generator fails before the datapath does, and we would be
# measuring our own load generator instead of Cilium/IPVS/iptables.
RUN adduser -D -u 10001 mtdp
WORKDIR /opt/mtdp
COPY churngen.py /opt/mtdp/churngen.py

USER 10001
ENTRYPOINT ["python3", "-u", "/opt/mtdp/churngen.py"]
