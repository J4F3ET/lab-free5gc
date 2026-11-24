"""Reenvío por NACK (REENVIO=1): dos CapaFec en bucle local, sin red, sin TUN y sin sleeps.

El emisor (ue) guarda cada marco limpio; el receptor (dn) descarta lo no corregible y pide la secuencia
por NACK; el emisor reenvía. El reloj lo pone el propio test (`t`)."""
from __future__ import annotations

import random
import sys
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canal as canal_mod
import fec
from registro_fec import (CLAVES_REENVIO, MARCA_INFORME, MARCA_NACK, CapaFec, codificar_nack,
                          decodificar_nack, es_informe, es_nack)

PAYLOAD = 100      # bytes comprimidos por carga (la capa FEC no los interpreta)


def carga(i: int, largo: int = PAYLOAD) -> bytes:
    """Carga determinista: el primer byte es un id de códec cualquiera."""
    r = random.Random(5000 + i)
    return bytes([0x80 | 3]) + bytes(r.randrange(256) for _ in range(largo - 1))


def entregar(rx: CapaFec, marco: bytes):
    """Recibe un marco y devuelve (lo que llegó a la TUN, entrada de registro, excepción)."""
    salida: list[bytes] = []

    def procesar(comprimido: bytes) -> int:
        salida.append(comprimido)
        return len(comprimido)

    entrada, error = rx.recibir(marco, procesar)
    return salida, entrada, error


