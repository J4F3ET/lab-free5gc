import streamlit as st
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict

st.set_page_config(page_title="Ambulancia Inteligente 5G + RL", layout="wide")

st.title("Sistema Inteligente de Comunicaciones 5G + Reinforcement Learning")
st.markdown("### Caso de estudio: Ambulancia inteligente")
st.markdown("Comparación de red 4G tradicional vs. red 5G apoyada por Reinforcement Learning para tráfico de emergencia URLLC y eMBB.")

# ==========================================
# PARÁMETROS GENERALES Y CAPACIDAD
# ==========================================
st.sidebar.header("Configuración de Red")
NUM_USERS = st.sidebar.number_input("Número de Usuarios", value=10)
SIMULATION_SLOTS = st.sidebar.number_input("Slots de Simulación por Episodio", value=20)
ARRIVAL_PROB = st.sidebar.slider("Probabilidad de Llegada", 0.0, 1.0, 0.30)
MAX_QUEUE = st.sidebar.number_input("Tamaño máximo de cola", value=5)
URLLC_DEADLINE = st.sidebar.number_input("Límite de espera URLLC", value=4)

CAPACITY_4G = st.sidebar.number_input("Capacidad 4G (paquetes/instante)", value=1)
CAPACITY_5G = st.sidebar.number_input("Capacidad 5G (paquetes/instante)", value=2)

ALPHA = 0.15
GAMMA = 0.90
EPSILON_START = 1.0
EPSILON_MIN = 0.05
EPSILON_DECAY = 0.9995

# ==========================================
# AGENTE DE REINFORCEMENT LEARNING
# ==========================================
class QLearningAgent:
    def __init__(self, alpha=ALPHA, gamma=GAMMA, epsilon=EPSILON_START):
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.q_table = defaultdict(lambda: np.zeros(3))

    def choose_action(self, state, training=True):
        if training and np.random.rand() < self.epsilon:
            return np.random.randint(3)
        else:
            return int(np.argmax(self.q_table[state]))

    def learn(self, state, action, reward, next_state):
        current_q = self.q_table[state][action]
        best_future_q = np.max(self.q_table[next_state])
        new_q = current_q + self.alpha * (reward + self.gamma * best_future_q - current_q)
        self.q_table[state][action] = new_q

    def decay_epsilon(self):
        self.epsilon = max(EPSILON_MIN, self.epsilon * EPSILON_DECAY)

