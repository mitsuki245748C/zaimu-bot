# Raspberry Piでも動かしやすい、公式の軽量Pythonイメージを使います。
# python:3.12-slim は amd64 / arm64 など複数CPUに対応しています。
FROM python:3.12-slim

# Pythonが .pyc を作らないようにし、ログをすぐ標準出力へ出します。
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# アプリをrootではなく専用ユーザーで実行します。
# Raspberry Piの通常ユーザーはUID/GIDが1000であることが多いため、
# bind mountした ./data にも書き込みやすいように1000で揃えます。
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --shell /usr/sbin/nologin app

# コンテナ内の作業ディレクトリです。
WORKDIR /app

# まずアプリ本体だけをコピーします。
# 今は外部ライブラリを使っていないので、pip installは不要です。
COPY --chown=app:app zaimu_bot ./zaimu_bot

# CSV保存先のディレクトリを作っておきます。
RUN mkdir -p /app/data/imports/paypay/inbox \
    && chown -R app:app /app

# ここ以降のプロセスは非rootユーザーで実行します。
USER app

# アップロードAPIの標準ポートです。
EXPOSE 8080

# コンテナ起動時にHTTPアップロードAPIを起動します。
CMD ["python", "-m", "zaimu_bot.upload_server"]
