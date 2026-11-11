# Kestrel Cycles Support Agent

A terminal support agent for Kestrel Cycles. It answers order, return, product and Night Owl promotion questions in one conversation. The tool-calling loop is written by hand, with no agent framework.

The design follows one rule: **the model chooses words and next steps; code decides every fact.** Kestrel's COO asked that the agent never invent a policy, a product or a discount, so nothing the customer could act on (an order detail, an eligibility answer, a price, a percentage, a link, a code) is left to the model, and code checks every reply before the customer sees it.

## Run it

Requires Python 3.11+ and an [OpenRouter](https://openrouter.ai) API key.

```bash
pip install -e ".[dev]"
cp .env.example .env          # then put your key in OPENROUTER_API_KEY
python -m kestrel --now "2026-12-01 21:30"
```

| Flag | Environment variable | Purpose |
| --- | --- | --- |
| `--now "2026-12-01 21:30"` | `KESTREL_NOW` | Pin the clock. Text without an offset is read as Eastern time. Omit it to use the real time. |
| `--state-file PATH` | `KESTREL_STATE_FILE` | Where issued Night Owl codes are kept (default `state/promo_codes.json`). |
| `--model SLUG` | `KESTREL_MODEL` | OpenRouter model slug (default `anthropic/claude-haiku-5.5`). |
| `--transcript FILE` | | Also write the conversation, with tool calls, to a markdown file. |
| `--debug` | | Print tool calls and guard decisions to stderr. |

```bash
pytest                      # 171 tests, no network, about 3 seconds
python -m evals.run_eval    # 30 scripted conversations x 3 runs against the real model, about 1 minute
```

**Optional browser chat for manual testing.** The brief asks for a terminal chat, and that is the product. For trying the agent by hand, `pip install -e ".[ui]"` then `streamlit run ui/app.py` opens the same agent in a browser. It shows each tool call, each guard decision and each model call's tokens, latency and cost, and can send the first message of any eval scenario.

## How a turn works

```
customer text
     |
     v
 [ model ] --tool call--> [ validate args ] --> [ argument came from the customer? ] --> [ tool: plain Python ]
     ^                                                                                         |
     +----------------------------- result, or {"error": ...} ------------------------------+
     |
  draft reply
     |
     v
 [ fact check ]  every code, link, email, price and percentage came from a tool (or the customer)?
     |      \
     |       +-- no --> one rewrite with the reason --> still no --> fixed safe reply
     v
 [ format repair, in code ]  copyable tokens on their own line, no markdown, at most two emoji
     |
     v
  customer
```

The loop is in [kestrel/agent.py](kestrel/agent.py). It allows 6 model calls per turn. The last one is asked for a text answer from the tool results already gathered, so hitting the limit costs a tool call, not the answer.

## Who decides what

| Decided in code | Decided by the model |
| --- | --- |
| Whether an email and an order number belong to the same order | What the customer wants and which tool answers it |
| Whether the customer actually typed the email and order number the model passes | What to ask for next |
| Order status, tracking link, notes; when to stop answering failed lookups | Wording, in the brand voice |
| Return eligibility for each item, and the reason | Which of the filtered products to recommend, and why |
| The current time and whether the Night Owl window is open | Whether the customer explicitly asked for Night Owl |
| Generating a code, and one code per email | When to drop the playful tone |
| Filtering products by price, style, rider height and stock | How to decline what is out of scope |
| Whether a reply may be shown, and its formatting | |

## Design decisions

**Order data never enters the prompt.** The model sees an order only as the result of `lookup_order(email, order_number)`, and that tool returns it only when both values belong to the same order. A wrong email, a wrong order number and a mismatched pair all produce the same `no_match` result, so the agent cannot be used to confirm that an email or an order exists. A repeat customer (Ava Chen has two orders) needs the order number for each.

**Arguments must come from the customer.** Each tool declares which arguments the customer must have typed (`customer_supplied`). `run_tool` checks them against the customer's messages after normalising (`kc 10433` is `KC-10433`, case is ignored in emails). The emails in the data follow `first.last@example.com`, so a model could guess Ava Chen's email from her name and get a real match; it now gets `not_provided_by_customer` instead. Order numbers are sequential, so after three lookups that do not match, `lookup_order` stops answering for the rest of the conversation.

**Return eligibility always comes with the order.** `lookup_order` returns `return: {eligible, reason}` for every item, computed from the status, the delivery date, the clock and the catalog's `final_sale` flag. The model never does date arithmetic, and it has the reason ready when the answer is no. The returns portal link is in the result only when at least one item is eligible. If the data cannot answer, `eligible` is `null` and the agent says it cannot confirm.

**Product search is a filter, not a retrieval.** The catalog is small enough to paste into the prompt, but then the model would compare heights, prices and stock itself. `search_products` does that in code and returns `matches` (in stock, meets every filter, with the frame sizes that fit) and `near_misses`, each with a `why_not` list such as "$450.00 over the $1,000.00 budget". When something matches, the near misses are the products that fit but are out of stock, so they are never hidden. Category and riding style are enums built from the data, and height can be given in centimetres or inches; the conversion is done in code.

**The Night Owl window is decided by the clock, in code.** Each turn the system prompt states whether Night Owl is open right now, computed from the same clock. Outside the window the agent says when it runs in one model call, without asking for an email or calling a tool. Inside it, the agent asks for the email and calls `get_night_owl_code(email)`, which checks the window again, issues `NIGHTOWL-` plus six random characters, and stores it by email in a JSON file, so the same email gets the same code after a restart.

**Facts are checked in code; formatting is repaired in code.** [kestrel/guard.py](kestrel/guard.py) collects every code, link, email, identifier and price that tools have returned. A draft is sent back once if it contains a discount code in any case or format (`nightowl-free50`, `KESTREL50`), a link, domain or email address, a dollar amount, or any percentage other than Night Owl's 15% that did not come from a tool or the customer. A second miss gets a fixed, safe reply. Formatting from the brand brief is applied without asking the model again: copyable tokens from tools are put on a line of their own, markdown is stripped and emoji beyond two are dropped. An earlier version asked the model to rewrite for formatting too; in live runs that cut a reply down to one bare line once, and leaked "my previous reply…" to the customer another time.

**Tool failures are data.** Bad JSON, a missing argument, an unknown tool or a crash inside a tool all come back to the model as `{"error": ...}`. One bad call cannot end the conversation, and a stack trace never reaches the prompt.

**One clock.** Every time-dependent rule asks `Clock.now()`. `--now` freezes it.

**Raw HTTP, three dependencies.** The OpenRouter client is `urllib` from the standard library. It records each call's tokens, cost, latency and finish reason for the eval. The only packages are `pydantic`, `tzdata` (time zones on Windows) and `pytest`; Streamlit is optional and only for the browser chat.

## Adding a fourth feature

Write the rule as a plain function with tests, then add one `Tool(name, description, ArgsModel, handler, customer_supplied=(...))` entry in `build_tools()` in [kestrel/tools.py](kestrel/tools.py), a short section in the prompt and a few scenarios in [evals/scenarios.json](evals/scenarios.json). The loop, the guard and the client do not change. Any link, code or price the new tool returns is allowed by the guard automatically.

## Trade-offs

- **The guard checks tokens, not sentences.** It cannot tell that "we offer free shipping" is invented. That risk is handled by the prompt and measured by the eval, which is weaker than code.
- **The guard is strict about numbers.** The model may not compute "$390 over your budget" itself, and may not repeat a discount the customer proposes even to refuse it, because a check cannot tell a refusal from a promise. Either costs one rewrite: 2.6% of turns in the latest full eval run.
- **Format repair can repeat a token.** When an order number sits mid-sentence and nowhere on its own line, it is added on its own line below the sentence. Slightly redundant, never a lost answer.
- **"Explicitly asks" is judged by the model.** A keyword check ("night owl") would refuse a customer who says "yes please" after the agent describes the promotion. The window, the email check and the one-code rule do not depend on that judgement.
- **The promo email is not verified.** Anyone can request a code for an address they type. The brief ties a code to an email but gives no way to prove ownership.
- **The frozen clock does not tick.** With `--now`, a long session stays at that minute.
- **The whole conversation is resent on every turn.** About 3,200 prompt tokens per call; a two-turn chat costs under $0.002 on Haiku 5.5, so trimming is not worth its complexity yet.
- **The JSON code store is single-process.** Writes are atomic, but two agents running at once could both issue a code to the same email.

## Assumptions

The brief leaves these open. I would ask them before shipping; each is one small change if the answer differs.

| Question | What the agent does now |
| --- | --- |
| An email that already has a code asks again outside the window. Repeat the code? | No. It explains when the promotion runs. |
| Does verifying one order unlock the same customer's other orders? | No. Each order needs its own number. |
| Is there a human to hand over to (defects, a return past 30 days)? | None is given, so the agent does not invent a contact. |
| Must the promo email belong to an existing customer? | No. Any well-formed address gets a code. |
| How are the 30 days counted? | Calendar days in Eastern time. Day 30 is still eligible. |
| Does a code expire? How is it redeemed? | Not stated, so the agent says nothing about either. |
| May the agent mention Night Owl when asked about discounts in general? | Yes, with its hours, but it issues a code only when asked for Night Owl. |

## Tests and evaluation

**Unit tests** (`pytest`, 171, no network) cover:
- the business rules at their boundaries (day 30 and day 31, 8:59:59 PM and 9:00:00 PM, a rider exactly between two frame sizes);
- every guard rule, including the false-positive cases (product words like `MIPS` and `1x12`, the customer's own email and budget);
- the argument checks and the failed-lookup limit;
- the HTTP client's retries and usage records;
- the loop, driven by a scripted fake model (rewrite, fallback, repair without another call, the final answer at the step limit).

**Scenario eval** (`python -m evals.run_eval`) runs 30 conversations from [evals/scenarios.json](evals/scenarios.json) against the real model, 3 times each, 4 at a time. It checks:
- **per turn:** reply patterns, which tools ran, the tools' arguments (for example `search_products` must receive `rider_height=190, max_price=1500`), and how many model calls the turn used (a closed Night Owl must be answered in one);
- **across the conversation:** e.g. one distinct code;
- **every reply, independently of the agent's guard:** codes, links, prices and percentages that no tool returned;
- **any fallback to a fixed reply** counts as a failure.

Ten scenarios are tagged `safety` (privacy, invented facts, discount injection). It reports pass rates, LLM calls per turn, latency, tokens and cost.

Latest run, saved with replies in [evals/eval-run.txt](evals/eval-run.txt), on `anthropic/claude-haiku-5.5`:

| Metric | Result |
| --- | --- |
| Runs passed | 90 / 90 (30 scenarios x 3) |
| Safety runs passed | 30 / 30 |
| LLM calls per turn | 1.62 |
| Latency per turn | p50 1.6 s, p95 3.3 s |
| Guard rewrites / fallbacks | 2.6% / 0% of turns (formatting repaired in code on 14%) |
| Cost | $0.08 for 90 conversations |

Three runs of a sampled model with pattern checks is evidence, not proof. Read it as "no known failure at this sample size".

## What the live runs changed

The unit tests were green before the model was ever called. Reading real replies still found the problems below. Each fix is covered by a unit test or an eval check that the original reply fails.

| Seen in a real reply or probe | Fix | Where |
| --- | --- | --- |
| An e-bike question got no mention of the out-of-stock Metro E-Assist, because another product matched | Out-of-stock products that fit the request are always returned as near misses | code: `catalog.py` |
| Asking the model to fix formatting cut one reply to a bare line and leaked "my previous reply…" into another | Formatting is repaired in code; only invented facts go back to the model | code: `guard.py`, `agent.py` |
| The guard let through `nightowl-free50`, `KESTREL50`, "50% off", "$999", `www.` links and invented support emails | Hard rules for codes in any case and format, percentages, amounts, domains and emails | code: `guard.py` |
| Nothing stopped a lookup with an email the customer never typed, or a sweep of sequential order numbers | `customer_supplied` arguments and a three-miss lookup limit | code: `tools.py` |
| Hitting the step limit threw away the tool results gathered so far. `tool_choice: "none"` alone returned an empty message from Haiku 5.5 | The last step sends `tool_choice: "none"` plus a short note, and the model answers from what it has | code: `agent.py` |
| Asked for an email before saying Night Owl was closed: a wasted customer turn and two extra model calls (found while testing in the browser) | The system prompt states, from the clock, whether Night Owl is open; the eval caps that turn at one model call | code: `prompt.py` |
| "Enter it at checkout" after issuing a code, though nobody said how a code is redeemed | The Night Owl result carries a note saying redemption and expiry are unknown, next to the code where the model reads it | code: `promo.py` |
| A 190 cm rider was offered size L (up to 188 cm) as "a snug fit" | `fitting_sizes` is the whole answer on fit | prompt |
| "Would you like me to check again later?", "check the shipping terms at checkout" | The agent offers only what its tools can do and points nowhere a tool did not mention | prompt |

The last two are prompt fixes, so they are measured by the eval and not guaranteed by code.

## Layout

```
kestrel/
  __main__.py   CLI and wiring
  agent.py      the loop
  tools.py      tool registry, argument and provenance checks, run_tool()
  guard.py      fact checks and format repair on every reply
  prompt.py     system prompt
  llm.py        OpenRouter client (urllib), usage records, the LLM protocol
  orders.py     order lookup, return eligibility
  catalog.py    product search
  promo.py      Night Owl window and code store
  clock.py      the clock
  data.py       typed loading of the two JSON files
data/           the two datasets, unmodified
tests/          unit tests
evals/          scenarios, runner, saved run
transcripts/    sample conversations at 2026-12-01 9:30 PM Eastern
ui/app.py       optional Streamlit chat for manual testing
```

## With another day

1. **Check claims, not only tokens.** A cheap second model call, only for replies that mention shipping, warranty, exchanges or refunds, asking whether every such statement is supported by a tool result. Validate it against a few hand-labelled replies first.
2. **Judge the voice.** An LLM-as-judge rubric for tone and refusal quality, calibrated on about ten replies scored by a person, since pattern checks cannot see tone.
3. **Compare models with the eval.** `--model` already switches it; run two cheaper candidates and keep the default unless safety stays at 100% and the pass rate holds.
4. **Trim long conversations** by replacing old search results with short summaries, once conversations get long enough for it to matter.
