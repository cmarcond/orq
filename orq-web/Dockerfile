# orq-web: só o servidor da página. Os agentes rodam nas máquinas, alcançadas por ssh; por isso a
# imagem não tem claude nem tmux, só Python e o cliente ssh. O usuário dentro do container tem o mesmo
# uid e home do usuário do host, para montar ~/.ssh e ~/.config com os mesmos caminhos.
FROM python:3.12-slim
ARG UID=1000
ARG GID=1000
ARG USER=user
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g "$GID" "$USER" && useradd -m -u "$UID" -g "$GID" -s /bin/sh "$USER"
WORKDIR /app
COPY orqweb /app/orqweb
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 ORQ_NO_OPEN=1 \
    ORQ_BIND=0.0.0.0 ORQ_LOCAL_HOST=none \
    ORQ_STATE_DIR=/home/$USER/.config/orq-web/state \
    ORQ_SETS_DIR=/home/$USER/.config/orq-web/conjuntos \
    ORQ_PASSWORD_FILE=/home/$USER/.config/orq-web/password \
    ORQ_SSH_CONFIG=/home/$USER/.config/orq-web/ssh_config \
    CLAUDE_MULTICONTA_CONF=/home/$USER/.config/claude-multiconta/accounts.json
USER $USER
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/login',timeout=4)" || exit 1
CMD ["python3", "-m", "orqweb.server.cli"]
