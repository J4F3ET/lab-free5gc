#!/usr/bin/env python3
"""
Exportador de metricas de capa fisica y teoria de la informacion hacia el
Pushgateway del lab. Es una traduccion 1:1 (sin numpy, para no cargar el
contenedor) de D:\\Proyectos\\UD.Lab2.TeoriaInformacion\\Actividad_2.ipynb:
mismo modelo de enlace de radio (FSPL, balance de enlace, SNR/SIR/SINR),
mismo modelo de fuente (entropia de Shannon, distribucion Zipf), mismo
entorno Environment5G6G y mismo agente QLearningAgent por slice.

free5GC/UERANSIM no simulan RF real (son un core + UE de software sobre
IP), asi que esto NO mide la red del lab: es la capa teorica de la materia,
corriendo en paralelo y correlacionada por slice (eMBB/URLLC/mMTC) con el
resto del dashboard.
"""
import math
import os
import random
import time
import urllib.request

PUSHGATEWAY = os.environ.get("PUSHGATEWAY", "http://10.100.205.14:9091")
INTERVAL = float(os.environ.get("INTERVAL", "15"))
ESCENARIO = os.environ.get("ESCENARIO", "nominal")

# ==============================================================================
# 1. Parametros del sistema y estandares tecnicos (5G/6G) — NetworkConfig
# ==============================================================================
NOISE_POWER_DBM = -104.0
SPEED_OF_LIGHT_M_S = 3.0e8

POWER_LEVELS_DBM = [5.0, 15.0, 25.0, 35.0, 43.0]

# Bandas 3GPP TS 38.101-1
FREQUENCY_BANDS_MHZ = {"n1": 2100.0, "n78": 3500.0, "n258": 26000.0}

ANTENNA_GAIN_TX_DBI = 15.0
ANTENNA_GAIN_RX_DBI = 2.0

CHANNEL_STATE_DISTANCES_M = [2000.0, 1000.0, 500.0, 200.0, 50.0]

ESCENARIOS_CANAL = {
    "nominal":            {"bandwidth_mhz": 20.0,  "freq_mhz": FREQUENCY_BANDS_MHZ["n78"],  "interference_dbm": -85.0, "band": "n78"},
    "banda_estrecha":     {"bandwidth_mhz": 5.0,   "freq_mhz": FREQUENCY_BANDS_MHZ["n78"],  "interference_dbm": -85.0, "band": "n78"},
    "alta_interferencia": {"bandwidth_mhz": 20.0,  "freq_mhz": FREQUENCY_BANDS_MHZ["n78"],  "interference_dbm": -70.0, "band": "n78"},
    "mmwave_n258":        {"bandwidth_mhz": 100.0, "freq_mhz": FREQUENCY_BANDS_MHZ["n258"], "interference_dbm": -85.0, "band": "n258"},
}

SLICES = {
    "eMBB":  {"w_throughput": 0.70, "w_latency": 0.15, "w_power": 0.15, "target_lat": 10.0},
    "URLLC": {"w_throughput": 0.10, "w_latency": 0.80, "w_power": 0.10, "target_lat": 1.0},
    "mMTC":  {"w_throughput": 0.30, "w_latency": 0.20, "w_power": 0.50, "target_lat": 20.0},
}

SOURCE_ALPHABET_SIZE = {"eMBB": 8, "URLLC": 4, "mMTC": 3}
SOURCE_SKEWNESS = {"eMBB": 0.3, "URLLC": 2.5, "mMTC": 1.0}
SOURCE_SYMBOL_RATE_HZ = {"eMBB": 5.0e7, "URLLC": 2.0e8, "mMTC": 1.0e6}


# ==============================================================================
# 2. Modelo de enlace de radio (funciones puras)
# ==============================================================================
def free_space_path_loss_db(distance_km, freq_mhz):
    """FSPL: PL_dB = 20*log10(d_km) + 20*log10(f_MHz) + 32.44"""
    distance_km = max(distance_km, 1e-6)
    return 20 * math.log10(distance_km) + 20 * math.log10(freq_mhz) + 32.44


