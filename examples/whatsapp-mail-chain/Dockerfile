FROM python:3.12-slim

RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY mail_chain/ mail_chain/
RUN mkdir -p /app/data && chown app:app /app/data
USER app

EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python3", "-m", "mail_chain", "serve", "--config", "/app/config.json"]
