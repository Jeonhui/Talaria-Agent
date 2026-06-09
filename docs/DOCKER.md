# Docker 운영 가이드

운영자(관리자)가 한 대 호스트에서 Talaria를 Docker로 띄우고, 메신저 봇을 통해 다수의 일반 사용자에게 서비스하는 시나리오를 가정한다. 샌드박스 정책·skill 잠금·다중 인스턴스까지 포함한다.

목차
- [0. 사전 준비](#0-사전-준비)
- [1. 이미지 확보](#1-이미지-확보)
- [2. 호스트 측 `.env`](#2-호스트-측-env)
- [3. `.managed` 마커 + 기본 `config.yaml`](#3-managed-마커--기본-configyaml)
- [4. (선택) interactive setup 1회 실행](#4-선택-interactive-setup-1회-실행)
- [5. Production `docker-compose.yml`](#5-production-docker-composeyml)
- [6. 기동·검증](#6-기동검증)
- [7. Skill / 데이터 잠금](#7-skill--데이터-잠금)
- [8. 일상 운영](#8-일상-운영)
- [9. 멀티 인스턴스](#9-멀티-인스턴스)
- [10. 보안 체크리스트](#10-보안-체크리스트)

---

## 0. 사전 준비

호스트 요건
- Docker 24+ 또는 Podman 4+
- Linux 권장. macOS/Windows Docker Desktop도 가능하지만 `--cap-drop`, `--pids-limit`, `--storage-opt size=` 동작 보장은 Linux에서 가장 좋다.
- `~/.talaria` 디렉토리의 소유자가 될 호스트 사용자 계정을 결정한다. 예: `talaria-admin` (UID/GID 1001).
- 외부 LLM API 키 / 메신저 봇 토큰을 미리 발급해 둔다 (Anthropic / OpenAI / OpenRouter / Telegram / Discord / Slack 중 사용할 것만).

호스트 디렉토리 생성

```bash
sudo mkdir -p /srv/talaria
sudo chown 1001:1001 /srv/talaria
```

이 경로는 컨테이너 안 `/opt/data`, 즉 `TALARIA_HOME`으로 연결된다. 모든 설정·세션 DB·로그·skill·sandbox 작업공간이 이 아래로 들어온다.

---

## 1. 이미지 확보

**옵션 A — GHCR pre-built (권장)**

```bash
docker pull ghcr.io/jeonhui/talaria-agent:v0.1.0
# 또는
docker pull ghcr.io/jeonhui/talaria-agent:latest
```

multi-arch (linux/amd64, linux/arm64) 자동 선택.

**옵션 B — 로컬 빌드**

```bash
cd /path/to/Talaria-Agent
docker build -t talaria-agent:local .
```

운영용으로는 `:latest`보다 버전 태그(`v0.1.0`)를 고정하는 것이 안전하다.

---

## 2. 호스트 측 `.env`

`/srv/talaria/.env`는 컨테이너 안에서 `~/.talaria/.env`로 보인다. 권한은 600.

```bash
sudo -u \#1001 tee /srv/talaria/.env > /dev/null <<'EOF'
# === Provider — 사용할 것만 ===
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=
# OPENROUTER_API_KEY=

# === Messenger ===
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_ALLOWED_USERS=11111111,22222222
TELEGRAM_HOME_CHANNEL=11111111

# (필요 시) DISCORD_BOT_TOKEN=..., SLACK_BOT_TOKEN=...

# === Gateway 접근 제어 ===
GATEWAY_ALLOW_ALL_USERS=false        # 기본 deny

# === 관리 모드 — 유저가 config/setup 못 만지게 ===
TALARIA_MANAGED=true

# === API 서버는 기본 OFF. 열려면 둘 다 ===
# API_SERVER_HOST=127.0.0.1
# API_SERVER_KEY=                      # openssl rand -hex 32 권장
EOF

sudo chmod 600 /srv/talaria/.env
sudo chown 1001:1001 /srv/talaria/.env
```

`TALARIA_MANAGED=true`는 setup/config CLI가 `is_managed()` 분기를 타고 사용자 변경을 거부하도록 만드는 핵심 스위치다.

---

## 3. `.managed` 마커 + 기본 `config.yaml`

`.managed` 파일은 환경변수와 별개로 managed 상태를 보장하는 안전망이다.

```bash
sudo -u \#1001 touch /srv/talaria/.managed
```

`config.yaml`은 첫 실행 시 자동 생성되지만 운영용은 미리 박아두는 편이 깔끔하다.

```bash
sudo -u \#1001 tee /srv/talaria/config.yaml > /dev/null <<'EOF'
model:
  provider: anthropic
  default: claude-sonnet-4-6

agent:
  terminal_backend: docker           # 절대 local 금지
  terminal:
    image: ghcr.io/jeonhui/talaria-agent:v0.1.0
    persistent_filesystem: false     # 유저 세션 leak 방지
    network: false                   # 기본 차단; 필요 시만 true
    cpu: 2
    memory: 2048
    pids_limit: 256

security:
  allow_private_urls: false
  website_blocklist:
    enabled: true
    domains:
      - "*.internal"
      - "metadata.google.internal"

skills:
  guard_agent_created: true          # agent가 만든 skill도 정적 스캔

gateway:
  allowed_users: [11111111, 22222222]
EOF
```

> **주의 — DooD(Docker-out-of-Docker)**
> `terminal_backend: docker`는 컨테이너 안에서 또 다른 docker를 호출한다. 이미지는 `docker-cli`를 포함하지만 `/var/run/docker.sock`을 마운트해야 동작한다. sock 마운트는 호스트 root 권한과 사실상 동등하므로 신뢰 경계를 명확히 인지하고 사용한다. 같은 호스트에서 DooD가 부담스러우면 별도 호스트의 Docker daemon을 TCP+TLS로 연결하는 방식도 검토할 만하다.

---

## 4. (선택) interactive setup 1회 실행

이미 `config.yaml` / `.env`를 박아 두었다면 건너뛴다. wizard로 채우고 싶다면 `.managed`를 만들기 *전에* 실행한다.

```bash
docker run --rm -it \
  -v /srv/talaria:/opt/data \
  -e TALARIA_UID=1001 -e TALARIA_GID=1001 \
  ghcr.io/jeonhui/talaria-agent:v0.1.0 setup
```

managed 상태에서는 wizard가 거부한다. wizard가 끝났으면 `touch /srv/talaria/.managed`로 잠근다.

---

## 5. Production `docker-compose.yml`

레포 기본 `docker-compose.yml`을 하드닝한 버전. 파일 위치는 `/srv/talaria/docker-compose.yml`.

```yaml
services:
  gateway:
    image: ghcr.io/jeonhui/talaria-agent:v0.1.0
    container_name: talaria
    restart: unless-stopped

    # network_mode: host 는 편하지만 격리가 약하다.
    # bridge + 명시 포트가 안전.
    networks: [talaria_net]

    volumes:
      - /srv/talaria:/opt/data
      # terminal_backend=docker 라면 sock 마운트 필요 (DooD)
      - /var/run/docker.sock:/var/run/docker.sock
      # sandbox 작업공간을 별도 디스크에 두려면:
      # - /srv/talaria-sandboxes:/opt/data/sandboxes

    environment:
      - TALARIA_UID=1001
      - TALARIA_GID=1001
      - TALARIA_MANAGED=true
      # 필요 시 API 서버 노출 (둘 다 또는 둘 다 X)
      # - API_SERVER_HOST=0.0.0.0
      # - API_SERVER_KEY=${API_SERVER_KEY}

    # ── 컨테이너 자체 하드닝 ─────────────────────────────
    cap_drop: [ALL]
    cap_add: [DAC_OVERRIDE, CHOWN, FOWNER, SETUID, SETGID]
    security_opt:
      - no-new-privileges:true
    pids_limit: 1024
    tmpfs:
      - /tmp:rw,nosuid,size=512m
      - /run:rw,noexec,nosuid,size=64m

    # 리소스 천장 (운영 환경 맞춰서 조정)
    deploy:
      resources:
        limits:
          cpus: "4"
          memory: 4G

    # 메신저 webhook 쓸 때만 포트 필요. 폴링이면 outbound only.
    # ports:
    #   - "8443:8443"               # Telegram webhook
    #   - "127.0.0.1:8080:8080"     # API server (localhost 한정)

    # systemd 같은 hang 감지 원하면:
    healthcheck:
      test: ["CMD", "/opt/talaria/talaria", "doctor", "--quick"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s

networks:
  talaria_net:
    driver: bridge
```

`SETUID`/`SETGID`는 컨테이너가 root로 시작해 `gosu`로 권한 강하할 때 필요하다. `--user=1001:1001`로 시작해 강하 단계를 건너뛸 수 있으면 두 cap도 떨어뜨려도 된다.

---

## 6. 기동·검증

```bash
cd /srv/talaria
sudo docker compose pull          # 또는 build
sudo docker compose up -d
sudo docker compose logs -f gateway
```

기동 확인 포인트
- 로그에 `Gateway listening` / `Telegram bot started` 같은 라인이 보이는가
- `/srv/talaria/state.db`가 생성되었는가
- `/srv/talaria/logs/`에 로그가 쌓이는가
- 메신저에서 봇에게 `/start` 보내면 allowed user인지 검증 후 응답하는가

내부 진단

```bash
sudo docker compose exec gateway /opt/talaria/talaria doctor
sudo docker compose exec gateway /opt/talaria/talaria config show
```

---

## 7. Skill / 데이터 잠금

`skill_manager_tool`은 현재 `is_managed()`를 확인하지 않는다 (2026-06 기준). 운영자가 별도 잠금을 걸어야 사용자가 agent를 통해 skill을 마음대로 만들거나 덮어쓸 수 없다.

```bash
# 1) bundled skill 모두 pin (agent가 덮어쓰지 못하게)
sudo docker compose exec gateway bash -lc '
  for d in /opt/data/skills/*; do
    name=$(basename "$d")
    /opt/talaria/talaria curator pin "$name" || true
  done
'

# 2) (선택) skills 디렉토리 자체를 read-only로 마운트
#    compose 파일에 추가:
#    volumes:
#      - /srv/talaria/skills:/opt/data/skills:ro
#    단, 이 경우 agent의 skill_manager.create/edit 호출이 실패한다.
#    운영자가 호스트에서 직접 편집하는 모델이 된다.
```

권한 정리

```bash
sudo chmod 600 /srv/talaria/.env
sudo chmod 644 /srv/talaria/config.yaml
sudo chmod 600 /srv/talaria/state.db
```

---

## 8. 일상 운영

| 작업 | 명령 |
|---|---|
| 재기동 | `docker compose restart gateway` |
| 업그레이드 | `docker compose pull && docker compose up -d` |
| 로그 확인 | `docker compose logs -f gateway` 또는 `/srv/talaria/logs/agent.log` |
| 쉘 진입 | `docker compose exec gateway bash` |
| 설정 변경 | 호스트에서 `/srv/talaria/config.yaml` 편집 후 restart |
| 백업 | `tar czf talaria-$(date +%F).tgz /srv/talaria` (`.env`는 별도 보관) |
| 봇 토큰 회전 | `.env` 수정 후 restart |
| 컨테이너 폐기 | `docker compose down` — 볼륨은 유지됨 |

`tini`가 PID 1로 동작하므로 MCP stdio·git 자식 등 좀비 프로세스 정리는 자동이다.

---

## 9. 멀티 인스턴스

같은 호스트에서 별도 봇·팀 단위로 다수 인스턴스를 띄울 수 있다. Talaria 코드(특히 `TALARIA_HOME` 분기, UID/GID 리맵, per-task sandbox)는 multi-instance를 무리 없이 지원한다. 막히는 곳은 인프라 레이어(컨테이너 이름·네트워크·볼륨)다.

인스턴스 분리 원칙
- `TALARIA_HOME` 호스트 디렉토리를 인스턴스마다 다르게: `/srv/talaria-teamA`, `/srv/talaria-teamB`
- `container_name`을 다르게: `talaria-teamA`, `talaria-teamB`
- 호스트 포트도 다르게 (예: 8081, 8082)
- 봇 토큰 / 메신저 채널도 다르게
- LLM API 키는 공유해도 무방 (rate limit만 주의)
- compose project name을 분리해 stack 충돌 방지: `docker compose -p talaria-teamA`

`docker-compose.teamA.yml` 예시

```yaml
services:
  gateway:
    image: ghcr.io/jeonhui/talaria-agent:v0.1.0
    container_name: talaria-teamA
    restart: unless-stopped
    networks: [talaria_teamA_net]
    volumes:
      - /srv/talaria-teamA:/opt/data
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - TALARIA_UID=1001
      - TALARIA_GID=1001
      - TALARIA_MANAGED=true
    cap_drop: [ALL]
    cap_add: [DAC_OVERRIDE, CHOWN, FOWNER, SETUID, SETGID]
    security_opt:
      - no-new-privileges:true
    pids_limit: 1024
    # 외부에 노출할 게 있다면:
    # ports:
    #   - "8081:8080"

networks:
  talaria_teamA_net:
    driver: bridge
```

기동

```bash
docker compose -p talaria-teamA -f docker-compose.teamA.yml up -d
docker compose -p talaria-teamB -f docker-compose.teamB.yml up -d
```

각 인스턴스 `state.db`가 분리되므로 세션·메모리·메시지 leak이 차단된다. 단, `~/.talaria-teamA/.env`도 인스턴스마다 독립이어야 하므로 토큰·키도 별도 보관한다.

---

## 10. 보안 체크리스트

운영 시작 전 한 번 훑는다.

- [ ] `.env` 권한 600, 운영자만 read
- [ ] `TALARIA_MANAGED=true` + `.managed` 마커 모두 존재
- [ ] `config.yaml`: `terminal_backend: docker`, `network: false`, `allow_private_urls: false`
- [ ] `website_blocklist.enabled: true`
- [ ] `skills.guard_agent_created: true`
- [ ] bundled skill pin 완료
- [ ] `gateway.allowed_users` 명시 / `GATEWAY_ALLOW_ALL_USERS=false`
- [ ] `API_SERVER_HOST=0.0.0.0`이 켜져 있지 않음 (켰다면 `API_SERVER_KEY` 32바이트 이상)
- [ ] compose에 `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`, `tmpfs` 적용
- [ ] `network_mode: host`를 끄고 bridge + 명시 포트 사용
- [ ] docker sock 마운트가 정말 필요한지 재검토 — 필요 없다면 떼라 (호스트 root와 동등한 권한)
- [ ] 이미지 태그 고정 (`v0.1.0`), 운영 환경에서는 `:latest` 금지
- [ ] healthcheck 동작 확인
- [ ] 백업 cron 설정 (`/srv/talaria` 전체 tarball)
- [ ] (멀티 인스턴스) 인스턴스마다 `TALARIA_HOME`·container_name·포트·토큰 모두 분리

---

## 참고

- 컨테이너 내부 사용자: `talaria` (UID 10000). entrypoint가 `TALARIA_UID`/`TALARIA_GID`로 리맵 후 `gosu`로 강하한다. 자세한 동작은 `docker/entrypoint.sh`.
- `TALARIA_HOME` 기본값은 `~/.talaria`, 컨테이너 안에서는 `/opt/data`로 고정 (`Dockerfile`의 `ENV TALARIA_HOME=/opt/data`).
- Playwright 브라우저는 `/opt/talaria/.playwright`에 별도로 설치되어 볼륨 오버레이로 사라지지 않는다.
- SSRF 차단·website blocklist·skill 정적 스캐너 등 샌드박스 정책 상세는 `tools/url_safety.py`, `tools/website_policy.py`, `tools/skills_guard.py` 참고.
