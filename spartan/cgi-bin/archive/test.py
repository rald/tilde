import socket
s = socket.create_connection(("127.0.0.1", 3000))
s.sendall(b"localhost /cgi-bin/test?foo=bar 0\r\n")
print(s.recv(1024).decode())