def received_power_dbm(tx_power_dbm, gt_dbi, gr_dbi, path_loss_db):
    """Balance de enlace: Prx = Ptx + Gt + Gr - PL (dBm)"""
    return tx_power_dbm + gt_dbi + gr_dbi - path_loss_db


def compute_snr_sir_sinr_db(rx_power_dbm, noise_dbm, interference_dbm):
    rx_mw = 10 ** (rx_power_dbm / 10.0)
    noise_mw = 10 ** (noise_dbm / 10.0)
    interf_mw = 10 ** (interference_dbm / 10.0)
    snr_db = 10 * math.log10(rx_mw / noise_mw)
    sir_db = 10 * math.log10(rx_mw / interf_mw)
    sinr_db = 10 * math.log10(rx_mw / (noise_mw + interf_mw))
    return snr_db, sir_db, sinr_db


def rayleigh(scale):
    """Muestreo por CDF inversa: equivalente a np.random.rayleigh(scale)."""
    u = random.random()
    return scale * math.sqrt(-2.0 * math.log(1.0 - u))


# ==============================================================================
# 3. Modelo de fuente de informacion y entropia de Shannon (funciones puras)
# ==============================================================================
def autoinformacion(p):
    p = min(max(p, 1e-12), 1.0)
    return -math.log2(p)


def entropia_fuente(probs):
    return sum(p * autoinformacion(p) for p in probs)


def extension_fuente(h, n):
    return n * h


def generar_distribucion_zipf(k, skew):
    pesos = [1.0 / (i ** skew) for i in range(1, k + 1)]
    total = sum(pesos)
    return [w / total for w in pesos]


def tasa_informacion_bps(entropia_bits_simbolo, tasa_simbolos_hz):
    return entropia_bits_simbolo * tasa_simbolos_hz


SOURCE_ENTROPY_BITS = {}
SOURCE_INFO_RATE_BPS = {}
for _slice in SLICES:
    _probs = generar_distribucion_zipf(SOURCE_ALPHABET_SIZE[_slice], SOURCE_SKEWNESS[_slice])
    _h = entropia_fuente(_probs)
    SOURCE_ENTROPY_BITS[_slice] = _h
    SOURCE_INFO_RATE_BPS[_slice] = tasa_informacion_bps(_h, SOURCE_SYMBOL_RATE_HZ[_slice])


