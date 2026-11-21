# Phase 6: Voice core <-> ledger backend integration contract

Status: written from the ACTUAL source of `asmitaguptha-sudo/s-vyasa`, branch `feature/ledger-backend`,
commit `759e2aa` ("Implement core ledger backend"). Nothing here is assumed from the README alone;
every endpoint, field and error below was read from `backend/main.py`, `schemas.py`, `ledger.py`,
`customer_service.py`, `database.py`, `money.py` and the backend tests.

Rule of the integration: **the backend is authoritative** for proposals, confirmation, transactions,
customers, balances, history and reversals. The voice core never touches the database. It only calls
the HTTP API through one isolated client (`backend/ledger_api/`).

## 1. Backend API (as implemented)

| Purpose | Call | Notes |
|---|---|---|
| Health | `GET /health` | 503 if the DB is unreachable |
| Create proposal | `POST /ai/contract/propose` | body = EXACTLY 5 fields (`extra=forbid`) |
| Confirm | `POST /ai/proposals/{id}/confirm` | no body; **idempotent** |
| Cancel | `POST /ai/proposals/{id}/cancel` | no body; re-cancel is a no-op |
| Customer lookup | `GET /customers/lookup?q=` | EXACT_MATCH / AMBIGUOUS_MATCH / NOT_FOUND |
| List customers | `GET /customers?limit&offset` | limit 1..500 |
| Get customer | `GET /customers/{id}` | 404 if unknown |
| Create customer | `POST /customers` | `{name, alias?, aliases[], phone?}` |
| Balance | `GET /customers/{id}/balance` | authoritative, integer paise, may be negative |
| History | `GET /customers/{id}/transactions?limit&offset` | all statuses incl. REVERSED |
| Reversal | `POST /transactions/{id}/reverse` | `{reason?, idempotency_key?}`; additive compensating entry |
| Legacy commit | `POST /ai/contract/commit` | **NOT used** (deprecated) |
| Validate | `POST /ai/contract/validate` | read-only; not needed |

Propose request (all 5 required): `intent`, `customer_name`, `amount` (rupees, <= 2 decimals),
`item` (string or null), `confirmation_required` (bool).
Propose response: `proposal_id` (`prop_<12 hex>`), `status` ("PENDING_CONFIRMATION"), `customer_id`,
`customer_name`, `intent` (normalised to `add_credit` / `record_payment`), `amount_paise`,
`amount_rupees`, `item`, `confirmation_prompt`, `confirmation_required`, `committed` (false).
Confirm response: `proposal_id`, `status` ("CONFIRMED"), `committed` (true), `transaction{transaction_id,type,amount_paise,...}`,
`customer_balance{balance_paise,...}`, `message`.

Errors: 404 (customer / proposal / transaction not found, detail = string), 409 (ambiguous customer on
propose: detail = object with `clarification_prompt` and `candidates`; idempotency conflict; duplicate
reversal), 400 (cancelled proposal confirmed; confirmed proposal cancelled; invalid values), 422
(pydantic validation, detail = list), 503 (health).

Facts that shape the design:
* **Customers are identified by NAME in a proposal**, not by id. The backend resolves the name itself.
* The backend lookup has a substring step: a partial name ("Ram") can resolve to ONE customer as
  `EXACT_MATCH`. A proposal can therefore be created for a different customer than the one meant.
* **Proposals never expire** and **there is no authentication** anywhere.
* Confirm is idempotent (it reuses transaction idempotency key `proposal-<id>`), so retrying a confirm
  after a timeout can never create a second ledger entry.
* There is **no API for promises** (a `promises` table exists, no endpoint), no date on propose
  (the backend stamps its own time), and no `payment_method` on propose (the schema forbids extra fields).

## 2. Mapping of the voice core to the backend

| Voice core | Backend |
|---|---|
| `ADD_CREDIT` (`transaction_type=CREDIT`) | `intent="add_credit"` |
| `RECORD_PAYMENT` (`PAYMENT`) | `intent="record_payment"` |
| `amount_paise` (int) | `amount` as an exact rupee STRING built with integer maths (`"300.00"`) |
| customer (name / qualifier / known id) | `customer_name` = a query that resolves to exactly that customer (see section 3) |
| item | always `null` (the voice core has no item) |
| (confirmation gate) | `confirmation_required = true` always |
| `ExecutionRequest` (legacy hand-off) | **replaced** by the backend proposal in API mode; no hand-off object is produced |
| pending action + confirmation prompt | backend `proposal_id` bound to (`action_id`, `revision`); spoken prompt = backend `confirmation_prompt` |
| `SET_COMMITMENT` (promise) | **unsupported by the backend**: refused, never faked as a payment or credit |
| transaction date other than today | **unsupported** (backend stamps "now"): refused, not silently recorded as today |
| `payment_method` | not sent (no field); documented loss, not claimed in the spoken readback |
| `GET_BALANCE`, `GET_HISTORY` | `GET /customers/{id}/balance`, `GET /customers/{id}/transactions` |
| `DAILY_SUMMARY`, `QUERY_COMMITMENT` | no endpoint: answered honestly as "not available", nothing invented |

## 3. Customer identity (the sharpest edge)

1. The voice core's `known_customers` list now comes from `GET /customers` (ids, names, aliases).
2. To propose for a customer the core has resolved to id N, the adapter builds a query that the
   backend resolves uniquely: the canonical name if no other customer shares it, otherwise one of that
   customer's aliases that no other customer shares, otherwise the proposal is REFUSED
   (`CUSTOMER_UNRESOLVABLE`) rather than guessed.
3. After `propose`, the echo is verified against what was asked: same `customer_id` (when known), or a
   name/alias equal to the spoken name; same `amount_paise`; same normalised intent; `committed=false`;
   a well-formed `proposal_id`. Any difference => the proposal is cancelled at the backend and the
   action is refused (`MISMATCH`). This closes the "partial name resolved to another customer" hole.
4. Ambiguous customer (409) => back into the existing customer-clarification flow; nothing is proposed.
5. Unknown customer (404) => told so; **no customer is created automatically**.

## 4. Proposal lifecycle (API mode)

```
voice -> STT -> intent -> command -> dialogue (pending action, all slots filled)
  -> POST /ai/contract/propose           proposal_id stored, bound to (action_id, revision)
  -> spoken prompt = backend confirmation_prompt (+ "Maine aisa samjha" if AI-assisted)
  -> user says "haan"  -> POST /ai/proposals/{id}/confirm  -> transaction committed by the BACKEND
  -> user says "nahi"  -> POST /ai/proposals/{id}/cancel   -> nothing committed
  -> correction/clarification/expiry/abandon -> old proposal cancelled; a changed readback needs a NEW proposal
```
Only an explicit confirmation of the exact readback calls `confirm`. Silence, unclear speech, direction /
amount / customer clarifications, stale or replayed messages never do.

Failure handling: see `docs/phase6_setup.md` (written with the implementation).
