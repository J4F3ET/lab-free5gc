# Resultados (generado por `evaluar.py comparar`)

## 1. Ahorro por clase de tráfico (% de bytes)

| clase | slice | B/paq | solo ROHC | ROHC+Huffman estático | ROHC+DEFLATE | ROHC+DEFLATE dicc. | ROHC+DEFLATE flujo | ROHC+TIC por paquete | TIC por paquete (sin ROHC) | DEFLATE flujo (sin ROHC) |
|---|---|---|---|---|---|---|---|---|---|---|
| iot_json | mMTC | 121.9 | 17.64 | 29.72 | 26.33 | 60.82 | 81.81 | 17.64 | -0.82 | 78.01 |
| urllc_ctrl | URLLC | 50.0 | 43.0 | 43.0 | 43.03 | 43.03 | 43.0 | 43.0 | -2.0 | 46.21 |
| voip_rtp | eMBB | 73.0 | 45.45 | 45.45 | 45.45 | 45.45 | 45.45 | 45.45 | -1.37 | 17.14 |
| video_rtp | eMBB | 1240.0 | 2.52 | 2.52 | 2.52 | 2.52 | 2.52 | 2.52 | -0.08 | -0.08 |
| web_tls | eMBB | 1440.0 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 |
| http_texto | eMBB | 1434.2 | -0.07 | 31.86 | 40.31 | 43.85 | 60.56 | 20.49 | 20.49 | 60.56 |

## 2. Flujo mezclado (3600 paquetes, 2,615,408 B)

| política | bytes enviados | razón | ahorro % | bytes que pasan por un compresor / byte | CPU emisor µs/paq (ruidoso) |
|---|---|---|---|---|---|
| sin compresión (crudo) | 2615408 | 1.0 | 0.0 | 0.0 | 0.6 |
| ROHC + ninguno | 2552120 | 1.0248 | 2.42 | 0.0 | 15.5 |
| ROHC + huffman_estatico | 2268565 | 1.1529 | 13.26 | 1.0 | 568.3 |
| ROHC + deflate | 2198296 | 1.1897 | 15.95 | 1.0 | 55.2 |
| ROHC + deflate_dict | 2142638 | 1.2206 | 18.08 | 1.0 | 85.2 |
| ROHC + deflate_flujo | 1983491 | 1.3186 | 24.16 | 1.0 | 118.3 |
| ROHC + tic_por_paquete | 2375205 | 1.1011 | 9.18 | 1.0 | 2110.0 |
| ROHC + deflate_flujo, reinicio cada 16 | 2027615 | 1.2899 | 22.47 | 1.0 | 114.4 |
| ROHC + bandido UCB, 6 brazos, γ=0,99 (como el Lab 3) | 2037326 | 1.2837 | 22.1 | 0.803 | 362.0 |
| ROHC + bandido UCB, configuración de red, sin reinicio | 1985347 | 1.3174 | 24.09 | 0.513 | 83.3 |
| ROHC + bandido UCB, configuración de red | 2029179 | 1.2889 | 22.41 | 0.513 | 75.5 |

Qué ELIGIÓ el bandido (configuración de red) en cada clase, antes del respaldo. En las clases cifradas o ya comprimidas, todo lo que no es "ninguno" es exploración:

| clase | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete |
|---|---|---|---|---|---|---|
| voip_rtp | 446 | 0 | 0 | 77 | 77 | 0 |
| http_texto | 1 | 0 | 0 | 3 | 596 | 0 |
| urllc_ctrl | 438 | 0 | 0 | 86 | 76 | 0 |
| video_rtp | 437 | 0 | 0 | 82 | 81 | 0 |
| iot_json | 1 | 0 | 0 | 3 | 596 | 0 |
| web_tls | 469 | 0 | 0 | 65 | 66 | 0 |

## 3. Latencia por paquete en µs (mediana, Python puro; comprimir / descomprimir)

| códec | iot_json comp/desc | urllc_ctrl comp/desc | voip_rtp comp/desc | video_rtp comp/desc | web_tls comp/desc | http_texto comp/desc |
|---|---|---|---|---|---|---|
| ninguno | 12.3 / 14.1 | 13.3 / 15.0 | 18.0 / 20.9 | 17.5 / 19.4 | 3.3 / 1.3 | 2.8 / 1.0 |
| huffman_estatico | 31.7 / 251.9 | 14.3 / 9.0 | 31.6 / 20.6 | 778.1 / 18.9 | 959.0 / 1.6 | 730.9 / 4749.3 |
| deflate | 27.8 / 17.5 | 21.4 / 10.5 | 26.3 / 13.8 | 78.8 / 15.4 | 71.8 / 1.2 | 71.8 / 17.5 |
| deflate_dict | 44.5 / 15.1 | 40.8 / 9.9 | 79.1 / 22.9 | 120.8 / 18.7 | 92.9 / 1.1 | 124.4 / 27.1 |
| deflate_flujo | 31.7 / 12.2 | 56.2 / 10.2 | 61.6 / 14.0 | 164.6 / 22.6 | 155.2 / 1.6 | 203.1 / 25.0 |
| tic_por_paquete | 246.8 / 12.4 | 110.2 / 13.5 | 150.3 / 15.2 | 3865.1 / 34.9 | 4714.3 / 4.7 | 2725.6 / 4586.4 |

## 4. Pérdida de 1 % de los paquetes (38 perdidos)

| caso | recibidos | mal reconstruidos | % mal | ahorro % |
|---|---|---|---|---|
| ROHC + deflate_dict (sin estado) | 3562 | 5 | 0.14 | 18.08 |
| ROHC + deflate_flujo (con estado, sin reinicio) | 3562 | 942 | 26.45 | 24.16 |
| ROHC + deflate_flujo (reinicio cada 16 paq.) | 3562 | 109 | 3.06 | 22.47 |
| ROHC + ninguno | 3562 | 5 | 0.14 | 2.42 |
| ROHC + bandido, config. de red sin reinicio | 3562 | 942 | 26.45 | 24.09 |
| ROHC + bandido, config. de red (reinicio cada 16 paq.) | 3562 | 126 | 3.54 | 22.41 |
