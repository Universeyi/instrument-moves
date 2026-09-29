# `openai_chat_completions_create` crashes when a reasoning model returns no content

**File:** `src/mobile_world/agents/base.py:115` (at `83e7b8f`)

## What happens

```python
final_content = response.choices[0].message.content.strip()
```

`message.content` is `None` whenever a provider returns `finish_reason: "length"`
after spending the whole completion budget on reasoning tokens. `.strip()` then
raises `AttributeError: 'NoneType' object has no attribute 'strip'`.

The exception is caught by the retry loop below, logged as
`Error calling OpenAI API`, and surfaces to the agent as:

```
ERROR | general_e2e_agent:parse_action:70 | Error parsing output: 'NoneType' object has no attribute 'rsplit'
WARNING | general_e2e_agent:predict:422 | Output is not in the correct format ... Retrying... (2 attempts left)
...
ValueError: Agent LLM failed
```

The task then leaves no `result.txt`, so the outer `--auto-retry` loop reruns it
up to nine more times, each failing the same way. The final record is a task
with **no score at all**, and the log points at output parsing rather than at
the cause.

## Why it triggers so readily on Kimi models

Two defaults combine:

- `base.py:105-106` forces `extra_body = {"enable_thinking": True}` for any
  model whose id contains `kimi-k`.
- `general_e2e_agent.py:214` sets `runtime_conf["max_tokens"] = 2048`.

Reasoning on a GUI step with a screenshot in context routinely runs past 2048
tokens on its own, so `content` comes back `None` with reasoning truncated. In a
290-run evaluation of `moonshotai/kimi-k2.5` we saw this on 10 steps; all were
absorbed by the in-task retry, but each cost a restart and the failure mode is
silent.

## Minimal reproduction

No harness required — one request reproduces it:

```python
import openai
c = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=KEY)
r = c.chat.completions.create(
    model="moonshotai/kimi-k2.5",
    messages=[{"role": "user", "content":
               "Think step by step in detail about how to turn on aeroplane "
               "mode on Android, then give one action line."}],
    temperature=0.0, max_tokens=2048,
    extra_body={"enable_thinking": True},
)
print(r.choices[0].finish_reason)      # 'length'
print(repr(r.choices[0].message.content))  # None
r.choices[0].message.content.strip()   # AttributeError
```

## Suggested fix

Treat missing content as empty and let the existing `reasoning_content` path
supply the text, so a truncated-reasoning response degrades into a normal
"unparseable output" retry instead of an exception:

```python
message = response.choices[0].message
final_content = (message.content or "").strip()
```

It would also help to log `finish_reason` when content is empty — the current
message sends readers to the parser rather than to the token budget.

## Not a bug, but related

Because reasoning is forced on for `kimi-k*` while `max_tokens` stays at the
2048 default, the budget is spent before the answer begins more often than the
default suggests. Raising the default for reasoning-enabled models, or sizing
it from `enable_thinking`, would avoid the situation rather than just surviving
it.
