# 09 — Arranque seguro y recuperación

Cómo arranca, se vigila y se recupera la pila del laboratorio sin congelar el
host. Es la referencia de `lab5g-stack`. Las órdenes de todos los días están en
la [sección 9](#9-comandos-de-todos-los-días).

---

## Índice

- [1. Por qué existe](#1-por-qué-existe)
- [2. Las piezas](#2-las-piezas)
- [3. El arranque por etapas](#3-el-arranque-por-etapas)
- [4. Compuertas y umbrales](#4-compuertas-y-umbrales)
- [5. Cuándo se considera sano un servicio](#5-cuándo-se-considera-sano-un-servicio)
- [6. La recuperación y sus límites](#6-la-recuperación-y-sus-límites)
- [7. Métricas `lab5g_stack_*` y `lab5g_host_*`](#7-métricas-lab5g_stack_-y-lab5g_host_)
- [8. Persistencia y copias](#8-persistencia-y-copias)
- [9. Comandos de todos los días](#9-comandos-de-todos-los-días)
- [10. Qué hacer si el host se satura](#10-qué-hacer-si-el-host-se-satura)
- [11. Primera migración desde la versión anterior](#11-primera-migración-desde-la-versión-anterior)
- [12. Pendientes y cosas por confirmar](#12-pendientes-y-cosas-por-confirmar)
- [13. Bugs que solo salieron con Docker real](#13-bugs-que-solo-salieron-con-docker-real)
- [14. No probado todavía](#14-no-probado-todavía)

---

## 1. Por qué existe

El laboratorio corre en un LXC con 4 núcleos, 3.8 GB de RAM en el host y disco
mecánico. Con la política `restart: unless-stopped`, Docker levantaba casi todos
los contenedores a la vez cuando arrancaba el demonio.

Hay dos incidentes documentados con cifras distintas:

- **Arranque en frío del 21 de noviembre:** load average 28 con presión de I/O
  del 99 %.
- **Estampida original:** load average 105. El host dejó de responder hasta por
  SSH.

El mecanismo es siempre el mismo. El disco no aguanta las lecturas de arranque
de decenas de servicios a la vez, todas se alargan, los chequeos de salud
expiran, Docker reinicia lo que parecía caído y cada reinicio añade carga.

La solución tiene tres partes:

1. **Ningún contenedor se reinicia solo.** Todos los servicios llevan
   `restart: "no"`.
2. **Un solo dueño.** `lab5g-stack.service` arranca un servicio cada vez y repone
   lo que se cae.
3. **Compuertas de carga.** Solo se arranca algo cuando el host está tranquilo.

Medido el 21 de noviembre: arranque en frío de **18.5 min (1112 s)** hasta tener
los 20 servicios arriba, tras encender el LXC; apagado limpio de **254 s**; pico de
carga de **7.1** solo en los primeros 30 s (arranque de Docker); carga entre **0.6 y
3.9** durante las etapas; pico de **12.5** solo al apagar todo a la vez. Congelar el
host, no.

Línea base medida el 21 de noviembre: Docker 29.10, Compose v5.7.1 y los ficheros
`/proc/pressure/{cpu,io,memory}` presentes dentro del LXC. El laboratorio vive en el
checkout del runner, por eso el código se despliega en `/opt/lab5g`.

---

## 2. Las piezas

| Pieza | Qué es | Dónde queda en el LXC |
|---|---|---|
| `lab5g-stack.sh` | Dueño del arranque, la recuperación y la publicación de métricas | `/usr/local/sbin/` |
| `lab5g-stack.service` | Unidad de systemd que ejecuta `lab5g-stack.sh run` | `/etc/systemd/system/` |
| `lab5g.sh` | CLI del operador; delega en `lab5g-stack.sh` y en la copia | `/usr/local/sbin/` |
| `lab5g-backup.sh`, `.service`, `.timer` | Copia diaria de Prometheus | `/usr/local/sbin/` y `/etc/systemd/system/` |
| `lab5g.env` | Entorno del despliegue, modo `640 root:deploy` | `/etc/lab5g/` (fuera del repositorio) |
| `.env` | Enlace simbólico a `lab5g.env`, creado por bootstrap | `/opt/lab5g/.env` |
| Código desplegado | Copia de los archivos versionados | `/opt/lab5g` (lo copia `lab5g.sh sync`) |
| Estado persistente | Etapa, registro de reinicios, última métrica | `/var/lib/lab5g` |
| Estado volátil | `parado`, `parados` y el cerrojo `ops.lock` | `/run/lab5g` (se borra al reiniciar el LXC) |

`deploy/bootstrap-runner.sh` instala todo lo anterior y **habilita** las unidades
`lab5g-stack.service` y `lab5g-backup.timer`, pero **no las arranca**. El
arranque lo decide el operador con `lab5g.sh start` o con el pipeline.

### Configuración del host (Proxmox)

Está fuera del repositorio. Sin esto, el resultado medido no se reproduce:

- Contenedor 110: `pct set 110 --memory 1920 --swap 512` y
  `pct set 110 --onboot 1 --startup order=6,up=180,down=360`.
- El `down` de Proxmox (360 s) debe ser **mayor** que el apagado medido (254 s). Si
  Proxmox corta antes, el apagado queda a medias. Por la misma razón,
  `TimeoutStopSec=900` del servicio nunca llega a aplicarse en un apagado del host:
  Proxmox corta antes.
- zram en el host: `apt install zram-tools`, y en `/etc/default/zramswap`:
  `ALGO=zstd`, `PERCENT=25`, `PRIORITY=100`. La primera vez, zram arrancó con los
  valores por defecto. Los de `/etc/default` solo se aplican tras reiniciar el host.
- `vm.swappiness=30` en `/etc/sysctl.d/99-lab5g.conf`.

Para que el bucle de recuperación vigile, hay que arrancar el servicio:

```bash
sudo systemctl start lab5g-stack
```

Sin él, `lab5g.sh start` arranca la pila pero **nadie la vigila** después.

Como `/opt/lab5g/.env` es un enlace a `/etc/lab5g/lab5g.env`, un `docker compose`
lanzado a mano desde `/opt/lab5g` lee el entorno real sin más. `lab5g.sh sync`
excluye `.env`, así que el enlace sobrevive a los despliegues.

---

## 3. El arranque por etapas

`STAGES` en `lab5g-stack.sh` define 14 etapas. Se recorren en orden:

```text
Etapa  Servicios
  1    prometheus pushgateway     (antes iban al final)
  2    mongodb
  3    nrf
  4    udr udm ausf pcf nssf
  5    amf
  6    upf                        (exige el modulo gtp5g cargado en el host)
  7    smf
  8    gnb
  9    ue
 10    dn-iperf
 11    cmp-dn
 12    cmp-ue                     (netns del ue)
 13    envio envio-eco            (envio vive en el netns de cmp-dn)
 14    cmp-gen                    (netns del ue)
```

Después de las etapas se arrancan, solo si se nombran, las sondas y `phy-sim`
(`EXTRAS`): `blackbox`, `iperf-probe` y `phy-sim`. `grafana` es bajo demanda.

Dentro de cada etapa los servicios arrancan uno a uno. Para cada uno se repite
el mismo ciclo:

```text
  COMPUERTA   load1 < 4  y  presion de I/O < 40 %  y  MemAvailable >= 300 MB
              cerrada  -> espera, sondeo cada 15 s
              cerrada 45 min -> avanza en modo degradado
        |
        v
  ARRANQUE    docker compose up -d --no-deps <servicio>
              (si ya corre y su padre de red es mas nuevo: reinicio)
        |
        v
  SALUD       espera hasta 300 s (mongodb: 600 s)
              fallo -> se anota y el arranque SIGUE con el siguiente
        |
        v
  PAUSA       30 s, para que la carga media de 1 minuto refleje el arranque
```

- Un fallo no detiene el arranque. Al final el log dice
  `arranque terminado con fallos en: ...` y la recuperación se encarga después.
- Una parada pedida (`lab5g.sh stop` sin argumentos) aborta el arranque en curso.
- Si una compuerta lleva cerrada 45 min, se avanza con **un solo** servicio en
  modo degradado. El valor de `lab5g_stack_etapa` pasa a negativo y la siguiente
  vuelta vuelve a exigir la compuerta.
- Al final, si `cmp-dn` está sano y el pushgateway no tiene `lab5g_cmp_eval`, se
  publica una vez la evaluación de compresión. Corre en un contenedor aparte y
  efímero (256 MB, red `obs_net`), para que un OOM no corte el túnel de `cmp-dn`.
  No aborta si falla.
- `grafana` no entra en el arranque: `lab5g.sh grafana up` lo enciende.

---

## 4. Compuertas y umbrales

### Compuerta de arranque y recuperación

La misma compuerta sirve para arrancar y para recuperar. Se sondea cada 15 s y
el log emite una línea cada 5 min.

| Compuerta | Umbral | Variable | Si no se cumple |
|---|---|---|---|
| Carga media de 1 min (`load1`) | menor que 4 (4 núcleos) | `GATE_LOAD` | Espera |
| Presión de I/O (PSI, `some avg10`) | menor que 40 % | `GATE_IO` | Espera. Si el kernel no expone PSI se asume 0 y solo protege `load1` |
| Memoria disponible (`MemAvailable`) | al menos 300 MB | `GATE_MEM` | Espera |
| Tope de espera en el arranque | 2700 s (45 min) | `GATE_MAX_WAIT` | El arranque avanza con un servicio en modo degradado |

La recuperación no tiene tope: mientras la compuerta esté cerrada, no reinicia nada.

`lab5g.sh grafana up` usa la misma compuerta. Si no pasa, se niega y dice por qué.

### Copia de seguridad

La copia espera hasta 20 minutos a que la compuerta se abra (`BACKUP_GATE_WAIT`,
1200 s). Si no se abre, sale con código 75.

### Comprobación previa: `lab5g.sh check`

`ci/capacity-check.sh` es el que ejecuta el job `capacidad` del pipeline. No
arranca nada.

| Comprobación | Umbral | Si falla |
|---|---|---|
| RAM disponible (`MemAvailable`) | al menos 600 MB (`MIN_MEM_MB`) | FAIL |
| Swap en uso | menos de 70 % (`MAX_SWAP_PCT`) | FAIL |
| Carga `load1` | menor que 4 | WARN |
| Presión de I/O | menor que 40 % | WARN (si no hay PSI, WARN "no se puede medir") |
| Espacio libre donde está `/var/lib/docker` | falla por debajo de 2 GB (`MIN_DISK_GB`); avisa por debajo de 4 GB (`WARN_DISK_GB`) | FAIL / WARN |
| Módulo `gtp5g` | cargado | FAIL |
| SCTP | disponible | FAIL |
| `/dev/net/tun` | presente | FAIL |
| `/etc/lab5g/lab5g.env` | legible | FAIL |

Códigos de salida: `0` todo bien, `1` fallo (el pipeline se para), `2` solo
avisos (el pipeline continúa). Con `--estricto` los avisos también fallan.

### Tiempos y reintentos

| Qué | Valor | Variable |
|---|---|---|
| Espera de salud por servicio | 300 s (mongodb, 600 s) | `HEALTH_TIMEOUT`, `HEALTH_TIMEOUT_MONGO` |
| Pausa tras cada servicio sano | 30 s | `SETTLE` |
| Parada de cada servicio (`stop -t`) | 30 s | `STOP_TIMEOUT` |
| Ciclo de recuperación | 60 s | `RECOVERY_PERIOD` |
| Enfriamiento entre reinicios del mismo servicio | 120 s | `COOLDOWN` |
| Reinicios máximos por servicio | 5 en una ventana de 3600 s | `MAX_RESTARTS`, `WINDOW` |
| Publicación de métricas | cada 30 s | `METRICS_PERIOD` |

---

## 5. Cuándo se considera sano un servicio

"Sano" no significa lo mismo para todos. Esto es lo que comprueba
`lab5g-stack.sh`:

| Servicio | Criterio |
|---|---|
| `mongodb` | Si tiene healthcheck de Docker, basta que esté `healthy`. Si no, TCP al 27017 |
| `nrf`, `amf`, `smf` | Estado `running` y TCP al puerto 8000 de su IP en `sbi_net` |
| `ue` | Estado `running` y existe `uesimtun0` con una dirección IP |
| El resto | Solo `running`. No tienen healthcheck ni sonda: "sano" significa "arrancado" |

Otras reglas de arranque:

- Un servicio cuyo **padre de red** no está corriendo se omite con error. Los
  hijos de namespace son `cmp-ue`, `cmp-gen` y `envio-eco` (padre `ue`), `envio`
  (padre `cmp-dn`) y `blackbox` e `iperf-probe` (padre `ue`).
- `upf` no arranca si `gtp5g` no está cargado en el host. Se omite con error.
- Un estado `unhealthy` se tolera dentro del presupuesto de espera.
- Al arrancar un servicio se normaliza su política de reinicio a `no` (ver
  [sección 11](#11-primera-migración-desde-la-versión-anterior)).

---

## 6. La recuperación y sus límites

Tras el arranque, `lab5g-stack run` entra en un bucle que cada 60 s hace esto:

```text
cada 60 s:
  docker responde?             no -> se salta el ciclo (no se decide nada)
  buscar UN candidato, en orden de etapas:
     caido (gestionado) u obsoleto (su padre de red arranco despues que el)
     y no detenido a mano, con su padre corriendo,
     fuera de enfriamiento (120 s) y no agotado (5 reinicios en 60 min)
  compuerta cerrada  -> espera
  compuerta abierta  -> reinicia ESE servicio, espera su salud, siguiente ciclo
```

Solo se reinicia **un servicio por ciclo**. Un hijo de namespace que quedó
"obsoleto" porque su padre se reinició se recupera en un ciclo posterior.

### Límites que conviene conocer

- **Agotamiento.** Un servicio que lleva 5 reinicios en 60 minutos queda
  `AGOTADO` (`lab5g_stack_agotado = 1`). La recuperación no insiste. Hay que mirar
  la causa y arrancarlo a mano con `lab5g.sh start <servicio>`. La ventana es
  deslizante: la recuperación vuelve a intentarlo cuando se vacía.
- **Solo mira el estado del contenedor.** Detecta un servicio caído o un
  namespace obsoleto. No detecta un servicio que sigue `running` pero no hace su
  trabajo (por ejemplo, una asociación PFCP caída). Eso sigue requiriendo
  diagnóstico, [docs/05](05-diagnostico.md).
- **Las paradas a mano son respetadas** hasta que `start` las deshace.
- **Las paradas a mano no sobreviven a un reinicio del LXC.** `/run` se vacía al
  arrancar y `lab5g-stack run` borra `parado` y `parados` al inicio. Tras un
  reinicio, la pila levanta todo lo gestionado.
- **Grafana, las sondas y `phy-sim` no se arrancan solos.** Las sondas y
  `phy-sim` solo se reinician si estaban corriendo y quedaron obsoletas.
- **Un fallo de Docker salta el ciclo; no provoca reinicios.** Si el CLI de
  Docker no responde (se cuelga bajo carga), la decisión se pospone.
- **La compuerta manda también sobre la recuperación.** Con la compuerta
  cerrada, no se reinicia nada, aunque haya servicios caídos.

---

## 7. Métricas `lab5g_stack_*` y `lab5g_host_*`

Cada 30 s el servicio escribe `/var/lib/lab5g/metricas.prom` y lo sube con
`PUT` al pushgateway, job `lab5g_stack`. El `PUT` reemplaza el grupo completo,
así que las series de servicios que desaparecen no quedan como fantasmas.
Prometheus las recoge del pushgateway.

| Métrica | Valor | Significado |
|---|---|---|
| `lab5g_stack_servicio_up{servicio}` | 1 o 0 | Está corriendo. Una serie por cada uno de los 20 servicios gestionados |
| `lab5g_stack_reinicios_total{servicio}` | entero | Reinicios automáticos de las últimas 24 h. Es un `counter` por tipo, pero el registro se poda a 24 h. Incluye las sondas |
| `lab5g_stack_agotado{servicio}` | 1 o 0 | 1 si agotó sus reinicios y sigue caído |
| `lab5g_stack_etapa` | entero | Etapa en curso (1 a 14). 0 = arranque terminado. Negativo = etapa con un servicio degradado |
| `lab5g_stack_latido_segundos` | época Unix | Momento de la última publicación. Si deja de avanzar, la pila no publica |
| `lab5g_host_load1` | número | Carga media de 1 minuto |
| `lab5g_host_io_presion_pct` | % | Presión de I/O (PSI `some avg10`) |
| `lab5g_host_mem_disponible_mb` | MB | `MemAvailable` del LXC |
| `lab5g_ct_mem_mb{servicio}` | MB | Memoria del cgroup de cada servicio gestionado. No empieza por `lab5g_stack_` |

Notas:

- El pushgateway conserva el último valor. Si la pila se para, las métricas se
  quedan "congeladas" en vez de desaparecer. Por eso `lab5g_stack_latido_segundos`
  es la forma de saber si la pila sigue viva.
- `prometheus.yml` no carga reglas de alerta. Cualquier alerta sobre el latido
  habría que añadirla aparte.
- El dashboard `lab5g-overview` lee ahora `lab5g_host_io_presion_pct`,
  `lab5g_host_mem_disponible_mb` y `lab5g_ct_mem_mb`. Ver la
  [sección 12](#12-pendientes-y-cosas-por-confirmar).

---

## 8. Persistencia y copias

| Dato | Dónde vive | Cómo se protege |
|---|---|---|
| **Abonados** (SIM, claves) | Volumen `mongo-data` | **No lo cubre la copia de Prometheus.** Se recupera con `ci/provision-subscriber.sh` |
| Métricas de Prometheus | Volumen `prom-data`, retención 90 días o 2 GB (lo que llegue antes) | Copia diaria en `/var/backups/lab5g/` |
| Métricas empujadas (sondas, compresión, pila) | Volumen `pushgw-data` con persistencia a archivo cada minuto | Sobrevive a reinicios del contenedor. Corre como `0:0` para poder escribir el volumen |
| Paneles y usuarios de Grafana | Volumen `grafana-data` | **Sin copia.** Por confirmar si se quiere añadir |
| Código y configuración | Repositorio | Git. En el LXC, `/opt/lab5g` se regenera con `lab5g.sh sync` |

### La copia de Prometheus

- `lab5g-backup.timer` se dispara cada día a las 03:30, con retraso aleatorio de
  hasta 15 minutos. `Persistent=true` recupera una ejecución perdida si el host
  estaba apagado a esa hora.
- Usa la API de snapshot de Prometheus, que exige `--web.enable-admin-api`. Corre
  con `ionice idle` y `nice 19`, y comprime con `gzip -1` para gastar poca CPU.
- Antes de copiar exige espacio libre de al menos 2 veces el tamaño del TSDB más
  2 GB. Si no lo hay, no copia nada. El disco del LXC es de 24 GB con unos 7 GB
  libres.
- Espera hasta 20 minutos a que la compuerta se abra. Si no se abre, **sale con
  código 75** y el timer no reintenta ese mismo día.
- Conserva las 2 copias más recientes (`BACKUP_KEEP=2`):
  `/var/backups/lab5g/prometheus-AAAAMMDD-HHMM.tar.gz`. Están en el mismo disco
  mecánico que el resto del sistema.

### Restaurar

```bash
sudo /usr/local/sbin/lab5g.sh restore /var/backups/lab5g/prometheus-AAAAMMDD-HHMM.tar.gz
```

- Si hay terminal, pide escribir `si`. Sin terminal exige `--si`.
- Detiene Prometheus, vacía el volumen `prom-data`, copia el snapshot y devuelve
  la propiedad al usuario `nobody` (65534).
- Después: `sudo /usr/local/sbin/lab5g.sh start prometheus`.
- Solo puede ejecutarlo root. El usuario `deploy` del runner no lo tiene en su
  `sudoers`.

---

## 9. Comandos de todos los días

Todos se ejecutan en el LXC. Se usa la ruta absoluta de `lab5g.sh` porque es la
que aparece en `sudoers` y en los workflows.

### Ver el estado

```bash
sudo /usr/local/sbin/lab5g.sh status
```

Muestra una fila por servicio (`SERVICIO ESTADO UPTIME MEM_MB`), incluidos
Grafana, las sondas y `phy-sim`, y una línea con
`carga: load1=... io=...% memdisp=...MB | etapa=...`. Si la recuperación está
suspendida, lo indica al final. El estado puede ser `running(healthy)`, `exited`,
`ausente` o `agotado`.

Por GitHub Actions: `gh workflow run observability-control.yml --repo J4F3ET/lab-free5gc -f accion=estado`.

### Encender y apagar Grafana

```bash
sudo /usr/local/sbin/lab5g.sh grafana up
sudo /usr/local/sbin/lab5g.sh grafana down
```

- `up` exige que Prometheus esté corriendo y que su último dato tenga menos de
  2 minutos. Después exige la compuerta completa, con la memoria incluida. Si no
  pasa, se niega y dice por qué. Luego espera la salud, con tope de 300 s.
- Por GitHub Actions, con el workflow `observability-control` (lanzamiento manual):

```bash
gh workflow run observability-control.yml --repo J4F3ET/lab-free5gc -f accion=grafana-up
gh workflow run observability-control.yml --repo J4F3ET/lab-free5gc -f accion=grafana-down
```

- Grafana queda en `http://192.168.1.50:3000`, solo accesible desde la LAN.
- `lab5g.sh start` **no** arranca Grafana.

### Arrancar

```bash
sudo /usr/local/sbin/lab5g.sh start              # todo lo gestionado, salvo grafana
sudo /usr/local/sbin/lab5g.sh start ran          # un grupo: nucleo, ran, compresion u obs
sudo /usr/local/sbin/lab5g.sh start amf          # un servicio; sus padres de red deben estar arriba
```

- Sin argumentos borra las paradas manuales anteriores.
- Un arranque en frío tardó **18.5 min** (1112 s) medidos el 21 de noviembre. Para seguirlo:
  `journalctl -u lab5g-stack -f`.

#### Sondas y phy-sim

Se arrancan por nombre, con la misma compuerta que el resto. Requieren el `ue`
arriba (las sondas comparten su red):

```bash
sudo /usr/local/sbin/lab5g.sh start blackbox
sudo /usr/local/sbin/lab5g.sh start iperf-probe
sudo /usr/local/sbin/lab5g.sh start phy-sim
```

`start` sin argumentos **no** las arranca.

### Parar

```bash
sudo /usr/local/sbin/lab5g.sh stop                    # todo, en orden inverso, uno a uno
sudo /usr/local/sbin/lab5g.sh stop cmp-gen cmp-ue     # solo esos
```

- `stop` sin argumentos crea `/run/lab5g/parado`, que suspende la recuperación
  hasta el próximo `start`. Para todo: sondas, `phy-sim`, Grafana y la pila.
- `stop` con nombres los deja anotados como parados a mano. La recuperación no
  los toca hasta el próximo `start`.
- `start <svc>` de un servicio que ya corre ejecuta igualmente `up -d --no-deps <svc>`:
  compose lo recrea solo si cambió su configuración (p. ej. tras `env-set`). El log
  dice si lo recreó o si compose no vio cambios; en ese caso se comprueba con
  `docker exec <svc> env` y, si hace falta, `stop <svc>` y luego `start <svc>`.
  Tras cambiar una variable que comparten `cmp-dn` y `cmp-ue`, conviene aplicar ambos
  en el mismo `start`.
- Un `stop` sin argumentos tardó **254 s** en el apagado medido. Mejora futura,
  no implementada: apagar por grupos (sondas y Grafana, compresión, RAN, NF, `upf`,
  `mongodb`, y por último `prometheus` y `pushgateway`).

### Copia de seguridad

```bash
sudo /usr/local/sbin/lab5g.sh backup
```

Hace lo mismo que el timer, a mano. Espera hasta 20 minutos a la compuerta; si no
se abre sale con código 75.
Por GitHub Actions: `gh workflow run observability-control.yml --repo J4F3ET/lab-free5gc -f accion=backup`.

### Restaurar, comprobar capacidad, cambiar el entorno

```bash
sudo /usr/local/sbin/lab5g.sh restore ARCHIVO        # ver la seccion 8
sudo /usr/local/sbin/lab5g.sh check                  # 0 todo bien, 1 fallo, 2 avisos
printf '%s' 'NUEVO_VALOR' | sudo /usr/local/sbin/lab5g.sh env-set GRAFANA_ADMIN_PASSWORD
```

- `env-set` solo acepta claves que existan en `.env.example`. El valor entra por
  stdin para que no quede en el historial ni en `ps`. Rechaza valores con comilla
  simple (`'`) o que terminen en barra invertida, porque compose no los lee de forma
  fiable. Rechaza también los valores de varias líneas o con retorno de carro (CR).

### Ver el log de la pila

```bash
journalctl -u lab5g-stack -f
```

---

## 10. Qué hacer si el host se satura

Señales: la carga se mantiene por encima de 4, `lab5g.sh status` muestra la
etapa atascada con la compuerta cerrada, o el host tarda en responder.

1. **Mirar antes de tocar.** `uptime` y `sudo /usr/local/sbin/lab5g.sh status`.
   Si la línea `carga:` marca `load1` o `io` altos, la compuerta ya está
   retrasando el arranque. Espera a que baje antes de actuar.
2. **Soltar lo que no hace falta**, en este orden:

   ```bash
   sudo /usr/local/sbin/lab5g.sh grafana down
   sudo /usr/local/sbin/lab5g.sh stop cmp-gen cmp-ue cmp-dn envio envio-eco
   ```

   Grafana tiene un tope de 768 MB y la compresión suma 368 MB de topes. Son topes,
   no uso real: en reposo la pila usa unos 0.3 GB.
3. **Si sigue saturado, parar la pila entera:** `sudo /usr/local/sbin/lab5g.sh stop`.
   Es el último recurso: el 5G se cae, y volver a arrancar cuesta unos 18.5 minutos.
4. **No lanzar `docker compose up` a mano mientras la pila arranca.** Las órdenes
   manuales no pasan por el cerrojo `ops.lock` ni por las compuertas.
5. **No reiniciar a mano en bucle.** La recuperación ya reintenta. Un servicio
   agotado se queda parado hasta que pase la ventana, y forzarlo solo vuelve a
   cargar el disco.
6. **Antes de volver a arrancar**, `sudo /usr/local/sbin/lab5g.sh check` para ver
   RAM, swap, disco y módulos.
7. **Si ni SSH responde**, la única salida es la consola física del host en Proxmox.

---

## 11. Primera migración desde la versión anterior

La versión anterior del compose arrancaba con `restart: unless-stopped` y tenía
otros servicios. En un host que ya los tiene, la primera vez que corre la pila
nueva hace dos limpiezas:

- **Política de reinicio.** `lab5g-stack` comprueba la política de reinicio de
  **todos** los contenedores del lab, incluidos los bajo demanda. Si no es `no`, la
  cambia a `no` con `docker update`. No recrea el contenedor.
- **Huérfanos conocidos.** Al primer `up` de la sesión, la pila usa
  `--remove-orphans` y borra por nombre exacto, de una lista cerrada, los
  contenedores de versiones anteriores: `dozzle`, `webui`, `cadvisor`,
  `node-exporter` y `5f3e1c9dd437_grafana`. Este último es un Grafana renombrado
  por un `up` interrumpido. No se borra nada por patrón.

Tras la primera migración no hay que hacer nada más. Si el host ya tenía los
contenedores viejos en marcha, conviene `sudo /usr/local/sbin/lab5g.sh status`
para comprobar que aparecen solo los servicios actuales.

---

## 12. Pendientes y cosas por confirmar

- **Cifra de carga.** Se documenta como: load average 28 con presión de I/O del
  99 % en el arranque en frío del 21 de noviembre; la estampida original llegó a
  105.
- **Topes de memoria.** Son provisionales hasta medir `memory.peak` con `docker
  stats`. Los 20 siempre encendidos suman 1824 MB de topes. La protección real del
  arranque de Grafana es la compuerta `GATE_MEM` (300 MB de `MemAvailable`), no la
  suma de topes.
- **Página de envío desde la LAN.** Se accede a `192.168.1.50:8088`. Docker hace
  el DNAT y la regla DOCKER-USER no lo bloquea. **No** añadir `10.100.204.0/24`
  a `lab5g-netrules.sh`: expondría la red de datos y el UPF. Por verificar en el
  servidor.
- **Copia de Grafana.** No existe. Por confirmar si hace falta.
- **Subredes del compose.** `docker-compose.yaml` fija las subredes con valores
  literales. Por confirmar si las variables `SBI_SUBNET`…`OBS_SUBNET` de
  `.env.example` se usan en algún sitio.
- **Panel de CPU por contenedor.** Quitado de `lab5g-overview` el 22-11:
  consultaba `container_cpu_usage_seconds_total` de cAdvisor, retirado. El panel
  de memoria por contenedor ocupa ahora todo el ancho de esa fila.
- **Configuración de `webuicfg`.** `config/webuicfg.yaml` sigue en el repositorio sin
  uso. Por confirmar si se borra.

---

## 13. Bugs que solo salieron con Docker real

- **mongod:** `--wiredTigerCacheSizeGB` tiene un mínimo de 0.25. Con 0.125 sale con
  error 1.
- **Docker 29:** `{{if .State.Health}}` rompe la plantilla de `inspect`. Se usa
  `index .State "Health"`.
- **Contenedores creados a mano:** sin etiquetas de compose, bloquean el `up` por
  nombre. `clear_foreign` los elimina antes de crear el servicio.
- **pids_limit:** 100 mataba a Grafana 13. Ahora son 512.
- **env-set:** rechazaba en silencio los valores multilínea. Ahora los rechaza con un
  error.

## 14. No probado todavía

- Restauración real (`lab5g.sh restore`) sobre datos vivos.
- Reinicio completo del host Proxmox.
- Recuperación del plano de control (caída de `amf` o `nrf`).
- Workflows en GitHub real. Solo se han verificado en local.
- Agotamiento de reintentos (5 reinicios en 60 min) en producción.
- Copia al host con `pct pull`.

---

**Anterior:** [08 — Compresión de paquetes](08-compresion-paquetes.md) ·
**Volver al** [README](../README.md)
