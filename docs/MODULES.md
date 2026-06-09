# 모듈 개발 가이드

Talaria의 **모듈**은 외부 서비스 연동을 한 단위로 묶은 swappable 패키지다. 활성 모듈을 바꾸면 신원 인증·MCP 엔드포인트·도구·context·skill·로깅이 한꺼번에 교체된다. 추가로 한 단계 작은 단위인 **plugin** (memory provider, context engine 같은 영역별 구현체)이 별도로 존재한다.

목차
- [1. 두 종류의 모듈](#1-두-종류의-모듈)
- [2. Integration module 만들기](#2-integration-module-만들기)
  - [2.1 디렉토리 레이아웃](#21-디렉토리-레이아웃)
  - [2.2 `IntegrationModule` ABC](#22-integrationmodule-abc)
  - [2.3 `UserInfo` 와 인가 모델](#23-userinfo-와-인가-모델)
  - [2.4 `register(ctx)` 진입점](#24-registerctx-진입점)
  - [2.5 활성화](#25-활성화)
  - [2.6 환경변수와 `get_config_schema`](#26-환경변수와-get_config_schema)
- [3. 식별 백엔드(HTTP) 계약](#3-식별-백엔드http-계약)
- [4. Memory provider plugin 만들기](#4-memory-provider-plugin-만들기)
- [5. 사용자 설치 경로](#5-사용자-설치-경로)
- [6. 테스트](#6-테스트)
- [7. 배포 / 공유](#7-배포--공유)
- [8. 자주 하는 실수](#8-자주-하는-실수)

---

## 1. 두 종류의 모듈

| 종류 | 디렉토리 | ABC | 동시 활성 | 책임 |
|---|---|---|---|---|
| **Integration module** | `integrations/<name>/` | `agent.integration_module.IntegrationModule` | 1개 | identity + MCP + tools + context + skills + logging 묶음 |
| **Plugin (memory)** | `plugins/memory/<name>/` | `agent.memory_provider.MemoryProvider` | 1개 | 장기 기억 저장/검색 백엔드 |
| **Plugin (context engine)** | `plugins/context_engine/<name>/` | `agent.context_engine.*` | 1개 | 컨텍스트 압축/요약 엔진 |

Discovery는 두 단계로 일어난다.

1. **번들** — 레포 안 `integrations/<name>/` 또는 `plugins/<kind>/<name>/`
2. **사용자 설치** — `$TALARIA_HOME/integrations/<name>/` 또는 `$TALARIA_HOME/plugins/<name>/`

이름 충돌 시 **번들이 우선**한다. 활성 모듈은 `config.yaml`에서 명시적으로 선택한다 (`integration.module: <name>`, `memory.provider: <name>` 등).

---

## 2. Integration module 만들기

가장 빠른 방법은 `integrations/example/`을 그대로 복사해 이름을 바꾸고 필요한 메서드만 갈아끼우는 것이다. 다음은 처음부터 만든다고 가정한 설명이다.

### 2.1 디렉토리 레이아웃

```
integrations/cocso/                # 또는 $TALARIA_HOME/integrations/cocso/
├── __init__.py                    # IntegrationModule 구현 + register(ctx)
├── plugin.yaml                    # 메타데이터 (선택, setup wizard용)
├── README.md                      # (선택)
└── ...                            # 모듈 전용 보조 파일
```

`plugin.yaml` 최소 예시:

```yaml
name: cocso
description: COCSO(코쏘) 정산 서비스 — identity + MCP + 사용자별 메모리
```

> 디렉토리 이름 = 모듈 이름. `_`나 `.`로 시작하는 디렉토리는 스킵된다.

### 2.2 `IntegrationModule` ABC

`agent/integration_module.py`의 `IntegrationModule`을 상속한다. 호출 시점은 다음과 같다.

| 메서드 | 호출 시점 | 비고 |
|---|---|---|
| `name` (property) | 항상 | 모듈 식별자. 디렉토리명과 일치 권장 |
| `is_available()` | 활성화 결정 시 | 네트워크 호출 금지. env/config/deps 존재 여부만 |
| `get_config_schema()` | `talaria integration setup` | wizard용 필드 목록 |
| `save_config(values, talaria_home)` | wizard 저장 시 | secret은 `.env`, 일반값은 `config.yaml` |
| `mcp_url()` / `mcp_key()` | 시작 시 1회 + MCP 재연결 시 | 모듈 레벨 단일 자격증명 |
| `resolve_user(...)` | 인바운드 메시지마다 (캐시 가능) | **fail-closed** 권장 |
| `available_tools(user)` | 에이전트 부트 시 | `None`=전부, `[]`=MCP 차단, `[...]`=화이트리스트 |
| `context_files(user)` | 새 세션 생성 시 | 절대경로 리스트 반환 |
| `skills(user)` | 새 세션 생성 시 | skill name 리스트 |
| `log_message(user, text, **ctx)` | 인바운드 메시지마다 | 비동기 무결성 보장 X — 빠르게 |
| `log_response(user, text, **ctx)` | 아웃바운드 응답마다 | 위와 동일 |
| `initialize(**kwargs)` | 게이트웨이 시작 시 | warm-up, DB 풀 오픈 등 |
| `shutdown()` | 게이트웨이 종료 시 | flush, close |

최소 골격:

```python
# integrations/cocso/__init__.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from agent.integration_module import IntegrationModule, UserInfo


class CocsoModule(IntegrationModule):
    @property
    def name(self) -> str:
        return "cocso"

    # -- Setup --------------------------------------------------------------

    def is_available(self) -> bool:
        return bool(os.getenv("COCSO_MCP_URL") and os.getenv("COCSO_MCP_KEY"))

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {"key": "mcp_url", "env_var": "COCSO_MCP_URL", "required": True,
             "description": "COCSO MCP endpoint URL"},
            {"key": "mcp_key", "env_var": "COCSO_MCP_KEY", "required": True,
             "secret": True, "description": "COCSO bearer key"},
            {"key": "identity_url", "env_var": "COCSO_IDENTITY_URL",
             "description": "Identity backend (선택)"},
        ]

    # -- MCP info -----------------------------------------------------------

    def mcp_url(self) -> str:
        return os.getenv("COCSO_MCP_URL", "")

    def mcp_key(self) -> str:
        return os.getenv("COCSO_MCP_KEY", "")

    # -- Identity -----------------------------------------------------------

    def resolve_user(self, *, mcp_key: str, platform: str,
                     user_id: str, user_name: str = "", **kwargs) -> UserInfo:
        # 여기서 외부 백엔드를 부르거나 정적 allowlist를 적용한다.
        # 실패 시 authorized=False (fail-closed).
        return UserInfo(
            user_id=user_id,
            platform=platform,
            name=user_name or user_id,
            authorized=True,
            attributes={"_tools": "*", "_context_files": [], "_skills": []},
        )

    def available_tools(self, user: UserInfo) -> Optional[List[str]]:
        tools = user.attributes.get("_tools", "*")
        if tools == "*":
            return None
        return list(tools) if isinstance(tools, (list, tuple)) else []


def register(ctx) -> None:
    ctx.register_integration_module(CocsoModule())
```

### 2.3 `UserInfo` 와 인가 모델

`UserInfo`는 dataclass다.

```python
@dataclass
class UserInfo:
    user_id: str
    platform: str = ""           # "telegram" | "discord" | "slack" | ...
    name: str = ""
    authorized: bool = False     # False면 게이트웨이가 차단
    attributes: Dict[str, Any] = field(default_factory=dict)
```

`attributes`는 자유 형식이다. 백엔드 응답이나 도메인 필드(`org_id`, `role`, `plan` 등)를 그대로 담아 두면 `available_tools` / `context_files` / `skills` 가 같은 객체를 다시 읽어 결정한다. `_tools` / `_context_files` / `_skills` 같은 언더스코어 prefix는 모듈 내부 컨벤션이다 — 외부에 노출할 필요 없는 백엔드 응답 캐시를 표시할 때 쓴다.

### 2.4 `register(ctx)` 진입점

discovery는 `__init__.py` 안에서 두 가지를 찾는다.

1. `IntegrationModule` 서브클래스가 **모듈 레벨**에 정의되어 있으면 자동으로 인스턴스를 만든다.
2. `register(ctx)` 함수가 있으면 그것을 호출한다. 내부에서 `ctx.register_integration_module(instance)` 호출.

두 패턴 다 동작하지만 의존성 주입·인스턴스 옵션 전달이 필요하면 `register(ctx)` 패턴을 쓰는 게 깔끔하다.

### 2.5 활성화

```yaml
# ~/.talaria/config.yaml
integration:
  module: cocso
```

활성화되면 게이트웨이는 모든 메신저 메시지에 대해 `resolve_user()`를 호출하고, 인가된 유저에 대해서만 세션을 시작한다. `available_tools()` 반환값이 MCP tool 노출 화이트리스트로 적용된다.

여러 모듈을 디스크에 두고 환경별로 갈아끼우는 패턴이 일반적이다. CI/스테이징/프로덕션마다 다른 `config.yaml`을 마운트한다.

### 2.6 환경변수와 `get_config_schema`

`get_config_schema` 가 반환하는 필드 한 개당 wizard가 prompt 하나를 띄운다. 키는 다음과 같다.

| 키 | 효과 |
|---|---|
| `key` | 내부 식별자 (config.yaml 내 키) |
| `env_var` | 매핑되는 환경변수 이름. `.env`에 기록됨 |
| `required` | True면 미설정 시 오류 |
| `secret` | True면 wizard가 echo 끄고, `.env`에만 저장 |
| `default` | 기본값 |
| `choices` | 라디오 선택지 |
| `url` | "Get key at: <url>" 안내 출력 |
| `description` | 한 줄 설명 |

`is_available()`은 wizard와 무관하게 **모듈이 동작 가능한지**만 빠르게 검사해야 한다. 네트워크/DB 호출 금지.

---

## 3. 식별 백엔드(HTTP) 계약

`resolve_user`를 HTTP 백엔드에 위임하는 경우의 권장 계약이다 (example 모듈이 따르는 형식).

요청:

```
POST {IDENTITY_URL}
Authorization: Bearer {MCP_KEY}
Content-Type: application/json

{
  "platform": "telegram",
  "user_id":  "123456",
  "user_name": "Alice"
}
```

응답:

```json
{
  "authorized": true,
  "name": "Alice",
  "tools": ["search", "lookup"],
  "context_files": ["/data/alice/brief.md"],
  "skills": ["triage"],
  "attributes": {"org_id": "acme", "role": "admin"}
}
```

규칙:
- `authorized`만 필수. 나머지는 모두 선택.
- `tools` 가 `"*"` 또는 누락이면 제한 없음. 빈 배열이면 MCP 도구 전부 차단.
- HTTP 호출은 **짧은 timeout** (기본 3초). 게이트웨이 async 경로에서 호출되므로 블록되면 다른 채팅이 멈춘다.
- 실패는 **fail-closed**. 인증 백엔드가 죽으면 인가도 죽는다.
- 결과는 모듈 내부에서 캐싱하는 게 안전하다 (예: 5분 LRU). resolve_user는 메시지마다 불릴 수 있다.

---

## 4. Memory provider plugin 만들기

`plugins/memory/<name>/` 또는 `$TALARIA_HOME/plugins/<name>/`에 둔다. ABC는 `agent.memory_provider.MemoryProvider`.

골격:

```python
# plugins/memory/my_provider/__init__.py
from agent.memory_provider import MemoryProvider


class MyMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "my_provider"

    def is_available(self) -> bool:
        return bool(os.getenv("MY_MEM_DB_URL"))

    def get_config_schema(self):
        return [{"key": "db_url", "env_var": "MY_MEM_DB_URL", "required": True}]

    def store(self, key: str, value: str, **kwargs) -> None:
        ...

    def recall(self, query: str, k: int = 5, **kwargs):
        ...

    # 그 외 ABC 메서드


def register_memory_provider(ctx):
    ctx.register(MyMemoryProvider())
```

활성화:

```yaml
memory:
  provider: my_provider
```

discovery는 `__init__.py` 안에 `register_memory_provider` 또는 `MemoryProvider` 키워드가 보이면 인식한다 (정적 텍스트 스캔이라 import 비용은 없음).

---

## 5. 사용자 설치 경로

모듈을 레포에 PR로 올리지 않고 운영 서버에만 설치하고 싶으면 `$TALARIA_HOME/integrations/<name>/`에 그대로 풀어두면 된다.

```bash
mkdir -p ~/.talaria/integrations/cocso
cp -r ./cocso/* ~/.talaria/integrations/cocso/
talaria config set integration.module cocso
talaria gateway run
```

Docker 운영 환경이면 `/opt/data/integrations/cocso/`로 마운트되도록 호스트 디렉토리에 풀고 컨테이너 재기동.

> 경고: 사용자 설치 모듈도 `IntegrationModule`을 그대로 상속한다. 즉 임의 코드 실행과 동등하다. 신뢰할 수 없는 출처의 모듈을 깔지 마라. skill 보안 스캐너(`tools/skills_guard.py`)는 모듈에는 적용되지 않는다.

---

## 6. 테스트

번들 모듈은 `tests/` 디렉토리 안에 단위 테스트를 둔다. 패턴:

```python
# tests/integrations/test_cocso.py
from integrations.cocso import CocsoModule
from agent.integration_module import UserInfo

def test_resolve_user_local_demo(monkeypatch):
    monkeypatch.setenv("COCSO_MCP_URL", "https://example.com/mcp")
    monkeypatch.setenv("COCSO_MCP_KEY", "test")
    monkeypatch.setenv("COCSO_ALLOWED_USERS", "alice,bob")

    m = CocsoModule()
    info = m.resolve_user(
        mcp_key="test", platform="telegram",
        user_id="alice", user_name="Alice",
    )
    assert info.authorized is True
```

discovery 자체를 테스트하려면 `integrations.discover_integration_modules()`를 호출하고 모듈 이름이 목록에 보이는지 확인한다.

수동 검증:

```bash
# 디스커버리에 잡히는지
python -c "from integrations import discover_integration_modules; print(discover_integration_modules())"

# 로드되는지
python -c "from integrations import load_integration_module; m = load_integration_module('cocso'); print(m, m.is_available())"
```

---

## 7. 배포 / 공유

세 가지 패턴:

1. **레포 번들** — `integrations/<name>/` 추가 후 PR. 모든 사용자에게 기본 노출.
2. **사용자 디렉토리 배포** — 다른 사용자에게 `*.tar.gz`로 배포하고 `~/.talaria/integrations/<name>/`에 풀게 한다.
3. **별도 Git 저장소** — `git clone` 후 `~/.talaria/integrations/<name>/` 심볼릭링크. 업데이트는 `git pull`.

`plugin.yaml`을 두면 향후 install/list CLI가 메타데이터를 읽을 수 있고, `skill`처럼 hub 기반 배포로 확장하기도 쉽다.

---

## 8. 자주 하는 실수

- **`is_available()`에서 네트워크 호출** — 게이트웨이 부트가 느려지고 외부 장애 시 모듈 자체가 안 뜬다. env/파일/dep 존재 여부만 검사.
- **`resolve_user`에서 캐시 없이 매번 외부 호출** — 메시지마다 외부 인증을 때려 백엔드와 게이트웨이 양쪽에 부하. LRU + short TTL 필수.
- **fail-open 인증** — 백엔드 장애 시 `authorized=True`로 떨어뜨리지 마라. `False`가 안전.
- **모듈 레벨 단일 MCP key** — 현재 ABC는 모듈 단일 자격증명을 가정한다. 멀티 테넌트가 필요하면 모듈 자체를 인스턴스마다 분리(인스턴스 멀티)하는 게 단순하다.
- **`context_files`에서 매번 새 파일 생성** — 새 세션마다 호출되므로 누적 디스크 사용량을 막을 캡/정리 로직이 필요하다 (example의 `_mem_cap` 패턴 참고).
- **`available_tools` 의 None vs []** — `None`은 "제한 없음", `[]`은 "MCP 도구 전부 차단". 혼동하면 의도와 정반대 동작.
- **번들 이름과 사용자 디렉토리 이름 충돌** — 번들이 우선이라 사용자 디렉토리가 silent skip 된다. 이름 충돌 피하기.
- **secret을 `config.yaml`에 저장** — `save_config`는 secret을 `.env`로 분리해야 한다 (`secret: True` 필드). config.yaml은 평문 백업 대상이라 새기 쉬움.

---

## 참고 소스

- ABC: `agent/integration_module.py`
- Discovery / loader: `integrations/__init__.py`
- 레퍼런스 구현: `integrations/example/__init__.py` (HTTP identity + per-user memory)
- Memory plugin discovery: `plugins/__init__.py`, `plugins/memory/`
- 활성화 분기: `talaria_cli/integration_setup.py`, `talaria_cli/memory_setup.py`
