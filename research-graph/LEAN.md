# Can Lean show that part of the research still holds?

**Short answer: yes for the logical skeleton, no for faithfulness to the science.**
Lean can't measure how closely the model matches the real research. A person has to
review that.

## What works today (`rdg/lean_export.py`)

Every claim becomes a `Prop`. Methodology and subject dependencies become premises
(`A ∧ B → C`). Unchallenged foundations become axioms. Challenged claims get no
axiom. Background citations are deliberately *not* premises. For every claim that can
still be derived, the exporter writes:

```lean
theorem c_our_program__h1_stands (_h0 : ¬ c_lee_2021__spec) : c_our_program__h1 :=
  (dep_c_our_program__h1 (dep_c_chen_2023__dose ...))
```

That is a machine-checkable statement: *h1 holds even assuming the antibody specificity
finding is false.* h2 and h3 get no theorem, because every route to them goes through
the contested claim.

**Status:** compiled with Lean 4 for both the antibody example and the Lesné 2006
snapshot, with no errors. To re-check:

```
python -c "from rdg.demo import demo_graph; from rdg.lean_export import export; print(export(demo_graph()))" > Demo.lean
lean Demo.lean
```

## Where the real difficulty is

1. **Fidelity (the hard part).** "The pull-down assay depends on mAb-7 specificity" is
   an empirical judgement. Lean verifies the proof *given* that premise and is silent on
   whether the premise is true or complete. Turning prose into premises
   (autoformalisation) is unreliable with current AI. A missing premise produces a
   wrong "still stands" that looks rigorous, which is exactly the "confidently wrong"
   failure the team wants to avoid.
2. **Science isn't boolean.** Findings have effect sizes, conditions and partial
   replication. Lean proofs are all-or-nothing, so confidence scores have to stay in the
   probabilistic layer (`propagation.py`). Lean should only answer "is there *any*
   derivation that avoids X?".
3. **The current check is only reachability.** With conjunctive premises, the Lean
   result equals graph reachability, which the Python layer already computes. Lean
   becomes useful once claims carry richer structure: alternative support routes
   (`A ∨ B → C`), or "uses only property P of the antibody" (for example affinity, not
   specificity). Then a proof shows something the graph alone cannot.

## Recommendation

- Use Lean as an optional **"provably unaffected"** badge, never as the main signal.
- Have the LLM draft the premises, show them as plain English for a scientist to
  approve, and only then generate proofs. The trust comes from the human approval.
- To score "how closely it represents the research", round-trip it: translate the
  approved Lean premises back into prose and have a scientist (or a second model) rate
  agreement with the paper. That is a review metric, not a proof.
