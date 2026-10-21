FROM python:3.12-slim
RUN useradd -u 10001 -r -s /usr/sbin/nologin pisos && mkdir /data && chown pisos /data
COPY app.py /app/app.py
USER pisos
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8787/api/health')" || exit 1
CMD ["python", "-u", "/app/app.py"]
