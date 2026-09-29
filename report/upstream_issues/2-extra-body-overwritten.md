# `extra_body` supplied by a caller is discarded for `kimi-k*` models

**File:** `src/mobile_world/agents/base.py:105-106` (at `83e7b8f`)

## What happens

```python
if "kimi-k" in model.lower():
    kwargs["extra_body"] = {"enable_thinking": True}
```

This is an assignment, not a merge. Anything the caller placed in `extra_body`
is silently dropped at the moment of the call.

## Why it matters

`extra_body` is the only channel OpenAI-compatible clients have for
provider-specific request fields. On OpenRouter it carries **provider routing**:

```python
extra_body = {"provider": {"only": ["atlas-cloud"],
                           "quantizations": ["int4"],
                           "allow_fallbacks": False}}
```

With that discarded, requests fall back to default routing. A single OpenRouter
model id is served by many providers at different quantizations, which are not
the same weights. Three near-identical requests we sent without routing came
back from **SiliconFlow**, **StreamLake** and **StreamLake**, with reasoning
lengths of 87, 476 and 228 tokens for the *same* prompt.

For anyone measuring an agent, that is an uncontrolled variable being
introduced after they took steps to remove it. The failure is silent: the agent
logs the configuration it built, and the configuration that goes out is
different.

## Reproduction

```python
kwargs = {"extra_body": {"provider": {"only": ["atlas-cloud"],
                                      "allow_fallbacks": False}}}
model = "moonshotai/kimi-k2.5"
if "kimi-k" in model.lower():
    kwargs["extra_body"] = {"enable_thinking": True}
print(kwargs["extra_body"])   # {'enable_thinking': True} -- routing is gone
```

Against the live API, compare the `provider` field of the response with and
without the caller's routing to see where the request was actually served.

## Suggested fix

Merge instead of replace, leaving the caller's keys in place:

```python
if "kimi-k" in model.lower():
    extra = dict(kwargs.get("extra_body") or {})
    extra.setdefault("enable_thinking", True)
    kwargs["extra_body"] = extra
```

`setdefault` also lets a caller turn thinking off deliberately, which the
current form makes impossible.

## The same shape appears twice more

`base.py:97-99` deletes `temperature` outright for any model whose id contains
`claude`, and `:101-103` renames `max_tokens` for `gpt`/`o1`. Both are
model-name-conditional rewrites of a caller's request that the caller cannot
see or override. The `temperature` one has a measurement consequence: Claude
entries on the leaderboard did not run at the temperature their configuration
specified, so they carry different sampling noise from every other entry.
