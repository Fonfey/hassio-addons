ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pillow

COPY ha_base.py media_manager.py /app/
COPY fonts /app/fonts

CMD ["python3", "-u", "/app/media_manager.py"]
