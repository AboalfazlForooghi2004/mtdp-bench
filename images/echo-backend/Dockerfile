# Pinned by digest-able tag; alpine keeps the image small enough that image pull
# is not a confounder when 1200 pods start at once on a 200-tenant run.
# Build context: images/echo-backend
FROM python:3.12-alpine

RUN adduser -D -u 10001 mtdp
WORKDIR /opt/mtdp
COPY server.py /opt/mtdp/server.py

USER 10001
EXPOSE 8080
ENTRYPOINT ["python3", "-u", "/opt/mtdp/server.py"]
CMD ["--listen=:8080", "--payload-bytes=1024"]
