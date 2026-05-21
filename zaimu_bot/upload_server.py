from __future__ import annotations

import os
import re
import tempfile
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


# このファイルは、iPhoneショートカットからPayPayのCSVを受け取るための
# 小さなHTTPサーバーです。FastAPIなどの外部ライブラリを使わず、
# Raspberry Piでも軽く動かせるようにPython標準ライブラリだけで書いています。

# プロジェクトのルートディレクトリです。
# 例: /home/pi/zaimu-bot
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# アップロードされたPayPay CSVを保存する標準の場所です。
DEFAULT_INBOX_DIR = PROJECT_ROOT / "data" / "imports" / "paypay" / "inbox"

# 受け付けるファイル名のルールです。
# PayPayのCSV名が Transactions_startdate_enddate.csv の形なので、
# Transactionsで始まり、.csvで終わるファイルだけを受け付けます。
FILENAME_PATTERN = re.compile(r"^Transactions[\w.-]*\.csv$")


def get_env(name: str, default_value: str) -> str:
    """環境変数を取得し、未設定ならデフォルト値を返す。"""
    return os.environ.get(name, default_value)


def get_required_token() -> str:
    """アップロード用トークンを環境変数から取得する。

    iPhoneから誰でもCSVを送れる状態にしないため、
    X-Upload-Tokenヘッダーに入れる秘密の文字列を必須にしています。
    """
    token = os.environ.get("UPLOAD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("UPLOAD_TOKEN is not set. Copy .env.example to .env and set a strong token.")
    return token


def safe_filename(filename: str) -> str | None:
    """保存してよいファイル名なら安全なファイル名を返す。

    ユーザーから送られたファイル名をそのまま使うと、
    ../../ のようなパスで意図しない場所に保存される危険があります。
    Path(...).name でファイル名部分だけにしてから、Transactions*.csvだけ許可します。
    """
    name = Path(unquote(filename)).name
    if FILENAME_PATTERN.match(name):
        return name
    return None


def unique_path(directory: Path, filename: str) -> Path:
    """同名ファイルがある場合に、上書きしない保存先パスを作る。

    例:
    - Transactions_20260501_20260531.csv
    - Transactions_20260501_20260531_1.csv
    - Transactions_20260501_20260531_2.csv
    """
    path = directory / filename
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not create unique filename for {filename}")


def extract_multipart_file(body: bytes, content_type: str) -> tuple[str | None, bytes | None]:
    """multipart/form-data から最初の添付ファイルを取り出す。

    iPhoneショートカットの設定によっては、CSVがHTTP本文そのものではなく
    multipart/form-dataという形式で送られることがあります。
    その場合でも取り込めるように、ファイル名と中身を取り出します。
    """
    # email.parserはメールだけでなく、multipart/form-dataの解析にも使えます。
    # Content-Typeヘッダーを足してから、HTTP本文を1つのメッセージとして解析します。
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\n\r\n" + body
    )

    if not message.is_multipart():
        return None, None

    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        return filename, payload

    return None, None


