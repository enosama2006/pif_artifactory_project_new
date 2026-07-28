"""Static server for the taskpane with caching DISABLED.

Word's webview caches add-in files aggressively; plain `python -m http.server`
sends no cache headers, so a `git pull` may not reach the pane. This server
sends Cache-Control: no-store on everything, so closing/reopening the pane
always loads the latest files.

Usage: python serve.py [port]   (default 3000)
"""
import http.server
import sys


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    print(f"Serving taskpane on http://localhost:{port} (cache disabled)")
    http.server.ThreadingHTTPServer(("", port), NoCacheHandler).serve_forever()
