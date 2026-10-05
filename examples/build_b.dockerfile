# 候选构建：中间层变化（version v1 -> v2），
# 同时把上下文文件的 mtime/uid/gid 全部改掉（这些不应影响缓存）。
FROM ubuntu:22.04
RUN mkdir -p /app
COPY src/app.py /app/app.py
COPY conf/settings.ini /app/settings.ini
RUN mkdir -p /data && write /data/version.txt v2
ENV APP_HOME=/app
WORKDIR /app
--- CONTEXT ---
{
  "files": [
    {"path": "src/app.py", "content": "print('hello')\n", "mode": 420,
     "mtime": 1899999999, "uid": 3333, "gid": 4444},
    {"path": "conf/settings.ini", "content": "[server]\nport=8080\n",
     "mode": 420, "mtime": 1899999998, "uid": 3333, "gid": 4444}
  ]
}
