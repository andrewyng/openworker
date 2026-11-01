"""Synthetic authenticated loopback Git HTTPS fixture. Never shipped."""
import base64, json, os, ssl, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text())
counts={'authorized':0,'denied':0,'receive':0}
backend=subprocess.check_output(['/usr/bin/git','--exec-path'],text=True).strip()+'/git-http-backend'
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,status,body,headers={}):
        self.send_response(status)
        for k,v in headers.items(): self.send_header(k,v)
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self): self.handle_git()
    def do_POST(self): self.handle_git()
    def handle_git(self):
        u=urlsplit(self.path)
        if u.path=='/status': return self.reply(200,json.dumps(counts).encode())
        expected='Basic '+base64.b64encode(('x-access-token:'+cfg['token']).encode()).decode()
        if self.headers.get('Authorization')!=expected:
            counts['denied']+=1;return self.reply(401,b'Authentication required',{'WWW-Authenticate':'Basic realm="fixture"'})
        if not u.path.startswith('/org/game.git/'): return self.reply(404,b'')
        counts['authorized']+=1
        if u.path.endswith('/git-receive-pack'):counts['receive']+=1
        size=int(self.headers.get('Content-Length','0'))
        if not 0<=size<=4*1024*1024: return self.reply(413,b'')
        data=self.rfile.read(size)
        if self.headers.get('Transfer-Encoding','').lower()=='chunked':
            data=b''
            while True:
                n=int(self.rfile.readline(128).split(b';')[0],16)
                if n==0: self.rfile.readline();break
                if n>4*1024*1024 or len(data)+n>4*1024*1024:return self.reply(413,b'')
                data+=self.rfile.read(n);self.rfile.read(2)
        env={'PATH':'/usr/bin:/bin','GIT_PROJECT_ROOT':cfg['root'],'GIT_HTTP_EXPORT_ALL':'1','PATH_INFO':u.path,'QUERY_STRING':u.query,'REQUEST_METHOD':self.command,'CONTENT_TYPE':self.headers.get('Content-Type',''),'CONTENT_LENGTH':str(len(data)),'REMOTE_USER':'fixture','HTTP_GIT_PROTOCOL':self.headers.get('Git-Protocol',''),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}
        p=subprocess.run([backend],input=data,capture_output=True,env=env,timeout=20)
        if p.returncode or len(p.stdout)>4*1024*1024:return self.reply(502,b'Backend failed')
        header,body=p.stdout.split(b'\r\n\r\n',1);status=200;headers={}
        for line in header.decode().splitlines():
            k,v=line.split(':',1)
            if k.lower()=='status':status=int(v.strip().split()[0])
            elif k.lower()!='content-length':headers[k]=v.strip()
        self.reply(status,body,headers)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cfg['cert'],cfg['key'])
server.socket=ctx.wrap_socket(server.socket,server_side=True)
print(server.server_port,flush=True)
server.serve_forever()
