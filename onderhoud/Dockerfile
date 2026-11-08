ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3

COPY onderhoud.py /app/
COPY fonts /app/fonts

CMD ["python3", "-u", "/app/onderhoud.py"]
