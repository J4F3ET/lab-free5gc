# Balance latencia ↔ banda por algoritmo (generado por `evaluar.py balance`)

3600 paquetes del flujo mezclado de PRUEBA, cada uno verificado byte a byte. Modelo: compresor → enlace → descompresor como colas M/G/1 en serie (Pollaczek–Khinchine), con los tamaños y tiempos medidos de cada paquete. Ver el encabezado de `balance.py`.

## 1. Lo que cuesta y lo que ahorra cada política

| política | ahorro de bytes % | comprimir µs/paq | descomprimir µs/paq | % mal con 1 % de pérdida |
|---|---|---|---|---|
| sin compresión | 0.0 | 0.0 | 0.0 | 0.0 |
| ROHC + ninguno | 2.42 | 9.6 | 9.6 | 0.14 |
| ROHC + huffman_estatico | 13.26 | 568.1 | 1000.4 | 0.14 |
| ROHC + deflate | 15.95 | 58.2 | 18.1 | 0.14 |
| ROHC + deflate_dict | 18.08 | 90.4 | 19.4 | 0.14 |
| ROHC + deflate_flujo | 22.47 | 114.2 | 17.7 | 3.06 |
| ROHC + tic_por_paquete | 9.18 | 2125.1 | 873.0 | 0.14 |
| ROHC + bandido UCB | 22.41 | 78.9 | 17.6 | 3.54 |

## 2. Latencia media en ms por escenario — CPU medida (Python, esta máquina)

Carga = cuánto ocuparía el enlace el tráfico SIN comprimir. **Negrita** = menor latencia. "Robusto" = el mejor entre los que reconstruyen mal ≤ 1 % con 1 % de pérdida. ∞ = alguna etapa no da abasto.

| enlace | carga | sin compresión | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | bandido UCB | gana | gana (robusto) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 kbps | 50% | 110 | 107 | 88.5 | 82.8 | 80.3 | **74.8** | 96.6 | 74.8 | deflate_flujo | deflate_dict |
| 100 kbps | 80% | 267 | 244 | 160 | 145 | 137 | **122** | 186 | 122 | deflate_flujo | deflate_dict |
| 100 kbps | 95% | 1050 | 727 | 275 | 234 | 213 | **176** | 364 | 176 | deflate_flujo | deflate_dict |
| 1 Mbps | 50% | 11 | 10.7 | 10.6 | 8.35 | 8.13 | 7.6 | 13 | **7.57** | bandido UCB | deflate_dict |
| 1 Mbps | 80% | 26.7 | 24.5 | 17.9 | 14.6 | 13.8 | 12.3 | 22.5 | **12.3** | bandido UCB | deflate_dict |
| 1 Mbps | 95% | 105 | 72.7 | 29.5 | 23.5 | 21.4 | **17.7** | 40.6 | 17.7 | deflate_flujo | deflate_dict |
| 10 Mbps | 50% | 1.1 | 1.09 | 20.6 | 0.906 | 0.917 | 0.886 | ∞ | **0.849** | bandido UCB | deflate |
| 10 Mbps | 80% | 2.67 | 2.46 | ∞ | 1.53 | 1.49 | 1.36 | ∞ | **1.32** | bandido UCB | deflate_dict |
| 10 Mbps | 95% | 10.5 | 7.29 | ∞ | 2.42 | 2.25 | 1.91 | ∞ | **1.87** | bandido UCB | deflate_dict |
| 100 Mbps | 50% | **0.11** | 0.128 | ∞ | 0.2 | 0.39 | 4.19 | ∞ | 0.327 | sin compresión | sin compresión |
| 100 Mbps | 80% | 0.267 | **0.266** | ∞ | 0.379 | ∞ | ∞ | ∞ | ∞ | ninguno | ninguno |
| 100 Mbps | 95% | 1.05 | **0.749** | ∞ | 1.06 | ∞ | ∞ | ∞ | ∞ | ninguno | ninguno |
| 1 Gbps | 50% | **0.011** | 0.113 | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | sin compresión | sin compresión |
| 1 Gbps | 80% | **0.0267** | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | sin compresión | sin compresión |
| 1 Gbps | 95% | **0.105** | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | ∞ | sin compresión | sin compresión |

## 3. Velocidad de equilibrio por clase — CPU medida (Python, esta máquina)

Con carga 50%: por debajo de esta velocidad de enlace la política tiene MENOS latencia que no comprimir. "nunca" = no ahorra lo suficiente para pagar su CPU a ninguna velocidad; "siempre" = gana en todo el rango probado (1 kbps a 100 Gbps).

| clase | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | bandido UCB |
|---|---|---|---|---|---|---|---|
| voip_rtp | 14.9 Mbps | 6.25 Mbps | 8.13 Mbps | 5.05 Mbps | 3.71 Mbps | 1.69 Mbps | 5.83 Mbps |
| http_texto | nunca | 994 kbps | 71 Mbps | 51.1 Mbps | 50.4 Mbps | 578 kbps | 43.6 Mbps |
| urllc_ctrl | 13.4 Mbps | 4.71 Mbps | 6.48 Mbps | 3.6 Mbps | 2.73 Mbps | 1.86 Mbps | 4.68 Mbps |
| video_rtp | 17.8 Mbps | 538 kbps | 5.44 Mbps | 4.31 Mbps | 3.53 Mbps | 147 kbps | 6.58 Mbps |
| iot_json | 16.8 Mbps | 1.27 Mbps | 7.74 Mbps | 9.51 Mbps | 11.1 Mbps | 1.05 Mbps | 8.73 Mbps |
| web_tls | nunca | nunca | nunca | nunca | nunca | nunca | nunca |

