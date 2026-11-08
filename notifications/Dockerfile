ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3

COPY ha_base.py notifications.py /app/
COPY fonts /app/fonts

CMD ["python3", "-u", "/app/notifications.py"]
