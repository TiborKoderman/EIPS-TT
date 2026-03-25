FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

ENV PYTHONUNBUFFERED=1

COPY requirements.txt /tmp/requirements.txt

RUN rm -f /etc/apt/sources.list.d/yarn*.list \
  && apt-get update \
  && export DEBIAN_FRONTEND=noninteractive \
  && apt-get install -y --no-install-recommends \
    postgresql-client \
    lmodern \
    latexmk \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-lang-english \
    texlive-publishers \
    texlive-science \
  && python -m pip install --upgrade pip \
  && python -m pip install -r /tmp/requirements.txt \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*
