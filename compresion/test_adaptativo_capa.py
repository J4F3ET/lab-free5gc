"""Modo auto de la capa FEC: dos CapaFec("auto") en bucle local, sin red ni TUN ni sleeps.

A (emisor) manda cargas a B (receptor), que mide el BER con el canal bsc que se le asigne y le
manda informes a A. El tiempo lo pone el propio test (`ahora` de informe_pendiente)."""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canal as canal_mod
import fec
from registro_fec import (CapaFec, ESCALERA_AUTO, MARCA_INFORME, MTU_ENLACE, TUNEL, es_informe, mtu_auto,
                          mtu_con_fec, tamano_en_cable, validar_codigo)

PAYLOAD = 100      # bytes comprimidos por carga (el canal no los toca: solo la capa FEC)
INTERVALO = 2.5    # s entre rondas del reloj simulado (> INTERVALO_INFORME = 2.0)


def carga(i: int, largo: int = PAYLOAD) -> bytes:
    """Carga determinista: el primer byte es un id de códec cualquiera (la capa no lo interpreta)."""
    r = random.Random(1000 + i)
    return bytes([0x80 | 3]) + bytes(r.randrange(256) for _ in range(largo - 1))


class Bucle:
    """Enlace A→B con informes B→A y reloj simulado. Cada ronda: n cargas de A a B y, si toca, el
    informe de B a A. `desde` sigue el índice de las cargas para que todas sean distintas."""

    def __init__(self, tx: CapaFec, rx: CapaFec, n: int = 40, largo: int = PAYLOAD):
        self.tx, self.rx, self.n, self.largo = tx, rx, n, largo
        self.t = 0.0
        self.desde = 0

    def ronda(self, aplicar: bool = True):
        """Devuelve (originales, recibidas, entradas, informe). `recibidas[i]` es None si el marco
        no dio paquete. Con aplicar=False el informe se genera pero no llega a A (para probar malos)."""
        self.t += INTERVALO
        originales, recibidas, entradas = [], [], []
        for i in range(self.desde, self.desde + self.n):
            datos = carga(i, self.largo)
            marco = self.tx.emitir(datos)
            capturado: list[bytes] = []

            def procesar(comprimido: bytes, cap=capturado) -> int:
                cap.append(comprimido)
                return len(comprimido)

            entrada, error = self.rx.recibir(marco, procesar)
            originales.append(datos)
            recibidas.append(capturado[0] if capturado and error is None else None)
            entradas.append(entrada)
        self.desde += self.n
        inf = self.rx.informe_pendiente(self.t)
        if inf is not None and aplicar:
            self.tx.recibir_informe(inf[1:])
        return originales, recibidas, entradas, inf

    def correr(self, rondas: int) -> None:
        for _ in range(rondas):
            self.ronda()


def par_auto(canal_rx: str | None = None) -> tuple[CapaFec, CapaFec]:
    tx = CapaFec("auto", "ue", reloj=lambda: 0.0)
    rx = CapaFec("auto", "dn", reloj=lambda: 0.0,
                 canal=canal_mod.parse_canal(canal_rx) if canal_rx else None)
    return tx, rx


