# Bundles our generator alongside the two standard tools we cross-check
# against. If churngen and wrk disagree by more than a few percent on the same
# workload, the generator is the suspect, not the datapath.
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory. Docker cannot COPY
# from a parent of the build context, so this image reuses images/churngen's
# source by widening the context instead:
#
#   docker build -f images/loadgen/Dockerfile -t mtdp-loadgen .
#
# The Makefile 'images' target already does this.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 iperf3 wrk curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/mtdp
COPY images/churngen/churngen.py /opt/mtdp/churngen.py
COPY images/loadgen/wrk/report.lua /opt/mtdp/report.lua

ENTRYPOINT ["python3", "-u", "/opt/mtdp/churngen.py"]
