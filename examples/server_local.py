#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import SimpleHTTPServer
import SocketServer

PORT = 28080

Handler = SimpleHTTPServer.SimpleHTTPRequestHandler

httpd = SocketServer.TCPServer(("", PORT), Handler)

print "serving at port", PORT
httpd.serve_forever()

