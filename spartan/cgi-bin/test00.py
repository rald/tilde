#!/usr/bin/env python3
import os
import sys

sys.stdout.write("2 text/gemini\n")

sys.stdout.write(f"=: test00.py?name=rald&pass=rose test00\n")

sys.stdout.write(f"QUERY_STRING: {os.environ.get('QUERY_STRING', 'NOT SET')}\n")
sys.stdout.flush()

