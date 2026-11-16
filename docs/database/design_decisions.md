# Database Design Decisions

**Status: preliminary proposal awaiting review and approval.**

This document records the reasoning behind the proposed model and the questions
that must be resolved before SQL implementation. It does not authorize a schema
or make the proposed attributes, keys, cardinalities, or constraints final.

## Confirmed requirements

The following requirements come from the project brief:

- Use exactly these 11 entities: `seasons`, `drivers`, `constructors`, `circuits`,
  `races`, `entries`, `race_result`, `qualifying_result`,
  `sprint_qualifying_result`, `sprint_result`, and `practice_result`.
- Use English names and `snake_case`.
- Associate drivers, constructors, and races through `entries`; a driver does
  not have a permanent constructor association.
- Allow a separate practice result for each driver and practice session, with
  FP1, FP2, and FP3 supported.
- Support optional sprint weekends and missing historical session data.
- Document attributes, primary and foreign keys, cardinalities, and proposed
  uniqueness constraints.
- Keep `er_diagram.mmd` as the Mermaid source of truth and embed the same diagram
  in `relational_model.md`.
- Produce documentation only, without creating tables, changing Python models,
  implementing imports, or inventing historical records.

Everything below is a modeling proposal or an unresolved question unless it
explicitly refers to one of these confirmed requirements.

## Proposed decisions

### Entry means participation in a race weekend

An `entries` row represents one driver's participation in a race weekend,
including a driver who participates only in practice. It is not evidence that
the driver started the race. The proposed `UNIQUE (race_id, driver_id)` constraint
permits one such participation record per driver and race.

The constructor belongs to the entry. A driver may therefore have entries for
different constructors across races without changing the driver record.
`constructor_id` is provisionally nullable so that an otherwise identified
participation record can be preserved when the constructor is unknown.

This choice assumes one constructor per driver per weekend. It cannot describe
multiple constructor assignments for that driver within the same weekend, nor
does it identify a shared car or distinguish several cars for one driver.
These are scope limitations to review, not claims about particular historical
events. If the approved scope requires those distinctions, the entry definition
and its uniqueness constraint must be revised before implementation.

### Results depend on entries

Each of `race_result`, `qualifying_result`, `sprint_qualifying_result`, and
`sprint_result` uses `entry_id` as both its primary key and foreign key. An entry
can have zero or one row in each table; every result must reference exactly one
entry. The optional side is intentional: a participant need not have a recorded
result for every session.

The result tables do not repeat `driver_id`, `constructor_id`, or `race_id`.
Those associations are obtained through the entry, avoiding contradictory
copies of the same relationship.

`practice_result` uses a surrogate primary key and a required entry reference.
The proposed `UNIQUE (entry_id, practice_session)` constraint, together with
entry uniqueness, enforces one record per driver, race, and practice session.
`practice_session` accepts only `1`, `2`, and `3`, corresponding to FP1, FP2,
and FP3. These values identify sessions; they do not imply that all three
sessions took place or that their data is available.

### Qualifying names describe a session's intended function

The proposed interpretation of `qualifying_result` is the qualifying session
used to determine the race grid. The proposed interpretation of
`sprint_qualifying_result` is the qualifying session used to determine the sprint
grid. This functional naming requires approval and a later mapping to source
terminology; no mapping across seasons or competition formats is assumed here.

Optional Q1, Q2, and Q3 duration fields are a preliminary way to retain segment
times where applicable. They do not assert that every qualifying session has
three segments. The model currently provides one aggregate result per entry and
session type, not separate records for repeated runs, laps, or arbitrary segment
structures.

### Missing information stays missing

Nullable result metrics have no fabricated defaults. An unknown time is `NULL`,
not zero; unknown points are `NULL`, not an assumed zero score. A known zero
remains a valid recorded value where the approved domain allows it.

An absent result row may mean that a session did not apply, did not occur, was
not imported, or has unavailable data. This preliminary model cannot distinguish
those situations from absence alone. A result row with nullable metrics means that a result
observation is known while some details are unavailable; no placeholder result
rows are required for missing sessions.

The nullable `races.is_sprint_weekend` flag records known yes, known no, or
unknown. Unknown is not equivalent to no. A known sprint weekend can have no
sprint results in the database because its data has not been recorded. Sprint
result rows and this flag will need a consistency policy; the flag alone does
not enforce conditional cross-table rules.

`races.circuit_id` is also provisionally nullable so incomplete venue metadata
does not require an invented circuit record. Required identity relationships
remain required: every race has a season, every entry has a race and driver,
and every result has an entry.

### Keys and uniqueness express identity rather than presentation

`seasons.season_year` is proposed as a natural primary key. Other independently
identified entities use surrogate identifiers, while the four single-result
tables share their entry's identifier. Source identifiers are not currently
used as primary keys.

The proposed `UNIQUE (season_year, round_number)` constraint on `races` assumes
that the approved dataset identifies each race by one official round within
its season. How to handle events outside that numbering scheme, source
variants, or several editions of an event must be agreed before adopting the
constraint. A name, circuit, or calendar date is not proposed as race identity.

