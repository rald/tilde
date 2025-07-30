#!/usr/bin/env python3
import os
print("2 text/gemini")
print("QUERY_STRING:")
if 'QUERY_STRING' in os.environ:
  print(os.environ['QUERY_STRING'])
