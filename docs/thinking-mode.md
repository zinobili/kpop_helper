# Thinking mode (reasoning tokens) and translation cost

Last verified: 2026-09-19. Model docs change quickly, so re-check the linked sources before
relying on any provider detail below.

## What it is

A "thinking" (reasoning) model writes a long private chain of thought before it answers. Those
reasoning tokens are **billed as output tokens** and take real time to generate, even when you
never see them. For a task like subtitle translation, where the answer is a few words per line,
the reasoning can be many times larger than the answer.

The takeaway for this project: **check whether the model you picked thinks by default.** It is
the largest hidden cost we found, bigger than prompt wording or output format.

## What we measured

DeepSeek `deepseek-flash`, 517 real subtitle lines, 20 lines per request, same prompt. Thinking
on vs off (run 1 of each):

| Sample | Output tokens on | Output tokens off | Time on | Time off |
|---|---|---|---|---|
| Korean, Whisper transcript (217 lines) | 66,012 | 3,348 | 300 s | 21 s |
| Korean, YouTube auto-captions (200 lines) | 115,926 | 4,269 | 757 s | 28 s |
| English manual captions (100 lines) | 23,517 | 2,334 | 111 s | 14 s |
| **Total** | **205,455** | **9,951** | 1,169 s | 63 s |

- Reasoning tokens were 96-98% of the output when thinking was on. Input tokens were unchanged.
- At DeepSeek's published peak prices that is about 16x cheaper (roughly $0.25 vs $0.015 for
  these 517 lines) and about 18x faster with thinking off.
- **Quality:** both modes returned every line, with no empties and no simplified-character
  leakage. Two runs of the *same* setting only agree on 25-66% of lines, so exact match is a
  noisy metric. In a read-through, thinking on was better on colloquial Korean (it fixed an
  inverted negation, the 씨 honorific, and idioms such as 굵은 가지 = 大咖). Thinking off had
  visible meaning errors in roughly 3-5% of colloquial Korean lines and was about equal on
  English-source lines.
- **Caveats:** the quality read was a single unblinded reviewer (an LLM), on three small
  samples, with some garbled auto-caption input. Treat it as directional, not a benchmark.

Rule of thumb: leave thinking **off** for bulk translation, and turn it **on** only for videos
where accuracy on slang and idiom matters more than cost and speed.

## Provider cheat-sheet

| Provider | Default | Turn it off | Billed as | Reported in usage |
|---|---|---|---|---|
| **DeepSeek** `deepseek-flash`, `deepseek-v4-pro` | On, `high` effort | `{"thinking": {"type": "disabled"}}` in the request body | Output tokens (same per-token price in both modes) | `completion_tokens_details.reasoning_tokens` |
| **Anthropic** `claude-sonnet-5` | On (adaptive) when `thinking` is omitted | `thinking={"type": "disabled"}` | Output tokens | Not confirmed whether it is broken out |
| **Anthropic** `claude-haiku-4-5` | Off | Omit `thinking` | Output tokens | Not confirmed |
| **Gemini** | Depends on the model (2.5 Flash on, 2.5 Flash-Lite off, 3.x Flash defaults to medium per the docs) | Control is `thinking_level` (`low`, `medium`, `high`); how to fully disable on 3.x was unclear in the docs | Output tokens (thinking + response) | `usage_metadata.thoughts_token_count` |

Status of each claim: the **DeepSeek** row was confirmed by experiment (`think_tok=0` when
disabled). The **Anthropic** and **Gemini** rows are from documentation only and have not been
tested against the real APIs. Notes:

- DeepSeek thinking mode ignores `temperature`, `presence_penalty` and `frequency_penalty`. Effort
  levels `low`, `high` and `max` exist per the docs, but `low` has not been tried here.
- The legacy `deepseek-chat` name was an alias for the non-thinking mode of V4-Flash. The docs
  put its legacy support end at 2026-07-24, yet a `deepseek-chat` request still worked on
  2026-09-16. Prefer a current model name plus `thinking: disabled`.
- Sonnet 5 has no `budget_tokens`, and sending it returns a 400. Haiku 4.5 uses
  `{"type": "enabled", "budget_tokens": N}` with N >= 1024 and N < `max_tokens`.

## How this repo handles it

| Provider | Behaviour today | Where |
|---|---|---|
| DeepSeek | Sends `thinking` from `DEEPSEEK_THINKING` (default `disabled`) | `backend/app/config.py`, `_translate_batch_deepseek` in `backend/app/translate.py` |
| Anthropic | Sends **no** thinking setting, so Sonnet 5 likely runs adaptive thinking. Not yet verified | `_translate_batch_anthropic` |
| Gemini | Sends **no** thinking config, so the model default applies. Not yet verified | `_translate_batch_gemini` |
| Local | Whatever the local server does | `_translate_batch_local` |

Turn thinking on for DeepSeek by setting `DEEPSEEK_THINKING=enabled` in `backend/.env`.

## Checking your own usage

Every LLM call and every job logs token usage (server console, `LOG_LEVEL` in `.env`):

```
translate batch 3/11 ok provider=deepseek attempt=1/3 lines=20 secs=32.7 model=deepseek-flash in_tok=471 out_tok=7765 think_tok=7491 stop=stop
translate summary provider=deepseek batches=11/11 resumed=4 api_calls=7 retries=0 in_tok=2717 out_tok=38610 think_tok=37040
```

What to look for:

- `think_tok` close to `out_tok` means nearly all your output spend is reasoning.
- `think_tok=None` means the provider did not report it. That is expected for Anthropic today,
  where any thinking tokens would sit inside `out_tok`.
- `stop=max_tokens` (or `length`) triggers a `TRUNCATED` warning. Thinking eats into the output
  limit, so a thinking model can run out of room before the JSON is finished.

## Gotchas we hit

- **Slow requests get dropped.** With thinking on, one batch took 60+ seconds and long responses
  were closed by the server (`RemoteProtocolError: peer closed connection`). This reproduced 4
  times on a single batch. The production retry loop only retries HTTP status errors, not
  dropped connections, so such a batch fails the whole job. Turning thinking off largely avoids
  it.
- **Failed jobs still cost money.** Finished batches are checkpointed so a rerun resumes, but the
  in-flight batch at the moment of failure is paid for and lost.
- **Model names get retired.** Check the provider's pricing and changelog pages when a model name
  starts behaving differently.

## Sources

- DeepSeek: [pricing and models](https://api-docs.deepseek.com/quick_start/pricing),
  [thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/),
  [changelog](https://api-docs.deepseek.com/updates/)
- Gemini: [thinking](https://ai.google.dev/gemini-api/docs/thinking)
- Anthropic: Claude API reference for extended and adaptive thinking
- Experiment data: three A/B samples run on 2026-09-19 via `deepseek-flash`