Driver names, constructor names, circuit names, and numeric result positions
are not proposed as unique. Shared display names should not merge identities,
and result uniqueness should not make assumptions about classification ties
or nonnumeric classification labels.

External-source references and their deduplication policy are deferred. The
future import design must establish how records map to these internal keys;
source-independent identifiers alone do not solve duplicate matching.

### Units and data types are preliminary

- Surrogate identifiers are proposed as `BIGINT UNSIGNED`; the season key and
  round/session values use narrower unsigned integer types.
- Durations use integer milliseconds rather than clock-time fields. Best-lap
  and qualifying-segment durations use `INT UNSIGNED`; elapsed race/sprint
  durations use `BIGINT UNSIGNED`. No duration is interpreted as a timestamp.
- Points use signed `DECIMAL(8,3)`, avoiding binary floating-point rounding.
  The scale and permission for negative values are proposals to review against
  the intended data scope. No nonnegative-points constraint is proposed yet.
- Dates use `DATE` and do not invent a time or time zone. Scheduling and event
  timestamps can be considered later if the approved scope needs them.
- Latitude and longitude use `DECIMAL(9,6)` with proposed geographic bounds.
  Missing coordinates remain nullable.
- Result positions are nullable positive integers. Optional labels and status
  text retain a description without inventing a status lookup entity or an
  enum of classifications before source vocabulary has been reviewed.

No permanent car number is assigned to a driver by this proposal. Whether an
entry needs a race number, starting-grid details, or additional metadata is an
open scope question.

### Referential integrity preserves recorded history

Foreign keys are proposed with `RESTRICT` for parent deletion and key updates.
This avoids implicit removal of recorded results through cascading operations.
Surrogate keys are intended to remain stable. Corrections, duplicate merges,
and intentional deletion would require a deliberate future workflow.

All such foreign keys, `UNIQUE` constraints, bounds, and checks are proposals.
They are not implemented by this documentation. Conditional rules involving
several tables, such as sprint consistency, require a separate enforcement
decision rather than being presented as ordinary row-level checks.

## Deliberate omissions

The fixed entity list excludes separate tables for sessions, individual laps,
classification statuses, source provenance, championship standings, and
participation changes within a weekend. No additional entities are introduced
to resolve these topics implicitly. The current model records aggregate session
results rather than a complete representation of every possible event format.

No example historical records are supplied, and no historical data has been
fetched or inferred.

## Decisions requiring approval

Before implementing the SQL schema, review and approve:

1. The proposed entity meanings, attribute lists, data types, units, and required
   versus nullable fields in `relational_model.md`.
2. A weekend-wide entry per driver, including practice-only participants, and
   whether one constructor assignment per driver per weekend is sufficient.
3. Nullable constructor and circuit references versus a stricter completeness
   policy.
4. The primary-key choices and uniqueness of `(season_year, round_number)`,
   `(race_id, driver_id)`, and `(entry_id, practice_session)`.
5. Zero-or-one results per entry for the four single-result entities, with no
   required result rows and no unique numeric-position constraint.
6. The functional interpretation of the two qualifying entities, source naming
   across formats, and whether optional Q1/Q2/Q3 fields are sufficient.
7. The difference between unavailable and inapplicable sessions, whether more
   availability metadata is required, and how to validate sprint flag/results
   consistency.
8. Millisecond precision, points precision and sign, date-only scheduling,
   free-text classifications, and the proposed value-domain checks.
9. Parent-delete/key-update restrictions and a future data-correction workflow.
10. The supported event scope and limits around shared cars, repeated session
    runs, within-weekend participation changes, source identifiers, and identity
    matching during future imports.

## Documentation maintenance

Edit `er_diagram.mmd` first, then replace the fenced Mermaid block in
`relational_model.md` with exactly the same content. Review attribute and
constraint descriptions alongside any diagram change. The three documentation
files describe one proposal and must be reviewed together before implementation.

## Validation status

The Mermaid source was syntax-validated with the locally available Mermaid
11.17.2 parser bundled with PyCharm, executed in Node without installing packages
or contacting external services. The parser recognized an ER diagram with all
11 required entities and confirmed the optional parent and result endpoints.
An invalid-diagram control was rejected by the same parser.

Separate structural checks confirmed that the embedded Markdown diagram exactly
matches `er_diagram.mmd`, that attribute names, types, and PK/FK markers match the
relational documentation, and that all 10 relationships reference the declared
entities. Git checks confirmed that only the three requested documentation files
changed and that `sql/schema.sql` remains unchanged. Whitespace validation passed.

**Visual rendering has not been verified.** Parser acceptance does not certify
layout or GitHub rendering. Review the diagram in a Mermaid-capable preview
before approving the design; compatibility with GitHub's deployed renderer has
not been tested. Validation also does not approve the proposed model or verify
it against any historical dataset.