## 4. Latencia media en ms por escenario — CPU 10× más rápida (supuesto: implementación en C o en la estación base)

Carga = cuánto ocuparía el enlace el tráfico SIN comprimir. **Negrita** = menor latencia. "Robusto" = el mejor entre los que reconstruyen mal ≤ 1 % con 1 % de pérdida. ∞ = alguna etapa no da abasto.

| enlace | carga | sin compresión | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | bandido UCB | gana | gana (robusto) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 kbps | 50% | 110 | 107 | 87.1 | 82.8 | 80.2 | **74.7** | 93.9 | 74.8 | deflate_flujo | deflate_dict |
| 100 kbps | 80% | 267 | 244 | 159 | 145 | 137 | **121** | 183 | 122 | deflate_flujo | deflate_dict |
| 100 kbps | 95% | 1050 | 727 | 274 | 234 | 213 | **176** | 361 | 176 | deflate_flujo | deflate_dict |
| 1 Mbps | 50% | 11 | 10.7 | 8.85 | 8.28 | 8.03 | **7.48** | 9.66 | 7.48 | deflate_flujo | deflate_dict |
| 1 Mbps | 80% | 26.7 | 24.4 | 16 | 14.5 | 13.7 | **12.2** | 18.6 | 12.2 | deflate_flujo | deflate_dict |
| 1 Mbps | 95% | 105 | 72.7 | 27.5 | 23.4 | 21.3 | **17.6** | 36.4 | 17.6 | deflate_flujo | deflate_dict |
| 10 Mbps | 50% | 1.1 | 1.07 | 1.06 | 0.835 | 0.813 | 0.76 | 1.3 | **0.757** | bandido UCB | deflate_dict |
| 10 Mbps | 80% | 2.67 | 2.45 | 1.79 | 1.46 | 1.38 | 1.23 | 2.25 | **1.23** | bandido UCB | deflate_dict |
| 10 Mbps | 95% | 10.5 | 7.27 | 2.95 | 2.35 | 2.14 | **1.77** | 4.06 | 1.77 | deflate_flujo | deflate_dict |
| 100 Mbps | 50% | 0.11 | 0.109 | 2.06 | 0.0906 | 0.0917 | 0.0886 | ∞ | **0.0849** | bandido UCB | deflate |
| 100 Mbps | 80% | 0.267 | 0.246 | ∞ | 0.153 | 0.149 | 0.136 | ∞ | **0.132** | bandido UCB | deflate_dict |
| 100 Mbps | 95% | 1.05 | 0.729 | ∞ | 0.242 | 0.225 | 0.191 | ∞ | **0.187** | bandido UCB | deflate_dict |
| 1 Gbps | 50% | **0.011** | 0.0128 | ∞ | 0.02 | 0.039 | 0.419 | ∞ | 0.0327 | sin compresión | sin compresión |
| 1 Gbps | 80% | 0.0267 | **0.0266** | ∞ | 0.0379 | ∞ | ∞ | ∞ | ∞ | ninguno | ninguno |
| 1 Gbps | 95% | 0.105 | **0.0749** | ∞ | 0.106 | ∞ | ∞ | ∞ | ∞ | ninguno | ninguno |

## 5. Velocidad de equilibrio por clase — CPU 10× más rápida (supuesto: implementación en C o en la estación base)

Con carga 50%: por debajo de esta velocidad de enlace la política tiene MENOS latencia que no comprimir. "nunca" = no ahorra lo suficiente para pagar su CPU a ninguna velocidad; "siempre" = gana en todo el rango probado (1 kbps a 100 Gbps).

| clase | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | bandido UCB |
|---|---|---|---|---|---|---|---|
| voip_rtp | 149 Mbps | 62.5 Mbps | 81.3 Mbps | 50.5 Mbps | 37.1 Mbps | 16.9 Mbps | 58.3 Mbps |
| http_texto | nunca | 9.94 Mbps | 710 Mbps | 511 Mbps | 504 Mbps | 5.78 Mbps | 436 Mbps |
| urllc_ctrl | 134 Mbps | 47.1 Mbps | 64.8 Mbps | 36 Mbps | 27.3 Mbps | 18.6 Mbps | 46.8 Mbps |
| video_rtp | 178 Mbps | 5.38 Mbps | 54.4 Mbps | 43.1 Mbps | 35.3 Mbps | 1.47 Mbps | 65.8 Mbps |
| iot_json | 168 Mbps | 12.7 Mbps | 77.4 Mbps | 95.1 Mbps | 111 Mbps | 10.5 Mbps | 87.3 Mbps |
| web_tls | nunca | nunca | nunca | nunca | nunca | nunca | nunca |
