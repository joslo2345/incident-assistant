# Case study: an AI incident assistant for a GPU fleet

*How we cut GPU fault diagnosis from hours to about two minutes, measured every claim against
labelled faults, and worked out when it's worth running the model yourself.*

## The customer and the problem

The customer runs its own GPU cluster for training and inference: a few hundred to a few thousand
GPUs in 8-GPU servers, an infrastructure team of a handful of engineers, and technicians who swap
parts. When a GPU or server misbehaves, someone on call has to work out why, and that takes hours.

It takes hours because the evidence is scattered and the symptoms overlap. GPU metrics, driver
errors and the servers' own management logs (BMC) live in different tools. A dead fan, a degrading
memory stack and a badly behaved training job can all look like "the GPU got slow, then the job
crashed". The fix depends entirely on the cause: drain the node and replace a fan, reset the GPU,
replace it, or do nothing because the workload was the problem. Guess wrong and you either waste
a technician's afternoon and an expensive machine, or leave a broken one in service. What the team
knows is spread across runbooks, vendor documentation and old tickets, and mostly in whoever is
on call.

The brief: a system that watches the fleet, opens an incident when hardware looks unhealthy, and
investigates it the way an experienced engineer would, returning a diagnosis with evidence, the
runbook it relied on, and a recommended action. Anything that changes the fleet needs a person to
approve it.

## Constraints

- **Trust before automation.** The system recommends; people decide. Taking a node out of service
  always waits for a human, enforced in the database, not just in the prompt.
- **Every claim measured.** Accuracy targets were set up front (root cause at least 85%, citations
  at least 90% correct, zero unsafe actions) and checked on faults with known answers, including a
  held-out set nobody tuned against.
- **Near-zero cost.** Everything had to run on one laptop with open models until the cloud stage,
  and the cloud stage was written and validated but not applied. The budget was $0.
- **Realistic data.** No real fleet was available, so the telemetry replays a public GPU cluster
  trace (Alibaba 2020) through a physical model of each GPU, and faults are injected by changing
  the physics (a dead fan raises temperature at a given power), not by writing alarm values.

## Approach

The system is a pipeline of small services, each measured on its own before the next was built.

**Ingest and storage.** An API takes telemetry in batches and returns "accepted" only once Kafka
(Redpanda) has durably stored the events. A consumer writes them to TimescaleDB, committing its
Kafka position only after the database commit, so a crash replays events instead of losing them,
and duplicate events are skipped. Choosing "accepted means stored" halved peak throughput, from
65,000 to 34,000 events a second, which still leaves about 50 times the headroom a realistic fleet
needs. Killing the consumer mid-replay lost and duplicated nothing.

**Detection.** Three layers, each measured separately: static rules for unambiguous events (a GPU
that falls off the bus), per-GPU statistical baselines, and a thermal model that predicts each GPU's
temperature from its power draw. The naive statistical approach found the faults but drowned them:
309 false alarms in three runs, 21% precision. The thermal model, which flags a GPU running hotter
than its power explains, took precision to 100% while keeping recall at 99%, and raised no alarm on
any of the twelve "noisy neighbor" decoys (a busy job, not broken hardware).

**Knowledge.** Twenty-two runbooks and sixty-four past incident reports, chunked by section and
searched with a hybrid of meaning-based and keyword search, then re-ranked. Each step was measured:
the correct passage was in the top five results 73% of the time with meaning-based search alone, 83%
with hybrid search, and 100% after re-ranking. A relevance threshold makes it refuse questions the
corpus can't answer (six out of six) without refusing any it can.

**The agent.** An AI model investigates each incident with read-only tools (metrics, logs, server
inventory, runbook search, similar past incidents) and one tool that files a drain request for a
person to approve. Its final answer is a structured diagnosis, and every citation must be a passage
one of its tools actually returned in that run. The database enforces the rules the agent can't be
trusted to follow: its account can file an approval request but can't approve, edit or self-approve
one.

**The evaluation harness.** This is where most of the improvement came from. A labelled dev set and
a held-out test set (25 incidents each, all seven failure types, decoys, and half the faults with
their BMC logs missing) are scored in one command, with reports tagged by code version, model,
prompt and judge. Citations are checked by a second model acting as judge, and that judge was
itself calibrated against careful hand grading. Every change ran on the dev set first; the test set
was scored only twice, before and after.

**Interfaces.** A web UI for technicians and a Slack bot. Approvals, an audit log and per-user
access control are built in.

