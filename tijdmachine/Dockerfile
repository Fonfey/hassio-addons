ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-yaml

COPY ha_base.py tijdmachine.py /app/
COPY fonts /app/fonts

CMD ["python3", "-u", "/app/tijdmachine.py"]
