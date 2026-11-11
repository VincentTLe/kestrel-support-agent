# Kestrel Cycles Support Agent

A terminal support agent for Kestrel Cycles. It handles orders, returns, product advice and the Night Owl promotion in one conversation. The tool-calling loop is written by hand, with no agent framework.

Two rules shape the design:

1. **The goal is the customer's problem solved, not the question answered.** When the agent cannot do what was asked, it finds the goal behind the request, takes every step its tools allow, and asks the one question that moves the customer forward.
2. **The model chooses words and next steps; code decides every fact.** Kestrel's COO asked that the agent never invent a policy, a product or a discount. Nothing the customer could act on (an order detail, an eligibility answer, a price, a link, a code) is left to the model, and code checks every reply before the customer sees it.

Version 2 came out of a review that said version 1 was safe but behaved like an automatic response: it said "I can't" and stopped, and its single prompt would not scale to many features. See [What changed in version 2](#what-changed-in-version-2).

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
| `--tool-loading auto\|all\|on_demand` | `KESTREL_TOOL_LOADING` | How feature tools are loaded (see [Scaling to many features](#scaling-to-many-features)). Default `auto`. |
| `--transcript FILE` | | Also write the conversation, with tool calls, to a markdown file. |
| `--debug` | | Print tool calls and guard decisions to stderr. |

```bash
pytest                       # 232 tests, no network, about 2 seconds
python -m evals.run_eval     # 30 scripted conversations x 3 runs against the real model, about 1 minute
python -m evals.simulate     # 8 customer journeys x 3 runs with a simulated customer, about 1 minute
python -m evals.scale        # prompt size with 3, 23 and 53 features, both loading modes
```

**Optional browser chat for manual testing.** The brief asks for a terminal chat, and that is the product. To try the agent by hand, `pip install -e ".[ui]"` then `streamlit run ui/app.py` opens the same agent in a browser. It shows, for each turn, whether the problem is resolved, each tool call, each guard decision, and each model call's tokens, latency and cost. It also shows the case file, and can send the first message of any eval scenario.

## How a turn works

```
customer message
  |
  v
[input guard]  soft: text that looks like instructions is flagged as the customer's words
  |
  v
AGENT LOOP. The model sees:
  - the fixed core prompt: role, how to solve a problem, fact rules, voice
  - the feature catalog (one line per feature) and the rules of the features in use
  - the case file kept by code: verified orders, what the customer gave, the last open question
  - recent turns in full, older tool results compacted by code
  |
  |-- feature tool call --> [tool guard, hard] argument schema, values the customer typed, lookup limit
  |                          --> handler: business rules in code --> result + "options" for next steps
  |
  `-- respond(message, goal_status, next_step)
        --> [output guard, hard] invented code/link/email/price/percentage, another customer's name,
        |                        a promise to act later --> one rewrite --> fallback that still offers a way forward
        --> [output guard, soft] formatting, plain tone for an upset customer, next step appended
        |                        --> repaired in code, no extra model call
        --> case file updated --> customer
```

The loop is in [kestrel/agent.py](kestrel/agent.py). It allows six model calls per turn; the last one is forced to call `respond` with what the tools have returned so far, so hitting the limit costs a tool call, not the answer.

## Who decides what

| Decided in code | Decided by the model |
| --- | --- |
| Whether an email and an order number belong to the same order | What the customer is really trying to achieve |
| Whether the customer actually typed the email, order number or name the model passes | Which tools to call, and in what order |
| Recovering a mistyped order number (email + name + one digit off, unambiguous only) | What to ask next, and how to restate the problem |
| Order status, tracking link, return eligibility, and the options the customer has next | Wording, in the brand voice |
| The current time, and whether Night Owl is open now | Which of the filtered products to recommend, and why |
| Generating a code, and one code per email | General know-how (bike care, fit), safety first |
| Filtering products by price, style, rider height and stock | Whether the customer's goal is met (`goal_status`) |
| Whether a reply may be shown, its formatting, and that it ends with a next step | |

## Solving the customer's problem

The core prompt ([kestrel/prompt.py](kestrel/prompt.py)) gives the model one job: get the problem solved. Find the goal behind the words, restate it when unclear, do every step the tools allow without asking permission, ask for exactly one thing when something is missing, and when something cannot be done, give the best path that does work.

Code backs this up in three places:

- **Every turn ends with `respond(message, goal_status, next_step)`.** `goal_status` is `resolved`, `needs_info` or `cannot_do`. When the problem is not resolved, code appends the next step unless the message already ends with a question. A turn never ends in a dead end, and "is the problem solved?" becomes data the eval measures.
- **Tool results say what the customer can do next.** `lookup_order` returns `options` computed from the order. A processing order says it cannot be cancelled in this chat, and which items can be returned within 30 days of delivery; final-sale items are excluded. A delivered order says what to do if it never arrived, and that the returns portal does not help with a missing package.
- **A mistyped order number can be recovered.** Every failed lookup returns the same `no_match` with the same suggestion: ask whether another email was used, and for the full name on the order. `recover_order(email, customer_name, order_number)` returns the order only when exactly one order matches the email and the name and its number is one edit away (one digit wrong, swapped, missing or extra). All three values must have been typed by the customer. Someone who knows only an email learns nothing.

The same six situations, before and after (live model):

| Customer says | Version 1 | Version 2 |
| --- | --- | --- |
| "Where's KC-10432?" (real order is KC-10433) | "Couldn't find it, double-check both." End. | Asks for the name on the order, finds KC-10433, gives tracking. Resolved in 2 turns. |
| "Return my jacket, it's too small" (past 30 days) | No, then "would you like me to look for another size?" | No, plus the same jacket in stock in M and L, found without asking. |
| "It says delivered but I never got it" | Tracking link, then the returns link | Delivered date, tracking, "returns won't help with a missing package", asks whether they checked with neighbours or a mailroom. |
| "Cancel my order, I don't need the pannier" | "I can't cancel." End. | Can't cancel here, but once delivered the pannier can be returned within 30 days. |
| "Can you fix my squealing brakes?" | "No repair information." | Common causes, what to check, when to stop riding and see a shop; asks rim or disc. |
| "Do you have a Specialized Stumpjumper?" | Already good: the closest Kestrel full-suspension bike in the right size | Same |

## Guardrails: hard and soft

Hard guardrails are enforced in code and block. Soft guardrails shape the reply and never block: code repairs them, or the eval measures them.

| | Rule | Layer | When broken |
| --- | --- | --- | --- |
| H1 | Order data only for a matching email + order number, or email + name + a number one edit away | tool | the same `no_match` for every miss |
| H2 | Identifying arguments (email, order number, name) must have been typed by the customer | tool | `not_provided_by_customer`; the model asks |
| H3 | At most three failed lookups per conversation, recoveries included | tool | `too_many_attempts` |
| H4 | Night Owl window and one code per email | tool | `outside_window`, or the existing code |
| H5 | No code, link, email, dollar amount or percentage that no tool returned and the customer did not give | output | one rewrite, then a fallback that offers a way forward |
| H6 | No other customer's name unless a tool returned it or the customer typed it | output | same as H5 |
| H7 | No promise to act later ("I'll let you know", "check again later"): the agent exists only in this chat | output | same as H5 |
| H8 | Identity and permissions come from the program, never from the chat ("I'm staff" changes nothing) | design | no tool grants anything on a claim |
| S1 | Copyable tokens on their own line, no markdown, at most two emoji | output | repaired in code |
| S2 | No emoji when the customer is upset or a delivery went wrong | output | repaired in code |
| S3 | An unresolved turn ends with a next step | output | appended in code from `respond` |
| S4 | Text that looks like instructions is the customer's words, not rules | input | a note is added to the prompt for that turn |
| S5 | Out of scope: help with the closest thing, general know-how allowed, safety first | prompt | measured by journeys |
| S6 | No promotion pitch when the customer did not ask about discounts | prompt | measured by scenarios |

The fallback after a second hard violation is no longer a bare apology. It repeats the open question from the case file, or lists what the agent can do: "Sorry, I couldn't put together a reliable answer to that. I can check an order's status, tracking or returns, find bikes and gear that fit you, or look up the Night Owl promotion. Which would help?"

## Scaling to many features

Version 1 had one prompt with every feature's rules and every tool on every call: about 740 more prompt tokens for each feature, and a change to one feature's text could break another. It did in development, when a Night Owl edit made the agent pitch discounts in an answer about warranties.

Version 2 makes each feature a module in [kestrel/features/](kestrel/features/). Each module declares:
- its tools;
- up to 200 words of rules, shown only while the feature is active;
- a one-line catalog entry, with live status such as "Night Owl: CLOSED now";
- what the guard should watch for (codes it issues, identifiers that must stand alone, allowed percentages, protected names).

The guard, the prompt and the fallback read from the registry, so none of them changes when a feature is added.

With more than eight feature tools, `--tool-loading auto` switches to on-demand loading:
1. The model starts with the catalog and two tools, `respond` and `enable_features`.
2. When it needs a feature, it enables it; that feature's tools and rules stay loaded for the rest of the conversation.
3. Live state stays in the catalog line, so a closed promotion is still answered in one call.

`python -m evals.scale` adds synthetic features of realistic size and asks the same order question:

| Features | Prompt tokens, first call, all loaded | On demand | Model calls (all / on demand) | Answered |
| --- | --- | --- | --- | --- |
| 3 | 4,573 | 2,368 | 2 / 3 | yes / yes |
| 23 | 17,633 | 2,968 | 2 / 3 | yes / yes |
| 53 | 37,223 | 3,868 | 2 / 3 | yes / yes |

On demand costs one extra model call the first time a feature is needed. The 30 scripted scenarios pass in on-demand mode too, so with three features `auto` still loads everything.

Long conversations are bounded the same way. Code keeps a case file (verified orders, what the customer gave, the last open question), and tool results older than two turns are compacted, keeping links, codes, prices and statuses. The guard keeps the full set of facts.

## Adding a feature

1. Write the business rule as plain functions with tests.
2. Add a module in [kestrel/features/](kestrel/features/) whose `build(ctx)` returns a `Feature`: name, one-line summary, tools (with `customer_supplied` for identifying arguments), short guidance, an `offer` phrase for fallbacks, and any codes it issues.
3. Add its `build` to `BUILDERS` in [kestrel/features/__init__.py](kestrel/features/__init__.py).
4. Add scenarios to [evals/scenarios.json](evals/scenarios.json), and a journey to [evals/journeys.json](evals/journeys.json) if customers will bring problems to it.

The loop, the core prompt, the guard and the client do not change. `tests/test_features.py` fails if a feature's guidance exceeds its budget or two features declare the same tool.

## Design decisions

**Order data never enters the prompt.** The model sees an order only as a tool result, and only when the email and the order number belong to the same order. A wrong email, a wrong number and a mismatched pair all produce the same `no_match`, so the agent cannot confirm that an email or an order exists.

**Return eligibility always comes with the order.** Every item carries `return: {eligible, reason}`, computed from the status, the delivery date, the clock and the catalog's `final_sale` flag. The model never does date arithmetic. If the data cannot answer, `eligible` is `null` and the agent says it cannot confirm.

**Product search is a filter, not a retrieval.** `search_products` filters by price, style, rider height and stock in code. It returns `matches` with the frame sizes that fit, and `near_misses` each with a `why_not` such as "$450.00 over the $1,000.00 budget". Out-of-stock products that fit are always listed, never hidden. Height can be given in centimetres or inches; the conversion is done in code.

**The Night Owl window is decided by the clock, in code.** The catalog line states whether the promotion is open now. Outside the window the agent answers in one model call without asking for an email. Inside it, `get_night_owl_code(email)` checks the window again and issues or returns the email's code, stored in a JSON file. The result reminds the model that redemption and expiry are unknown.

**Formatting is repaired, never re-asked.** An early version asked the model to rewrite for formatting. In live runs that cut one reply to a bare line and leaked "my previous reply…" into another, so formatting moved into code.

**Tool failures are data.** Bad JSON, a missing argument, an unknown tool or a crash inside a tool comes back to the model as `{"error": ...}`. One bad call cannot end the conversation.

**Raw HTTP, three dependencies.** The OpenRouter client is `urllib`. It records each call's tokens, cost, latency and finish reason for the evals. The packages are `pydantic`, `tzdata` (time zones on Windows) and `pytest`; Streamlit is optional.

## Trade-offs

- **The name is a weak second factor in this dataset.** Emails are `first.last@example.com`, so anyone who knows an email can guess the name. Recovery still needs a number one digit away, and counts toward the three-miss limit. A real deployment would use a stronger factor (order total, postcode) or a signed-in session. The name check ignores emails, so typing `marcus.reed@...` does not count as typing "Marcus Reed".
- **No human handoff.** The brief names no support team, so the agent guides the customer as far as its tools go and invents no contact. The simulated customers rate exactly these cases lowest (CSAT 2 for a cancellation, 2 to 3 for a late return or a missing package). A handoff tool is the next thing I would ask the business about.
- **The guard checks tokens, not sentences.** It cannot tell that "we offer free shipping" is invented. The prompt and the evals cover that, which is weaker than code.
- **The guard is strict.** The model may not compute "$390 over your budget", repeat a discount the customer proposes even to refuse it, or offer to "check back". Each costs one rewrite: 6.0% of turns in the latest run, up from 2.6% before the new hard rules. None fell back.
- **Version 2 costs more tokens per call.** The problem-solving rules, the `respond` tool, `recover_order` and the options take a first call with all features loaded from about 3,300 to 4,600 prompt tokens. The full scripted eval costs $0.12 for 90 conversations.
- **On-demand loading costs a model call** the first time each feature is needed, so `auto` only uses it above eight tools.
- **Format repair can repeat a token.** An order number in mid-sentence is also put on its own line. Slightly redundant, never a lost answer.
- **"Explicitly asks" for Night Owl is judged by the model.** A keyword check would refuse "yes please" after the agent describes the promotion.
- **The promo email is not verified**, and **the JSON code store is single-process.**

## Assumptions

The brief leaves these open. I would ask them before shipping; each is one small change if the answer differs.

| Question | What the agent does now |
| --- | --- |
| Is there a human or team to hand problems to? | No. The agent guides as far as its tools go and invents no contact. |
| An email that already has a code asks again outside the window. Repeat the code? | No. It explains when the promotion runs. |
| Does verifying one order unlock the same customer's other orders? | No. Each order needs its own number. |
| May a mistyped order number be recovered with the name on the order? | Yes, when the email and name match and the number is one digit off, unambiguously. |
| Must the promo email belong to an existing customer? | No. Any well-formed address gets a code. |
| How are the 30 days counted? | Calendar days in Eastern time. Day 30 is still eligible. |
| Does a code expire? How is it redeemed? | Not stated, so the agent says nothing about either. |

## Tests and evaluation

**Unit tests** (`pytest`, 232, no network) cover:
- the business rules at their boundaries;
- each guard rule and its false positives;
- order recovery, including ambiguous and two-digit-off numbers;
- the loop with a scripted fake model: `respond`, the forced final step, rewrites, fallbacks, compaction and on-demand loading;
- the feature registry and its budgets;
- the eval and simulator logic.

**Scripted scenarios** (`python -m evals.run_eval`) run 30 conversations from [evals/scenarios.json](evals/scenarios.json), 3 times each. Per turn, they check reply patterns, which tools ran, the tools' arguments, and how many model calls the turn used. Every reply is also checked, independently of the agent's guard, for facts no tool returned. Ten scenarios are tagged `safety`.

**Customer journeys** (`python -m evals.simulate`) are the main measure of whether problems get solved.
- A second model plays a customer from [evals/journeys.json](evals/journeys.json): a goal, and facts it gives only when asked.
- The conversation runs until code decides the problem is solved, judged from tools and replies, never from the simulated customer's opinion.
- The customer then rates satisfaction (CSAT, 1 to 5) and effort (CES, 1 easy to 5 hard).
- Journeys cover a mistyped order number, a wrong email, a missing package, a cancellation, a late return, a vague request, an out-of-stock product and an out-of-scope service.

Latest runs on `anthropic/claude-haiku-5.5`, saved with every reply in [evals/eval-run.txt](evals/eval-run.txt) and [evals/journeys-run.txt](evals/journeys-run.txt):

| | Result |
| --- | --- |
| Scripted runs passed | 90 / 90 (30 scenarios x 3); safety 30 / 30 |
| Journeys resolved | 24 / 24 (8 journeys x 3), in 1.38 customer turns on average |
| Satisfaction / effort | CSAT 3.46 / 5, CES 2.0 / 5; lowest where the brief allows no action (cancel, late return, missing package) |
| Model calls per turn | 1.67; latency p50 2.2 s, p95 4.6 s |
| Guard | rewrite 6.0% of turns, fallback 0%, formatting repaired in code 12.8% |
| Cost | $0.12 for 90 scripted conversations, $0.04 for 24 journeys |

Three runs of a sampled model with pattern checks and a simulated customer is evidence, not proof. Read it as "no known failure at this sample size".

## What changed in version 2

The review said the agent refused and stopped, missed the intent behind requests, and would not scale. Each change below has a test or an eval case that fails without it.

| Found | Change | Where |
| --- | --- | --- |
| Refusals were dead ends | Core prompt: solve the goal, act without asking, one targeted question, best remaining path; `respond` with `goal_status` and `next_step` | `prompt.py`, `agent.py` |
| A mistyped order number ended the conversation | `recover_order` with email + name + one digit off; the same next step on every miss | `features/orders.py`, `orders.py` |
| "Never got it" got a returns link; "cancel" got a flat no | Code-computed `options` for each order state | `orders.py` |
| One prompt and every tool on every call | Feature modules, catalog plus active rules, on-demand loading above eight tools, scale benchmark | `features/`, `evals/scale.py` |
| No memory beyond raw history | Case file, compaction of old tool results | `case.py` |
| Guard rules written for three features | Guard policy assembled from the features; new hard rules H6 (other customers' names) and H7 (promises to act later) | `guard.py` |
| Fallback was an apology | Fallback repeats the open question or lists what the agent can do | `agent.py` |
| Evals checked only "said nothing wrong" | Simulated-customer journeys with resolution, turns, CSAT and CES | `evals/simulate.py` |

Found while testing version 1 and still covered:
- an out-of-stock e-bike hidden when something else matched;
- invented discounts in lower case or other formats;
- a 190 cm rider offered size L;
- an email asked for before saying Night Owl was closed;
- "Enter it at checkout" said after issuing a code.

## Layout

```
kestrel/
  __main__.py      CLI and wiring
  agent.py         the loop, respond, fallbacks, on-demand loading
  prompt.py        the fixed core prompt and the catalog
  case.py          the case file and history compaction
  guard.py         hard checks and soft repairs, policy from the features
  tools.py         Tool, argument provenance, run_tool()
  features/        one module per feature: orders, products, night_owl
  orders.py        order lookup, recovery, return eligibility, options
  catalog.py       product search
  promo.py         Night Owl window and code store
  llm.py           OpenRouter client (urllib) with usage records
  clock.py, data.py
data/              the two datasets, unmodified
tests/             unit tests
evals/             scripted scenarios, customer journeys, scale benchmark, saved runs
transcripts/       sample conversations at 2026-12-01 9:30 PM Eastern
ui/app.py          optional Streamlit chat for manual testing
```

Transcripts 1 to 3 cover the brief's features and refusals. Transcript 4 recovers a mistyped order number and checks the bike's size against the customer's height. Transcript 5 guides a customer from a wrong email to a missing package and a jacket that no longer fits.

## With another day

1. **A human handoff tool**, once the business confirms there is a team: a ticket with a case ID, so the lowest-rated journeys can end resolved.
2. **A stronger second factor** for order recovery, or a signed-in mode where identity comes from the app (`--customer`), letting the agent list the customer's own orders.
3. **Check claims, not only tokens.** A cheap second model call for replies that mention shipping, warranty or refunds, asking whether every statement is backed by a tool result. Validate it on hand-labelled replies first.
4. **Judge the voice** with an LLM rubric calibrated on about ten replies scored by a person.
5. **Compare models with the evals.** `--model` already switches both runners; keep the default unless safety stays at 100% and resolution holds.
