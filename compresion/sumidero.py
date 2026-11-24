"""Sumidero TCP robusto del shim (sin fcntl: se puede probar en cualquier SO)."""
from __future__ import annotations

import threading
import time


def servir_sumidero_tcp(s, espera: float = 30.0, parar=lambda: False) -> None:
    """Acepta conexiones TCP y descarta lo que llega. Nunca muere por falta de hilos o de fds:
    con ruido en el canal las conexiones se quedan colgadas, y antes cada una dejaba un hilo
    bloqueado hasta agotar pids_limit (RuntimeError: can't start new thread), con lo que el
    sumidero dejaba de escuchar y el generador veia 'Connection refused' para siempre.
    Cada conexion se cierra sola tras `espera` s sin datos."""
    def drenar(c):
        try:
            c.settimeout(espera)
            while c.recv(65535):
                pass
        except OSError:          # reset, timeout, cierre: se acabo la conexion
            pass
        finally:
            try:
                c.close()
            except OSError:
                pass

    while not parar():
        try:
            c, _ = s.accept()
        except OSError:          # EMFILE, conexion abortada...: seguir escuchando
            time.sleep(0.1)
            continue
        try:
            threading.Thread(target=drenar, args=(c,), daemon=True).start()
        except RuntimeError:     # sin hilos disponibles: soltar esta conexion y seguir
            c.close()
            time.sleep(0.05)
