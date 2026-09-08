#!/usr/bin/env python3
"""Backend Zipkin-compatibile che marca il tempo di ARRIVO di ogni POST.

Serve a misurare la latenza di consegna processor -> exporter -> backend:
per ogni span  latency = t_arrivo_POST - (span.timestamp + span.duration).
Il percorso di risposta e' tenuto minimo (timestamp, leggi, rispondi 202) e il
parsing avviene in un thread consumatore, cosi' il proxy non entra nel costo
sincrono che il SimpleSpanProcessor paga dentro il thread del task."""
import json, sys, threading, queue, time, signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUT = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9412
q = queue.Queue()

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive: una connessione, non un thread per POST
    def do_POST(self):
        t = time.time_ns()
        n = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(n) if n else b''
        self.send_response(202); self.send_header('Content-Length','0'); self.end_headers()
        q.put((t, body))
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Length','2'); self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *a): pass

def consumer():
    with open(OUT, 'w') as f:
        f.write("t_recv_ns\tbatch_idx\tn_span\tname\ttimestamp_us\tduration_us\tid\tparent\n")
        i = 0
        while True:
            item = q.get()
            if item is None: break
            t, body = item
            try:
                spans = json.loads(body)
            except Exception:
                continue
            for s in spans:
                f.write("%d\t%d\t%d\t%s\t%d\t%d\t%s\t%s\n" % (
                    t, i, len(spans), s.get('name','?'),
                    s.get('timestamp',0), s.get('duration',0),
                    s.get('id','-'), s.get('parentId','-')))
            i += 1
            f.flush()
        f.flush()

th = threading.Thread(target=consumer, daemon=True); th.start()
srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)

# Terminazione pulita: con un SIGTERM secco il processo muore prima che il
# consumatore abbia svuotato la coda e fatto flush, e si perde la CODA dei dati --
# proprio i batch finali, che con il BatchSpanProcessor sono quelli dello shutdown.
def _stop(signum, frame):
    threading.Thread(target=srv.shutdown, daemon=True).start()
for _s in (signal.SIGTERM, signal.SIGINT):
    signal.signal(_s, _stop)

print("probe in ascolto su 127.0.0.1:%d -> %s" % (PORT, OUT), flush=True)
try:
    srv.serve_forever()
finally:
    q.put(None); th.join(timeout=15)
