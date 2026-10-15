# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv "$VIRTUAL_ENV" \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY build-requirements.lock requirements.lock /build/
COPY LICENSE.wrapper LICENSE.upstream NOTICE.md /build/
RUN pip install --require-hashes -r build-requirements.lock \
    && pip install --require-hashes --no-build-isolation -r requirements.lock
ARG UPSTREAM_COMMIT=a4e000b650dbc55cefe4e5c02e6f97a4ec4b0eae
RUN git clone --filter=blob:none https://github.com/Archmonger/Wyoming-OpenAI-Gateway.git upstream \
    && git -C upstream checkout --detach "$UPSTREAM_COMMIT" \
    && test "$(git -C upstream rev-parse HEAD)" = "$UPSTREAM_COMMIT" \
    && pip wheel --no-deps --no-build-isolation --wheel-dir /wheels ./upstream \
    && cp /build/LICENSE.wrapper /build/LICENSE.upstream /build/NOTICE.md /wheels/

FROM ${PYTHON_IMAGE} AS runtime
ARG BUILD_VERSION=0.1.1
ARG BUILD_ARCH=amd64
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends libasound2 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
COPY --from=build /wheels /wheels
RUN pip install --no-deps /wheels/wyoming_openai_gateway-*.whl \
    && mkdir -p /usr/share/licenses/wyoming-openai-gateway \
    && cp /wheels/LICENSE.wrapper /wheels/LICENSE.upstream /wheels/NOTICE.md /usr/share/licenses/wyoming-openai-gateway/ \
    && rm -rf /wheels
WORKDIR /usr/local/bin
COPY run.sh /run.sh
COPY rootfs/usr/local/bin/discover.py /usr/local/bin/discover.py
RUN chmod 0755 /run.sh /usr/local/bin/discover.py
EXPOSE 8555/tcp
# run.sh execs Python, making the signal-handling supervisor PID 1.
ENTRYPOINT ["/run.sh"]
