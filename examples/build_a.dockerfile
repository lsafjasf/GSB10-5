# 基线构建：A 版本
FROM ubuntu:22.04
RUN mkdir -p /app
COPY src/app.py /app/app.py
COPY conf/settings.ini /app/settings.ini
RUN mkdir -p /data && write /data/version.txt v1
ENV APP_HOME=/app
WORKDIR /app
--- CONTEXT ---
{
  "files": [
    {"path": "src/app.py", "content": "print('hello')\n", "mode": 420,
     "mtime": 1700000000, "uid": 1000, "gid": 1000},
    {"path": "conf/settings.ini", "content": "[server]\nport=8080\n",
     "mode": 420, "mtime": 1700000001, "uid": 1000, "gid": 1000}
  ]
}
