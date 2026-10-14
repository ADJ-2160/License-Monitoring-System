FROM continuumio/miniconda3

# 기본 필수 패키지 설치 (git, curl)
RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

# Node.js 20 설치 (Gemini CLI 동작을 위해 필요)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Gemini CLI 및 정적 파일 서버 전역 설치
RUN npm install -g @google/gemini-cli http-server

WORKDIR /workspace