**Packaging.** A Helm chart with hardened pods and network policies that admit only the paths each
service needs, Terraform for Azure (AKS, managed Postgres, Key Vault, keyless deploys from GitHub),
and CI that gates every change on tests, security scans and a replayed agent evaluation.

## Results

| Measure | Target | Result |
| --- | --- | --- |
| Time from incident to diagnosis | under 2 minutes | about 2 minutes (125 s median) |
| Root cause correct, held-out test set | 85% | **88%** (up from 76%) |
| Recommended action allowed by the runbook | — | **92%** (up from 60%) |
| Real faults left in service ("missed actions") | — | **1 of 22** (down from 8) |
| Unsafe actions on false alarms | 0 | **0** |
| Fault detection | — | 99% recall, 100% precision |
| Right runbook passage in the top 5 | — | 100% |
| Model cost per incident | — | $0 (local model) |
| Kubernetes install, from nothing | — | 1 min 45 s |

Three measured rounds moved the agent. The baseline missed faults it had *seen*: the traces showed
it noticing a power limit cut from 400 W to 240 W and a GPU 17 °C hotter than its power explained,
then dismissing both because the prompt said that without hardware errors the cause was a busy
job. Round two told it that those signals are faults on their own; root-cause accuracy on the dev
set went from 19 to 22 of 25, and every fault with missing BMC logs was found. Round three told it
to take the action from the runbook; missed actions fell from 5 to 1 with no unsafe action. Thermal
runaway, the worst failure type at the start, went from 1 of 4 to 4 of 4 on the held-out set.

## Self-hosting the model: when is it worth it?

The assistant talks to its model through a standard API, so the model is a setting. A companion
project took that further: it served a smaller open model (Qwen3.5-9B) on vLLM, deployed it to
Kubernetes with autoscaling and alerts, load-tested it with replayed agent conversations, and
plugged it into the assistant through configuration alone.

The 9B model held up on the same held-out set: 23 of 25 root causes, 97% of citations supported, no
unsafe actions. Its weakness was the action: three times it confidently chose the wrong one. Two of
the 25 runs failed outright on long investigations, which the assistant now handles by falling back
to a hosted model mid-conversation; that fallback was tested by killing the model halfway through
an investigation, and the investigation finished correctly.

The deciding factor was cost structure, not accuracy. A hosted model costs about $0.10 per
incident at any volume. A GPU costs the same per hour whether it serves one incident or a thousand:
an always-on A100 is about $89 a day on demand or $17 on spot. At twenty incidents a day that's
$0.84 to $4.43 per incident, 8 to 44 times the hosted price. Self-hosting only wins above about
170 incidents a day on spot (with the hosted model as fallback) or 900 on demand, or when incident
data must not leave the customer's cloud. **The recommendation was the hosted model at today's
volume, with the self-hosted path ready to switch on.**

## Trade-offs worth knowing

- **Durability over speed.** Waiting for Kafka before saying "accepted" halved peak ingest, a cost
  worth paying for an assistant that must never lose the evidence.
- **Simple, explainable detection over a learned model.** With labelled faults and clear physics, a
  thermal model beat a black box and is easy to explain to the engineer who has to trust the alarm.
- **A bigger, slower model over a faster one.** The non-thinking variant of the same model was
  twice as fast but left runs without a diagnosis, recommended worse actions, and used 2.3 times
  the tokens, so it would cost more on a paid API. Rejected on the evidence.
- **Measure the measuring stick.** The first citation judge called almost everything "supported";
  against hand grades it agreed no better than chance (kappa 0.00). Its 96% score was really about
  78%. A rewritten judge reached 90% agreement (kappa 0.60) on incidents it wasn't written from.
- **Autoscaling can't save a spike.** Scaling on queue depth reacts in about 40 seconds, but a new
  GPU node takes minutes, and requests already queued don't move to the new replica. The minimum
  replica count has to cover the normal peak.

## What I'd do next

1. Run the held-out evaluation on the hosted model (about $4) to put a measured number next to its
   cost, before committing either way.
2. Fix the one remaining retrieval miss: power faults citing a general procedure instead of the
   power runbook.
3. Repeat each evaluation several times and report the spread; identical runs differ on about five
   of 25 incidents at the model's temperature setting.
4. Measure the self-hosted model on a real A100 for an hour (about $4), replacing the estimated
   throughput the break-even volumes rest on.
5. Run against a real fleet's telemetry, where the failure modes are messier than any simulation.

---

*Code, measurements and decision log: [incident-assistant](https://github.com/joslo2345/incident-assistant)
and [self-hosted-model](https://github.com/joslo2345/self-hosted-model). Every number above
comes from `docs/RESULTS.md` in one of the two repos.*