# ==============================================================================
# 4. Entorno de simulacion de red (una instancia persistente por slice)
# ==============================================================================
class Environment5G6G:
    def __init__(self, slice_type, escenario):
        self.slice_type = slice_type
        self.slice_params = SLICES[slice_type]
        params = ESCENARIOS_CANAL[escenario]
        self.escenario = escenario
        self.bandwidth_mhz = params["bandwidth_mhz"]
        self.freq_mhz = params["freq_mhz"]
        self.band = params["band"]
        self.interference_dbm = params["interference_dbm"]
        self.source_info_rate_bps = SOURCE_INFO_RATE_BPS[slice_type]
        self.num_states = 5  # 0=Pesimo .. 4=Excelente
        self.current_channel_state = random.randrange(self.num_states)

    def _get_distance_m(self):
        base_distance = CHANNEL_STATE_DISTANCES_M[self.current_channel_state]
        return base_distance * (1.0 + rayleigh(0.15))

    def step(self, action_idx):
        power_dbm = POWER_LEVELS_DBM[action_idx]

        distance_m = self._get_distance_m()
        path_loss_db = free_space_path_loss_db(distance_m / 1000.0, self.freq_mhz)
        rx_power_dbm = received_power_dbm(power_dbm, ANTENNA_GAIN_TX_DBI, ANTENNA_GAIN_RX_DBI, path_loss_db)

        snr_db, sir_db, sinr_db = compute_snr_sir_sinr_db(rx_power_dbm, NOISE_POWER_DBM, self.interference_dbm)
        sinr_linear = 10 ** (sinr_db / 10.0)
        snr_linear = 10 ** (snr_db / 10.0)

        throughput_mbps = self.bandwidth_mhz * math.log2(1 + sinr_linear)
        capacity_theoretical_bps = self.bandwidth_mhz * 1e6 * math.log2(1 + snr_linear)

        propagation_delay_ms = (distance_m / SPEED_OF_LIGHT_M_S) * 1000.0
        packet_size_bits = 1e5
        transmission_delay_ms = (packet_size_bits / (throughput_mbps * 1e6 + 1e-6)) * 1000.0
        total_latency_ms = propagation_delay_ms + transmission_delay_ms

        meets_shannon_criterion = 1.0 if self.source_info_rate_bps <= capacity_theoretical_bps else 0.0

        w_t = self.slice_params["w_throughput"]
        w_l = self.slice_params["w_latency"]
        w_p = self.slice_params["w_power"]
        norm_tp = throughput_mbps / 200.0
        norm_lat = total_latency_ms / 50.0
        norm_pow = power_dbm / 43.0
        reward = (w_t * norm_tp) - (w_l * norm_lat) - (w_p * norm_pow)
        if total_latency_ms > self.slice_params["target_lat"]:
            reward -= 2.0

        self.current_channel_state = random.choices(range(self.num_states), weights=[0.1, 0.2, 0.4, 0.2, 0.1])[0]

        return {
            "next_state": self.current_channel_state,
            "reward": reward,
            "throughput_mbps": throughput_mbps,
            "latency_ms": total_latency_ms,
            "distance_m": distance_m,
            "freq_mhz": self.freq_mhz,
            "path_loss_db": path_loss_db,
            "tx_power_dbm": power_dbm,
            "snr_db": snr_db,
            "sir_db": sir_db,
            "sinr_db": sinr_db,
            "capacity_theoretical_bps": capacity_theoretical_bps,
            "required_rate_bps": self.source_info_rate_bps,
            "meets_shannon_criterion": meets_shannon_criterion,
            "target_lat_ms": self.slice_params["target_lat"],
        }


class QLearningAgent:
    def __init__(self, num_states, num_actions, alpha=0.1, gamma=0.95, epsilon=0.3):
        self.q_table = [[0.0] * num_actions for _ in range(num_states)]
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.num_actions = num_actions

    def choose_action(self, state):
        if random.random() < self.epsilon:
            return random.randrange(self.num_actions)
        row = self.q_table[state]
        return max(range(len(row)), key=lambda i: row[i])

    def learn(self, state, action, reward, next_state):
        predict = self.q_table[state][action]
        target = reward + self.gamma * max(self.q_table[next_state])
        self.q_table[state][action] += self.alpha * (target - predict)

    def decay_epsilon(self, decay_rate=0.995):
        self.epsilon = max(0.01, self.epsilon * decay_rate)


