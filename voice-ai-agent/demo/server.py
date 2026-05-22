import http.server
import socketserver
import urllib.request
import urllib.error
import sys
import os

PORT = 8080
BACKEND_URL = "http://127.0.0.1:8000"

class ProxyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def handle_proxy(self):
        url = f"{BACKEND_URL}{self.path}"
        # Read request body for POST/PUT requests
        content_length = int(self.headers.get('Content-Length', 0))
        req_data = self.rfile.read(content_length) if content_length > 0 else None
        
        # Build the proxy request
        req_headers = {k: v for k, v in self.headers.items() if k.lower() != 'host'}
        req = urllib.request.Request(
            url,
            data=req_data,
            headers=req_headers,
            method=self.command
        )
        
        try:
            with urllib.request.urlopen(req) as response:
                self.send_response(response.status)
                for k, v in response.getheaders():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(response.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(e.read())
        except urllib.error.URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Bad Gateway: {e.reason}".encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Internal Server Error: {str(e)}".encode('utf-8'))

    def do_GET(self):
        # Identify paths that are part of the backend API / docs
        proxy_prefixes = ["/metrics", "/traces", "/docs", "/openapi.json", "/health", "/redoc"]
        should_proxy = any(self.path.startswith(prefix) for prefix in proxy_prefixes)
        
        if should_proxy:
            self.handle_proxy()
        else:
            super().do_GET()

    def do_POST(self):
        # Proxy campaigns, voice process, or any other POST endpoint
        proxy_prefixes = ["/campaigns/", "/voice/"]
        should_proxy = any(self.path.startswith(prefix) for prefix in proxy_prefixes)
        
        if should_proxy:
            self.handle_proxy()
        else:
            self.send_error(404, "File not found")

if __name__ == "__main__":
    # Change working directory to where index.html is located
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), ProxyHTTPRequestHandler) as httpd:
        print(f"Serving Demo UI on http://localhost:{PORT}")
        print(f"Proxying backend requests to {BACKEND_URL}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nDemo server stopped.")
            sys.exit(0)
