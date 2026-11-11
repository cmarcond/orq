# orq-web: only the server of the page. The agents run on the machines, reached over ssh; that is why
# the image has neither claude nor tmux, only Python and the ssh client. The user inside the container
# has the same uid and home as the host user, so that ~/.ssh and ~/.config are mounted at the same paths.
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
