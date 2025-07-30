#!/usr/bin/env python3
import os
import sys

sys.stdout.write("2 text/plain\r\n")
sys.stdout.write(f"QUERY_STRING: {os.environ.get('QUERY_STRING', 'NOT SET')}\r\n")
sys.stdout.flush()

