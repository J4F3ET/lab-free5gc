#!/usr/bin/env python3
"""
shim.py — El compresor corriendo en la red del lab: un túnel que comprime cada paquete IP.

  ue  (contenedor cmp-ue, vive en el netns del UE):  python3 shim.py ue
  dn  (contenedor cmp-dn, en la Data Network):       python3 shim.py dn

Cada lado crea una interfaz TUN "cmp0" (10.99.0.1 en el UE, 10.99.0.2 en la DN). Lo que una
aplicación del UE mande a 10.99.0.2 entra a cmp0, se comprime (ROHC-lite + el códec que elija
el bandido) y viaja como UDP por uesimtun0 → gNB → GTP-U → UPF → cmp-dn, que lo descomprime
y lo entrega en su cmp0. La vuelta (DN → UE) hace lo mismo al revés.

    app ──► cmp0 ─[comprimir]─► uesimtun0 ══ 5G ══► UPF ──► cmp-dn ─[descomprimir]─► cmp0 ──► app

Lo mismo enviado directo a 10.100.204.30 (la IP real de cmp-dn) NO pasa por el compresor: así
se compara con y sin compresión sobre la misma red, midiendo los bytes de uesimtun0.

Ojo con lo que esto mide: en el lab el compresor no puede ir dentro de PDCP (UERANSIM no lo
implementa), así que cada paquete comprimido paga 28 B de cabecera IP+UDP del túnel que en
PDCP no existirían. Ver docs/08-compresion-paquetes.md.

Solo biblioteca estándar: corre en python:3.12-alpine sin instalar nada.
"""
from __future__ import annotations

import fcntl, os, select, socket, struct, subprocess, sys, threading, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compresores import CODECS, Descompresor, Modelo
from bandido import EmisorAdaptativo

TUNSETIFF, IFF_TUN, IFF_NO_PI = 0x400454CA, 0x0001, 0x1000
PUERTO = int(os.environ.get("PUERTO", "7700"))
MTU = int(os.environ.get("CMP_MTU", "1400"))   # 1464 de uesimtun0 − 28 del túnel − margen del marco
KEEPALIVE = b"\xff"                              # mantiene viva la entrada NAT del UPF
PUERTOS_SUMIDERO_UDP = (5683, 9000, 4000, 5004)  # los de trafico.py: aquí se descartan
PUERTOS_SUMIDERO_TCP = (443, 8080)


def abrir_tun(nombre: str, ip: str) -> int:
    fd = os.open("/dev/net/tun", os.O_RDWR)
    fcntl.ioctl(fd, TUNSETIFF, struct.pack("16sH", nombre.encode(), IFF_TUN | IFF_NO_PI))
    for cmd in (f"ip addr add {ip}/30 dev {nombre}", f"ip link set dev {nombre} mtu {MTU}",
                f"ip link set dev {nombre} up"):
        subprocess.run(cmd.split(), check=False)
    return fd


def sumideros() -> None:
    """Descarta lo que llega a los puertos del generador (como /dev/null de red)."""
    def udp(p):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", p))
        while True:
            s.recv(65535)

    def tcp(p):
        s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", p)); s.listen(16)
        while True:
            c, _ = s.accept()
            threading.Thread(target=lambda c=c: [None for _ in iter(lambda: c.recv(65535), b"")], daemon=True).start()

    for p in PUERTOS_SUMIDERO_UDP:
        threading.Thread(target=udp, args=(p,), daemon=True).start()
    for p in PUERTOS_SUMIDERO_TCP:
        threading.Thread(target=tcp, args=(p,), daemon=True).start()


