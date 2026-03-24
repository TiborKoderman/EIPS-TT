FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

ENV PYTHONUNBUFFERED 1

RUN rm -f /etc/apt/sources.list.d/yarn*.list \
  && apt-get update \
  && export DEBIAN_FRONTEND=noninteractive \
  && apt-get install -y --no-install-recommends \
  postgresql-client \
  texlive-full \
  latexmk \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*



