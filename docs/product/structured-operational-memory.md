# Structured operational memory

## Product concept

A useful operational object has:

- durable data and a clear business meaning;
- an owner and accountable legal entity where applicable;
- relationships to standard business objects;
- a current state and lifecycle;
- applicable policies and permissions;
- supporting evidence;
- an audit history;
- expected next events;
- permitted actions and escalation rules.

Examples include companies, partners, projects, tasks, decisions, trips, collaborations, invoices, bills, expenses, payments, journal entries and declarations.

## Object quality standard

An object may be added only when:

- its meaning is unambiguous;
- it cannot be represented adequately by an existing standard concept;
- its ownership and lifecycle are defined;
- it creates useful relationships or behaviour;
- it does not duplicate authoritative truth.

## Event-driven behaviour

Events update state. State drives behaviour.

The system must be able to inspect current state and determine remaining work after interruption. Processes must not rely solely on fragile one-time chains.

## Evidence and attribution

Meaningful changes identify:

- who or what acted;
- why the change occurred;
- which evidence and policy supported it;
- confidence or uncertainty where relevant;
- whether human approval was required and obtained.
