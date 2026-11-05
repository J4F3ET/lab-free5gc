# Ajuste del bandido (conjunto de ENTRENAMIENTO, semilla 1)

Criterio: mayor ahorro; entre los que quedan a menos de 0,1 puntos del mejor, el de menos CPU.

| brazos | γ | c | ahorro % | CPU µs/paq | veces que eligió ninguno (ideal 2400) |
|---|---|---|---|---|---|
| 6 (todos) | 0.99 | 0.02 | 22.91 | 247.3 | 1385 |
| 6 (todos) | 0.99 | 0.05 | 22.81 | 359.4 | 730 |
| 6 (todos) | 0.995 | 0.02 | 22.99 | 202.2 | 1620 |
| 6 (todos) | 0.995 | 0.05 | 22.93 | 336.1 | 857 |
| 6 (todos) | 0.999 | 0.02 | 23.01 | 147.4 | 1941 |
| 6 (todos) | 0.999 | 0.05 | 23.01 | 278.2 | 1147 |
| 3 (ninguno, dicc., flujo) | 0.99 | 0.02 | 22.42 | 66.9 | 1947 |
| 3 (ninguno, dicc., flujo) | 0.99 | 0.05 | 22.99 | 78.6 | 1396 |
| 3 (ninguno, dicc., flujo) | 0.995 | 0.02 | 22.24 | 65.5 | 2068 |
| 3 (ninguno, dicc., flujo) | 0.995 | 0.05 | 23.01 | 83.0 | 1539 |
| 3 (ninguno, dicc., flujo) | 0.999 | 0.02 | 20.65 | 54.2 | 2213 |
| 3 (ninguno, dicc., flujo) | 0.999 | 0.05 | 23.04 | 69.4 | 1794 |

Elegido: brazos 3 (ninguno, dicc., flujo), γ = 0.999, c = 0.05.