class Metricas:
    def __init__(self, lado: str):
        self.lado = lado
        self.c = {"orig_tx": 0, "marco_tx": 0, "orig_rx": 0, "marco_rx": 0, "errores": 0}
        self.codec = {n: 0 for n in CODECS.values()}

    def texto(self) -> str:
        l = f'lado="{self.lado}"'
        s = "".join(f"lab5g_cmp_bytes{{{l},sentido=\"{k[-2:]}\",tipo=\"{k[:-3]}\"}} {v}\n"
                    for k, v in self.c.items() if k != "errores")
        s += f"lab5g_cmp_errores{{{l}}} {self.c['errores']}\n"
        s += "".join(f'lab5g_cmp_paquetes{{{l},codec="{n}"}} {v}\n' for n, v in self.codec.items())
        return s

    def publicar(self) -> None:
        url = os.environ.get("PUSHGATEWAY")
        tx, mt = self.c["orig_tx"], self.c["marco_tx"]
        print(f"[cmp-{self.lado}] tx {tx} B -> {mt} B ({100 * (1 - mt / tx) if tx else 0:.1f} % menos) | "
              f"rx {self.c['marco_rx']} B -> {self.c['orig_rx']} B | errores {self.c['errores']} | {self.codec}",
              flush=True)
        if url:
            try:
                req = urllib.request.Request(f"{url}/metrics/job/cmp_shim/lado/{self.lado}",
                                             data=self.texto().encode(), method="PUT")
                urllib.request.urlopen(req, timeout=3).read()
            except OSError as e:
                print(f"[cmp-{self.lado}] pushgateway: {e}", flush=True)


def esperar_interfaz(nombre: str, segundos: int = 300) -> None:
    for _ in range(segundos):
        if Path(f"/sys/class/net/{nombre}").exists():
            return
        time.sleep(1)
    sys.exit(f"{nombre} no apareció en {segundos} s")


def ip_de(nombre: str, segundos: int = 300) -> str:
    """IPv4 de una interfaz (espera a que la tenga). En el UE hace falta: UERANSIM solo
    enruta por uesimtun0 lo que sale CON la IP de la sesión PDU (ip rule from 10.60.x)."""
    import fcntl
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for _ in range(segundos):
        try:
            r = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", nombre.encode()[:15]))  # SIOCGIFADDR
            return socket.inet_ntoa(r[20:24])
        except OSError:
            time.sleep(1)
    sys.exit(f"{nombre} no tiene IPv4 tras {segundos} s")


def main() -> None:
    lado = sys.argv[1] if len(sys.argv) > 1 else ""
    if lado not in ("ue", "dn"):
        sys.exit("uso: shim.py ue|dn")
    modelo = Modelo.cargar(os.environ.get("MODELO", Path(__file__).with_name("modelo.json")))
    if lado == "ue":
        esperar_interfaz("uesimtun0")  # sin sesión PDU no hay por dónde mandar nada
    tun = abrir_tun("cmp0", "10.99.0.1" if lado == "ue" else "10.99.0.2")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((ip_de("uesimtun0") if lado == "ue" else "0.0.0.0", PUERTO))
    par = (os.environ.get("PAR", "10.100.204.30"), PUERTO) if lado == "ue" else None
    if lado == "dn":
        sumideros()
    emisor = EmisorAdaptativo(modelo, reinicio_flujo=int(os.environ.get("REINICIO_FLUJO", "16")))
    rx = Descompresor(modelo)
    met = Metricas(lado)
    ultimo_pub = ultimo_ka = 0.0
    print(f"[cmp-{lado}] listo: cmp0 ↔ UDP {PUERTO}" + (f" → {par[0]}" if par else ""), flush=True)
    while True:
        r, _, _ = select.select([tun, sock], [], [], 1.0)
        ahora = time.time()
        if tun in r:
            pkt = os.read(tun, 65535)
            if par is not None:
                marco, cod = emisor.enviar(pkt)
                sock.sendto(marco, par)
                met.c["orig_tx"] += len(pkt); met.c["marco_tx"] += len(marco)
                met.codec[CODECS[cod]] += 1
        if sock in r:
            marco, origen = sock.recvfrom(65535)
            if lado == "dn":
                par = origen  # la dirección del UE tras el NAT del UPF
            if marco != KEEPALIVE:
                try:
                    pkt = rx.desempaquetar(marco)
                    os.write(tun, pkt)
                    met.c["marco_rx"] += len(marco); met.c["orig_rx"] += len(pkt)
                except Exception as e:  # contexto perdido, flujo desincronizado...
                    met.c["errores"] += 1
                    if met.c["errores"] < 20:
                        print(f"[cmp-{lado}] no se pudo descomprimir: {e!r}", flush=True)
        if lado == "ue" and ahora - ultimo_ka > 15:
            sock.sendto(KEEPALIVE, par); ultimo_ka = ahora
        if ahora - ultimo_pub > 10:
            met.publicar(); ultimo_pub = ahora


if __name__ == "__main__":
    main()
