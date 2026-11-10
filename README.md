# Kestrel Cycles Support Agent

A terminal support agent for Kestrel Cycles. It answers order, return, product and Night Owl promotion questions in one conversation. The tool-calling loop is written by hand, with no agent framework.

The design follows one rule: **the model chooses words and next steps; code decides every fact.** Kestrel's COO asked that the agent never invent a policy, a product or a discount, so nothing the customer could act on (an order detail, an eligibility answer, a price, a link, a code) is left to the model.

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
| `--model SLUG` | `KESTREL_MODEL` | OpenRouter model slug. |
| `--transcript FILE` | | Also write the conversation, with tool calls, to a markdown file. |
| `--debug` | | Print tool calls and guard decisions to stderr. |

```bash
pytest                      # 124 tests, no network, under 2 seconds
python -m evals.run_eval    # 17 scripted conversations against the real model
```

## How a turn works

```
customer text
     |
     v
 [ model ] --tool call--> [ validate args (pydantic) ] --> [ tool: plain Python over the JSON data ]
     ^                                                              |
     +----------------------- result, or {"error": ...} -----------+
     |
  text reply
     |
     v
 [ guard ] --ok--> customer
     |
     +-- violation --> one rewrite with the reason --> still a hard violation --> fixed safe reply
```

The loop is in [kestrel/agent.py](kestrel/agent.py) and is about 90 lines. It stops after 6 model calls per turn.

## Who decides what

| Decided in code | Decided by the model |
| --- | --- |
| Whether an email and an order number belong to the same order | What the customer wants and which tool answers it |
| Order status, tracking link, notes | What to ask for next |
| Return eligibility for each item, and the reason | Wording, in the brand voice |
| The current time and whether the Night Owl window is open | Which of the filtered products to recommend, and why |
| Generating a code, and one code per email | When to drop the playful tone |
| Filtering products by price, style, rider height and stock | How to decline what is out of scope |
| Whether a reply may be shown (the guard) | |

## Design decisions

**Order data never enters the prompt.** The model sees an order only as the result of `lookup_order(email, order_number)`, and that tool returns it only when both values belong to the same order. A wrong email, a wrong order number and a mismatched pair all produce the same `no_match` result, so the agent cannot be used to confirm that an email or an order exists. A repeat customer (Ava Chen has two orders) needs the order number for each.

**Return eligibility always comes with the order.** `lookup_order` returns `return: {eligible, reason}` for every item, computed from the status, the delivery date, the clock and the catalog's `final_sale` flag. The model never does date arithmetic, and it has the reason ready when the answer is no. The returns portal link is in the result only when at least one item is eligible. If the data cannot answer (an item missing from the catalog), `eligible` is `null` and the agent says it cannot confirm.

**Product search is a filter, not a retrieval.** The catalog is small enough to paste into the prompt, but then the model would compare heights, prices and stock itself. `search_products` does that in code and returns either `matches` (in stock, meets every filter, with the frame sizes that fit) or, when nothing matches, up to three `near_misses`, each with a `why_not` list such as "$450.00 over the $1,000.00 budget". Category and riding style are enums built from the data at startup, so the model cannot search for something Kestrel does not sell. Rider height can be given in centimetres or total inches; the conversion is done in code.

**The Night Owl window is checked by the tool, not the model.** `get_night_owl_code(email)` asks the clock, issues `NIGHTOWL-` plus six random characters, and stores it by email in a JSON file, so the same email gets the same code after a restart. The model cannot issue a code by any other route.

**A guard checks every reply.** [kestrel/guard.py](kestrel/guard.py) collects every link and code that tools have returned in the conversation. A reply that contains any other `NIGHTOWL-` code, any other URL, or any other mention of Kestrel's domain (an invented support address, for example) is not shown. The model gets one chance to rewrite with the reason; if the rewrite still breaks a hard rule, the customer sees a fixed, safe sentence. Two formatting rules from the brand brief are checked the same way but are let through after one rewrite: at most two emoji, and order numbers, links and codes alone on their own line.

**Tool failures are data.** Bad JSON, a missing argument, an unknown tool or a crash inside a tool all come back to the model as `{"error": ...}`. One bad call cannot end the conversation, and a stack trace never reaches the prompt.

**One clock.** Every time-dependent rule asks `Clock.now()`. `--now` freezes it.

**Raw HTTP, three dependencies.** The OpenRouter client is `urllib` from the standard library. The only packages are `pydantic` (argument and data validation), `tzdata` (time zones on Windows) and `pytest`.

## Adding a fourth feature

Write the rule as a plain function with tests, then add one `Tool(name, description, ArgsModel, handler)` entry in `build_tools()` in [kestrel/tools.py](kestrel/tools.py) and a short section in the prompt. The loop, the guard and the client do not change. Any link the new tool returns is allowed by the guard automatically.

## Trade-offs

- **The guard covers codes and links, not sentences.** It cannot tell that "we offer free shipping" is invented. That risk is handled by the prompt and measured by the eval, which is weaker than code.
- **"Explicitly asks" is judged by the model.** Whether the customer asked for the Night Owl promotion is a language question, so the prompt and the tool description state the rule and the eval tests it. The window and the one-code rule do not depend on that judgement.
- **The promo email is not verified.** Anyone can request a code for any address. The brief ties a code to an email but gives no way to prove ownership.
- **The eval checks are regular expressions.** They are cheap and repeatable, but they can pass a reply that a person would reject. The saved run keeps every reply so it can be read.
- **The frozen clock does not tick.** With `--now`, a long session stays at that minute.
- **The whole conversation is resent on every turn.** That is fine for a support chat and would need trimming for very long sessions.
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
| May the agent mention Night Owl when asked about discounts in general? | Yes, with its hours, but it issues a code only when asked for Night Owl by name. |

## Tests

- **Unit tests** (`pytest`): the rules above, including the boundaries (day 30 and day 31, 8:59:59 PM and 9:00:00 PM, a rider exactly between two frame sizes), the guard, the HTTP client's retry behaviour, and the loop driven by a scripted fake model.
- **Scenario eval** (`python -m evals.run_eval`): 17 conversations against the real model with written expectations, in [evals/scenarios.json](evals/scenarios.json). The latest saved run is [evals/eval-run.txt](evals/eval-run.txt).

## Layout

```
kestrel/
  __main__.py   CLI and wiring
  agent.py      the loop
  tools.py      tool registry, argument validation, run_tool()
  guard.py      checks on every reply
  prompt.py     system prompt
  llm.py        OpenRouter client (urllib) and the LLM protocol
  orders.py     order lookup, return eligibility
  catalog.py    product search
  promo.py      Night Owl window and code store
  clock.py      the clock
  data.py       typed loading of the two JSON files
data/           the two datasets, unmodified
tests/          unit tests
evals/          scenarios, runner, saved run
transcripts/    sample conversations at 2026-12-01 9:30 PM Eastern
```

## With another day

1. **Limit failed lookups.** Order numbers are sequential, so someone who knows an email can try numbers. Count `no_match` results per session and stop answering after a few.
2. **Check claims, not only links.** A second, cheap model call that asks "is every policy statement in this reply supported by a tool result?" for replies that made no tool call.
3. **Confirm where arguments came from.** Reject a `lookup_order` call whose email or order number never appeared in a customer message.
4. **Run each scenario several times** and report a pass rate, since one run of a sampled model proves little.
5. **Trim long conversations** and replace old tool results with short summaries.
