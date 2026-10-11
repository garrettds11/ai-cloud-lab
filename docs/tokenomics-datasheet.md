# AI Lab Tokenomics Datasheet

Oct 10, 2026 · @Alias

## At a glance

The AI lab costs a fixed amount per hour while it runs, about **$0.80 per hour** on its GPU server, and nothing while it is stopped. Every run of the lab is a **session**. Its cost is shared by the people who used it, in proportion to the tokens each of them used.

- **One person alone** pays the whole session and the highest price per token.
- **More people in the same session** share the same cost, so everyone's price per token falls.
- **The lab stops itself** when nobody uses it, so people chatting are what keep it running, and what pays for it.

The result is an honest bill: each person pays a share of what the lab actually cost while they used it, and the page shows how busy each session was.

## How the lab is paid for

The lab is paid for **by the hour, not by the token.** The model runs on a server in our own AWS account, so there is no per-token fee to an AI vendor. The bill depends only on how long the server runs.

| Cost item | Charged | Example rate |
| --- | --- | --- |
| GPU server (`g6.xlarge`, one NVIDIA L4) | Per hour while running | about $0.80 per hour, AWS on-demand list price |
| Disk, load balancer, DNS, control panel | Small fixed amounts | not allocated to users in this model |

A token is a short piece of text, roughly three quarters of a word. Questions, documents and tool results going into the model are **input tokens**. The answer coming out is **output tokens**. Because the hour costs the same whether the model writes ten tokens or ten thousand, **the price of a token is not fixed: it depends on how many tokens share the hour.**

## How a session is charged

Each session is charged on its own, in three steps. A session runs from the moment the lab starts until it stops.

1. **Session cost.** What that run of the server cost.

```latex
\text{session cost} = \text{price per hour} \times \text{hours running}
```

2. **Price per token.** The session cost spread over every token used in that session, by everyone.

```latex
\text{price per token} = \frac{\text{session cost}}{\text{all tokens in the session}}
```

3. **Each person's charge.** Their share of the session's tokens, applied to the session cost.

```latex
\text{charge} = \text{session cost} \times \frac{\text{their tokens}}{\text{all tokens in the session}}
```

The charges in a session always add up to exactly the session cost. Nothing is marked up and nothing is left over. During a session the figures are a running estimate; they become final when the lab stops.

If a session ends with no tokens used (someone started the lab and nobody chatted), its cost is charged to the person who started it, from the control panel's start log.

## Worked example

The same work costs Alice **$1.60 alone but $0.32 when two colleagues use the same session**. Both sessions run 2 hours at $0.80 per hour, so each costs $1.60. The numbers are illustrative.

| Session | Person | Tokens used | Share | Charge | Price per 1M tokens |
| --- | --- | --- | --- | --- | --- |
| A: Alice alone | Alice | 40,000 | 100% | $1.60 | $40.00 |
| B: three people | Alice | 40,000 | 20% | $0.32 | $8.00 |
| B: three people | Ben | 60,000 | 30% | $0.48 | $8.00 |
| B: three people | Chen | 100,000 | 50% | $0.80 | $8.00 |

In session B the three charges add up to $1.60, the session cost. Everyone in a session pays the same price per token; what differs is how many tokens each person used. Alice did the same work in both sessions and paid five times more in A, because the server ran for her alone.

## Capacity: why busy sessions are cheaper

A session's cost is fixed, so **its price per token falls as more tokens share it, down to a floor when the server is fully used.**

&#91;embedded content: Illustrative: 2-hour session at $0.80 per hour; capacity of 500k tokens assumed until measured\]

**Capacity used** is a session's tokens divided by what the server could have produced in the same hours, measured on the lab. It explains a high price at a glance: a session that ran at 8% of capacity paid about twelve times the floor. The floor itself is the best price this server size can reach, and the figure to compare against other server sizes or hosted services. Past full capacity, answers slow down and the next step is a larger or second server.

## Idle time and auto-stop

The lab watches for use and **stops itself after a set number of idle minutes**, so it runs only while people use it, plus one short idle tail at the end. Use keeps it running, and the people using it pay for it.

Every session's cost is shown in parts, so waste is visible rather than spread silently:

| Part of the session | What it is | What it tells you |
| --- | --- | --- |
| Start-up | Server boot and model load | Fixed overhead of each start |
| Active | Time with requests in progress or users signed in | The useful time |
| Idle tail | The idle minutes before auto-stop | Set too long, it wastes money |

One thing to watch: the lab counts a person as active while Open WebUI has seen them in the last 3 minutes, so an open, unused browser tab can keep it running without using tokens. The admin page shows each person's active minutes next to their tokens, so this shows up.

## Where to see it

Per-person costs are on an **admin-only Usage and cost page in the control panel**. Grafana shows totals only, and no per-person data leaves the lab.

| View | Shows |
| --- | --- |
| Sessions | Start, stop, hours, session cost, tokens, price per 1M tokens, capacity used |
| One session | Each person's tokens, share, charge and active minutes |
| People | Each person's total tokens and charges over a chosen date range |
| Grafana | Total cost, cost per 1M tokens, cost per chat, idle share, model speed |

**Glossary**

| Term | Meaning |
| --- | --- |
| Token | A piece of text the model reads or writes, about three quarters of a word |
| Session | One run of the lab, from start to stop |
| Session cost | Price per hour × hours the session ran |
| Price per token | Session cost ÷ all tokens used in the session |
| Share | One person's tokens ÷ all tokens in the session |
| Capacity | Tokens the server could have produced in the session's hours |
| Capacity used | Tokens used ÷ capacity |
| Floor price | Price per token if the session had run at full capacity |

## Questions executives ask

**Why does the same question cost me different amounts on different days?** Because the price per token is set by how busy the session was. In a quiet session one person carries the whole hour; in a busy one, many people share it. The page shows each session's capacity used, so the reason is visible.

**Why not a flat fee per person?** A flat fee hides what the lab costs and who uses it. Session sharing charges exactly what was spent, adds up to the real bill, and shows when the lab is underused.

**What happens as more people use it?** The price per token falls until the server is fully used. Past that, answers slow down and the next step is a larger or second server, which raises the hourly cost in one step.

**Is it cheaper than a hosted AI service?** It depends on volume. At light use a pay-per-token service usually costs less; at steady, heavy use a dedicated server costs less per token. The Grafana dashboard can show the hosted equivalent once a price per token is entered for comparison.

**Can we cap the cost?** Yes. Cost is hours × rate, so it is predictable. Auto-stop ends idle sessions, and an optional hard limit stops any session after a set time, with an email warning first.

**Does cost tracking expose what people asked?** No. Only token counts, times and costs are recorded. Questions and answers stay in the lab and never go to the cost page or to Grafana. The model runs in our own AWS account, so prompts are not sent to an AI vendor.

## Assumptions and open decisions

- **Rates are approximate.** About $0.80 per hour is the AWS on-demand list price for `g6.xlarge` in us-east-1, from memory; check current pricing before quoting it. The worked example and capacity figures are illustrative until measured on the lab.
- **Only the GPU server is allocated.** Disk, load balancer, DNS and the always-on control panel are small and not charged to users.
- **Open: how shares are counted.** By total tokens (simple), or by server time per request (fairer: writing an output token takes much more server time than reading an input token).
- **Open: which hourly price.** AWS list price, or a figure entered for discounts or savings plans.
