"""Pruebas de los reproductores de datos reales:  python -m unittest compresion/test_reproductores.py"""
import gzip, json, struct, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compresores as C
import reproducir_iot as RI
import reproducir_pcap as RP
import trafico as T

MODELO = C.Modelo.cargar(Path(__file__).with_name("modelo.json"))


class IoTReal(unittest.TestCase):
    LINEAS = ["2004-03-31 03:38:15.757551 2 1 122.153 -3.91901 11.04 2.03397\n",    # batería muerta: basura
              "2004-02-28 00:59:16.02785 3 1 19.9884 37.0933 45.08 2.69964\n",
              "2004-02-28 01:03:16.33393 11 1 19.3024 38.4629 45.08 2.68742\n",
              "2004-02-28 01:03:16.33393 11 1 19.3024 38.4629 45.08 2.68742\n",       # repetida
              "2004-02-28 01:06:16.013453 17 2 19.1652 38.8039 45.08\n",              # incompleta
              "2004-02-28 01:00:46.778088 4 2 19.175 38.8379 45.08 2.69964\n"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "data.txt.gz"
        with gzip.open(self.tmp, "wt") as f:
            f.writelines(self.LINEAS)

    def test_limpia_basura_incompletas_y_repetidas(self):
        lect, malas = RI.leer(self.tmp)
        self.assertEqual(len(lect), 4)
        self.assertEqual(malas, {"fuera de rango (temp)": 1, "incompleta": 1})
        sel = RI.elegir(lect, "2", 0)
        self.assertEqual([(r["sensor"], r["seq"]) for r in sel], [(1, 3), (2, 4), (1, 11)])  # por tiempo, sin repetidas

    def test_el_json_se_reproduce_byte_a_byte(self):
        """predictivo_iot solo comprime si json.dumps devuelve exactamente la carga."""
        for r in RI.elegir(RI.leer(self.tmp)[0], "2", 0):
            c = RI.carga(r)
            self.assertEqual(C._json(json.loads(c)), c)
            self.assertEqual(json.loads(c)["temp"], float(r["temp"]))

    def test_cada_sensor_es_un_flujo_aparte(self):
        sel = RI.elegir(RI.leer(self.tmp)[0], "2", 0)
        claves = {C.clave_flujo(T.paquete_udp(RI.carga(r), RI.PUERTO_BASE + r["sensor"], RI.PUERTO_IOT, 0)) for r in sel}
        self.assertEqual(len(claves), 2)


def _udp4(sp, dp, carga):
    u = struct.pack("!HHHH", sp, dp, 8 + len(carga), 0) + carga
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(u), 1, 0, 64, 17, 0, b"\x0a\0\0\1", b"\x0a\0\0\2") + u


def _tcp4(sp, dp, carga):
    t = struct.pack("!HHIIBBHHH", sp, dp, 1, 1, 5 << 4, 0x18, 1000, 0, 0) + carga
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(t), 1, 0, 64, 6, 0, b"\x0a\0\0\1", b"\x0a\0\0\2") + t


def _udp6(sp, dp, carga):
    u = struct.pack("!HHHH", sp, dp, 8 + len(carga), 0) + carga
    return struct.pack("!IHBB16s16s", 6 << 28, len(u), 17, 64, b"\0" * 15 + b"\1", b"\0" * 15 + b"\2") + u


def _eth(ip, vlan=False):
    tipo = b"\x86\xdd" if ip[0] >> 4 == 6 else b"\x08\x00"
    return b"\x02" * 6 + b"\x04" * 6 + ((b"\x81\x00\x00\x07") if vlan else b"") + tipo + ip


PAQUETES = [_eth(_udp4(5353, 53, b"\x12\x34consulta-dns")),
            _eth(_tcp4(51000, 80, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"), vlan=True),
            _eth(_tcp4(80, 51000, b"")),                      # ACK puro: se omite
            _eth(_udp6(40000, 443, b"\xc0quic" + bytes(50))),
            _eth(_tcp4(51000, 80, b"A" * 3000))]               # se parte en 3 (MTU del túnel)


def _pcap(ruta, tramas):
    with open(ruta, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for i, t in enumerate(tramas):
            f.write(struct.pack("<IIII", 1000 + i, 0, len(t), len(t)) + t)


def _pcapng(ruta, tramas):
    def bloque(tipo, cuerpo):
        cuerpo += b"\0" * (-len(cuerpo) % 4)
        n = 12 + len(cuerpo)
        return struct.pack("<II", tipo, n) + cuerpo + struct.pack("<I", n)
    with open(ruta, "wb") as f:
        f.write(bloque(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)))
        f.write(bloque(1, struct.pack("<HHI", 1, 0, 65535)))
        for t in tramas:
            f.write(bloque(6, struct.pack("<IIIII", 0, 0, 0, len(t), len(t)) + t))


class CapturaReal(unittest.TestCase):
    def _revisar(self, ruta):
        sal, omit = RP.cargas(ruta)
        self.assertEqual(omit["sin carga (ACK, SYN…)"], 1)
        self.assertEqual([s for s, _, _ in sal], ["dns", "http", "quic", "http", "http", "http"])
        self.assertEqual(sal[0][2], b"\x12\x34consulta-dns")
        self.assertEqual(b"".join(c for s, f, c in sal[3:]), b"A" * 3000)
        self.assertTrue(all(len(c) <= RP.MAX_CARGA for _, _, c in sal))
        self.assertEqual(sal[1][1], sal[3][1])        # el mismo flujo TCP

    def test_pcap_clasico_y_comprimido(self):
        d = Path(tempfile.mkdtemp())
        _pcap(d / "a.pcap", PAQUETES)
        self._revisar(d / "a.pcap")
        (d / "a.pcap.gz").write_bytes(gzip.compress((d / "a.pcap").read_bytes()))
        self._revisar(d / "a.pcap.gz")

    def test_pcapng(self):
        d = Path(tempfile.mkdtemp())
        _pcapng(d / "a.pcapng", PAQUETES)
        self._revisar(d / "a.pcapng")

    def test_las_cargas_pasan_ida_y_vuelta_por_el_compresor(self):
        d = Path(tempfile.mkdtemp()); _pcap(d / "a.pcap", PAQUETES)
        tx, rx = C.Compresor(MODELO), C.Descompresor(MODELO)
        for i, (_, fid, c) in enumerate(RP.cargas(d / "a.pcap")[0]):
            p = T.paquete_udp(c, 20000 + fid, RP.PUERTO_SUMIDERO, i)
            for cod in C.CODECS:
                marco, _ = tx.empaquetar(p, cod)
                self.assertEqual(rx.desempaquetar(marco), p)


if __name__ == "__main__":
    unittest.main()
