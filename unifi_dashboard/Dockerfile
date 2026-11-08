ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3

COPY unifi_dashboard.py i18n_en.py dom_i18n.js /

CMD ["python3", "-u", "/unifi_dashboard.py"]
