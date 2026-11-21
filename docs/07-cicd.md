# 07 — CI/CD: despliegue automático

Cómo hacer que un `git push` despliegue el laboratorio solo, y qué hace el
pipeline exactamente.

Todo esto es **opcional**: el laboratorio funciona igual desplegándolo a mano.

---

## Índice

- [1. Advertencia de seguridad, primero](#1-advertencia-de-seguridad-primero)
- [2. Qué hace el pipeline](#2-qué-hace-el-pipeline)
- [3. Instalar el runner](#3-instalar-el-runner)
- [4. Preparar los permisos](#4-preparar-los-permisos)
- [5. El secreto](#5-el-secreto)
- [6. Lanzarlo](#6-lanzarlo)
- [7. Concurrencia](#7-concurrencia)
- [8. Los cinco fallos que ya nos pasaron](#8-los-cinco-fallos-que-ya-nos-pasaron)

---

## 1. Advertencia de seguridad, primero

Un *self-hosted runner* ejecuta el código que llegue al repositorio, **con los
permisos del usuario que lo corre, dentro de tu red**.

Consecuencias que hay que aceptar antes de instalarlo:

- **Nunca** pongas un runner self-hosted en un repositorio público. Cualquiera
  puede abrir un pull request, y ese PR puede ejecutar comandos en tu máquina.
- El usuario del runner debe ser **uno dedicado**, sin sudo general. Este
  repositorio incluye un fichero de sudoers acotado para eso.
- El runner de este laboratorio tiene acceso a tu LAN. Trátalo como una máquina
  expuesta.

---

## 2. Qué hace el pipeline

Son **dos workflows**, que comparten el mismo `concurrency.group` para no
competir nunca por el host (ver [concurrencia](#7-concurrencia)).

### `deploy.yml` — la pila completa

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml), nombre
`deploy-5g-lab`, con cinco trabajos encadenados:

```
validate  →  capacidad  →  deploy  →  smoke  →  (rollback, solo si algo falló)
```

### `observability-control.yml` — Grafana a demanda

[`.github/workflows/observability-control.yml`](../.github/workflows/observability-control.yml)
enciende o apaga Grafana, muestra el estado, o hace una copia de Prometheus. No
recrea contenedores ni toca el entorno. Sustituye al antiguo
`deploy-observability.yml`, que ya no existe.

Prometheus y el pushgateway no son cosa de este workflow: arrancan con la pila en
`deploy.yml`. Lo que hace la pila y por qué está escalonada está en
[09 — Arranque seguro](09-arranque-seguro.md).

### `validate` — sin tocar nada de lo que está corriendo

Cuatro comprobaciones estáticas:

1. `ci/preflight.sh` — las 13 condiciones del entorno
2. `docker compose --env-file .env.example config --quiet` — sintaxis del compose, con los perfiles `ran`, `compresion`, `obs` y `grafana` (no `obs-extra` ni `probes`)
3. `ci/check-plmn.py` — coherencia de los tres números (PLMN, TAC, S-NSSAI)
4. `python3 -m unittest compresion/test_compresores.py compresion/test_reproductores.py` — pruebas del compresor

Si algo falla aquí, **el despliegue no llega a empezar**. Es deliberado: es mucho
más barato fallar en la validación que a mitad del arranque.

### `capacidad` — sincronizar y comprobar el host

1. `sudo -n lab5g.sh sync` copia a `/opt/lab5g` solo los archivos versionados
   (`git ls-files`). Ese directorio no lo borra el checkout del runner.
2. Compara `deploy/lab5g.sh`, `lab5g-stack.sh`, `lab5g-backup.sh` y las unidades
   de `/opt/lab5g/deploy` con las copias instaladas en `/usr/local/sbin/` y
   `/etc/systemd/system/`. Si difieren, emite un `::warning::`. Se corrige
   ejecutando `deploy/bootstrap-runner.sh` en el LXC.
3. `sudo -n lab5g.sh check` comprueba RAM, swap, carga, I/O, disco, `gtp5g`,
   SCTP y `/dev/net/tun`.

El código de salida decide: `0` continúa, `2` (solo avisos) continúa con una
advertencia visible, cualquier otro valor aborta. Los criterios están en
[09 §4](09-arranque-seguro.md#4-compuertas-y-umbrales).

### `deploy` — arranque por etapas

```
1. Guardar el secreto de Grafana en /etc/lab5g/lab5g.env   (lab5g.sh env-set)
2. Reaplicar las reglas de red del host                    (lab5g-netrules.sh)
3. Reiniciar los servicios cuya configuración o compresion/ cambió
4. Arranque escalonado: lab5g.sh start                     (hasta 60 min)
5. Marcar el sha desplegado                                (lab5g.sh marcar-desplegado)
6. ci/wait-ready.sh                                        (hasta 15 min)
7. ci/provision-subscriber.sh                              (hasta 10 min)
```

El paso 3 mira `git diff` entre una base y el commit nuevo, solo en `config/`,
`ueransim/` y `compresion/`. Para cada fichero cambiado reinicia el servicio afectado, uno a
uno, con `lab5g.sh stop <svc>` y `lab5g.sh start <svc>`. Un `start` a secas no
recrea un contenedor cuyo YAML montado cambió, porque el bind-mount no entra en
el hash del servicio. La base es `github.event.before` en un push. En dispatch, o
si ese valor no es válido, es el último sha desplegado con éxito
(`lab5g.sh ultimo-desplegado`, guardado en `/var/lib/lab5g/desplegado`). Para
`compresion/*` reinicia `cmp-dn` y luego `cmp-ue`, en ese orden, porque el código
se monta `:ro` y su hash no cambia. Si no hay base válida, no reinicia nada y avisa.
Tras el arranque, el workflow marca el sha con `lab5g.sh marcar-desplegado`. Un cambio en `nrf` o `ue` obliga a reiniciar también a sus
dependientes; eso lo reordena la recuperación de `lab5g-stack` por hora de
arranque, no este paso.

El paso 4 es el único que arranca contenedores, y lo hace `lab5g-stack` **de uno
en uno**, con compuertas de carga. No hay `docker compose pull` global ni `up`
sueltos. Un `up` solo descarga las imágenes que falten al crear cada contenedor.

El job tiene un tope de 90 minutos. Un arranque en frío tarda 20–30 minutos. Ver
[09 §3](09-arranque-seguro.md#3-el-arranque-por-etapas).

### `smoke` — la validación end-to-end

Ejecuta `ci/smoke-ue.sh`. Si falla, recoge el diagnóstico con
`ci/collect-logs.sh` y lo sube como artefacto de la ejecución, con 7 días de
retención.

### `rollback` — solo si `deploy` o `smoke` fallaron

Solo corre si `deploy` o `smoke` fallaron (`needs.deploy` o `needs.smoke` con
resultado `failure`). Si todo va bien, no se ejecuta.

Para el RAN y la compresión **por nombre**:

```bash
lab5g.sh stop gnb ue dn-iperf cmp-gen cmp-ue cmp-dn envio envio-eco
```

Deja el **núcleo y la observabilidad en pie** para que puedas inspeccionarlos. No
hace un `down`: perder el estado justo cuando falla sería lo contrario de lo útil.

Los servicios parados así quedan anotados como parados a mano. La recuperación
automática no los revive hasta que alguien ejecute `lab5g.sh start`.

---

## 3. Instalar el runner

Dentro del LXC, con un usuario dedicado:

```bash
useradd -m -s /bin/bash deploy && usermod -aG docker deploy
```

Coge la URL y el token desde **Settings → Actions → Runners → New self-hosted
runner** en tu repositorio, y sigue las instrucciones que muestra esa página.

### Las etiquetas

Los dos workflows piden tres etiquetas concretas:

```yaml
runs-on: [ self-hosted, lxc, free5gc ]
```

Un runner recién instalado solo trae `self-hosted`, `Linux` y `X64`. **Si no le
añades `lxc` y `free5gc`, los trabajos se quedan encolados para siempre** sin
ningún mensaje de error — simplemente no hay ningún ejecutor que cumpla los
requisitos.

Se añaden durante `./config.sh` con `--labels lxc,free5gc`, o después desde la
web en Settings → Actions → Runners.

### Como servicio

```bash
sudo ./svc.sh install deploy && sudo ./svc.sh start
```

---

## 4. Preparar los permisos

Desde el checkout del repositorio, como root dentro del LXC:

```bash
sudo bash deploy/bootstrap-runner.sh
```

El script es idempotente. Hace lo siguiente:

- Instala `lab5g-netrules.sh` y su servicio, y los habilita.
- Valida y después instala [`deploy/sudoers-lab5g-deploy`](../deploy/sudoers-lab5g-deploy)
  con `visudo -cf` antes de escribirlo. Un fichero mal formado dejaría el sistema
  sin `sudo`, y recuperarlo requiere consola física.
- Crea `/etc/lab5g/lab5g.env` desde `.env.example` si no existe. Si ya existe, no
  lo toca. Avisa de que `GRAFANA_ADMIN_PASSWORD` tiene el valor de ejemplo.
- Crea `/opt/lab5g`, `/var/lib/lab5g` y `/var/backups/lab5g`.
- Instala `lab5g.sh`, `lab5g-stack.sh` y `lab5g-backup.sh` en `/usr/local/sbin/`,
  y las unidades `lab5g-stack.service`, `lab5g-backup.service` y
  `lab5g-backup.timer`. Las **habilita pero no las arranca**.
- Instala `python3-yaml`, que necesita `ci/check-plmn.py`.

Después, el primer arranque lo decide el operador o el pipeline:

```bash
sudo /usr/local/sbin/lab5g.sh sync && sudo /usr/local/sbin/lab5g.sh start
sudo systemctl start lab5g-stack
```

`lab5g.sh start` arranca la pila, pero **no la vigila**. El bucle de recuperación
es el servicio `lab5g-stack`, que bootstrap habilita sin arrancar. Hay que
arrancarlo con `systemctl start lab5g-stack`, o tras el próximo reinicio del LXC.
Ver [09 §2](09-arranque-seguro.md#2-las-piezas).

### Qué permite el sudoers

El runner corre como `deploy` y solo tiene `sudo -n` sin contraseña para estos
comandos:

| Comando | Para qué lo usa el pipeline |
|---|---|
| `lab5g-netrules.sh`, `iptables`, `ip` | Reglas de red de la LAN y comprobaciones de `preflight` |
| `lab5g.sh sync` | Copiar el código a `/opt/lab5g` |
| `lab5g.sh check` | Comprobación de capacidad |
| `lab5g.sh start` y `lab5g.sh start <nombres>` | Arranque escalonado. Los nombres se validan contra una lista cerrada |
| `lab5g.sh stop` y `lab5g.sh stop <nombres>` | Parada total y rollback |
| `lab5g.sh status`, `grafana up`, `grafana down`, `backup` | Gestión desde `observability-control` |
| `lab5g.sh env-set GRAFANA_ADMIN_PASSWORD` | Secreto de Grafana, con el valor por stdin |

`restore` no está en el sudoers. Solo lo ejecuta root a mano.

---

## 5. El secreto

En **Settings → Secrets and variables → Actions**, crea:

| Secreto | Contenido |
|---|---|
| `GRAFANA_ADMIN_PASSWORD` | La contraseña de admin de Grafana |

El job `deploy` escribe el secreto en `/etc/lab5g/lab5g.env` con
`lab5g.sh env-set`. El valor entra por stdin y nunca por argumento, así que no
aparece en `ps` ni en el log. El paso no usa `set -x`. `env-set` rechaza valores con comilla simple (`'`) o que
terminen en barra invertida, ni los de varias líneas: compose no los lee de forma fiable.

Ya no se genera ningún `.env` en cada job. El entorno vive fuera del checkout, y
`actions/checkout` ejecuta `git clean -ffdx` sin tocarlo. Por eso el job `smoke`
no tiene que regenerar nada.

---

## 6. Lanzarlo

### Automático

**`deploy.yml`** (pila completa) — un push a `master` que toque:

```
config/**  ueransim/**  compresion/**  deploy/**  ci/**
docker-compose.yaml  .github/workflows/deploy.yml
```

**`observability-control.yml`** — un push a `master` que toque:

```
observability/**  .github/workflows/observability-control.yml
```

En un push, este workflow solo sincroniza los archivos a `/opt/lab5g` y recarga
Prometheus (`/-/reload`). Solo lo hace desde `master`. Grafana relee
`provisioning/dashboards` cada 30 s, así que no hace falta recrear nada.

### A mano

`deploy.yml`, desde la pestaña Actions o por línea de comandos:

| Input | Por defecto | Qué hace |
|---|---|---|
| `skip_smoke` | `false` | Despliega sin ejecutar la prueba end-to-end |

```bash
gh workflow run deploy.yml --repo J4F3ET/lab-free5gc -f skip_smoke=true
```

`observability-control.yml` tiene un input `accion` con cuatro valores:

| `accion` | Qué hace |
|---|---|
| `grafana-up` | Enciende Grafana (`lab5g.sh grafana up`) |
| `grafana-down` | Apaga Grafana (`lab5g.sh grafana down`) |
| `estado` | Muestra el estado de la pila (`lab5g.sh status`) |
| `backup` | Copia de Prometheus (`lab5g.sh backup`) |

Antes de ejecutar la acción, este workflow sincroniza el código, para que Grafana
vea los dashboards del commit actual. Las acciones que modifican (`grafana-up`,
`grafana-down`, `backup`) se rechazan si el workflow no corre sobre `master`.
`estado` es de solo lectura y corre desde cualquier rama.

```bash
gh workflow run observability-control.yml --repo J4F3ET/lab-free5gc -f accion=grafana-up
```

### Pararlo del todo

Si el servidor está caído o en mantenimiento, conviene desactivar los workflows
para que un merge no dispare un despliegue contra una máquina que no está:

```bash
gh workflow disable deploy.yml --repo J4F3ET/lab-free5gc
gh workflow disable observability-control.yml --repo J4F3ET/lab-free5gc
```

```bash
gh workflow enable deploy.yml --repo J4F3ET/lab-free5gc
gh workflow enable observability-control.yml --repo J4F3ET/lab-free5gc
```

---

## 7. Concurrencia

```yaml
concurrency:
  group: lab5g-deploy
  cancel-in-progress: false
```

Nunca dos despliegues a la vez sobre el mismo LXC, y **sin cancelar el que ya
está corriendo**: interrumpir un despliegue a mitad deja el laboratorio en un
estado peor que dejarlo terminar.

**Los dos workflows usan el mismo `group: lab5g-deploy`**. El grupo de
concurrencia de GitHub Actions es global al repositorio, no por archivo de
workflow. Si `deploy.yml` está corriendo y se dispara `observability-control.yml`
(por un push a `observability/**` o a mano), GitHub Actions lo deja en cola en
vez de ejecutarlo en paralelo sobre el mismo host de 4 núcleos.

---

## 8. Los cinco fallos que ya nos pasaron

Están documentados porque son los que te encontrarás tú también.

### El mismo bug de `pipefail`, dos veces

`ci/wait-ready.sh` tenía un patrón como este para esperar a que algo
apareciera en un log:

```bash
docker compose logs "$svc" 2>&1 | grep -qiE "$pat"
```

Con `set -o pipefail`, esto falla aunque `grep` SÍ encuentre el patrón: en
cuanto `grep -q` encuentra la primera coincidencia, cierra su entrada, y
`docker compose logs` —que todavía está escribiendo el resto del
historial— recibe SIGPIPE y sale con código distinto de cero. `pipefail`
toma ese código, no el de `grep`, así que la tubería entera se reporta como
fallida. Ese bug ya se había encontrado y arreglado en `wait-ready.sh`
(commit `e88e32a`) cambiando a sustitución de proceso:

```bash
grep -qiE "$pat" < <(docker compose logs "$svc" 2>&1)
```

Lo que no se hizo fue revisar si el mismo patrón existía en otro sitio. Sí
existía: la función `wait_log` de `ci/smoke-ue.sh` tenía exactamente el
mismo pipe, y por eso el paso 1/6 del smoke test ("NG Setup (N2)") fallaba
con "sin NG Setup Response" en dos ejecuciones reales (`34793481927` y
`34796529773`) **aunque el log de `gnb` tuviera la línea `NG Setup procedure
is successful` desde el primer segundo**. La primera vez se le atribuyó al
load average del host (que sí estaba alto, 11.99, por una casualidad real de
contenedores de observabilidad sin detener) — una hipótesis razonable pero
equivocada, descartada al reproducirse el fallo idéntico en una segunda
corrida con el host descansado (load average 1.83). Se confirmó reproduciendo
las dos variantes en vivo contra el runner: la versión con pipe salía con
código 255 pese a que el patrón estaba ahí; la de sustitución de proceso,
con 0.

La lección: **un bug de `pipefail` en un patrón reutilizable no se arregla
una vez — se busca en todo el repositorio.** `grep -rn 'logs.*|.*grep' ci/`
antes de dar por cerrado un hallazgo así.

### El workflow no se disparaba nunca

Escuchaba pushes a `main`. La rama por defecto de este repositorio es `master`.
No había error: simplemente no pasaba nada.

### Los trabajos se quedaban encolados

El runner tenía las etiquetas por defecto y el workflow pedía `lxc` y `free5gc`.
Sin ejecutor que encaje, GitHub espera indefinidamente sin avisar.

### `pip install pyyaml` fallaba siempre

Debian 13 marca el entorno de Python como *externally managed* (PEP 668), y en
ese runner `pip` ni siquiera estaba instalado. Resultado: `No module named yaml`
y el job de validación caía en su tercer paso.

Ahora `pyyaml` va como paquete del sistema (`python3-yaml`) y el workflow solo
comprueba que se pueda importar, fallando con un mensaje que dice qué ejecutar.

### El preflight daba cinco falsos negativos

Todos por correr como usuario sin privilegios en lugar de como root:

| Comprobación | Por qué fallaba | Cómo se arregló |
|---|---|---|
| sudo | Probaba `sudo -n true`, y `true` no está en el sudoers | Prueba con `ip -V`, que sí está |
| `ip_forward`, `rp_filter` | `sysctl` no está en el `secure_path` del runner, y `command not found` se leía como valor 0 | Se leen de `/proc/sys` |
| `CAP_NET_ADMIN` | `capsh` inspeccionaba las capabilities del usuario, no las del contenedor | Se consulta el bounding set de PID 1 |
| gateway | ICMP está vedado a usuarios sin privilegios (`ping_group_range`) | Se cae hacia la tabla de vecinos y la ruta por defecto |
| `DOCKER-USER` | Buscaba reglas con la forma `-s <lan> -d <subred>`, y el script las crea con la forma `-i eth0 -d <subred>` | Se alineó el check con lo que el script genera |

La lección general: **un script de validación que corre como root y como usuario
sin privilegios tiene que probar lo que realmente le importa**, no lo que es
cómodo de comprobar. Un falso negativo en un preflight es peor que no tener
preflight, porque manda a depurar un problema que no existe.

---

**Anterior:** [06 — PLMN e identidades](06-plmn-e-identidades.md) ·
**Volver al** [README](../README.md)
