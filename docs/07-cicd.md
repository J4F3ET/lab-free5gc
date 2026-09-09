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
- [7. Los cuatro fallos que ya nos pasaron](#7-los-cuatro-fallos-que-ya-nos-pasaron)

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

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) tiene cuatro
trabajos encadenados:

```
validate  →  deploy  →  smoke  →  (rollback, solo si algo falló)
```

### `validate` — sin tocar nada de lo que está corriendo

Tres comprobaciones estáticas:

1. `ci/preflight.sh` — las 13 condiciones del entorno
2. `docker compose config --quiet` — sintaxis del compose
3. `ci/check-plmn.py` — coherencia de los tres números

Si algo falla aquí, **el despliegue no llega a empezar**. Es deliberado: es mucho
más barato fallar en la validación que a mitad del arranque.

### `deploy` — por etapas

Y las etapas importan:

```
1. Generar .env a partir del secreto
2. Reaplicar las reglas de red del host
3. docker compose pull
4. mongodb, con --wait --wait-timeout 600     ← espera de verdad
5. El plano de control
6. ci/wait-ready.sh
7. ci/provision-subscriber.sh
8. El perfil 'ran'
9. (opcional) 'obs' y 'probes', solo si se pidió
```

El paso 4 es el que se añadió después de un fallo real: arrancar los 16
servicios de golpe hacía que MongoDB compitiera por disco justo con los
servicios que esperaban su chequeo de salud. Ahora cada etapa imprime un `ps`,
así que un fallo dice qué quedó arriba y qué no.

**La observabilidad no se levanta por defecto.** Está detrás de un input
explícito, porque son 8 contenedores más y en un host ajustado esa es la
diferencia entre arrancar y no arrancar.

### `smoke` — la validación end-to-end

Ejecuta `ci/smoke-ue.sh`. Si falla, recoge el diagnóstico y lo sube como
artefacto de la ejecución, con 7 días de retención.

### `rollback` — solo si algo falló

Para la RAN y las sondas, y **deja el núcleo en pie** para que puedas
inspeccionarlo. No hace un `down`: perder el estado justo cuando falla sería lo
contrario de lo útil.

---

## 3. Instalar el runner

Dentro del LXC, con un usuario dedicado:

```bash
useradd -m -s /bin/bash deploy && usermod -aG docker deploy
```

Coge la URL y el token desde **Settings → Actions → Runners → New self-hosted
runner** en tu repositorio, y sigue las instrucciones que muestra esa página.

### Las etiquetas

El workflow pide tres etiquetas concretas:

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

El runner corre sin privilegios, pero necesita aplicar reglas de iptables. La
solución es un sudoers acotado a exactamente tres comandos:

```bash
sudo bash deploy/bootstrap-runner.sh
```

Ese script:

- Instala `lab5g-netrules.sh` en `/usr/local/sbin/`
- Instala y habilita el servicio de systemd
- Instala [`deploy/sudoers-lab5g-deploy`](../deploy/sudoers-lab5g-deploy), que
  permite **solo** `lab5g-netrules.sh`, `iptables` e `ip`, sin contraseña
- Instala `python3-yaml`
- **Valida el sudoers con `visudo -cf` antes de escribirlo**

Ese último punto no es paranoia: un fichero de sudoers mal formado deja el
sistema entero sin `sudo`, y recuperarlo requiere consola física.

---

## 5. El secreto

En **Settings → Secrets and variables → Actions**, crea:

| Secreto | Contenido |
|---|---|
| `GRAFANA_ADMIN_PASSWORD` | La contraseña de admin de Grafana |

El workflow genera el `.env` en cada ejecución a partir de `.env.example` más
ese secreto.

> **Ojo con un detalle nada obvio:** `actions/checkout` ejecuta `git clean -ffdx`,
> que borra el `.env` por no estar trackeado. Como el job de `smoke` hace su
> propio checkout, el `.env` que creó `deploy` desaparece — y sin él las
> variables de imagen quedan vacías y `docker compose exec` apunta a servicios
> equivocados. Por eso el job de smoke lo regenera. Si añades un job nuevo que
> use compose, tendrá que hacer lo mismo.

---

## 6. Lanzarlo

### Automático

Un push a `master` que toque alguno de estos caminos:

```
config/**  ueransim/**  observability/**  docker-compose.yaml  .github/workflows/deploy.yml
```

### A mano

Desde la pestaña Actions, con dos opciones:

| Input | Por defecto | Qué hace |
|---|---|---|
| `skip_smoke` | `false` | Despliega sin ejecutar la prueba end-to-end |
| `con_observabilidad` | `false` | Levanta también Grafana, Prometheus y las sondas |

O por línea de comandos:

```bash
gh workflow run deploy.yml --repo J4F3ET/lab-free5gc -f con_observabilidad=true
```

### Pararlo del todo

Si el servidor está caído o en mantenimiento, conviene desactivar el workflow
para que un merge no dispare un despliegue contra una máquina que no está:

```bash
gh workflow disable deploy.yml --repo J4F3ET/lab-free5gc
```

```bash
gh workflow enable deploy.yml --repo J4F3ET/lab-free5gc
```

### Concurrencia

```yaml
concurrency:
  group: lab5g-deploy
  cancel-in-progress: false
```

Nunca dos despliegues a la vez sobre el mismo LXC, y **sin cancelar el que ya
está corriendo**: interrumpir un despliegue a mitad deja el laboratorio en un
estado peor que dejarlo terminar.

---

## 7. Los cuatro fallos que ya nos pasaron

Están documentados porque son los que te encontrarás tú también.

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
