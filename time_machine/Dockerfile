ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-yaml

COPY ha_base.py time_machine.py /app/
COPY fonts /app/fonts

CMD ["python3", "-u", "/app/time_machine.py"]
