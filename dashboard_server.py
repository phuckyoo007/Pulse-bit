from http.server import HTTPServer, SimpleHTTPRequestHandler
import os

PORT = int(os.environ.get("PORT", 8000))

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.path = "/PiggyBank_dashboard.html"
        return super().do_GET()

HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()