class TestBucle(unittest.TestCase):
    """Escenarios de BER: la política de A responde a los informes de B."""

    def test_ber_cero_sube_a_hamming(self):
        tx, rx = par_auto()
        self.assertEqual(tx.codigo.nombre, "secded_8_4")     # arranque conservador
        b = Bucle(tx, rx)
        b.correr(6)
        self.assertEqual(tx.codigo.nombre, "hamming_15_11")
        self.assertEqual(tx.cambios_codigo, 1)
        self.assertGreaterEqual(tx.informes["recibidos"], 3)  # histéresis: espera=3 informes seguidos

    def test_ber_alto_fuerza_secded_8_4(self):
        tx, rx = par_auto()
        b = Bucle(tx, rx)
        b.correr(6)                                            # primero baja a hamming (BER 0)
        self.assertEqual(tx.codigo.nombre, "hamming_15_11")
        rx.canal = canal_mod.parse_canal("bsc:5e-3")
        b.correr(10)
        self.assertEqual(tx.codigo.nombre, "secded_8_4")
        self.assertGreaterEqual(tx.cambios_codigo, 2)   # hamming→secded_8_4, posible paso por secded_16_11

    def test_histeresis_ida_y_vuelta(self):
        tx, rx = par_auto("bsc:1e-4")
        b = Bucle(tx, rx)
        b.correr(12)
        self.assertEqual(tx.codigo.nombre, "hamming_15_11")
        rx.canal = canal_mod.parse_canal("bsc:5e-3")
        b.correr(6)
        self.assertEqual(tx.codigo.nombre, "secded_8_4")
        rx.canal = canal_mod.parse_canal("bsc:1e-4")
        codigos = []
        for _ in range(16):
            b.ronda()
            codigos.append(tx.codigo.nombre)
        self.assertEqual(codigos[0], "secded_8_4")   # no baja de golpe: el BER aún está alto
        self.assertEqual(codigos[-1], "hamming_15_11")

    def test_bsc_bajo_decodifica_todo_con_contenido_identico(self):
        tx, rx = par_auto("bsc:1e-4")
        b = Bucle(tx, rx)
        for _ in range(10):
            originales, recibidas, entradas, _inf = b.ronda()
            for o, r, e in zip(originales, recibidas, entradas):
                self.assertNotEqual(e["estado"], "invalido")
                self.assertTrue(e["integro"] or e["no_corregibles"] > 0)
                if e["no_corregibles"] == 0:
                    self.assertEqual(r, o)

    def test_informe_malo_no_cambia_el_codigo(self):
        tx, rx = par_auto()
        b = Bucle(tx, rx)
        _o, _r, _e, inf = b.ronda(aplicar=False)
        self.assertIsNotNone(inf)
        alterado = bytearray(inf[1:])
        alterado[4] ^= 0x10                       # rompe el CRC8 del informe
        self.assertFalse(tx.recibir_informe(bytes(alterado)))
        self.assertEqual(tx.informes["malos"], 1)
        self.assertEqual(tx.codigo.nombre, "secded_8_4")
        self.assertFalse(tx.recibir_informe(b"corto"))  # largo incorrecto: también malo
        self.assertEqual(tx.informes["malos"], 2)
        self.assertTrue(tx.recibir_informe(inf[1:]))    # el bueno sí se aplica
        self.assertEqual(tx.informes["recibidos"], 1)


class TestInformes(unittest.TestCase):
    def test_intervalo_y_muestras_nuevas(self):
        tx, rx = par_auto()
        self.assertIsNone(rx.informe_pendiente(0.0))     # sin muestras no hay informe
        b = Bucle(tx, rx)
        _o, _r, _e, inf = b.ronda(aplicar=False)         # informe en t=2.5
        self.assertIsNotNone(inf)
        t = b.t
        self.assertIsNone(rx.informe_pendiente(t + 0.5))  # sin muestras nuevas
        rx.recibir(tx.emitir(carga(999)), lambda c: len(c))
        self.assertIsNone(rx.informe_pendiente(t + 1.0))  # hay muestras, pero solo 1 s desde el último
        self.assertIsNotNone(rx.informe_pendiente(t + 2.0))

    def test_informe_con_marca_y_largo(self):
        tx, rx = par_auto()
        inf = Bucle(tx, rx).ronda(aplicar=False)[3]
        self.assertEqual(len(inf), 13)
        self.assertEqual(inf[0], MARCA_INFORME)
        self.assertTrue(es_informe(inf))
        self.assertFalse(es_informe(b"\x03" + bytes(10)))

    def test_fijo_no_informa_ni_acepta_informes(self):
        capa = CapaFec("hamming_7_4", "dn")
        self.assertIsNone(capa.informe_pendiente(10.0))
        self.assertFalse(capa.recibir_informe(bytes(12)))
        self.assertFalse(capa.auto)


class TestEscalera(unittest.TestCase):
    def test_mtu_auto_es_el_minimo_de_la_escalera(self):
        self.assertEqual(mtu_auto(), 677)
        self.assertEqual(CapaFec("auto", "ue").mtu(), 677)
        self.assertEqual(mtu_con_fec("secded_8_4"), 677)
        self.assertEqual(CapaFec("hamming_7_4", "ue").mtu(), 780)   # sin auto, el MTU del código fijo

    def test_marcos_de_mtu_caben_en_el_enlace(self):
        mtu = mtu_auto()
        for nombre in ESCALERA_AUTO:
            self.assertLessEqual(tamano_en_cable(nombre, mtu), MTU_ENLACE - TUNEL)
            marco = CapaFec(nombre, "ue").emitir(bytes(mtu))
            self.assertLessEqual(len(marco), MTU_ENLACE - TUNEL)

    def test_series_precargadas_para_los_tres_codigos(self):
        lineas = CapaFec("auto", "dn").texto('lado="dn"').splitlines()
        for nombre, r in (("hamming_15_11", 4), ("secded_16_11", 5), ("secded_8_4", 4)):
            sind = [x for x in lineas if x.startswith("lab5g_cmp_fec_sindrome_total") and f'codigo="{nombre}"' in x]
            self.assertEqual(len(sind), 2 ** r - 1)
            self.assertTrue(all(x.endswith(" 0") for x in sind))
            paq = [x for x in lineas if x.startswith("lab5g_cmp_fec_paquetes_total") and f'codigo="{nombre}"' in x]
            self.assertTrue(paq and all(x.endswith(" 0") for x in paq))
            self.assertTrue(any('estado="invalido"' in x for x in paq))

    def test_validar_codigo_acepta_auto(self):
        self.assertEqual(validar_codigo("auto").nombre, "secded_8_4")
        self.assertEqual(validar_codigo(" AUTO ").nombre, "secded_8_4")
        with self.assertRaisesRegex(ValueError, "auto"):
            validar_codigo("cosa")


