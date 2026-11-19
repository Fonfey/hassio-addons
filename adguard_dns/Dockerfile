ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3

COPY adguard_dashboard.py i18n_en.py dom_i18n.js map.json /app/

CMD ["python3", "-u", "/app/adguard_dashboard.py"]
