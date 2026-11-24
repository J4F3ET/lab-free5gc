"""Pruebas del sumidero TCP: no debe dejar de escuchar aunque falten hilos ni dejar conexiones colgadas."""
import socket
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sumidero


def _escucha():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(16)
    return s, s.getsockname()[1]


class SumideroTcp(unittest.TestCase):
    def arrancar(self, **kw):
        s, puerto = _escucha()
        parar = [False]
        h = threading.Thread(target=sumidero.servir_sumidero_tcp, args=(s,),
                             kwargs=dict(parar=lambda: parar[0], **kw), daemon=True)
        h.start()
        self.addCleanup(lambda: (parar.__setitem__(0, True), s.close()))
        return puerto

    def test_descarta_datos_y_cierra_al_cerrar_el_cliente(self):
        puerto = self.arrancar(espera=2.0)
        c = socket.create_connection(("127.0.0.1", puerto), timeout=3)
        c.sendall(b"x" * 5000)
        c.shutdown(socket.SHUT_WR)
        c.settimeout(3)
        self.assertEqual(c.recv(10), b"")   # el sumidero cerro su lado
        c.close()

    def test_cierra_conexiones_colgadas_tras_la_espera(self):
        puerto = self.arrancar(espera=0.3)
        c = socket.create_connection(("127.0.0.1", puerto), timeout=3)
        c.settimeout(3)
        t0 = time.time()
        self.assertEqual(c.recv(10), b"")   # sin datos: el sumidero la cierra solo
        self.assertLess(time.time() - t0, 2.5)
        c.close()

    def test_sigue_escuchando_si_no_hay_hilos(self):
        puerto = self.arrancar(espera=1.0)
        fallos = [3]

        class Falso:
            def __init__(self, *a, **k):
                self.real = threading.Thread(*a, **k)

            def start(self):
                if fallos[0] > 0:
                    fallos[0] -= 1
                    raise RuntimeError("can't start new thread")
                self.real.start()

        falso = types.SimpleNamespace(Thread=Falso)
        with mock.patch.object(sumidero, "threading", falso):
            for _ in range(3):              # tres conexiones sin hilo: se sueltan
                c = socket.create_connection(("127.0.0.1", puerto), timeout=3)
                c.settimeout(3)
                self.assertEqual(c.recv(10), b"")
                c.close()
            c = socket.create_connection(("127.0.0.1", puerto), timeout=3)   # la cuarta ya tiene hilo
            c.sendall(b"hola")
            c.shutdown(socket.SHUT_WR)
            c.settimeout(3)
            self.assertEqual(c.recv(10), b"")
            c.close()
        self.assertEqual(fallos[0], 0)


if __name__ == "__main__":
    unittest.main()
