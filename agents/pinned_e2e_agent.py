"""A general_e2e agent with every floating knob nailed down and recorded.

Supplied to the harness as `--agent-type agents/pinned_e2e_agent.py`, which
`mobile_world.agents.registry.load_agent_from_file` accepts. Nothing under
vendor/ is patched, so reproducing this needs no fork of the benchmark.

What it pins that the stock agent leaves loose:

* **Which machine actually serves the model.** On OpenRouter a single model id
  fans out to ~20 providers at different quantizations (fp4 / int4 / fp8 /
  bf16). Left alone, the router may pick a different one per request, and that
  variance would land in our measurement labelled as environment noise. We send
  `provider.only` + `provider.quantizations` + `allow_fallbacks: false`, so a
  request either runs on the pinned endpoint or fails loudly.
* **seed**, where the provider advertises support for it. The stock agent never
  sends one.
* **temperature / top_p / max_tokens / history_n_images**, so the A/B arms
  differ in exactly one thing and the value is in the log rather than implied
  by a default.

Configured by environment variable so the orchestrator can set it per arm:

    MW_OR_PROVIDER      OpenRouter provider slug, e.g. deepinfra
    MW_OR_QUANT         quantization, e.g. fp4
    MW_TEMPERATURE      float (default 0.0)
    MW_TOP_P            float (unset -> not sent)
    MW_MAX_TOKENS       int (default 2048)
    MW_HISTORY_N_IMAGES int (default 3)
    MW_SEED             int (unset -> not sent)

Note on imports: `load_agent_from_file` collects *every* BaseAgent subclass
visible in the module, including ones merely imported, and picks the first
alphabetically. So we import the parent's module rather than the class, leaving
exactly one class bound at module level.
"""

import os

from loguru import logger

import mobile_world.agents.implementations.general_e2e_agent as _general_e2e


def _opt(name, cast):
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    try:
        return cast(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a valid {cast.__name__}")


class PinnedE2EAgent(_general_e2e.GeneralE2EAgentMCP):
    def __init__(self, model_name, llm_base_url, api_key="empty", **kwargs):
        # Build a fresh dict every time. The parent declares runtime_conf as a
        # mutable default and pops history_n_images out of it, so the stock
        # default is mutated on first instantiation and shared thereafter.
        runtime_conf = {
            "history_n_images": _opt("MW_HISTORY_N_IMAGES", int) or 3,
            "temperature": _opt("MW_TEMPERATURE", float) or 0.0,
            "max_tokens": _opt("MW_MAX_TOKENS", int) or 2048,
        }

        top_p = _opt("MW_TOP_P", float)
        if top_p is not None:
            runtime_conf["top_p"] = top_p

        seed = _opt("MW_SEED", int)
        if seed is not None:
            runtime_conf["seed"] = seed

        provider = os.getenv("MW_OR_PROVIDER")
        quant = os.getenv("MW_OR_QUANT")
        if provider or quant:
            routing = {"allow_fallbacks": False}
            if provider:
                routing["only"] = [provider]
            if quant:
                routing["quantizations"] = [quant]
            # Reaches the OpenAI SDK as extra_body and is forwarded verbatim;
            # a non-OpenRouter endpoint ignores the unknown field.
            runtime_conf["extra_body"] = {"provider": routing}

        kwargs.pop("runtime_conf", None)
        super().__init__(
            model_name=model_name,
            llm_base_url=llm_base_url,
            api_key=api_key,
            runtime_conf=runtime_conf,
            **kwargs,
        )

        # ------------------------------------------------------------------
        # Putting the routing in runtime_conf is NOT enough to get it sent.
        #
        # agents/base.py:105-106 does, for every model whose name contains
        # "kimi-k":
        #
        #     kwargs["extra_body"] = {"enable_thinking": True}
        #
        # -- an assignment, not a merge. Whatever the caller put in extra_body
        # is discarded at the moment of the call, so the provider pinning never
        # reaches OpenRouter and the request is routed by the default policy
        # across every provider serving the model. Measured directly: three
        # near-identical unpinned requests came back from SiliconFlow,
        # StreamLake and StreamLake, with reasoning lengths of 87, 476 and 228
        # tokens for the *same* trivial prompt. That is precisely the variance
        # this agent exists to eliminate, and it would have been recorded as
        # environment noise.
        #
        # vendor/ is not ours to patch, so the routing is re-applied at the last
        # possible moment, around the SDK call itself, merging rather than
        # replacing so upstream's enable_thinking survives.
        self._expected_provider = provider
        self._served_provider = None
        if provider or quant:
            self._pin_extra_body({"provider": routing})

        # Goes into the per-task thread_*.log, so the resolved configuration is
        # recoverable from the trial's own artifacts rather than from memory.
        logger.info("PINNED AGENT CONFIG: model={} base_url={} conf={}",
                    model_name, llm_base_url, runtime_conf)

    def _pin_extra_body(self, pinned):
        """Merge `pinned` into extra_body on every request this agent makes.

        Wraps the one shared client (base.py:56-62 builds it once and both call
        sites reuse it), so it covers the tool path and the plain path alike.

        Also records where the request was actually served. Logging what we
        *intended* to send proves nothing -- that is exactly the mistake this
        works around. OpenRouter reports the serving provider on the response,
        so the artifacts get the fact rather than the intention.
        """
        completions = self.openai_client.chat.completions
        original = completions.create

        def create(*args, **kwargs):
            merged = dict(kwargs.get("extra_body") or {})
            merged.update(pinned)
            kwargs["extra_body"] = merged
            response = original(*args, **kwargs)
            served = getattr(response, "provider", None)
            if served is None:
                extra = getattr(response, "model_extra", None) or {}
                served = extra.get("provider")
            if served and served != self._served_provider:
                self._served_provider = served
                logger.info("PINNED AGENT SERVED BY: {}", served)
                # The routing slug and the display name are not the same
                # string: OpenRouter routes on "atlas-cloud" and reports
                # "AtlasCloud". Comparing them raw raises a false alarm on
                # every single call, which is worse than not checking at all --
                # a spurious alarm in the artifacts teaches a reader to
                # disregard a real one. Compare on alphanumerics only.
                norm = lambda x: "".join(
                    c for c in (x or "").lower() if c.isalnum())
                if self._expected_provider and norm(self._expected_provider) \
                        != norm(served):
                    # allow_fallbacks=false should make this impossible; if it
                    # happens the run is not what it claims to be.
                    logger.error(
                        "PROVIDER PIN VIOLATED: asked for {}, served by {}",
                        self._expected_provider, served)
            return response

        completions.create = create
