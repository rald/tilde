#!/usr/bin/env python3

"""
A reference spartan:// protocol server.

Copyright (c) Michael Lazar

Blue Oak Model License 1.0.0
"""

import argparse
from datetime import datetime
import mimetypes
import os
import pathlib
import shutil
import sys
import io
import subprocess
from socketserver import ThreadingTCPServer, StreamRequestHandler
from urllib.parse import unquote
import cgi

mimetypes.add_type("text/gemini", ".gmi")


class SpartanRequestHandler(StreamRequestHandler):

    enable_cgi = False  # Set after parsing args

    def handle(self):
        try:
            self._handle()
        except ValueError as e:
            self.write_status(4, str(e))
        except Exception:
            self.write_status(5, "An unexpected error has occurred")
            raise

    def _handle(self):
        request = self.rfile.readline(4096)
        request = request.decode("ascii").strip("\r\n")
        print(f'{datetime.now().isoformat()} "{request}"')

        try:
            hostname, path, content_length = request.split(" ")
        except ValueError:
            raise ValueError("Malformed request")

        if not path:
            raise ValueError("Not Found")

        path = unquote(path)

        # Protect from path traversal attacks
        safe_path = os.path.normpath(path.strip("/"))
        if safe_path.startswith(("..", "/")):
            raise ValueError("Not Found")

        filepath = root / safe_path

        # Decide on CGI execution if enabled
        if SpartanRequestHandler.enable_cgi and self.is_cgi(filepath) and filepath.is_file():
            self.run_cgi(filepath)
        elif filepath.is_file():
            self.write_file(filepath)
        elif filepath.is_dir():
            if not path.endswith("/"):
                # Redirect to canonical directory path ending with slash
                self.write_status(3, f"{path}/")
            elif (filepath / "index.gmi").is_file():
                self.write_file(filepath / "index.gmi")
            else:
                self.write_status(2, "text/gemini")
                # Sorted for nicer listing
                for child in sorted(filepath.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
                    if child.is_dir():
                        self.write_line(f"=>{child.name}/")
                    else:
                        self.write_line(f"=>{child.name}")
        else:
            raise ValueError("Not Found")

    def is_cgi(self, filepath):
        p = pathlib.Path(filepath)
        # CGI only inside cgi-bin directory
        if 'cgi-bin' not in p.parts:
            return False
        # Python scripts run in process
        if p.suffix == '.py':
            return True
        # Other executable files run as subprocesses
        try:
            return os.access(filepath, os.X_OK) and filepath.is_file()
        except Exception:
            return False

    def run_cgi(self, filepath):
        if filepath.suffix == '.py':
            self.run_cgi_python(filepath)
        else:
            self.run_cgi_exec(filepath)

    def run_cgi_python(self, filepath):
        """Run Python CGI script in-process using exec and cgi module."""
        try:
            env = os.environ.copy()
            env['GATEWAY_INTERFACE'] = 'CGI/1.1'
            env['SCRIPT_FILENAME'] = str(filepath)
            env['SCRIPT_NAME'] = str(filepath.name)
            env['REQUEST_METHOD'] = 'GET'  # Spartan supports only GET currently
            env['SERVER_SOFTWARE'] = 'spartan_server.py'
            env['SERVER_PROTOCOL'] = 'SPARTAN'
            env['REMOTE_ADDR'] = self.client_address[0]
            env['SERVER_NAME'] = self.server.server_address[0]
            env['SERVER_PORT'] = str(self.server.server_address[1])

            old_stdout = sys.stdout
            old_stderr = sys.stderr
            old_stdin = sys.stdin

            # Capture output in BytesIO with TextIOWrapper for utf-8 safety
            sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
            sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
            sys.stdin = io.StringIO()  # No stdin for GET

            os.environ.update(env)

            # Compile and exec script
            with open(filepath, 'rb') as f:
                code = compile(f.read(), str(filepath), 'exec')

            script_globals = {
                '__file__': str(filepath),
                '__name__': '__main__',
                'os': os,
                'sys': sys,
                'cgi': cgi,
            }

            exec(code, script_globals)

            sys.stdout.flush()
            sys.stderr.flush()

            out_bytes = sys.stdout.buffer.getvalue()
            err_text = sys.stderr.buffer.getvalue().decode('utf-8', errors='replace')

            sys.stdout = old_stdout
            sys.stderr = old_stderr
            sys.stdin = old_stdin

            # Parse headers/body
            split_seq = b'\r\n\r\n'
            header_end = out_bytes.find(split_seq)
            if header_end == -1:
                split_seq = b'\n\n'
                header_end = out_bytes.find(split_seq)

            if header_end != -1:
                headers_bytes = out_bytes[:header_end]
                body_bytes = out_bytes[header_end+len(split_seq):]
                headers = headers_bytes.decode('iso-8859-1').splitlines()
            else:
                headers = []
                body_bytes = out_bytes

            content_type = 'text/plain'
            status_line = None

            for header in headers:
                if header.lower().startswith('content-type:'):
                    content_type = header.split(':', 1)[1].strip()
                elif header.lower().startswith('status:'):
                    status_line = header.split(':', 1)[1].strip()

            if status_line is not None:
                parts = status_line.split(' ', 1)
                try:
                    code = int(parts[0]) // 100
                except Exception:
                    code = 2
                meta = content_type
            else:
                code = 2
                meta = content_type

            self.write_status(code, meta)
            self.wfile.write(body_bytes)
            self.wfile.flush()

            if err_text:
                print(f"CGI script stderr: {err_text}", flush=True)

        except Exception as e:
            self.write_status(5, f"CGI Error: {e}")

    def run_cgi_exec(self, filepath):
        """Run a CGI executable (e.g. C binary) via subprocess."""
        try:
            env = os.environ.copy()
            env['GATEWAY_INTERFACE'] = 'CGI/1.1'
            env['SCRIPT_FILENAME'] = str(filepath)
            env['SCRIPT_NAME'] = str(filepath.name)
            env['REQUEST_METHOD'] = 'GET'  # Spartan supports only GET
            env['SERVER_SOFTWARE'] = 'spartan_server.py'
            env['SERVER_PROTOCOL'] = 'SPARTAN'
            env['REMOTE_ADDR'] = self.client_address[0]
            env['SERVER_NAME'] = self.server.server_address[0]
            env['SERVER_PORT'] = str(self.server.server_address[1])

            proc = subprocess.Popen(
                [str(filepath)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )

            stdout, stderr = proc.communicate(timeout=10)

            # Parse headers/body
            split_seq = b'\r\n\r\n'
            header_end = stdout.find(split_seq)
            if header_end == -1:
                split_seq = b'\n\n'
                header_end = stdout.find(split_seq)

            if header_end != -1:
                headers_bytes = stdout[:header_end]
                body_bytes = stdout[header_end + len(split_seq):]
                headers = headers_bytes.decode('iso-8859-1').splitlines()
            else:
                headers = []
                body_bytes = stdout

            content_type = 'text/plain'
            status_line = None

            for header in headers:
                if header.lower().startswith('content-type:'):
                    content_type = header.split(':', 1)[1].strip()
                elif header.lower().startswith('status:'):
                    status_line = header.split(':', 1)[1].strip()

            if status_line is not None:
                parts = status_line.split(' ', 1)
                try:
                    code = int(parts[0]) // 100
                except Exception:
                    code = 2
                meta = content_type
            else:
                code = 2
                meta = content_type

            self.write_status(code, meta)
            self.wfile.write(body_bytes)
            self.wfile.flush()

            if stderr:
                err_text = stderr.decode('utf-8', errors='replace')
                print(f"CGI exec script stderr: {err_text}", flush=True)

        except Exception as e:
            self.write_status(5, f"CGI Error: {e}")

    def write_file(self, filepath):
        mimetype, encoding = mimetypes.guess_type(filepath, strict=False)
        mimetype = mimetype or "application/octet-stream"
        with filepath.open("rb") as fp:
            self.write_status(2, mimetype)
            shutil.copyfileobj(fp, self.wfile)
            self.wfile.flush()

    def write_line(self, text):
        self.wfile.write(f"{text}\n".encode("utf-8"))

    def write_status(self, code, meta):
        self.wfile.write(f"{code} {meta}\r\n".encode("ascii"))


parser = argparse.ArgumentParser(description="A spartan static file server")
parser.add_argument("dir", default=".", nargs="?", type=pathlib.Path, help="Root directory to serve")
parser.add_argument("--host", default="127.0.0.1", help="Host to bind server")
parser.add_argument("--port", default=3000, type=int, help="Port to listen on")
parser.add_argument("--cgi", action="store_true", help="Enable CGI script execution")

args = parser.parse_args()
root = args.dir.resolve(strict=True)

SpartanRequestHandler.enable_cgi = args.cgi

print(f"Root Directory {root}")
print(f"CGI Execution Enabled: {SpartanRequestHandler.enable_cgi}")

server = ThreadingTCPServer((args.host, args.port), SpartanRequestHandler)
print(f"Listening on {server.server_address}")

try:
    server.serve_forever()
except KeyboardInterrupt:
    pass

