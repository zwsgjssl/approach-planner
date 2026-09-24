# 遅い回線・途中切断・Wi-Fiログイン画面・オフラインを再現するテスト用サーバー(tests/offline.js から起動)
# 使い方: python3 slow_server.py <公開フォルダ> <モードファイル> <ポート>
import http.server, socketserver, os, time, sys
ROOT=sys.argv[1]
MODEFILE=sys.argv[2]
PORT=int(sys.argv[3])
def mode(): 
    try: return open(MODEFILE).read().strip()
    except: return 'normal'
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(s,*a,**k): super().__init__(*a,directory=ROOT,**k)
    def log_message(s,*a): pass
    def do_GET(s):
        m=mode(); p=s.path.split('?')[0]
        is_html = p.endswith('/') or p.endswith('.html')
        is_map = '/map_data_' in p
        if m=='offline': s.connection.close(); return
        if (is_html or is_map) and m=='portal302' and not p.startswith('/login'):
            s.send_response(302); s.send_header('Location','/login.html'); s.end_headers(); return
        if (is_html or is_map) and m=='portal200':
            b=b'<html><body><h1>Wi-Fi Login</h1></body></html>'
            s.send_response(200); s.send_header('Content-Type','text/html'); s.send_header('Content-Length',str(len(b))); s.end_headers(); s.wfile.write(b); return
        if p=='/login.html':
            b=b'<html><body>Login portal</body></html>'
            s.send_response(200); s.send_header('Content-Type','text/html'); s.end_headers(); s.wfile.write(b); return
        if (is_html or is_map) and m in('slow','truncated'):
            fp=os.path.join(ROOT, 'index.html' if p.endswith('/') else p.lstrip('/'))
            data=open(fp,'rb').read()
            s.send_response(200); s.send_header('Content-Type','text/html' if is_html else 'text/javascript'); s.send_header('Content-Length',str(len(data))); s.end_headers()
            try:
                if m=='truncated':
                    s.wfile.write(data[:len(data)//2]); s.wfile.flush(); time.sleep(0.5); s.connection.close(); return
                for i in range(0,len(data),2000):
                    s.wfile.write(data[i:i+2000]); s.wfile.flush(); time.sleep(0.5)
            except Exception: pass
            return
        return super().do_GET()
class T(socketserver.ThreadingMixIn, http.server.HTTPServer): daemon_threads=True
T(('127.0.0.1',PORT),H).serve_forever()