class TestMetricas(unittest.TestCase):
    def test_texto_solo_en_auto_tiene_las_nuevas(self):
        auto = CapaFec("auto", "dn").texto('lado="dn"')
        self.assertIn('lab5g_cmp_fec_ber_estimado{lado="dn"} 0.0', auto)
        self.assertIn('lab5g_cmp_fec_codigo_activo{lado="dn",codigo="secded_8_4"} 1', auto)
        self.assertIn('lab5g_cmp_fec_codigo_activo{lado="dn",codigo="hamming_15_11"} 0', auto)
        self.assertIn('lab5g_cmp_fec_cambios_codigo_total{lado="dn"} 0', auto)
        self.assertIn('lab5g_cmp_fec_informes_total{lado="dn",tipo="enviados"} 0', auto)
        fijo = CapaFec("hamming_7_4", "dn").texto('lado="dn"')
        for nombre in ("lab5g_cmp_fec_ber_estimado", "lab5g_cmp_fec_codigo_activo",
                       "lab5g_cmp_fec_cambios_codigo_total", "lab5g_cmp_fec_informes_total"):
            self.assertNotIn(nombre, fijo)

    def test_contadores_tras_bucle_y_etiqueta_real(self):
        def suma(texto: str, prefijo: str) -> int:
            return sum(int(x.rsplit(" ", 1)[1]) for x in texto.splitlines() if x.startswith(prefijo))

        tx, rx = par_auto()
        Bucle(tx, rx).correr(6)                 # tx pasa de secded_8_4 a hamming a mitad del bucle
        texto_rx = rx.texto('lado="dn"')
        # cada marco cuenta con el código que traía en su cabecera, no con el vigente de rx
        self.assertGreater(suma(texto_rx, 'lab5g_cmp_fec_paquetes_total{lado="dn",codigo="secded_8_4",estado="limpio"'), 0)
        self.assertGreater(suma(texto_rx, 'lab5g_cmp_fec_paquetes_total{lado="dn",codigo="hamming_15_11",estado="limpio"'), 0)
        # rx envía 6 informes y no recibe ninguno; tx recibe 6 y cambia de código una vez
        self.assertIn('lab5g_cmp_fec_informes_total{lado="dn",tipo="enviados"} 6', texto_rx)
        self.assertIn('lab5g_cmp_fec_informes_total{lado="dn",tipo="recibidos"} 0', texto_rx)
        texto_tx = tx.texto('lado="ue"')
        self.assertIn('lab5g_cmp_fec_informes_total{lado="ue",tipo="recibidos"} 6', texto_tx)
        self.assertIn('lab5g_cmp_fec_cambios_codigo_total{lado="ue"} 1', texto_tx)

    def test_responder_codigo_en_auto(self):
        capa = CapaFec("auto", "ue")
        r = capa.responder("CODIGO")
        self.assertTrue(r["auto"])
        self.assertEqual(r["codigo_activo"], "secded_8_4")
        self.assertEqual(r["escalera"], list(ESCALERA_AUTO))
        self.assertEqual(r["ber_estimado"], 0.0)
        fijo = CapaFec("hamming_7_4", "ue").responder("CODIGO")
        self.assertFalse(fijo["auto"])
        self.assertIsNone(fijo["escalera"])
        self.assertEqual(fijo["codigo"], "hamming_7_4")


class TestConstantes(unittest.TestCase):
    def test_marca_no_es_id_de_codigo(self):
        self.assertNotIn(MARCA_INFORME, fec.CODIGOS)
        self.assertEqual(MARCA_INFORME, 0xFF)


if __name__ == "__main__":
    unittest.main()
