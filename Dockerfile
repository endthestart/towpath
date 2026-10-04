# Towpath container images. Built and tested by GitHub Actions; deployments only pull them.
#
#   core    the towpath CLI, no optional extras (no Gmail or model client libraries)
#   recoll  the towpath CLI plus Recoll 1.36 with its Python binding and document helpers
#
# Both run as an unprivileged user, start no service, and only run the command given
# (default: --help). Nothing here syncs mail or scans files on its own.

ARG PYTHON_IMAGE=python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016
ARG UBUNTU_IMAGE=ubuntu:24.04@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

# ---------------------------------------------------------------- wheel
FROM ${PYTHON_IMAGE} AS wheel
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Optional: a CA for a TLS-intercepting proxy, passed as a build secret (never stored in a layer).
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

# ---------------------------------------------------------------- core
FROM ${PYTHON_IMAGE} AS core
ARG REVISION=unknown
ARG VERSION=dev
ARG CREATED=unknown
ARG SOURCE=https://github.com/endthestart/towpath
LABEL org.opencontainers.image.title="towpath" \
      org.opencontainers.image.description="Towpath command line (read-only); optional file discovery" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.licenses="MIT"
RUN groupadd --system --gid 10001 towpath \
 && useradd --system --uid 10001 --gid 10001 --home-dir /state --no-create-home --shell /usr/sbin/nologin towpath \
 && mkdir -p /state /recovered /config /data && chown 10001:10001 /state /recovered
COPY --from=wheel /wheels /tmp/wheels
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels
COPY LICENSE /usr/share/licenses/towpath/LICENSE
RUN mkdir -p /usr/share/towpath \
 && printf '{"image": "core", "revision": "%s", "version": "%s", "created": "%s", "source": "%s"}\n' \
      "${REVISION}" "${VERSION}" "${CREATED}" "${SOURCE}" > /usr/share/towpath/build-info.json
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/state
USER 10001:10001
WORKDIR /state
ENTRYPOINT ["towpath"]
CMD ["--help"]

# ---------------------------------------------------------------- recoll
FROM ${UBUNTU_IMAGE} AS recoll
ARG REVISION=unknown
ARG VERSION=dev
ARG CREATED=unknown
ARG SOURCE=https://github.com/endthestart/towpath
LABEL org.opencontainers.image.title="towpath-recoll" \
      org.opencontainers.image.description="Towpath command line with Recoll for optional file discovery" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.documentation="Bundled third-party packages and their licenses: /usr/share/licenses/bundled"
# Recoll (GPL-2.0-or-later) and helpers come unmodified from Ubuntu's archive. Helpers:
# python3-lxml (DOCX, ODT), poppler-utils (PDF), antiword (legacy DOC), unrtf (RTF), pff-tools (PST).
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      recollcmd python3-recoll python3-lxml python3-venv python3-chardet \
      poppler-utils antiword unrtf pff-tools \
 && rm -rf /var/lib/apt/lists/*
COPY packaging/recoll/NOTICE.md /usr/share/licenses/bundled/NOTICE.md
COPY packaging/recoll/collect-licenses.sh /usr/local/lib/towpath/collect-licenses.sh
RUN sh /usr/local/lib/towpath/collect-licenses.sh \
      recollcmd python3-recoll python3-lxml poppler-utils antiword unrtf pff-tools \
      libxapian30 python3 \
 && rm /usr/local/lib/towpath/collect-licenses.sh
# Towpath in a virtualenv that can see the system's Recoll binding (built for this Python only).
COPY --from=wheel /wheels /tmp/wheels
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python3 -m venv --system-site-packages /opt/towpath \
 && /opt/towpath/bin/pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels \
 && /opt/towpath/bin/python -c "import recoll.recoll, recoll.rclextract"
COPY LICENSE /usr/share/licenses/towpath/LICENSE
RUN groupadd --system --gid 10001 towpath \
 && useradd --system --uid 10001 --gid 10001 --home-dir /state --no-create-home --shell /usr/sbin/nologin towpath \
 && mkdir -p /state /recovered /config /data /index && chown 10001:10001 /state /recovered /index \
 && mkdir -p /usr/share/towpath \
 && printf '{"image": "recoll", "revision": "%s", "version": "%s", "created": "%s", "source": "%s"}\n' \
      "${REVISION}" "${VERSION}" "${CREATED}" "${SOURCE}" > /usr/share/towpath/build-info.json
ENV PATH=/opt/towpath/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/state
USER 10001:10001
WORKDIR /state
ENTRYPOINT ["towpath"]
CMD ["--help"]
