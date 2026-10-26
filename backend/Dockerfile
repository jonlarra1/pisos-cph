FROM python:3.12-slim
RUN groupadd -g 10002 pisos && useradd -u 10002 -g 10002 -r -s /usr/sbin/nologin pisos && mkdir /data && chown pisos:pisos /data
COPY app.py admin.html routing.py portales.py anuncios.py api_v2.py puente.py destinos.py precalculo.py /app/
USER pisos
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/api/health')" || exit 1
CMD ["python", "-u", "/app/app.py"]