class TestReenvio(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.tx = CapaFec("secded_8_4", "ue", reloj=lambda: self.t[0], reenvio=True)
        self.rx = CapaFec("secded_8_4", "dn", reloj=lambda: self.t[0], reenvio=True)

    def test_a_doble_error_no_entrega_y_el_reenvio_limpio_si(self):
        datos = carga(1)
        marco = self.tx.emitir(datos)
        # Posiciones 76 y 77: los dos bits de mensaje de la palabra 8 del cuerpo (todas en la misma
        # palabra de 8 bits). SECDED detecta el doble error y no lo corrige.
        self.rx.armar({"posiciones": [76, 77]})
        salida, e, err = entregar(self.rx, marco)
        self.assertEqual(salida, [])                      # nada llega a la TUN
        self.assertIsNone(err)
        self.assertEqual(e["estado"], "no_corregible")
        self.assertFalse(e["integro"])
        nack = self.rx.nack_pendiente()
        self.assertIsNotNone(nack)
        self.assertEqual(decodificar_nack(nack), [e["secuencia"]])
        self.assertEqual(decodificar_nack(nack), [0])
        self.assertTrue(self.tx.recibir_nack(nack))
        reenvios = self.tx.reenvios_pendientes()          # emisor sin canal ni armada: marco limpio
        self.assertEqual(reenvios, [marco])
        salida, e2, err2 = entregar(self.rx, reenvios[0])
        self.assertEqual(salida, [datos])                 # el segundo envío se entrega idéntico
        self.assertIsNone(err2)
        self.assertEqual(e2["estado"], "limpio")
        self.assertTrue(e2["integro"])
        self.assertEqual(self.tx.reenvio_stats["reenviados"], 1)
        self.assertEqual(self.tx.secuencia, 1)            # un reenvío no avanza la secuencia

    def test_b_sin_reenvio_entrega_como_hoy(self):
        tx = CapaFec("secded_8_4", "ue", reloj=lambda: self.t[0])
        rx = CapaFec("secded_8_4", "dn", reloj=lambda: self.t[0])
        datos = carga(1)
        marco = tx.emitir(datos)
        rx.armar({"posiciones": [76, 77]})
        salida, e, err = entregar(rx, marco)
        self.assertEqual(len(salida), 1)                  # se escribe igual que antes, aunque esté mal
        self.assertNotEqual(salida[0], datos)
        self.assertEqual(e["estado"], "no_corregible")
        self.assertTrue(e["integro"])
        self.assertIsNone(rx.nack_pendiente())
        self.assertIsNone(err)

    def test_c_nack_con_crc_malo_no_hace_nada(self):
        self.tx.emitir(carga(0))
        nack = codificar_nack([0])
        malo = bytearray(nack)
        malo[-1] ^= 0x01
        self.assertFalse(self.tx.recibir_nack(bytes(malo)))
        self.assertFalse(self.tx.recibir_nack(nack[:-1]))
        self.assertEqual(self.tx.reenvio_stats["nack_malos"], 2)
        self.assertEqual(self.tx.reenvio_stats["reenviados"], 0)
        self.assertEqual(self.tx.reenvios_pendientes(), [])
        self.assertFalse(CapaFec("secded_8_4", "ue").recibir_nack(nack))   # sin reenvío: ignora

    def test_d_secuencia_fuera_del_anillo_cuenta_sin_copia(self):
        self.tx.emitir(carga(0))
        self.assertTrue(self.tx.recibir_nack(codificar_nack([999])))
        self.assertEqual(self.tx.reenvio_stats["sin_copia"], 1)
        self.assertEqual(self.tx.reenvios_pendientes(), [])

    def test_e_maximo_dos_reenvios_por_secuencia(self):
        self.tx.emitir(carga(0))
        for t in (0.0, 1.0, 2.0):                         # separados para no tocar el límite por segundo
            self.t[0] = t
            self.assertTrue(self.tx.recibir_nack(codificar_nack([0])))
        self.assertEqual(len(self.tx.reenvios_pendientes()), 2)
        self.assertEqual(self.tx.reenvio_stats["nack_recibidos"], 3)
        self.assertEqual(self.tx.reenvio_stats["reenviados"], 2)

    def test_f_limite_de_ocho_reenvios_por_segundo(self):
        for i in range(20):
            self.tx.emitir(carga(i))
        self.t[0] = 0.0
        self.assertTrue(self.tx.recibir_nack(codificar_nack(range(0, 8))))
        self.assertTrue(self.tx.recibir_nack(codificar_nack(range(8, 16))))
        self.assertEqual(self.tx.reenvio_stats["descartados_limite"], 8)
        self.assertEqual(len(self.tx.reenvios_pendientes()), 8)
        self.t[0] = 1.0                                   # ya ha salido la ventana de 1 s
        self.assertTrue(self.tx.recibir_nack(codificar_nack(range(16, 20))))
        self.assertEqual(self.tx.reenvio_stats["descartados_limite"], 8)
        self.assertEqual(len(self.tx.reenvios_pendientes()), 4)

    def test_g_anillo_de_128_con_evicion(self):
        for i in range(130):
            self.tx.emitir(carga(i))
        self.assertEqual(len(self.tx._anillo), 128)
        self.assertTrue(self.tx.recibir_nack(codificar_nack([0, 1])))   # expulsadas
        self.assertEqual(self.tx.reenvio_stats["sin_copia"], 2)
        self.assertTrue(self.tx.recibir_nack(codificar_nack([129])))    # todavía está
        self.assertEqual(len(self.tx.reenvios_pendientes()), 1)

    def test_h_hamming_no_produce_nack(self):
        tx = CapaFec("hamming_15_11", "ue", reloj=lambda: self.t[0], reenvio=True)
        rx = CapaFec("hamming_15_11", "dn", reloj=lambda: self.t[0], reenvio=True,
                     canal=canal_mod.parse_canal("bsc:2e-2"))
        for i in range(30):
            _salida, e, _err = entregar(rx, tx.emitir(carga(i)))
            self.assertNotEqual(e["estado"], "no_corregible")   # Hamming no detecta doble error como tal
        self.assertIsNone(rx.nack_pendiente())
        self.assertEqual(rx.reenvio_stats["nack_enviados"], 0)

    def test_i_marcos_fec_y_nack_no_se_confunden(self):
        self.assertNotIn(MARCA_NACK, fec.CODIGOS)
        self.assertNotEqual(MARCA_NACK, MARCA_INFORME)
        for cid in fec.CODIGOS:
            marco = bytes([cid]) + bytes(20)
            self.assertFalse(es_nack(marco))
        for nombre in ("secded_8_4", "hamming_15_11", "secded_16_11", "hamming_7_4", "repeticion_5_1"):
            marco = CapaFec(nombre, "ue").emitir(carga(3))
            self.assertFalse(es_nack(marco))
            self.assertFalse(es_informe(marco))
        nack = codificar_nack([5])
        self.assertTrue(es_nack(nack))
        self.assertFalse(es_informe(nack))

    def test_j_texto_y_responder_solo_con_reenvio(self):
        texto = self.tx.texto('lado="ue"')
        for tipo in CLAVES_REENVIO:
            self.assertIn(f'lab5g_cmp_fec_reenvio_total{{lado="ue",tipo="{tipo}"}} 0', texto)
        self.assertNotIn("lab5g_cmp_fec_reenvio_total", CapaFec("secded_8_4", "ue").texto('lado="ue"'))
        r = self.tx.responder("CODIGO")
        self.assertTrue(r["reenvio"])
        self.assertEqual(set(r["reenvio_stats"]), set(CLAVES_REENVIO))
        r0 = CapaFec("secded_8_4", "ue").responder("CODIGO")
        self.assertFalse(r0["reenvio"])
        self.assertNotIn("reenvio_stats", r0)

    def test_k_bucle_con_bsc_reenvio_entrega_mas(self):
        def correr(con_reenvio: bool) -> int:
            t = [0.0]
            tx = CapaFec("secded_8_4", "ue", reloj=lambda: t[0], reenvio=con_reenvio)
            rx = CapaFec("secded_8_4", "dn", reloj=lambda: t[0], reenvio=con_reenvio,
                         canal=canal_mod.parse_canal("bsc:1e-2"), semilla_canal=7)
            originales: dict[int, bytes] = {}
            ok: set[int] = set()
            cola: deque[bytes] = deque()

            def llegar(marco: bytes) -> None:
                salida, e, err = entregar(rx, marco)
                if salida and err is None and originales.get(e["secuencia"]) == salida[0]:
                    ok.add(e["secuencia"])

            for i in range(200):
                t[0] += 0.1                               # 10 paquetes por segundo
                datos = carga(i)
                originales[tx.secuencia] = datos
                cola.append(tx.emitir(datos))
                while cola:
                    llegar(cola.popleft())
                    if con_reenvio:
                        while (nack := rx.nack_pendiente()) is not None:
                            tx.recibir_nack(nack)
                            cola.extend(tx.reenvios_pendientes())
            return len(ok)

        sin = correr(False)
        con = correr(True)
        self.assertGreater(con, sin)
        self.assertGreater(sin, 0)

    def _doble_error(self, marco: bytes) -> dict:
        """Entrega `marco` al receptor con un doble error armado en la misma palabra (no corregible)."""
        self.rx.armar({"posiciones": [76, 77]})
        _salida, e, _err = entregar(self.rx, marco)
        return e

    def test_l_entrada_reenviada_lleva_marca_y_la_descartada_no(self):
        datos = carga(1)
        marco = self.tx.emitir(datos)
        descartada = self._doble_error(marco)
        self.assertIs(descartada["reenviado"], False)
        self.assertTrue(self.tx.recibir_nack(self.rx.nack_pendiente()))
        reenvio = self.tx.reenvios_pendientes()[0]
        _salida, e2, _err = entregar(self.rx, reenvio)
        self.assertIs(e2["reenviado"], True)
        self.assertEqual(e2["estado"], "limpio")          # el estado sigue siendo el real
        self.assertEqual(self.rx.registro.contadores()["reenviados"], 1)
        self.assertEqual(self.rx._nack_secuencias, {})    # la secuencia se cumplió y sale del recuerdo

    def test_m_ttl_de_30_s_para_reconocer_el_reenvio(self):
        marco = self.tx.emitir(carga(1))
        self._doble_error(marco)                          # NACK de la secuencia 0 a t=0
        self.rx.nack_pendiente()
        self.t[0] = 29.0
        self.tx.recibir_nack(codificar_nack([0]))
        tardio = self.tx.reenvios_pendientes()[0]
        self.assertIs(entregar(self.rx, tardio)[1]["reenviado"], True)      # dentro de TTL
        self.t[0] = 0.0
        marco2 = self.tx.emitir(carga(2))
        self._doble_error(marco2)                         # NACK de la secuencia 1 a t=0
        self.rx.nack_pendiente()
        self.t[0] = 31.0                                  # caducada
        self.tx.recibir_nack(codificar_nack([1]))
        tarde = self.tx.reenvios_pendientes()[0]
        self.assertIs(entregar(self.rx, tarde)[1]["reenviado"], False)

    def test_n_recuerdo_limitado_a_64_secuencias(self):
        for i in range(70):
            self._doble_error(self.tx.emitir(carga(i)))
            self.rx.nack_pendiente()                      # vacía la cola: cada secuencia se recuerda
        self.assertEqual(len(self.rx._nack_secuencias), 64)
        self.assertNotIn(0, self.rx._nack_secuencias)     # la más vieja se olvidó
        self.assertIn(69, self.rx._nack_secuencias)
        self.assertFalse(self.rx._es_reenvio(0))

    def test_o_sin_reenvio_ninguna_entrada_reenviada(self):
        tx = CapaFec("secded_8_4", "ue", reloj=lambda: self.t[0])
        rx = CapaFec("secded_8_4", "dn", reloj=lambda: self.t[0])
        for i in range(3):
            rx.armar({"posiciones": [76, 77]})
            _salida, e, _err = entregar(rx, tx.emitir(carga(i)))
            self.assertIs(e["reenviado"], False)
        self.assertEqual(rx.registro.contadores()["reenviados"], 0)
        self.assertEqual(rx._nack_secuencias, {})
        self.assertIn("reenviados", rx.registro.contadores())

    def test_p_reenvio_que_vuelve_a_fallar_se_pide_otra_vez(self):
        marco = self.tx.emitir(carga(1))
        self._doble_error(marco)
        self.assertTrue(self.tx.recibir_nack(self.rx.nack_pendiente()))
        reenvio = self.tx.reenvios_pendientes()[0]
        e2 = self._doble_error(reenvio)                   # el reenvío vuelve con doble error
        self.assertIs(e2["reenviado"], True)
        self.assertEqual(e2["estado"], "no_corregible")
        self.assertIsNotNone(self.rx.nack_pendiente())    # se pide la secuencia de nuevo
        self.assertIn(0, self.rx._nack_secuencias)
        self.assertEqual(self.rx.registro.contadores()["reenviados"], 1)


if __name__ == "__main__":
    unittest.main()
