# Towpath container images. Built and tested by GitHub Actions; deployments only pull them.
#
#   core    the towpath CLI, no optional extras (no Gmail or model client libraries)
#   recoll  the towpath CLI plus Recoll 1.36 with its Python binding and document helpers
#
# Both run as an unprivileged user, start no service, and only run the command given
# (default: --help). Nothing here syncs mail or scans files on its own.
#
# Image contents depend only on the source revision, never on the ref that triggered the build:
# VERSION is the Python package version and CREATED the commit time (both passed by CI).
# Every distribution package is upgraded to the archive's current version at build time, so CI
# can fetch and publish the exact corresponding source for each one (packaging/release/sources.py).

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
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.documentation="Third-party software and its licenses: /usr/share/licenses/NOTICE.md"
# DISTRO_UPGRADE=false is for offline development builds only. CI never sets it, and an image built
# without the upgrade fails the corresponding-source check, so it cannot be published.
ARG DISTRO_UPGRADE=true
RUN if [ "$DISTRO_UPGRADE" = true ]; then \
      apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/*; fi
# CPython here is built from source, not a distribution package. Drop the modules that link GNU
# readline, gdbm (GPL-3) or Berkeley DB 5.3 (Sleepycat), whose licenses would extend to CPython;
# a non-interactive CLI does not use them. sources.py fails the build if such a link reappears.
RUN removed="$(find /usr/local/lib -path '*/lib-dynload/*' \( -name 'readline.*.so' -o -name '_gdbm.*.so' \
      -o -name '_dbm.*.so' \) -print -delete | wc -l)" && test "$removed" -eq 3
RUN groupadd --system --gid 10001 towpath \
 && useradd --system --uid 10001 --gid 10001 --home-dir /state --no-create-home --shell /usr/sbin/nologin towpath \
 && mkdir -p /state /recovered /config /data && chown 10001:10001 /state /recovered
COPY --from=wheel /wheels /tmp/wheels
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels
COPY packaging/licenses/ /usr/local/lib/towpath/
RUN sh /usr/local/lib/towpath/collect-licenses.sh python3 \
 && cp /usr/local/lib/towpath/NOTICE-core.md /usr/share/licenses/NOTICE.md && rm -rf /usr/local/lib/towpath
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
      org.opencontainers.image.documentation="Third-party software and its licenses: /usr/share/licenses/NOTICE.md"
# Recoll (GPL-2.0-or-later) and helpers come unmodified from Ubuntu's archive. Helpers:
# python3-lxml (DOCX, ODT), poppler-utils (PDF), antiword (legacy DOC), unrtf (RTF), pff-tools (PST).
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends \
      recollcmd python3-recoll python3-lxml python3-venv python3-chardet \
      poppler-utils antiword unrtf pff-tools \
 && rm -rf /var/lib/apt/lists/*
# Towpath in a virtualenv that can see the system's Recoll binding (built for this Python only).
COPY --from=wheel /wheels /tmp/wheels
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    python3 -m venv --system-site-packages /opt/towpath \
 && /opt/towpath/bin/pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels \
 && /opt/towpath/bin/python -c "import recoll.recoll, recoll.rclextract"
COPY packaging/licenses/ /usr/local/lib/towpath/
RUN sh /usr/local/lib/towpath/collect-licenses.sh /opt/towpath/bin/python \
 && cp /usr/local/lib/towpath/NOTICE-recoll.md /usr/share/licenses/NOTICE.md && rm -rf /usr/local/lib/towpath
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
