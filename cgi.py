#!/usr/bin/env python3
import argparse
from datetime import datetime
import mimetypes
import os
import pathlib
import shutil
from socketserver import ThreadingTCPServer, StreamRequestHandler
from urllib.parse import unquote, urlparse, parse_qs
import subprocess

class SpartanRequestHandler(StreamRequestHandler):
    def handle(self):
        try:
            self._handle()
        except ValueError as e:
            self.write_status(4, str(e).encode("ascii"))
        except Exception:
            self.write_status(5, b"An unexpected error has occurred")
            raise

    def _handle(self):
        raw_request_bytes = self.rfile.readline(4096)
        print(f"DEBUG: Raw request bytes received: {raw_request_bytes!r}")

        request = raw_request_bytes.decode("ascii").strip("\r\n")
        print(f'{datetime.now().isoformat()} "{request}"')

        try:
            hostname, raw_path_with_query, content_length = request.split(" ", 2)
        except ValueError:
            self.write_status(4, b"Bad Request: Invalid Spartan request format")
            return

        if not raw_path_with_query:
            self.write_status(4, b"Not Found: Empty path in request")
            return

        print(f"DEBUG: raw_path_with_query BEFORE parsing: {raw_path_with_query!r}")

        self.parsed_url = urlparse(raw_path_with_query)
        path = self.parsed_url.path  # keep the query string separately
        query_string = self.parsed_url.query
        query_params = parse_qs(query_string)

        print(f"DEBUG: Parsed path = {path}")
        print(f"DEBUG: Parsed query string = {query_string}")
        print(f"DEBUG: Query parameters = {query_params}")

        safe_path = os.path.normpath(unquote(path).strip("/"))
        if safe_path.startswith(("..", "/")):
            self.write_status(4, b"Not Found: Invalid path traversal attempt")
            return

        filepath = root / safe_path

        if args.cgi and filepath.is_file() and os.access(filepath, os.X_OK):
            self.run_cgi(filepath, query_params, query_string)
        elif filepath.is_file():
            self.write_file(filepath)
        elif filepath.is_dir():
            if not path.endswith("/"):
                self.write_status(3, f"{path}/")
            elif (filepath / "index.gmi").is_file():
                self.write_file(filepath / "index.gmi")
            else:
                self.write_status(2, b"text/gemini")
                self.write_line(b"=>..")
                for child in filepath.iterdir():
                    if child.is_dir():
                        self.write_line(f"=>{child.name}/".encode("utf-8"))
                    else:
                        self.write_line(f"=>{child.name}".encode("utf-8"))
        else:
            self.write_status(4, b"Not Found: File or directory does not exist")

    def write_file(self, filepath):
        mimetype, encoding = mimetypes.guess_type(filepath, strict=False)
        mimetype_bytes = (mimetype or "application/octet-stream").encode("ascii")
        with filepath.open("rb") as fp:
            self.write_status(2, mimetype_bytes)
            shutil.copyfileobj(fp, self.wfile)

    def write_line(self, text):
        if isinstance(text, str):
            text_bytes = text.encode("utf-8")
        else:
            text_bytes = text
        self.wfile.write(text_bytes + b"\n")

    def write_status(self, code, meta):
        if isinstance(meta, str):
            meta_bytes = meta.encode("ascii")
        else:
            meta_bytes = meta
        self.wfile.write(f"{code} ".encode("ascii") + meta_bytes + b"\r\n")

    def run_cgi(self, filepath, query_params, query_string):
        try:
            os.chmod(filepath, 0o755)
            cgi_env = os.environ.copy()
            cgi_env['QUERY_STRING'] = query_string  # assign unparsed query string as required by CGI
            cgi_env['REQUEST_METHOD'] = 'GET'

            print(f"DEBUG: CGI ENV QUERY_STRING='{cgi_env['QUERY_STRING']}'")
            print(f"DEBUG: Running script {filepath}")

            process = subprocess.run(
                [str(filepath)],
                capture_output=True,
                check=True,
                cwd=filepath.parent,
                env=cgi_env
            )

            cgi_output_bytes = process.stdout.strip()
            newline_index = cgi_output_bytes.find(b'\n')

            if newline_index == -1:
                self.write_status(5, b"CGI script produced no output or invalid format")
                return

            status_line_bytes = cgi_output_bytes[:newline_index].strip()
            body_content_bytes = cgi_output_bytes[newline_index+1:]

            parts = status_line_bytes.split(b' ', 1)
            if len(parts) < 2 or not parts[0].isdigit():
                self.write_status(5, b"CGI script produced invalid status line format")
                print(f"DEBUG: CGI Script Raw Output:\n{process.stdout!r}")
                print(f"DEBUG: Invalid status line: '{status_line_bytes.decode('utf-8', errors='replace')}'")
                return

            status_code = int(parts[0].decode('ascii'))
            meta_bytes = parts[1]

            self.wfile.write(f"{status_code} ".encode("ascii") + meta_bytes + b"\r\n")
            self.wfile.write(body_content_bytes)

        except FileNotFoundError:
            self.write_status(4, b"CGI script not found or not executable")
        except subprocess.CalledProcessError as e:
            stderr_msg_bytes = e.stderr.strip() or b'Unknown error'
            self.write_status(5, b"CGI script error: " + stderr_msg_bytes)
            print(f"DEBUG: CGI Script Stderr: {e.stderr!r}")
        except Exception as e:
            self.write_status(5, f"Error running CGI script: {e}".encode("ascii"))
            print(f"DEBUG: General error during CGI execution: {e}")

mimetypes.add_type("text/gemini", ".gmi")

parser = argparse.ArgumentParser(description="A spartan static file server")
parser.add_argument("dir", default=".", nargs="?", type=pathlib.Path,
                    help="Root directory to serve files from (default: current directory)")
parser.add_argument("--host", default="127.0.0.1",
                    help="Host address to bind to (default: 127.0.0.1)")
parser.add_argument("--port", default=3000, type=int,
                    help="Port to listen on (default: 3000)")
parser.add_argument("--cgi", action="store_true",
                    help="Enable CGI script execution for executable files")
args = parser.parse_args()

root = args.dir.resolve(strict=True)
print(f"Root Directory {root}")
if args.cgi:
    print("CGI functionality enabled.")

server = ThreadingTCPServer((args.host, args.port), SpartanRequestHandler)
print(f"Listening on {server.server_address}")

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nServer shutting down.")
    server.shutdown()
    server.server_close()