class UploadHandler(BaseHTTPRequestHandler):
    """HTTPリクエスト1件ごとの処理を担当するクラス。"""

    server_version = "ZaimuUploadServer/0.1"

    def do_GET(self) -> None:
        """GETリクエストを処理する。

        今はサーバーが起動しているか確認する /health だけ用意しています。
        """
        if self.path == "/health":
            self.respond_text(HTTPStatus.OK, "ok\n")
            return
        self.respond_text(HTTPStatus.NOT_FOUND, "not found\n")

    def do_POST(self) -> None:
        """POSTリクエストを処理する。

        iPhoneショートカットからPayPay CSVを送る入口です。
        URL、トークン、ファイル名を確認してからCSVを保存します。
        """
        parsed_url = urlparse(self.path)
        if parsed_url.path != "/upload/paypay-csv":
            self.respond_text(HTTPStatus.NOT_FOUND, "not found\n")
            return

        # .envのUPLOAD_TOKENと、リクエストヘッダーのX-Upload-Tokenが一致するか確認します。
        # LAN内だけで使う想定でも、最低限の認証として入れています。
        if self.headers.get("X-Upload-Token", "") != self.server.upload_token:
            self.respond_text(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
            return

        # Content-LengthはHTTP本文のサイズです。
        # 0以下ならファイルが送られていないのでエラーにします。
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            self.respond_text(HTTPStatus.BAD_REQUEST, "empty body\n")
            return

        body = self.rfile.read(content_length)
        filename = self.get_filename(parsed_url.query)
        file_body = body

        # iPhoneショートカットがmultipart/form-dataで送ってきた場合は、
        # HTTP本文からCSVファイル部分だけを取り出します。
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            multipart_filename, multipart_body = extract_multipart_file(body, content_type)
            if multipart_body is None:
                self.respond_text(HTTPStatus.BAD_REQUEST, "multipart file not found\n")
                return
            filename = filename or multipart_filename
            file_body = multipart_body

        if not filename:
            self.respond_text(HTTPStatus.BAD_REQUEST, "filename is required\n")
            return

        # ファイル名は Transactions*.csv のみ許可します。
        # ここで不正なファイル名や、関係ないファイルのアップロードを防ぎます。
        safe_name = safe_filename(filename)
        if safe_name is None:
            self.respond_text(HTTPStatus.BAD_REQUEST, "filename must match Transactions*.csv\n")
            return

        saved_path = self.save_file(safe_name, file_body)
        relative_path = saved_path.relative_to(PROJECT_ROOT)
        self.respond_text(HTTPStatus.CREATED, f"saved: {relative_path}\n")

    def get_filename(self, query: str) -> str | None:
        """リクエストからファイル名を取得する。

        優先順位:
        1. URLのクエリパラメータ filename
        2. X-Filenameヘッダー

        iPhoneショートカットではURLに filename を付ける方法がわかりやすいです。
        """
        values = parse_qs(query).get("filename", [])
        if values:
            return values[0]
        return self.headers.get("X-Filename")

    def save_file(self, filename: str, body: bytes) -> Path:
        """CSVファイルをinboxディレクトリに保存する。

        直接保存先に書き込まず、一度一時ファイルに書いてからリネームします。
        これにより、書き込み途中の壊れたファイルを取り込み処理が読みにくくなります。
        """
        inbox_dir = self.server.inbox_dir
        inbox_dir.mkdir(parents=True, exist_ok=True)
        destination = unique_path(inbox_dir, filename)

        with tempfile.NamedTemporaryFile(dir=inbox_dir, delete=False) as temporary_file:
            temporary_file.write(body)
            temporary_path = Path(temporary_file.name)

        temporary_path.replace(destination)
        return destination

    def respond_text(self, status: HTTPStatus, message: str) -> None:
        """テキスト形式のHTTPレスポンスを返す。"""
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """標準のアクセスログ出力を少しだけ見やすい形にする。"""
        print(f"{self.address_string()} - {format % args}")


class UploadServer(ThreadingHTTPServer):
    """アップロードAPI全体の設定を持つHTTPサーバー。"""

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler_class)

        # リクエストごとの処理で参照できるように、サーバー側に設定値を持たせます。
        self.upload_token = get_required_token()
        self.inbox_dir = Path(get_env("PAYPAY_CSV_INBOX_DIR", str(DEFAULT_INBOX_DIR))).resolve()


def main() -> None:
    """コマンドラインからサーバーを起動する入口。"""
    host = get_env("UPLOAD_HOST", "127.0.0.1")
    port = int(get_env("UPLOAD_PORT", "8080"))
    server = UploadServer((host, port), UploadHandler)
    print(f"Upload server listening on http://{host}:{port}")
    print(f"PayPay CSV inbox: {server.inbox_dir}")
    server.serve_forever()


if __name__ == "__main__":
    main()