# ==========================================
# FUNCIONES DEL ENTORNO
# ==========================================
def get_state(queues, delays):
    total_packets = min(int(queues.sum()) // 3, 6)
    urgent_users = min(int(np.sum(delays >= 2)), 5)
    max_delay = min(int(delays.max()), URLLC_DEADLINE)
    return (total_packets, urgent_users, max_delay)

def select_users(queues, delays, action, capacity):
    available_users = np.flatnonzero(queues > 0)
    if len(available_users) == 0:
        return []

    if action == 0:
        key_function = lambda user: (delays[user], queues[user])
    elif action == 1:
        key_function = lambda user: (queues[user], delays[user])
    else:
        key_function = lambda user: (2 * delays[user] + queues[user], delays[user], queues[user])

    selected_users = sorted(available_users, key=key_function, reverse=True)
    return selected_users[:capacity]

def reset_environment():
    return np.zeros(NUM_USERS, dtype=int), np.zeros(NUM_USERS, dtype=int)

def environment_step(queues, delays, rng, action, capacity):
    queues = queues.copy()
    delays = delays.copy()

    arrivals = (rng.random(NUM_USERS) < ARRIVAL_PROB).astype(int)
    generated_packets = int(arrivals.sum())

    overflow = np.maximum(queues + arrivals - MAX_QUEUE, 0)
    overflow_lost = int(overflow.sum())
    queues = np.minimum(queues + arrivals, MAX_QUEUE)

    delays = np.where(queues > 0, delays + 1, 0)

    selected_users = select_users(queues, delays, action, capacity)

    delivered = 0
    total_latency = 0
    urgent_delivered = 0

    for user in selected_users:
        if queues[user] > 0:
            delivered += 1
            total_latency += int(delays[user])
            if delays[user] >= 2:
                urgent_delivered += 1
            queues[user] -= 1
            delays[user] = 0

    expired = ((queues > 0) & (delays >= URLLC_DEADLINE))
    deadline_lost = int(queues[expired].sum())
    queues[expired] = 0
    delays[expired] = 0

    lost_packets = overflow_lost + deadline_lost

    reward = (15 * delivered - 2 * total_latency + 8 * urgent_delivered - 25 * lost_packets)
    if len(selected_users) < capacity:
        reward -= 2 * (capacity - len(selected_users))

    return queues, delays, reward, generated_packets, delivered, total_latency, lost_packets

def simulate_4G(rng):
    queues, delays = reset_environment()
    gen_tot = del_tot = lat_tot = lost_tot = 0
    for _ in range(SIMULATION_SLOTS):
        queues, delays, _, gen, dlv, lat, lst = environment_step(queues, delays, rng, action=0, capacity=CAPACITY_4G)
        gen_tot += gen; del_tot += dlv; lat_tot += lat; lost_tot += lst
    return (gen_tot, del_tot, lat_tot, lost_tot)

def simulate_5G_RL(rng, agent):
    queues, delays = reset_environment()
    gen_tot = del_tot = lat_tot = lost_tot = 0
    for _ in range(SIMULATION_SLOTS):
        state = get_state(queues, delays)
        action = agent.choose_action(state, training=False)
        queues, delays, _, gen, dlv, lat, lst = environment_step(queues, delays, rng, action, capacity=CAPACITY_5G)
        gen_tot += gen; del_tot += dlv; lat_tot += lat; lost_tot += lst
    return (gen_tot, del_tot, lat_tot, lost_tot)

# ==========================================
# EJECUCIÓN DESDE LA INTERFAZ
# ==========================================
if st.button("Iniciar Entrenamiento y Simulación", type="primary"):
    
    agent = QLearningAgent()
    EPISODES = 10000
    rewards_history, epsilon_history = [], []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for episode in range(EPISODES):
        queues, delays = reset_environment()
        rng = np.random.default_rng(episode + 12345)
        total_reward = 0

        for step in range(SIMULATION_SLOTS):
            state = get_state(queues, delays)
            action = agent.choose_action(state, training=True)
            new_queues, new_delays, reward, _, _, _, _ = environment_step(queues, delays, rng, action, CAPACITY_5G)
            new_state = get_state(new_queues, new_delays)
            agent.learn(state, action, reward, new_state)
            
            queues, delays = new_queues, new_delays
            total_reward += reward

        rewards_history.append(total_reward)
        epsilon_history.append(agent.epsilon)
        agent.decay_epsilon()

        if episode % 1000 == 0:
            progress_bar.progress(episode / EPISODES)
            status_text.text(f"Entrenando agente... Episodio {episode}/{EPISODES}")
            
    progress_bar.progress(1.0)
    status_text.text("¡Entrenamiento finalizado!")

    # SIMULACIÓN COMPARATIVA
    NUM_TESTS = 3000
    results_4G, results_5G_RL = [], []

    for test in range(NUM_TESTS):
        rng_4G = np.random.default_rng(test)
        rng_5G = np.random.default_rng(test)
        results_4G.append(simulate_4G(rng_4G))
        results_5G_RL.append(simulate_5G_RL(rng_5G, agent))

    results_4G = np.array(results_4G)
    results_5G_RL = np.array(results_5G_RL)

    generated_4G, generated_5G = np.mean(results_4G[:, 0]), np.mean(results_5G_RL[:, 0])
    delivered_4G, delivered_5G = np.mean(results_4G[:, 1]), np.mean(results_5G_RL[:, 1])
    lost_4G, lost_5G = np.mean(results_4G[:, 3]), np.mean(results_5G_RL[:, 3])
    
    latency_4G = results_4G[:, 2].sum() / max(results_4G[:, 1].sum(), 1)
    latency_5G = results_5G_RL[:, 2].sum() / max(results_5G_RL[:, 1].sum(), 1)
    
    reliability_4G = delivered_4G / max(generated_4G, 1) * 100
    reliability_5G = delivered_5G / max(generated_5G, 1) * 100

    # MOSTRAR RESULTADOS
    st.markdown("---")
    st.subheader("Resultados de la Comparación Final")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Entrega 4G vs 5G", f"{delivered_5G:.2f}", f"{delivered_5G - delivered_4G:.2f} paquetes")
    col2.metric("Pérdidas 4G vs 5G", f"{lost_5G:.2f}", f"{lost_5G - lost_4G:.2f} paquetes", delta_color="inverse")
    col3.metric("Latencia 5G+RL", f"{latency_5G:.2f} ms", f"{latency_5G - latency_4G:.2f} ms", delta_color="inverse")
    col4.metric("Confiabilidad 5G+RL", f"{reliability_5G:.2f}%", f"{reliability_5G - reliability_4G:.2f}%")

    # GRÁFICOS
    st.markdown("### Métricas de Rendimiento")
    fig, axs = plt.subplots(1, 4, figsize=(20, 5))
    labels = ["4G tradicional", "5G + RL"]

    axs[0].bar(labels, [delivered_4G, delivered_5G], color=['blue', 'green'])
    axs[0].set_title("Paquetes Entregados")

    axs[1].bar(labels, [lost_4G, lost_5G], color=['red', 'orange'])
    axs[1].set_title("Paquetes Perdidos")

    axs[2].bar(labels, [latency_4G, latency_5G], color=['purple', 'pink'])
    axs[2].set_title("Latencia Promedio")

    axs[3].bar(labels, [reliability_4G, reliability_5G], color=['cyan', 'teal'])
    axs[3].set_title("Reliability (%)")
    axs[3].set_ylim(0, 100)

    st.pyplot(fig)