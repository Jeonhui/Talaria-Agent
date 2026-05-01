"""Stub for the legacy Nous subscription system.

Talaria does not include the Nous Tool Gateway. The public API is kept as
inert no-ops so existing call sites keep working without any subscription
side effects.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, Set


@dataclass(frozen=True)
class NousFeatureState:
    key: str = ""
    label: str = ""
    included_by_default: bool = False
    available: bool = False
    active: bool = False
    managed_by_nous: bool = False
    direct_override: bool = False
    toolset_enabled: bool = False
    current_provider: str = ""
    explicit_configured: bool = False


_FEATURE_KEYS = ("web", "image_gen", "tts", "browser", "modal")
_FEATURE_LABELS = {
    "web": "Web",
    "image_gen": "Image gen",
    "tts": "TTS",
    "browser": "Browser",
    "modal": "Modal",
}


@dataclass(frozen=True)
class NousSubscriptionFeatures:
    subscribed: bool = False
    nous_auth_present: bool = False
    provider_is_nous: bool = False
    features: Dict[str, NousFeatureState] = field(default_factory=dict)

    @property
    def web(self) -> NousFeatureState:
        return self.features.get("web", NousFeatureState(key="web", label="Web"))

    @property
    def image_gen(self) -> NousFeatureState:
        return self.features.get("image_gen", NousFeatureState(key="image_gen", label="Image gen"))

    @property
    def tts(self) -> NousFeatureState:
        return self.features.get("tts", NousFeatureState(key="tts", label="TTS"))

    @property
    def browser(self) -> NousFeatureState:
        return self.features.get("browser", NousFeatureState(key="browser", label="Browser"))

    @property
    def modal(self) -> NousFeatureState:
        return self.features.get("modal", NousFeatureState(key="modal", label="Modal"))

    def items(self) -> Iterable[NousFeatureState]:
        for key in _FEATURE_KEYS:
            yield self.features.get(
                key, NousFeatureState(key=key, label=_FEATURE_LABELS.get(key, key))
            )


def get_nous_subscription_features(_config) -> NousSubscriptionFeatures:
    return NousSubscriptionFeatures(
        subscribed=False,
        nous_auth_present=False,
        provider_is_nous=False,
        features={
            key: NousFeatureState(key=key, label=_FEATURE_LABELS[key])
            for key in _FEATURE_KEYS
        },
    )


def apply_nous_managed_defaults(_config, *_args, **_kwargs) -> Set[str]:
    return set()


def get_gateway_eligible_tools(_config, *_args, **_kwargs) -> Set[str]:
    return set()


def apply_gateway_defaults(_config, *_args, **_kwargs) -> Set[str]:
    return set()


def prompt_enable_tool_gateway(_config) -> Set[str]:
    return set()