# ==============================================================================
# 5. Push al Pushgateway (formato de exposicion de texto, igual que iperf-probe.sh)
# ==============================================================================
def push_metrics(slice_type, escenario, band, sample):
    labels = 'slice="%s",escenario="%s",band="%s"' % (slice_type, escenario, band)
    lines = []

    def g(name, value, help_text):
        lines.append("# HELP %s %s" % (name, help_text))
        lines.append("# TYPE %s gauge" % name)
        lines.append("%s{%s} %s" % (name, labels, value))

    g("lab5g_phy_tx_power_dbm", sample["tx_power_dbm"], "Potencia de Tx elegida por el agente Q-Learning (dBm)")
    g("lab5g_phy_antenna_gain_tx_dbi", ANTENNA_GAIN_TX_DBI, "Ganancia de antena de la estacion base (dBi)")
    g("lab5g_phy_antenna_gain_rx_dbi", ANTENNA_GAIN_RX_DBI, "Ganancia de antena del equipo de usuario (dBi)")
    g("lab5g_phy_path_loss_db", sample["path_loss_db"], "Perdidas de espacio libre, FSPL (dB)")
    g("lab5g_phy_distance_m", sample["distance_m"], "Distancia UE-estacion base simulada, con fading Rayleigh (m)")
    g("lab5g_phy_frequency_mhz", sample["freq_mhz"], "Frecuencia de portadora, banda 3GPP TS 38.101-1 (MHz)")
    g("lab5g_phy_bandwidth_mhz", ESCENARIOS_CANAL[escenario]["bandwidth_mhz"], "Ancho de banda del canal (MHz)")
    g("lab5g_phy_snr_db", sample["snr_db"], "Signal-to-Noise Ratio (dB)")
    g("lab5g_phy_sir_db", sample["sir_db"], "Signal-to-Interference Ratio (dB)")
    g("lab5g_phy_sinr_db", sample["sinr_db"], "Signal-to-Interference-plus-Noise Ratio (dB)")
    g("lab5g_phy_throughput_mbps", sample["throughput_mbps"], "Throughput efectivo, Shannon-Hartley con SINR (Mbps)")
    g("lab5g_phy_capacity_theoretical_mbps", sample["capacity_theoretical_bps"] / 1e6, "Capacidad teorica de canal, Shannon-Hartley con SNR puro (Mbps)")
    g("lab5g_phy_latency_ms", sample["latency_ms"], "Latencia total: propagacion (distancia/c) + transmision (ms)")
    g("lab5g_phy_latency_sla_target_ms", sample["target_lat_ms"], "Objetivo de latencia SLA del slice (ms)")
    g("lab5g_phy_reward", sample["reward"], "Recompensa del agente Q-Learning en este paso")
    g("lab5g_info_entropy_bits_per_symbol", SOURCE_ENTROPY_BITS[slice_type], "Entropia de la fuente H(f) (bits/simbolo)")
    g("lab5g_info_entropy_block_bits", extension_fuente(SOURCE_ENTROPY_BITS[slice_type], 10), "Extension de fuente H(f^10), bloque de 10 simbolos")
    g("lab5g_info_required_rate_mbps", sample["required_rate_bps"] / 1e6, "Tasa de informacion requerida R = H(f) * Rs (Mbps)")
    g("lab5g_info_meets_shannon_criterion", sample["meets_shannon_criterion"], "1 si R <= C: criterio de comunicacion sin perdida")
    g("lab5g_info_alphabet_size", SOURCE_ALPHABET_SIZE[slice_type], "Numero de simbolos K de la fuente del slice")

    body = ("\n".join(lines) + "\n").encode("utf-8")
    url = "%s/metrics/job/phy_sim/instance/%s/escenario/%s" % (PUSHGATEWAY, slice_type, escenario)
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as exc:  # el pushgateway puede no estar listo aun al arrancar
        print("[phy-sim] push a %s fallo: %s" % (slice_type, exc), flush=True)


def main():
    envs = {s: Environment5G6G(s, ESCENARIO) for s in SLICES}
    agents = {s: QLearningAgent(num_states=5, num_actions=len(POWER_LEVELS_DBM)) for s in SLICES}
    state = {s: envs[s].current_channel_state for s in SLICES}

    for s in SLICES:
        print("[phy-sim] %s: H(f)=%.3f bits/simbolo, R=%.2f Mbps (K=%d, skew=%.1f)" % (
            s, SOURCE_ENTROPY_BITS[s], SOURCE_INFO_RATE_BPS[s] / 1e6,
            SOURCE_ALPHABET_SIZE[s], SOURCE_SKEWNESS[s]), flush=True)

    while True:
        for slice_type, env in envs.items():
            agent = agents[slice_type]
            action = agent.choose_action(state[slice_type])
            sample = env.step(action)
            agent.learn(state[slice_type], action, sample["reward"], sample["next_state"])
            state[slice_type] = sample["next_state"]
            agent.decay_epsilon()
            push_metrics(slice_type, ESCENARIO, env.band, sample)
            print("[phy-sim] %-5s tx=%.0fdBm d=%.0fm SINR=%.1fdB tp=%.1fMbps lat=%.2fms shannon=%s" % (
                slice_type, sample["tx_power_dbm"], sample["distance_m"], sample["sinr_db"],
                sample["throughput_mbps"], sample["latency_ms"],
                "OK" if sample["meets_shannon_criterion"] else "NO"), flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
